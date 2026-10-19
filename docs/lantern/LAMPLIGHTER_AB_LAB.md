# A/B Lamplighter lab

A/B are single-LoRa Lamplighter/admin/public-knowledge/repeater support units
with attached dashboard screens, a remote web dashboard and SSH administration
for the Orange Pi 5 Plus C–F user Lanterns. Current A/B/C radio qualification uses
one LoRa radio per device; a second radio is optional.
[Device roles](DEVICE_ROLES.md) is the canonical allocation. A is the initial public
pack seed and B is a receiving/re-seeding peer; either can serve verified content.
Neither carrier is a publisher, personal Root, public validator or recipient's
private SAGE. Four Lanterns are not a four-validator consensus test.

`scripts/lantern/lamplighter.py` provides a bounded resource lab using the
existing signed knowledge-pack-v2 format and an HTTP/IP adapter. It is BitTorrent-style multi-source
retrieval, not the BitTorrent wire protocol. There is no external tracker/DHT,
Internet fetch, upload API, directory listing or arbitrary file-serving route.
Only explicitly loaded, verified pack objects are available.

The client needs an independently supplied manifest digest and trust root. It
checks the publisher signature, canonical manifest, content hashes, metadata,
quotas and completed pack rules. Catalogue announcements are untrusted discovery.
A carrier cannot authorize a new publisher or trust root. Downloads remain in a
public file cache and never automatically enter personal SAGE or Commons state.
The lab rejects embedded vectors, whose bytes need separate authentication.

Transfer batches contain at most 32 chunks. Completed chunks are durably stored,
rechecked on resume and reused; damaged chunks are re-fetched. A failed or corrupt
peer is excluded for that transfer, preventing one timeout per remaining chunk.
A later explicit resume can reconsider it. The local verification report is not
a signed recipient receipt or proof of SAGE ingestion.

The 9 October source increment replaces round-robin source rotation with
estimated completion time based on locally verified delivery. Initial and later
requests use the adapter's same bounded batch size, up to 32 pieces, with at most
one batch per source and four concurrent paths by default. Sources that share an
explicit bottleneck resource do not execute concurrently. A stale observation
returns to the local bootstrap estimate; route rebinding needs a fresh source.
The HTTP default names each peer separately; an integrator must supply shared
resource IDs for paths that contend for the same radio or interface.
The selector can try an unmeasured alternative within a finite probe allowance:
at most one second of predicted work and 10% of the initially predicted transfer
time. Probes carry needed pieces, not duplicates, and still require the carrier's
normal admission and budget. This is an estimate, not an airtime grant or proof
that every selected route is globally optimal. A small transfer need not spend
more time testing alternatives than delivering its content.

Compact availability bitmaps identify cached pieces. Availability is an
untrusted hint and never supplies a URL, publisher key or measured rate. Rare
pieces are considered first. Failed sources are tried at most once per transfer;
bounded in-flight requests drain on cancellation and verified chunks survive.
The older complete-pack HTTP server remains compatible through an availability
404 fallback. Completed caches can be verified offline against the pinned
manifest and independently supplied trust root.

## Operator use

Run with the matched Python environment containing `cryptography`, alongside
`knowledge_pack.py`, `meshticulum_swarm.py` and the reviewed public
`pack-trust-root.json`. Non-HTTP adapters can also use `meshticulum_resource.py`:

```sh
python lamplighter.py --trust-root pack-trust-root.json fetch \
  --peer http://192.168.50.103:18090 \
  --peer http://192.168.50.96:18090 \
  --manifest 9ec8c8351846383c1fe082d66e1667197be6a128991c9561bb5db87fe64a4a3b \
  --destination /path/to/public-cache/world-facts-lite-v3
```

The peer addresses above are the current private bench LAN. Replace them with
explicit local IPv4 peers when moving the lab. The trust root is provisioned
independently, never accepted from a peer's catalogue. `--max-chunks 128` creates
a deliberately partial checkpoint; repeat without it to finish. A verified
complete directory can be served with `serve --pack DIR --bind IP --port 18090`.
A complete or partial download can instead use
`serve-cache --pack DIR --manifest DIGEST --bind IP --port 18090`. This endpoint
rechecks piece hashes before advertising or serving them and sees newly
verified pieces without a restart. It serves no cache misses, fetches no
upstream content and exposes no files outside the pinned pack. `--timeout`
bounds the entire fetch; `--max-workers` bounds concurrent path requests.

For a non-HTTP path, call `fetch_sources` with locally constructed
`meshticulum_swarm.Source` adapters. Each reader accepts
`(resource_path, byte_limit, absolute_monotonic_deadline)` and must enforce
that deadline and its own live admission/budgets. `BlockReader(exchange)` and
`BlockServer(verified_cache.read)` provide a compact bounded request/reply codec.
Raw piece blocks cross the carrier, with JSON/base64 batch conversion confined
to the host adapter. This is an adapter seam, not an installed LoRa service;
the real radio flow/broker still needs explicit integration and qualification.

On A/B, `sage-lamplighter-seed.service` is a dedicated dynamic-user service with
read-only files, no devices/capabilities, denied access to private SAGE storage,
a 256 MiB limit and access limited to the bench subnet. The role marker under
`/etc/sage-lamplighter-lab/` inhibits retired endpoint services. These are lab
configuration changes, not an updated C–F appliance release. Both native personal
SAGE stores were deleted with explicit approval, including test vault/agent keys.
Both seeds passed service restart and controlled device reboot verification after deletion. The old endpoint Fleet
Admin UI is stopped. Scoped SSH administration is now installed and verified;
RF repeater forwarding and a broader fleet web interface remain open. Both seeds
also passed a controlled whole-device reboot. See [operator commands](AB_OPERATOR_RUNBOOK.md).

## Evidence and remaining work

Live WiFi tests verified the signed 5,686-chunk world-facts-lite v3 pack:
B resumed after 128 chunks, downloaded the remaining 5,558, then served as a
second seed. A fresh transfer used both peers. With A's seed stopped, a complete
transfer from B succeeded and A's seed was restored. See the exact receipts and
service/firmware status in `work/ab-support-lab-20260927/README.md`.

The 9 October source checks cover route costs and shared links, signatures,
corrupt blocks/batches/cache, source failure, partial re-seeding, adapter changes,
offline resume and total deadlines under trickle traffic. The final client also
fetched a 128-piece checkpoint from A, then verified the remaining 5,558 pieces
using both A (1,216) and B (4,342) in 12.91 seconds. This is one
bench-LAN application observation including validation and disk writes, not
maximum WiFi throughput or a comparison with the historical device-to-device
timings. Receipts and exact source hashes are in
`work/meshticulum-mixed-transports-20261009/live-lan-03/`; the earlier source
variants and their different timings remain in `live-lan-01/` and `live-lan-02/`.
No device software or radio state was changed by these read-only checks.

LoRa discovery, compact manifest offers, small/delta pack delivery, interactive
priority, radio airtime budgets and route integration remain separate work.
Fast LoRa is an explicitly measured radio profile, not assumed WiFi speed.
The initial seed service cannot operate a radio. USB firmware qualification
alone does not establish LoRa pack delivery. Current C qualification uses a raw
SPI SX1262 HAT; A/B use USB MCU firmware. The new resource layer still needs
those actual radio-owner bindings, physical mixed-path tests and user-facing
download/verification controls before deployment acceptance.
