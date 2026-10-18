# C protected-storage audit — 1 October 2026

C's installed r17 uses the protected conversation/media path. The inspected
legacy history has no saved conversation content or photos. Synthetic storage
tests pass, including with C's exact SDK. **Security acceptance remains open:**
crash-memory collection, the desktop authentication boundary, migration and
physical lifecycle tests still need qualification.

The owner confirmed the fixed 5 V/4 A adapter has not arrived. This audit used
read-only checks on C and synthetic tests on the Mac. It did not run inference,
capture, suspend, reboot, unlock, reset, or owner-key export. Device configuration
and existing crash artifacts were left intact.

## Observed storage

| Surface | Observation | Limit |
| --- | --- | --- |
| Runtime | `protected_storage=true`; conversation service bound to existing identity and local SAGE | Enabled configuration alone is not at-rest acceptance |
| Legacy history | `history.json` contains preferences and zero content items; legacy conversation SQLite and photo directory absent | Existing legacy content on another unit would still need migration handling |
| Native storage | One sealed workflow row; private JPEG table has zero rows; private-media backend enabled with positive quotas | No real photo on C was available for an at-rest/reopen test |
| SQLite inventory | Device-lock DB, personal SAGE DB and its snapshot; no inspected legacy content tables | An unfitted radio cannot supply live outbox acceptance evidence |
| Audio | Persistent audio directory empty; TTS disabled | The TTS-enabled orchestrator can write a WAV to configured `output_dir`; qualify its storage policy before enabling it |
| State log | Ten transition events; no question/answer/transcript/response fields; details are known metadata | Does not establish privacy of every system journal or browser artifact |
| Permissions | Private state directories are mode 0700; kiosk database read check denied | File permissions do not establish encryption or browser/image provenance |
| Swap | Only RAM-backed zram observed; `/run` and `/tmp` are tmpfs | Crash collection is a separate persistence path |

Protected conversations use the vault-backed workflow journal. Photo writes
reserve an intent, store immutable original JPEG bytes, then commit a guarded
conversation link. Access epochs and owner fencing prevent an operation started
before lock from becoming valid after a later unlock. Protected reads do not
fall back to legacy JPEG files or the legacy conversation database.

Migration and retention remain explicit work: switching to protected mode does
not automatically migrate or scrub older plaintext history/JPEGs. Removing a
photo link retains its encrypted immutable object; it is not secure deletion.
The mesh runtime constructs its inbox/outbox over the protected workflow adapter,
but live messaging qualification on C remains pending radio commissioning.

## Crash memory and desktop boundary

Apport is enabled and active, and the kernel core pattern invokes its collector.
The three inspected private services report `LimitCORE=infinity`; their live
processes have soft core limit 0 and hard limit unlimited. These settings need
an explicit, verified product crash-memory policy.

Two existing crash reports were observed by metadata only. A GNOME-shell report
contains a `CoreDump` field; a vision probe report does not. The dump payload was
not read, decoded, copied or scanned for private content. This proves desktop
memory has been persisted, without proving a private Lantern-service dump or a
specific owner-content leak. Preserve existing evidence until an explicit
retention/disposal policy is applied. Verify no-content diagnostics across the
desktop, auth, native SAGE, voice and model processes.

The autologin desktop account `sage` still lists `disk`, `sudo` and `docker`
groups. Its normal sudo rule requires authentication, apart from the scoped
PSD helper. Docker was inactive with no socket. Read-permission probes for the
personal DB and tested NVMe nodes denied access; group membership alone is not
evidence of a working disk exploit. Review privileged operator separation and
the actual desktop boundary without blindly removing management access.

[D-022](DECISIONS.md#d-022--docked-desktop-requires-a-login) requires login for
docked desktop. Acceptance must cover terminal/files and the desktop itself,
as well as the console. RAM-only browser tokens and relock on mode switch are
intentional; do not persist credentials to bypass this requirement.

## Test evidence and release binding

- **240 Python tests and 17 subtests pass** against six SDK source files copied
  from C. All six hashes match the installed SDK,
  `11.19.10+lantern.20260906`.
- All **115 runtime modules** and the voice-loop source on C match the tested
  checkout. C is still `lantern-cf-20260930-r17` on its existing boot.
- **59 native store/REST tests pass**, covering ciphertext-only persistence,
  wrong keys, tampering, actor isolation, lock gates, quotas, restart, CAS and
  guarded interrupted writes. These ran on the current SAGE checkout, which is
  newer than C's native build. C's build metadata has no VCS revision, so these
  results do not attest the exact deployed native binary.

The first laptop run used an older globally installed SDK and could not bind a
localhost test server inside the sandbox. Both conditions were corrected before
interpreting results. Two remaining failures came from a stale media test double
missing the correction-journal interface; the fixture now supplies that interface
and checks its binding. No production runtime behavior was changed. The final
installed-SDK run has one Pydantic schema-name warning and no failures.

Local synthetic tests cover consent/opt-out, pending writes, epoch changes,
draining and denial of plaintext fallback. They do not establish owner UAT,
electrical stability, secure deletion, full-device erasure, or C–F physical
acceptance.

## Remaining acceptance

1. Define and verify crash-memory handling and privileged desktop/operator
   boundaries, including D-022 authentication and browser/image provenance.
2. Qualify the exact deployed native artifact with synthetic conversation/photo
   canaries, ciphertext inspection and reopen/lock denial during a qualified
   power and owner-unlock test window. Do not export real keys or private content.
3. Test legacy migration, original-history/opt-out, immutable-media retention,
   restart and pending-write behavior on each C–F unit; qualify live outbox when
   hardware is fitted. Keep private/public retrieval isolated.
4. After adapter delivery and meter acceptance, complete actual capture/PTT,
   playback, touch, camera, power-button, lock/sleep/STR/wake/PIN/reboot and
   power-loss gates. Same-key recovery and sterile reset remain required; PIN
   recovery UI remains deferred to the owner's later UAT/UI/UX pass.
5. Productize bounded authenticated recovery diagnostics preserving unknown work
   and sole ownership. Today's manual drain-receipt archival does not prove
   automatic power-loss recovery.

Detailed metadata, source hashes and test logs are in the ignored local
[audit evidence](../../work/c-protected-storage-audit-20261001/README.md).
Use [NEXT_SESSION](NEXT_SESSION.md) for current successor task IDs and resume
order. No D/E/F work was performed and no recurring automation was scheduled.

## Later follow-up — 1 October 2026

After this read-only audit, the
[OS crash-memory policy](CRASH_PRIVACY.md#c-result--1-october-2026) was applied
and verified on C. Kernel dump collection, Apport forwarding and automatic
upload reporters are now disabled; existing reports were preserved. The
original audit observations above describe the pre-hardening state. No Lantern,
model or desktop instance changed. Fresh-session hard limits, browser-managed
dumps, a disposable crash canary, reboot persistence, report disposition and
the desktop login boundary remain open. This follow-up does not change the
audit's storage or physical acceptance limits.
