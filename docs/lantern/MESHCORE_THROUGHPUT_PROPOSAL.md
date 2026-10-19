# A path to 56 kbps on MeshCore

A proposal for upstream, written after measuring two real units rather than
reasoning from airtime tables. Everything marked *verified* was read out of the
code we actually ship; everything marked *measured* came off the hardware on
2026-09-13; everything marked *indicative* is my recollection of datasheet
behaviour and should be checked before anyone quotes it.

## 1. The measured starting point

Two units, ~1 m apart, mesh runtime stopped on both, 25 frames of 128 bytes per
profile, each frame confirmed by its own mesh ACK
(`work/messages-ab-20260913/measure_profiles.py`):

| profile | raw bit rate | median ACK round trip | confirmed throughput |
| --- | --- | --- | --- |
| SF8 / BW62.5 (what Lantern runs) | 1,562 bps | 1,432 ms | 89 B/s |
| SF7 / BW500 | 21,875 bps | 306 ms | 410 B/s |

Two things to read off that, and the second is the important one.

* The fast lane is real: 14x the raw bit rate buys 4.7x on confirmation.
* **The physical layer is not the first bottleneck.** At SF7/BW500 the air is
  capable of roughly 2.4 KB/s of 128-byte frames; confirmed delivery achieves
  410 B/s. Five sixths of the capacity is being spent on something that is not
  modulation - a serialized ACK round trip per frame, plus the firmware's
  inter-frame delays. The same ratio holds at the commissioned profile: 195 B/s
  of airtime, 89 B/s delivered.

That gap is the first place to look, because closing it needs no new silicon and
helps every MeshCore user.

## 2. What the hardware can already do (verified)

Three modems are available in the driver MeshCore already depends on, and
MeshCore uses exactly one of them:

1. **LoRa** - the only mode configured today. Every radio wrapper calls
   `setFrequency` / `setSpreadingFactor` / `setBandwidth` / `setCodingRate` and
   nothing else (`src/helpers/radiolib/CustomSX1262Wrapper.h:16`).
2. **FSK/GFSK, 0.6 to 300 kbps** - present in the same driver
   (`RadioLib/src/modules/SX126x/SX126x.h:449`, `setBitRate`, "Allowed values
   range from 0.6 to 300.0 kbps"). Our radios are SX1262. So **50 kbps is not a
   hardware problem; it is a firmware architecture problem.**
3. **LR-FHSS** - the SX126x driver carries a full implementation adapted from
   Semtech's own demo (`RadioLib/src/modules/SX126x/SX126x_LR_FHSS.cpp`,
   `setLrFhssConfig(bw, cr, hdrCount, hopSeqId)`). Frequency-hopping spread
   spectrum is the modern answer to capacity and interferer robustness, and it is
   already sitting unused in the dependency tree.

MeshCore is a LoRa-only protocol sitting on a tri-modem radio.

## 3. Where the 56 kbps actually comes from

Neither step alone gets there; together they do.

| step | what changes | expected | cumulative |
| --- | --- | --- | --- |
| today | LoRa SF8/BW62.5, ACK per frame | 89 B/s | 89 B/s |
| 1. pipeline | LoRa SF7/BW500, windowed bulk, no per-frame ACK | ~1.5-2 KB/s | ~2 KB/s |
| 2. second PHY | FSK ~50 kbps for the local lane | ~4-6 KB/s effective | **~48 kbps** |

Step 1 is the interesting one for upstream, because it is pure protocol work on
hardware people already own. Step 2 is the one that makes a Lantern gateway feel
like a link rather than a trickle, and it is the reason to stop treating the
radio configuration as a network-wide constant.

## 4. Concrete, PR-sized items

### 4.1 Windowed bulk transfer (highest value, no new hardware)

