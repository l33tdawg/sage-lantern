# What Lantern needs from the mesh core

Scope: MeshCore `d9296435` (what the units run) against upstream `34404309`
(`dev`). The link numbers are computed from the profile the units are actually
commissioned on - 917.375 MHz, BW 62.5 kHz, SF8, CR 4/5
(`UNIT_B_RADIO_COMMISSIONING.md`) - with the standard LoRa airtime formula. They
are arithmetic, not board measurements, and should be re-measured before anyone
quotes them as measured throughput.

## 1. The number that shapes every other decision

| | |
| --- | --- |
| raw LoRa bit rate, current profile | 1,562 bps (195 B/s) |
| one 160-byte frame on the air | 923 ms |
| a 128-byte app fragment, transmit + radio ACK (arithmetic) | ~1.05 s |
| **confirmed throughput (arithmetic)** | **~122 B/s** |
| **confirmed throughput (measured, 1.1)** | **89 B/s, 1,432 ms round trip** |

Those are airtime arithmetic. Section 1.1 is what the units actually did, and it
is materially worse than the arithmetic for confirmed delivery - because the
per-frame ACK round trip, not the airtime, is what a fragment costs.

## 1.1 What the sweep actually measured (2026-09-13)

Both units, ~1 m apart, mesh runtime stopped on both so the probe was the only
owner of each radio, 25 frames of 128 bytes per profile, each frame confirmed by
its own mesh ACK (`work/messages-ab-20260913/measure_profiles.py` and
`meshcore_throughput.py`; raw reports `profile-measurement-20260913.json`,
`profile-ackloss-20260913.json`).

| profile | median ACK round trip | implied confirmed rate | ACKs A saw | frames B received | SNR at B |
| --- | --- | --- | --- | --- | --- |
| SF8 / BW62.5 (commissioned) | 1,432 ms | 89 B/s | 25/25 | 27/27 | 12.50 dB |
| SF7 / BW62.5 | 889 ms | 144 B/s | 22/25 | - | - |
| SF8 / BW125 | 827 ms | 155 B/s | 21/25 | - | - |
| SF8 / BW250 | 525 ms | 244 B/s | 17/25 | - | - |
| SF7 / BW250 | 390 ms | 328 B/s | 15/25 | - | - |
| SF7 / BW500 | 306 ms | 410 B/s | 25/25 | 27/27 | 12.75 dB |

1. **The fast lane is real.** The firmware's own round-trip measurement drops
   from 1,432 ms to 306 ms - 4.7x - and the implied confirmed rate rises from 89
   to 410 B/s. That is the honest version of the 56k comparison: 410 B/s is 13x
   slower than a 56k modem, not 2x. The arithmetic in section 1 assumed airtime
   alone; a confirmed fragment also pays the ACK frame, the receiver's turnaround
   and the firmware's inter-frame delays, and that is ~70% of the cost.
2. **SNR did not move** (12.50 dB at the commissioned profile, 12.75 dB at
   SF7/BW500). At 1 m the wider profile costs nothing measurable in link margin,
   so the ~10 dB sensitivity penalty is a distance effect that a bench test
   cannot show. Range claims need a distance test; nothing here establishes one.
3. **Nothing was lost.** 27 frames sent, 27 received, 27 ACKs transmitted by the
   receiver, 27 observed by the sender, at both profiles.

### The ACK loss that was not one

The first sweep at SF7/BW500 reported only 14 of 25 frames acknowledged while the
receiver counted all 27, and the obvious reading - a race in the host client -
was wrong. The receiver's own counter settled it: B *transmitted* only 16 ACKs,
matching exactly the 16 the sender heard. Nothing was lost in the air or in the
host; the frames were never distinct packets. The probe was sending
byte-identical payloads with whole-second timestamps, and at a fast profile
several frames land in the same second, so the mesh deduped them against its
seen-table and the receiver never acknowledged a repeat. Making each payload
carry its own index turned 14/25 into 25/25 with every counter agreeing. The
probe now does that (`meshcore_throughput.payload_for`), and any future rate
measurement has to.

This is worth keeping as a lesson rather than deleting: the artifact was
indistinguishable from a transport defect at the level the sender can see, and
only the receiver-side counter separated them - which is also why the receiver
reads, not just the sender's view, are worth their cost.

