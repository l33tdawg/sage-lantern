# B radio commissioning — configuration readback accepted

Parent reports successful once-only B commissioning and final configuration readback on 2026-09-07, recorded below. This documentation update performs no B actions and does not independently re-query the device or inspect its journal. Parent owns deployment and B operations. `scripts/lantern/commission-b-radio.py` imports unchanged sibling `collect-b-radio-inventory.py` for USB identity, opener checks and bounded reads; ship and review both together. No additional Python package is needed. Existing collector behavior is unchanged.

## Device acceptance — parent-reported 2026-09-07

Parent reports **75 tests passed**, matched artifacts staged at `/opt/lantern-b-radio-commission-v1`, and one explicitly consented apply with successful final readback. Reported artifact hash prefixes: helper `2efe8424`, collector `3d486f`; these are abbreviated identifiers, not independently verified full deployment hashes in this update.

| Public field | Actual pre-apply query | Final readback |
|---|---|---|
| Frequency wire value (kHz) | 869618 (869.618 MHz) | **917375 (917.375 MHz)** |
| Name | `C9851003` | **`SAGE-LANTERN-B`** |
| Bandwidth wire value (Hz) | 62500 (62.5 kHz) | 62500, unchanged |
| SF / CR | 8 / 5 | 8 / 5, unchanged |
| Firmware TX setting | 10 | 10, unchanged |
| Repeat-enable / path-hash mode | 0 / 0 | 0 / 0, unchanged |
| Reported maximum TX setting | 22 | 22, unchanged |
| Public key | `c98510036349cec6495b3712880a12ea58d23cd8f09ea9fe9cd804cd98b29e54` | Unchanged |

Device binding: USB serial `44:1B:F6:62:56:54`, firmware `v1.17.1-d929643`. The observed initial frequency/name did **not** match A's intended profile; acceptance rests on actual before/after readback, not factory-default assumptions.

**Preserve `/var/lib/sage-lantern-b-radio-commission/attempt.jsonl` in place.** This successful attempt remains the exclusive no-retry fence. Do not delete, truncate, replace or relocate it to permit another apply; no more B actions are authorized by this update. Configuration readback is accepted only within the reported scope—not reboot persistence, measured RF power, radio silence, peer reachability or RF/application delivery.

## Pinned scope

- USB `303a:1001`, serial `44:1B:F6:62:56:54`; exact existing by-id path in the collector.
- Firmware `v1.17.1-d929643`, protocol 13; public key `c98510036349cec6495b3712880a12ea58d23cd8f09ea9fe9cd804cd98b29e54`.
- Target: 917.375 MHz, 62.5 kHz, SF8, CR5, firmware TX setting 10, name `SAGE-LANTERN-B`. Preserve observed repeat-enable and path-hash mode; do not infer factory defaults match A.
- Setter allowlist: TX `0c 0a`; name `08` + ASCII name; radio `0b 7f ff 0d 00 24 f4 00 00 08 05 RR`, with explicit preflight repeat byte `RR`. Query payloads: `16 0d`, then `01` + seven zero bytes + ASCII `Lantern`.

## Protocol sources and validation

