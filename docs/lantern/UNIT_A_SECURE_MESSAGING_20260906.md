# Secure messaging integration checkpoint

September 6, 2026. Source-only work after the accepted Messages/lock rollout.
No new device deployment, real messages, identity enrollment, radio IO or
private-data inspection occurred in this checkpoint. Unit A is not yet ready
for all A/B feature tests.

## Implemented contracts

- SAGE already owns encrypted canonical message payloads, intents and atomic
  argument-bound idempotency. Do not introduce a second Lantern vault/key.
- New signed `/v1/messages/storage` observes the concrete SQLite store, actual
  attached vault, encryption expectation, generation and per-server instance.
  Store replacement fails closed. This is not historical migration evidence.
- Optional signed `require_encrypted_storage:true` on canonical sends enforces
  encryption in the store while vault publication is excluded. A status probe
  alone cannot guarantee a later write will be encrypted. Legacy sends remain
  compatible; protected callers must explicitly request admission enforcement.
- Matching sync/async SDK methods use strict storage models. Lantern requires
  an operator-pinned loopback endpoint, no environment proxies or redirects,
  bounded HTTP timeouts and unchanged signer/transport bindings.
- Canonical Lantern sends preserve the original UUID, durably record only that
  UUID before dispatch, and retain uncertainty across restart. No automatic
  retry, new UUID, plaintext payload copy or exported recovery key is added.
  Atomic empty-directory admission prevents independent senders racing past
  the same empty observation. Marker removal must be positively confirmed.
- The firmware-pinned MeshCore driver now implements bounded handshake, send,
  radio ACK observation and single-message retrieval with synthetic streams.
  Details and primary protocol sources: `MESHCORE_TRANSPORT_PROTOCOL.md`.
- Signed self-consent cards and recipient-sealed, sender-signed message/receipt
  codecs now use the existing SAGE Ed25519 identity. Exact chain-qualified peers,
  UUIDs, expiry and original-message digests are bound. Receipt correlation
  rejects mismatched stored-message references; it returns a peer assertion,
  not an automatic history update. See `MESH_CONTACTS.md` and
  `MESH_ENVELOPE_PROTOCOL.md`.
- Bounded fragment assembly and an existing-radio contact inventory are now
  implemented. Fragment admission includes per-peer resource/rate limits;
  neither a radio prefix nor an assembled payload establishes identity.
  See `MESH_FRAGMENT_PROTOCOL.md` and `MESHCORE_CONTACTS.md`.

## Activation requirements — still open

1. Bind the ordinary provisioned signer to live signed enrollment checks and
   operator-verified endpoint/chain configuration. Never use the Root signer.
2. Integrate the signed exact-recipient consent cards into discovery and send,
   retaining revision/revocation evidence across restart. Active directory
   registration is not findability or messaging consent;
   remote chain/domain federation permission is not local-recipient consent.
3. Establish exclusive producer ownership, fence and join producers before
   interpreting empty markers as a clean lock/suspend drain. Reconcile unknown
   outcomes against canonical evidence without silently clearing markers.
4. Wire the voice service's provider factory. Its default remains disabled.
   Same-node canonical sends do not deliver to another Lantern's private node.
5. Integrate the identity cards, protected application payloads and fragment
   codecs with durable queues and authentic application delivery/read receipts.
   The codecs are not a running transport. A short radio ACK is not such a receipt.
   Firmware receive dequeues destructively; host persistence is not crash-atomic
   with that dequeue. Do not claim exactly-once receipt.
6. Enroll the separate public proposer, protect the public workflow archive and
   verify canonical acceptance/rejection before displaying status or reusing
   public knowledge. Private rejection history must remain intact.
7. Package matched runtime/API/SDK artifacts, finish guarded clean-install
   orchestration and validate on A before provisioning B with independent keys.

## Validation recorded

- Full local runtime/script regression at the first checkpoint: 1,691 tests and
  1,074 subtests passed with loopback test-server access permitted.
- SDK storage/admission contract: 132 focused tests passed.
- Focused SAGE API/store tests passed normally and with Go's race detector.
- Later incoming-radio and SDK integration tests require their own recorded
  validation; no synthetic result establishes physical RF behavior.
- Subsequent codec checks: 49 contact tests, 73 envelope/correlation tests,
  73 driver tests and 33 fragment tests pass in focused runs. A two-private-chain
  synthetic serial roundtrip verifies contact signatures, encrypted message
  framing/reassembly and a correlated return receipt; wrong-recipient decryption
  is rejected. Its two tests use in-memory persistence callbacks, not a real
  SAGE journal or physical radio, so they do not establish durable A/B delivery.

Release tooling now has a strict sterile runtime builder/verifier and a separate
SDK builder using a local version (`11.19.10+lantern.20260906`) without changing
the upstream public version. Final artifact promotion must follow source freeze
and actual artifact checks. Passing synthetic builder tests or legacy base-SDK
imports does not establish protected messaging capability.

## Durable workflow storage follow-on

SAGE now implements encrypted, actor-bound auxiliary workflow records and signed
ordinary-agent GET/PUT/discovery under `/v1/workflows`. Records bind their actor,
UUID, revision, schema and kind inside the existing vault ciphertext. Strict JSON,
actor/node quotas, JSON-safe CAS revisions and bounded 50-record pages are
enforced. Lock/corruption is unavailable, not a false empty history. Discovery
pages are not a multi-page snapshot; fence producers or rescan during recovery.
These rows are explicitly untrusted application state, never canonical memory,
foreign-message provenance, delivery or public acceptance by themselves.

Independent focused API/store tests pass normally and with the Go race detector;
76 workflow SDK tests also pass. Runtime integration and actual matched device
artifacts remain separate. Cancellable write admission releases the vault gate
while waiting for another writer; protected writes pin it through commit.

Two actual sterile runtime builds of the mesh checkpoint were byte-identical:
`6c37a7ee12f264743f8de6bc8f937985fb6cdc95b10bc02eb9c24722e6f67028`.
That unpromoted checkpoint contains 53 source modules. Its macOS/Python 3.12
verification imported 52 modules using host-provided dependencies; this is not
hermetic Linux/ARM64 acceptance. Any subsequent workflow source or dependency
change requires rebuilding. The expanded local runtime/script checkpoint passed
1,950 tests and 1,110 subtests, with one optional installed-artifact test skipped.

Full-security signoff must also close the existing plaintext conversation outbox
and retained JPEG storage. Owner-only permissions and screen locking do not
prove at-rest encryption. SAGE task `e28f5711-dc60-4beb-9a0a-b99fd3b80e1f` tracks
the media/history gap alongside the earlier conversation-storage task.

## Power-loss durability correction

