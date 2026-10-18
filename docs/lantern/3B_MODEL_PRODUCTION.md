# Lantern 3B model — Unit C production deployment

**Status:** live on Unit C, 18 October 2026. Owner-authorized, Unit C only.

Unit C's production chat port (8081) runs a 3B English model through the
RKLLM token engine with deterministic interval and status response guards. The
previous 2B prompt-string service remains the rollback; the NPU vision lane on
8082 is unchanged.

## What runs

| Component | Path / identity |
|---|---|
| Model | `/opt/sage-lantern/npu/models/v79_6-fused-w8a8-opt1-ctx16384-rk3588.rkllm` |
| Model SHA-256 | `41172d782a6a401549ab2000f2ef44debb890d69c6a72e228c4548ccc3de78c6` |
| Token engine | `/opt/sage-lantern/npu/token-engine/bin/rkllm_engine_token` (`11fcb514…`) |
| RKLLM runtime | `/opt/sage-lantern/npu/token-engine/lib/librkllmrt.so` (`f25e9b09…`) |
| Tokenizer | `/opt/sage-lantern/npu/token-engine/assets/tokenizer.json` (`c0382117…`) |
| Server | `/opt/sage-lantern/npu/token-engine/libexec/rkllm_server.py` (`b568af7d…`) |
| Guards | `/opt/sage-lantern/npu/token-engine/libexec/response_guards.py` (`07d64238…`) |
| Engine | `/opt/sage-lantern/npu/token-engine/bin/rkllm_engine_token` (`c4cf4fda…`, 16k/8192 limits) |
| Tokenizer runtime | dedicated venv, `tokenizers` 0.23.3 |
| Service | `sage-lantern-llm.service`, loopback port 8081, model id `lantern-native5-2b` |
| Fallback | `--fallback-url http://127.0.0.1:8082` (NPU vision lane) |

The model is the V79.6 fine-tune, built from the V72→V79.6 adapter chain and
merged into the publisher layout before RKLLM conversion. It is W8A8 with a
16,384-token compiled context. The training/evaluation host model remains
32,768-context; the device artifact cap comes from RKLLM toolkit 1.3.0.

**Served caps:** the current Unit C service is configured with
`--context 16384 --max-new-tokens 8192`. The 16k artifact is active; the
32k host context is not. The model often stops well before 8192 tokens. The
long-form paragraph guard buffers explicit long-form requests and normalizes
the requested paragraph count without inventing content.

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
decode, fresh KV cache per request. This historical comparison was run at the
2048-token serving profile; the current 16k profile has not been re-run through
the full 48-request matrix.

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
  (`bc2ce87b…`; `action_guard.py` `f844e2ed…`) and the staged package
  verification passes. The product UI/HTTP route that will invoke this service
  method is still separate work.
- Memory is measured as process RSS and service-cgroup current usage, not as
  the RKLLM SDK allocator under a long soak. At 16k/8192 the engine was 3.99 GB
  RSS and the service cgroup 4.16 GB; this is not a thermal or multi-hour soak.
- The model remains a 3B near-bench build; broader arithmetic, field-range and
  multi-user claims need their own qualification. The 8192 cap is active and
  the structural long-form guards work, but invented label meanings/cadence and
  semantic restatement remain model-quality defects. The full 48-request
  benchmark at the 16k/8192 profile is still pending; the table above is the
  historical 2048-context measurement.

## Deployment and rollback

The systemd drop-in
`/etc/systemd/system/sage-lantern-llm.service.d/50-v79.6-token.conf`
(`298b5b50…`) overrides only `ExecStart` and `LD_LIBRARY_PATH`. The base unit,
user/group, render/video supplementary groups, restart policy, fallback URL and
sleep/read-write drop-ins are preserved.

Rollback:

```sh
sudo systemctl stop sage-lantern-llm.service
sudo rm /etc/systemd/system/sage-lantern-llm.service.d/50-v79.6-token.conf
sudo systemctl daemon-reload
sudo systemctl start sage-lantern-llm.service
```

The 2B unit backup is
`/var/tmp/lantern-focused-finetune-20261012/deploy-20261018/sage-lantern-llm.service.pre-v79.6-token.bak`
(`1c5888ae…`). The 2B model file was not modified.

## Reproduce

```sh
# Export the merged V79.6 HF model to RKLLM W8A8 at the toolkit context cap
python3 export_rkllm_chat_candidate.py \
  --model fused_hf_v79_6_20261018 \
  --output export_v79_6_rkllm_16384_20261018 \
  --name <fresh-owned-container> \
  --max-context 16384 --execute

# Device performance (existing 48-request harness)
python3 run_device_comparison.py \
  --model /var/tmp/lantern-focused-finetune-20261012/v79_6.rkllm \
  --output device_v79_6_48

# Guarded functional check against the production port
python3 run_device_guard_check.py \
  --base-url http://127.0.0.1:8081 \
  --output device_v79_6_production_guard_check_final2.json

python3 run_device_stream_guard_check.py \
  --base-url http://127.0.0.1:8081 \
  --output device_v79_6_stream_guard_final4.json
```

Local evidence: `work/lantern-3b-focused-finetune-20261012/V40_DEVELOPMENT_DECISION.json`,
`V79_6_HANDOFF.md`, `RESULT_V79_6_DEVICE_20261018.json`,
`device_v79_6_production_smoke_final2.json`,
`device_v79_6_production_guard_check_final2.json`,
`device_v79_6_stream_guard_final4.json` and `device_long_tests_final2.json`.
The tracked summary and hashes are in
[`evidence/V79_6_DEVICE_20261018.json`](evidence/V79_6_DEVICE_20261018.json).
