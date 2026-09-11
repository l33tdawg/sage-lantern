# SAGE Lantern MeshCore Host Protocol Test Plan

This plan produces the evidence needed for the `sage.lantern.message.v1` software envelope and acceptance fixture. Do not infer limits from marketing specifications.

## Test identity

| Field | Unit A | Unit B |
|---|---|---|
| Heltec board/revision | Pending | Pending |
| Radio frequency variant | Pending | Pending |
| MeshCore firmware/version | Pending | Pending |
| USB VID:PID and stable path | Pending | Pending |
| Host protocol/baud/framing | Pending | Pending |
| Ordinary SAGE agent ID | Pending | Pending |
| Separate radio identity | Pending | Pending |

## Host enumeration and recovery

- [ ] Enumerates after cold boot.
- [ ] Re-addresses through a stable identifier after reboot.
- [ ] USB disconnect creates a visible degraded state.
- [ ] Reconnect recovers without reimaging or manual identity replacement.
- [ ] Unit A and Unit B identities are distinct.

## Payload sweep

Use deterministic payloads and record encoded size, not only source-body size.

| Test | Body bytes | Encoded bytes | Fragments | Attempts | Duplicates | A→B result/time | B→A result/time | Evidence |
|---:|---:|---:|---:|---:|---:|---|---|---|
| 1 | 32 | — | — | — | — | Pending | Pending | Pending |
| 2 | 64 | — | — | — | — | Pending | Pending | Pending |
| 3 | 128 | — | — | — | — | Pending | Pending | Pending |
| 4 | 256 | — | — | — | — | Pending | Pending | Pending |
| 5 | 512 | — | — | — | — | Pending | Pending | Pending |
| 6 | 1024 | — | — | — | — | Pending | Pending | Pending |
| 7 | Measured ceiling | — | — | — | — | Pending | Pending | Pending |
| 8 | Ceiling + 1 | — | — | — | — | Expected safe rejection | Expected safe rejection | Pending |

## Adverse cases

| Case | Expected behavior | Observed result | Evidence |
|---|---|---|---|
| Duplicate source + message ID | Deduplicated before agent exposure | Pending | Pending |
| Expired TTL | Rejected | Pending | Pending |
| Unsupported schema version | Rejected | Pending | Pending |
| Malformed envelope | Rejected | Pending | Pending |
| Unauthorized source | Rejected | Pending | Pending |
| Fragment loss | Bounded retry/failure; no false delivery | Pending | Pending |
| Radio/USB reconnect during send | Visible queued/degraded/failure state | Pending | Pending |

## Handoff summary for `codex/sage`

```text
Verified host protocol:
Maximum reliable encoded payload:
Fragmentation behavior:
Retry/timeout behavior:
Duplicate behavior:
Reboot/reconnect behavior:
Recommended beta body limit:
Evidence bundle:
Open software decision:
```
