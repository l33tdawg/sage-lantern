# Owner-facing private peer pairing

Source-only design and issuance-core implementation. No daemon, receipt policy,
device configuration, radio action or privacy default changed. Self-signature is
not enrollment, proximity, owner identity, RF delivery, or mutual consent proof.

## Owner flow

1. **Add nearby person** explicitly opens a short pairing window while unlocked.
   Existing `DeviceProfile.public_discoverable` remains false. Explain exactly
   which name and public identifiers are shared; profile naming never opts in.
   Cancel/lock ends the local window and any pending approval.
2. Prefer explicit QR/file exchange initially. A temporary nearby beacon must be
   a separate reviewed transport with ephemeral rendezvous data, not a broadcast
   of the permanent ordinary agent key/name or a new public-directory entry.
   QR is not implemented by this change and physical proximity is not assumed.
3. Display remote name as an unverified claim plus fingerprints derived from
   full chain/ordinary-agent/radio-key binding. Owners compare the actual trusted
   QR/fingerprints in person or over another authenticated channel. Prefixes,
   received signal strength and firmware names cannot authorize adoption.
4. Both owners approve independently. Separate “allow messages from this person”
   from “let this person find me”; do not infer one from the other. Root validates
   ordinary signer, current profile revision, broker/config/access epoch, signed
   card freshness, full firmware-contact match, and independently approved peer
   policy. Signer's private seed never enters browser, logs or exchange payloads.
5. Show paired/consent-expiry state, not delivery. A normal message still requires
   the established send consent and production dispatch path; read/delivery
   receipts stay governed by their existing distinct policies.

## Renewal without hourly root operations

Separate short cryptographic card validity (existing maximum 3600 seconds) from
an explicit owner-approved durable relationship. A later root renewal service may
issue fresh bounded cards for that exact peer under a finite owner-selected grant
with allowed scopes, grant revision, expiry and renewal budget. It must stop on
revocation, config/profile/radio/lifetime change, or unavailable private storage.
Default grant is absent: this design does not silently enable renewal or visibility.
While-locked flags remain false. After a long lock, expiry is expected; owner unlock
can resume an explicitly authorized unexpired grant without hourly operator input.

Renewal delivery uses the approved peer transport and shared serial scheduler,
never a second USB owner or raw browser serial access. Keep bounded airtime,
rate/queue budgets, randomized spacing and priority below safety/ordinary receive
work. Do not assume a whole <=4096-byte card fits a LoRa payload: current transport
MAX_FRAME is 176 and includes overhead. Fragmentation/reassembly, authentication,
replay/flood bounds, finite rendezvous slots and firmware-contact installation
need a separate protocol review. No arbitrary reserved/public channel is selected.

Revocation reserves a higher revision and emits signed false flags only with
owner authorization. Locally block immediately; remote revocation propagation
may fail and cannot invalidate already disclosed identity data. Never claim a
remote peer stopped before authenticated evidence. Preserve expired cards as
historical rollback barriers, not current send authority.

## Implemented narrow core

`mesh_contact_issuance.py` provides `preview`, `approval_digest`, and `issue`.
Preview binds profile revision/name, ordinary actor/full radio key, canonical
config hash, broker/access epoch, exact prior/next card revision, TTL and explicit
consent flags. Approval must match that complete digest and be used within
60 seconds, while still unlocked and current. No percent/proximity inference.

The injected existing ordinary signer signs through `mesh_contacts.sign_contact_card`.
Root must supply trusted fresh readers and a **durable exclusive CAS reservation**:
`reserve_revision(previous, next, approval_digest) -> True` only after confirmed
persistence. Reserve before signing. No rollback/retry on uncertainty; an unused
reserved revision is acceptable. Checks bracket reservation and signing, and a
revoked/stale request returns no card. Card bytes are output only, never sent.
The core imports no filesystem, socket, serial, provider or daemon entry point.

This is not a standalone authorization service. Do not expose caller-supplied
readers/CAS or trust a browser boolean as root authorization. The root handler
must mint and retain the server-side preview, bind confirmation to an authenticated
owner session and one-shot challenge, then invoke the core with trusted callbacks.

## Concrete next production implementation

### Separate ledger/handler implementation added

`mesh_contact_owner.py` now implements signed owner-scoped encrypted WorkflowStore
CAS reservation/finalization and root-only state/preview/confirm/cancel dispatch.
It reserves before signing, persists exact issued bytes before returning them,
and never clears an uncertain reservation or retries a write automatically.
The one pending preview is server-owned, digest-bound, consumed on confirmation,
and discarded on denial/cancel. Current broker/access/config/profile checks bracket
signing and finalization. The state operation returns hashes/revisions only.
Revocation uses a freshly confirmed higher-revision plan with both flags false.

