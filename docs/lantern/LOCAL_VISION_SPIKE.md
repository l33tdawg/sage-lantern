# Unit A local vision — bounded spike

**Status:** Optional vision activation deployed; real CLI photo-answer path demonstrated with synthetic input. Broader quality and everyday UX acceptance remain pending.  
**Updated / primary sources accessed:** 2026-09-05.  
**Scope:** Existing ROCK 5B+, existing Qwen3.5 4B GGUF, one user-approved still image, local text answer. Parent owns camera preview. No Site changes, purchases, model downloads or live-unit actions performed by this study.

## Implementation evidence — 2026-09-05

The following measurements are **parent-reported live Unit A evidence**, not independently reproduced by this documentation update. The original research below is retained as historical: its statements that no projector was installed and inference was unvalidated describe the earlier state, not these later probes. **Optional activation is now deployed; the follow-up below records successful enable and a real CLI photo-answer run.**

### Activation and end-to-end CLI follow-up — 2026-09-05

The parent reports that installer `--enable` succeeded, with drop-in backup at `/var/lib/sage-lantern/vision-dropins-backup-pz0u95vd`, and backend health reported `photo_ready: true`. After repair, the runtime gate reported **18 passed, 0 failed**. This establishes optional activation on Unit A, not a change to default text-only provisioning.

A real CLI run used a **synthetic Piper-generated 16 kHz WAV**, the real signed SAGE recall path and a **256×192 synthetic red JPEG**:

- Transcript: “What single color fills this photo? Answer with only the color.”
- Response: “The photo is filled with red.”

| Measurement | Reported time |
|---|---:|
| STT | 2,467.5 ms |
| SAGE recall | 15.47 ms |
| LLM first visible token (`llm_first_token`) | 45,401.7 ms |
| Processing start to first visible text (`first_text`) | 47,885.3 ms |
| LLM stage | 46,822.9 ms |
| Stage-sum `total` | 49,305.9 ms |
| Wall elapsed | 49,863 ms |

`llm_first_token` starts at the LLM adapter call; `first_text` includes preceding STT and recall, excluding audio hold. The total sums stage durations only and does not add either diagnostic metric again. LLM stage timing includes synchronous delta-callback backpressure. Reported rounded components may differ slightly from the rounded total.

The synthetic fixture was deleted in final cleanup and voice service was restored. The parent reports **no raw camera capture or history write** for this run. This is end-to-end synthetic CLI evidence through STT, real authenticated recall and image inference, not a completed physical camera/PTT touchscreen acceptance test. The color was correct, but the response did not follow the requested color-only format. Photo answering is functional at **approximately 50 seconds in this run**, with first visible text after approximately 48 seconds: it is **not demonstrated fast**, nor does it establish object recognition, OCR or general visual grounding quality.

### Verified artifacts and bounded inference

| Installed artifact | Parent-reported SHA-256 |
|---|---|
| Qwen3.5-4B Q4_K_M model | `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4` |
| Matching `mmproj-BF16.gguf` | `302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6` |
| `llama-server` executable | `c3ce4aa9ac29f08ebf639b7431e7bd6e7a73f64e5cf7fb3a6bab87293bf81a28` |

The model/projector hashes match the pinned source pair in the historical research. The parent ran **one CPU-only LLM server with the projector**, not concurrent text and vision servers: parallelism 1, image-token cap 256, projector offload disabled. Reported guardrails were `MemoryMax=5G`, `MemorySwapMax=0` and a 300-second runtime limit for the probe.

| Synthetic JPEG input | Visible answer | Elapsed | Reported prompt timing |
|---|---|---:|---:|
| 256×256 red | `Red` | 41.86 s | 41,622.8 ms |
| 256×256 blue | `Blue` | 41.92 s | 41,589.4 ms |
| 128×128 green | `green` | 12.49 s | 12,164.2 ms |

Generation was two tokens, approximately 0.2 seconds. These runs establish that this installed pair can process bounded synthetic images and return the corresponding colors. Prompt processing dominates these observations; streaming cannot remove the wait before visible generation begins. These are individual probes, not a latency distribution, isolated image-encoder timings or a guarantee that smaller real-world images yield the same speedup.

