# Meshticulum mixed transports and swarm distribution

## Current direction — owner-stated 2026-10-09

Meshticulum is Lantern's network over interchangeable carriers. LoRa, WiFi/IP,
Ethernet and later qualified adapters belong beneath the same logical peer and
service identities. A route may be `C —LoRa→ A —WiFi→ B`; devices and successive
hops need not use the same medium. MeshCore, Reticulum and BitTorrent wire
interoperability are not prerequisites. Adding a supported adapter must not
require rewriting applications or acquiring a second LoRa radio.

Choose an eligible path by predicted completion time from **local verified
delivery measurements**, queueing, loss and shared-link costs. A fast final hop
does not erase an earlier LoRa bottleneck. A busy fast path can still finish
sooner than an idle slow one. Route or authority expiry, consent and service
scope constrain eligibility before speed is considered. Slow links should not
receive unnecessary bulk copies or fast-link discovery traffic.

Borrow the useful swarm mechanisms: a pinned signed manifest, independently
verified content-addressed pieces, bounded availability bitmaps, multiple
sources, rare-piece preference, resumable verified caches and re-seeding.
Different pieces can use independent paths concurrently. Switch paths at
verified-piece boundaries with fresh carrier admission; never carry an old
wire session, nonce state or expired request authority onto another route.
Interactive messages and receipts retain their priority and actual airtime
accounting underneath the bulk transfer layer. Public seeding grants no
authority to read or import private SAGE data.

Mixed-carrier routing and verified resources are **core requirements**. General
IP/TUN service, per-packet striping of one ordered stream, a second LoRa radio,
and actual RNS compatibility remain separate work. One qualified LoRa radio
plus whatever faster link exists is the baseline. The completed A/B/C bench
already exceeded 1,000 B/s in 29 of 30 repeated single-LoRa transfers; one
retransmission sample was slower. That finite bench establishes neither a
minimum-throughput guarantee nor mixed-carrier deployment.

### Privacy and authenticated transport — owner requirement 2026-10-09

Private messages must remain encrypted from the originating Lantern to the
intended recipient across every carrier and relay. Relays and public seed nodes
must not receive the recipient's decryption keys or private plaintext. Retain
the existing recipient-sealed, sender-signed message envelope above Meshticulum,
independently pinned peer identities, consent, expiry and durable replay checks.
A faster route must never introduce a plaintext fallback, accept a replacement
identity from a route advertisement, or re-seal private content for a relay.

The deployed finite A/B/C bench already encrypts and authenticates radio DATA,
ACK and control frames with ChaCha20-Poly1305 and independently provisioned
pairwise session keys. Keys are direction-specific and bound to the full peer,
session, route/profile, limits and expiry context. Transmit counters are reserved
durably before encryption and replay state is committed before plaintext is
returned. Encryption happens on the host before bytes reach the radio worker.
This is not a shared default network password or over-the-air identity enrollment.

That bench's object relay decrypts and verifies its payload at each hop. It
therefore proves hop protection, not private end-to-end delivery through an
untrusted relay. The existing Lantern message-envelope codec supplies the
recipient-only encryption and sender signature, but the new Meshticulum resource
adapters are not yet wired to that private-message path. Their HTTP lab serves
approved public packs; publisher signatures supply integrity, not confidentiality.
The compact binary codec supplies no encryption or peer authentication itself.

Before private traffic uses mixed paths, qualification must show that a relay
with both adjacent link keys still cannot open or forge the recipient's message;
wrong peers, tampering, replay, stale authority and carrier changes must fail
closed without plaintext delivery. Keep private custody opaque and separate
from public pack caches, availability announcements and diagnostics. Validate
key provisioning, revocation and rotation; no forward-secrecy or ratcheting claim
is established by the current provisioned bench keys or message codec.

Content encryption does not hide the current radio header/session reference,
frame length, timing or the physical transmitter, and cannot prevent RF jamming
or a relay dropping traffic. Metadata minimization and traffic-analysis resistance
remain separate privacy work. Exact source verification and 175 local crypto,
envelope and opaque-custody checks are recorded under
`work/meshticulum-mixed-transports-20261009/security-check-20261009/`.

