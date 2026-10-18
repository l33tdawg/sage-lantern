# FunctionGemma action admission candidate

This disabled experiment separates the requested operation, target and current
instruction before proposing a call. It adds no production imports, credential
loading, device runner or service activation. The separate SDK candidate below
adds a disabled network adapter. A deletion request remains
unsupported; a tablet request cannot use the current Lantern status facade.

`action_boundary.py` recognizes bounded English instructions as whole requests.
Each read names either the current owner's private records or the authenticated
current device. Parallel reads require both operands; no partial action survives.
Literal saves preserve title/body bytes and keep payload instructions as data.
Ambiguous quoting, missing fields, mixed writes/reads, future/quoted instructions
and unresolved clauses fall back to answer/clarification. The grammar is narrow;
these rules do not prove universal natural-language safety.

Two policies are deliberately separate. `model_and_grammar` requires the calibrated
FunctionGemma proposal to match the complete parsed request exactly. `exact_grammar`
can propose explicitly recognized commands without the classifier. The latter
measures deterministic command coverage, not improved FunctionGemma accuracy.
Both still require independent qualification and authenticated execution gates.

Examples supported by the candidate:

```text
Find my saved note about the red suitcase.
Check this Lantern's battery level.
Find my saved suitcase note and check this Lantern's battery level.
Save a private note titled 'Keys' with exact text 'Use shelf 4.'
Save a private note titled "Keys" with exact text "Don't change this."
```

`action_session.py` defines the disabled broker contract. Its default is
`enabled=False`, and `QualifiedPolicy` defaults to unqualified. A trusted
qualification registry must bind the complete boundary/broker/contract/parser
source bundle and exact backend profile. No boolean supplied by a model can
qualify it. Submitted request IDs and exact text must match the authority's
authenticated reviewed-submit record. Interim speech and recalled instructions
are never submission records.

The authority resolves and binds owner, session, access epoch, private domain,
SAGE signing identity/chain/endpoint profile, current device, consent revision and
expiry. These are identity references, never bearer credentials. The expiry and
deadline use the broker's monotonic clock. `run_guarded` must serialize dispatch
and publication with lock/sleep/revocation and supervise drain. The backend must
honor the finite deadline and recheck between parallel calls. A separate check
followed by a network call does not meet this contract.

The durable journal reserves an owner/request key before dispatch. Handles are
single-use; rebindings cannot retry an unknown write, even if an expiry changes.
Cancellation can revoke work while the backend runs. A trusted receipt verifier
must verify the exact operation, current instruction, scope, full calls, result
bytes and final state before completion. `ok=True` and a note ID are insufficient.
A committed save requires a content-bound signed receipt or pinned online
canonical observation. Online verification is not an offline chain proof. Read
completion does not establish retrieval relevance, evidence interpretation or
answer correctness. A receipt retained internally after revocation does not
authorize disclosure into a new session.

The original broker tests use explicitly synthetic adapters. The next isolated
candidate supplies a filesystem journal, bounded HTTP transport and real SDK
adapter, tested against a synthetic loopback server. There is **no production
Lantern session/consent issuer or qualified private-node policy verifier wired
to the broker**. The isolated issuer below composes the actual authentication
classes with synthetic consent and policy verification; the adapters have not
passed production acceptance. Existing
`conversation_service.py`, `conversation_sage.py`,
`unit-a-device-auth.py` and `community_public.py` show relevant lifetime and
canonical-observation boundaries; their public/conversation policies cannot be
silently reused as authority for arbitrary private-note writes.

## Disabled private-note SDK candidate

`action_journal.py` reserves permanent owner/request fingerprints in a pre-created
0700 directory, using exclusive creation and file/directory fsync. It writes only
hashes and verified receipt metadata. Completion must match the original reserved
operation, instruction, scope and calls. Unknown, truncated or conflicting
reservations cannot be recycled; no retry/reset API exists. Local filesystem
tests do not qualify the production protected storage or crash/power behavior.

`action_transport.py` is a synchronous HTTPX transport for one fixed numeric
loopback endpoint. One monotonic deadline covers the whole SDK operation, capped
at 30 seconds. A supervised thread checks a nonblocking access predicate and
shuts down the active socket on cancellation/expiry. Every request and response
is checked; response bounds, duplicate JSON keys and security-field wire types
are enforced before SDK model coercion. It has no DNS, proxies, redirects or
retries. A stalled response body is fenced too. Undrained supervision refuses
completion. Cancellation cannot undo an operation already received by a node.

