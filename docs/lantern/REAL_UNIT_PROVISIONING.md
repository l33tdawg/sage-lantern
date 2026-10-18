# Provisioning the production units (Orange Pi 5 Plus 16 GB / 256 GB NVMe)

**Planning state, 27 September 2026:** C, D, E and F are the first production
builds: Orange Pi 5 Plus 16 GB with 256 GB NVMe, dual LoRa, GPS and WiFi via
the Orange Pi. The owner is unboxing; first-boot IP/SSH is still pending. Parcel delivery does not
establish inspection, assembly or electrical readiness. Six C–H units are the
December target; Flint owns the current hardware BOM and build status.

A and B are single-LoRa ROCK 5B-family 8 GB support units: Lamplighter, admin,
public knowledge delivery and repeater testing. Their native personal SAGE stores
have been wiped and old endpoint services retired. Their public WiFi pack seeds
and the A/B pack dashboard (local screens and remote browser) are running; RF repeater and LoRa pack integration remain open. User-facing model,
UI and end-to-end delivery acceptance belongs on C–F. See the authoritative
[device-role map](DEVICE_ROLES.md).

Start with the read-only [C–F first-boot inventory](CF_FIRST_BOOT.md). The old
Radxa NVMe migration procedure is not an Orange Pi installer. Fresh provisioning
requires a reviewed Orange Pi image, exact disk/peripheral identities and the
complete per-unit release; do not clone A/B's live private state.

## What 16 GB / 256 GB changes

| | Historical A/B endpoint measurements (8 GB) | C–F user devices (16 GB) |
|---|---|---|
| primary chat model | 2B on NPU; CPU 4B for vision/fallback | Existing 4B RKLLM candidate needs board qualification |
| verifier | Background CPU 4B, ~7 s in recorded tests | Measure full-service memory and latency before adding a judge |
| embeddings | Local nomic with Ollama shim | Recheck alongside the production service stack |
| packs | A/B storage and recall evidence | Measure usable 256 GB capacity and bound recall candidates separately |
| memory/swap | Recorded A/B runs were RAM constrained | Installed RAM is not free headroom; record resident load and swap |

## Model qualification after a working baseline

1. **Qualify the existing stage-5 4B RKLLM W8A8 export.** It already exists in
   the training workspace at
   `exports/rkllm-native5-4b-w8a8-opt0-8s-32g/lantern-native5-4b-w8a8-opt0-rk3588.rkllm`
   (5,535,253,076 bytes; SHA-256
   `e5d4221dff6494f917fd22ff37a35673b30d8497c2a33295615873e7b8303f96`).
   Export success is not device acceptance. On a cooled 16 GB board, use a spare
   port with production services resident and record load time, memory peak,
   first-token/total latency and thermals. Score the frozen answer and factual
   sets before choosing 4B-only versus 2B plus a verifier. Do not assume 2B-class
   latency or re-export merely to start this test.
2. **Judge model.** First measure a calibrated verdict from the existing model.
   Add a helper only if its accuracy, ECE and flip-rate improvement justifies
   measured memory/latency cost. Keep verification outside the first-answer path.

These experiments do not block preparing the measured 2B NPU / CPU 4B baseline.
The fresh held-out answer corpus and remaining 2B fine-tuning/quality gates are
still open; the historical 60-case set alone does not close them.

## Software gates before installation

- Fresh unit bindings must agree across provisioning, protected configuration,
  runtime, display and release checks. Accepting `unit-c` at one CLI is not enough.
- The canonical profile now includes pinned NPU engine/server source, vendor
  runtime, 2B model, CPU 4B vision and local embedding services. The assembled
  candidate passed manifest verification and C–F plan-only checks; actual
  Orange Pi acceptance remains pending;
  source packaging alone does not establish driver compatibility or inference.
- Flint's 27 September reply identifies an I2S microphone (not yet ordered)
  and CSI IMX519 camera. Exact carrier, connector, pin mapping and drivers remain
  unverified. Existing USB serial/audio and `/dev/v4l/by-id` bindings do not
  qualify I2S or CSI hardware.
- The proposed XIAO ESP32S3/Wio-SX1262 radio needs its own image, firmware
  admission, USB identity and bench commissioning. See the
  [candidate recipe](../../firmware/lantern-meshcore/xiao-s3-wio/README.md).
  Its partition map differs from the Heltec; do not use the A/B flash procedure
  unchanged. Flint's radio order records conflict and are being reconciled;
  this is not confirmation that the XIAO replacement was purchased. The GNSS
  antenna is separate from a verified GNSS host service.

## Provisioning sequence (per unit)

1. **Base image and fresh identity** after the inventory and software gates:
   device lock, vault, private listeners and fleet-admin UI. Generate each unit's
   SAGE, Root, radio and host identities locally. Do not copy live homes, vaults,
   message journals, nonces, host keys or machine identities from another unit.
