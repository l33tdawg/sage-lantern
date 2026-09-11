# Lantern premise test — private, public, radio and offline knowledge

**Date:** 2026-09-05. **Status:** reviewed implementation/acceptance plan, NOT a passed demonstration.
Four scoped agent reviews covered retention/settings, privacy/poisoning, SAGE
lifecycle authority, and transport/topology. Parent cross-checked key source
paths and ran the existing queue/envelope suites: **8 tests passed**. These
are scaffold tests, not cryptographic, RF, public-network or Unit B evidence.
No device changes, RF transmissions, federation activation or key operations
were performed for this review.

## 1. Agreed topology and trust

- **Lantern A/B, later C/D:** each owns one independent full personal SAGE
  ledger, plus an ordinary public proposer/query client and public-only cache.
  They never hold public validator keys/votes. A's personal chain is not a
  replica of B's. Private local consensus must not be confused with Commons votes.
- **Public Commons:** a separate ledger with governed, admitted, stable
  Lightbearer Validators. Foundation and independent operators can host
  services; hosting or internet access alone grants neither votes nor data access.
- **Four-validator lab:** V1–V4 are separate public-validator identities and
  processes, not Lanterns A–D. With equal Comet voting power, three signatures
  are required; one Byzantine validator is within the fault bound. Two faulty
  validators are outside it. Application semantic-vote weights are a separate
  configuration and must be recorded rather than assumed equal.
- **Adversarial E/F:** malicious ordinary clients test forgery, spam, replay and
  poisoning. They do not become Byzantine validators unless deliberately given
  a validator role in a separate isolated test. No production identities/data.
- **Radio repeater:** transport only. **Lamplighter**, formerly Data Donkey,
  carries packs and is never a validator. Online validator service is a
  separately admitted **Lightbearer Validator** profile.

See [canonical terminology](../TERMINOLOGY.md) and
[public authority contract](DUAL_HOME_AND_PUBLICATION_CONTRACT.md).

## 2. Verified gaps — do not hide behind working buttons

| Capability | Current evidence | Missing before acceptance |
|---|---|---|
| Saved questions/photos | `scripts/lantern/unit-a-voice-loop.py` HistoryStore defaults off and stores text only; camera retains a separate full-frame browser preview | Durable original captured frame, item association, authorized media access, restart/delete/crash behavior and visible saving failures |
| Retention settings | History control is real, but disable currently deletes saved items; `settings_store.py` contains inert drafts only | Move real control to Settings; separate stop-future-saving from delete-existing; do not replace consent with a draft |
| Personal knowledge | Orchestrator recalls SAGE and generates an answer; it does not automatically submit that answer as a memory | Explicit local-save/provenance semantics where required; history is not canonical personal knowledge |
| Public queue | `public_queue.py` persists IDs and transitions | Strict PUBLIC-CANDIDATE schema, actual signature/digest/consent checks, scoped intake, encrypted queue boundary, authenticated evidence and reconciliation |
| Public outcomes | Test fixture uses `test-signature` and reaches COMMITTED without receipts | Never expose arbitrary state advancement as verified outcome; keep local/transport/intake/chain/validity states separate |
| Public subscription | Private signed recall exists | Scoped public query/cache, provenance, freshness and invalidation; no automatic private federation |
| Photo/public linkage | No durable local media or proposal linkage | Rejection retains the local source under its ordinary retention policy; it neither erases it nor makes it a trusted private fact |
| Knowledge bundles | Architecture exists; no importer/verifier/retrieval adapter found | Pinned publisher trust, verified atomic import, freshness/rollback handling and supplemental retrieval |
| Radio / Unit B | Unit A USB/profile evidence and component tests | Actual independent B provisioning, host adapter, authenticated exchanges and measured RF evidence |

`ProposalOutbox._validate_envelope` currently requires `PUBLIC`, whereas
contract section 6 requires application `PUBLIC-CANDIDATE` until approved
mapping to canonical numeric PUBLIC. This mismatch and non-verified signatures
must be fixed before that class is used as an intake trust boundary.

