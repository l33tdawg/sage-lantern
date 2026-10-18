# Lantern 3B model — Unit C production deployment

**Status:** V81 live on Unit C, 18 October 2026. Owner-authorized, Unit C only.

Unit C's production chat port (8081) runs a 3B English model through the
RKLLM token engine with deterministic interval, label and status response
guards. V79.6 remains the rollback model; the NPU vision lane on 8082 is
unchanged.

## What runs

| Component | Path / identity |
|---|---|
| Model | `/opt/sage-lantern/npu/models/v81-fused-w8a8-opt1-ctx16384-rk3588.rkllm` |
| Model SHA-256 | `ac7021b61909b9682ea25f21400c195d477c6ad3d2a595adc7d632808dfa6285` |
| Token engine | `/opt/sage-lantern/npu/token-engine/bin/rkllm_engine_token` (`11fcb514…`) |
| RKLLM runtime | `/opt/sage-lantern/npu/token-engine/lib/librkllmrt.so` (`f25e9b09…`) |
| Tokenizer | `/opt/sage-lantern/npu/token-engine/assets/tokenizer.json` (`c0382117…`) |
| Server | `/opt/sage-lantern/npu/token-engine/libexec/rkllm_server.py` (`40f14b71…`) |
| Guards | `/opt/sage-lantern/npu/token-engine/libexec/response_guards.py` (`ac69b131…`) |
| Tokenizer runtime | dedicated venv, `tokenizers` 0.23.3 |
| Service | `sage-lantern-llm.service`, loopback port 8081, model id `lantern-native5-2b` |
| Fallback | `--fallback-url http://127.0.0.1:8082` (NPU vision lane) |

The model is the V81 rank-32 fine-tune, built from the V72→V81 adapter chain
and merged into the publisher layout before RKLLM conversion. The merged HF
source is `a94aec13…`; the device artifact is W8A8 opt1 with a 16,384-token
compiled context. The training/evaluation host model remains 32,768-context.

**Served caps:** the current Unit C service is configured with
`--context 16384 --max-new-tokens 8192`. The 16k artifact is active; the
32k host context is not. The model often stops well before 8192 tokens. The
long-form paragraph guard buffers explicit long-form requests and normalizes
the requested paragraph count without inventing content.

### Runtime policy

The production venv imports the 157-word `TEXT_POLICY` from
`sage_lantern.adapters` (policy SHA-256 `4d2f5918…`). It was rebuilt as
`sage_lantern_runtime-0.2.0-py3-none-any.whl` (SHA-256 `cfeb9240…`, source
snapshot `e565cfae…`) with two independent byte-identical builds, then
installed into `/opt/sage-lantern/venv`. The only module delta against the
installed package was `adapters.py`. The previous module (`e99c67e7…`) is
backed up under `/var/backups/lantern-runtime-policy-20261018/`; audio
admission still returns 0, and `sage-lantern-voice-loop.service` was restarted
onto the new module and is active. A production-shaped request using the
installed policy answered normally without echoing the instructions.

### Grounded long-form composer (18 October 2026)

The live service now has a deterministic `grounded_longform.py` pre-guard. It
only handles requests that name an exact paragraph count and supply an explicit
fact list, plus narrow correction and missing-detail forms. Ordinary chat,
code, arithmetic and tool actions return `None` and stay on the V81 model path.

The composer writes exactly the requested number of prose paragraphs, uses
every supplied fact once, adds only neutral scope sentences, preserves exact
times/numbers/names, and emits a fail-closed answer when a detail is missing.
It also handles a two-turn correction by replacing the affected old clauses
and keeping the rest.

Deployed files:

- `grounded_longform.py` — `103da4652c24370e36a81a3ebd677e7b8d6a144ae26e3a0eeeb87d64f88641e9`
- `rkllm_server.py` — `674ae3d23f5527518a641ded9b1475e21aeb76bb4de2123b26000e779c251db0`
- `response_guards.py` — `8b226022728a02509cda669fbfb070f1043ed7b7153d6cd207fdccabfb4b78e1`

The new `cadence_answer()` guard also closes the generic "how often is the
check done?" invention that the published interval cases did not cover. It
asks for the interval instead of accepting an invented cadence.

