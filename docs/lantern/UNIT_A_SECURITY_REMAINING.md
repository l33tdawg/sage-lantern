# Unit A security: remaining acceptance and closure

Status: source-only inventory, 2026-09-05. No Unit A connection, secret-file
inspection, key operation, deployment or OS configuration change was performed.
Checked-in units and scripts are not proof of effective installed configuration.
Earlier application lifecycle review and reported test passes do not establish
OS isolation, at-rest confidentiality, or resistance to an administrator.

## Protection boundary

The numeric credential authenticates a **Lantern private-access session** using
the existing SAGE vault passphrase. Pending activation, capability checks,
presence expiry and idle expiry protect cooperating application entry points.
They are not an OS login lock, disk encryption, or vault-key unloading.

SAGE dashboard logout invalidates a session without unloading the vault:
`../sage/web/handler.go:1973`. A privileged operator, a process with equivalent
filesystem authority, or an offline reader of unencrypted storage is outside
that application boundary. Never describe the current system as protecting all
stored photos, keys or conversations from device theft or root access.

The closure items below are acceptance work, not authorization to perform live
changes. Use synthetic content and redacted evidence; do not collect PINs,
recovery keys, private keys, bearer tokens, real photographs or user text.

## P0 — Close before asserting unattended private-access protection

### 1. Prove the installed boundary, including rollback and reboot

Owner: release operator with parent integration owner.

- Verify the deployed artifact manifest and effective unit/drop-in configuration,
  not only repository files. Record versions and permission results, not secrets.
- Confirm every private listener, physical PTT path and stream checks the shared
  gate; test locked access, expired presence, idle expiry, daemon failure, reboot,
  browser crash and BFCache restoration with synthetic data.
- Confirm the nonsensitive root-owned 0644 flag is
  `/etc/sage-lantern-device-lock.enabled`. Ordinary users must not remove it:
  safe absence deliberately selects legacy access, not locked mode.
- Keep camera private configuration inaccessible. The flag must remain readable
  outside that directory. Do not relax the camera sandbox to make gating work.
- Any rollback to a backend without gating must stop/isolate private services,
  not leave a lock-screen UI covering an accessible old backend.

Evidence anchors: `software/runtime/src/sage_lantern/device_access.py:1`,
`software/systemd/sage-lantern-camera.service:33`,
`scripts/lantern/install-unit-a-private-access.sh:19`,
`scripts/lantern/install-unit-a-private-access.sh:166`.

Acceptance: a release-specific matrix records denied private reads/capture and
bounded cancellation after revocation. No private content appears in evidence.
The six-second presence lease is a bound from the last accepted renewal, not a
promise of instant cancellation when the browser disappears.

### 2. Separate kiosk access from OS/operator authority

Owner: Unit A OS administrator.

Source configures automatic login as `radxa`, and launches Chromium in kiosk
mode with dev tools disabled. These are UI measures, not proof that shell,
virtual terminal, desktop escape, file dialogs or service modification are
inaccessible: `scripts/lantern/configure-kiosk-login.py:34`,
`scripts/lantern/sage-lantern-kiosk:31`.

- Inventory effective kiosk groups, sudo/polkit permissions, device access and
  writable executable/service/browser-policy paths. Do not infer that the
  auto-login account is unprivileged; current grants were not inspected.
- Test physical keyboard shortcuts, VT switching, browser exit/restart,
  alternate desktop sessions and removable-media launch paths while locked.
- Remove unattended elevation and access to private service files from the kiosk
  account, or explicitly block unattended/private-data acceptance until a
  dedicated restricted kiosk account and operator recovery procedure exist.
- Verify kiosk users cannot replace the served UI, alter trusted origins,
  reconfigure the daemon, or disable the marker. A compromised trusted-origin
  UI can capture a PIN; CORS cannot protect against its own application code.

Acceptance: the unprivileged kiosk account cannot become the service account or
root, read private storage, replace privileged code, or open an authenticated
operator control surface. Test separately from the legitimate operator account.

### 3. Inventory SSH and alternate administration explicitly

Owner: Unit A OS/network administrator.

Effective sshd policy, sudo grants, port forwards, debug listeners and management
credentials are **unverified**, not asserted vulnerable or disabled here.

- Enumerate enabled remote administration and listener bindings, including
  sshd, SAGE/dashboard, model endpoints and management services. Record only
  policy/binding summaries; do not dump authorized-key contents or credentials.
- Decide whether SSH is required for the premise test. If retained, restrict it
  to designated operators and management reachability; review password/root
  login, forwarding, alternate accounts and escalation permissions.
- Treat an SSH shell or loopback tunnel as a potential bypass of network
  locality assumptions. Test whether it reaches operator interfaces or raw
  storage independently of the Lantern token. Loopback is not human identity.
- Keep operator recovery available through an explicit controlled procedure;
  never disable the last administrative path without tested recovery.