Reported observation: **MemAvailable 3,386 MB, system swap usage 135 MB, temperature 54.5°C, no OOM observed**. The model cgroup's zero-swap limit does not mean the whole host had zero swap usage. This snapshot is not peak-memory evidence or a guaranteed reserve under simultaneous browser/STT/SAGE load. **Original text-service recovery passed.** GPU/NPU acceleration is neither used nor established by these results.

### Implemented request boundary and remaining quality limits

- Frontend selection uses a **256×192 bounding box**. Runtime independently accepts one JPEG no larger than **512 KiB**, each dimension ≤640, total pixels ≤307,200; frontend bounds are tighter than the runtime ceiling.
- Runtime API: `voice --image PATH [--events]`, or `run_audio(..., image_path=path, on_delta=callback)`. The request contains one inline `image_url` data URI, not a remotely fetched image URL. Existing transcript-bearing delta events and final result events remain unchanged.
- Photo responses have a **128-output-token request cap**; ordinary text requests receive no new global prediction cap. Truncation, empty/failed inference and malformed streams do not become successful final results or trigger text-only retries. Partial deltas may precede an error and must remain visibly incomplete.
- Stdlib validation bounds JPEG bytes and headers; it is **not full pixel decoding**. Actual image decoding remains the llama decoder's responsibility and decoder failures must fail closed. Photos are untrusted evidence, never instructions. SAGE receives transcript-only recall, not image writes; the caller owns photo cleanup and session association.
- Reported local checks: **runtime 96 tests + 3 subtests; voice backend 74 tests after cleanup hardening; optional installer 14 tests; smoke checks 11 tests passed**. These are separate reported suites, not proof of physical camera-to-answer acceptance. The deployed controller deletes attachments after runtime exit and before history writes; stderr is discarded rather than accumulated without a byte bound.

Solid-color success does **not** establish OCR, document reading, object identification, spatial reasoning, natural-scene grounding, hallucination resistance or safe advice. The small frontend derivative may discard important detail. Representative user-approved camera scenes, wrong/unclear-image behavior, text-only voice regression, cancellation, stale-session rejection and deletion still require integrated validation. No general quality or acceptable everyday latency claim follows from these three probes.

### Optional installer and rollback

Default provisioning remains **text-only**. The parent may run the separately added installer in an idle maintenance window:

```sh
sudo bash scripts/lantern/install-vision-local.sh --check-only
sudo bash scripts/lantern/install-vision-local.sh --enable
sudo bash scripts/lantern/install-vision-local.sh --disable
```

`--check-only` verifies the pinned model/projector, hashes the installed binary and checks its advertised flags. It prints artifact hashes but performs **no inference, service changes or readiness certification**. Expected projector path: `/opt/sage-lantern/models/mmproj-BF16.gguf`.

`--enable` requires existing `LOCAL_VOICE_READY` and `/var/lib/sage-lantern/LOCAL_VISION_VALIDATED`: a regular root-owned JSON file ≤8,192 bytes, not writable by group/others (mode `0644` is suitable). This is an explicit local operator attestation of already validated inference, **not signed evidence**. Minimum schema for the reported artifacts:

```json
{
  "validated": true,
  "model_sha256": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
  "projector_sha256": "302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6",
  "llama_server_sha256": "c3ce4aa9ac29f08ebf639b7431e7bd6e7a73f64e5cf7fb3a6bab87293bf81a28"
}
```

Do not create the attestation merely because `--check-only` passed. Enable checks these hashes against installed artifacts and checks wrapper support for `--photo-ready`. It backs up service drop-in directories under `/var/lib/sage-lantern/vision-dropins-backup-*`, atomically writes its named `90-lantern-local-vision.conf` files, and refuses conflicting local `ExecStart` overrides for operator review. It stops voice, restarts the single model service, waits for model health, then starts voice with `--photo-ready`. Settings include threads 6, parallel 1, CPU-only inference, no projector offload, image-token cap 256, `MemoryMax=5G` and `MemorySwapMax=0`; **no global prediction cap**. Health success is not a new inference test. The probe's 300-second runtime guardrail is not installed as a service lifetime limit.

