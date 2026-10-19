// Persistent RKLLM engine for Lantern: loads one .rkllm artifact once and answers
// JSON-lines requests from stdin on stdout.
//
//   stdin: strict server-owned JSON; see token_protocol.hpp. IDs are encoded on C per request.
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
#include <atomic>
#include <dlfcn.h>
#include <limits.h>
#include <unistd.h>
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
#include "token_protocol.hpp"
#include "build_binding.hpp"

using Clock = std::chrono::steady_clock;

namespace {

// The RKLLM runtime prints its own INFO lines on stdout; every protocol line carries a
// marker so the front end can skip them.
constexpr const char* kMarker = "@@RKLLM_JSON@@ ";
std::atomic<bool> outside_callback_error{false};

struct Run {
    Clock::time_point start;
    double first_ms = -1;
    bool finished = false;
    int finish_count = 0;
    bool perf_observed = false;
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

void ids_json(std::ostream& out, const std::vector<std::int32_t>& ids) {
    out << '[';
    for (std::size_t i=0; i<ids.size(); ++i) { if (i) out << ','; out << ids[i]; }
    out << ']';
}

std::string mapped_runtime() {
    Dl_info info{};
    char resolved[PATH_MAX];
    if (!dladdr(reinterpret_cast<const void*>(&rkllm_init), &info) || !info.dli_fname || !realpath(info.dli_fname, resolved)) return "";
    return resolved;
}

int callback(RKLLMResult* result, void* userdata, LLMCallState state) {
    // A callback outside a request has no Run object; preserve SDK status via
    // stderr without inventing a request ID or emitting a protocol event.
    if (userdata == nullptr) {
        if (state == RKLLM_RUN_ERROR) {
            outside_callback_error = true;
            std::cerr << "E rkllm: native callback error without request userdata" << std::endl;
        }
        return 0;
    }
    auto& run = *static_cast<Run*>(userdata);
    if (state == RKLLM_RUN_ERROR) run.failed = true;
    if (state == RKLLM_RUN_FINISH) {
        run.finished = true; ++run.finish_count;
        if (result) { run.perf = result->perf; run.perf_observed = true; }
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

static_assert(sizeof(RKLLMInput) == 208 && sizeof(RKLLMCallback) == 48, "SDK 1.3.1 LP64 ABI");
static_assert(sizeof(RKLLMTokenInput) == 16 && RKLLM_INPUT_TOKEN == 1, "SDK TOKEN ABI");

int main(int argc, char** argv) {
    if (argc != 6) {
        std::cerr << "usage: rkllm_engine model.rkllm max_context cpus default_max_new_tokens absolute_runtime_path\n";
        return 2;
    }
    const char* model = argv[1];
    char expected_runtime[PATH_MAX];
    const std::string runtime_path = mapped_runtime();
    if (argv[5][0] != '/' || !realpath(argv[5], expected_runtime) || runtime_path != expected_runtime) {
        std::cerr << "rkllm_engine: mapped runtime differs from explicit binding" << std::endl;
        return 2;
    }
    int max_context, cpus, default_new_tokens;
    if (!token_protocol::integer_arg(argv[2], 32, 32768, max_context) ||
        !token_protocol::integer_arg(argv[3], 1, 8, cpus) ||
        !token_protocol::integer_arg(argv[4], 1, 8192, default_new_tokens) ||
        default_new_tokens >= max_context) return 2;
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
    param.extend_param.base_domain_id = 2;
    param.extend_param.embed_flash = 1;
    param.extend_param.enabled_cpus_num = cpus;
    param.extend_param.enabled_cpus_mask = cpus == 2 ? 0xc0 : cpus == 3 ? 0xe0 : cpus == 4 ? 0xf0 : 0xff;
    if (param.is_async) {
        std::cout << kMarker << "{\"event\":\"error\",\"message\":\"unexpected asynchronous SDK default\"}" << std::endl;
        return 1;
    }

    RKLLMCallback cb{};
    cb.result_callback = callback;
    LLMHandle handle = nullptr;
    int code = rkllm_init(&handle, &param, &cb);
    double init_ms = std::chrono::duration<double, std::milli>(Clock::now() - init_start).count();
    if (code != 0) {
        std::cout << kMarker << "{\"event\":\"error\",\"message\":\"rkllm_init failed\",\"code\":" << code << "}" << std::endl;
        return 1;
    }
    if (!handle || outside_callback_error) {
        std::cout << kMarker << "{\"event\":\"error\",\"message\":\"init returned null handle or callback error\"}" << std::endl;
        if (handle) rkllm_destroy(handle);
        return 1;
    }
    if (rkllm_set_chat_template(handle, "", "", "") != 0 || outside_callback_error) {
        std::cout << kMarker << "{\"event\":\"error\",\"message\":\"rkllm_set_chat_template failed\"}" << std::endl;
        rkllm_destroy(handle);
        return 1;
    }
    std::cout << kMarker << "{\"event\":\"ready\",\"init_ms\":" << init_ms
              << ",\"max_context\":" << max_context << ",\"cpus\":" << cpus
              << ",\"default_max_new_tokens\":" << default_new_tokens
              << ",\"input_type\":\"TOKEN\",\"protocol\":\"lantern.rkllm.token-input.v1\""
              << ",\"compiled_source_binding_sha256\":" << quote(kCompiledSourceBindingSha256)
              << ",\"expected_header_sha256\":" << quote(kExpectedHeaderSha256)
              << ",\"expected_runtime_sha256\":" << quote(kExpectedRuntimeSha256)
              << ",\"runtime_loaded_path\":" << quote(runtime_path) << "}" << std::endl;

    std::string line;
    Run run; // Callback userdata remains alive until after final destroy.
    int exit_code = 0;
    while (true) {
        if (outside_callback_error) { exit_code = 1; break; }
        const int read_status = token_protocol::read_line(std::cin, line);
        if (outside_callback_error) { exit_code = 1; break; }
        if (read_status == 0) break;
        token_protocol::Request request;
        if (read_status < 0 || !token_protocol::parse(line, max_context, request)) {
            std::cout << kMarker << "{\"event\":\"error\",\"message\":\"invalid bounded TOKEN request\"}" << std::endl;
            exit_code = 1; break;
        }
        const std::string id = request.id;
        const long max_new_tokens = request.max_new_tokens;
        const auto original_ids = request.ids;

        if (rkllm_clear_kv_cache(handle, 0, nullptr, nullptr) != 0 || outside_callback_error) {
            std::cout << kMarker << "{\"event\":\"done\",\"id\":" << quote(id)
                      << ",\"ok\":false,\"error\":\"cache clear failed\"}" << std::endl;
            exit_code = 1; break;
        }
        RKLLMInput input{};
        const char* expected_role = "user";
        input.role = expected_role;
        input.input_type = RKLLM_INPUT_TOKEN;
        input.enable_thinking = false;
        input.token_input.input_ids = request.ids.data();
        input.token_input.n_tokens = request.ids.size();
        RKLLMInferParam infer{};
        infer.mode = RKLLM_INFER_GENERATE;
        infer.keep_history = 0;
        infer.max_new_tokens = static_cast<int>(max_new_tokens);

        std::cout << kMarker << "{\"event\":\"input_delivery\",\"id\":" << quote(id)
                  << ",\"input_type\":\"TOKEN\",\"prompt_utf8_hex\":" << quote(request.prompt_hex)
                  << ",\"input_ids\":";
        ids_json(std::cout, request.ids);
        std::cout << ",\"n_tokens\":" << request.ids.size() << ",\"max_new_tokens\":" << max_new_tokens
                  << ",\"keep_history\":0,\"clear_rc\":0,\"compiled_source_binding_sha256\":"
                  << quote(kCompiledSourceBindingSha256) << "}" << std::endl;
        run = Run{};
        run.id = id;
        run.stream = request.stream;
        run.start = Clock::now();
        if (outside_callback_error) { exit_code = 1; break; }
        int rc = rkllm_run(handle, &input, &infer, &run);
        double total_ms = std::chrono::duration<double, std::milli>(Clock::now() - run.start).count();
        const auto& p = run.perf;
        const bool descriptor_unchanged = input.input_type == RKLLM_INPUT_TOKEN &&
            input.role == expected_role && !input.enable_thinking &&
            infer.mode == RKLLM_INFER_GENERATE && infer.keep_history == 0 && infer.max_new_tokens == max_new_tokens &&
            input.token_input.input_ids == request.ids.data() && input.token_input.n_tokens == request.ids.size();
        const bool buffer_unchanged = request.ids == original_ids;
        bool ok = rc == 0 && run.finished && run.finish_count == 1 && run.perf_observed && !run.failed &&
            buffer_unchanged && descriptor_unchanged && !outside_callback_error;
        std::cout << kMarker << "{\"event\":\"done\",\"id\":" << quote(id) << ",\"ok\":" << (ok ? "true" : "false")
                  << ",\"rc\":" << rc
                  << ",\"text\":" << quote(run.text)
                  << ",\"first_ms\":" << run.first_ms
                  << ",\"total_ms\":" << total_ms
                  << ",\"prefill_ms\":" << p.prefill_time_ms
                  << ",\"prefill_tokens\":" << p.prefill_tokens
                  << ",\"generate_ms\":" << p.generate_time_ms
                  << ",\"generate_tokens\":" << p.generate_tokens
                  << ",\"memory_mb\":" << p.memory_usage_mb
                  << ",\"input_type\":\"TOKEN\",\"input_ids_after\":";
        ids_json(std::cout, request.ids);
        std::cout << ",\"input_buffer_unchanged\":" << (buffer_unchanged ? "true" : "false")
                  << ",\"input_descriptor_unchanged\":" << (descriptor_unchanged ? "true" : "false")
                  << ",\"outside_callback_error\":" << (outside_callback_error ? "true" : "false")
                  << ",\"finish_count\":" << run.finish_count << ",\"native_eos\":null}" << std::endl;
        if (!ok) { exit_code = 1; break; }
    }
    const int destroy_rc = rkllm_destroy(handle);
    return destroy_rc == 0 && !outside_callback_error ? exit_code : 1;
}
