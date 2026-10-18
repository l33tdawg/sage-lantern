# RK3588 performance options — research, not acceptance

**Primary sources accessed:** 2026-09-05.  
**Scope:** ROCK 5B/5B+, RK3588, approximately 8 GB RAM; local PTT speech and photo-to-text. No always-listening operation.  
**Evidence boundary:** Every alternative model, NPU/GPU path, resident Whisper worker and quantized Whisper candidate below is **UNTESTED ON UNIT A**. Parent-owned CPU/Whisper experiments are in progress; this document reports no outcomes. The parent owns measured release/evidence documentation. No downloads, code changes or live operations were performed for this brief.

**Update 18 October 2026:** Unit C now runs a measured 3B production lane; see
[3B production lane on C](#3b-production-lane-on-c--18-october-2026) at the end.
The 2026-09-05 research snapshot and Unit A boundaries below are unchanged.

The comparison baseline is the existing Qwen3.5-4B Q4_K_M with its matching BF16 projector on CPU, llama.cpp `b9637`, and whisper.cpp `v1.8.6` multilingual base invoked through a fresh CLI process with six threads. The model service has a 5 GiB memory limit. These identify the baseline, not proof that an alternative fits or runs faster.

**Provisional priority:** benchmark the existing CPU path and request-only Whisper residency before changing toolchains. For photo latency, distinguish vision encoding from language prefill: a smaller language model does not necessarily have a smaller vision encoder. Preserve Arabic readability; do not trade away image detail to claim speed.

## Ranked options

| Rank | Candidate and source-backed feasibility | Boundary, risk and next gate |
|---|---|---|
| 1 | **CPU affinity and separate prompt/decode thread counts.** RK3588 has four Cortex-A76 and four Cortex-A55 cores. llama.cpp `b9637` exposes `--threads`, `--threads-batch` and separate CPU affinity controls. Compare the current six-thread setup against four verified A76 cores; vary prompt threads separately. [Radxa hardware](https://radxa.com/products/rock5/5b/), [pinned llama server options](https://github.com/ggml-org/llama.cpp/blob/b9637/tools/server/README.md). | **RESULT 2026-09-13: four threads wins on the text path and is now the shipped default** (`software/systemd/sage-lantern-llm.service`; evidence `work/cpu-tier-prefill-20260913/REPORT.md`, 11-arm bisect, noise band 0.57%). Four threads beat the previously shipped six by ~25-30% on the warm answer path, which is what a question actually pays; eight threads gains ~4% on a cold full prefill and loses ~24% warm. **Affinity was deliberately not promoted**: pinning to cores 4-7 measured neutral on text (-0.2% cold, +0.0% warm), and `RK3588_PERFORMANCE_REVIEW_20260907.md` already found all-CPU placement beat 4-7 confinement on photo TTFT (37.2 s vs 41.9 s). Still open: whether placement controls reach image-encoder workers, and the **photo** path's own thread count - the bisect had no image in it. Avoid realtime priority, overclocking or blanket frequency changes. |
| 2 | **Resident, request-only Whisper base worker.** The `v1.8.6` server maintains model context across requests, providing a path to avoid repeated initialization without replacing the model. [Pinned server source](https://github.com/ggml-org/whisper.cpp/blob/v1.8.6/examples/server/server.cpp), [server interface](https://github.com/ggml-org/whisper.cpp/blob/v1.8.6/examples/server/README.md). | **UNTESTED ON UNIT A.** Measure initialization cost before predicting savings. Residency retains memory between requests. Bind locally, serialize work, reset per-request state, bound temporary files and preserve cancellation. A resident model must not continuously capture audio: microphone use remains PTT-triggered. |
| 3 | **Quantized multilingual Whisper base, initially Q5_0.** Integer quantization is supported upstream; its documentation says memory requirements fall and processing efficiency depends on hardware. [Pinned quantization documentation](https://github.com/ggml-org/whisper.cpp/blob/v1.8.6/README.md#quantization). | **UNTESTED ON UNIT A.** Compare against the same unquantized multilingual base, not `base.en`. Arabic transcription accuracy and latency may regress. Preserve the original model, record the quantizer build and output hash, and measure rather than assume improvement. |
| 4 | **Qwen3.5-2B for language-side work; Qwen3.5-0.8B for a smaller-encoder experiment.** Official configs show 2B and 4B both have 24-layer, width-1024 vision encoders; 0.8B uses 12 layers, width 768. Inference: 2B primarily targets language-side cost, while 0.8B more directly targets vision-encoder cost. [2B config](https://huggingface.co/Qwen/Qwen3.5-2B/blob/main/config.json), [4B config](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json), [0.8B config](https://huggingface.co/Qwen/Qwen3.5-0.8B/blob/main/config.json). | **Both candidates UNTESTED ON UNIT A.** No speed ratio or Arabic quality claim follows from layer counts. Each requires its own matching GGUF/projector conversion and installed-build validation; similar encoder dimensions do not make projectors interchangeable. Both cards declare Apache-2.0. [2B card](https://huggingface.co/Qwen/Qwen3.5-2B), [0.8B card](https://huggingface.co/Qwen/Qwen3.5-0.8B). |
| 5 | **Rockchip RKNN/RKLLM vision path.** `release-v1.3.0` explicitly documents Qwen3.5 support and an RK3588 Qwen3.5-0.8B W8A8 language model plus RKNN vision encoder example. It also includes a Qwen3-VL-2B example. [Pinned Rockchip README](https://github.com/airockchip/rknn-llm/blob/release-v1.3.0/README.md). | **UNTESTED ON UNIT A.** This requires a separate conversion/runtime integration, not an NPU switch for the installed Q4_K_M GGUF/BF16 projector. Verify target-specific artifacts, driver/toolkit/runtime compatibility, image preprocessing, streaming and cancellation. The documented 0.8B example does not prove that the installed 4B pair works on NPU. |
| 6 | **Mali GPU experiment after driver inventory.** llama.cpp `b9637` documents Vulkan builds; its OpenCL build instructions describe recent Adreno GPUs, not established compatibility with this Mali installation. Radxa documents GPU-driver alternatives that affect graphics/compute support. [Pinned build guide](https://github.com/ggml-org/llama.cpp/blob/b9637/docs/build.md), [Radxa GPU-driver documentation](https://docs.radxa.com/en/rock5/rock5a/radxa-os/mali-gpu). | **UNTESTED ON UNIT A.** Hardware API capability does not establish installed-driver support for the required model/projector operations. Require device enumeration, actual offload evidence and correct output. Do not infer speed from desktop GPUs or count shared GPU memory as extra RAM. Driver changes risk kiosk regressions and need a separate rollback plan. |
| 7 | **Alternative vision models.** Qwen3-VL-2B-Instruct is an Apache-2.0 comparison candidate. SmolVLM-500M has a 93M vision encoder and Apache-2.0 license, but its card explicitly lists English. [Qwen card](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct), [SmolVLM card](https://huggingface.co/HuggingFaceTB/SmolVLM-500M-Instruct). | **UNTESTED ON UNIT A.** SmolVLM is not recommended as an Arabic-reading replacement without evidence. Neither model is a drop-in substitute for the existing projector. Validate conversion, prompts, quality and memory before considering activation. |
| 8 | **Rockchip-converted Whisper.** The official model-zoo example documents RK3588 encoder/decoder conversion and English/Chinese recognition examples. [Official Whisper example](https://github.com/airockchip/rknn_model_zoo/blob/main/examples/whisper/README.md). | **UNTESTED ON UNIT A.** Treat this as a later port, not a replacement already equivalent to whisper.cpp. The cited example does not establish Arabic parity; check language tokens, audio window behavior, conversion constraints and error/cancellation handling. |

## Arabic and licensing constraints

- **Arabic voice is not validated by this research.** whisper.cpp `v1.8.6` CLI defaults to `language = "en"`; a multilingual model file alone does not select Arabic. Confirm deployed arguments. A controlled experiment may compare explicit `ar` with `auto`, using transcription rather than translation. Arabic text in a photograph is a separate capability from Arabic speech recognition. [Pinned CLI source](https://github.com/ggml-org/whisper.cpp/blob/v1.8.6/examples/cli/cli.cpp).
- **No further image shrinkage as a speed optimization.** Preserve the full-resolution source and distinguish preview from inference. Compare user-selected Arabic text crops or higher-detail inference as separate quality experiments, not mixed into a model/CPU speed comparison. Measure character errors, invented characters and honest unreadability responses; small color fixtures do not establish OCR quality.
- **Research snapshots are not deployment pins.** The cited model `main` pages and Rockchip model-zoo `main` example are mutable. Freeze repository revisions, source/conversion provenance, model/projector hashes and runtime builds before any authorized trial. Existing toolchain tags are references for investigation, not permission for blind upgrades.
- **Model and SDK licensing are separate.** Qwen/SmolVLM cards declare Apache-2.0; whisper.cpp declares MIT. Preserve notices and inspect the exact converted-artifact provenance. Rockchip SDK/runtime packages and included dependencies require their own review; do not infer their terms from the model license. [Pinned whisper.cpp README](https://github.com/ggml-org/whisper.cpp/blob/v1.8.6/README.md), [Rockchip SDK repository license](https://github.com/airockchip/rknn-llm/blob/release-v1.3.0/LICENSE).
- **Mynah:** no source-backed, supplied port summary was available to this review. No Mynah candidate or performance number is asserted; the parent can assess its code summary separately.

## Bounded benchmark and acceptance gates

1. **Freeze the baseline.** Record executable/model/projector hashes, flags, actual CPU topology, driver versions, context/image-token budgets, image bytes and recalled context. Keep one LLM service, parallelism 1, existing model memory/swap limits and working rollback configuration. Reserve host capacity for browser, SAGE and STT; a model cgroup limit is not a guaranteed host memory reserve.
2. **CPU and prompt round.** Compare the baseline against verified A76 placement; then independently compare the compact and original prompt. Change one variable at a time. Use the same image bytes, question, recall payload, output budget and cache conditions. Confirm image-encoder thread behavior rather than attributing all differences to language-worker placement.
3. **Whisper round.** Compare fresh CLI with resident unquantized base, then quantized versus unquantized base under the same lifecycle. Use short PTT English/Arabic fixtures with silence, names and numbers. Record language selection and decoding settings. No always-listening capture, automatic translation, cross-session transcript reuse or model-state leakage.
4. **Sampling and timing.** Start with one explicitly recorded cold run and five warm runs per candidate, alternating baseline/candidate where practical. Record initialization, STT, image encoding, text prefill, LLM TTFT, processing-to-first-text, total, peak memory, available memory, system swap changes and temperature. Report median and range; five warm runs do not establish a reliable p95. Do not double-count diagnostic timings or assume server prompt timing excludes image encoding.
5. **Quality and safety gates.** Compare against human-checked Arabic/English answers and transcripts. Require no new critical mistakes, fabricated text or lost refusal/uncertainty behavior in the small gate set; report sample size rather than claiming broad equivalence. Include corrupted images, missing projector, timeout, cancel/retry and stale-session cases. Preserve fail-closed vision, incomplete-stream labeling, transient image cleanup and no automatic SAGE/history writes.
6. **Stop conditions.** Agree memory-reserve and thermal thresholds before testing. Stop on OOM, swap-pressure growth, timeout, unstable services, kiosk regression or quality failure; restore and verify ordinary PTT. Do not raise limits merely to turn a failed candidate into a passing result. Resident STT must fit the total host budget, not just its own service limit.
7. **Escalate only with evidence.** After CPU/Whisper results, trial one smaller matched model/projector pair, never a second concurrent resident LLM. NPU/GPU trials are separate authorized maintenance experiments with pinned dependencies and rollback. Published benchmark or language-coverage claims are not Unit A acceptance evidence.

**Decision remains provisional:** retain the functioning baseline until a candidate improves measured latency without sacrificing Arabic readability, answer correctness, privacy or device stability. Actual benchmark outcomes belong in the parent-owned evidence/release record.

## Measured NPU residency limit on C — 9 October 2026

An NPU trial of the 4B qualification artefact (`lantern-native5-4b-w8a8-opt0-rk3588.rkllm`,
5,535,253,076 bytes, SHA-256 `e5d4221d…`) failed to initialise on C and is now
explained. It is not host RAM and not the cgroup limit: the rknpu 0.9.8 driver
refused the model's 3,565,158,400-byte mapping with `RKNPU: failed to allocate
IOVA: -12` / `rknpu_gem_get_pages: dma map … fail`, which RKNN surfaces as
`failed to allocate handle, ret: -1, errno: 14`. With the resident
`sage-lantern-llm` (2B, 1,880,096,768-byte mapping) and `sage-lantern-vlm`
stopped, the identical command loaded in 8.8 s and answered correctly
(`first_ms` 880–996, `total_ms` ~1.9–2.0 s for six tokens); with the 4B already
resident, the 2B's mapping is refused the same way. **The NPU window admits one
of these lanes at a time, in either load order** — so the 4B is a scheduled
swap-in lane, not a resident one, and `sage-lantern-voice-loop.service` (bound
to the chat unit) has to be paused and restored with it.

This is a measurement, not a decision: D-016's matched 0.8B VL pair remains the
resident photo lane and the CPU 4B lane remains the parallel fallback at
6.4 tok/s. Whether a swap-in 4B lane is worth the temporary loss of chat, vision
and PTT is for the owner and the C acceptance owner. Evidence, pinned artefact
hashes, the four experiments and the reversible swap tool are under
`work/ember-c-4bnpu-20261009/`; no product default, deployment or acceptance
follows from this note.

### 3B production lane on C — 18 October 2026

Unit C's production chat port now runs the V79.6 3B model on the RKLLM token
engine with response guards. The 3B mapping is 3,085,697,024 bytes and fits the
NPU window when the 2B and vision lanes are stopped. Measured with the 48-request
device harness: 625.1 ms median TTFT, 734.1 ms p90, 7.82 tok/s decode,
3,393 MB SDK memory (stock 3B: 669.7 ms, 1,327.0 ms, 8.03 tok/s, 3,250 MB).

The prompt-string engine cannot initialize this 3B model: it fails the same
3,085,697,024-byte IOVA mapping even with the 2B stopped. The token engine loads
it; the server encodes the rendered chat with the model tokenizer and sends
`prompt_utf8_hex` plus `input_ids`. The device artifact is compiled at 16k
context because RKLLM toolkit 1.3.0 caps conversion there; the training host
model remains 32k. Only one 3B lane is resident.

Deployment, guards, rollback and the raw-model caveats are in
[3B_MODEL_PRODUCTION.md](3B_MODEL_PRODUCTION.md).
