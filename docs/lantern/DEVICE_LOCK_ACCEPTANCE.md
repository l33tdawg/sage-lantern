# Private device lock: implementation and release gate

## Approved interaction

User decision, 2026-09-05: **8–12 ASCII digits**, preserving leading zeros.
This replaces the earlier four-digit minimum. Use large number buttons,
backspace, Clear, and Unlock. No letter keyboard, automatic plaintext PIN display,
browser storage, analytics, or logging of credentials. Use the existing SAGE
vault passphrase; do not introduce a second PIN hash or pad short PINs.

Owner-requested setup refinement: “Create your passcode”, then “Confirm your
passcode”. Both setup entries offer an explicit Show/Hide passcode button,
masked by default. Reveal expires after 15 seconds and resets on submission,
Clear or page hiding. Ordinary unlock/recovery remains masked. The two setup
entries must match before the vault-enable request is made.

## Guarded prototype deployed; owner acceptance pending

The reusable keypad lives in `apps/lantern-fleet-admin/app/device/lock.tsx`.
The access boundary mounts it only with `LANTERN_DEVICE_LOCK=1`. The installer
must enable the shared server-side gates before activating that UI. Unlock uses
a pending capability, explicit activation and a short presence lease; the kiosk
keeps the capability only in memory and clears private views when locking.

The backend foundation lives in `software/runtime/src/sage_lantern/device_lock.py`.
It verifies against the loopback SAGE vault, requires encryption, and issues a
separate short-lived device capability. A SAGE dashboard session has broader
authority and must never reach the kiosk. This module is now connected to
the running HTTP services through the shared access gate. The guarded prototype
was deployed on 2026-09-05; this does not resolve the broader OS and at-rest
security limitations below.

Unit A's read-only native check on 2026-09-05 reports encryption disabled.
The owner must choose and confirm their PIN on-device; the agent must never
choose a default or receive it in chat. The source setup flow enables a fresh
vault only with an explicit installation marker and requires recovery backup
acknowledgment. Existing alphanumeric vault credentials need a separate
authenticated migration/setup flow; the numeric keypad cannot unlock them.
New-device enrollment must let the owner choose and confirm the PIN and safely
acknowledge the recovery key before becoming ready. Never silently enroll a
default credential or disable encryption to regain access.

## Verified SAGE limitations

September 6 fresh-Unit-A observation supersedes the September 5 encryption
observation above: encryption is enabled, but the screen can report locked while
the serving vault remains unlocked. Native96102 verified that distinction; it
does not prove private endpoint bypass or cryptographic lock. Suspend-to-RAM
also does not establish key unloading. Track four separate states: screen
locked, producers quiesced, cryptographically locked, and suspended.

Remaining cryptographic-lock work requires authenticated native coordination:
close admission, drain signers/requests/workers, invalidate all sessions, release
key/plaintext holders, then publish a verified locked generation. Drain failure
must retain fences. Inventory projections, caches, federation/snapshot workers,
swap, core dumps and suspend images; do not claim verified memory erasure from
an API flag or process restart. Resume must require fresh owner authentication.
Acceptance must race reads/writes/unlock against relock, force drain failures,
reject old credentials and signed private reads while cryptographically locked,
and cover suspend/resume and crash recovery. Privileged/root and trusted
same-UID access remain outside the screen-lock threat boundary.

The inspected sibling source requires at least eight characters when enabling
encryption or changing/recovering the passphrase (`web/handler_ledger.go`).
`web/handler.go` implements `/v1/dashboard/auth/lock` as current-session
invalidation. It does not unload the vault key, invalidate every other
session, or revoke ordinary agent access. Successful dashboard login therefore
cannot by itself establish a secure whole-device lock.

Upstream work is required if cryptographic key unloading is promised. SAGE's
own concurrent login limiter also needs correction; local bridge serialization
does not protect attempts sent directly to SAGE. Full local access enforcement
is Lantern work, not something to defer to an upstream vault feature.

## Required behavior before activation

