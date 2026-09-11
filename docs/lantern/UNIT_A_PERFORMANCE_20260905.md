# Unit A performance measurements — 2026-09-05

These are bounded synthetic experiments, not representative multilingual,
camera-quality or physical touchscreen acceptance. Model, projector, image
budget and the original six-thread CPU configuration are retained.

## Measured results

### Live photo follow-up after power-button shutdown/restart

The latest logged model task after the 20:48 restart reports 34,988.70 ms
prompt evaluation (207 tokens), 13,088.79 ms generation (65 tokens), and
48,077.49 ms total. This is model-side timing, not microphone-to-screen latency;
it does not isolate image encoding from text prefill. Voice state had already
returned to idle and no longer retained its stage timing snapshot.

Configuration remains six CPU threads, one slot, 256 maximum image tokens,
Qwen3.5 4B Q4_K_M with BF16 projector and no GPU offload. Temperatures around
49°C and ondemand governors at inspection do not establish throttling or a
governor-related cause. No model/server tuning was deployed during this check.
Next controlled test: vary batch/prefill threads independently, with the same
image/prompt and quality checks. Do not repeat rejected A76 affinity changes or
reduce image detail blindly; preserve Arabic/OCR and injection-resistance tests.

### Earlier controlled experiments

| Experiment | Baseline | Candidate | Decision |
|---|---|---|---|
| Synthetic red 256×144 JPEG, first token, two runs each | Original prompt: 40.436 / 40.597 s | Final hardened compact prompt: 37.187 / 37.217 s | About 3.3 s (8%) lower in this case; not a full-pipeline claim |
| Same final prompt, six threads | All CPUs: 37.187 / 37.217 s | CPU 4–7 only: 41.979 / 41.843 s | Reject affinity change |
| Same final prompt, four threads on CPU 4–7 | Six threads/all CPUs above | 39.107 / 39.275 s | Reject: faster generation, worse photo first token |
| Fresh-process Whisper, synthetic English questions, ABBA | General: 2.297 / 2.278 s; photo: 2.277 / 2.227 s; names: 2.581 / 2.527 s | Beam 1, best-of 2, no timestamps: general 2.128 / 2.130 s; photo 2.076 / 2.077 s; names 2.228 / 2.226 s | About 0.16–0.33 s lower; retained base model and six threads |

Both Whisper variants transcribed the synthetic name Farid as Farad. General
and photo-question words matched; names punctuation differed. No Arabic,
Malay, noisy-room or accent acceptance is implied. No silence/noise threshold
or language change was adopted. Installed English-default ASR is not proven
multilingual simply because its model supports multiple languages.

An earlier compact prompt was faster (35.850 / 35.836 s) but followed an
injected instruction in retrieved context. The original prompt also failed
that same probe. Additional explicit separation of untrusted context from
the user question made the final candidate pass this one probe. This is not
a claim of general prompt-injection resistance. The discarded candidate is
retained only as a benchmark comparison, not production guidance.

## Arabic photo observation

The inspected user run took 55.892 s across measured pipeline stages: STT
2.341 s, recall 0.012 s, LLM first token 39.980 s, first runtime text 42.335 s,
LLM total 53.538 s. The answer declined to invent unreadable Arabic wording.
No original photo was retained for independent optical-focus diagnosis.
The 1280×720 camera frame becomes 256×144 for inference; small glyph detail
loss is plausible. A sharper display preview does not improve that input.

## Reproduction and remaining work

Reviewed helpers live in `scripts/lantern/benchmarks/`: `prompt.py`,
`affinity.py`, `four_threads.py`, and `whisper.py`. They are Unit A maintenance
experiments using synthetic fixtures, not provisioning services. Check idle
voice state, schedule maintenance and verify restoration after every run.
The four-thread override and recovery timer were removed/stopped; original
model and voice services were verified active afterward.

Next: representative quality corpus, controlled cold/warm distributions,
actual first visible text measurement, bounded selected-crop OCR/detail mode,
and separate resident/quantized STT or smaller vision-model experiments.
No NPU, GPU, driver, model download or clock change was made.
See [hardware/model options](RK3588_PERFORMANCE_OPTIONS.md) and
[settings/recovery contract](SETTINGS_AND_RECOVERY_CONTRACT.md).

## Installed-runtime regression

After installing the final adapter, three synthetic Matrix voice requests
completed with first runtime text at 12.667 / 5.001 / 4.878 s and CLI wall
24.317 / 14.434 / 15.767 s. These are sequential runs, not controlled cold
versus warm distributions. All traversed STT and SAGE recall without errors;
one answer incorrectly said the protagonist teams up with “agents,” so
successful execution must not be mistaken for factual-quality acceptance.

A separate 256×192 synthetic red-photo full pipeline returned the correct
color: STT 2.211 s, recall 0.009 s, LLM first token 42.072 s, first runtime
text 44.292 s, stage total 46.079 s and CLI wall 46.664 s. This larger fixture
is not the 256×144 prompt microbenchmark. Photo latency remains substantial.
The voice service was restored and synthetic files removed after the checks.
