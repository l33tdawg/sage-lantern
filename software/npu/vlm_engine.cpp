// Persistent RKLLM multimodal (vision) engine for Lantern: loads one RKNN vision
// encoder and one .rkllm LLM once and answers JSON-lines requests from stdin on
// stdout, so a photo question never pays a model reload and never touches the CPU
// tier.
//
//   stdin : {"id":"...","prompt":"<fully rendered chat text>","image":"/path.jpg",
//            "max_new_tokens":192,"stream":false}
//   stdout: {"event":"ready","init_ms":...,"vision_init_ms":...,"image_token":...,
//            "image_width":...,"image_height":...,"max_context":...} once, then
//           {"event":"token","id":"...","text":"..."} per generated piece when the
//           request asks for "stream": true, then
//           {"event":"done","id":"...","ok":true,"text":"...","first_ms":...,
//            "total_ms":...,"vision_ms":...,"prefill_ms":...,"prefill_tokens":...,
//            "generate_ms":...,"generate_tokens":...,"memory_mb":...}
//
// "image" is optional: a request without it runs the same LLM text-only. The
// caller renders the full chat transcript and places the literal "<image>" marker
// where the image belongs; the runtime substitutes the vision tokens there. The
// chat template is left empty, exactly like rkllm_engine.cpp, so the caller's
// transcript is authoritative.
//
// The vision path follows Rockchip's rknn-llm multimodal_model_demo (Apache-2.0)
// and Q-engineering's RK35llm wrapper (BSD-3-Clause): RGB, grey square expand,
// resize to the encoder's input size, one NPU run, then interleave the encoder's
// outputs into the embedding block the LLM consumes. base_domain_id=1 matches the
// RK3588 multimodal layout (the text engine uses 0).
#include <chrono>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/opencv.hpp>

#include "rknn_api.h"
#include "rkllm.h"

using Clock = std::chrono::steady_clock;

namespace {

// The RKLLM runtime prints its own INFO lines on stdout; every protocol line carries a
// marker so the front end can skip them.
constexpr const char* kMarker = "@@RKLLM_JSON@@ ";

struct VisionContext {
    rknn_context ctx = 0;
    rknn_input_output_num io_num{};
    int width = 0;
    int height = 0;
    int channel = 0;
    int image_token = 0;
    int embed_size = 0;
    cv::Mat resized;
    std::vector<float> embedding;
};

struct Run {
    Clock::time_point start;
    double first_ms = -1;
    bool finished = false;
    bool failed = false;
    bool stream = false;
    std::string id;
    std::string text;
    RKLLMPerfStat perf{};
};

std::string quote(const std::string& s) {
    std::ostringstream o;
    o << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') o << '\\' << char(c);
        else if (c == '\n') o << "\\n";
        else if (c == '\r') o << "\\r";
        else if (c == '\t') o << "\\t";
        else if (c < 32) o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else o << char(c);
    }
    o << '"';
    return o.str();
}

void append_utf8(std::string& out, unsigned code) {
    if (code <= 0x7f) out += char(code);
    else if (code <= 0x7ff) {
        out += char(0xc0 | (code >> 6));
        out += char(0x80 | (code & 0x3f));
    } else if (code <= 0xffff) {
        out += char(0xe0 | (code >> 12));
        out += char(0x80 | ((code >> 6) & 0x3f));
        out += char(0x80 | (code & 0x3f));
    } else {
        out += char(0xf0 | (code >> 18));
        out += char(0x80 | ((code >> 12) & 0x3f));
        out += char(0x80 | ((code >> 6) & 0x3f));
        out += char(0x80 | (code & 0x3f));
    }
}

int hex4(const std::string& s, size_t i) {
    int value = 0;
    for (size_t k = 0; k < 4 && i + k < s.size(); ++k) {
        char c = s[i + k];
        value <<= 4;
        if (c >= '0' && c <= '9') value |= c - '0';
        else if (c >= 'a' && c <= 'f') value |= c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') value |= c - 'A' + 10;
        else return -1;
    }
    return value;
}