## 3. Lifecycle permissions — critical design decision

Reviewed sibling SAGE source/reference at checkout `d9dfd176`; deployment must
verify its actual binary/app version and effective grants independently.

- Public classification does not remove authentication/domain Read checks.
- Corroboration can be available to an eligible Read-authorized identity;
  self-corroboration is denied and the same principal cannot add repeated
  endorsements. A second device repeating downloaded text is not independent
  evidence, even where a protocol request is syntactically permitted.
- Opening a canonical challenge normally needs ownership or Modify authority,
  not ordinary proposer Write/Read. App-v27's narrow own-record exception in
  reserved static domains does not grant universal Commons challenge rights.
- Every eligible proposer should be able to **Report an issue** within its
  submission scope. An authorized review path must then open and resolve the
  canonical challenge; otherwise this part of the premise is not demonstrated.
  Do not grant all devices broad Modify merely to make a button work.
- Append/correction means a new attributed candidate/record. Generic links
  are off-chain and require Modify on the source plus Read on both ends;
  a `supersedes` link does not itself deprecate the original.
- Comet transaction inclusion, SAGE semantic commitment, intake approval and
  real-world correctness are different. BFT does not make false evidence true.

Source anchors: `../sage/internal/abci/app.go` functions
`processMemoryChallenge` and `processMemoryCorroborate`;
`../sage/internal/abci/appv21_challenge_test.go:79` explicitly denies a
Read-authorized corroborator permission to open a challenge;
`../sage/api/rest/memory_handler.go:3794` documents link enforcement;
`../sage/docs/reference/concepts/app-v27-lifecycle.md:13` limits the exception.

## 4. Minimum integrated A/B demonstration

### Automatic correction scenario — required, not yet implemented

The user specifically wants knowledge to heal: an old “clinic closed Monday”
claim should yield to reliable newer evidence that it is open. SAGE provides
corroboration, decay and challenge transitions, but they do not by themselves
detect contradictory text or automatically replace one record with another.
Corroboration is not a direct status change. In the reviewed app-v21 challenge
policy, eligible supporters/Modify holders are frozen when a round opens;
deprecation requires `k+1` distinct eligible challengers for the selected
supporter count `k`. App-version, bounds and author exceptions must be checked
on the actual chain. Four arbitrary devices saying the opposite is not that
protocol. See SAGE's `docs/reference/concepts/memory-lifecycle.md:115` and
`docs/reference/concepts/consensus-confidence-decay.md` corroboration section.

Build the smallest governed automation around these primitives: structured
clinic identity, claim type, effective dates, observation times, independently
attributed evidence and a named policy version. Conflict detection may propose
a review; it must not silently give an LLM mutation authority. A narrowly
authorized service can automate policy-permitted review/challenge actions,
with auditable receipts, rather than requiring every report to be manually
handled or granting every proposer broad Modify. Its admission and evidence
rules are an explicit implementation/review gate, not a bypass of consensus.

Test: commit a synthetic closed-Monday observation, submit four independently
originated newer open-Monday observations, reconcile the conflicting scope,
apply the actual authorized challenge and replacement lifecycle, then prove
A/B and refreshed packs prefer the new valid claim while the old record stays
available as historical/deprecated evidence. Also test four echoes from one
source, four identities controlled by one actor, different clinics/dates,
temporary holiday exceptions and insufficient evidence. Those must not
silently become an accepted correction. An unresolved conflict is displayed
as disputed; offline caches may be stale until reconnect. A replacement still
requires its own accepted record and invalidation of the old current view.

Run with harmless staged photos/observations, not personal data. Each row
needs timestamped results, exact software/config versions, identifiers/hashes
appropriate to that trust plane, and observed pass/fail—not a checked box alone.