`action_sdk.py` requires a real `SageClient` and the supervised transport, supplied
by trusted construction; it never loads credentials or selects an endpoint from
model arguments. The SDK signs requests with the caller's supplied identity.
The adapter checks the current profile and explicit owned/read/write domain
grants before and after work. Its backend profile binds adapter/journal/transport
and SDK source hashes, private classification, endpoint, chain, identity, domain,
node revision and the verifier's private-policy reference.

The adapter defaults disabled. There is no default node-policy verifier. Its
operator-supplied verifier must independently attest the actual node, transport,
chain, canonical disclosure and private encryption/export/grant policy. Numeric
loopback pinning and a signed caller request do not authenticate the server or
establish owner/session/consent authority. Those references must not be echoed
from an HTTP payload or supplied by a model.

Private-note writes use `sage.lantern.private-note.v1`, exact title/text and the
bound operation ID, explicit private classification and observation type. An
acknowledgement or transaction hash never confirms a save. The adapter requires
an exact canonical GET with committed state, matching author, domain,
classification, content and content hash. Searches cover this new note schema
only, use committed/provider-filtered hybrid recall and verify every hit with a
fresh exact GET. Legacy notes, general facts and conversation records are not
silently reclassified. An empty result is not proof of complete search or answer
quality. Records remain untrusted data for any subsequent answer generation.

The whole plan is refused before network activity if it includes device status:
that source needs its own qualified current-device attestation. No partial
private read is executed from a mixed private/status plan. Receipt verification
consumes internal canonical-observation evidence once; a supplied raw `ok` result
has no such evidence. The real lifecycle authority must still serialize review,
consent, epoch/lock/sleep revocation, dispatch, drain and publication.

`scripts/tests/test_functiongemma_sdk.py` verifies real SDK request signatures,
actual loopback HTTP and journal filesystem operations. Its server, authority,
node policy and qualification are synthetic. These are integration regressions,
not authenticated SAGE-node acceptance, independent commands/human speech,
retrieval relevance or RK3588 measurements. The source snapshot and remaining
gates are in `work/functiongemma-admission-20261002/sdk-candidate-r1/`.

### Lantern lifecycle candidate, revision 2

`action_authority.py` supplies a disabled lifecycle coordinator for the private
SDK lane. Its privileged `activate` seam does not authenticate a session. A real
trusted issuer still must resolve the full owner/session/domain/signer/node/device
binding and explicit private-action consent. Every lock, sleep, owner change and
consent withdrawal must revoke this same coordinator before teardown. Existing
HTTP authorization and epoch snapshots do not wire that lifecycle or make a
check followed by dispatch atomic. No production service imports this candidate.
The core SAGE repository and Python SDK remain unchanged; the owner directs any
needed core change to `codex/sage`.

The coordinator accepts only trusted typed or final-speech review input. Preparing
a review cannot authorize execution. Submission consumes an exact displayed text
once; edits need a fresh review. A save additionally requires both explicit
session private-write consent and per-review human approval. These entry points
must remain trusted application handlers, never model tools or fields interpreted
from a transcript, recalled content or public/history preference. Source labels
and injected test scopes are not authentication or evidence of human consent.

`run_guarded` registers in-flight work under the lifecycle gate, runs network,
verification and filesystem work outside that gate, then checks the current
submission before returning. Each bounded nonblocking socket send is serialized
through `dispatch_step`; backpressure waits outside the gate. Transport now
refuses a plain check-only predicate. Broker cancellation shares the send gate
and also cancels the coordinator submission. No new SDK request bytes are admitted
after revocation wins the gate. Bytes already sent can still be received or
committed; their durable reservation remains uncertain and is never recycled.

Optional `execute(handle, publish=trusted_local_sink)` fences a synchronous local
completion sink through the same authority. Sink callbacks must be bounded and
must not wait or initiate network/model work. A retained receipt after revocation
never authorizes a late completion. Returning a result is not proof of browser
delivery: the eventual UI integration must carry and check the session/epoch at
the receiving surface. Thread-safe access/policy checks must be nonblocking; an
operator verifier performing network work inside a check violates this contract.

Revocation immediately closes admission and discards coordinator review text.
Replacement sessions cannot activate until in-flight work has drained. Epochs
cannot be reissued, completed/uncertain submissions cannot rerun, review lifetime
and capacities are bounded, and a failed bounded drain permanently taints this
instance. Close is terminal. No worker is killed and no uncertain reservation is
released. These process-local controls require qualification with real issuer,
revocation hooks, protected storage and crash behavior before use.