### The transport hole that is real, and separate

`MeshCoreClient._exchange` reads frames until it sees the code it asked for and
silently discards any push frame that arrives in the meantime
(`meshcore_transport.py:283`); `_frame` diverts only `0x88`, `0x80` and `0x8A` to
`_observe_discovery`, so `0x81` PATH_UPDATED, `0x82` SEND_CONFIRMED, `0x83`
MSG_WAITING, `0x8F` CONTACT_DELETED and `0x90` CONTACTS_FULL are read, ignored
and dropped whenever they land inside a command exchange.

This did **not** cause anything measured above, and no user-visible failure has
been traced to it. It is a latent hole: the runtime polls around all of those
pushes today, so the cost is latency rather than loss - a dropped MSG_WAITING
delays a fetch until the next poll, a dropped CONTACT_DELETED leaves a stale
directory until the next contacts read. It becomes a real defect the moment
anything is built on push-driven wakeups or on observing mesh ACKs. Fix it
before that, not because it is breaking something now.

What a page costs at the current profile:

| page | time on air |
| --- | --- |
| 3 KB (a reader-mode article) | 25 s |
| 10 KB | 84 s |
| 100 KB | 14 min |

That table is the whole "surf the web from the Lantern" story. It is a text-mode
reader with a deliberate fetch, a cache, and transcoding on the internet side.
Images are out, because 100 KB of JPEG is 14 minutes of a channel the rest of
the household also has to use.

## 2. What the core gives us today

* ~60 numbered companion commands (`examples/companion_radio/MyMesh.cpp:9-68`)
  over USB/BLE/WiFi/Ethernet, including raw access (`CMD_SEND_RAW_DATA` 25,
  `CMD_SEND_RAW_PACKET` 65).
* Mesh payload types including `RAW_CUSTOM` and `MULTIPART` (`src/Packet.h:22-48`);
  184-byte mesh payload, 64-byte path (`src/MeshCore.h:20-22`).
* Direct and flood routing, path discovery, repeaters and room servers.
* 160 bytes of text per frame at the chat layer (`src/helpers/BaseChatMesh.h:8`).
* Inbound store for a disconnected app: 16 frames
  (`MyMesh.cpp:229`), evicting the oldest *channel* message - and if there is no
  channel message to evict, dropping the new frame.
* Airtime awareness exists but is narrow: `airtime_factor`
  (`NodePrefs.h:16`, clamped 0..9 in `CommonCLI.cpp:111`) only scales the flood
  retransmit timeout (`MyMesh.cpp:921`).

## 3. Gaps, in priority order

### Tier 1 - blocks what we have already committed to

**3.1 No bulk transfer primitive.** `MULTIPART` is implemented only so a direct
burst can be ACKed (`src/Mesh.cpp:574`, `:359`, and the receiver's
`FUTURE: other multipart types??` at `:321`). Nothing fragments, reassembles,
windows, or selectively retransmits anything larger than one frame. Lantern's
own signed envelopes are therefore chopped into 128-byte text messages by the
host (`meshcore_transport.MAX_TEXT`), each paying a full ACK round trip. That is
where the 122 B/s comes from, and why a 10 KB answer or attachment is 84 s.

*What it unlocks:* attachments, page fetches, model/pack updates, history sync,
anything that is not a sentence. This is the single highest-value core change for
Lantern, and it is generically useful to every MeshCore client that has ever
wanted to move a file.

**3.2 No priority or airtime budget.** Everything shares one queue and one
channel. A bulk transfer in flight starves message ACKs and adverts, and there is
no way for the host to say "this page fetch may yield to anything else". A
two-or-three-class scheduler (interactive / bulk / background) plus an
app-visible remaining-airtime figure would fix it.

