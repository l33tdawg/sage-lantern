# ROCK 5B+ 8GB inference review — 2026-09-07

**Research only; no target access, downloads, installation or configuration changes.** External evidence below is exclusively official maintainer/vendor documentation, accessed 2026-09-07. Alternatives remain untested on this device; mutable sources must be revision-pinned before implementation.

## Baseline and decision

User-reported current baseline: Qwen3.5-4B Q4_K_M, llama.cpp CPU, six threads, automatic parallelism resolving to four slots; 1,058 prompt tokens in 61.79 s and generation at 4.39 tokens/s. Arithmetic gives approximately **17.12 prompt tokens/s**, not an isolated text-prefill benchmark or end-to-end photo TTFT. Separate image encoding, prefill, queueing and first visible answer before assigning a bottleneck.

Local evidence in `UNIT_A_PERFORMANCE_20260905.md` rejects blind A76 affinity: six threads/all CPUs gave photo TTFT 37.187/37.217 s versus 41.979/41.843 s with six threads confined to CPUs 4–7; four confined threads gave 39.107/39.275 s. That earlier record used one slot, so it does not describe today's four-slot configuration. Preserve six-thread unrestricted placement as the comparison baseline.

**Priority:** explicit single-slot/prompt profiling → smaller matched CPU model → Vulkan only if the installed stack permits it → separate RKLLM integration. None is a promised speedup.

## 1. Existing CPU path: low implementation cost