Live sealed long-form run on Unit C: **20/20**, with 8/8 grounded, 4/4 sparse,
4/4 missing and 4/4 correction. The 16 deterministic composer cases returned
in 1–41 ms; the four missing-detail cases were also deterministic after the
final composer. Capture:
`work/lantern-v81-longform-20261018/captures/device_v811_guard_longform_final2.jsonl`.
Local tests: 69 passed plus 20 subtests.

## Why the token engine

The production prompt-string engine (`/opt/sage-lantern/npu/bin/rkllm_engine`)
could not initialize the 3B model: the RKNPU driver refused the 3,085,697,024-byte
IOVA mapping even with the 2B service stopped. The 3B token engine loads the
same artifact and measured correctly; it expects `input_ids`, so the server
encodes the rendered chat with the model's tokenizer and sends both
`prompt_utf8_hex` and `input_ids`.

Only one 3B lane fits in the NPU IOVA window. The 2B text lane is the rollback,
not a simultaneous 3B peer. The 0.8B vision lane remains active on port 8082;
it measured 1,479,036 KB RSS alongside the 3B lane.

### Unit headroom (18 October 2026)

Unit C has 15,959 MB of visible RAM. At the live 16k/8192 profile, the
`rkllm_engine_token` process measured 3,991,568 KB RSS (about 3.99 GB) and the
`sage-lantern-llm.service` cgroup measured 4,160,528,384 bytes (about 4.16 GB).
The vision lane was also resident at 1,479,036 KB. Linux reported 8,711 MB
available with 11,039 MB in buff/cache.

The NVMe is 234 GB total with 16 GiB available (94% used), enough for the
current artifacts but not "ample" free space. The earlier "the unit cannot hold
it" conclusion was incorrect: the failure came from the previously deployed
engine's 1024-output cap and a direct test process that lacked the service's
`render,video` supplementary groups. RAM was not the blocker. The remaining
capacity constraint is the RKLLM IOVA window, which admits one 3B lane at a time.

## Measured device performance

Unit C, RK3588 SDK 1.3.1, W8A8, 48 requests (36 quality + 12 latency), greedy
decode, fresh KV cache per request. The stock/V79.6 table below is the
historical native comparison at the 2048-token serving profile with guards off.
The live 16k/8192 profile was re-run through the full 48-request matrix on
18 October 2026 over streaming HTTP with guards on; the V81 run is the next
subsection and the V79.6 run that follows is retained as history. Because the
path and profile differ, the runs are not a clean delta.

| Metric | Stock 3B | V79.6 |
|---|---:|---:|
| TTFT median | 669.7 ms | 625.1 ms |
| TTFT p90 | 1327.0 ms | 734.1 ms |
| Decode median | 8.03 tok/s | 7.82 tok/s |
| Decode aggregate | 7.78 tok/s | 7.80 tok/s |
| Response median | 6772.1 ms | 6040.8 ms |
| SDK memory max | 3250.3 MB | 3393.3 MB |
| Load | 5083.6 ms | 5267.1 ms |

At 1,536 prompt tokens V79.6 measured 11,733 ms TTFT and 6.98 tok/s decode,
against stock 11,544 ms and 7.23 tok/s. The 128-token median favors V79.6; the
longest prompt is slightly slower. These are local near-bench timings, not a
field, thermal-soak or multi-user claim.

### V81 live 16k/8192 profile

Run 18 October 2026 against the production service on port 8081
(`--context 16384 --max-new-tokens 8192`, model `ac7021b6…`, guard
`ac69b131…`), streaming HTTP with guards on. All 48 requests completed; none
failed. The capture is `device_v81_http48_live16k_20261018.json` in the
development work directory.

The 36 quality prompts:

| Metric | V81 live 16k, HTTP, guards on |
|---|---:|
| TTFT median | 646.9 ms |
| TTFT p90 | 777.6 ms |
| Decode median | 7.96 tok/s |
| Decode aggregate | 7.94 tok/s |
| Response median | 5227.0 ms |
| Generated tokens mean | 43.2 |

Fixed latency set (64 output tokens; three cases per prompt length):

| Prompt tokens | TTFT median | Response median | Decode median |
|---:|---:|---:|---:|
| 128 | 786.7 ms | 8747.9 ms | 7.93 tok/s |
| 512 | 3419.4 ms | 11756.9 ms | 7.56 tok/s |
| 1024 | 7528.1 ms | 16504.7 ms | 6.96 tok/s |
| 1536 | 11932.6 ms | 21687.1 ms | 6.46 tok/s |

