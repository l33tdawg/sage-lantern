# Shared photo transport: bounded A/B proof

Read-only source audit, 2026-09-05. This is a proposed test/design, not a deployed
feature or authorization to transmit. No live operations, publication, key
creation or application changes were performed. Source may be changing in
parallel; recheck these gates against the release selected for the experiment.

## What exists, and what does not

| Component / source anchor | Actual capability | Gap or limit |
|---|---|---|
| `software/runtime/src/sage_lantern/history_media.py:1`, `:89`, `:195` | Private UUID-addressed captured-JPEG store; `put/get/delete/prune`, digest, atomic persistence, owner-only permissions. Maximum JPEG 2 MiB; configured dimensions 1280×720 or 1920×1080. | Preserves original bytes **including metadata**. Header validation is not full pixel decoding. File permissions are not encryption. Not a public export service. |
| `scripts/lantern/unit-a-voice-loop.py:298`, `:326`, `:743` | POST `/history/photo` accepts exactly `{session_id}` and returns `{image_base64}` only for a retained item with a photo. Handler applies control/header and device-lock checks. `/history/list`, `/history/settings`, `/history/delete`, `/history/clear` are separate local operations. | Retrieval is private and is not publication consent. Do not expose this API to B, transfer device session credentials, or treat history consent as sharing consent. |
| `scripts/tests/test_unit_a_photo_history_integration.py:48` | Test covers retrieval, rejecting an untrusted origin, retaining an existing photo after disabling saving, then deletion. | A source test is not new A/B delivery evidence. Opt-out and removal of existing copies are distinct operations. |
| `software/runtime/src/sage_lantern/public_queue.py:54`, `:78`, `:141` | SQLite stores canonical JSON, stable hash, ID-bound idempotency, allowed state transitions and a receipt field; same ID/different bytes rejects. | Signature validation is structural only (nonempty Ed25519 field), not cryptographic. No attachment storage, exporter, sender or authenticated receipt verifier here. `get()` omits stored manifest/receipt bytes. Queue storage is not encrypted and does not establish private modes itself; receipt updates overwrite rather than append a journal. |
| `docs/lantern/DUAL_HOME_AND_PUBLICATION_CONTRACT.md:109` | Designed signed candidate manifest includes provenance evidence hashes and optional public URI; detached signature covers canonical manifest without signature. | Contract requires `PUBLIC-CANDIDATE`; queue currently accepts only `PUBLIC`. Resolve this incompatibility before wiring Share. A queue-wide hash including signature is not the manifest's signing digest. Neither an image hash nor a URI contains a photo. |
| `software/runtime/src/sage_lantern/envelope.py:15`, `:34`, `:64` | Versioned message wrapper checks body bound (default 1024 UTF-8 bytes), source-ID shape, TTL and required fields. | Nonempty `integrity` is not signature verification. Body limit is not measured radio wire capacity. This parser alone supplies neither durable replay protection nor fragmentation/ACK/serial delivery. |
| `evidence/unit-a-radio/meshcore-radio-public.json` | Dated 2026-09-02 evidence records companion firmware v1.17.1-d929643, protocol pass and settings surviving reboot. | Explicitly records two-node RF exchange pending Unit B. USB presence and companion responsiveness do not prove a link, neighbors, delivery or usable photo throughput. |
| `docs/lantern/MESHCORE_HOST_PROTOCOL_TEST_PLAN.md:27` | Defines encoded-size sweep and adverse-case evidence. | Sweep, loss/retry, fragments and A/B results remain pending in this document. Do not infer capacity from firmware or marketing. |
| `docs/SAGE_V12_DEPENDENCY_CROSSWALK.md:89`, `:146`; `docs/lantern/PREMISE_TEST_ACCEPTANCE.md` §7 | Separates curated signed packs from deterministic committed-PUBLIC checkpoint export. | No signed knowledge-pack exporter/importer was found in reviewed runtime/scripts; frozen subset/proof API remains a documented dependency. Publisher signature is an attestation, not native inclusion proof or factual correctness. |

Private conversation outbox, public proposal outbox and fleet observation outbox
are different stores with different authority and acknowledgement semantics.
Do not repurpose private conversation submission as public photo transport.
This audit does not establish an upstream SAGE binary-attachment endpoint.

## Exact user-review boundary (proposed, not implemented)

1. From one retained interaction, create a separate **public candidate** draft.
   Show the question and answer separately, permit redaction, and explain that
   model-generated text is an inference, not a verified observation. Include
   only the exact reviewed Q/A or reviewed claim; never implicit private recall.
2. Photo inclusion is optional and initially unchecked. If selected, fully
   decode a copy, normalize orientation and re-encode pixels without EXIF, GPS,
   comments, embedded thumbnails or copied auxiliary metadata. Let the owner
   crop/redact and inspect the exact final image, including Arabic text at
   readable scale. Do not reuse the low-resolution inference thumbnail as a
   silent quality optimization. Pixels themselves can disclose people/location.