| Gate | Required working behavior and evidence |
|---|---|
| P1 — Independent units | A and B independently provisioned; distinct personal/proposer/radio/management identities; private voice/photo use survives restart |
| P2 — Useful saved interaction | Full captured frame plus question/answer survives restart; Past questions displays it; failed inference/save is represented honestly; delete removes associated local media |
| P3 — Retention choice | New setup offers the requested default-on text/photo retention with notice before retained capture; preserve old explicit opt-outs. Stop future saving separately from deleting existing items; invalidate in-flight saves when consent changes |
| P4 — Deliberate publication | Share previews exact claim, provenance and optional minimized evidence. Original photo, private query/context and location never ride along implicitly. User approval binds exact bytes |
| P5 — Verified outcome | One approved/committed and one rejected proposal; authenticated digest/identity/chain-bound evidence; both sources remain local; retry/timeout/reboot causes no duplicate semantic submission |
| P6 — B learns | B queries one authorized public domain and retrieves the committed record with attribution/freshness; cannot retrieve A's private canary or rejected intake candidate |
| P7 — Correct and dispute | B independently corroborates, submits a linked addition and reports an issue; authorized review applies a real challenge/outcome; A/B reconcile actual challenged/deprecated/reinstated states rather than displaying stale trust |
| P8 — Reverse roles | Repeat the central flow with B proposing and A consuming; no special A-only privilege or copied identity |
| P9 — Radio path | Actual encoded proposal/message and authenticated receiver receipt traverse direct and one-relay paths; duplicated, altered and expired packets never duplicate application effects |
| P10 — Offline knowledge | Verified synthetic signed pack supplements local recall while offline; source/freshness visible; no private-ledger import or instruction execution implied |
| P11 — Automatic healing | Run the scoped clinic scenario and negative controls above; demonstrate real policy-driven canonical outcomes and A/B cache refresh, not simply a higher confidence score or a supersedes link |

Do not adopt arbitrary tiny media quotas just because old text history has
100-item/30-day limits. Measure capacity, reserve a free-space floor for runtime
and ledgers, bound individual decoding/uploads and define a visible retention
policy. Full captured photo means the actual captured frame, not a promise of
maximum sensor resolution. Persist explicit captures, never idle preview video.
Shared-device retention is visible to subsequent users; it is not per-user
authenticated privacy. Missing/corrupt established preferences must not silently
reset to on. Gallery is deferred; robust item/media storage is not.

## 5. Radio measurements and resilience

Extend [the MeshCore plan](MESHCORE_HOST_PROTOCOL_TEST_PLAN.md), preserving its
actual wire-size sweep. Before transmitting, verify hardware variant, antennas,
firmware/role and locally permitted region/power/airtime configuration.

- Measure A→B and B→A at recorded distance, terrain, obstruction, placement and
  antenna orientation. Report tested working range, not advertised maximums.
- Report application goodput **and** transmitted byte/airtime cost including
  signatures, encryption, framing, fragments, ACKs and retries; record offered
  load, sample count, first-attempt loss, eventual delivery and latency p50/p95.
  Small-sample tail estimates are exploratory, not field reliability claims.
- Test one relay, reverse ACK loss, fragment loss/reordering/duplication,
  tampering, unknown identities, old replay after restart, expired TTL,
  queue exhaustion and receiver/relay USB or power interruption.
- Break the route; test alternate route recovery only if supported. Unsupported
  ACK/relay/reroute behavior is a capability gap, never an assumed pass.
- Keep queued, relay-received, recipient-persisted, read and publicly committed
  separate. Replays must be harmless across restarts, not just within one process.
- Start LoRa with bounded text, IDs and receipts. Full photos, bulk bundles and
  full-chain synchronization are **not** assumed practical over this link.

## 6. Three sync tests and four-validator fault tests

1. **Private:** each personal ledger preserves its own memories/lifecycle across
   restart and supported fenced recovery. No automatic cross-Lantern private sync.
2. **Public validators:** authorize join/catch-up; compare height, canonical
   state/AppHash and exact outcomes after partition/heal. Snapshot/state sync is
   distinct from replaying missing blocks and needs its own verified procedure.
3. **Public client/cache:** resume scoped retrieval, reconcile challenges and
   deprecations, label offline staleness. A cache is not a full public replica.