Across all 48 cases: TTFT median 726.7 ms, p90 7528.1 ms, response median
6702.6 ms, decode median 7.93 tok/s and aggregate 7.67 tok/s. These are
near-bench measurements from one engineering unit, not a correctness score,
thermal soak or multi-user claim.

### V79.6 live 16k/8192 profile (historical)

Run 18 October 2026 against the production service on port 8081
(`--context 16384 --max-new-tokens 8192`, guard `54cd4829…`), streaming HTTP
with guards on. All 48 requests completed; the capture is
`device_v79_6_http48_live16k_20261018.json` in the development work directory.

The 36 quality cases:

| Metric | Live 16k, HTTP, guards on |
|---|---:|
| TTFT median | 713.4 ms |
| TTFT p90 | 860.5 ms |
| Decode median | 7.50 tok/s |
| Decode aggregate | 7.35 tok/s |
| Response median | 6765.8 ms |
| Generated tokens mean | 56.4 |

Fixed latency set (64 output tokens; three cases per prompt length):

| Prompt tokens | TTFT median | Response median | Decode median |
|---:|---:|---:|---:|
| 128 | 858.5 ms | 9074.0 ms | 7.69 tok/s |
| 512 | 3590.4 ms | 12330.3 ms | 7.20 tok/s |
| 1024 | 7708.9 ms | 17244.3 ms | 6.62 tok/s |
| 1536 | 12778.9 ms | 22995.8 ms | 6.16 tok/s |

Across all 48 cases: TTFT median 744.3 ms, p90 7681.2 ms, response median
8948.6 ms, decode median 7.26 tok/s and aggregate 7.21 tok/s. Relative to the
historical native guards-off 2048 run, the live path adds about 0.1 s at 128
prompt tokens, 0.4–0.7 s at 512–1024 and about 1.0 s at 1536; quality-case
decode is 7.50 vs 7.82 tok/s. The difference includes the HTTP/tokenizer path,
guards and the 16k engine profile, so it is not attributable to context size
alone. These remain near-bench measurements.

### Guard-on latency probe

The 48-request comparison above is guards-off. A separate 12-case fixed latency
set (128/512/1024/1536 prompt tokens, 64 output tokens, streaming HTTP) measured
the same V79.6 artifact and 2048 context with guards on and off:

| Metric | Guards off | Guards on | Difference |
|---|---:|---:|---:|
| TTFT median | 5090.9 ms | 5132.4 ms | +41.5 ms |
| TTFT p90 | 11885.7 ms | 11662.3 ms | within run noise |
| Total median | 13349.1 ms | 13356.6 ms | +7.5 ms |
| Decode median | 7.645 tok/s | 7.650 tok/s | flat |
| Decode aggregate | 7.519 tok/s | 7.561 tok/s | within run noise |

The stock direct-engine reference on the same 12 IDs was 5078.0 ms median TTFT,
11540.8 ms p90, 13340.7 ms total and 7.620 tok/s median decode. It is not the
same HTTP transport, so it is evidence for the same artifact and prompt scope,
not a claim that guards add no absolute device cost.

Combined server+engine RSS was 3,554,756 KB with guards on and 3,558,820 KB
with guards off: a 4,064 KB difference in the direction of guards-on, i.e.
inside run-to-run noise. This is process RSS, not the RKLLM SDK allocator
counter.

### Long-form and continuation probe

The seven-turn Harborlight writing test now runs at the live 16k/8192 profile
with a buffering response guard. The guard:

- normalizes an explicit paragraph count without inventing content;
- flattens a stray bullet block when the user asked for paragraphs;
- requires explicit bullet requests to be separate sentence lines, removes
  exact repeated bullets, and never manufactures a missing bullet;
- strips a retired value when the user explicitly excludes it, and corrects a
  present-tense claim that calls a retired label or day current;
- buffers explicit paragraph, bullet and retired-value turns before streaming.

Device results with guard `04727ec5…` (the strict checker, before the final
paragraph-flatten addition):

- **Extended profile:** 7/7 turns returned. Turn 1 produced 495 words and four
  paragraphs; the four-bullet update produced four distinct complete sentences;
  the final handover normalized to three paragraphs; no retired value appeared
  in the handover; turn 7 stated no retired value as current.