The pinned llama.cpp server documents `--parallel`, separate `--threads-batch`, batch/microbatch controls and timing/cache counters. For a single-user appliance, compare explicit `--parallel 1` against observed auto-four, holding effective per-request context and cache conditions constant. Four slots do not mean four requests are actively consuming compute or imply a fourfold speedup when removed. Independently sweep prompt threads and batch sizes while retaining six decode threads and unrestricted placement. [llama.cpp b9637 server](https://github.com/ggml-org/llama.cpp/blob/b9637/tools/server/README.md)

**Proposed gates:** inspect resolved slot/context allocations and memory first; reduce redundant recalled text without weakening instruction/data separation. Keep output concise, but do not call reduced generation length a TTFT improvement. Do not further shrink photos blindly: Arabic glyph preservation is a quality requirement. Batch increases can raise transient memory; one slot can reduce concurrency and alter context allocation. No default changes are authorized here.

## 2. Mali-G610 Vulkan: feasible stack, unproven acceleration

Mesa explicitly reports **PanVK conformance on Mali-G610**. Therefore “Mali cannot run Vulkan” is incorrect. PanVK is the Vulkan userspace implementation, not synonymous with the Panthor kernel driver. [Mesa Panfrost/PanVK](https://docs.mesa3d.org/drivers/panfrost.html)

Radxa's Bookworm driver-switch page nevertheless marks its Panthor option as lacking Vulkan and proprietary Mali as supporting it. Treat this as a packaged-stack distinction, not evidence that upstream PanVK lacks support; neither page identifies this board's installed stack. Radxa also warns of desktop rendering problems when components are mismatched. [Radxa driver guidance](https://docs.radxa.com/en/rock5/rock5a/radxa-os/mali-gpu)

llama.cpp b9637 provides `GGML_VULKAN=1`, recommends checking `vulkaninfo`, and demonstrates GPU-layer offload. This establishes a build route, not Qwen3.5 hybrid-layer or vision-projector correctness/performance on G610. Its OpenCL instructions target Adreno and do not establish a preferable Mali route. [Pinned build guide](https://github.com/ggml-org/llama.cpp/blob/b9637/docs/build.md)

**Implementability: medium if compatible drivers already exist; high if OS/graphics changes are needed.** A future authorized inventory should identify kernel, GPU module, Mesa/libmali, Vulkan ICD and actual hardware device—not software Vulkan. Then test an isolated build, partial/full offload and projector placement separately, checking logs for real execution/fallback and outputs against CPU. Shared 8GB RAM is not extra GPU memory. Risks: unsupported operations, synchronization overhead, memory pressure and kiosk regressions. Do not replace the working graphics stack merely to enable an experiment; require rollback and display/wake acceptance.

## 3. RKLLM NPU: supported, but a different runtime

Rockchip release-v1.3.0 explicitly adds Qwen3.5. Supported families include Qwen2/2.5/3/3.5, Llama/TinyLlama, Phi2/3, Gemma variants and several VLMs. Crucially, its RK3588 quickstart demonstrates **Qwen3.5-0.8B W8A8 + RKNN vision**, Qwen3-VL-2B and Qwen2.5-VL-3B. This is not an NPU flag for the existing Q4_K_M GGUF/BF16 projector: host-side conversion produces RKLLM language artifacts and RKNN vision artifacts for Rockchip runtimes. [Rockchip release README](https://github.com/airockchip/rknn-llm/blob/release-v1.3.0/README.md)

Official RK3588 benchmark figures provide scale, not appliance predictions:

| Qwen3.5 W8A8 | Vendor TTFT | Decode tokens/s | Reported memory MB |
|---|---:|---:|---:|
| 0.8B | 587.74 ms | 27.05 | 1039.66 |
| 2B | 776.95 ms | 13.59 | 2121.91 |
| 4B | 2068.88 ms | 6.22 | 4575.78 |

These use **128 input/64 output tokens and maximum CPU/NPU frequencies**; the page specifies conversion `optimization_level=0`. Thus 4B does have official benchmark evidence, beyond family-level support. These rows do not establish complete photo-pipeline timing or total host/vision memory. Do not compare their TTFT directly with 1,058-token/61.79-second processing, transfer RK3576 quantization results to RK3588, or run frequency scripts blindly. [Rockchip benchmark](https://github.com/airockchip/rknn-llm/blob/main/benchmark.md)

**Implementability: high integration cost.** The multimodal example requires RKLLM toolkit ≥1.3.0 and RKNN toolkit2 ≥2.3.2, model-specific exports and preprocessing. Its sample quantization uses 20 English MMBench examples—not multilingual calibration assurance. Validate image shapes/tokens and representative Arabic/Malay/English calibration and output quality. [Official multimodal example](https://github.com/airockchip/rknn-llm/tree/main/examples/multimodal_model_demo)

Before any trial, pin compatible toolkit/runtime/kernel-driver versions and artifact hashes; verify licenses separately for weights and SDK binaries. Integrate streaming, cancellation, timeout, serialization and lock/session cleanup rather than assuming demo API parity. The 4B memory figure leaves limited headroom for vision, context, browser and other services; prefer the documented 0.8B pair for an initial feasibility trial, not an immediate production replacement.

## 4. Smaller multilingual text/vision candidates

| Candidate | Why test it | Main risk |
|---|---|---|
| **Qwen3.5-2B** | Lower language-side capacity/cost; natural first quality-oriented CPU comparison. | Vision encoder remains 24 layers, width 1024, like 4B: do not expect halved image-encoding time. [2B config](https://huggingface.co/Qwen/Qwen3.5-2B/blob/main/config.json), [4B config](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json) |
| **Qwen3.5-0.8B** | Vision encoder is 12 layers, width 768: inference is that it targets both vision and language cost more directly. | Greater quality risk; architecture dimensions do not predict speed ratios. [0.8B config](https://huggingface.co/Qwen/Qwen3.5-0.8B/blob/main/config.json) |
| **Qwen3-VL-2B-Instruct** | Independent vision/OCR comparison with an official RKLLM example; Qwen advertises 32-language OCR. | Different architecture/projector; family claims do not prove Arabic photo accuracy or faster G610/CPU execution. [Official model card](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) |

Qwen3.5 cards advertise 201 languages/dialects and Apache-2.0; 0.8B/2B operate in non-thinking mode by default. Use them for text-only requests without image work, and matched model-specific projectors for photos. Treat multilingual coverage as a candidate-selection signal, not local quality acceptance. [0.8B card](https://huggingface.co/Qwen/Qwen3.5-0.8B), [2B card](https://huggingface.co/Qwen/Qwen3.5-2B)

## Acceptance before changing the baseline

Future experiments only: one cold plus at least five alternating warm runs per candidate, one variable at a time, identical images/questions/recall/output budgets and recorded cache state. Report median/range, image encoding, prefill, model TTFT, first visible answer, decode rate, peak host/service memory, swap and temperature. Do not infer p95 from five runs.

Require human-checked Arabic/Malay/English text and photo questions, OCR names/numbers, honest unreadability, injection resistance, cancellation and lock/restart tests. Stop on OOM, growing swap pressure, instability or quality regression; restore the working baseline. No second resident LLM, new package, model download, driver change or target benchmark was performed for this review.

## Deployment benchmark reviewer handoff — 2026-09-07

**Ownership:** Curie alone owns A live writes and guarded runs; Lovelace owns retrieval. Reviewer performs no A mutations. NPU/GPU/model replacement proposals above are outside this deployment. Direct coordination is pending: this session has no subagent messaging tool, and SAGE name lookup returned no Curie/Lovelace matches. This written handoff is not evidence they received or agreed to it.

### Fixed synthetic protocol for Curie and Lovelace

1. Freeze a synthetic request before either configuration runs: for example, “A box contains 12 red pencils and 8 blue pencils. How many pencils are there altogether? Answer in one sentence.” Use identical UTF-8 request bytes, a pinned synthetic retrieval fixture and rendered prompt hash. For STT-inclusive tests, freeze one audio file/hash rather than re-recording. Keep tokenizer/model/build, sampling/seed, output cap and workload concurrency fixed; record actual completion lengths and EOS/stop reasons. An equal cap does not guarantee equal output work.
2. Separate **engine comparison** (identical rendered prompt and retrieval payload) from **retrieval/product comparison** (identical question and source fixture, but Lovelace may change selected context). Label the latter workload reduction/end-to-end improvement, not pure inference acceleration. Run baseline, single-slot/cache candidate, retrieval-only candidate and combined candidate separately where feasible; otherwise attribute only the combined result.
3. Distinguish first request after model-process start, resident-model prompt-cache miss, and repeated-prefix cache hit. A process restart is not OS-page-cache cold. Curie alone controls any authorized restart; do not drop system caches or bypass inference guards. If no safe cold run is feasible, mark it unmeasured. Record startup/warmup separately, then at least **three measured warm repeats per configuration and workload** (five preferred). Alternate baseline/candidate if safe; otherwise disclose sequential-block thermal/order bias. Keep cache-hit and cache-miss samples separate.
4. Capture per-run monotonic durations: queue/admission, STT, protected retrieval, image encoding if applicable, prompt processing, LLM TTFT, first visible answer, generation and total. Identify each start/end boundary; do not add nested stages or silently equate runtime text with pixels displayed. Record prompt processed/cached tokens, generated tokens, prompt/decode rates, resolved slots/context, peak service RSS/cgroup memory, host available memory, swap delta, CPU utilization/frequencies and temperature during runs. Missing instrumentation is N/A, not zero. No photo comparison unless Curie first establishes a working matched projector.
5. Retain every run and failure with configuration/fixture hashes and synthetic correctness result. Report warm median and range plus sample count; cold samples separately. Compute latency reduction as 100 × (baseline median − candidate median) / baseline median only for comparable work/cache class. For unequal completions, show token counts, decode rates and product latency without claiming total time is pure speedup. Three repeats are a small sanity check, not p95 or statistical confidence.

### Available-results review and limitations

The local `UNIT_A_PERFORMANCE_20260907.md` records **one owner request**, not this synthetic workload: STT 2.350 s; protected retrieval 2.332 s; LLM first-visible-token metric 61.901 s; first runtime text 66.584 s; LLM complete 73.735 s; stage sum 78.417 s. Server prompt processing was 61.790 s / 1,058 tokens (17.12 tokens/s); generation 11.835 s / 52 tokens (4.39 tokens/s). These support prompt processing dominating that request, not attribution to automatic four slots.

That record reports no current projector, four automatic slots, six threads, unlimited model cgroup and an 8 GiB prompt-cache limit. Post-run available memory (~3.6 GiB), swap used (1 MiB) and earlier idle temperatures (50–52°C) are not peak memory, swap growth or in-run thermal evidence. Earlier photo timings are a different configuration/workload and cannot supply the missing matched “before.”

**Before/after acceptance remains pending:** no candidate run results were available to this reviewer. Curie should provide per-run baseline/candidate rows with the fields above; Lovelace should identify rendered prompt/token changes and synthetic retrieval correctness. Do not publish a deployment speedup until those actual numbers and limitations have been reviewed.
