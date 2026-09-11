# Unit A latency investigation — 2026-09-07

Measured one owner question on boot `74baf7fa-c876-4457-a906-37593901f11b`.
Only numeric timing fields were collected; no transcript or answer was copied.

| Stage | Duration |
| --- | ---: |
| STT | 2.350 s |
| Protected SAGE context | 2.332 s |
| LLM first visible token | 61.901 s |
| First runtime text from orchestrator entry | 66.584 s |
| LLM complete | 73.735 s |
| Stage sum | 78.417 s |

Model logs independently attribute 61.790 s to processing 1,058 prompt tokens
(17.12 tokens/s), and 11.835 s to generating 52 tokens (4.39 tokens/s).
This sample establishes prefill as the dominant delay, not Whisper, hidden
reasoning or UI batching. It is not a representative median or p95.

The resident model PID remained 5401. Approximately 3.6 GiB memory was available
after completion, with 1 MiB swap used. Earlier idle temperatures were around
50–52 C; this does not prove absence of throttling during inference.

## Configuration drift requiring controlled comparison

September 5 notes document one slot, six CPU threads, a projector and a 5 GiB
model memory limit. Current fresh installation logs show six prompt/decode
threads, automatic four slots, unified KV, an 8 GiB prompt-cache limit,
unrestricted CPU affinity, no CPU quota and unlimited model memory cgroup.
Current command has no projector. Do not claim current photo readiness.

The latest task selected a slot by LRU with zero cached prompts. These differences
are candidates, not proof that four slots caused the measured slowdown. One-slot
and bounded-cache configuration need same-prompt controlled measurements and
lock/cache-erasure lifecycle review before deployment. Do not blindly restore
the historical memory limit or use old benchmark scripts that directly stop
voice and bypass the current inference guard.

## Work split

- Startup worker: redundant source verification and repeated systemd checks,
  preserving signed scans, epoch checks and lifecycle safety.
- Retrieval worker: bounded relevant context and request-scoped duplicate work
  removal, preserving suppression, provenance and the original user question.
- Hardware research worker: primary-source RK3588 CPU/GPU/NPU and model options;
  research does not establish on-device acceleration support or performance.
- Parent: inference baseline, guarded comparison and integration. No active
  model, clock, driver or commissioned runtime changes made in this investigation.

Historical all-core six-thread photo tests beat four-A76 affinity alternatives.
Do not repeat that rejected change without a distinct measured hypothesis.