The existing store used WAL with `synchronous=NORMAL`, which can lose recent
acknowledged commits after power loss. SQLite documents that FULL synchronizes
the WAL at every commit; NORMAL omits that sync:
[SQLite WAL documentation](https://www.sqlite.org/wal.html).

Source now sets FULL in both the connection DSN and fallback PRAGMA, and verifies
the on-disk WAL/FULL configuration at startup. Explicit `:memory:` test stores
remain supported and are not power-loss-durable stores. The new regression holds
three pooled connections simultaneously, checks `synchronous=2`/WAL, closes and
reopens the database, and repeats. Focused workflow/message/store tests pass.
This is configuration and software evidence, not a physical power-cut test or
proof that every storage device honors synchronization. Native latency and
drain measurements remain required; do not downgrade to NORMAL to hide latency.

Unit A reachability check after this source change failed: the previous SSH
session ended with a timeout and a new strict-host-key connection also timed
out. No reboot, service restart or private-data read occurred. Owner wake-up was
requested; local implementation can continue while hardware is unavailable.

The temporary-database benchmark `BenchmarkWorkflowJournalDurableCommit` records
100 encrypted FULL-sync commits without touching a live ledger. On the development
environment (`darwin/amd64`, reported VirtualApple CPU), it measured p50 0.08583 ms
and p95 0.1172 ms. These virtual-host results are only a harness check, not Unit A
storage latency, physical sync guarantees, lock/drain timing or power-cut evidence.

## Runtime queue checkpoint

`WorkflowStore` now supplies guarded, epoch-bound SDK access. `MeshOutbox` records
signed queue/progress state and commits `dispatching` before a radio command;
uncertain dispatch cannot silently retry. `MeshReceiver` records intent before
destructive dequeue and protects captured bytes before disclosure. Pending capture
survives restart; unresolved receive intent blocks another dequeue. Its storage
lifecycle permit must remain valid until the root dispatcher has joined workers.

The combined adapter/outbox/receiver checkpoint passed 108 tests. These are
software tests using synthetic transport/storage fixtures. Public-provider wiring,
final message/receipt ingestion, exclusive runtime ownership, UI integration and
real device delivery are not established by these results.

The initial sandboxed full run could not bind loopback test sockets. Rerunning
with that permission passed; no application workaround was introduced.

Tracking: SAGE task `c4c789b2-8986-47be-bdc1-2e1cb3e478bc`, linked to community
and Unit A completion tasks. The prior deployment remains documented separately
in `UNIT_A_COMMUNITY_LOCK_20260906.md`.

## Real API interoperability checkpoint

`python3 scripts/lantern/run-workflow-interop.py` now passes against two temporary
Go HTTP servers, actual signed SDK requests, encrypted SQLite workflow stores and
the runtime outbox/receiver/inbox. Eighteen simulated radio fragments reconstruct
the original message, including replacing the receiver/inbox handlers mid-transfer.
A genuine read receipt also traverses twenty simulated receive fragments and
durable inbox admission before correlation promotes the sender state. Admission
alone leaves it awaiting a receipt; replay after correlation is idempotent.
The fixture checks cross-store isolation and absence of its private plaintext
canary in both database and WAL files. Client cleanup uses the SDK context-manager
protocol through `ExitStack`; an initial harness-only nonexistent `close()` call
was corrected.

These are synthetic identities and simulated serial streams. Consensus fixtures
are mocks; replacing handlers is not restarting either Go process. Receipt
receive frames are synthesized without a production durable receipt sender.
This is not independent deployed chain, two-device, radio or power-loss acceptance.

Review found that rejected input could leave the receiver's only captured slot
occupied indefinitely. Sixteen reusable hash-only disposition slots now record
definitive rejections before capture is cleared. Storage uncertainty, revoked
access and temporary capacity failures remain unacknowledged. Independent review
and invalid-frame-then-valid-message integration tests pass under the required
single-owner receiver contract.

Historical inbox reads now verify a local admission attestation before trusting
its timestamp and revalidating the encrypted envelope. This permits authorized
reading after network expiry without admitting expired incoming traffic or
retroactively signing legacy rows. Receipt quota review also found and fixed
unbounded redundant receipt reservations: redundant assertions allocate nothing,
and each original message has at most two fixed slots plus two receipt UUID rows.
The parent checkpoint passed 215 adapter/outbox/inbox/receiver tests; the real API
harness also passes with historical verification. These are not live UI tests.

The private JPEG store is implemented using the existing SAGE vault, preserving
original bytes with immutable actor/UUID binding, quotas and verified reads.
It remains disabled by default. Authenticated REST, sync/async SDK methods and
the guarded runtime adapter are implemented. The real two-store harness also
passes original-byte upload/read, identical retry and cross-store photo isolation;
its synthetic header-only JPEG includes the same database/WAL plaintext canary.
This is storage/protocol evidence, not an image-decoder or camera quality test.
GET validates actor, UUID, length and SHA-256 headers; only schema-marked 404 is
treated as actual absence. Production configuration and retained-history caller
wiring remain required. Current deployed JPEG files are not thereby encrypted.
No new artifacts or providers have been deployed at this checkpoint.

Conversation storage additionally needs atomic consent-versus-dispatch admission.
The protected workflow store now supports a same-actor control-revision guard in
the target CAS transaction and two explicit conversation kinds. Guarded REST,
sync/async SDK and runtime adapters are implemented. The real API harness confirms
that changing the control revision prevents a stale guarded session mutation and
leaves its payload/revision unchanged. The guard checks revision, not the meaning
of the consent payload; callers must still enforce consent before admission.
The runtime adapter's 61 tests pass, including strict guard rejection before IO.
The actual conversation-store replacement and lifecycle wiring remain in progress;
this does not encrypt the existing local SQLite outbox. Parent race tests covering
private media, workflow guards, message storage and SQLite durability pass.

## Protected history integration checkpoint

The conversation workflow replacement now stores interaction/reflection state
and photo intents in the existing encrypted SAGE workflow store. The real API
harness verifies reservation before JPEG PUT, digest-bound link commitment,
history retrieval, and suppression that hides history without pretending the
immutable encrypted JPEG was erased. It also verifies cancellation of a public
preparation before submission; no public proposal is transmitted by that fixture.

Runtime configuration has an explicit `[sage] protected_storage = true` branch
requiring an operator-verified local `chain_id`. It uses the existing ordinary
identity, five-second no-proxy SDK client, and defers vault-backed initialization
until authorized use, allowing boot while locked. Missing providers fail closed;
there is no plaintext fallback in protected mode. Owned clients close after drain.
Generic/legacy configuration remains unchanged; provisioning must opt into and
verify the matched protected stack. Existing legacy data is not migrated here.

Conversation service media calls now participate in foreground shutdown tracking.
Stopping during a photo operation cannot return a clean close or disclose late
bytes; a completed PUT whose authorization was lost remains an unknown outcome.
The photo intent and ordinary conversation use identical reflection content.
Parent factory/service/media tests pass: 50 tests and five subtests.

Review additionally identified lock→unlock between checks as a stale-work hazard.
Protected service activities now capture a per-thread access epoch; nested work
cannot adopt a replacement epoch. The broker's matching helper-only access-epoch
endpoint is implemented, with legacy `/state` unchanged. Epochs rotate on lock,
activation, expiry/removal, error revocation and restart, not presence heartbeats.
This source branch must not be
activated against an older broker. Genuine root-owned storage lease, shutdown
ordering, matched release and native lock/sleep acceptance remain required.

## Ownership and root release checkpoint

The protected factory now holds the same `owner.lock` as legacy conversation
storage, preventing cooperating old/new producers from running together. The
directory must already be provisioned and canonical; only an empty lock file is
created. Held inode, permissions, ownership and process identity are rechecked.
This is not protection against root or noncooperating same-credential writers.

`ConversationService.release()` stops admission, joins producers, acquires a
thread-bound/deadline-bound storage inspection lease, and validates a typed drain
snapshot before closing the client and releasing ownership. Durable queued work
alone permits release; dispatching, unknown and pending-media states do not.
Root voice cleanup uses this path for protected memory without a legacy/resource
close fallback. Missing/uninitialized storage evidence remains unavailable; a
future dormant cold-boot release requires separate root evidence, not zero counts.
Parent owner/factory/store/root-cleanup regression: 97 tests and 17 subtests pass.

The real two-store API harness now uses the actual protected factory and real
temporary owner lock. It verifies queued-only release, client closure and owner
reacquisition. Its broker authorization epoch remains an explicit synthetic test
input. The return-message path now uses the durable receipt sender, including
sender reconstruction and explicit recovery, rather than direct receipt sealing.
Independent parent execution passes; radio frames remain simulated and consensus
fixtures mocked. No physical radio, sleep or four-validator acceptance is claimed.

Public acceptance remains a separate open gate. An authenticated gateway
observation is not a validator-quorum/inclusion proof and must not independently
enable accepted-cache answers. Isolated real-consensus testing and the missing
proof/accepted-retrieval integration are being worked on, not replaced by the
mocked interoperability fixture or a single gateway signature.

## Awakened-device checkpoint — 12:52 MYT

After the user confirmed A was awake, authenticated read-only SSH succeeded.
Boot ID remains `7fc2a334-26e4-48e8-b585-42e1072c4346`; the LLM remains PID 797,
started at 10:13:52, with zero automatic restarts. Personal SAGE remains PID
1077 with zero automatic restarts. Systemd reports no failed units and storage
has approximately 400 GiB available. USB enumeration shows one Espressif serial
device at `/dev/ttyACM0`; enumeration is not a firmware handshake or RF test.

Voice loop is PID 66652, started at 12:49:04 with zero automatic restarts. Its
changed process identity must be rechecked before deployment; the cause was not
established because the unprivileged journal view is restricted. This checkpoint
does not establish that installed artifacts still match the earlier manifest.
No services were restarted, hardware ports opened, or software deployed here.

Parent regression of the new mesh runtime factory, initial USB opener and public
acceptance preparation passes: 41 tests and 36 subtests using the sibling SDK
source at `../sage/sdk/python/src`. A first invocation used the wrong SDK search
path and failed factory setup; it is superseded by the matched-source run, not
evidence of deployed compatibility. Root authorization/controller integration
and real four-validator native enrollment remain open work.

## Public sharing from past questions — source checkpoint

History details now open the same explicit public preview/consent flow as the
current answer, retaining the selected question when returning. History lists
request source-bound status in batches of 20. Yellow indicates queued/pending;
green is reserved for verified accepted status. Unavailable or unknown status
stays neutral, never silently becomes unshared or accepted. No badge is shown
for an explicit verified `not_shared` result. Icons include text labels.

The backend persists source IDs only in the protected auxiliary journal and
resolves historical previews from guarded protected history. Status requires
current canonical online verification for acceptance, not an auxiliary row or
gateway observation. Legacy rows without source binding make lookup unavailable
rather than matching question text. Photos remain excluded from public previews.

Parent checks: 99 frontend tests pass; focused backend/root sharing checks pass
93 tests and 54 subtests; changed frontend files lint clean. TypeScript checking
still reports unrelated live-enrollment and observation-contract errors, with no
remaining errors in the changed history/community files. Not deployed to A.

The source association now carries domain-separated local approval signatures
binding source UUID, proposal operation/hash, timestamp and actor/chain/domain.
Existing unsigned rows cannot acquire proofs during lookup. Reassignment and
cross-row proof swaps are rejected. An unsigned cancellation cannot erase this
association: status becomes unknown, not falsely unshared. Parent regression
passes 100 tests and 61 subtests. The real encrypted-store fixture also exercises
signed preparation and reconstruction; this is not public consensus acceptance.
Matched deployment and native acceptance remain outstanding.

## Release and consensus-test preparation checkpoint

Release verification now checks protected/mesh module inventory, guarded workflow
and media SDK APIs, and the exact reviewed SAGE binary version/hash before claiming
the installation directory. Parent checks pass 122 tests and 115 subtests, with
one installed-artifact test skipped; no artifact rebuild or native install occurred.

The four-native-node public acceptance runner is implemented and awaiting review
before execution. Local Docker metadata confirms ten CPUs and 17,248,088,064 bytes
of memory. This is capacity evidence only, not acceptance: no containers were
started. Compatible pinned native/helper/Comet images still need preparation.

The root storage lease cannot live in the auth broker, which is stopped before
voice drain. Supervisor integration must own admitted producer lifetime and the
cooperating vault-close gate; a marker or live unlocked check is insufficient.
This integration and the matched appliance manifest remain active source work.

Parent integration regression found a reproducible multi-fragment send timeout:
`test_explicit_consent_required_and_multifragment_bytes_preserved` fails with
`outcome_unknown`, including a single-test run after 10.31 seconds. The broader
mesh/controller run passed 68 tests and failed this one. Do not treat earlier
agent-reported passes as current activation evidence; dispatch lifetime is under
investigation and deployment remains held. Docker runner isolation fixes pass
30 tests and 33 subtests; no real consensus containers have started.

The storage supervisor source now implements process/vault-bound admitted leases,
verified producer drain and a cooperating SAGE-stop gate. Parent's focused suite
passes 44 tests; native Linux peer credentials and startup wiring remain unproven.
Integration direction: keep voice and browser unprivileged; a dedicated root mesh
daemon owns the hardware/runtime behind a fixed, credential-checked IPC surface.
Do not elevate the voice process to satisfy USB-open/adoption requirements.

The generic appliance manifest passes eight tests and 18 subtests. It is not yet
the complete deployable appliance: canonical payload/destination selection and
fresh-unit installer integration are still being implemented. No provider was
activated and no device software changed at this checkpoint.

## Isolated public-test build started

Reviewed builder/runner regression passes 40 tests and 46 subtests. Real offline
arm64 preflight succeeds with Go 1.26.0, source inventory
`a877e282aa1fdf5a1f3d3bff7cb76e72fb382ae2bd7159dd9c3daf69e448cb4f`
and toolchain inventory
`b0c95061a4c9a11fd511fbbd6e459182b59a93f041f2f798c32b9735de97008b`.
Compilation has started into `/private/tmp/lantern-public-static-20260906-1315`;
completion, binary hashes, image build and network execution are not yet verified.
The runner now compares historical headers after the post-acceptance height
barrier, uses an empty Docker client configuration, and bounds generator resources.

Compilation and local scratch-image packaging subsequently succeeded. Verified
image: `sha256:6764ae664e994af3b381e1422a65955c7eef02ff16d3867a147797b4e8bb25cd`
(arm64). The legacy Docker builder did not consume SOURCE_DATE_EPOCH, so image-ID
reproducibility is not established. Runtime preflight passed.

Actual isolated run 1 failed during genesis generation; explicit writable
`--home /generated` fixed the verified native Comet startup issue. Run 2 then
generated four nodes but failed the running-container binding check before
consensus acceptance. Both attempts report confirmed cleanup. No public
acceptance is claimed. Native startup diagnostics are being investigated with
fresh synthetic state; Unit A and institutional SAGE were not contacted.

Startup diagnosis confirms missing version/fork provenance markers on the newly
generated synthetic homes, not OOM. The production migration guard correctly
refused ambiguous existing genesis. The fixture must establish reviewed binary/
fork pins on new homes before startup; never disable that guard or stamp failed
chains. A third diagnostic attempt also reports confirmed cleanup.

Messaging HTTP enqueue now returns the durable queued record without attempting
all radio fragments inside the ten-second request budget. Parent's 16 controller
tests pass in 2.05 seconds. Those tests intentionally send no RF frames; they do
not establish completion of background delivery. Background-worker integration
and full fragment/reconstruction checks remain required.

Canonical fresh-app installation now enforces the stage profile rather than
accepting arbitrary role-labelled files. Parent combined installer/stager checks
pass 37 tests and 77 subtests. This remains local validation, not an installed B.

Fresh public-test version/fork pinning has passed migration on actual execution:
the native process remained running without OOM. The next attempt failed strict
port-publication validation and cleaned up. The port guard is retained while
actual Docker binding metadata is investigated; there is still no consensus pass.

Parent rerun of the targeted simulated background roundtrip now passes (56.65s).
It preserves the authorized wire/UUID across restart, finishes fragment delivery,
reconstructs the recipient message and exercises the return receipt. This
supersedes the earlier failure for that specific test, not the full background/
daemon suite or real RF acceptance. Broader validation and native integration
remain open.

Parent frozen combined background/daemon/controller regression now passes all
61 tests (72.43 seconds). Earlier inbox reconstruction failure was traced by the
author to test-fixture capability discovery and an invalid synthetic radio key;
the passing simulation is not native RF evidence.

The public runner is moving to fixed-loopback RPC through the signed-test helper
inside each owned container, with no host-port publication. The helper accepts
only bounded read-only Comet queries and does not load keys for RPC mode. A new
pinned offline image compilation is running for source inventory
`b0f677449d4fbb456e7360dc5bddc43ab723061f6ae5805d34d62fea25554b1b`.
The previous image lacks that helper mode and must not be used with this runner.

The RPC-enabled image has now compiled and packaged successfully:
`sha256:2775353edf1c363ad5c1567158202beb99190fd3fff0d33e9251df8c594ac35d`.
A fresh actual four-node run reached `legacy_watchdog` using zero published host
ports, the pinned native version and fork 1. Project:
`lantern-public-e1d2d2a94b564411b47c98bc755de709`. It remains running, not accepted.
Do not restart or clean it up merely because a status observation times out.

Read-only native upgrade status on all four test nodes confirms a pending app-v6
plan at activation height 202, with no active ballot. Observed heights 28–29 were
below that threshold; this is an expected production activation wait, not a
reason to bypass governance timing or restart the run.

The daemon's coordinated shutdown patch is present: it stops admission, joins
handlers/producers and uses supervisor shutdown before closing client/key
resources. Parent's current daemon/supervisor tests pass 90 tests in 2.00 seconds.
Independent review of the original termination/receive-resumption blockers is
still pending; these tests alone do not authorize native activation.

## Follow-up verification: history sharing and public transport

The subsequent independent review cleared the two original daemon lifecycle
blockers in the reviewed source snapshot. Native activation remains unverified.
Parent reran history/community UI tests: 20 passed. Public workflow/history
backend tests: 74 passed plus 18 subtests. Public mesh protocol and gateway bridge
tests: 42 passed. These are source tests, not radio or public acceptance evidence.

The still-live isolated four-node acceptance process (session 45428) was read
without restarting it. All four nodes reported height 190, identical block and
application hashes, and catching_up=false. The app-v6 activation remains scheduled
for height 202; the full governed upgrade and ordinary public proposal checks
have not completed. No new artifacts have been deployed to Unit A.

Remaining work is actively split between public mesh provider integration,
unit-neutral status/provisioning, strict root mesh preparation, and explicit
user-consented read receipts. Message opening currently rejects a request to
share a read receipt; fetching a message must not silently become proof it was
rendered or permission to disclose reading activity.

The same acceptance runner subsequently advanced from `legacy_watchdog` to
`upgrade_7` without intervention. The legacy migration gate is therefore past,
but this is not completion of the remaining upgrades or public proposal test.
Unit-neutral status/ALSA/kiosk source is now implemented and agent-tested; parent
regression verification is in progress. Autologin preparation and root mesh
configuration preparation remain separate work, with no service activation.

Parent regression verification completed: installer, canonical staging and status
tests passed 64 tests plus 101 subtests. Independent protocol/bridge review found
no concrete production REST mismatch in the reviewed modules; its 42 tests use
mocked HTTP. Actual gateway identity/chain pins and lifecycle integration remain
required before activation, and neither result proves radio delivery.

## Matched runtime completeness gate

The release verifier now explicitly requires the root daemon, voice IPC, storage
supervisor/socket, and public mesh protocol/provider/bridge/observation/queue
modules. Previously a matched but incomplete source/wheel pair could omit these
modules without violating the minimum inventory. Regression cases remove each
module from both source and wheel and require rejection. Parent results:
18 passed, 91 subtests passed, 1 skipped (installed-import acceptance needs an
explicit separately provisioned validation environment). No bundle was rebuilt
and no installed/native coverage is implied.

## Cold boot and provider integration follow-up

The preparation CLI now handles help and invalid arguments without importing
installed runtime dependencies; real preparation still refuses missing runtime.
Parent verification passed 124 tests. Native-service review found that requiring
the voice runtime directory before the daemon starts voice creates a cold-boot
dependency failure. Its ReadOnlyPaths entry is now optional while strict system
protection remains. The focused updated daemon/preparation set passed 73 tests.

The mandatory drain directory is a separate real fresh-install gap: it must be
provisioned as service-owned 0700 and auth must receive its drain-report argument.
It must not be made optional or have old receipts erased. Installer owner is
implementing that handoff. Current installer/stager regression passed 44 tests
and 87 subtests before that change.

Hooke's typed public mesh provider is source-frozen; root/IPC/CommunityService
composition is now assigned. Its gateway observations are not verified public
acceptance. A separate proof/export investigation and realistic packet-lifetime
review remain required. The isolated real consensus run remains live at
`upgrade_7`; no Unit A activation or independent Unit B boot is claimed.

Parent public provider/protocol/bridge regression passed 59 tests. The explicit
read-receipt domain helper is now implemented; its UI rendered-event, service
route and root authorization integration are assigned, not yet a deployed user
feature. Fetching alone must never emit read receipts.

## Review findings requiring implementation, not acceptance claims

The public transport review found the previous default worker pacing could not
finish even a tiny signed request/receipt exchange inside its 120-second window:
49 outbound and 31 return fragments imply roughly 158 seconds with the alternating
one-second schedule, before I/O or RF delays. A 500-character answer required 63
outbound fragments. These are synthetic wire-size calculations, not RF measures.
Hooke is fixing pacing and admission budgeting together, plus inconsistent public
content limits and response-slot collisions. Extending an authentication lifetime
or treating mock transport tests as physical success is not the resolution.

Upstream inspection found no compact memory/lifecycle inclusion proof against
the current concatenated-state AppHash. Transaction inclusion alone cannot prove
later record acceptance. Lorentz is investigating a governed authenticated-state
commitment/export design; green offline acceptance must remain unavailable until
the actual record state can be verified from an independent trusted checkpoint.

Fresh installer drain provisioning is now implemented and pending parent rerun;
existing receipts/state are rejected and preserved, not erased. The release
minimum inventory also now includes mesh_read_receipts: 18 release tests and
92 subtests pass; the explicit installed-import environment test remains skipped.

Parent fresh-installer/stager rerun after mandatory drain setup: 47 passed and
87 subtests passed. Parent read-receipt helper/sender tests: 39 passed, using
synthetic storage/radio fixtures, not recipient-device display or RF acceptance.

The existing four-node test was polled live again and all nodes reported height
364 with the same application hash and catching_up=false; runner still reports
upgrade_7. This is active consensus progress, not a reason to reset the run.
The proposed public-proof boundary is a governed composite AppHash retaining
the legacy full-state hash alongside an authenticated public-memory commitment.
Implementation must preserve preactivation history, use canonical state from
one committed snapshot, and verify membership against H=h+1. It is not yet
implemented or an available acceptance signal.

The unchanged real consensus runner has advanced to `upgrade_8`. Parent launched
a broader runtime integration run (session 13532; log
`/tmp/lantern-runtime-integration-20260906-current.log`). It is still running and
has reported at least one failure, whose traceback is not yet emitted. Do not
claim a suite pass or restart the live process. Source is concurrently changing,
so this run diagnoses integration failures rather than certifying a release.

The broader run finished: 2036 passed and 697 subtests passed; the one failure
was PermissionError when binding the synthetic localhost HTTP test in the
sandbox. The exact test rerun with approved loopback access passed (1 test,
4 subtests). This does not freeze concurrently changing sources or substitute
for Linux/device acceptance. No product code change was needed for that failure.

The new fresh-provision phase runner's parent tests passed 8 tests and 6 subtests.
It wires base bootstrap/install/metadata and guarded app installation, but
personal-SAGE onboarding is still being separated from legacy voice readiness.
The read-receipt UI is implemented; root IPC and service provider composition
remain active handoffs before the feature is usable end to end.

## Scratch installed-package verification

Built unpromoted runtime and SDK wheels into new private temporary directories:
runtime snapshot `6d433590b01157f4cb78e5654d767b9987243be7548463b9f89e42dce0047ed9`,
SDK wheel SHA256 `93b9708ec81f054f6495adc17c03aab60efda77ef4163a5653f5a0632879d455`.
The runtime wheel matched the source tree when checked immediately after build.
Both were installed with no-index/no-deps into a new host test venv, using host
site packages for dependencies, not into an existing environment or Unit A.

The installed verifier imported 79 modules and hashed 80 source files; required
SDK signatures were present without constructing clients. Canonical messaging
correctly remained unavailable pending runtime storage/auth verification. This
is macOS/Python 3.12 package integration, not isolated dependency resolution,
Linux ARM64 acceptance, production artifact promotion or a final source freeze.

Hooke's transport timing/slot fixes are frozen for review. Actual device radio
timing must be calibrated before public admission; simulated timing tests do
not establish physical airtime. Root/IPC/provider integration continues.

Independent review cleared the original timing and response-slot collision bugs
but identified unbudgeted private scans/dispatch during a public response wait.
Hooke is correcting that remaining scheduling path. Public-provider documentation
now describes four-frame batches, bounded free-slot allocation and actual timing
calibration requirements rather than the previous one-frame/hash-slot behavior.
Parent's complete frontend test run passed 107 tests. This is source testing,
not visual/on-device or root-backed message acceptance.

The scheduler now returns before private queue scans while public work is active;
the mixed-queue regression preserves the private queued message. Personal SAGE's
onboarding startup no longer requires LOCAL_VOICE_READY: it requires lock/app
installation and independently initialized SAGE files; voice readiness remains
separate. Parent combined onboarding/installer/provision/public-provider tests
passed 67 tests and 20 subtests. Independent review and native first boot remain
outstanding. No file-presence check is represented as encryption or identity proof.

Root read IPC plus domain tests passed 31 tests. The actual service adapter is
now implemented against that IPC. Parent added `/community/messages/rendered`
to voice ingress mutation tracking so active/uncertain receipt submissions prevent
false clean drain; voice-community tests passed 20 tests and 12 subtests. This
establishes protected queueing, not automatic RF receipt dispatch or physical read.
Runtime completeness now requires mesh_read_root and mesh_public_root as well;
18 release tests and 94 subtests pass, installed-environment test still skipped.

Independent onboarding review identified an additional listener configuration
gap: nonempty SAGE config/identity files do not prove loopback-only listeners.
Leibniz is implementing pre-start validation or explicit listener restrictions.
No startup/deployment authorization follows from file-presence checks alone.

REST/RPC/P2P/TLS loopback overrides are now in the personal-SAGE source unit.
Federation/quorum have separate configuration paths: their onboarding exposure
must also be enforced, not merely documented as disabled. Leibniz owns that fix.
Parent combined public-root/read-service/onboarding tests passed 61 tests.

Public root composition still needs a real current gateway contact verifier and
an approval-state transition check through the actual facade reservation/poll
path. Parent flagged both to the root/provider owners; an always-false callback
is not completed integration, and replacing it with always-true is not valid.
Gateway-side fragment/bridge/response-worker composition is separately assigned.
The unchanged real four-node acceptance runner has advanced to `upgrade_9`.

## Native read-only checkpoint

Authenticated SSH to Unit A succeeded. Kernel is `6.1.84-8-rk2410`, systemd is
`252.39-1~deb12u2`; boot remains `7fc2a334-26e4-48e8-b585-42e1072c4346`.
Voice, personal SAGE and device-auth services reported active. No service was
restarted, no private records/keys were read and no radio was opened.

A non-root synthetic native probe verified SO_PEERCRED on a Unix socketpair
matches the actual process PID/UID/GID and that memfd accepts/reports all four
write/shrink/grow/seal seals. The probe closed its descriptors and sockets.
This establishes available Linux primitives only, not cross-UID root admission,
systemd confinement, complete mesh IPC authorization or radio acceptance.

## Enforced private listener policy

Personal-SAGE source now sets SAGE_LANTERN_PRIVATE_LISTENERS=1 and requires the
new check-lantern-private-config command before startup. Configuration loading
also enforces the policy during serve: explicit YAML federation/quorum false,
and fixed effective loopback REST/TLS/RPC/P2P addresses. This is not dependent
only on an earlier prestart check. Older binaries cannot satisfy the required
command, so a matched new binary remains necessary before native installation.

Parent offline Go TestLanternPrivateListener tests passed. Parent onboarding and
installer tests passed 37 tests and 14 subtests. Independent source review and
actual listening-address/native-startup verification remain required. No SAGE
binary was rebuilt or deployed for this policy in this checkpoint.

Independent source review now clears the private listener-policy gap: enforcement
occurs after environment overrides and again through serve configuration loading.
Parent public service/facade contract tests passed 12 tests. Their in-process
contract peer uses real protected preparation/root queue code but is not the
actual daemon high-level RPC dispatch; do not count it as deployed end-to-end
public sharing. Actual root routes/current gateway contact and receipt dispatch
integration remain under implementation.

Parent gateway-runtime tests passed 21 tests, covering synthetic LP1 reassembly,
bridge forwarding, response queue/restart, uncertainty, expiry, demultiplexing
and owner join. Serial/HTTP are synthetic: this is not a native gateway deployment.
The required release inventory now includes public_gateway_runtime; 18 release
tests and 95 subtests pass, installed-environment test remains skipped.

Hooke confirmed a real public-state transition bug with a failing regression:
after a gateway receipt is archived, root's approval predicate rejects polling.
Initial HTTP reservation does not prevent initial dispatch. Socrates must separate
retained signed consent from operation outcome and allow exact-receipt polling
without authorizing uncertain POST resubmission. The failure is not skipped or
treated as an accepted public workflow.

Independent-key initialization now has an explicit guarded identity phase using
SAGE's existing genesis generator. It does not start services or enroll the public
key. Parent identity-preflight/provision tests passed 12 tests and 9 subtests.
These orchestration tests use injected generation; real cryptographic/genesis
integration in an isolated temporary test home is requested before claiming the
initializer proven. No Unit A identity or key was changed.

Lorentz's sparse-index sync error now poisons the enclosing transaction even
when failure occurs before index writes; re-review is requested. Actual governed
activation/migration/export remains separate unfinished work, not a proof endpoint.

Parent independently reran the exact archived-receipt polling regression: it now
passes against current root source. It still uses the in-process service contract
peer, not deployed daemon RPC or RF. Full root composition freeze remains pending.
Parent also ran offline Go TestPublicMemoryIndex tests successfully against
temporary stores, confirming the reviewed rollback/index slice. No governed
activation, public acceptance proof, or native release follows from these checks.

Parent public service/root regression set now passes 38 tests. Root's five public
RPCs and archived-receipt polling are implemented, but fresh contact-probe actual
scheduler/gateway routing remains unfinished. Receipt background dispatch now
reaches synthetic serial acceptance; its subsequent drain regression fails and
blocks release until corrected. No uncertainty bypass or skipped test is allowed.

The user selected `sage.ngo` as the public foundation/Lantern home; intent is
recorded in FOUNDATION_PUBLIC_HOME.md. No DNS, hosting, publication or private
service exposure was performed. Unit A/B engineering remains the active goal.

Auto-review rejected the proposed v28 consensus wiring because of chain-divergence
risk. The rejected patch was not applied; the source ceiling remains v27.
User approval was explicitly requested for source-only implementation/testing,
independent review and no build/deployment/activation. Do not bypass that rejection
through another agent or tool. Other Unit A work can continue independently.

Parent generated-protected-config/identity-preflight tests passed 7 tests and
9 subtests. The exact receipt worker/drain regression is running as session
49067; no result or drain success is claimed yet.

That exact receipt worker run subsequently failed: HardwareRuntime.close(5)
returned false after synthetic dispatch. Source changed during execution, so the
printed assertion line is not a stable snapshot; the failure was reported to the
owner for diagnosis and a frozen rerun. Session 49067 is terminal, not a live wait.

The completed identity-config phase now binds actual initializer output into
protected per-unit configuration. Parent combined generated-config/provision/
identity tests passed 17 tests and 15 subtests; independent security review is
requested. This does not certify native first boot or public enrollment.

Hooke reports clean drain after actual service/socket/root worker synthetic
receipt dispatch in the new background receipt tests. Parent is independently
rerunning both that file and the original failing regression in session 37272;
the process is confirmed live, result still pending. Do not replace the original
failure evidence with a pass until that exact rerun completes.

The gateway composition module is now required by runtime completeness checks;
parent release tests passed 18 tests and 97 subtests (installed-environment test
skipped). Existing scratch wheels predate these modules and are not release
candidates. A separate matched-update path for already provisioned Unit A is
under investigation; the fresh installer must not be forced onto Unit A or its
existing identity/state discarded to satisfy a fresh-install guard.

Receipt rerun session 37272 completed successfully: 12 tests passed in 154.36s,
including the original worker/clean-drain regression and the background receipt
cases. This replaces that specific failing snapshot with passing current-source
evidence, not native RF/read or deployed shutdown acceptance.

Parent independently verified the frozen gateway composition/runtime/contact
suite: 57 tests passed in 60.08s with runtime and matched SDK source on PYTHONPATH.
The initial invocation used the globally installed incompatible SDK and failed
fixture setup (`trust_env` unsupported); no product workaround was applied.
LP1/LC1 shared scheduler integration is source-tested, not native radio accepted.
Daemon role configuration and positively supported lease budgets remain open.

Independent commissioning review found two release blockers: partial config
replacement lacks a durable app-install admission fence/completion receipt, and
predictable installed-template/backup conflicts are checked after key generation.
Leibniz is implementing preflight checks and durable commissioning containment;
no fresh installer will be forced onto existing Unit A. Gateway integration is
under independent review while Socrates handles remaining root composition and
aggregate deployment-drain authority. No deployment occurred.

The existing four-node acceptance process (session 45428) was positively polled
live and advanced to upgrade_11. It remains unfinished; no restart or acceptance
claim follows from that progress. The prior domain-only reply was no engineering
progress; this continuation restores test evidence and actionable review work.

Gateway independent review now blocks activation on two actual timing issues:
60-second contact freshness is rechecked inside a 120-second admitted exchange,
and gateway maximum response reservation disagrees with client bounded response
preflight after outbound airtime has elapsed. Hooke owns request-bound contact
evidence (with revocation preserved); Beauvoir owns shared response bounds and
gateway calculation. Neither deadline extension nor uncertain retry is approved.

Parent release verification now requires the new mesh_gateway_root module and
SageClient constructor trust_env support, preventing silent omission or the SDK
constructor mismatch observed in the previous test invocation. Release tests:
18 passed, 100 subtests passed, installed-environment test skipped. The actual
matched SDK source AST also passes all required API checks without constructing
a client. No wheel promotion, native activation, or deployment is implied.
Acceptance session 45428 was polled live again at upgrade_11, not restarted.

Commissioning fixes are now implemented: an exclusive durable pending receipt
precedes initialization; deterministic TOML/backup conflicts fail preflight;
completion binds reviewed inputs and hashes of all four generated outputs.
Fresh app admission checks completion both before native preflight and before
installation, rejecting pending/missing/mismatched state. Parent current-source
verification passed 54 tests and 37 subtests across provisioning, identity
preflight, generated configuration, and fresh app installation. This includes
failure injection around replacement/fsync and retained admission fencing.
Independent rereview remains pending; these synthetic files are not actual
Unit B commissioning or a power-cut test on its filesystem.

Leibniz resumes the separate existing-appliance update path while Socrates
completes aggregate deployment-drain authority. Artifact staging/journaling must
not imply permission to switch files without positive native drains; no keys,
databases, or uncertain queue state may be reset. Unit A remains unchanged.

Independent commissioning rereview clears both original source blockers after
76 tests and 61 subtests; native filesystem power-loss behavior remains untested.
Parent separately ran actual daemon gateway-role composition tests: 18 passed,
covering explicit role startup, drain, evidence rejection before USB, bounded
extended lease, and mocked process/listener/stop-budget checks. These use synthetic
identity/transport/backend/systemd evidence and do not prove native operation.

Gateway response-budget correction is frozen: the shared protocol helper bounds
valid response size instead of reserving generic transport capacity. Parent
protocol/gateway-runtime suite passed 55 tests in 15.52s, including elapsed
outbound airtime. The reviewer example is 50 request and 32 response frames:
97.82s total at 0.7s/frame; gateway reserves 62.56s of the remaining 85s. The
120-second deadline is unchanged. Provider adoption and request-bound contact
freshness remain Hooke's integration work; root role review remains pending.

Parent contact/provider verification now passes 63 tests in 23.67s, covering
request-bound freshness, revocation/replay/deadline behavior and the shared
response bound. Root must pass contact_admission=self.probe; this was relayed to
Socrates rather than assuming standalone tests establish production wiring.

Independent root review found two activation blockers: cross-UID backend procfs
listener ownership inspection lacks the necessary native authority under the
current service confinement, and gateway jobs must bind the exact admitted
unlock epoch rather than merely checking that some current epoch exists. A
lock/unlock between checks must revoke an old job while retaining the separate
storage lifetime needed to persist captured data. Socrates owns these fixes;
no relaxation of listener checks or native activation is authorized by tests.
The real consensus process was positively polled live at upgrade_11 again.

Existing-appliance artifact staging is now frozen in stage-appliance-update.py,
separate from fresh commissioning. Parent ran 9 tests and 2 subtests, adding an
unmocked canonical-verifier rejection case for synthetic candidate/rollback
inputs and mismatched manifest hashes. Other staging cases exercise sealed
copies, journal ordering, tampering and failure retention with fixture manifests.
This does not establish a matched-release happy path or installed rollback
baseline. Independent staging review is requested. Aggregate drain authority and
activation remain unimplemented; staging never authorizes service/file changes.

Consensus session 45428 remains positively live and has advanced to upgrade_12.
No new process, chain reset, native deployment or acceptance claim was made.

New root integration is not passing yet. Parent combined public/gateway root
tests (session 10727) ended with 7 failures and 43 passes while source was being
edited: two public IPC admissions failed, a renamed backend helper broke one
test call, and four unsafe gateway pre-open progress cases were not rejected.
The exact failures were returned to Socrates for diagnosis and a frozen rerun;
earlier narrower passes do not clear these failures. Gateway evidence is now
required in release closure: 18 tests and 101 subtests passed, installed check
skipped. No candidate rebuild or deployment occurred.

Independent staging review found lexical '..' live-path exclusion bypass and
separately read manifest snapshots not bound to the same parsed inventory.
Leibniz owns the corrective tests and fixes. Staging review is not cleared by
the earlier nine passing tests. The consensus process is still live at upgrade_12.

Parent added test_gateway_evidence_contract.py: 16 tests pass over actual Unix
socketpair framing with explicitly simulated peer credentials/configuration.
Coverage rejects non-root callers before inspection, malformed/arbitrary-target
requests, mismatched configuration hashes, configuration changes during inspection,
and truthy-but-not-True results; successful responses bind nonce and config hash.
These tests do not prove Linux credentials, CAP_SYS_PTRACE confinement, backend
access or native deployment. Previously reported root integration failures remain
open pending the owner's frozen correction; the narrow helper pass does not
supersede them. Existing consensus session 45428 was again positively polled live.

Staging path/manifest corrections are frozen and parent verification now passes
14 tests and 10 subtests. Paths reject parent traversal before filesystem access;
the verified manifest object, saved bytes, selection and sealed inventory are
bound to the same strict parsed snapshot. Independent rereview is requested.

Helper review adds unresolved production requirements: socket readiness must
precede mesh admission; evidence IPC needs an overall monotonic deadline rather
than only per-read timeout; listener ownership must be rechecked after inspection.
CAP_SYS_PTRACE/DAC privileges also make this a trusted privileged process capable
of reaching secrets through process memory; masking vault paths is not proof of
secret isolation. Socrates received these findings. No native activation claim.

Staging independent rereview clears the two reported source blockers after
14 tests and 10 subtests. Matched happy-release compatibility is still unproven.
The new deployment-drain controller is work in progress: parent ran its initial
eight synthetic tests successfully, but these use a fake aggregate appliance and
do not prove native service drains, manifest/rollback binding or single-use apply
authority. Those missing production requirements were explicitly relayed to
Beauvoir before API freeze; no updater may consume this incomplete controller as
deployment permission. The original root integration failures remain open until
a frozen rerun. Consensus session 45428 was positively polled live at upgrade_12.

Parent root rerun session 68136 completed: 67 tests passed in 55.26s across
public-root, gateway-root and evidence-contract suites. The originally reported
seven failing cases now pass in this current-source run. This supersedes those
specific test failures, not native acceptance or pending independent review.
Parent additionally added deadline-boundary tests: incremental reads cannot reset
the deadline, and recv/send finishing at expiry cannot return success. All 19
helper contract tests pass. Readiness/deadline implementation and helper service
inclusion in canonical staging/installation are present; source review remains
requested. No deployment or real cross-UID capability test was performed.

Independent helper rereview clears its three scoped findings: notify readiness
follows listen, monotonic IPC budget rejects late responses, and listener/FD/PID
ownership is rechecked. Filesystem inspection is not preempted; evidence is a
snapshot, not an atomic guarantee about a later HTTP connection. No native proof.

Parent installer/stager/mesh-preparer/release suite completed with 101 tests and
191 subtests passing; installed-environment check skipped. The new deployment
controller now exposes manifest-bound source API, but its old eight fixtures
currently error on new required constructor parameters while the owner updates
tests. Parent will require the frozen API rerun rather than report the previous
eight passes as current acceptance. Real consensus session 45428 remains live.

Updated deployment-drain fixtures now pass 22 tests; this supersedes the eight
old-constructor setup errors, not production authority acceptance. Parent also
ran suspend-system and release gates: 50 tests and 135 subtests passed, installed
environment check skipped. deployment_drain is now required in wheel/source
closure, including rejection when omitted from both. Independent review of
manifest/broker/service binding, model stop and single-use mutation authority is
requested. The real broker-verification composition and apply consumer must still
be completed rather than leaving permissive injected callbacks.

The existing real consensus session 45428 was positively polled live and advanced
to upgrade_13. No restart, new chain or deployment occurred.

Session recovery changes the live-state evidence: agent handles now return
not_found and runner handle 45428 is unknown. An approved host process inventory
found no run-public-acceptance process. The log stops at upgrade_13 without a
completion result; it must no longer be described as running or accepted.
Container/chain recovery remains to be inspected before any restart decision.
Current-source deployment-drain/staging rerun passed 36 tests and 10 subtests.
This does not complete missing apply/broker integration or native deployment.

Read-only Docker recovery inspection confirms all four original isolated nodes
survived the lost runner. Each reports height 1451, catching_up=false and AppHash
2D3E783AA7A2473F7564AECC4E388BE1A72DD5DBB176AEB47F42E852BBC02679.
The orchestration process is absent, not the chain. Current launcher explicitly
requires a fresh workspace for run and has no resume path; do not invoke fresh
initialization against retained nodes. No container or state was changed.
This agreement proves only the observed chain snapshot, not the unfinished
enrollment/public acceptance phases. A replacement read-only reviewer (Curie)
is inspecting deployment authority while parent investigates safe runner recovery.

Further read-only recovery shows all nodes on app-v12 at height1451. Governance
reports an existing app-v13 ballot in voting state, not a pending activation plan.
Do not submit a duplicate upgrade; recovery must reconcile that existing ballot.
Curie's deployment review found the broker verification hook absent from daemon
dispatch, missing LLM sleep-fence configuration, and no safe apply consumer.
Parent wired verify-deployment-drain to the existing retained-proof verifier with
the daemon's kernel-derived UID. Focused tests are running as session29996;
actual route coverage and the other two blockers remain to be completed.

Session29996 completed with 55 tests passing. Parent then added actual daemon
dispatch tests using socketpair framing and retained supervisor objects: valid
proof roundtrip, non-root denial, missing/copied proof rejection and resumed
phase rejection. Broker suite now passes 21 tests. Peer UID is injected in these
tests; this is not native credential/confinement acceptance. The LLM sleep-fence
is still absent from both base unit and generated fenced-service list, so source
deployment preparation remains incomplete even with the broker route fixed.

Parent added the LLM sleep-fence condition to the base systemd unit and included
LLM in the idle-suspend installer's generated fence drop-ins. This prevents a new
model start while the shared sleep/deployment fence exists; it does not stop an
already running model or replace invocation-bound drain/stop checks. Installer,
deployment-stop, fresh-app and release-staging tests passed 77 tests and 90
subtests. Source only: existing Unit A's effective systemd configuration remains
unchanged, and safe apply/restart authority is still unfinished.

Curie now owns the concrete apply consumer (new script/tests and update guide),
using the staged release and live manifest-bound drain rather than a fake gate.
Parent added drain-ordering regressions: invalid auth receipt prevents broker
drain, failed aggregate quiescence prevents model stop, and uncertain model stop
cannot publish prepared authority. Combined drain/broker/system suites pass 64
tests. These exercise source orchestration with synthetic service evidence, not
actual Unit A stop/deployment or a verified installed rollback baseline.

Parent acquisition regressions now verify failed manifest binding creates no
deployment marker, an existing marker is never overwritten, and failed idle
service stop prevents journal/appliance acquisition while retaining the marker.
All 28 deployment-drain tests pass. These use temporary paths and simulated
service/credential results, not actual root service operations. Apply owner was
explicitly reminded that old Unit A lacks the new broker verification operation
and LLM fence: candidate APIs cannot be assumed installed before an update.
Any bootstrap path must prove old-generation drain, not weaken these gates.

User explicitly authorized wiping Unit A Lantern data and reprovisioning fresh,
accepting loss of history/photos/messages and old identity. This supersedes the
requirement to preserve Unit A test data via the legacy updater, not security or
public consensus verification. Parent verified authenticated target hostname
sage-lantern-a, aarch64 Debian12, boot7fc2a334-26e4-48e8-b585-42e1072c4346,
400GiB available and active voice/personal-SAGE/auth services. No reset performed
yet: replacement release must pass current-source checks before destructive work.
OS/network access and unrelated repositories/test-chain state are outside the
authorized Lantern data reset. Curie redirected from legacy apply to fresh
release readiness. Inbox reconciliation notice explicitly declined without
changing task ownership/status; no private progress was sent in that reply.

Fresh release progress: rebuilt runtime twice with pinned offline build tools in
/private/tmp/lantern-fresh-runtime-20260906-a and -b. Both wheels are byte-identical,
SHA256 38727908568e17d74b373073db269c31f8eeb22dbac68263f84c4f44561122e2,
with 88 manifest source entries and snapshot
149844a6e83b4d85672fb2d745ae097ea140bf232e6913bb1a32b5a156c8a6e8.
Both pass current source/wheel verification. Not promoted into the old mixed
bundle. Native SAGE source review, ARM64 UI dependencies/build and canonical
allowlisted release assembly remain required before the authorized Unit A reset.

Fresh SDK rebuilt offline from current source into
/private/tmp/lantern-fresh-sdk-20260906-a. SHA256
93b9708ec81f054f6495adc17c03aab60efda77ef4163a5653f5a0632879d455
matches the earlier independently built SDK bytes. This verifies the SDK source
has not drifted and supplies a fresh checkpoint; no old stack lock was relabeled
or bypassed. Native SAGE snapshot review is still running with Curie. No reset.

Current frontend source passes all107 tests and LANTERN_EDGE_LOCAL=1 production
build on the host. This is macOS build evidence, not Linux ARM64 dependency or
device acceptance. No hosted site publication performed; target remains local.
Native source review found ungated experimental public-memory exclusions in
historical Badger hashes despite ceilingv27. Those unapproved changes will not
be shipped. Curie is preparing a separate allowlisted source snapshot retaining
private-device fixes while restoring recorded Badger and omitting three unused
public-memory experimental files, with explicit deviation provenance. No source
in the original SAGE checkout is reverted; no experimental binary built.

Prepared isolated Linux ARM64 UI dependencies using package-lock npm ci with
scripts/bin links disabled. Offline cache installed492 packages but omitted native
optional packages; approved registry access installed517 and esbuild is verified
ELF AArch64. Host-built dist/public were copied for inventory validation only.
Canonical UI gate rejects legitimate locked dependency source paths under
miniflare/dist/src/workers/queues due to generic state-directory exclusion.
Do not remove required dependency files or blindly relax secret checks: a scoped
packaged-code rule and negative tests are needed. This is not native UI execution.

Packaging now allows only the exact Miniflare queues directory and its two
shipped broker.worker.js/map paths (standalone and canonical app prefix).
Real state/key/env files and arbitrary additional paths remain denied; manifest/
stager tests pass28 tests109subtests. Full real UI inventory now reaches the next
check, exposing two dependency-classification errors: children of excluded
other-platform optional parents are incorrectly required, and resolve's bundled
test fixture node_modules/util/index.js is mistaken for an installed package.
No required dependency was dropped; full inventory remains unaccepted pending
correct graph and package-root handling with regressions.

Parent corrected nested dependency applicability to include parent platform
selectors and package-root recognition to treat nested test fixtures as owning
package contents. Stager tests pass19 tests76subtests; the real UI inventory now
passes31,128 files and517 Linux ARM64 packages. All files remain hashed; this is
not target execution. Isolated v27 source audit checksum verified as
c64824699fb61188827621d4e652223808617b23f68542fbfe6addd7c44b5498;
native cross-build started from that757-input snapshot, not mixed live checkout.

Both native offline builds completed and cmp confirms identical ELF AArch64
bytes: bcd8407d23be4b84e3f5a605a5fd05fef57b717ae99191b43140b50845efdb06,
version lantern-local.20260906-fresh-v27. Build inventory matches reviewed isolated
snapshot1377b50346273805fac362ff5b20acd669ea93109332455af03835ee420caa1f.
Curie is assembling the separate canonical allowlisted artifact root with these
native bytes, rebuilt runtime/SDK, original SDK sources, selected caches and
verified ARM64 UI inventory. No binary run on Unit A and no reset yet.

Subsequent native smoke check: authenticated read-only inventory confirms Unit A
is reachable with unchanged boot ID 7fc2a334-26e4-48e8-b585-42e1072c4346,
Node v22.22.0, active existing camera/auth/personal-SAGE/voice services, Espressif
radio and Logitech C270 present. Uploaded only the new binary to the operator's
temporary path /tmp/lantern-fresh-v27-bcd8407d-sage-gui. Target sha256sum matches
bcd8407d23be4b84e3f5a605a5fd05fef57b717ae99191b43140b50845efdb06.
Its stateless version command executes successfully on real ARM64 hardware:
lantern-local.20260906-fresh-v27, source-1377b50346273805, max-app-v27.
This proves executable compatibility/version only, not daemon startup, identity,
encrypted storage or public workflow acceptance. No installed file replaced,
service changed, data erased or private content read. Canonical release assembly
remains pending with Curie; its live handle returned an observation timeout,
not completion or failure. The previous status-only turn was no progress; this
turn supplies new target execution evidence without restarting that worker.