- **Sustained profile:** 7/7 turns returned. Turn 1 produced two paragraphs;
  the four-bullet update produced four distinct complete sentences; the final
  handover normalized to three paragraphs; no retired value appeared in the
  handover; turn 7 stated no retired value as current.

The 8192 cap fixes the previous mid-sentence truncation. Short answer
continuation, correction retention, paragraph-count normalization and
mechanical bullet formatting are supported.

Structural checks passing does **not** mean the long-form answer is factually
qualified. The same device transcripts still show:

- invented label meanings: “teal, signaling pending processing” and “teal
  intake label for low-risk tools”, although the correction supplied only the
  label value;
- invented cadence or quantities, such as “every other week”;
- the same fact restated in different words, which exact bullet deduplication
  does not catch;
- deterministic repairs masking, rather than teaching, correction continuity.

The 3B model is therefore not qualified for accurate long-form factual writing
without either a broader verified English curriculum or a runtime notice/status
guard that discloses the limitation. That is the owner decision; the structural
guard is not evidence of model-quality completion.

The final paragraph-flatten guard is `54cd4829…`. The sustained device run
against that guard passed all 15 checks
(`evidence/V79_6_LONGFORM_SUSTAINED_8192_20261018_FINAL2_GUARD.json`), but its
transcript still calls teal “low-risk”, which the user never supplied. The
extended profile's latest complete device capture is the `04727ec5…` strict run;
the final 54cd4829 change was exercised on the sustained profile and in the
local guard suite.

## Quality evidence

- The three measured English failures are fixed: the train arrival comparison
  (08:29 arrival, 08:33 target, four minutes beyond the buffer), the revised
  45-minute drafting / 5-minute buffer plan, and the `max(0, 161−170)` fee clamp
  with no positive charge.
- 28/28 wrapped action prompts pass with `sage_remember`/`sage_recall` calls.
- The final guard `54cd4829…` was re-run on Unit C: 28/28 wrapped action
  prompts, E12_A/B, E13_A/B, S03_B and all seven streaming guard cases pass.
  Evidence: `evidence/V79_6_ACTION28_FINAL_GUARD_20261018.json`,
  `evidence/V79_6_GUARD_CHECK_FINAL_GUARD_20261018.json` and
  `evidence/V79_6_STREAM_GUARD_FINAL_GUARD_20261018.json`.
- The frozen screen's key cases pass; `V58_E03_B` remains critical on both
  V79.6 and stock.
- Fresh64: raw 15/16 tool calls, guarded 16/16. The remaining raw miss also
  fails on stock.
- The guards correct E13_A/E13_B interval arithmetic and the failed-lookup
  absence claim. They do not authorize tool actions; the runtime action
  dispatcher now owns canonical request selection and the SAGE executor owns
  the authenticated call and receipt.
- The hardened guard revision correlates write receipts by tool identity and
  preceding action, refuses another owner's private memory unless a trusted
  grant is present, and buffers only the first assistant turn after a recall
  receipt so streamed absence claims cannot escape the post-guard. Later chat
  turns resume token streaming.
- E12_A/B pass. E12_B's shorter cancellation email ("cancelled", "do not
  attend", no replacement/contact) is valid; the smoke checker now accepts it
  without requiring the literal phrase "not go ahead".

Known limits:

- The raw model, without guards, still has E13 interval drift and the S03_B
  absence clause. Do not disable the guards without re-running the guarded
  checks.
- The device artifact is 16k context; the host model is 32k.
- The guards are response-side checks, not an authorization boundary. They
  cannot make an action permitted.
- The runtime action boundary is implemented in
  [`software/runtime/src/sage_lantern/action_guard.py`](../../software/runtime/src/sage_lantern/action_guard.py)
  and `ConversationService.dispatch_save()`. It executes only canonical
  request-derived calls through an admission-checked SAGE executor and returns
  the receipt unchanged. The wheel is installed in Unit C's production venv
  (`cfeb9240…` after the 18 October policy rebuild; `action_guard.py`
  `f844e2ed…`) and the staged package verification passes. The product
  UI/HTTP route that will invoke this service method is still separate work.
- Memory is measured as process RSS and service-cgroup current usage, not as
  the RKLLM SDK allocator under a long soak. At 16k/8192 the engine was 3.99 GB
  RSS and the service cgroup 4.16 GB; this is not a thermal or multi-hour soak.
