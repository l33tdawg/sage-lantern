# Unit A power settings rollout — 2026-09-06

Applied approximately 00:28 UTC, September 6. This is a narrow settings rollout,
not the fresh-device installer or completion of automatic suspend.

## Changed on A

- Added `power_settings.py` to the installed runtime.
- Updated the auth daemon with authenticated preference reads/writes and the
  nonsensitive timing-policy endpoint.
- Updated the display helper to consume that policy and reset eligibility on
  changes; missing/invalid policy prevents blanking and attempts wake.
- Updated the native UI with Screen and sleep controls. Existing Change name
  remains available. The exact kiosk process was refreshed after verification.

The network setup page/broker, Wi-Fi adapter and Plymouth installer/theme were
**not deployed**. Wi-Fi, boot configuration, personal SAGE and model services
were not changed. The shared `device_access.py` correction was tested in staging
but deliberately excluded: deploying it requires a separate controlled restart
of its importing voice/conversation processes.

## Native validation

Staged against copies of installed runtime **source files only**, with the new
modules overlaid. An initial broad package copy encountered protected backup/
bytecode files and was abandoned; it was not used for tests or deployment.

- 128 native Python tests passed: power store 12, shared access 18, auth 50,
  display helper 47 and display service 1. These include ephemeral HTTP tests.
- 71 native UI tests, lint and ARM64 production build passed.
- Matching source digests were checked before installation and after startup.
- Fresh auth state was exactly locked. Invalid-token power reads returned 401;
  unauthenticated private status reads returned 403. The status codes differ
  intentionally; the one-off rollout validator was corrected before execution.
- Live policy: revision 0, screen-off 30 seconds, deep-sleep 300 seconds,
  `deep_sleep_available: false`.
- UI, auth and display services were active with zero reported automatic
  restarts after the update. Voice PID 1605 and model PID 1512 were preserved.
- Only the matching kiosk browser (previous PID 63929, fixed executable/UID/
  profile and kiosk flags) was terminated for refresh; no broad browser kill.

Owner confirmation of changed delay, physical display-off and wake is pending.
No PIN, recovery material or real conversation/photo content was collected.

## Artifact and recovery record

Native staging: `/home/radxa/lantern-settings-stage-20260906`.

Guarded code backup:
`/var/backups/sage-lantern-power-settings-20260906T002826Z`.

Previous UI:
`/home/radxa/sage-commons/apps/lantern-fleet-admin.before-power-settings-20260906T002826Z`.

Installed SHA-256:

| Artifact | Digest |
| --- | --- |
| power settings module | `c57160fca98db9c7f6ad1502103be9566894d4b66e88b07c058fe61bbc27d9ef` |
| auth helper | `9394ff621f4998285c18133aff47345593baf4e747dbc536fa65057351812451` |
| display helper | `abd0d59f5fd591b25bb1809a20b1e4e2951c9dfac54edb54826b32cc80bcb744` |

The reviewed one-off rollout script remains in the staging directory. It is not
a reusable fresh installer: it expects this exact initial state and source
digests. It preserves private state, restores guarded code only after confirmed
stops, and leaves affected services stopped after failure. This was not a
power-loss/crash-recovery test. Backups are not sanitized distribution artifacts.

## Owner test

1. Unlock A and open Settings → Screen and sleep.
2. Select 1 minute for screen-off and save.
3. Use Menu → Lock, then leave the screen untouched. Confirm actual display-off
   after the selected delay, measured from locking/last touch—not from saving.
4. Tap to wake, confirm no stray digits, unlock and reopen Settings to confirm
   the preference remains saved.

Deep-sleep preference remains save-only and is clearly marked unavailable. No
automatic fan-off/suspend controller has been enabled. Change name can be tested
separately; its existing behavior does not rotate identity keys.