**3.3 Duty-cycle accounting (corrected 2026-09-19, r5 built).** The original
claim here was wrong: the dispatcher has enforced a token-bucket duty cycle
since upstream PR #1297 (`tx_budget_ms`, `getAirtimeBudgetFactor()`), and
`total_air_time`/`rx_air_time` are exposed in the radio stats. What was missing
is (a) any *relay-specific* accounting, (b) any way to cap the relay role
separately, and (c) a strict rolling-window guarantee - a token bucket
accumulates credit and can be drawn down in a burst, then refills, so it is a
pacing mechanism, not "no more than X% of any window". A 1% band still makes a
bulk transfer illegal without (c). The r5 patch adds all three: a
rolling-window `AirtimeBudget`, forward-path admission and accounting in the
dispatcher, `set fwd.dutycycle` (pref `fwd_af`; upstream default off, the
Lantern build defaults to a 10% relay cap), and relay figures in `stats-radio`.
Built 2026-09-19; upstream PR meshcore-dev/MeshCore#3450;
Lantern build record `work/mesh-repeat-sf-20260919/README.md`.

### Tier 2 - resilience and scale

**3.4 Single rate for the whole mesh.** Radio parameters are deployment-wide
(`CMD_SET_RADIO_PARAMS` per node, `MyMesh.cpp:1469`), so "fast lane for bulk,
narrow lane for range" is not expressible. Either the mesh gains a negotiated
second profile for bulk windows, or Lantern carries a second radio for the bulk
path. Those are the only two honest options; time-slicing the whole mesh between
profiles would break flood routing for every node mid-conversation.

**3.5 Nothing survives an absent recipient.** The offline queue serves a
disconnected *app*, not an unreachable *peer*: 16 slots, and direct messages are
never evicted, so a full queue of DMs silently drops new ones. Lantern's ICQ
semantics (durable local row, retry until the peer is reachable) are currently
entirely host-side. A peer mailbox on a repeater/room server is the natural core
feature, and it is the difference between "delivered eventually" and "delivered
when both units happen to be awake".

**3.6 `RAW_CUSTOM` is direct-only.** Repeaters deliberately do not flood it
(`src/Mesh.cpp:292-296`). So a custom Lantern frame cannot cross a relay, which
means our own envelope format cannot be used for anything multi-hop - we are
stuck expressing ourselves as chat text for exactly the traffic that most needs
its own format.

**3.7 Transport crypto is weak by modern standards.** Direct messages use a
2-byte MAC with 1-byte source/destination hashes (`CIPHER_MAC_SIZE 2`,
`PAYLOAD_VER_1` in `src/Packet.h`), and there is no ratchet. Our envelopes are
end-to-end signed, so Lantern's own confidentiality does not depend on this - but
anything the mesh relays is only as private as the layer underneath it, and
`PAYLOAD_VER_2` (2-byte hashes, larger MAC) has been "FUTURE" for a while.

