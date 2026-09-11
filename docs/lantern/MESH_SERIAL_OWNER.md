# Exclusive serial owner — source-only

2026-09-06. Implemented in `software/runtime/src/sage_lantern/mesh_serial_owner.py`.
No deployment, USB opening, RF transmission, or hardware validation performed.

## Initial-open gate

The existing `SerialFDStream` borrows an already-configured FD; it does not open
devices. The inspected runtime/scripts have no production MeshCore serial owner
or periodic serial poller to reuse; interoperability fixtures use synthetic
streams. This source inspection does not establish absence of native services,
ad hoc terminal sessions, ModemManager, or other pre-existing device openers.
The operator must inventory and stop competing users before ownership handoff.

Opening a TTY can change modem signals before userspace termios configuration.
This implementation therefore **does not provide a USB opener**. Safe initial
open, configuration and close behavior must be validated for the particular
driver/radio before enabling a daemon. No promise of electrically unchanged
DTR/RTS is made. There are no explicit modem-control, reset, flush, reopen, or
retry commands. HUPCL is cleared, but this is not hardware proof.

## Runtime factory and API

- `RadioBinding.from_inventory(document, unit_id=..., expected_fingerprint=...)`
  consumes the existing `sage.lantern.meshcore-radio-public.v1` public inventory.
  A trusted operator supplies the independently verified unit and fingerprint;
  matching documents alone do not prove verification. Browser claims are not
  provisioning authority. The stable `/dev/serial/by-id/...` path, serial,
  VID/PID, and radio public key are required; no friendly-name derivation.
- `adopt_serial_fd(fd, binding=..., lock_directory=..., timeout=3)` is Linux/root
  only and **consumes the donated FD even on failure**. No duplicate/inherited
  handles may remain. It compares the FD and stable link target and checks USB
  serial/VID/PID through sysfs. It takes nonblocking flocks on a root-owned 0600
  lock file in an operator-prepared 0700 directory and on the TTY, plus TIOCEXCL.
  It configures nonblocking raw 115200 8N1, no flow control, validates via
  `SerialFDStream`, and completes the existing pinned-firmware/public-key
  `MeshCoreClient.handshake` on its worker before returning.
- `owner.submit(operation) -> Future` runs a trusted bounded callback with the
  real `MeshCoreClient`, preserving existing exact-type checks. Submit a whole
  outbox/receipt/inbox producer operation, including its workflow persistence.
  Send, receive, contacts and ACK operations consequently share one FIFO worker.
  There is no background polling or automatic send. Default pending bound is
  32 (maximum configurable 128); rejection occurs before admission.
- `with owner.producer_fence(timeout=5) as lease:` blocks new admissions and
  waits for all admitted jobs to finish, holding the gate for the context.
  `lease()` checks thread-bound lifetime, closing state, queue and active job.
  Pass `owner.producer_fence` to `MeshMailbox` only when **all** producers for
  that actor use this owner, including enqueue/receipt updates not involving RF.
- `owner.close(timeout=5) -> bool` closes admissions, drains admitted work,
  waits for fence holders, closes owned descriptors, and joins the worker.
  Only actual joined/released completion returns True. Timeout returns False
  with ownership retained until work exits; fence timeout similarly fails
  closed. No shutdown claim is inferred from an idle queue alone.

For example, the trusted daemon can submit
`lambda radio: outbox.dispatch_one(message_id, device=radio, radio_timestamp=stamp, access_check=check)`.
It must not submit just the inner serial send while writing journals outside
the scheduler. Never re-enter submit/fence or wait on another owner job from a
worker callback. Callbacks must not spawn detached producers or retain the client.

## Boundaries and recovery

The lock/TIOCEXCL prevents cooperating second owners and ordinary later openers,
not pre-existing FDs, privileged bypasses, alternate databases, or direct API
writers. It does not alone establish a cross-process actor journal fence. Only
the owning daemon may run those producers; other processes must dispatch to it.
No daemon IPC or systemd wiring is added in this change.

A Future wait timeout is not cancellation, evidence of no transmission, or
permission to retry. Cancellation before execution prevents the job; once
running, the existing MeshCore uncertainty/poisoning semantics are preserved.
Unknown outbox states must remain available for reconciliation. The scheduler
does not invent receipt/delivery success, reset a poisoned client, or release
ownership while a hung callback may still write. Suspend must require a genuine
successful close and separately verified process/cgroup shutdown.

Tests use synthetic jobs, mocked FD/sysfs/configuration, and an ordinary pipe
for rejection cleanup; no serial ports or live services are opened. Real USB
signal behavior, sysfs binding on each unit, competing-process inventory and
root daemon integration remain acceptance gates.