3. Preview all exported fields: reviewed text, optional derivative, attribution,
   selected locality/time, intended audience and explicit PUBLIC intent. Explain
   that recipients may retain copies and later local deletion cannot recall them.
   Retaining full photos by default does **not** authorize this export.
4. Bind explicit approval to immutable final bytes, signer, audience and manifest
   generation. Any text/image change requires new review and a new generation.
   Keep private item IDs/paths and private-source mapping only locally. Use a
   fresh export ID, not the private session identifier, on the transport.

Provisional proof limits: one JPEG ≤2 MiB, dimensions ≤1920×1080 and decoded
pixels ≤2,073,600; manifest ≤16 KiB UTF-8 including reviewed Q/A and signatures;
one active transfer with bounded timeout and retries. These are **proposed IP
test limits**, not implemented wire limits or permission to upscale a capture.
Reject oversize input visibly; do not silently shrink text evidence. Keep a
free-space reserve and independently bound encoded, decoded and response sizes.
Avoid archives and automatic URL fetching in the first importer.

The manifest must bind export ID/version, proposer public identity, reviewed
text, attribution/derivation, consent timestamp, intended lab audience, photo
MIME/byte count/dimensions/SHA-256 and signature algorithm/key/digest. Hash the
**exported derivative**, not the private original. Provide an explicit absent
photo value for text-only proposals. Verify signature against an already
authorized proposer trust mapping; mere possession of a signing key is not
public admission. Do not substitute Root, validator or fleet keys.

## Smallest achievable A/B path

**First build the missing thin export/import and receipt slice; this cannot be
claimed as an existing end-to-end feature.** Use one harmless staged photo and
reviewed Q/A, independently provisioned units, existing authorized identities,
and an isolated lab recipient. If proposer trust is not provisioned, stop at a
local unsigned preview rather than creating keys or mislabelling it signed.

1. A persists the immutable reviewed manifest plus derivative in a separate
   private export spool before showing queued. Preserve its source history item
   and public-candidate outbox. Never mount/copy the private SAGE database or
   sweep the history directory, credentials, vault or backup into an export.
2. After separate execution approval, transfer only these allowlisted artifacts
   over authenticated lab IP/Wi-Fi, or explicitly controlled removable-media
   custody. No external publication is needed to test bytes/provenance. Do not
   call this step LoRa sharing, public commitment or automatic federation.
3. B quarantines and bounds input before processing; verifies authorized signer,
   canonicalization, exact bytes/hash, dimensions/full decoding and duplicate ID.
   Missing or altered photo means incomplete/rejected, never photo received.
   Display reviewed Q/A and the actual derivative, untrusted and attributed.
4. B returns an authenticated receipt binding recipient, export ID, manifest
   digest, photo digest and persisted outcome. A records receipt without deleting
   source or candidate. Retry identical bytes after lost ACK/restart and prove
   one receiver item. A local rejected item and reason must remain inspectable.
5. Only a separately authorized public-lab phase exercises intake, fresh signed
   SAGE submission and B's public retrieval. Record intake rejection separately
   from transaction inclusion and semantic memory status; fetch actual current
   status, chain/transaction/memory IDs and observation time. Do not convert a
   receiver ACK into `COMMITTED`, or blindly retry an ambiguous submission.

Add LoRa **after** the above: measure a minimal bounded manifest reference and
authenticated acknowledgement in both directions using the host-protocol sweep.
Keep photo bytes on IP/custody initially. Digest-only radio receipt means
“reference received”; B must retrieve and verify actual image bytes before
“photo available.” No automatic peer URL fetch, full-photo radio claim, relay
claim or chain-sync claim without separate measured evidence and an implemented
transport. RF configuration and permitted operation require execution-time checks.

## Acceptance and non-claims

- Record exact software/config versions, actual bytes, derivative digest,
  signature verification, receipt identity, timing and observed outcomes. Never
  log private originals, PINs, keys, session cookies or private recall.
- Negative tests: changed text/photo, missing photo, wrong signer, same ID with
  different bytes, duplicate resend after reboot, timeout before/after persistence,
  full disk, oversized/invalid JPEG and malformed manifest. No partial activation.
- Check metadata absence on the derivative and visual readability against the
  reviewed preview. Verify no private canary/context or unselected photo exports.
- Preserve immutable candidate and media across failure/rejection; do not let
  history pruning remove an attachment still owned by the export spool. Define
  explicit local cancellation/deletion separately from remote revocation.
- A valid hash proves byte correspondence; a valid signature proves authorized
  attribution under the configured trust policy. Neither proves photo origin,
  factual truth, native consensus inclusion or semantic acceptance. A source
  checkpoint does not prove the cache contains all later corrections.

Priority gaps: (1) reviewed metadata-free derivative builder; (2) resolve
candidate schema and implement cryptographic verification; (3) durable bounded
attachment spool/export/import and authenticated receipt journal; (4) public
intake/submission reconciliation; (5) measured radio adapter. Gallery polish,
bulk packs and full-photo LoRa are not prerequisites for the first IP A/B proof.
