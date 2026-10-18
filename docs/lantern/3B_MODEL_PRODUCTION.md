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

Only one 3B lane fits in the NPU IOVA window. The 2B and vision services are
stopped while the 3B class model is resident; the 2B is the rollback, not a
simultaneous chat lane.

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

A dedicated seven-turn Harborlight writing test was run on Unit C in two
profiles. This is separate from the earlier short-reply continuation test.

- **8192-token profile:** turn 1 produced 534 words and four paragraphs; the
  correction and four-bullet instruction were retained; the final handover was
  normalized to three paragraphs. The final note still contrasted the retired
  label (“teal, not amber”), so the “do not mention retired values” instruction
  is not fully satisfied.
- **8192-token bounded profile:** all seven turns returned; paragraph counts
  and final continuity state were normalized correctly. The four-bullet update
  repeated the Friday bullet, so bullet uniqueness remains a model-quality
  limitation.

The 8192 cap is active and fixes the previous mid-sentence truncation. Short
answer continuation, correction retention, and paragraph-count normalization
are supported; exact bullet uniqueness and strict retired-value exclusion are
not yet qualified.

## Quality evidence

- The three measured English failures are fixed: the train arrival comparison
  (08:29 arrival, 08:33 target, four minutes beyond the buffer), the revised
  45-minute drafting / 5-minute buffer plan, and the `max(0, 161−170)` fee clamp
  with no positive charge.
- 28/28 wrapped action prompts pass with `sage_remember`/`sage_recall` calls.
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
- Memory was measured as process RSS on a one-request probe, not as the RKLLM
  SDK allocator under a longer soak; the reading showed no guard-attributable
  increase.
- The model remains a 3B near-bench build; broader arithmetic, field-range and
  multi-user claims need their own qualification. The 8192 cap is active; the
  remaining long-form defects are repeated bullets and occasional mentions of a
  retired value when contrasting the correction.

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
