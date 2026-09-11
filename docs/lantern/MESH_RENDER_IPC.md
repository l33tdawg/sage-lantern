# Root render challenge / explicit read receipt IPC

Source-only composition; no native activation or browser route deployment.
Beauvoir owns service/UI routing. Root owns these facade methods:

```
render_challenge(id, *, rendered, access_check)
    -> {id, challenge_id, expires_at}
rendered(id, *, challenge_id, rendered, share_read_receipt, access_check, on_submit)
    -> {id, receipt_id, state: "queued"}
```

`rendered` must be literal true for both calls; the second additionally requires
literal `share_read_receipt=true`. No integer/truthy substitutions. Only a
verified inbox mailbox ID is eligible. Fetch/list never writes read consent.
The first call verifies the actual historical inbox message and exact current
sender contact, then issues an opaque 60-second challenge without durable writes.

Root derives the session binding from its broker ID, kernel-verified voice PID
and UID, and independently checked device access epoch. No HTTP field chooses
these bindings. The challenge binds that session and exact mailbox ID; a different
message, restart, lock/reunlock, expiry or another challenge is rejected. The
existing IPC repeatedly challenges the original HTTP access guard, including
before returning a result. This is an authenticated render assertion, not proof
that pixels were visible or a human read them.

The acknowledgement uses the existing `MeshReadReceipts` domain helper to commit
an actor-signed immutable protected consent record before queueing a sealed read
receipt through the real `MeshReceiptSender`. Root constructs the exact privacy
and render bindings in a temporary runtime context; unrelated operations cannot
borrow it. `on_submit` marks possible mutation before IPC submission. Lost replies
do not authorize retries, replacement approvals or new receipt IDs.

These routes return durable **queued** only. Root now retains bounded dispatch
permission for the exact receipt, supervised voice process, broker and access
epoch, independent of the completed HTTP request. The existing background
receipt sender rechecks signed consent and current contact. Lock/epoch change
denies dispatch; restart does not recreate permission. A synthetic real-worker
test reaches serial acceptance and positive drain. Serial acceptance is not RF
delivery or evidence of human reading.

## Drain and recovery

HardwareRuntime's existing serialization covers challenge/ACK/queue admission.
Close must acquire that mutex, pause/join background work, then positively
close/join the domain helper before closing the runtime. Graceful pause stops
new admissions without revoking an admitted response verification; live
process/epoch/policy and lifecycle checks still apply throughout the join.
Unknown consent writes retain helper uncertainty and deny
clean drain; unknown receipt writes retain sender uncertainty. A timeout never
releases the supervisor owner or SDK dependencies.

Startup reconciliation and runtime close now recognize only the exact signed
`sage.lantern.mesh-read-consent.v1` schema. The dedicated verifier checks revision,
actor, deterministic row ID, signature, explicit booleans, bounded timestamps and
the exact attested historical original. Expired but intact consent may be safe
to drain; it does not become authority to send. Unknown/forged schemas still fail
closed. No generic skip was added. Recovery/resend is not exposed through IPC.

Tests use real Unix IPC, protected SDK MockTransport, real inbox attestations,
domain consent and receipt queueing. They cover wrong message/challenge/epoch,
explicit consent, duplicate ACK, expired challenge, corrupted consent, lost write
acknowledgment, and shutdown/lock races. No physical radio or display assertion
is inferred from these synthetic tests.
