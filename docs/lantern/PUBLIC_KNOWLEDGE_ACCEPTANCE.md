# Share Knowledge and Accepted Public Answers — Acceptance Gate

Status: design/security review and unexecuted test plan, 2026-09-05.
Scope: existing Unit A master only. No code, device changes, key operations,
external publications, Unit B cloning, or activation authorized by this document.
Owner handoff: parent integrates; this document records blockers, not readiness.

## 1. Evidence and current blockers

Paths/line numbers below refer to the reviewed working-tree snapshot, not a
deployed build. The older dual-home contract is a design baseline, not proof
that the current SDK implements its proposed interfaces.

| Evidence | Finding / consequence |
| --- | --- |
| `software/runtime/src/sage_lantern/public_queue.py:141` | Envelope validation checks a nonempty signature string, not cryptographic validity, signer authority, consent, claim digest, or full schema. Duplicate JSON keys and unbounded extra fields are not rejected here. Do not use this as the publication security boundary. |
| `software/runtime/src/sage_lantern/public_queue.py:101` | `update_state` permits caller-supplied receipts, including no receipt; legal state progression is not authenticated acceptance. Never promote cache eligibility from this status alone. |
| `software/runtime/tests/test_public_queue.py:55` | Existing test progresses to `COMMITTED` without receipts. It proves transition ordering only, not public authenticity or factuality. |
| `software/runtime/src/sage_lantern/public_queue.py:56` | SQLite construction does not establish encryption, private permissions, owner/link safety, bounded storage, or fail-closed initialization. Unpublished candidates can contain sensitive information despite the intended PUBLIC classification. |
| `software/runtime/src/sage_lantern/public_queue.py:154` and `docs/lantern/DUAL_HOME_AND_PUBLICATION_CONTRACT.md:184` | Code accepts `PUBLIC`; design requires application `PUBLIC-CANDIDATE` with explicit versioned mapping to target SAGE classification. Resolve this mismatch before submission; do not silently reinterpret it. |
| `software/runtime/src/sage_lantern/history_media.py:12` | Private JPEG storage preserves original bytes and metadata. Its digest binds bytes, not truth; its parser does not fully decode images. Never export this blob automatically. |
| `software/runtime/src/sage_lantern/conversation_sage.py:552` | Private conversation recall explicitly excludes public/global fallback. Keep public answer acquisition separate; do not repurpose private recall as a publication feed. |
| `software/runtime/src/sage_lantern/adapters.py:133` | Existing model request builds a prompt from context. Routing a public hit through this method is not a zero-LLM direct answer. |
| `docs/lantern/DUAL_HOME_AND_PUBLICATION_CONTRACT.md:288` | Public cache needs committed status, named validity policy and provenance; receipt/query interface details remain unresolved at line 383 onward. No new SDK method is assumed here. |

Parent blockers: verified acceptance adapter, deterministic direct-answer gate,
explicit derivative consent, sanitized photo binding, durable protected outbox,
and lock-fenced submission/output must all exist before enabling sharing.

## 2. Explicit sharing workflow

1. Authenticated user selects one Q/A and chooses **Share knowledge**. Saving
   history, taking a photo, profile setup, or having public read access is not
   public consent. No old-history backfill, batch inference, or default sharing.
2. Build a separate draft from exact selected fields. Do not include prior
   turns, ambient recall, prompts, reflections, session IDs, profile name,
   private source IDs, audio, keys, cookies, local file paths, or precise GPS.
   Selecting a Q/A is not proof its contents are safe: the selected answer may
   itself contain private recalled facts. Require deliberate minimization.
3. Show the actual proposed public question, answer, source class, locality,
   validity, and optional sanitized photo. Photo sharing is a separate default-off
   choice. Label model-generated answers as generated/unverified, not observed.
   A model may suggest edits but cannot authorize publication or establish truth.
4. Warn before approval: public copies may persist; withdrawal/deprecation is
   not erasure. Editing or withdrawing private history does not retract public
   records. Public sharing may link observations through the proposer identity.
5. Explicit approval binds canonical derivative bytes, all included media hashes,
   destination/chain/domain, schema/policy version, and consent generation.
   Any edit, photo replacement, destination change or generation change requires
   a new preview and approval. Consent evidence remains locally protected; export
   only fields required by the approved public schema.
6. Persist exact immutable envelope and sanitized blobs before acknowledging
   `QUEUED_LOCAL`. On storage/fsync failure, give an error or uncertain outcome,
   never a success assertion. Stable ID plus canonical digest gives idempotency;
   same ID/different bytes fails. Signing requires a separately authorized public
   proposer; this review does not authorize creating one or using private Root.
7. Retry only definite unsent work. Persist dispatch intent first. Unknown remote
   outcomes reconcile by stable identity and exact digest, not blind resubmission.
   Keep transport, intake, canonical status, and answer eligibility separate.