`--disable` removes only the installer's two named drop-ins, preserves other overrides and validation markers, reloads systemd and restarts the underlying model/voice configuration. It does not require the projector or validation marker to remain available. Configuration/restart failure triggers an attempt to restore the previous owned drop-ins and services; recovery failures are reported with the backup location, not claimed as successful rollback.

## Historical research and pre-implementation plan — 2026-09-05

The remainder is the original planning record. Unchecked gates and the closing decision below are historical; the dated evidence above supersedes only the specific checks it demonstrates.

## Smallest safe next step

**Identify the running llama.cpp build and installed model before changing anything.** Then, only after the build/provenance and capacity gates below, consider staging its matching vision projector for a single-image test. Do not replace the working text service or download another language model merely because it currently accepts text only.

The operator reports one Logitech C270 1280×720 MJPEG frame captured and successfully fully decoded locally with system Python GI/GdkPixbuf to 1280×720×3 pixels; it was not uploaded. The [BOM](HARDWARE_BOM.md) records a 37,590-byte JPEG. Decode is now evidenced by the operator report; useful focus, device preview and vision inference remain separate checks. Parent owns preview and explicit Use photo/Retake. No continuous-video processing is needed.

**Latest operator measurements:** Unit A has **7,935 MB total RAM (approximately 8 GB)** and **3,968 MB available at idle**. The model directory contains the existing Qwen GGUF, `ggml-base.bin` and Piper files; **no projector is installed**. This sidecar did not independently probe the unit. Available memory is a point-in-time baseline, not a guaranteed allocation budget or proof that multimodal inference fits.

## Installed-build checks — proposed for the parent, not executed

1. Resolve the actual running executable and service command; record executable SHA-256, its `--version` and `--help`, startup log/build commit, compiler/backend information and current model path. A shell binary elsewhere on PATH is not evidence about the running service.
2. [SOFTWARE_STACK.md](SOFTWARE_STACK.md) records **b9637** and Unsloth revision `e87f176`; treat these as intended pins, not confirmation of the installed build. Check the service against `software/stack.lock.json`, including whether any local model repair/conversion changed its hash or metadata.
3. Confirm the installed revision supports the Qwen3.5 text architecture **and its vision encoder/projector** through `libmtmd`; check local `--mmproj`, `--no-mmproj-offload`, image-input API and any image-token limiting options. A flag in current upstream documentation or successful text loading alone is insufficient. Inspect the corresponding pinned source/build configuration; `llama-mtmd-cli` presence is useful but not required for server support.
4. Record current context, parallel slots, GPU layers, chat template/thinking settings, RAM/MemAvailable, swap, process memory, disk headroom and thermal state. Preserve the working voice service configuration and rollback binary/model. No blind upstream upgrade; any required build gets a separate pinned ARM64 build and regression review.

Upstream documents local `-m` plus `--mmproj`, image processing through `libmtmd`, and `llama-server` multimodal chat requests. It also notes projector GPU offload defaults on; a CPU baseline must explicitly disable it when supported. [llama.cpp multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)

## Matching source pair to investigate

Qwen identifies **Qwen3.5-4B** as a language model with a vision encoder; the official repository is Transformers-format, not the existing GGUF. [Qwen model card](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/README.md)

Use the **same Unsloth conversion repository and immutable revision** already recorded locally:
`unsloth/Qwen3.5-4B-GGUF@e87f176479d0855a907a41277aca2f8ee7a09523`.

| Artifact | Publisher file metadata, not local verification |
|---|---|
| [Qwen3.5-4B-Q4_K_M.gguf](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/blob/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf) | Approximately **2.74 GB**; SHA-256 `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4` |
| [mmproj-BF16.gguf](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/blob/e87f176479d0855a907a41277aca2f8ee7a09523/mmproj-BF16.gguf) | Approximately **676 MB**; SHA-256 `302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6` |

This is a **same-source pairing candidate**, not verified compatibility with Unit A. Reuse the installed language file only if its identity matches, or its documented modifications are reviewed. The projector filename is generic: do not substitute one from Qwen3-VL, another parameter size, another converter or floating `main`. BF16 storage does not establish accelerated BF16 execution on the ROCK. If unsupported, stop and review a matching alternative conversion/build rather than silently mixing files.