**3.8 The host link has no frame checksum.** A corrupted or unexpected frame is
caught structurally, if at all. The contacts-response interleaving we just fixed
(PR #3403) is the same class of problem: a host has to infer protocol state from
frame codes rather than being told. A CRC and an explicit "response in progress"
notion on the companion protocol would remove a whole family of bugs.

Our own client has the mirror-image defect: it drops push frames that arrive
during a command exchange (1.1). Nothing measured so far has been traced to it,
and the runtime polls around it, so it is a hole rather than a live failure -
but it is ours, it is small, and it should close before anything is built on
push-driven wakeups.

### Tier 3 - long term

**3.9 Packet RSSI/SNR is not upstream.** Our units carry a private patch for it
(`firmware/lantern-meshcore/packet-rssi.patch`). Per-packet signal is table stakes
for a deployed network and for diagnosis; it belongs upstream.

**3.10 No firmware update path over the host link.** Field units are Linux hosts
attached to a radio, which is the ideal place to stage a firmware image - but
there is no such command, so a fielded unit's radio can only be updated by
physically reaching it. For a fleet this is a delivery blocker, and it is also
the scariest feature on the list: it must be signed, staged, and rollback-able
from day one, not bolted on.

**3.11 Diagnostics are thin.** `STATS_TYPE_CORE/RADIO/PACKETS`
(`MyMesh.cpp:71-73`) do not expose CAD failures, retry counts, airtime used, or
queue drops - the exact numbers needed to debug a flaky link from the host.

## 4. Starlink uplink and desktop-mode web: what it actually needs

The shape of the feature: one Lantern (or a dedicated box) has Starlink; it acts
as the community's uplink; every other Lantern reaches it over LoRa and asks for
pages. At 122 B/s the design falls out almost arithmetically.

* **Server side is a transcoder, not a proxy.** HTML never crosses the radio.
  The uplink node fetches, then renders to a compact Lantern page schema
  (title, text, links, no layout), compresses it, and keeps it cached with an
  ETag so a revisit costs a header rather than a page.
* **Request/response with deltas.** Because the link is half-duplex and one
  frame is 1.05 s, the protocol must pipeline (request N+1 while N renders) and
  support "page changed by X" deltas. This is the part that needs the Tier 1
  bulk primitive; over chat text it is possible but wasteful.
* **The cache is the product.** A household shares one cache; the second person
  to look at the same page pays nothing. This is also how the design survives
  the airtime budget.
* **Priority classes are mandatory.** A 100 KB fetch must not delay a message
  ACK, so Tier 1 must ship with Tier 2.
* **A fast lane makes it feel real.** SF7/BW500 (2,449 B/s, ~2x slower than a
  56k modem) turns a 10 KB page from 84 s into 4 s - but only when the peer is
  close, which for a village gateway is usually true. Without a second rate
  (3.4) "desktop mode" is a text reader with a 25 s page turn.

## 5. What not to expect

Images, video, JavaScript, and anything interactive. A single 3 KB text page
costs 25 s of a shared channel today. "Surfing" here means: type or pick a link,
wait, read, and let the cache pay for itself on the second visit. Any product
claim that implies otherwise is a claim the physics does not support, whatever
the firmware does.

## 6. Recommended next three

0. **Stop our client dropping push frames** (3.8) - a few lines in
   `meshcore_transport._exchange`. Cheap insurance, not a fire: the runtime polls
   around the pushes it currently loses.
1. **Bulk transfer primitive in the core** (3.1) - fragmented, windowed,
   selectively retransmitted, with reassembly. Do it with priority classes (3.2)
   or it will be unusable the first time it overlaps a message.
2. **Rate negotiation or a second radio** (3.4) - now a measured decision rather
   than an arithmetic one: the fast lane is 4.7x on confirmed round trip (1,432
   ms to 307 ms, 89 to 417 B/s) with no measurable SNR cost at 1 m, but its range
   penalty is still unmeasured and desktop mode depends on it.
3. **Airtime accounting plus upstream RSSI/SNR** (3.3, 3.9) - the first is a
   shipping gate for regulated bands, the second is what makes every future
   radio problem debuggable from the host instead of from a serial console.

Nothing here is committed work; this is the analysis. Items 1-3 are the ones
worth taking upstream, and 3.9 already exists in our tree as a patch.

## 7. Update 2026-09-18 - two items are now built in our layer

**Push frames are no longer dropped (6.0, 3.8).** `MeshCoreClient._observe_push`
now records every frame >= 0x80 it reads inside a command exchange: bounded,
payload-free summaries in `push_events`, an explicit `take_messages_waiting()`
latch for 0x83, `take_contact_table_dirty()` for 0x8F/0x90, and the 0x82 send
confirmation (ACK CRC + trip time) kept as a radio-level observation that is
still not delivery evidence. Nothing consumes the latches for wakeups yet - the
runtime still polls - but the events now exist instead of disappearing.

**Duty-cycle pacing exists on the host (3.3, corrected).** The firmware has
always counted its own TX airtime (`Dispatcher.total_air_time`), the companion
exposes it in STATS_TYPE_RADIO, and `health()` already parsed it as
`tx_airtime_seconds`; what was missing was a policy. `airtime_budget.py` now
provides a fixed-window ledger fed by that counter, and `MeshRuntime` gates its
background dispatch paths through it when the optional `airtime` block is
present in mesh-runtime.json. It is a pacing gate, not a certification: the
counter is floor-quantised to seconds, the windows are fixed, and one-shot
dispatch routes are not gated yet. With no `airtime` block the runtime behaves
exactly as before.

Evidence: `software/runtime/tests/test_airtime_budget.py`,
`test_mesh_airtime_dispatch.py`, and the push tests in
`test_meshcore_transport.py`; handoff notes in
`work/push-capture-airtime-20260918/README.md`.