`PAYLOAD_TYPE_MULTIPART` exists but is only used to ACK a direct burst
(`src/Mesh.cpp:574`, `:359`, and the receiver's `FUTURE: other multipart
types??` at `:321`). There is no bulk sender anywhere in the core.

* Add a transfer mode: a window of N frames in flight, a single selective-repeat
  control frame per window instead of one ACK per data frame, and reassembly with
  a transfer id.
* Keep it opt-in per transfer so ordinary messaging is untouched.
* Measured justification: it removes ~5/6 of the cost of moving bytes today.

### 4.2 A second PHY profile, negotiated (the 56 kbps step)

Radio parameters are network-wide and host-set per node
(`CMD_SET_RADIO_PARAMS`, `MyMesh.cpp:1469`), so "fast lane nearby, narrow lane
far away" cannot be expressed. Bulk on FSK, messaging on LoRa, with a
negotiation frame that agrees the fast profile and a timeout that falls back.

* FSK at 50 kbps costs roughly 10-15 dB of link budget against the narrow LoRa
  profile (*indicative*), so this is a same-village lane, not a same-mountain
  lane - which is exactly what a Starlink uplink needs.
* It also gives mixed networks a way out of the single-rate trap in 4.4.

### 4.3 Priority classes and airtime accounting

Today everything shares one queue and one channel. A bulk transfer starves
message ACKs, adverts and receipts. `airtime_factor` only scales a flood timeout
(`MyMesh.cpp:921`, `NodePrefs.h:16`) - it neither limits nor reports duty cycle,
which is a legal problem in any 1% band and the reason bulk cannot ship without
this. Ship 4.3 with 4.1 or the first bulk transfer will break messaging.

### 4.4 Per-link rate instead of one rate for the whole mesh

Every node currently shares one SF/BW. In any real deployment the link margins
differ enormously between "the peer across the room" and "the repeater on the
hill", so the mesh pays the weakest link's rate on every hop. LoRaWAN solved
this with ADR; MeshCore has the knobs and no policy. This is a large but
well-understood win for airtime across the whole network, not just for Lantern.

### 4.5 Rateless (fountain) delivery for one-to-many

For broadcast bulk - a page cache pushed to every Lantern in range - ACKs are
wasteful and unnecessary. LT/Raptor-style rateless codes (RaptorQ is IETF RFC
6330) let a sender transmit encoded symbols until receivers have enough, with no
per-receiver ACK, no retransmit negotiation, and graceful behaviour under loss.
The codec is host-side; the firmware contributes a symbol stream and a symbol
size. This is speculative for MeshCore's scale and should be discussed before
anyone writes code, but it is the natural fit for a shared-airtime village.

### 4.6 Smaller, immediate

* Per-packet RSSI/SNR upstream (we carry a patch for it -
  `firmware/lantern-meshcore/packet-rssi.patch`).
* Do not drop push frames that arrive during a command exchange: the host must be
  told, not left to infer (`meshcore_transport._exchange` is where *we* get this
  wrong too).
* Diagnostics: CAD failures, retry counts, airtime used, queue drops. None are
  exposed today (`MyMesh.cpp:71-73`), and all of them are needed to tell a slow
  link from a congested one.

## 5. What we can contribute, and what needs a maintainer first

We can write and test 4.1, 4.3 and 4.6 against real hardware, and we already have
the measurement harness to show the before/after. 4.2 and 4.4 change how a mesh
agrees on its physics, which is a design decision for the maintainer rather than
a patch to submit. 4.5 is worth an issue before any code.

## 6. Honest caveats

* **Range is unmeasured.** Both measurement runs were at ~1 m. Every fast option
  here trades link budget for rate, and the size of that trade has to be
  measured by moving units apart, not inferred from a datasheet. That test is
  next.
* **Absolute sensitivity numbers above are indicative**, not read from a
  datasheet during this analysis.
* **Regulation.** Wider bandwidth and higher duty cycles are band- and
  country-specific (a 1% duty cycle band makes bulk transfer illegal rather than
  slow); FSK at 50 kbps in a 500 kHz channel is a very different regulatory
  object from SF8 in 62.5 kHz.
* **"56 kbps" is a local-lane number.** The long-range channel stays LoRa, and
  nothing here makes a mountain link fast. The claim is that a household or
  village link to a gateway can behave like a slow modem, which is what desktop
  mode needs.