// Extract a JSON string field. The request objects are emitted by json.dumps with
// ensure_ascii=False, so this handles the escapes Python can produce.
bool json_string(const std::string& body, const std::string& key, std::string& out) {
    std::string needle = "\"" + key + "\"";
    size_t at = body.find(needle);
    if (at == std::string::npos) return false;
    at = body.find(':', at + needle.size());
    if (at == std::string::npos) return false;
    ++at;
    while (at < body.size() && isspace(static_cast<unsigned char>(body[at]))) ++at;
    if (at >= body.size() || body[at] != '"') return false;
    std::string value;
    for (size_t i = at + 1; i < body.size(); ++i) {
        char c = body[i];
        if (c == '"') { out = value; return true; }
        if (c != '\\') { value += c; continue; }
        if (++i >= body.size()) return false;
        char e = body[i];
        switch (e) {
            case '"': value += '"'; break;
            case '\\': value += '\\'; break;
            case '/': value += '/'; break;
            case 'b': value += '\b'; break;
            case 'f': value += '\f'; break;
            case 'n': value += '\n'; break;
            case 'r': value += '\r'; break;
            case 't': value += '\t'; break;
            case 'u': {
                int code = hex4(body, i + 1);
                if (code < 0) return false;
                i += 4;
                if (code >= 0xd800 && code <= 0xdbff && i + 6 < body.size()
                    && body[i + 1] == '\\' && body[i + 2] == 'u') {
                    int low = hex4(body, i + 3);
                    if (low >= 0xdc00 && low <= 0xdfff) {
                        unsigned combined = 0x10000 + ((unsigned(code) - 0xd800) << 10)
                                          + (unsigned(low) - 0xdc00);
                        append_utf8(value, combined);
                        i += 6;
                        break;
                    }
                }
                append_utf8(value, static_cast<unsigned>(code));
                break;
            }
            default: return false;
        }
    }
    return false;
}

bool json_long(const std::string& body, const std::string& key, long& out) {
    std::string needle = "\"" + key + "\"";
    size_t at = body.find(needle);
    if (at == std::string::npos) return false;
    at = body.find(':', at + needle.size());
    if (at == std::string::npos) return false;
    return std::sscanf(body.c_str() + at + 1, " %ld", &out) == 1;
}

bool json_bool(const std::string& body, const std::string& key, bool& out) {
    std::string needle = "\"" + key + "\"";
    size_t at = body.find(needle);
    if (at == std::string::npos) return false;
    at = body.find(':', at + needle.size());
    if (at == std::string::npos) return false;
    size_t value = body.find_first_not_of(" \t", at + 1);
    if (value == std::string::npos) return false;
    if (body.compare(value, 4, "true") == 0) { out = true; return true; }
    if (body.compare(value, 5, "false") == 0) { out = false; return true; }
    return false;
}

int callback(RKLLMResult* result, void* userdata, LLMCallState state) {
    auto& run = *static_cast<Run*>(userdata);
    if (state == RKLLM_RUN_ERROR) run.failed = true;
    if (state == RKLLM_RUN_FINISH) {
        run.finished = true;
        if (result) run.perf = result->perf;
    }
    if (state == RKLLM_RUN_NORMAL && result && result->text) {
        if (result->text[0] && run.first_ms < 0)
            run.first_ms = std::chrono::duration<double, std::milli>(Clock::now() - run.start).count();
        if (run.stream && result->text[0]) {
            // Emitted from the runtime's worker thread while rkllm_run blocks the main
            // thread, so this is the only writer during a run.
            std::cout << kMarker << "{\"event\":\"token\",\"id\":" << quote(run.id)
                      << ",\"text\":" << quote(result->text) << "}" << std::endl;
        }
        run.text += result->text;
        if (run.text.size() > 262144) {
            run.failed = true;
            return 1;
        }
    }
    return 0;
}

