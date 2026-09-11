# Locked display power — Unit A

This is display power control, not Linux suspend, radio sleep or vault key
unloading. The helper never unlocks the device or calls private endpoints.

## Behavior

- Require 30 seconds of continuously observed locked state and 30 seconds of
  X11 input inactivity before a display-off attempt.
- Keep X11 automatic timers at zero: never blank an unlocked session using a
  global timeout. Missing auth state or missing input-idle data prevents blanking.
- Touch input can wake the screen; the existing application passcode remains
  required. Observed new input rearms blanking only after another full 30-second
  input-idle and locked grace period. Definite configuration failures have
  bounded retries; uncertain force-off outcomes are not retried without input.
- Stopping the helper restores a display it may have blanked. Invalid auth
  state also attempts restoration. X11 failure can prevent a successful wake.
- Preserve SAGE and the warmed model processes. No reboot, suspend, power-key
  remapping or radio changes are part of this installer.

## Install and rollback

Run `sudo bash scripts/lantern/install-display-power-local.sh` on Unit A from
the reviewed source tree. It checks X11 idle-query availability, backs up any
existing helper/unit, and starts the unprivileged service. It does not enable
the service at boot or change existing enablement. Do not use the broader UI
installer for this update.

Stop with `sudo systemctl stop sage-lantern-display-power.service`. If needed,
restore the display as the kiosk user using
`DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority xset dpms force on`.
Use the backup path printed by the installer to restore a previous helper/unit
and run `sudo systemctl daemon-reload` before restarting that version.

## Physical acceptance before boot enablement

- [x] Confirm the installed helper starts and reads the exact locked state.
- [x] Lock, then leave touch/mouse/keyboard alone for at least 30 seconds.
- [x] Observe that the panel/backlight actually switches off. A driver report
  of `Monitor is Off` alone is insufficient; record panel behavior separately.
- [ ] Touch once and confirm wake, then enter the passcode normally. No private
  answer, photo or history may appear before authorization.
- [ ] Unlock, lock again, and verify the next idle cycle blanks and wakes.
- [ ] Confirm interrupted auth service and helper stop restore the screen.
- [ ] Measure power reduction; do not infer watts saved from DPMS status.
- [ ] Only after these checks, explicitly enable the service with
  `sudo systemctl enable sage-lantern-display-power.service` and test reboot.

The preliminary 12-second native DPMS experiment reported Off and then restored
the screen. The user subsequently saw Tap to wake; no physical off observation
was established by that experiment. Automatic operation remains unverified until
the acceptance checks above are performed on the deployed helper.

## Deployment evidence — 2026-09-05 13:32 UTC

Installed the isolated helper and service on Unit A; native stdlib tests:
37 passed. Native `--query-idle` succeeds with existing libX11/libXss; no package
installation was needed. The service runs as radxa, is active, and remains
disabled at boot pending physical acceptance. At 13:32:35 UTC, X11 reported
DPMS Enabled / Monitor Off after the guarded delay. Physical backlight state
and touch wake are awaiting owner confirmation, not yet passed.

Installed artifact SHA-256:
`39460262d6f11f291f07b06e159c65b637a9b6c9a6d6f1f05c970231b3ba90a1`.
Installer backup directory:
`/var/backups/sage-lantern-display-power-20260905T133159Z`.
No application, SAGE, LLM or radio service was restarted.

## Owner confirmation

Owner confirms the panel is physically off and tapping wakes it. This verifies
the initial display-off/touch-wake path, not measured watts, suspend, repeated
idle cycles, or a passcode/privacy regression test. Those checks remain open.

The tested service was subsequently enabled at boot; systemd reports active and
enabled. Reboot acceptance remains pending. No system suspend was enabled.

## Hibernation inventory