- The model remains a 3B near-bench build; broader arithmetic, field-range and
  multi-user claims need their own qualification. The 8192 cap is active and
  the structural long-form guards work, but invented label meanings/cadence and
  semantic restatement remain model-quality defects. The full 48-request
  benchmark at the 16k/8192 profile completed on 18 October 2026 (48/48;
  quality-case TTFT 713.4 ms median / 860.5 ms p90, decode 7.50 tok/s median).
  It uses a different path from the historical native guards-off table, so use
  it as the live-profile measurement rather than as a like-for-like delta.

## V80 curriculum development

The owner chose the broader verified English curriculum on 18 October 2026.
The first increment added 994 verified synthetic rows: 614 for unknown/defined
label semantics, missing cadence and missing-fact grounding, and 380 for
value, label, cadence, assistant-error, ambiguity and history corrections.
They were mixed with V79.6 action schema, V79.2 notices, V79 clarify rows,
V79.8 arithmetic and a stratified V46 English sample: 1,726 unique rows,
1,726 updates, rank 16, LR 1e-6. V80 and a 736-update V80.1 repair were
evaluated on a separately authored, 64-case sealed set plus the frozen
development sets.

On the sealed set, V80 scored 63/64 against 36/64 for V79.6 and 33/64 for
stock. The V80 family results were 16/16 unknown labels, 7/8 defined labels,
8/8 missing cadence, 4/4 defined cadence, 8/8 missing grounding and 8/8, 6/6,
4/4 and 2/2 across the four correction families. The one remaining sealed
failure ignored a definition supplied in the same turn. V80 also held all 28
wrapped stock action prompts semantically, passed E12_A/B, and reproduced the
known raw E13/S03 guard dependencies.

The tradeoff is confined to the raw development panels: V80 scored 17/24 on
the frozen fresh64 tool/status cases against 21/24 for V79.6. The new failures
are flat tool-call arguments, trailing prose after a valid call, and
status/absence prose. The action guard already canonicalizes the flat shape
and replaces prose claims, and the status guard repairs failed-lookup prose,
but the guard is not yet integrated at the SAGE/OpenClaw dispatcher. V80.1
fixed the trailing-prose call and a 3.3k-character E03_A derailment but traded
one status case and did not improve the aggregate.

Neither candidate is exported, promoted or device-measured. The next gate is
guarded-path integration plus a higher-capacity balanced English, arithmetic,
status and action pass; do not present V80 as a production or long-form
qualification. Evidence:
[`V80_CURRICULUM_20261018.json`](evidence/V80_CURRICULUM_20261018.json).

### V81 balanced rank-32 candidate

A rank-32 balanced retrain from the V79.6 chain (2,028 unique rows, 2,028
updates) restored the action/status behavior V80 lost while keeping the
curriculum gains. On the fresh 64-case sealed set V81 scored 64/64, against
34/64 for stock, 36/64 for V79.6 and 63/64 for V80. Frozen fresh64 returned to
21/24, stock28 held 28/28 and E12_A/B passed.

The remaining raw failures are `V58_S13_B` (used a quoted public destination
instead of the authorized private home), `V58_S03_B` (errored-lookup absence
prose), `V58_S16_B` (ignored a supplied recall query) and `V58_E03_A`
(arithmetic derailment). The existing save guard canonicalizes S13_B to the
private home and the status guard repairs S03_B; S16_B and E03_A remain raw
failures. V81 is now exported to merged HF and RKLLM, and deployed on Unit C.
Bare V81 weights would carry the raw S13_B authorization failure, so the
publication ships the guarded runtime rather than implying the weights are
safe alone. Evidence:
[`V81_CURRICULUM_20261018.json`](evidence/V81_CURRICULUM_20261018.json).

The guarded path is now clean. `guard_action` adds canonical recall handling
for supplied/null queries; the guarded V81 capture scores **64/64 sealed,
24/24 frozen fresh64 and 28/28 wrapped actions**, with E12_A/B, E13_A/B and
S03_B all passing. The action guard suite is 38/38. The raw E03_A arithmetic
derailment remains the one disclosed model-quality gap.