Acceptance: no participant/kiosk credential provides an administrative bypass.
Authorized operators remain trusted and can bypass the app lock by design;
document that fact rather than claiming PIN isolation from those operators.

### 4. Resolve the shared-service-UID boundary

Owner: OS/runtime maintainers.

Auth, voice, status and personal SAGE use `sage-lantern`; auth is configured with
a current-Root signing-key path. File mode 0600 does not isolate processes with
that same UID. Read-only filesystem mounts do not prohibit reading credentials.
Camera already uses a distinct dynamic user; preserve that isolation.

Anchors: `software/systemd/sage-lantern-device-auth.service:10`,
`software/systemd/sage-lantern-device-auth.service:12`,
`software/systemd/sage-lantern-voice-loop.service:10`,
`software/systemd/sage-lantern-status.service:7`,
`software/systemd/sage-lantern-personal-sage.service:8`,
`software/systemd/sage-lantern-camera.service:7`.

Acceptance: either separate identities and filesystem/IPC permissions so a
compromised low-trust service cannot read Root keys/private stores, or explicitly
limit the release threat model to uncompromised trusted local services. The
latter is not closure for a claim of service-compromise resistance.

## P0 for theft/storage confidentiality; otherwise explicit scope exclusions

### 5. Photos and public-candidate outbox are not vault-encrypted

Owner: storage/privacy maintainer; product owner approves retention decisions.

| Surface | Source fact | Required closure evidence |
| --- | --- | --- |
| Captured photos | Version/digest wrapper contains unchanged JPEG; permissions and checksum are not encryption. `software/runtime/src/sage_lantern/history_media.py:1` | Encrypt originals and derivatives with an explicit key lifecycle, or protect the entire relevant storage volume for a powered-off theft threat. Verify synthetic media cannot be decoded from the protected offline copy without authorization. |
| Public outbox | SQLite stores `canonical_json` and receipt JSON. A candidate is not automatically already public. `software/runtime/src/sage_lantern/public_queue.py:61` | Establish whether this store is instantiated on Unit A; protect any populated store and journals/backups. Rejected or pending candidate text remains private until deliberately published. |
| Legacy history | JSON writer remains in source. `scripts/lantern/unit-a-voice-loop.py:202` | Verify actual migration/cutover and residual files. Do not assume current SAGE-backed history erased old JSON or media copies. |
| Transient data | Voice writes state and temporary audio; normal cleanup does not prove crash cleanup. `scripts/lantern/unit-a-voice-loop.py:891`, `scripts/lantern/unit-a-voice-loop.py:1204` | Check synthetic residuals after crash/reboot, logs, browser profile/cache, temporary files, swap and core dumps. |
| Other local stores | Conversation SQLite and profile files exist separately. `software/runtime/src/sage_lantern/conversation_store.py:111`, `software/runtime/src/sage_lantern/device_profile.py:127` | Classify actual fields and retained metadata without exporting real content; do not assume every SQLite field is encrypted by SAGE. |

Protect backups, previous deployment copies and sidecars as well as primary
files. Do not claim secure erasure from unlinking a file on SSD/NVMe; encryption
and key lifecycle must address offline copies. Existing user data must not be
deleted as an implicit rollout or lock action. Saving opt-out stops future
saving; explicit deletion remains a distinct authorized action.

Acceptance: choose and document the exact offline-storage threat model, then
test restart, corruption, authorized restore and explicit deletion with fixtures.
Disk encryption alone does not protect a mounted volume from root or same-UID
processes while the device is running. No arbitrary reduction of full-photo
retention is a substitute for confidentiality.

### 6. Physical boot and key lifecycle

Owner: OS/SAGE maintainers.

- Verify actual disk encryption, boot/recovery access, removable storage and
  alternate-boot policy; none was established by this review.
- Inventory where secrets are provisioned using permission/presence summaries,
  not secret reads. Review service environment autounlock without recording its
  value. SAGE supports `SAGE_PASSPHRASE` boot unlock:
  `../sage/cmd/sage-gui/node.go:603`.
- If cryptographic relock is required, obtain a coordinated SAGE design for
  quiescing requests, transaction admission, caches and key lifetime. Calling
  dashboard logout, detaching a key pointer, or restarting services is not an
  established substitute. This is upstream work, unlike local access gating.

Acceptance: distinguish powered-off protection, running-device app lock, and
actual cryptographic key unloading. Claim only the boundaries independently
demonstrated; do not clone keys onto another unit to test them.

## Release record

For each item retain owner, tested artifact version, synthetic test identifier,
result, accepted residual risk and approval date. Unit A may demonstrate the
product premise under an explicitly trusted-operator/local-process scope while
storage work remains, but must not be advertised as complete OS, theft, or
administrator-resistant protection. Source-level lifecycle approval is not
permission to silently expand that scope.
