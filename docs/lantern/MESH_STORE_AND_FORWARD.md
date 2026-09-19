# Mesh store-and-forward: Lanterns as repeaters and custodians

Status: design + core codec/store/ingest/handover built 2026-09-19. The
depositor-side routing and on-device acceptance are open; the forward-path
airtime budget (r5, upstream PR meshcore-dev/MeshCore#3450) is deployed on both
units with the relay cap defaulted to 10%, but repeat is still switched off, so
nothing is relayed yet. This document
supersedes the "absent-recipient store-and-forward" bullet in
`MESHCORE_CAPABILITY_ROADMAP.md` as the concrete plan; the roadmap keeps the
upstream analysis, this is what Lantern builds in its own layer.

## 1. Why this layer exists

MeshCore gives us three things and not a fourth:

* a **companion node** — the Heltec radio attached to the Lantern host, which
  sends and receives for that host;
* **repeaters** — nodes that re-transmit flood traffic in real time;
* an **offline queue** of 16 frames — for a disconnected *app*, not an
  unreachable *peer*; direct messages are never evicted and a full queue drops
  new ones.

What it does not have is **custody**: a place where a message waits while its
recipient is off the air. An awake repeater is not a mailbox, and ordinary RF
repeaters are not assumed to understand SAGE, issue receipts, or persist
messages. So if A sends to an offline B today, nothing on the air holds the
message. The durable copy is the sender's own outbox row, and delivery happens
only when B is back in range of A (or of a real-time relay path).

Lantern closes this in two layers:

1. **Repeater + node (firmware, r4).** Each Lantern's radio forwards mesh
   traffic for its neighbours while continuing to serve its own host, so a
   message can cross a chain of Lanterns instead of needing one hop.
2. **Custody (Lantern layer, this document).** A Lantern can accept a sealed
   envelope addressed to someone else, hold it, and hand it over when that
   peer is next seen. The original sender keeps its own durable copy and
   retries; custody is what makes delivery possible after the sender has gone
   away.

## 2. Repeater + node: what r4 actually changes

The companion firmware already contains the forwarding path:
`Mesh::routeRecvPacket()` re-transmits flood traffic when
`allowPacketForward()` agrees, and the companion's override is
`_prefs.isRepeatEn()`. `CMD_SET_RADIO_PARAMS` carries the repeat bit (protocol
v9+) and the host commissioning schema already models it.

The gate is the frequency allowlist in `MyMesh::isValidClientRepeatFreq()`:
without a build-time override, repeat is accepted only on 433.000, 869.495 and
918.000 MHz. The Lanterns are commissioned on **917.375 MHz**, so
`client-repeat-917.patch` builds the companion env with
`-D ALLOWED_REPEAT_FREQ_RANGE={917375,917375}` and version `r4`.

Properties of the r4 build:

* Repeat is **opt-in per unit** and off by default (`repeat.disable_fwd = 1`).
  Flashing r4 changes nothing on air; enabling it is an explicit, reviewed
  commissioning write of the same radio parameters with repeat = 1.
* Forwarding inherits the base Mesh behaviour: seen-table dedupe, path-length
  bound (`MAX_PATH_SIZE`), hop-scaled random retransmit delay, and no
  region/transport-code filtering (the companion's `filterRecvFloodPacket()`
  returns false; the dedicated repeater build has extra filters). The r4
  companion is therefore a useful relay, not a hardened infrastructure
  repeater.
* The **forward-path airtime budget (r5)** is built and carried in the r5
  image. The Lantern build defaults it to 10% of the rolling window for relayed
  traffic (the upstream change defaults to off); with repeat off it changes
  nothing on air either way. It accounts every
  forwarded packet in a rolling window (`mesh::AirtimeBudget`), refuses a
  forward when the relay budget is spent, and reports relay airtime and
  refusals. It is enforced in the dispatcher when a retransmit is queued
  (`processRecvPacket`), not in `allowPacketForward()`: that is the single
  chokepoint for flood, routed and trace relays, and a refused forward never
  takes a queue slot. Setting `fwd_af` / `set fwd.dutycycle` bounds the relay
  role separately from the node's own traffic; with it unset the ledger still
  reports utilisation, which is what the repeat-on decision needs.

## 3. Custody protocol (`SLC1.`)

### 3.1 Deposit format

A custody deposit is an ordinary sealed peer envelope (`LME1.`, see
`mesh_envelope.py`) with a small clear outer header:

```
SLC1. base64( header || LME1-envelope )
header = version:u8 | recipient_radio_prefix:6 | message_id_digest:16 | expires_at:u32 | size:u16
```

* `recipient_radio_prefix` — the six-byte radio key prefix the transport
  already uses to address a contact, so a custodian can route without opening
  anything.
* `message_id_digest` — `sha256(message_id)[:16]`, for dedupe. The real id stays
  inside the sealed box.
* `expires_at` — the custody copy's lifetime, bounded by
  `MAX_TTL_SECONDS` (14 days hard cap; the product default is a policy choice,
  expected 72 h).

The envelope bytes are carried **unmodified**. There is no re-sealing, no
re-encryption and no plaintext anywhere in custody.

### 3.2 Deposit flow

1. The sender's runtime tries the normal path to the peer (radio, then the
   planned SAGE fallback). When the peer is unreachable and at least one
   trusted custodian contact is reachable, it wraps the already-sealed envelope
   as an `SLC1.` deposit and sends it to the custodian using the ordinary
   fragment transport and receipt flow.
