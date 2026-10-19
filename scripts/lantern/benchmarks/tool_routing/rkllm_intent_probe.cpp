// Isolated qualification probe. Never installed by the Lantern service scripts.
// Usage: probe MODEL.rkllm RENDERED_PROMPT.txt A_ID B_ID C_ID D_ID E_ID
// IDs must come from the exact exported tokenizer; do not reuse guessed IDs.
// Compile/link on a qualification unit against its matching runtime and header.
#include <cstddef>
#include "rkllm.h"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
struct Probe {
    std::vector<int> ids;
    std::vector<double> logits;
    double label_mass = 0;
    int callbacks = 0;
    bool failed = false;
};

int callback(RKLLMResult* result, void* userdata, LLMCallState state) {
    auto& p = *static_cast<Probe*>(userdata);
    if (state == RKLLM_RUN_ERROR) { p.failed = true; return 0; }
    if (!result || !result->logits.logits) return 0;
    ++p.callbacks;
    const auto& r = result->logits;
    if (p.callbacks != 1 || r.vocab_size <= 0 || r.vocab_size > 1000000 || r.num_tokens <= 0 || r.num_tokens > 2048) {
        p.failed = true; return 2;
    }
    const float* last = r.logits + size_t(r.num_tokens - 1) * size_t(r.vocab_size);
    double peak = -std::numeric_limits<double>::infinity();
    for (int i = 0; i < r.vocab_size; ++i) {
        if (!std::isfinite(last[i])) { p.failed = true; return 2; }
        peak = std::max(peak, double(last[i]));
    }
    double total = 0, selected = 0;
    for (int i = 0; i < r.vocab_size; ++i) total += std::exp(double(last[i])-peak);
    for (int id : p.ids) {
        if (id < 0 || id >= r.vocab_size) { p.failed = true; return 2; }
        p.logits.push_back(last[id]);
        selected += std::exp(double(last[id])-peak);
    }
    p.label_mass = selected/total;
    if (!std::isfinite(p.label_mass) || p.label_mass < 0 || p.label_mass > 1) p.failed = true;
    return 2;  // SDK-defined release of the consumed output buffer.
}
}

int main(int argc, char** argv) {
    if (argc != 8) { std::cerr << "Usage: probe MODEL PROMPT A_ID B_ID C_ID D_ID E_ID\n"; return 2; }
    try {
        Probe p;
        for (int i = 3; i < argc; ++i) {
            std::string s(argv[i]); size_t consumed = 0;
            long value = std::stol(s, &consumed);
            if (consumed != s.size() || value < 0 || value > 1000000) throw std::runtime_error("bad_token_id");
            p.ids.push_back(int(value));
        }
        if (std::set<int>(p.ids.begin(),p.ids.end()).size() != p.ids.size()) throw std::runtime_error("duplicate_token_ids");
        std::ifstream file(argv[2],std::ios::binary);
        if (!file) throw std::runtime_error("prompt_unreadable");
        std::string prompt;
        char c;
        while (file.get(c)) {
            if (prompt.size() >= 16384 || c == '\0') throw std::runtime_error("prompt_bound");
            prompt.push_back(c);
        }
        if (prompt.empty()) throw std::runtime_error("empty_prompt");
        auto param = rkllm_createDefaultParam();
        param.model_path = argv[1];
        param.max_context_len = 2048;
        param.max_new_tokens = 1;
        param.top_k = 1;
        param.temperature = 0;
        param.skip_special_token = false;
        RKLLMCallback cb{}; cb.result_callback = callback;
        LLMHandle handle = nullptr;
        int init = rkllm_init(&handle,&param,&cb);
        if (init != 0) { std::cerr << "rkllm_init_failed " << init << '\n'; return 3; }
        if (rkllm_set_chat_template(handle,"","","") != 0) {
            rkllm_destroy(handle); std::cerr << "chat_template_setup_failed\n"; return 3;
        }
        RKLLMInput input{};
        input.role = "user";
        input.input_type = RKLLM_INPUT_PROMPT;
        input.prompt_input = prompt.c_str();
        RKLLMInferParam infer{};
        infer.mode = RKLLM_INFER_GET_LOGITS;
        infer.keep_history = 0;
        infer.max_new_tokens = 1;
        int code = rkllm_run(handle,&input,&infer,&p);
        rkllm_destroy(handle);
        if (code != 0 || p.failed || p.callbacks != 1 || p.logits.size() != 5) {
            std::cerr << "logit_probe_failed " << code << '\n'; return 4;
        }
        std::cout << "@@RKLLM_INTENT@@ {\"label_mass\":" << std::setprecision(17) << p.label_mass << ",\"logits\":[";
        for (size_t i=0;i<p.logits.size();++i) { if (i) std::cout << ','; std::cout << p.logits[i]; }
        std::cout << "]}\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 2; }
}