Recommended initial lock policy: freeze unsent public dispatch on lock/expiry/
suspend and require explicit authenticated resume. Already transmitted bytes may
be irreversible. A cancellation racing dispatch must report unknown/submitted
honestly. Authenticate and fence at preview, enqueue, dispatch, and final output;
an earlier `/authorize` success is not a transaction-wide capability.

## 3. Accepted-cache direct-answer contract

`accepted` means independently verified eligibility, not an outbox label,
transport acknowledgement, proposer confidence, embedding match, or transaction
inclusion alone. Canonical commitment records acceptance under chain rules; it
does not by itself prove that a claim is factually correct.

The trusted cache ingest boundary must validate supported authoritative evidence
binding target chain, memory ID, canonical content digest, PUBLIC classification,
authorized publisher/domain, status revision/height and named approval policy.
Caller JSON, a syntactically valid signature, or a private-ledger receipt is not
enough. Pin the accepted trust configuration; fail closed on unknown evidence
formats. If the supported upstream surface cannot supply this evidence, keep
direct answers disabled rather than inventing a proof or SDK API.

Serve a direct answer only when ALL conditions hold:

- Cached answer bytes are integrity-checked and their provenance is displayable.
- Canonical status is committed, not proposed, challenged, deprecated, rejected
  or superseded; current policy permits this domain and source class.
- Validity and status-sync freshness pass. Track observed time, validity expiry,
  last successful verification and verified revision separately. Offline status
  cannot guarantee absence of later challenges. Without an approved bounded
  freshness policy, offline material is labelled historical/stale, not current.
- The query maps unambiguously to the exact public Q/A or a separately approved
  alias. Initial implementation should use exact matching with narrowly specified
  normalization only. Preserve negation, numbers, units, entities, dates, place,
  language and other qualifiers. Embeddings may rank candidates, never authorize
  a direct answer. No fuzzy threshold alone is sufficient.
- There is no conflicting eligible answer or missing scope. Questions such as
  “is it safe here now?” require location/time/evidence that a similar cached
  question does not supply. High-stakes/current safety advice is excluded from
  initial direct-answer domains pending a separate reviewed policy.
- Photo-dependent questions do not reuse answers for different image bytes or
  objects. A public cache photo illustrates its original observation, not the
  current camera scene. Without an approved evidence-binding scheme, decline
  direct answering of current-photo questions even when text matches exactly.
- Auth remains live and the generation/status snapshot remains eligible at
  emission. Invalidation must cancel in-flight streaming/TTS; already disclosed
  output cannot be recalled. Do not cache a positive auth decision across calls.

On a hit, return stored answer text through a deterministic renderer plus source,
location, observed/verified dates and `public-cached` label. No generation,
rewriting, summarization, model-based relevance judge, vision inference, or hidden
LLM fallback. Initial exact matching needs no embedding model either. STT/TTS,
if used, are separately measured and must not be represented as zero processing.
Treat all text as inert data: no HTML execution, prompt/tool commands, automatic
links/fetches, or embedded media dereferences.

On a miss, ambiguity, stale evidence or challenge, return a typed miss/selection
view. The ordinary generated-answer workflow may be offered separately with an
honest source label; it must not masquerade as the accepted-cache fast path.
Keep generated output out of the accepted cache unless it independently passes
the full explicit proposal and acceptance workflow.

## 4. Photos, storage and LoRa

- Derive a new public image via bounded decode, orientation application and
  re-encoding that strips metadata, embedded thumbnails and trailing payloads.
  Review visible faces, documents, screens, addresses and location clues; EXIF
  stripping alone is insufficient. User reviews the final derivative, not the
  original. Bind Q/A, final image digest, MIME, dimensions and byte length.
- Never publish a private-media URL or let a digest authorize access to private
  blobs. A digest/public URI is not proof of availability or image correctness.
  No automatic fetching of arbitrary evidence URLs; require a separate bounded,
  approved media retrieval boundary, including redirect and local-address rules.
- Specify tested ceilings for question/answer bytes, JSON depth, envelope size,
  decoded pixels, media size/count, pending entries, total disk use, request time,
  and per-user submission rate. Unknown bounds block activation; reject rather
  than silently truncate approved bytes. Protect drafts/outbox/journals with
  private storage and validated recovery; never repair corruption by erasing
  consent, receipts, or retry identity.
- Initial LoRa policy: no photo payloads, base64, or automatic fragmentation.
  Queue an approved compact manifest/digest only if it fits a measured transport
  budget; otherwise retain locally for an approved higher-bandwidth path.
  Missing required photo evidence prevents photo-dependent answer eligibility.
  “Photo pending” must not be displayed as delivered evidence.