int init_vision(VisionContext& v, const char* model_path, int core_num) {
    rknn_context ctx = 0;
    if (rknn_init(&ctx, (void*)model_path, 0, 0, NULL) < 0) return -1;

    if (core_num == 2) rknn_set_core_mask(ctx, RKNN_NPU_CORE_0_1);
    else if (core_num >= 3) rknn_set_core_mask(ctx, RKNN_NPU_CORE_0_1_2);
    else rknn_set_core_mask(ctx, RKNN_NPU_CORE_AUTO);

    rknn_input_output_num io_num{};
    if (rknn_query(ctx, RKNN_QUERY_IN_OUT_NUM, &io_num, sizeof(io_num)) != RKNN_SUCC) return -1;

    rknn_tensor_attr attr{};
    attr.index = 0;
    if (rknn_query(ctx, RKNN_QUERY_INPUT_ATTR, &attr, sizeof(attr)) != RKNN_SUCC) return -1;
    if (attr.fmt == RKNN_TENSOR_NCHW) {
        v.channel = attr.dims[1];
        v.height = attr.dims[2];
        v.width = attr.dims[3];
    } else {
        v.height = attr.dims[1];
        v.width = attr.dims[2];
        v.channel = attr.dims[3];
    }

    attr = rknn_tensor_attr{};
    attr.index = 0;
    if (rknn_query(ctx, RKNN_QUERY_OUTPUT_ATTR, &attr, sizeof(attr)) != RKNN_SUCC) return -1;
    for (int i = 0; i < 4; ++i) {
        if (attr.dims[i] > 1) {
            v.image_token = attr.dims[i];
            v.embed_size = attr.dims[i + 1];
            break;
        }
    }
    if (v.image_token <= 0 || v.embed_size <= 0) return -1;

    v.ctx = ctx;
    v.io_num = io_num;
    return 0;
}

// Encode one image into v.embedding via the NPU. The encoder may expose several
// outputs; interleave them per image token into one contiguous block, matching the
// layout rkllm expects (see Rockchip's multimodal demo RunImgEnc).
int run_vision(VisionContext& v) {
    rknn_input input{};
    input.index = 0;
    input.type = RKNN_TENSOR_UINT8;
    input.fmt = RKNN_TENSOR_NHWC;
    input.size = v.width * v.height * v.channel;
    input.buf = v.resized.data;

    if (rknn_inputs_set(v.ctx, 1, &input) < 0) return -1;
    if (rknn_run(v.ctx, nullptr) < 0) return -1;

    std::vector<rknn_output> outputs(v.io_num.n_output);
    for (uint32_t j = 0; j < v.io_num.n_output; ++j) outputs[j].want_float = 1;
    if (rknn_outputs_get(v.ctx, v.io_num.n_output, outputs.data(), NULL) < 0) return -1;

    const size_t per_token = size_t(v.embed_size);
    if (v.io_num.n_output == 1) {
        memcpy(v.embedding.data(), outputs[0].buf, outputs[0].size);
    } else {
        for (int i = 0; i < v.image_token; ++i) {
            for (uint32_t j = 0; j < v.io_num.n_output; ++j) {
                memcpy(v.embedding.data()
                           + (size_t(i) * v.io_num.n_output + j) * per_token,
                       (float*)(outputs[j].buf) + size_t(i) * per_token,
                       sizeof(float) * per_token);
            }
        }
    }
    rknn_outputs_release(v.ctx, v.io_num.n_output, outputs.data());
    return 0;
}