`sdk-candidate-r2/` freezes this source and the unchanged SDK. The scoped suite
passes 172 tests and 73 subtests, including 34 coordinator cases and 10 additional
SDK integration cases. New integration tests use the actual coordinator and SDK
with synthetic issuance, consent, node policy, qualification and HTTP node. They
cover revoke-before-first-byte, nonblocking backpressure, stalled-body cancellation,
drain, verified local publication and post-verification revocation. Lock/sleep/
owner/consent labels invoke the common coordinator revoke path; they do not
exercise physical events or live authentication. No real SAGE node/device was
contacted, no action qualified, and nothing activated.

### AuthBroker owner-binding candidate, revision 3

`action_owner.py` composes the actual `AuthBroker`, `DeviceLock` and
`SessionContext`. It defaults disabled and has no production import, HTTP route,
credential loader or live node-policy verifier. Trusted installation setup pins
an owner reference, device, signer, domain, chain, endpoint profile and exact
policy hash. The owner reference identifies that installation; it does not
establish a person's identity from a display name or arbitrary request field.

A trusted consent handler prepares one short-lived challenge with the current
authenticated context and exact private actions. Acceptance consumes it once,
requires strict human acceptance and identical context/actions, and rechecks
the current token and node policy. Neither existing history/public-share
preferences nor model fields can supply this consent. The inherited synthetic
`activate(scope)` seam is refused. The eventual application must authenticate
and expose the real human handler separately from model tools.

The issuer and action coordinator share the actual authentication gate. Broker
lock, cancellation, shutdown, replacement activation and observed device-epoch
changes revoke consent, reviewed text and submission authority under that gate.
Lazy presence/idle expiry is observed on protected checks without renewing
either lease. A refused old token does not revoke an unrelated current epoch.
Authentication cannot report a clean shutdown while an action worker remains
in flight; an unknown drain remains unknown after later quiescence. A failing
revocation callback closes auth admission and marks cleanup unknown. Revocation
cannot undo bytes already sent or release an uncertain write reservation.

The coordinator still forbids reissuing a revoked epoch, including a second
consent grant in that epoch. Reauthentication with a new epoch is required.
Changing consent while a worker remains active cannot admit a replacement.
Policy checks and local publication callbacks must be bounded and nonblocking;
no network verifier may execute while holding the authentication gate.

The qualification digest now includes the issuer, actual auth-broker source and
loaded DeviceLock/SessionContext source, as well as the existing action boundary.
An older coordinator qualification cannot authorize this revision. The SDK
profile separately binds its actual SDK, transport, journal and node-policy
reference. Neither hash creates qualification or proves a live private policy.

The scoped suite passes **326 tests and 178 subtests**, including composition
with the unchanged real SDK, loopback HTTP and filesystem journal. PIN
verification, human consent, node policy, qualification registry and SAGE
canonical records are synthetic in these tests. They cover lock before any
request bytes, cancellation of a stalled body, broker epoch/lease changes and
honest dependent-worker drain. This is source integration evidence; no live
private action, physical lock/sleep event or UI delivery is accepted.

`work/auth-integration-20261002/owner-binding/` freezes the source, validation
and read-only C policy observation. The observation pins C's running binary
and config. Federation's master switch is false; generic P2P/relay settings and
an omitted required-voter field nevertheless prevent the stricter private
policy attestation. The proposed correction is reviewable and uninstalled.
Core native/config semantics and cookie-cleanup guidance belong to
`codex/sage`. The local auth source also contains a separate native-desktop
candidate: never install that dirty whole file as a C-matched auth overlay.

## Read-only real signer preflight

`auth_preflight.py` uses the actual SAGE Python SDK and an existing pinned key
to read the exact caller profile and home-domain access sample. Its transport
allows only GET `/v1/agent/me` and GET `/v1/agent/me/domains`, with a shared
15-second deadline, bounded responses, strict wire security types, no proxies,
redirects, cookies, retries or key generation. The operator supplies the expected
public identity and exact home domain independently of the response. A missing
home domain in a truncated sample refuses; the sample is never treated as a
complete domain inventory.

Six negative controls require 401: unsigned, forged signature, changed identity,
replay of the successful signed profile read, expired signature and changed path.
The final fresh signed profile must match the first. Successful output is a
read-only protocol observation, never an `AuthenticatedScope`,
`OnlinePrivateBinding`, consent grant or qualification record.

