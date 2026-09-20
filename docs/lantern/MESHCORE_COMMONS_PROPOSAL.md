# Commons over MeshCore — a proposal to the MeshCore community

**Status:** draft, held. Owner direction 2026-09-20: this is not for the
MeshCore community yet — it is scheduled to be proposed around **December 2026**,
ahead of the January 2027 public launch. Do not send, post or circulate it before
then; revisit it against what has actually shipped by December. Not a
specification and not an announcement. Nothing here is adopted, and the name in
the last section is the community's to accept, change or refuse.

## What we are

SAGE is building a **knowledge layer** that runs over community-owned mesh
networks: content-addressed, signed knowledge packs that any node can hold and
relay, with a household device — the SAGE Lantern — at the edge. We run MeshCore
as the radio standard, and this layer is designed to sit *above* it rather than
change it.

## What we are not

* Not a fork of MeshCore, and not a competing mesh.
* Not a new radio standard, and not a request to change how MeshCore works for
  existing users.
* Not a coin, and not consensus-by-work. On a link measured at 89 B/s confirmed,
  spending the scarcest resource in the system on proof-of-work would buy
  properties we already have: every participant holds a cryptographic identity,
  and content-addressed hashes plus publisher signatures give verifiable
  content, provenance and tamper-evident history without them.

## What we have brought so far

Everything below is measured on real hardware, not projected.

* **The measured gap.** Two units, 1 m apart: SF8/BW62.5 delivers 89 B/s
  confirmed; SF7/BW500 delivers 410 B/s. At the fast profile the air can carry
  ~2.4 KB/s of 128-byte frames, so **five sixths of capacity is not modulation**
  — it is a serialized ACK round trip per frame plus firmware inter-frame
  delays. Closing that helps every MeshCore user, which is why the proposal is
  written for upstream (`MESHCORE_THROUGHPUT_PROPOSAL.md`).
* **Upstream PRs:** #3403 contacts-response atomicity (fixes a read latch we
  measured: 25/30 contact reads failing), #3410 companion bridges + BridgePrefs
  (ESP-NOW escalation from a companion build).
* **An open design ask:** #3404, bulk transfer + priority + a second PHY,
  awaiting maintainer design input before code.
* **A relay primitive in our layer, not theirs:** store-and-forward custody
  (deposit a chunk for a peer that is asleep) and the bulk/priority wire format
  are being built as application-layer behaviour over the existing MeshCore
  framing.

## What the knowledge layer adds (and why a relay is safe)

1. **Content addressing.** A page or knowledge pack is split into chunks; each
   chunk is named by its hash, and a signed manifest names the chunk list. Any
   node that holds a chunk can serve it, because verification is per chunk — a
   relay is trusted for transport only, never for truth.
2. **Priority classes.** Interactive traffic pre-empts chunk traffic, and the
   airtime budget tells a node when it must stop forwarding.
3. **Bounded advertisements.** Availability gossip is tiny and scoped on LoRa
   (household/community), chattier only on the 2.4 GHz lane.
4. **Community gateways with backhaul.** A gateway is an internet entry point
   for a local mesh — WiFi where it exists, satellite backhaul where it doesn't
   — carrying authorised requests and compact knowledge further. It is a node on
   the network, not a change to the network.

## What stays MeshCore's

The radio standard, the protocol, the firmware and the project's governance. Our
working policy is already written down: contribute upstream when MeshCore is
missing something we need or is broken for everyone; keep SAGE-specific pieces
in the SAGE layer and do not open a PR for them.

## What we ask

* Review or discuss the bulk + priority design (#3404) and the two open PRs.
* Tell us what would make the knowledge layer a good citizen of the network:
  payload budgets, priority classes, duty-cycle accounting, anything we have
  not thought about.
* If the community ever wants a name for *the wider network* that radios like
  yours and knowledge layers like ours form together, we would offer **"MeshCore
  Commons."** It is your name, so it is yours to accept, rename or decline — we
  will not use it on releases or documentation until you decide.

## Where to look

* `docs/lantern/MESHCORE_THROUGHPUT_PROPOSAL.md` — the measured path to 56 kbps.
* `docs/lantern/SWARM_CONTENT_DISTRIBUTION.md` — the distribution direction note.
* <https://sage.ngo/commons> — the public explanation of the foundation, the
  commons, the network and the device.

Honesty note: two prototype Lanterns exist today. The knowledge layer, the
gateway backhaul and the public commons are under development, not live
services. Contact: dhillon@sage.ngo.