int prepare_image(VisionContext& v, const std::string& path) {
    cv::Mat img = cv::imread(path);
    if (img.empty()) return -1;
    cv::Mat rgb;
    cv::cvtColor(img, rgb, cv::COLOR_BGR2RGB);

    // Grey square expand, then resize to the encoder input (see the reference demo;
    // the background grey matches the model's preprocessing).
    int width = rgb.cols, height = rgb.rows;
    cv::Mat square = rgb;
    if (width != height) {
        int size = std::max(width, height);
        square = cv::Mat(size, size, rgb.type(), cv::Scalar(127, 127, 127));
        rgb.copyTo(square(cv::Rect((size - width) / 2, (size - height) / 2, width, height)));
    }
    cv::resize(square, v.resized, cv::Size(v.width, v.height), 0, 0, cv::INTER_LINEAR);

    size_t floats = size_t(v.image_token) * v.embed_size * v.io_num.n_output;
    v.embedding.assign(floats, 0.0f);
    return run_vision(v);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "usage: vlm_engine vision.rknn model.rkllm [max_context] [cpus] [default_max_new_tokens] [vision_cores]\n";
        return 2;
    }
    const char* vision_model = argv[1];
    const char* model = argv[2];
    int max_context = argc > 3 ? std::atoi(argv[3]) : 4096;
    int cpus = argc > 4 ? std::atoi(argv[4]) : 4;
    int default_new_tokens = argc > 5 ? std::atoi(argv[5]) : 256;
    int vision_cores = argc > 6 ? std::atoi(argv[6]) : 3;
    if (max_context < 32 || max_context > 16384) return 2;
    if (cpus != 2 && cpus != 3 && cpus != 4 && cpus != 8) return 2;
    if (vision_cores < 1 || vision_cores > 3) return 2;

    auto init_start = Clock::now();
    RKLLMParam param = rkllm_createDefaultParam();
    param.model_path = model;
    param.max_context_len = max_context;
    param.max_new_tokens = default_new_tokens;
    param.n_keep = (max_context > 2048) ? 2048 : max_context / 2;
    param.top_k = 1;
    param.top_p = 1;
    param.temperature = 0;
    param.repeat_penalty = 1;
    param.frequency_penalty = 0;
    param.presence_penalty = 0;
    param.skip_special_token = 1;
    // RK3588 multimodal models are converted expecting the LLM on domain 1 (the
    // vision encoder owns domain 0). The text engine uses 0; do not copy it here.
    param.extend_param.base_domain_id = 1;
    param.extend_param.enabled_cpus_num = cpus;
    param.extend_param.enabled_cpus_mask = cpus == 2 ? 0xc0 : cpus == 3 ? 0xe0 : cpus == 4 ? 0xf0 : 0xff;

    RKLLMCallback cb{};
    cb.result_callback = callback;
    LLMHandle handle = nullptr;
    int code = rkllm_init(&handle, &param, &cb);
    double init_ms = std::chrono::duration<double, std::milli>(Clock::now() - init_start).count();
    if (code != 0) {
        std::cout << kMarker << "{\"event\":\"error\",\"message\":\"rkllm_init failed\",\"code\":" << code << "}" << std::endl;
        return 1;
    }
    // The caller renders the whole transcript, so no automatic chat template.
    if (rkllm_set_chat_template(handle, "", "", "") != 0) {
        std::cout << kMarker << "{\"event\":\"error\",\"message\":\"rkllm_set_chat_template failed\"}" << std::endl;
        rkllm_destroy(handle);
        return 1;
    }

    VisionContext vision;
    auto vision_start = Clock::now();
    if (init_vision(vision, vision_model, vision_cores) != 0) {
        std::cout << kMarker << "{\"event\":\"error\",\"message\":\"vision encoder init failed\"}" << std::endl;
        rkllm_destroy(handle);
        return 1;
    }
    double vision_init_ms = std::chrono::duration<double, std::milli>(Clock::now() - vision_start).count();

    std::cout << kMarker << "{\"event\":\"ready\",\"init_ms\":" << init_ms
              << ",\"vision_init_ms\":" << vision_init_ms
              << ",\"max_context\":" << max_context
              << ",\"cpus\":" << cpus
              << ",\"vision_cores\":" << vision_cores
              << ",\"image_token\":" << vision.image_token
              << ",\"embed_size\":" << vision.embed_size
              << ",\"image_width\":" << vision.width
              << ",\"image_height\":" << vision.height
              << ",\"default_max_new_tokens\":" << default_new_tokens << "}" << std::endl;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::string id, prompt, image;
        long max_new_tokens = default_new_tokens;
        bool stream = false;
        if (!json_string(line, "prompt", prompt) || prompt.empty()) {
            std::cout << kMarker << "{\"event\":\"done\",\"ok\":false,\"error\":\"missing prompt\"}" << std::endl;
            continue;
        }
        json_string(line, "id", id);
        json_string(line, "image", image);
        json_long(line, "max_new_tokens", max_new_tokens);
        json_bool(line, "stream", stream);
        if (max_new_tokens < 1 || max_new_tokens > 1024) max_new_tokens = default_new_tokens;

        if (rkllm_clear_kv_cache(handle, 0, nullptr, nullptr) != 0) {
            std::cout << kMarker << "{\"event\":\"done\",\"id\":" << quote(id)
                      << ",\"ok\":false,\"error\":\"cache clear failed\"}" << std::endl;
            continue;
        }

        double vision_ms = 0.0;
        if (!image.empty()) {
            auto vision_run_start = Clock::now();
            if (prepare_image(vision, image) != 0) {
                std::cout << kMarker << "{\"event\":\"done\",\"id\":" << quote(id)
                          << ",\"ok\":false,\"error\":\"image encode failed\"}" << std::endl;
                continue;
            }
            vision_ms = std::chrono::duration<double, std::milli>(Clock::now() - vision_run_start).count();
        }

        RKLLMInput input{};
        input.role = "user";
        input.enable_thinking = false;
        if (image.empty()) {
            input.input_type = RKLLM_INPUT_PROMPT;
            input.prompt_input = prompt.c_str();
        } else {
            input.input_type = RKLLM_INPUT_MULTIMODAL;
            input.multimodal_input.prompt = (char*)prompt.c_str();
            input.multimodal_input.image.image_embed = vision.embedding.data();
            input.multimodal_input.image.n_image_tokens = vision.image_token;
            input.multimodal_input.image.n_image = 1;
            input.multimodal_input.image.image_height = vision.height;
            input.multimodal_input.image.image_width = vision.width;
            input.multimodal_input.image.image_start = "<|vision_start|>";
            input.multimodal_input.image.image_end = "<|vision_end|>";
            input.multimodal_input.image.image_content = "<|image_pad|>";
        }
        RKLLMInferParam infer{};
        infer.mode = RKLLM_INFER_GENERATE;
        infer.keep_history = 0;
        infer.max_new_tokens = static_cast<int>(max_new_tokens);

        Run run;
        run.id = id;
        run.stream = stream;
        run.start = Clock::now();
        int rc = rkllm_run(handle, &input, &infer, &run);
        double total_ms = std::chrono::duration<double, std::milli>(Clock::now() - run.start).count();
        const auto& p = run.perf;
        bool ok = rc == 0 && run.finished && !run.failed;
        std::cout << kMarker << "{\"event\":\"done\",\"id\":" << quote(id) << ",\"ok\":" << (ok ? "true" : "false")
                  << ",\"rc\":" << rc
                  << ",\"text\":" << quote(run.text)
                  << ",\"first_ms\":" << run.first_ms
                  << ",\"total_ms\":" << total_ms
                  << ",\"vision_ms\":" << vision_ms
                  << ",\"prefill_ms\":" << p.prefill_time_ms
                  << ",\"prefill_tokens\":" << p.prefill_tokens
                  << ",\"generate_ms\":" << p.generate_time_ms
                  << ",\"generate_tokens\":" << p.generate_tokens
                  << ",\"memory_mb\":" << p.memory_usage_mb << "}" << std::endl;
    }
    rkllm_destroy(handle);
    return 0;
}