All offsets below are zero-based in the response payload, including its response-code byte, excluding the three-byte serial header. Host framing is `<` + little-endian uint16 payload length; device framing uses `>`. The helper caps frames at 176 bytes. Integers in payloads are little-endian. [Official companion protocol](https://github.com/meshcore-dev/MeshCore/wiki/Companion-Radio-Protocol)

| Response | Validated layout |
|---|---|
| Device-info, exactly 82 bytes | `[0:2]=13,13`; `[60:80]` NUL-terminated exact firmware string; `[80]` repeat 0/1; `[81]` path-hash mode 0/1/2. `[4:8]` BLE PIN is never emitted. |
| Self-info, 58–89 bytes | `[0:2]=5,1`; signed TX byte `[2]`, maximum `[3]`; exact public key `[4:36]`; frequency uint32 `[48:52]`, bandwidth uint32 `[52:56]`, SF `[56]`, CR `[57]`; remaining name `[58:]`. |
| Setter reply, exactly one byte | `00`; an error, push, trailing bytes, timeout or malformed response fails closed. |

Device-info length derives from 4 header bytes + 4 PIN + 12 build-date + 40 manufacturer + 20 version + 2 flags. Self-info fixed prefix is 58 bytes. Frequency/BW wire integers are 917375/62500. Name, RF and TX handlers save preferences; RF/TX apply immediately. Omitted repeat defaults false, so the helper always includes it. Readback is preference reporting, not hardware-register/calibrated-power verification. [Pinned firmware handlers, lines 943–1000 and 1121–1329](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/MyMesh.cpp#L943)

`node_name[32]` includes a terminator: at most 31 transmitted name bytes, giving a maximum self-info length of **89**, stricter than the existing inventory collector's 90-byte allowance. This helper additionally rejects embedded NUL, invalid UTF-8 and non-printable names. [Pinned NodePrefs declaration](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/NodePrefs.h)

## Invocation reference — not authorization to rerun

The parent has completed the once-only apply above. Commands below document the interface; **do not rerun apply on commissioned B**. Any further inspection or recovery requires separate parent authorization.

Linux root is required. Default reads only USB metadata and competing-openers inventory; it does not open the TTY:

```sh
python3 scripts/lantern/commission-b-radio.py
```

Read-only public profile inspection requires explicit review of stopped producers and serial open/close electrical behavior:

```sh
python3 scripts/lantern/commission-b-radio.py --query \
  --operator-reviewed-open-close-and-stopped-producers
```

Before apply, parent must provision `/var/lib/sage-lantern-b-radio-commission` as an existing root-owned real directory, mode 0700, under root-owned non-group/other-writable real ancestors. The helper does not create or repair it. Parent must verify exclusive ownership, absence of a previous attempt and release provenance before authorizing:

```sh
python3 scripts/lantern/commission-b-radio.py --apply \
  --operator-reviewed-open-close-and-stopped-producers \
  --consent SET-B-917375-62500-8-5-TX10-SAGE-LANTERN-B
```

## Durable transaction and refusal contract

1. Validate consent and exact USB identity; refuse competing openers. Apply creates fixed `attempt.jsonl` with exclusive creation, no symlink following, mode 0600; fsync file and parent before serial access. Directory flock serializes compliant attempts. No selectable journal filename or resume flag exists.
2. Exclusively open the pinned TTY, verify device identity, apply raw 115200 8N1, take flock/TIOCEXCL. Recheck USB and competing openers before/after exchanges. No DTR/RTS command, reset, flush or unsolicited-message draining is added. Privileged competing openers remain outside this protection; parent must keep producers fenced.
3. Verify firmware/key and collect a redacted snapshot. Persist/fsync prepared record. For each differing field group, persist/fsync `setter_intent` with unknown outcome **before** sending; require exact OK and a complete fresh device-info/self-info snapshot matching the expected intermediate profile before recording verification. TX precedes name, then radio. Matching fields are not rewritten.
4. Final full readback must match target, identity, repeat, path-hash mode and reported maximum power. Successful connection teardown precedes the fsynced completion record. Output includes only selected public fields; raw frames, BLE PIN, coordinates and exception text are not output or journaled.
5. Any exception, crash, error ACK, timeout, partial transport failure or mismatch stops the sequence. Partial writes only continue the same frame; commands are never resent. The journal remains even if empty or complete, and blocks **every later apply**. After uncertainty, a separate `--query` may inspect current state but neither clears nor authorizes retry. Parent must preserve evidence and review any recovery separately; do not delete the attempt to make the command pass. No automatic rollback.

No advertisement, message, contact/key/channel mutation or reboot command is allowed. This does **not** establish radio silence: firmware background operation and USB electrical effects are separate concerns. Readback does not establish persistence across reboot, regulatory compliance, calibrated antenna power, peer reachability or application delivery. No peer test belongs in this helper. A repeat-enabled profile may be rejected by firmware frequency policy; stop rather than silently disabling repeat.

## Offline validation

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  scripts/tests/test_commission_b_radio.py scripts/tests/test_collect_b_radio_inventory.py
```

Tests use fake serial responses only. They cover pins/lengths, exact wire bytes, repeat preservation, mismatches, consent, exclusivity, fsync ordering, retained journals, no retry, redaction and the unchanged collector. Journal filesystem tests use temporary files with simulated trusted ancestor metadata; they do not establish B's actual filesystem or serial-driver behavior. Parent-reported device configuration acceptance is recorded separately above; no RF delivery acceptance follows from these tests or readback.

Local results: **75 passed** for the two script test modules; **148 passed** when also including `software/runtime/tests/test_meshcore_transport.py` with `PYTHONPATH=software/runtime/src`. Collector SHA-256 remained `3d486fb17b12628fe31c6cea188613bd51cc34145bb3f42e01aa28f07c89a925` across this change.
