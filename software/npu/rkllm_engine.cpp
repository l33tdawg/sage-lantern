// Persistent RKLLM engine for Lantern: loads one .rkllm artifact once and answers
// JSON-lines requests from stdin on stdout.
//
//   stdin : {"id":"...","prompt":"<fully rendered chat text>","max_new_tokens":128}
//   stdout: {"event":"ready","init_ms":...} once, then
//           {"event":"token","id":"...","text":"..."} per generated piece when the
//           request asks for "stream": true, then
//           {"event":"done","id":"...","ok":true,"text":"...","first_ms":...,
//            "total_ms":...,"prefill_ms":...,"prefill_tokens":...,"generate_ms":...,
//            "generate_tokens":...,"memory_mb":...}
//
// Greedy decoding (top_k=1, temperature=0), matching work/board-validation-20260913 so
// answers stay comparable with the frozen-set runs. The chat template is left empty and
// the caller sends the full rendered transcript; thinking is disabled by the caller's
// assistant prefix and skip_special_token.
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

#include "rkllm.h"

using Clock = std::chrono::steady_clock;

namespace {

// The RKLLM runtime prints its own INFO lines on stdout; every protocol line carries a
// marker so the front end can skip them.
constexpr const char* kMarker = "@@RKLLM_JSON@@ ";

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

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: rkllm_engine model.rkllm [max_context] [cpus] [default_max_new_tokens]\n";
        return 2;
    }
    const char* model = argv[1];
    int max_context = argc > 2 ? std::atoi(argv[2]) : 2048;
    int cpus = argc > 3 ? std::atoi(argv[3]) : 4;
    int default_new_tokens = argc > 4 ? std::atoi(argv[4]) : 256;
    if (max_context < 32 || max_context > 16384) return 2;
    if (cpus != 2 && cpus != 3 && cpus != 4 && cpus != 8) return 2;

    auto init_start = Clock::now();
    RKLLMParam param = rkllm_createDefaultParam();
    param.model_path = model;
    param.max_context_len = max_context;
    param.max_new_tokens = default_new_tokens;
    param.top_k = 1;
    param.top_p = 1;
    param.temperature = 0;
    param.repeat_penalty = 1;
    param.frequency_penalty = 0;
    param.presence_penalty = 0;
    param.skip_special_token = 1;
    param.extend_param.base_domain_id = 0;
    param.extend_param.embed_flash = 1;
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
    if (rkllm_set_chat_template(handle, "", "", "") != 0) {
        std::cout << kMarker << "{\"event\":\"error\",\"message\":\"rkllm_set_chat_template failed\"}" << std::endl;
        rkllm_destroy(handle);
        return 1;
    }
    std::cout << kMarker << "{\"event\":\"ready\",\"init_ms\":" << init_ms
              << ",\"max_context\":" << max_context << ",\"cpus\":" << cpus
              << ",\"default_max_new_tokens\":" << default_new_tokens << "}" << std::endl;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::string id, prompt;
        long max_new_tokens = default_new_tokens;
        bool stream = false;
        if (!json_string(line, "prompt", prompt) || prompt.empty()) {
            std::cout << kMarker << "{\"event\":\"done\",\"ok\":false,\"error\":\"missing prompt\"}" << std::endl;
            continue;
        }
        json_string(line, "id", id);
        json_long(line, "max_new_tokens", max_new_tokens);
        json_bool(line, "stream", stream);
        if (max_new_tokens < 1 || max_new_tokens > 1024) max_new_tokens = default_new_tokens;

        if (rkllm_clear_kv_cache(handle, 0, nullptr, nullptr) != 0) {
            std::cout << kMarker << "{\"event\":\"done\",\"id\":" << quote(id)
                      << ",\"ok\":false,\"error\":\"cache clear failed\"}" << std::endl;
            continue;
        }
        RKLLMInput input{};
        input.role = "user";
        input.input_type = RKLLM_INPUT_PROMPT;
        input.enable_thinking = false;
        input.prompt_input = prompt.c_str();
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
                  << ",\"prefill_ms\":" << p.prefill_time_ms
                  << ",\"prefill_tokens\":" << p.prefill_tokens
                  << ",\"generate_ms\":" << p.generate_time_ms
                  << ",\"generate_tokens\":" << p.generate_tokens
                  << ",\"memory_mb\":" << p.memory_usage_mb << "}" << std::endl;
    }
    rkllm_destroy(handle);
    return 0;
}