| Surface | Required lock behavior |
| --- | --- |
| Voice/history, port 3197 | Authenticate all private requests; reject missing, expired and replayed capabilities. Keep auth routes narrow and bounded. |
| Physical push-to-talk | Gate hardware as well as touchscreen requests. Lock cancels capture and fences queued presses. |
| Status, port 3199 | Hide transcripts, answers, photo identifiers and private SAGE details. Health returns minimal liveness only. |
| Camera, port 3198 | Gate capture and terminate already-open streams on lock. |
| Runtime/conversation worker | Quiesce or cancel work; fence late completion, status updates and saves. |
| SAGE and admin | No root dashboard proxy; prevent alternate ports/sessions from bypassing the intended user privacy boundary. |
| Files | Inventory plaintext photos, outbox, audio and keys outside the vault; do not imply dashboard locking encrypts them. |
| Kiosk | Unmount private views, clear attachments/answers, close streams, prevent browser escape and cached/back-button restoration. |
| Restart/recovery | Start locked, fail closed on outages, retain attempt limits. Audit environment auto-unlock and clock rollback. |

## Acceptance evidence required

- Correct and incorrect 8-, 9-, and 12-digit PINs; reject seven/thirteen digits,
  non-ASCII digits, whitespace, duplicate JSON fields and oversized bodies.
- Parallel guesses, controller restart and time rollback do not reset limits.
- SAGE unavailable, unencrypted, redirecting, malformed or partially unlocked
  means access denied, not fallback successful login.
- No dashboard cookies, PINs or recovery keys in browser responses, URLs,
  logs, browser storage or ordinary device capabilities.
- Lock wins against delayed unlock, queued PTT, inference completion, history
  requests and an already-open camera stream.
- Replay old tokens after lock, expiry and reboot: probe every endpoint
  directly rather than trusting UI state.
- Rehearse power-loss/recovery while preserving memories, photos, identity
  and encryption. User chooses the PIN; never choose one on their behalf.
- Physically test keypad layout on Unit A, including error and long-PIN states.

These checks remain under the existing Unit A master task. The present stage
is ready for owner setup and physical testing, not a completed security release.

## Evidence and remaining gates — 2026-09-05

- Guarded activation completed; all seven auth/UI/voice/camera/status/SAGE/LLM
  services report active. `/state` returns locked; unauthenticated history,
  status and preview return 403. Read-only setup status and setup-marker reading
  under the service UID passed. Exact kiosk process refreshed for owner setup.
- No PIN was chosen or vault enabled by the agent. The owner must complete the
  on-device PIN/recovery flow. Existing local test history was cleared with the
  user's prior authorization; this was not permission to erase SAGE records.
- Final local Python regression: 797 tests and 454 subtests pass. Native stdlib
  subset: 160 tests pass. Native pytest is absent, so pytest-only profile/vault
  suites were tested locally, not on the device.
- Isolated native private-memory verifier FINAL5 passed actual SAGE commit,
  observation/reflection, store reopen, synthetic three-day-old recall, local
  model use and deprecation. This is not a physical three-day/reboot test.
- Local frontend: 61 tests, lint and production build passed. The isolated
  native ARM64 UI also passes 61 tests and its production build. Source security
  review approved the pagehide/BFCache lifecycle; actual browser restoration
  and touchscreen behavior still require acceptance.
- Locked voice idle now clears once, rather than writing/fsyncing every poll;
  the focused voice suite passed 107 tests and 62 subtests.
- Worker admission now rechecks between SDK operations and retains receipts for
  in-flight writes. Source tests cover mid-submission locking and safe shutdown.
- Installer now freezes/hashes the app, durably masks services before changes,
  swaps/fsyncs the app before releasing masks and remasks on failure. These are
  source/helper tests, not a live power-loss rehearsal. The setup flag contract
  is root-owned 0644 so the service UID can read it; native inspection and setup
  status must pass before owner setup is offered.
- Photos/outbox remain plaintext outside SAGE encryption. OS/SSH/admin bypasses,
  default system credentials, key unloading and encrypted attachment storage
  remain explicit security work. Screen blanking is not hardware suspension.
- Naming is not full provisioning: hardware checks, unique per-unit enrollment,
  directory-name updates and paired transport acceptance remain open.
- Staging must not preserve hardlinks in dependencies. The first attempt safely
  rejected esbuild hardlinks before service changes; a separate copy using
  `cp -a --no-preserve=links` passed the unchanged artifact policy.