The 2 October live Mac check authenticated Rook's existing identity and exact
home domain at clearance 1; all six negative controls returned 401. This proves
the tested node enforces these signer checks. It does not bind the Lantern human
owner, device session, access epoch, private storage/export policy or live
lock/sleep hooks. It does not authorize a classification above the observed
clearance. The earlier 172-test SDK candidate snapshot remains unchanged, with
its synthetic issuance/policy/node evidence and all activation gates still open.

## Fresh external qualification

`qualify_admission.py` freezes an externally supplied English corpus. It checks
author declarations against training-author IDs, requires an authorship-review
reference, refuses normalized exact overlap with historical questions, binds
gold/request files and audio bytes, and preserves raw predictions before scoring.
Declarations cannot prove independent authorship; their provenance needs trusted
review. Use independently authored positives and negatives with operation,
target, ownership, quoted/recalled instructions, negation, missing fields and
compound requests. Require useful positive coverage alongside zero incorrect
admissions. A zero admitted denominator has undefined precision.

The input JSON schema is `lantern.external-english-actions.v1`, with `authors`
(ID to provenance text), `training_authors` (IDs), `authorship_review` (review
reference), and `cases`. Every case contains `id`, `question`, `expected`,
`author`, `split` (`calibration` or `confirmation`), `modality` (`command` or
`human_speech`), `family`, and `language` (`en`). `expected` is either an empty
array or facade entries shaped as `{"name": ..., "exact": {...}}`.

Human-speech cases also supply `reference_transcriber` and `audio` with `path`,
`sha256`, and provenance. Recordings must be PCM WAV, mono 16-bit 16 kHz, at most
30 seconds. Their reference instruction and actual final ASR transcript remain
separate. Speech evaluation refuses to substitute reference text for ASR. A
transcript file is an ordered array of `id`, `text`, `asr_profile_sha256`, and
`state: "final"`. This evaluates proposals, not consent to execute the speech.

```sh
python3 scripts/lantern/benchmarks/tool_routing/qualify_admission.py freeze \
  --input <external-corpus.json> --history <historical-questions.jsonl> \
  --output <new-freeze>
python3 scripts/lantern/benchmarks/tool_routing/qualify_admission.py evaluate \
  --freeze <new-freeze> --split confirmation \
  --transcripts <actual-final-ASR.json> --output <new-results>
```

Use complete historical training/development lists, not a hand-picked overlap
list. Freeze sources, calibration policy/backend, authorship and speech provenance
before predictions. Do not tune on confirmation and call it untouched afterward.
The existing English and resident-ASR suites are inspected development evidence,
and the ASR command fixtures are synthetic. Neither satisfies this fresh gate.

## Timing and RK3588 gate

`bench_admission.py` replays the inspected 100/60 suites on the existing local
retention classifier. It checks the original complete backend calibration
binding, freezes sources/artifacts/policies, alternates all three variants and
checks original logits and repeated decisions. Exact grammar performs no model
call. Model measurements include tokenization, two compiled forwards and admission;
they exclude model loading, ASR, session checks, tool execution and answers.

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
  <existing-MLX-python> scripts/lantern/benchmarks/tool_routing/bench_admission.py \
  --output <fresh-output>
```

`voice_path_metrics.py` validates offline traces from one trusted clock, separates
user review wait from processing, and calculates cold/warm p50/p95 from end of
speech through final ASR, reviewed submit, authentication, admission, dispatch,
verified receipt, first visible token, first published text and browser paint.
First approved audio is separate. Incomplete, out-of-order, duplicate or mixed
profile traces are refused. Its arithmetic never certifies physical acceptance.
The current trace contract is for completed action turns; general-answer and
failure/cancellation traces need separate stage contracts.

RK3588 execution remains held by the existing C power gate in
`docs/lantern/NEXT_SESSION.md`: qualify at least 4.8 V under uncapped load with at
least 4 A available through the cable. No flag in this experiment overrides that
gate. Before device measurement, qualify independent command/speech admission,
real session/receipt adapters, original model/tokenizer/template/conversion and
RKLLM fidelity. The hidden-state head is not implemented by the vocabulary-logit
probe. After a qualified owner-approved window, capture paired cold/warm full
path traces, memory, power/thermal load, shared NPU contention, timeout/cancel,
owner/epoch changes, lock/sleep and drain. Preserve existing device sessions.

Local evidence and remaining gates are recorded in
`work/functiongemma-admission-20261002/README.md`. No local result enables actions
or completes the existing inference/device acceptance tasks.