- Measure encoded envelope/signature/framing overhead, not just source text.
  `docs/lantern/MESHCORE_HOST_PROTOCOL_TEST_PLAN.md:27` requires these measurements;
  no radio capacity, duty-cycle policy, or delivery guarantee is asserted here.
  Bound retries/reassembly and prioritize PTT/audio over cache sync or media work.

## 5. Required test plan (not executed)

Use synthetic Q/A, fabricated secrets, generated test images and fake receipt
providers. No real private history, external intake, keys, radio, or Unit B.
An executable test implementation belongs to the integrating code owners.

| ID | Exercise | Required result |
| --- | --- | --- |
| PK01 | Seed surrounding context/profile/reflections and selected answer with secret canaries. | Only reviewed derivative fields cross the boundary; selected-answer leakage requires explicit removal, not silent copying. No canaries in logs, telemetry, outbox extras or cache. |
| PK02 | Save history/take photo without sharing; cancel preview; edit text/photo after approval. | No publication; changed digest/generation rejects enqueue until reapproved. Photo default remains off. |
| PK03 | Generated false answer, malicious instructions, fabricated citations, conflicting sources. | Generated/unverified remains a candidate; no automatic trust promotion, execution, fetch or accepted-cache insertion. |
| PK04 | Exact query versus changed negation, unit, quantity, date, location, entity and paraphrase. | Only the exact eligible key or separately approved alias hits; high semantic similarity is a miss. |
| PK05 | Two conflicting eligible records or query missing locality/time. | No arbitrary top-score answer; bounded selection/miss with provenance. |
| PK06 | Same question with different current photo; swap derivative after preview; missing photo. | Photo-dependent fast path rejects; content hash mismatch prevents dispatch/cache use. |
| PK07 | EXIF GPS, thumbnail, trailing data, malformed JPEG, decompression/pixel bombs, visible document. | Bounded decoding and final-byte preview; unsafe content rejected or deliberately redacted; original bytes never exported. |
| PK08 | Forged signature, wrong chain/private receipt, wrong digest/domain/publisher, receipt replay. | Trusted ingest rejects; no eligible cache row. |
| PK09 | Walk local outbox through every state to COMMITTED without evidence. | UI may show local workflow state but direct-answer eligibility remains false. |
| PK10 | Committed becomes challenged/deprecated/superseded; older checkpoint arrives afterward. | Invalidate cache and in-flight output; never roll trust state backward. Explicit verified reinstatement is required. |
| PK11 | Offline cache, validity expiry, stale sync, clock rollback, unknown status. | Label stale/historical or miss according to reviewed policy; no false “current” answer. |
| PK12 | Lock/lease expiry/suspend at preview, enqueue, before network dispatch, during receipt handling and output. | Generation fences disclosure/new dispatch; paused work remains durable; uncertain dispatched work reconciles. No token resurrection. |
| PK13 | Crash before/after blob fsync, enqueue commit, dispatch marker, remote acceptance and receipt commit. | Exact bytes/ID survive; no false queued acknowledgement, dangling eligible media or blind duplicate submission. |
| PK14 | Withdraw consent/delete private source before dispatch and after unknown/accepted dispatch. | Unsent work cancels; uncertain/accepted outcome is honestly reported. No claim of public erasure. |
| PK15 | Duplicate JSON fields, oversized fields/arrays, extra hidden context, malformed encoding and same-ID changed bytes. | Strict bounded schema rejects before durable admission; immutable identity cannot be rebound. |
| PK16 | Symlink/hardlink/permissions, missing/corrupt DB, full disk, concurrent queue operations. | Fail closed without resetting consent/journal; private history and keys untouched. |
| PK17 | Instrument all generation/vision/embedding clients to fail if called on a direct hit. | Correct stored answer and provenance with zero such calls; separately report STT/TTS calls and latency. |
| PK18 | Large photo/text queued on simulated LoRa, duplicate fragments/receipts, endless retry and low disk. | No photo transmission; bounded text/retry/resource use, honest pending state, PTT priority. |
| PK19 | Public Q/A contains HTML, command strings, external/local URLs and hostile image references. | Inert display only; no execution, local fetch, private context retrieval or tool invocation. |
| PK20 | Reboot with pending candidates and an accepted-cache snapshot. | No auth restored; drafts remain protected; cache revalidates provenance/freshness and cannot become private canonical history. |

## 6. Freeze / activation evidence

Before activation, parent must attach: reviewed schema and numeric resource
limits; supported upstream receipt/status mapping with source evidence; exact
consent and match rules; all negative/race test results; no-LLM call counters;
private-storage/recovery evidence; and measured Unit A transport/latency budgets.
Document what remains disabled. This review neither validates public-chain
enrollment nor supersedes the upstream publication contract. No changes to
existing code or live systems were made for this review.