2. The custodian's runtime receives fragments addressed to its own radio
   contact, reassembles them, sees the `SLC1.` prefix, and calls
   `parse_custody()`. It does **not** attempt to open the envelope.
3. Admission is a transport trust decision: the deposit is accepted only when
   it arrived from a verified contact configured as a trusted depositor, inside
   per-source and per-recipient quotas. There is no content check, because the
   custodian cannot verify or read content by design.
4. The custodian stores the deposit in the protected vault-backed store (same
   storage and access gates as the message vault) with its TTL.

Implemented 2026-09-19: `MeshInbox` accepts an optional custody sink and, when
the reassembled wire starts with `SLC1.`, parses it, admits it through
`MeshCustodian` (verified depositor, quotas, TTL) and clears the capture slot
with a `custodied` result. Custody off means the same frame is refused as
unsupported, never silently stored. The sender-side deposit routing (deciding
when a queued message should be deposited with a custodian) is not wired yet.

### 3.3 Retrieval flow

1. Custody retrieval is triggered by evidence that the recipient is present:
   the recipient's radio contact appears in a contact read, or its advert is
   observed. Presence is a trigger to attempt delivery, never a delivery claim.
2. The custodian takes pending deposits for that recipient's prefix, oldest
   first, and sends the **inner `LME1.` envelope** to the recipient through the
   normal contact path, fragmenting it exactly as a direct send.
3. The recipient's existing inbound path verifies the original sender's
   signature, dedupes by `(sender, message_id)`, stores the message durably and
   emits its signed delivery/read receipt to the **original sender** (not to
   the custodian).
4. The custodian keeps the copy until its TTL or until the caller presents
   explicit evidence to `confirm_delivered()`. It never claims delivery: the
   recipient dedupes duplicate handovers from multiple custodians, and the
   sender's own outbox still owns the "delivered" state.

Implemented 2026-09-19: the background dispatch wake ends with a bounded
handover pass. For each inventory contact whose radio prefix matches a held
deposit, the runtime sends up to four fragments of the inner envelope per wake
through the same airtime reservation as ordinary dispatch, continues from the
stored fragment cursor on the next wake, re-sends a completed pass only after
300 s, stops after eight passes and leaves the copy held until TTL. Sending is
not delivery and the record says so.

### 3.4 Limits and failure modes

* **Bounded store.** Entries, total bytes and per-recipient copies are capped;
  expired deposits are purged on every operation. Defaults to size in code:
  256 entries / 1 MiB / 16 per recipient.
* **Dedupe.** `(recipient_prefix, message_id_digest)` stores once; the first
  copy wins. Multiple custodians holding the same deposit is expected and
  harmless.
* **Custodian drop.** A custodian that drops a deposit can cost the sender its
  copy, but cannot forge or alter one: the recipient verifies the original
  signature inside the sealed box and a substituted wire fails to open.
* **Metadata.** A custodian necessarily learns the recipient prefix, message
  size, timing and the depositing contact's identity. It does not learn sender,
  content, or the real message id. This must be visible in the product's
  custody consent text.
* **No custody receipt (yet).** The recipient's receipt goes to the original
  sender. A custody-delivery receipt (so a custodian can delete early with
  evidence) is a follow-up; until then TTL plus quotas bound storage.