**License gate:** official Qwen card and the Unsloth artifact repository declare **Apache-2.0**; llama.cpp is **MIT**. Retain exact-revision model/projector provenance, license text, applicable notices and conversion history in the eventual artifact inventory. Check dependencies separately; a model-card label is not complete redistribution review. [Qwen license](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/LICENSE), [llama.cpp license](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE)

## Capacity and request envelope

- The pair is roughly **3.42 GB on disk**, not a RAM requirement. Account for loaded weights, projector buffers, decoded image, image embeddings, KV/recurrent state, compute buffers, OS/browser, STT and personal SAGE. No minimum RAM, latency, battery runtime or accelerator claim is established.
- CPU-first baseline: verify supported flags equivalent to `--n-gpu-layers 0` and `--no-mmproj-offload`; assume neither Mali GPU nor RK NPU integration. **Do not start a concurrent vision server on this measured 8 GB unit.** First measure the working service's resident/peak load and reserve capacity for OS/browser, STT and personal SAGE. The 3,968 MB idle availability is not spare capacity for another full model plus vision buffers.
- A later test requires an explicitly approved maintenance window: pause new voice requests, stop the existing model process, verify it has exited and released its allocations, then run only one bounded multimodal process using the existing language weights plus the verified projector. Keep original configuration/artifacts for restoration and run a no-photo voice regression afterward. Do not stop the live service as part of this research; if a safe interruption is unavailable, defer inference rather than run both servers or rely on swap.
- One image, one request, short output. Proposed initial limits: aspect-preserving derivative no larger than 640 pixels on its long edge, one slot, at most 128 output tokens. These are experiment bounds, not accuracy/performance promises. Freeze timeout/memory/thermal stop thresholds before running. Existing 2048-context smoke configuration may not fit image tokens plus prompt/output; measure token use before adjusting it.
- Use local `/v1/chat/completions` typed content: a text question and `image_url` containing an inline `data:image/jpeg;base64,...` image. Never place base64 into ordinary text and call that vision. Verify the installed revision's API and image preprocessing/token controls against its own help/docs. [llama-server request and image options](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- Bind any later spike endpoint to loopback, disable remote image fetching in the application, and avoid broad local-file access. Bound both encoded bytes and decoded dimensions; fully decode before inference. Associate one explicitly selected frame with one voice session; no stale-photo reuse or background uploads.

## Validation gates before enabling vision in the device UI

- [ ] **Preview:** full local decode reported complete; still must be displayed on-device with Use photo/Retake/Cancel, capture timestamp/session and deletion policy. Decode/preview success is not model readiness.
- [ ] **Build/artifacts:** exact binary and compatible pair verified; licenses recorded; no unknown/mismatched architecture or silently ignored projector. Any subsequent download/staging is separately authorized.
- [ ] **Isolated image proof:** one approved synthetic object/shape scene and short question; repeat with a visibly changed scene and unchanged question. Correlated logs establish image encoding, not merely a plausible text-only guess. No tools, personal-memory writes or public publication in the first test.
- [ ] **Failure handling:** missing projector, corrupt/oversized image, absent camera, timeout and stale session produce explicit errors; failed vision never becomes an unlabelled text-only answer. Test cancellation/timeout recovery and image cleanup.
- [ ] **Measured envelope:** approve a memory/load budget against the reported 7,935 MB total and fresh available-memory measurements before model loading. Set a conservative memory-reserve floor and stop thresholds before the run; record load/image-encode/prefill/generation timing, peak process memory, swap pressure and thermals. Abort before exhausting the reserve; OOM, unsafe temperature or unplanned service disruption fail the test. No blanket quality or OCR claim from one successful image.
- [ ] **Voice regression/privacy:** ordinary no-photo PTT still works; restore original service and verify recovery. Photos/transcripts stay local, ephemeral by default and excluded from fleet logs; image text is untrusted data, not authority or instructions to execute.

**Decision:** proceed now with parent-owned preview and read-only installed-build inventory. Projector staging, server changes and bounded inference follow only after those checks. Vision remains **unvalidated** until evidence exists.
