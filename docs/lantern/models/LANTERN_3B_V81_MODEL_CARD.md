---
license: other
license_name: qwen-research
license_link: https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct/blob/main/LICENSE
base_model: Qwen/Qwen2.5-VL-3B-Instruct
library_name: mlx
tags:
  - lantern
  - sage
  - english
  - lora
  - research-preview
  - non-commercial
---

# Lantern 3B V81 — Model Card

**Version:** 1.0 research preview  
**Release date:** 18 October 2026  
**Model type:** Ordered LoRA adapter chain for a text-only English assistant  
**Base model:** [Qwen2.5-VL-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct)  
**License:** Qwen Research License — **non-commercial research/evaluation only**  
**Contact:** [Open an issue](https://github.com/l33tdawg/lantern-3b/issues)

## 1. Model details

Lantern V81 is an English-focused 3B research preview for a local, offline
assistant with guarded memory and tool actions. It is distributed as an
ordered chain of ten MLX LoRA adapters on Qwen2.5-VL-3B-Instruct, not as a
standalone model. The base weights are not included. The release also
publishes the exact RK3588 W8A8 `.rkllm` device artifact as three split parts;
the merged HF weights are reproducible from the adapter chain. See
`MODEL_ARTIFACTS.md`.

| Item | Value |
|---|---|
| Base architecture | Qwen2.5-VL-3B-Instruct, 3,085.9M parameters, 36 layers |
| Adapter chain | V72 → V73 → V74 → V75 → V76 → V77 → V78 → V79 clarify → V79.2 notice → V79.6 schema → V81 |
| V81 adapter | Rank 32, scale 32, 59.87M trainable parameters (1.94%) |
| Parent adapters | Rank 8, scale 8 |
| Training precision / host | MLX on Apple M5 Max, 128 GB |
| Adapter format | MLX `adapters.safetensors` |
| V81 adapter SHA-256 | `eddb21695aec95bb72fca99c67c5430619988cb0e2761fd509f523263e1fe077` |
| Curriculum SHA-256 | `be3af0b4d771002e9350710aa3fa3b0d85df95acea909e05cb6da8669fd79b44` |
| Release asset SHA-256 | Recorded in the GitHub release metadata and the repository evidence. |

The model is a derivative of Qwen2.5-VL-3B-Instruct and carries the Qwen
Research License restrictions. It is not affiliated with or endorsed by
Alibaba Cloud or the Qwen team.

## 2. Intended use

### Primary use

- Non-commercial research and evaluation of a focused English assistant.
- Local/offline conversation and tool-workflow experiments where the runtime
  owns authorization, values and execution.
- Reproducing the synthetic evaluation panels in `evaluation/`.
- Studying correction continuity, label semantics, factual grounding and
  guarded memory actions on a small device-class model.

### Downstream use

- A guard-integrated prototype where the model proposes text and the runtime
  canonicalizes save/recall actions, interval answers and failed-lookup status.
- Research on adapter-chain composition and rank/capacity tradeoffs.

### Out-of-scope use

- Commercial use without a separate license from Alibaba Cloud.
- Safety-critical, medical, legal, financial or emergency decisions.
- Autonomous execution of model-authored tool calls without the guard.
- General-purpose chat, open-domain factual assistance, long-form factual
  writing or arithmetic without independent verification.
- Claims of device performance, production readiness or general intelligence.
- Surveillance, profiling, or processing private data without explicit
  authorization.

## 3. Factors

- **Language:** English. The curriculum and evaluation are English-only.
- **Prompt style:** Short, explicit instructions. The model is trained for
  direct answers and structured memory/tool requests, not free-running prose.
- **Guard/runtime:** The intended deployment uses `guard_action` for saves and
  recalls, `status_text` for failed/empty lookup prose, `interval_answer`
  for the published interval cases and `label_answer` for undefined
  colour/label meanings. The raw model is not action-safe.
- **Context:** Host training uses a 2,048-token training sequence. The base
  model and the device deployment have separate limits. The Unit C build is
  compiled for 16,384 tokens and serves up to 8,192 new tokens. That is a
  runtime envelope, not a long-context or long-form qualification.
- **Device:** The preview ships a host MLX adapter chain and a verified RK3588
  `.rkllm` build. The device figures below come from one engineering unit; no
  thermal, battery, field or multi-user claim is made.
- **Data:** Synthetic English task material. No private facts, real user
  conversations, credentials or contact details were used.

## 4. Metrics

The evaluation is deterministic and synthetic. It is development evidence, not
a general benchmark.

- **Sealed pass:** a 64-case set authored after the curriculum freeze, with
  required facts, forbidden meanings/cadences and expected behavior per case.
- **Fresh64 tool/status:** 24 frozen cases covering save/recall tool calls and
  receipt/absence status prose.
- **Stock28:** 28 wrapped save-request prompts scored semantically for tool
  name, content, type and destination.
- **E12/E13/S03:** targeted notice, interval/rate and errored-lookup cases.
- **Raw vs guarded:** raw means the model output alone; guarded means the
  included canonicalization/status/interval functions.

## 5. Evaluation data

| Panel | Cases | Source | Qualification status |
|---|---:|---|---|
| V81 sealed | 64 | Authored after the curriculum freeze; 16 unknown-label, 12 defined-label, 8 missing-cadence, 4 defined-cadence, 8 grounding, 8 value-correction, 4 label-correction, 2 cadence-correction, 2 history | Synthetic development evidence |
| Fresh64 | 64 prompts, 24 tool/status scored | Frozen V58 development set | Targeted development diagnostic |
| Screen17 | 17 | Frozen V58 screen set | Targeted development diagnostic |
| Stock28 | 28 | Wrapped save-request set | Development action check |
| E12/E13/S03 | 5 | Published guard cases | Targeted guard checks |

The V81 sealed set has zero 12-word shingle overlap with the new training
rows. It was authored after the curriculum was frozen and was not used for
training. The frozen sets are exposed development diagnostics, not fresh
qualification.

No private or real user data is included in the evaluation. All names, places,
labels and values are invented.

## 6. Training data

The balanced curriculum contains 2,028 unique rows (4,056 written rows for two
passes, 2,028 optimizer updates). The maximum approximate sequence length is
1,189 tokens.

| Source | Unique rows |
|---|---:|
| New label semantics / cadence / grounding | 614 |
| New correction continuity | 380 |
| V79.6 action schema | 285 |
| V79.8 interval/arithmetic | 170 |
| V79.2 notice/E12 | 22 |
| V79 clarify/public selection | 54 |
| V46 verified teacher English (short/medium) | 503 |

The new rows are deterministic synthetic constructions with fail-closed
verifiers. The V46 rows are a previously admitted verified teacher corpus of
fictional English tasks. No private facts, real conversations, URLs,
credentials or repository secrets are present. The curriculum manifest and
per-source hashes are in `evaluation/V81_CURRICULUM_MANIFEST.json`.

## 7. Quantitative analyses

### Raw and guarded results

| Panel | Stock | V79.6 | V80 | V81 raw | V81 guarded |
|---|---:|---:|---:|---:|---:|
| V81 sealed 64 | 34 | 36 | 63 | **64** | **64** |
| Fresh64 tool/status | 6/24 | 21/24 | 17/24 | 21/24 | **24/24** |
| Stock28 wrapped actions | 0/28 | 28/28 | 28/28 | 28/28 | **28/28** |
| E12_A/B | 1/2 | 1/2 | 2/2 | 2/2 | **2/2** |
| E13_A/B | 0/2 | 0/2 | 0/2 | 0/2 | **2/2** |
| S03_B | 0/1 | 0/1 | 0/1 | 0/1 | **1/1** |

### V81 sealed families (guarded)

| Family | Result |
|---|---:|
| Unknown label meaning | 16/16 |
| Defined label meaning | 12/12 |
| Missing cadence | 8/8 |
| Defined cadence | 4/4 |
| Missing grounding | 8/8 |
| Value correction | 8/8 |
| Label correction | 4/4 |
| Cadence correction | 2/2 |
| Correction history | 2/2 |

### Failure analysis

- `V58_S13_B`: the raw model used a quoted public destination instead of the
  authorized private home. `guard_save` canonicalizes it to the private-home
  save with no domain.
- `V58_S16_B`: the raw model ignored a supplied recall query and asked for one.
  `canonical_recall` uses the trusted query and options.
- `V58_S03_B`: the raw model made an errored-lookup absence claim.
  `status_text` repairs it to a failed-lookup non-absence answer.
- `V58_E03_A`: the raw model derails into a long inconsistent tape/strip
  calculation. No arithmetic guard covers it; arithmetic remains unqualified.

### Host timing (not device performance)

In the host MLX capture, the V81 sealed prompts had a median first-visible
token time of about 125 ms and median generation throughput of about 48.5
tokens/s; the frozen fresh64 prompts were about 254 ms and 40.0 tokens/s.
Peak host memory was about 10.9 GB. These are desktop-host measurements, not
RK3588, thermal-soak, field or multi-user results.

### Device deployment (Unit C)

The release artifact is the build serving on Unit C, an RK3588 engineering
device. It is the same merged V81 model converted to RKLLM, not a second
training run.

| Item | Value |
|---|---|
| Artifact | `qwen2_5-vl-3b_fused_w8a8_opt1_ctx16384_rk3588.rkllm` |
| SHA-256 | `ac7021b61909b9682ea25f21400c195d477c6ad3d2a595adc7d632808dfa6285` |
| Size | 3,823,749,236 bytes |
| Quantization | W8A8 opt1 |
| Context / max output | 16,384 / 8,192 tokens |
| Service | `sage-lantern-llm.service`, loopback port 8081 |
| Response guard | `ac69b131412ada129483c9003e301f5fb930ed37536919a3ec4ca90fd199ae07` |
| Server | `40f14b71e4f2e729c811290b297fbcdb06db7ebc6fe021caf6532581ee028ec8` |
| Health | `/health` returns `{"status":"ok"}` |
| Guard check | E13_A, E13_B and S03_B pass |

Live probes on the deployed server:

- An undefined label question returns: "The meaning of the glaucous label
  isn't specified here. What does glaucous mean in this context?"
- A missing cadence question returns: "The check frequency isn't specified
  here. What is the check interval?"
- A supplied definition ("teal means pending") is honored: "Pending in our
  intake, as you specified."

The full 48-request streaming HTTP suite completed on 18 October 2026 at the
live 16k/8192 profile. All 48 requests returned; none failed.

| Panel | Requests | TTFT median | TTFT p90 | Response median | Decode median |
|---|---:|---:|---:|---:|---:|
| Quality prompts | 36 | 646.9 ms | 777.6 ms | 5,227.0 ms | 7.96 tok/s |
| All requests | 48 | 726.7 ms | 7,528.1 ms | 6,702.6 ms | 7.93 tok/s |

The p90 rises because the fixed latency set includes 1,536-token prompts;
those measured 11,932.6 ms TTFT and 21,687.1 ms response median. These are
latency measurements from one unit, not a correctness or capability score.

## 8. Ethical considerations

- **Privacy:** The curriculum and evaluation are synthetic. No private facts
  or real conversations are stored in the adapters. The intended runtime is
  local/offline; no telemetry is part of this preview.
- **Action safety:** The guard is a canonicalization and authorization
  boundary for specific save/recall actions, not a general safety filter. It
  must not be treated as permission to execute arbitrary model output.
- **Hallucination:** The model can still invent facts, meanings or schedules in
  unguarded conversation. The curriculum reduces specific observed failure
  families; it does not eliminate hallucination.
- **Licensing:** The base is non-commercial research-licensed. Redistribution
  must carry the license and notices. Commercial use needs Alibaba Cloud
  permission.
- **Compute:** Training used a local Apple M5 Max. No cloud training or
  third-party API data is part of this release.

## 9. Caveats and recommendations

- Use the guard for every save/recall action and for interval/status prose.
- Use `label_answer` for undefined colour/label questions. It covers a bounded
  vocabulary and does not make the model factually reliable elsewhere.
- Do not publish or deploy bare weights as an autonomous action agent.
- Disclose `E03_A` arithmetic as unqualified; verify arithmetic independently.
- Long-form factual writing is not qualified. The model can still invent
  meanings, cadences and world knowledge outside the guarded cases.
- Treat the 64-case sealed result as synthetic development evidence, not a
  general capability score.
- The 48-request suite covers latency and guard behavior on one Unit C build.
  Run thermal, battery, memory-soak and multi-user qualification before
  claiming production performance.
- Keep the Qwen Research License and NOTICE with every redistribution.
- Report issues or evaluation gaps through the repository issue tracker.

## 10. Reproduction

1. Obtain Qwen2.5-VL-3B-Instruct separately under its license.
2. Apply `adapters/00-v72` through `adapters/10-v81` in the order in
   `adapters/ORDER.json`.
3. To run the device build, download `v81-rkllm.part-00` through
   `v81-rkllm.part-02`, reassemble them as described in `REASSEMBLE.md`, and
   verify the SHA-256 above.
4. Load the guard from `guard/` and use `guard_action` for actions,
   `status_text` for failed/empty lookups, `interval_answer` for interval
   cases and `label_answer` for undefined label meanings.
5. Reproduce the evaluation with `evaluation/prompts.jsonl`,
   `evaluation/references.jsonl`, `evaluation/score_v81.json` and
   `evaluation/score_v81_guarded.json`.
6. Verify every adapter against `adapters/ORDER.json`; the merged and device
   hashes are in `MODEL_ARTIFACTS.md` and `MERGE_RECEIPT.public.json`.

Repository: `https://github.com/l33tdawg/lantern-3b`  
Release: `https://github.com/l33tdawg/lantern-3b/releases/tag/v81-research-preview`  
Model snapshot: V81 adapter `eddb21695aec95bb72fca99c67c5430619988cb0e2761fd509f523263e1fe077`

## 11. License

Qwen2.5-VL-3B-Instruct is distributed under the Qwen Research License
Agreement. The license permits non-commercial use, reproduction, distribution
and derivative works only. This adapter chain is a derivative and carries the
same restriction. Commercial use requires a license from Alibaba Cloud. See
`LICENSE-QWEN-RESEARCH` and `NOTICE.md`.