Dedicated workflow kind is `contact_issuance`, NOT a reused conversation or outbox
kind. Current native/SDK/runtime allowlists do not yet admit it. The constructor
therefore refuses unsupported runtime schemas. The exact additive proposal is
`scripts/lantern/owner-contact-workflow-kind.patch`: apply the runtime hunk in
sage-commons and SDK/native hunks in sage only after shared-source owner review.
No shared source was changed; native acceptance and matched package promotion
remain required. Tests of the handler-to-real-SDK/WorkflowStore path use an
isolated source snapshot with just those Python allowlist additions and synthetic
signed HTTP/CAS responses. They do not prove native encrypted persistence.

Parent relay for Lovelace integration (no concurrent daemon edit): construct one
`OwnerContactHandlers` per owned resource, using the existing ordinary signer and
WorkflowStore probe. Root live readers must bind canonical installed config hash,
broker ID, unlocked epoch, ordinary actor/full radio key, and exact current profile.
Resource `active()` and unlocked access are mandatory, not a browser boolean.
Forward only the exact `owner-contact-*` operations through kernel `peer_uid==0`;
do not initialize another provider, enable workers or send a card. Owner-auth
forwarding must retain its real session and one-shot confirmation boundary.
Invalidate the handler on resource teardown. A new epoch/broker rejects stale
pending challenges even if no explicit invalidation callback ran.

Route schemas: all requests include `op, broker_id, epoch`. Preview additionally
requires `ttl_seconds, findable, messaging_allowed`; confirm requires exactly
`challenge, plan_sha256, consent:true`. State/cancel accept no extra fields.
Response card bytes only follow successful durable finalization. For a reserved
or unknown outcome, inspect the state and preserve evidence; abandoned-revision
reconciliation/export after response loss remains separately reviewed, not an
implicit retry. No renewal timer or public discovery is enabled by these handlers.

1. Add a separately versioned encrypted owner-relationship/revision ledger with
   real WorkflowStore CAS and uncertain-reservation reconciliation. Do not reuse
   an unrelated workflow kind or reset counters when historical cards expire.
2. Add root-broker preview/confirm/export/revoke handlers and owner-auth forwarding;
   invalidate one-shot challenges on lock, epoch/broker change and cancellation.
   Wire them only in coordinated maintenance with matched runtime/bootstrap pins.
3. Add minimal touch UI: scan/show card, fingerprint comparison, separate scopes,
   confirm/cancel, paired/expired/revoked state. No public visibility toggle implied.
4. Add explicit peer-policy adoption through a reviewed root-owned relationship
   policy extension. Existing import rejects unknown installed peers; do not edit
   immutable live config or bypass that gate to make discovery appear to work.
5. Only then implement authenticated bounded LoRa card exchange and opted-in
   renewal scheduler, including cross-lock, CAS conflict, crash, replay, fragment
   flood, firmware ambiguity and actual two-device acceptance tests.

These remaining integrations are not represented as shipped. Root import is a
source-reviewed route, not evidence of deployment; Lovelace's daemon/receipt files
are untouched. Issuance accepts either explicit public-discoverability preference
without changing it. Both that boolean and exact profile revision are approval-bound.

### Matched native/SDK schema evidence

The isolated `/private/tmp/lantern-owner-contact-native-review-20260908` review
now includes actual sibling `sage/internal/store/workflow_journal.go` **and**
`sage/api/rest/workflow_journal_handler.go` admission, sibling SDK request/response
schemas and the runtime allowlist. Real signed SDK/WorkflowStore requests against
the native REST router and vault-encrypted SQLite exercise the owner handlers,
dedicated kind, unknown rejection, actor isolation, CAS and locked-vault denial.
Three new native tests and 238 Python tests pass. The broader native workflow run
retains one pre-existing vault-expectation test failure, reproduced on the exact
unmodified base. Review manifests, patches, commands and logs are in that closure;
this is not a device deployment or full-native-suite success claim.

Uncertain reservations remain durable and block issuance after restart, without
expiry-based reset or automatic retry. **Production activation requires explicit
reviewed reconciliation** bound to the original UUID/revision and retained
evidence; no such recovery mutation is introduced in the schema patch. Daemon
wiring remains deferred until the corrected receipt-uncertainty SHA is reviewed.

Review v2 isolates Python/native interop behind the existing Go `integration`
build tag; native REST admission and encrypted-store tests remain ordinary tests.
Release review must explicitly run `go test -tags=integration ./api/rest -run
'^TestContactIssuancePythonNativeInterop$'` with matched
`CONTACT_INTEROP_PYTHONPATH`. Missing configuration fails the tagged test, never
silently skips it; ordinary Go tests need no Python environment. V1 evidence is
preserved separately from v2 validation in the versioned review closure.

### Explicit reconciliation source candidate

The subsequent separate `mesh_contact_reconciliation.py` candidate now implements
fresh owner-confirmed abandonment, preserved signed authorization/completion
archives, monotonic next issuance, and exact already-issued export without signing
a new card. See `OWNER_CONTACT_RECONCILIATION_20260908.md` for partial-outcome,
restart/cancel/race rules and the isolated real native interop closure. This
supersedes the callback-only recovery gap above, not the still-pending daemon,
owner-auth/UI integration or physical acceptance gates. No further native/SDK kind
change is introduced.