### Implemented increment and qualification boundary

The Lamplighter client now uses `scripts/lantern/meshticulum_swarm.py` for
measured piece scheduling, bounded concurrency, shared-resource exclusion,
source failure quarantine and rare-piece preference. `Source` is the resource
adapter boundary; it receives a byte bound and the original absolute deadline.
Its configured route can describe heterogeneous hops, but that description is
not authenticated discovery or proof of physical forwarding.

The concrete HTTP/IP adapter and partial-cache seed reuse the existing publisher
verification. `meshticulum_resource.py` supplies a compact block codec for an
already admitted request/reply carrier: raw piece bytes cross that boundary,
without HTTP or base64 batch envelopes. Its exchange callable still needs the
real exclusive radio-owner binding. The codec supplies no encryption or peer
authorization itself. Existing authenticated flow, worker and airtime rules
remain below it; signing public content is not transport authentication.

Local tests exercise mixed adapters and store/forward re-seeding. A read-only
LAN check against the existing A/B seeds verified the 5,686-piece public pack,
including checkpoint/resume and offline cache verification. These source changes
are not installed on the devices. Physical LoRa/IP switching, autonomous
authenticated route discovery, private services and whole-stream migration
remain unqualified. Exact evidence and the dated requirement overlay are in
`work/meshticulum-mixed-transports-20261009/`; the prior radio evidence remains in
`work/meshticulum-abc-deployment-20261009/BENCHMARK_REPORT.txt`.

This follows Reticulum's [interchangeable-interface model](https://reticulum.network/manual/interfaces.html)
and BitTorrent's [verified pieces and availability exchange](https://www.bittorrent.org/beps/bep_0003.html).
The implementation is our own; these references do not imply protocol or code
compatibility. BitTorrent-style duplicate endgame requests are not implemented
in this increment, since they would need explicit scarce-carrier budgeting.

## Historical direction — 2026-09-19

The record below preserves earlier proposals and measurements. Its fixed lane
hierarchy, dual-radio assumptions and throughput arithmetic are superseded by
the 2026-10-09 direction above. Its actual observations remain historical data.

Owner words: "we want it to work like bittorrent — each node is a relay thing",
"this way knowledge packs can be distributed", "internet backhaul would just
work basically — like multicast bittorrent with some elements of blockchain i.e.
bitcoin".

The following was the September direction note, not an implemented protocol.

## What this changes

The earlier plan was *one* uplink node that fetches, transcodes, caches and
serves deltas - a single seeder plus caches. The direction is stronger: every
node that holds content is a source for its neighbours. The uplink is the
original seeder, not the only server. Repeated reads get cheaper as more nodes
hold a copy, and a household that is offline still serves what it already has.

## The pieces this needs

1. **Content addressing.** A page or knowledge pack is split into chunks; each
   chunk is named by its hash; a signed manifest names the chunk list and the
   manifest root. Verification is per chunk, so no node has to trust the node it
   downloaded from - which is what makes "any node may relay" safe.