Native kernel `6.1.84-8-rk2410`: `# CONFIG_HIBERNATION is not set`, although
`CONFIG_ARCH_HIBERNATION_POSSIBLE=y`. `/sys/power/state` exposes only `freeze mem`;
`/sys/power/mem_sleep` reports `s2idle [deep]`. logind CanHibernate returns `na`.
Only 3.9G zram swap is configured; no disk-backed resume target is established.
Therefore this installed system cannot hibernate to disk. Enabling a kernel
option alone would not establish safe board/driver resume or protect the saved
RAM image, which can contain unlocked secrets. No kernel/swap changes made.

At initial inventory, suspend-to-RAM was advertised but untested. Fan shutdown, USB/touch
wake, RTC wake, peripheral recovery and lock-before-suspend remain acceptance
gates; display DPMS success does not pass them.

## Controlled suspend-to-RAM result — 2026-09-05

One manual test now passes owner observation: fans stopped, wake succeeded, and
the owner subsequently unlocked the device. Before sleep, the auth service was
stopped to invalidate active and in-flight sessions; a cleanup handler restarted
it after resume. This was a test fence, not a deployed automatic suspend hook.
The power-button shutdown action was inhibited for the test. A 60-second RTC
wake alarm was armed as a fallback and is cleared after resume; the actual wake
source has not been independently established, so RTC wake is not yet proven.

Suspend began at 14:10:46 UTC. A fresh SSH connection after the owner's unlock
verified unchanged boot ID `d846aa3a-9313-4b0d-beba-397e81672253`, unchanged active
LLM PID `1512`, active auth service and `locked:false`, consistent with the
owner's explicit unlock. The running model survived; no inference latency or
cache-hit benchmark was performed. Display-off/touch-wake and this system-wake
test are separate results; touchscreen wake from suspend remains unverified.

Remaining: repeated cycles, camera/audio/radio recovery, wake-source evidence,
RTC-only wake, fan/power measurements, production auth fencing, idle timer and
power-key policy. Automatic five-minute system suspend is not enabled.

Owner additionally confirmed the camera works after resume. Audio and radio
recovery are still unverified.

## Repeat-idle update — 2026-09-05 14:17 UTC

Deployed the revised display helper with 42 passing native tests. Unlike the
first version, a touch/wake followed by fresh inactivity can blank again without
requiring an intervening unlock. The service remains boot-enabled. Hardware
acceptance for repeated cycles is pending; initial off/touch-wake was confirmed.
Backup: `/var/backups/sage-lantern-display-power-20260905T141751Z`.
Artifact SHA-256:
`17ba2d6c65ec5c4ec0b8af3b75c931fbaff1e975a9344a0e64a3e5830377b9d7`.

The automatic system-suspend policy is staged separately (`idle_suspend.py`,
14 tests). It cannot execute suspend. Review requires confirmed service
quiescence and a durable transition fence through uncertain suspend outcomes;
the manual test's timed UI grace and unconditional cleanup are not sufficient
for unattended operation. No automatic system-suspend controller is enabled.

## Wake input isolation — 2026-09-05

Owner observed wake taps entering digits before the panel was visibly ready.
The UI now captures wake pointer/key sequences and compatibility clicks before
the keypad receives them. It shows Waking and waits for contacts to be released
plus 1.2 seconds without input before mounting an empty keypad. A locked-idle
fallback also covers the first input when DPMS and the UI sleep timer differ.
This is an input guard, not proof of physical panel readiness from the browser.

Native build/lint and 68 frontend tests pass, including repeated taps, held
contacts, multi-touch, delayed compatibility clicks and focus loss. Deployed
with guarded previous-release backup:
`/home/radxa/sage-commons/apps/lantern-fleet-admin.before-wake-input-20260905T142945Z`.
Owner validation remains pending: wake with several taps, wait for the keypad,
confirm no digits are prefilled, then enter and submit the passcode normally.

Owner subsequently reports the wake-input fix works. Keep repeated cycles and
slow-panel/multi-touch cases in regression testing; this is user acceptance of
the tested wake flow, not a claim that all physical edge cases were exercised.