## 4. Sender-side policy

The custody attempt sits between "radio direct" and "keep it locally":

```
verified radio route to peer?          -> radio (unchanged)
else reachable trusted custodian?      -> deposit (SLC1.) + keep local row
else                                   -> hold locally, retry on schedule
```

Rules that carry over from `MESSAGE_TRANSPORT_ROUTER.md`:
* one delivery path per message id — never radio *and* custody *and* SAGE for
  the same message;
* a deposit is not delivery; the row stays "not delivered yet" until the
  peer's signed receipt arrives;
* the sender's own copy is never deleted because custody accepted it.

## 5. Privacy and safety review

* Custody payloads are the same end-to-end sealed envelopes used today; the
  custodian's compromise exposes metadata, not content.
* Deposits are only admitted from verified, configured depositor contacts;
  unknown senders cannot use a Lantern as free storage.
* Storage is vault-backed, subject to the same lock/unlock and at-rest rules as
  the message store. Custody must not create a second plaintext path.
* Quotas are enforced before write; a full store rejects rather than evicting
  an existing deposit.
* The UI must show custody state honestly: "held for delivery to X", never
  "delivered"; the recipient's signed receipt is the only delivery evidence.
* A custody-enabled Lantern is a relay for other people's messages. Product
  consent must say that plainly, together with the airtime cost.

## 6. Airtime

| path | cost | guard |
| --- | --- | --- |
| repeat (r4/r5) | every forwarded flood frame doubles channel use for that frame | r5 rolling relay budget (default off) + relay accounting; host gate covers host dispatch only |
| deposit | ~1 fragment set + ACK per deposit | quotas; prefer deposits only when the direct path failed |
| retrieval | one fragment set per held deposit per contact appearance | one attempt per deposit per presence window; TTL bounds the backlog |
| receipt | unchanged direct path | existing |

Repeat must not be enabled on a duty-cycled band until the r5 forward budget is
deployed *and set* on the unit, or the owner explicitly accepts the regulatory
position. The mechanism now exists; the policy (whether to cap, and at what
percentage) is the owner's decision, and the on-unit value still has to be
written before repeat is switched on. This remains the single most important
gate in this document.

## 7. Acceptance gates

1. **Firmware**: r4 builds; repeat is refused with `ERR_CODE_ILLEGAL_ARG` when
   asked on a frequency outside the pinned range; repeat reads back as 0 before
   commissioning and 1 after; identity, key and RF profile unchanged across
   the flash.
2. **Relay**: with repeat on one unit, a flood packet from another node is
   re-transmitted (packet log / `packets_sent`) and the relaying unit's own
   host messaging keeps working. A true three-node multi-hop delivery proof
   waits for a third MeshCore node.
3. **Custody codec/store**: malformed, oversized, wrong-version and
   wrong-prefix wires are refused; TTL bounds enforced; quotas enforced;
   dedupe by recipient + digest; expired purge; records round-trip. (Covered
   by `tests/test_mesh_custody.py`.)
   Durable service: strict rows, tamper fail-closed, handover progress and
   bounds. (Covered by `tests/test_mesh_custodian.py`.)
   Ingest: an `SLC1.` contact frame is admitted to the sink and never opened;
   custody off refuses it. (Covered by `tests/test_mesh_inbox.py`.)
   Status: the counts-only route reaches the daemon through the real IPC
   facade. (Covered by `tests/test_mesh_radio_events.py`.)
4. **Custody end to end**: A deposits for offline B at custodian C; C delivers
   when B appears; B's inbox row appears once even if C retries; A's row shows
   delivered only after B's signed receipt; C's store releases on TTL.
5. **Airtime**: deposit + retrieval measured in `tx_airtime_seconds`; the
   forward budget holds under a sustained flood; no duty-cycle violation.

## 8. Open decisions

1. Custodian discovery: today the design assumes the owner configures trusted
   custodian contacts. A signed capability flag on the contact card ("accepts
   custody, TTL, quota") would replace that with automatic discovery.
2. Custody-receipt protocol so custodians can release on evidence instead of
   TTL.
3. Default TTL, quotas and per-source limits for a village deployment.
4. Whether custody deposits ride the existing contact transport (as designed)
   or a dedicated custody channel; the codec is transport-agnostic.
5. The forward-path budget (r5) parameters for 917.375 MHz in the deployment
   region.