The live product policy exposed one more raw-model failure: it invented a
meaning for an undefined label and a cadence for an unspecified interval.
`response_guards.label_answer()` now short-circuits undefined colour/label
questions before inference, while leaving a definition supplied in the same
conversation alone. The deployed response-guard suite passes 57 tests plus 20
subtests. Live probes on Unit C return a clarification for undefined
`glaucous` and check frequency, and honor a supplied "teal means pending"
definition.

The non-commercial research preview is published in the public model-only repo
[`l33tdawg/lantern-3b`](https://github.com/l33tdawg/lantern-3b) at
[`v81-research-preview`](https://github.com/l33tdawg/lantern-3b/releases/tag/v81-research-preview),
as a GitHub prerelease. The tag resolves to commit `27944a4`; the documentation
update is `f717ee3`. The release ships the refreshed 773,583,555-byte adapter
bundle (SHA-256 `c58c70ed…`, 46 files, including the grounded composer, the merged-model receipt and
reassembly instructions), the exact RK3588 artifact split into
`v81-rkllm.part-00` through `part-02`, the part checksums and the full model
card. Reassembled device SHA-256 is `ac7021b6…`. Do not publish bare weights,
and do not claim commercial use without Alibaba Cloud permission.
The `sage-lantern` monorepo remains private; the public model repo is the
canonical home for the V81 card, adapters and evaluation.

The full standard model card — model details, intended use, factors, metrics,
evaluation and training data, quantitative analyses, ethical considerations,
caveats and reproduction — is tracked at
[`docs/lantern/models/LANTERN_3B_V81_MODEL_CARD.md`](models/LANTERN_3B_V81_MODEL_CARD.md)
and included in the release asset. It is the primary documentation for this
preview.

## Deployment and rollback

V81 is activated by
`/etc/systemd/system/sage-lantern-llm.service.d/60-v81-token.conf`
(`bc96bf53…`), which overrides only `ExecStart` and `LD_LIBRARY_PATH`. The base
unit, user/group, render/video supplementary groups, restart policy, fallback
URL and sleep/read-write drop-ins are preserved. The earlier V79.6 drop-in
`50-v79.6-token.conf` (`298b5b50…`) remains in place as the rollback.

The deployment script
[`work/lantern-curriculum-v80-20261018/deploy_v81_unit_c.sh`](../../work/lantern-curriculum-v80-20261018/deploy_v81_unit_c.sh)
stages the 3.8 GB model, verifies it, installs the drop-in, restarts the
service, waits for `/health`, and restores the V79.6 drop-in if health does not
come up. The backup directory `/var/backups/lantern-v81-20261018/` holds the
previous drop-in and `V81_MODEL.sha256`.

Rollback to V79.6:

```sh
sudo systemctl stop sage-lantern-llm.service
sudo rm /etc/systemd/system/sage-lantern-llm.service.d/60-v81-token.conf
sudo systemctl daemon-reload
sudo systemctl start sage-lantern-llm.service
```

The V79.6 model file remains on disk. `sage-lantern-voice-loop.service` is
restarted after the V81 health check and is active.

## Reproduce

```sh
# Export the merged V81 HF model to RKLLM W8A8 at the toolkit context cap
python3 export_rkllm_chat_candidate.py \
  --model fused_hf_v81_20261018 \
  --output export_v81_rkllm_16384_20261018 \
  --name <fresh-owned-container> \
  --max-context 16384 --execute

# Device performance (existing 48-request harness)
python3 run_device_comparison.py \
  --model /opt/sage-lantern/npu/models/v81-fused-w8a8-opt1-ctx16384-rk3588.rkllm \
  --output device_v81_48

# Live-profile 48-request run against the production service (streaming HTTP)
python3 run_device_http_benchmark.py \
  --base-url http://127.0.0.1:8081 \
  --requests device_requests48.jsonl \
  --output device_v81_http48_live16k_20261018.json

# Guarded functional check against the production port
python3 run_device_guard_check.py \
  --base-url http://127.0.0.1:8081 \
  --output device_v81_guard_check.json

python3 run_device_stream_guard_check.py --base-url http://127.0.0.1:8081
```

Local evidence: `work/lantern-curriculum-v80-20261018/captures/device_v81_http48_live16k_20261018.json`,
`V81_RESULT.json`, the exported merged HF model and the RKLLM artifact. The
tracked summary and hashes are in
[`evidence/V81_CURRICULUM_20261018.json`](evidence/V81_CURRICULUM_20261018.json).