In an isolated synthetic V1–V4 network: baseline commits; stop one validator
and prove continued progress; test a 3/1 partition and heal; test a 2/2 split
and require no fabricated progress until quorum returns. Record convergence
and no conflicting finalized history. A crash test is not an equivocation
test: use separately authorized Byzantine fault injection to claim the latter.
Two malicious validators are outside the equal-power four-validator guarantee;
do not claim resilience to that case. Colluding E/F clients instead test
admission, rate/resource limits, forged signatures/receipts, spam and provenance
laundering. Multi-process tests on one host do not prove independent power,
network or operator failure domains.

## 7. Signed knowledge-pack slice

### Retrieval order and the local radio pool

The intended product path is authorized private recall, verified local public
cache, then a bounded public request over LoRa to a nearby peer/gateway when
useful. The model answers using relevant retrieved evidence plus pretrained
knowledge; missing retrieval must not cause blanket refusal for ordinary
general questions. Time-sensitive claims without sufficiently fresh evidence
must expose uncertainty. This combined retrieval path is not yet implemented;
the current local private recall plus model fallback does not prove public use.

Nearby devices may have different authorized **subsets** of public knowledge.
A peer's highest observed source-chain height is not its completeness watermark,
not evidence of the latest version of every record, and not a new local public
consensus chain. Track source chain ID, record ID/version/status, verified
checkpoint or publisher attestation, coverage/domain, fetch time and applicable
validity policy independently. Display source checkpoint as such—not “synced”
without coverage proof. A raw peer height or stronger RSSI is not trust evidence.

Test peers with different subsets/heights, a high-height cache missing a later
clinic correction, stale but valid signatures, forged high checkpoints, a
disconnected upstream and conflicting publishers. Verify bounded request
timeouts, no silent private query/history disclosure, correction reconciliation
on reconnect and honest cached/unverified/unavailable labeling. A LoRa request
must contain an explicit authorized public query or minimized selector—not
blindly forward a user's private question, recall context or photo.

An accepted replacement or public challenge does not silently overwrite a
personal record. Its public-linked annotation can be refreshed while preserving
the private original/provenance; independent private facts remain private.
Public invalidations affect the public cache/current-answer policy, not private
ownership. A disconnected peer cannot promise unseen upstream corrections.

Use a tiny curated synthetic PUBLIC pack from a pinned lab publisher. Verify
authorized metadata, signature, all content hashes, size/path/type limits,
version/expiry policy and atomic activation. Reject unknown/revoked signers,
tampering, rollback, incomplete chunks and unsafe archive paths. Preserve the
last valid generation on failed import; expose stale/unknown freshness offline.
Verify that a distinctive pack fact can be retrieved alongside—not instead
of—private SAGE context; show provenance and treat embedded text as untrusted.

Use IP/Wi-Fi or verified removable-media custody for bulk distribution.
Lamplighters cannot sign releases or change trust. A signature authenticates
publisher/content, not factual correctness or native chain inclusion. Curated
knowledge-pack acceptance is not **Commons Snapshot Pack** proof: deterministic
committed-PUBLIC export/proof remains a separate `SAGE-EXPORT` dependency in
[the core crosswalk](../SAGE_V12_DEPENDENCY_CROSSWALK.md). Receiving a bundle
does not change model weights/training cutoff or make the public cache personal
canonical memory. Corrections/revocations need a tested refresh path.

## 8. Execution order and scope

1. Fix durable history/media and the real Settings retention control on A.
2. Implement strict candidate signing/intake/receipt reconciliation and the
   least-privilege issue-review path against an isolated public lab.
3. Independently bring up B; pass the bidirectional IP public/private flow.
4. Integrate and measure direct/relay LoRa, including replay and recovery.
5. Pass offline pack retrieval and separate public-validator fault/sync gates.
6. Add C/D and adversarial E/F workloads; expand terrain/load/operator tests.

Defer gallery polish, broad subscriptions, arbitrary private federation and
full fleet administration. None substitutes for the above vertical slice.
The two existing Unit A/Fleet master tasks remain open. This plan is sufficient
to define a premise test; the premise is not yet proven on A/B.