2. **Models**: Unit C's production chat is the V79.6 3B token lane on loopback
   port 8081, with the 2B prompt-string service retained as rollback. The NPU
   vision lane uses the Qwen3.5-0.8B pair on 8082; the CPU 4B vision fallback
   is bench-only and must not share 8082 with the vision unit. Add
   `nomic-embed-text-v1.5.Q8_0.gguf` (146 MB). C is the only unit with this
   production switch; D–F remain on their independently accepted profile.
3. **Answer runtime**: install the runtime wheel (complete answers + follow-up
   parsing + echo guard + verifier); set `llm.experimental_answer_suggestions`
   and `llm.verify_answers_background` in `lantern.toml`. Follow-up questions
   come from the in-band `<followups>` trailer when the model writes one, and
   otherwise from one bounded second request (64 tokens, 4 s) that runs while the
   answer is being spoken — a small model drops the trailer on its shortest
   answers, which is exactly when the bubbles are wanted.
   The wheel is the unit of deployment (D-023): it is built from the whole of
   `software/runtime`, so an update ships whatever that tree holds at build
   time, including work in progress from other lanes. Review what a deployment
   changes by reading the wheel contents, not the commit range that motivated
   it; the commission gate in step 6 is what catches an unexpected runtime byte.
4. **Embeddings**: the canonical base installer stages llama.cpp embedding
   service on :8083 and the Ollama-compatible shim on :8084. Review the fresh
   [`sage-personal.example.yaml`](../../software/config/sage-personal.example.yaml)
   before independent identity initialization (`provider: ollama`, nomic, 768).
   Verify actual semantic retrieval after startup. Historical ad-hoc embedding
   patch scripts are not the fresh-node installation path.
   *A node restart can lock the vault. Use the reviewed lifecycle and fresh
   owner session; diagnose `storage_supervisor_unavailable` before recovery.
   A refusal alone is not authority to discard a fence or unknown work.*
5. **Packs**: build with `scripts/lantern/build-knowledge-pack.py <source.jsonl|.md>`
   (`--archive` for the GitHub release asset) and install with
   `scripts/lantern/install-knowledge-pack.py <pack-dir|.tar.gz>` after
   `--verify`; then `--check` for coverage, and verify that the probe questions
   rank the exact fact #1. Verification resolves against
   `scripts/lantern/pack-trust-root.json`, so the Foundation pack key has to be in
   that file on the unit before a signed pack is accepted.
   `ingest-knowledge-pack.py` is the pre-v2 flat-JSONL path and stays only for the
   historical test-unit workflow; A/B native personal stores are now deleted.
   *Use an approved curated/public domain with verified retrieval scoping; do
   not reproduce A/B's provisional home-domain ingest on fresh units by default.
   A dated brief uses a bounded window (installer default/maximum 31 days), with
   date/source wording and measured recall competition. Curated packs are not
   Commons Snapshot Packs or consensus proofs.*
6. **Audio commission** after a change to any of the four capture-contract files
   — `appliance-bindings.json`, `audio-capture-alsa.conf`, `lantern.toml` and
   `/usr/bin/arecord`. It is **not** owed for every runtime byte: the installed
   package and the voice loop are recorded in the receipt for audit but
   deliberately excluded from the contract check (D-018/D-019, see D-023). Then
   cold start and **first release pin** (`release.py` two-pass, verify all six
   baseline fields).
7. **Acceptance on C before D–F**: factual and answer quality, streamed typed
   and spoken questions, actual touch/audio/camera behavior, lock/sleep/wake,
   unique public identities and cross-unit messages with durable receiver
   records and signed delivery/read receipts. Queue success or a radio ACK is
   not end-to-end delivery. Retain software and physical acceptance separately.

## Pack shape for torrent-style delivery

Implemented as a prototype in [KNOWLEDGE_PACK_V2.md](KNOWLEDGE_PACK_V2.md):
content-addressed chunks (`{id, title, text, tags, source, date, sha256}`, plus an
optional embedding that is deliberately outside the content address) behind a
signed manifest carrying pack id, version, chunk list, totals, licence and
provenance. Devices seed what they hold; updates ship as new versions plus
per-chunk deltas, not full re-downloads. Size classes: practical/regional packs
10–100 MB work today, a dense encyclopedia subset 1–10 GB needs the sharded chunk
store and chunk-index tree that the note lists as not yet built. The format is
explicitly non-normative until `OPEN-DST-001` (canonical encoding, chunking,
compression, POUF) is decided.

## Also open

* **Public domain for packs** needs current node-policy verification. The
  September 20 vendored-agent probes returned 403 on domain creation/grants;
  this is historical evidence, not a current-node capability check. Defer fresh
  ingest until the intended domain is authorized; migrate A/B's old copies with
  verified coverage before retirement.
* Cores/thermal budget for the embedding server plus judge on the same board.