2. **Availability gossip.** Nodes advertise compact summaries ("I hold chunks
   X"). On LoRa this must be tiny and scoped (household/community) and never
   flood; on the 2.4 GHz lane it can be chattier.
3. **Chunk transport with priority classes.** The bulk+priority primitive
   (roadmap item 1) becomes the swarm's wire format, not just a downloader:
   interactive traffic pre-empts chunk traffic, and the relay duty-cycle budget
   already tells a node when it must stop forwarding.
4. **Custody for absent peers.** The store-and-forward layer (SLC1, custodian
   side built) is exactly "keep seeding for a peer that is asleep": a deposit
   is a chunk offer held on someone else's disk until the owner returns.
5. **Provenance and receipts.** Signed manifests give tamper-evidence for
   knowledge packs; the existing airtime ledger and device-signed receipts give
   the audit trail of who published, who relayed and what was fetched.

## On the blockchain element

The properties we actually want from that family - verifiable content, signed
provenance, tamper-evident history, contribution accounting - come from
content-addressed hashes, publisher signatures and an append-only local log
gossiped between known nodes. They do not require proof-of-work or a coin, and
on a radio with a measured 89 B/s budget, consensus-by-work would spend the
scarcest resource in the system on a problem we do not have: all participants
hold cryptographic identities already. Contribution fairness (a node that
relays more may ask for more) is an accounting question, not a consensus one.

## On a second LoRa radio at an offset frequency

Worth measuring later, not now. A second radio lets a node receive while it
transmits, which is the cost that dominates relay throughput today (a confirmed
128-byte frame is 1.437 s, most of it the half-duplex round trip), and it adds
its own airtime budget. Two radios on one board need real isolation: a few
hundred kHz of separation, filtering and antenna distance, or the receiver goes
deaf while the partner transmits. The cheap version of the same idea is already
on the bench: the ESP-NOW bridge companion is a second radio (2.4 GHz) with the
firmware plumbing to route mesh packets between two transports, and that
plumbing is reusable for a second SX1262 unchanged.

Three-lane hierarchy (owner-stated 2026-09-19):

1. **WiFi / ESP-NOW (2.4 GHz)** - same room or household, Mbps-class: the
   swarm's fast lane for chunks, previews and pack replication between nearby
   units. Highest speed, shortest range.
2. **LoRa, eventually two radios per unit** - the village tier. One radio keeps
   the commissioned long-range channel; the second can run a faster local
   profile (SF7/BW500 measured 417 B/s) as the LoRa-to-LoRa speed lane between
   peers in range. Two radios also mean two duty-cycle budgets and a relay path
   that no longer blocks interactive traffic.
3. **SAGE mesh / custody** - the durable layer: signed store-and-forward for
   messages and eventually the pack ledger. Not a transport for browsing.

Desktop mode is the gating context, not a separate product: web/terminal work
happens when the Lantern is docked (on its side, keyboard and mouse), so a
browsing session is a job queue - request goes out on whatever lane reaches the
uplink, the uplink renders the page, and the result returns as content-addressed
chunks the swarm then seeds. Nothing interactive assumes HTML crosses the radio.

### Desktop mode wants real TCP/IP, not a job queue (owner-stated 2026-09-19)

The docking posture is where a full browser and terminal live, over a real IP
path to the Starlink uplink: slow, but a network - the owner's frame is bonded
ISDN channels, i.e. multilink PPP. That shape needs three things:

1. **A bonding layer with a scheduler and a reorder buffer.** Fragment each IP
   packet, sequence the fragments across the available lanes, reassemble at the
   far end. Unlike MLPPP the lanes are wildly asymmetric, so the scheduler is
   priority- and rate-aware: interactive traffic and retransmits on the WiFi
   lane, bulk and background on LoRa, one sequence space across both.
2. **Split TCP / PEP at the ends.** A 1.4 s LoRa round trip kills end-to-end TCP
   congestion control. The Lantern terminates the browser's TCP locally; the
   uplink node terminates it on the internet side and ships rendered results
   back over the bonded link.
3. **Multi-hop-aware scheduling.** The uplink may be several Lanterns away, so
   fragments are scheduled hop by hop, sharing the relay duty-cycle budget and
   the custody machinery rather than assuming a single link.

Honest rate arithmetic: two SF7/BW500 LoRa lanes are ~830 B/s, roughly 15% of a
56k dialup; a WiFi/ESP-NOW lane in the path is what makes dialup-class browsing
realistic. The swarm pieces (chunk ids, verification, reorder windows, custody)
and the bonding pieces (fragment/sequence/reassemble) are the same machinery
used at two layers, so building the bulk primitive once serves both.

## What was measured on 2026-09-19

`work/espnow-20260919/` flashes the bridge companion (after fixing PR #3410,
which never started its bridge) and measures the 2.4 GHz lane against the
freshly re-measured LoRa baseline (25 x 128 B, 25/25 ACKed, 1.437 s median,
89.1 B/s). Results, and why the mirror is the wrong shape for a fast lane, are
in `work/espnow-20260919/REPORT.md`.
