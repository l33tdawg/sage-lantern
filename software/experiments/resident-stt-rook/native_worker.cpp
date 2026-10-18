// Isolated lab driver: no capture, listener, authentication or service wiring.
// Compile only against the repository-locked whisper.cpp 1.8.6 sources.
// The upstream MIT notice remains in the extracted source tree.
#define main pinned_whisper_cli_main
#include "cli/cli.cpp"
#undef main
#include "json.hpp"
#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <memory>
#include <sys/resource.h>

using json = nlohmann::ordered_json;
using Clock = std::chrono::steady_clock;
static std::atomic<bool> aborted{false};
static_assert(std::atomic<bool>::is_always_lock_free, "signal cancellation must be lock-free");
static void cancel_signal(int) { aborted.store(true, std::memory_order_relaxed); }
static double ms(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double, std::milli>(b-a).count();
}
static void emit(const json & value) { std::cout << value.dump() << std::endl; }
static whisper_full_params decoding(const whisper_params & params) {
    // Generated verbatim from the pinned CLI's parameter-construction block.
    // Includes default strategy, thresholds, language and all CLI overrides.
    #include "cli_parameters.inc"
    wparams.encoder_begin_callback = [](whisper_context *, whisper_state *, void *) {
        return !aborted.load(std::memory_order_relaxed);
    };
    wparams.abort_callback = [](void *) { return aborted.load(std::memory_order_relaxed); };
    return wparams;
}
int main(int argc, char ** argv) {
    std::signal(SIGUSR1, cancel_signal);
    whisper_params params;
    if (!whisper_params_parse(argc, argv, params)) return 2;
    // Bounded parity lane only; unsupported CLI features cannot drift silently.
    if (params.n_threads != 6 || params.best_of != 2 || params.beam_size != 1
        || !params.no_timestamps || params.n_processors != 1 || params.diarize
        || params.vad || !params.grammar.empty() || !params.dtw.empty()
        || !params.fname_inp.empty() || !params.fname_out.empty()
        || !(params.language == "en" || params.language == "auto" || params.language == "ms")) return 2;
    auto cparams = whisper_context_default_params();
    cparams.use_gpu = params.use_gpu;
    cparams.gpu_device = params.gpu_device;
    cparams.flash_attn = params.flash_attn;
    auto started = Clock::now();
    std::unique_ptr<whisper_context, decltype(&whisper_free)> ctx(
        whisper_init_from_file_with_params_no_state(params.model.c_str(), cparams), whisper_free);
    if (!ctx) return 3;
    emit({{"event","ready"},{"model_load_ms",ms(started,Clock::now())},
          {"language",params.language},{"no_context",decoding(params).no_context}});
    std::string path;
    int sequence = 0;
    while (std::getline(std::cin,path)) {
        if (path == "quit") break;
        if (path.empty() || path.size() > 4096 || path[0] != '/') return 2;
        aborted.store(false, std::memory_order_relaxed);
        std::vector<float> samples;
        std::vector<std::vector<float>> stereo;
        auto a = Clock::now();
        if (!read_audio_data(path,samples,stereo,false) || samples.empty()
            || samples.size() > 16000*120) return 4;
        auto b = Clock::now();
        std::unique_ptr<whisper_state, decltype(&whisper_free_state)> state(
            whisper_init_state(ctx.get()), whisper_free_state);
        if (!state) return 5;
        auto c = Clock::now();
        emit({{"event","begin"},{"sequence",sequence}});
        auto wparams = decoding(params);
        const int code = whisper_full_with_state(ctx.get(),state.get(),wparams,samples.data(),samples.size());
        auto d = Clock::now();
        std::string text;
        const bool cancelled = aborted.load(std::memory_order_relaxed);
        if (code == 0 && !cancelled) {
            for (int i=0; i<whisper_full_n_segments_from_state(state.get()); ++i)
                text += std::string(whisper_full_get_segment_text_from_state(state.get(),i)) + "\n";
        }
        state.reset();
        samples.clear(); samples.shrink_to_fit();
        stereo.clear(); stereo.shrink_to_fit();
        auto e = Clock::now();
        rusage usage{}; getrusage(RUSAGE_SELF,&usage);
        emit({{"event","final"},{"sequence",sequence++},{"text",text},
              {"code",code},{"cancelled",cancelled},{"state_freed",true},
              {"prepare_ms",ms(a,b)},{"state_init_ms",ms(b,c)},
              {"decode_ms",ms(c,d)},{"state_free_ms",ms(d,e)},
              {"peak_rss_platform_units",usage.ru_maxrss}});
    }
    ctx.reset();
    emit({{"event","closed"},{"model_freed",true}});
    return 0;
}
