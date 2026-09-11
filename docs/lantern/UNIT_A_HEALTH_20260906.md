# Unit A read-only health review — 2026-09-06

Live SSH review at approximately 2026-09-05 23:53 UTC (September 6 local).
No deployment, configuration edit, restart, lock/unlock, capture, reboot or
suspend was performed. Private keys, PINs, recovery material and user content
were not collected. This is a health snapshot, not full release acceptance.

## Observed on A

- All nine checked services were active/running and enabled: fleet admin,
  device auth, voice, camera, status, personal SAGE, LLM, PTT and display power.
  Each reported `NRestarts=0`; the failed-unit list was empty.
- Boot ID remained `d846aa3a-9313-4b0d-beba-397e81672253`; LLM PID remained
  `1512`, matching the previously accepted manual suspend/resume session.
  Uptime was about 11 hours. This is not evidence of a new reboot test.
- Memory available was about 3.2 GiB; swap use about 65 MiB. Root filesystem
  had 403 GiB available (11% used). No immediate capacity problem was observed.
- Device UI/auth/voice/camera/status, SAGE, model and observed chain listeners
  were loopback-bound. SSH listened on all IPv4/IPv6 interfaces. This is a
  listener snapshot, not a firewall or SSH-policy audit.
- Auth `/state` returned HTTP 200 with boolean `locked:false`. A direct,
  unauthenticated GET to status returned HTTP 403 despite that state. No lock
  action or private data retrieval was attempted; other routes remain untested
  in this review. Recent X11 input was present, so this is not an idle-expiry test.
- Display helper SHA-256 matched the deployed accepted artifact:
  `632795282019dd4437292e5cc45f6c0837dfa6b5a9c3e41f2563b713677f37e3`.
  Display service was enabled; no Lantern sleep/suspend unit file was listed.
  Kernel reported `freeze mem` and `s2idle [deep]`.
- The lock/setup markers and sampled UI server entrypoint were root-owned
  mode 0644. This does not prove all parent paths or executable artifacts safe.
- `radxa` belongs to `sudo` and hardware-access groups. Auth, voice, status
  and personal SAGE run as the same `sage-lantern` UID. Root mount source was
  directly `/dev/nvme0n1p3`, ext4; whole-volume encryption was not established.
- Two-hour warning-or-higher journal queries returned no accessible records
  for auth, voice, camera, LLM and display power. Do not equate this limited
  priority/access window with proof that every application operation succeeded.

## What remains before calling A complete

1. **Integrate safe automatic suspend.** Manual deep suspend, wake and camera
   after resume were owner-accepted. The automatic policy is not deployed;
   positive service/request drain acknowledgements remain prerequisites.
   See `SUSPEND_RUNTIME_GATES.md`. Do not enable a timer alone.
2. **Run a release-specific restart/wake acceptance session.** Verify audio
   after deep resume, repeat lock/blank/wake with no stray digits, cold boot to
   locked state, revoked-token rejection and recovery with synthetic content.
   Schedule disruptive tests separately; preserve the working system today.
3. **Close or explicitly scope OS/storage privacy.** Kiosk operator privileges,
   shared service UID, raw-photo storage and cryptographic key lifetime are not
   solved by the app PIN. See `UNIT_A_SECURITY_REMAINING.md`. Do not claim
   protection from root, service compromise or offline theft on this evidence.
4. **Finish existing feature integration and release packaging.** Messaging,
   verified public sharing and secure fresh provisioning are not made complete
   by healthy services or pure-module tests. A-to-B transport acceptance waits
   for B; A-only integration and a sanitized pinned release can proceed now.

Immediate priority: safe automatic-suspend integration, followed by a scheduled
audio/reboot acceptance session. No capacity tuning or emergency reinstall is
indicated by this snapshot.
