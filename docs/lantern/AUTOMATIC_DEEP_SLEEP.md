# Guarded automatic deep sleep

## Behaviour

The root controller uses the saved **Screen and sleep** delay: 5, 10, 15 or
30 minutes. The device must remain positively locked and input-idle for the
whole delay. Startup, changed preferences and lost observations require fresh
timing evidence. This is separate from the display-off timer and the broker's
automatic lock timer. It is suspend-to-RAM, not shutdown or hibernation.

Normal sleep preserves the running local model and private SAGE process. The
broker, voice worker, camera and UI server restart; the browser returns through
a fresh locked session. Existing rate-limit state, keys, preferences and history
are not replaced. The compositor and session SSH agent remain running.

## Admission and completion evidence

- A root journal and public service-start fence survive controller death.
- Auth closes admission, revokes local sessions, drains requests and reports
  instance-bound upstream-cookie cleanup. Wrong-PIN denial is not confused with
  an unknown login response. A public `/sleep-ready` check prevents starting a
  transition with known unresolved auth cleanup.
- Voice fences new work, reaps process groups, drains HTTP/session finalizers and
  requires positive conversation-worker completion. Root independently verifies
  empty service cgroups and matching invocation receipts.
- Inference creates a durable, content-free pending marker **before** HTTP
  admission. Only a verified terminal response and closed HTTP context clear it.
  `/slots` alone is not a queue-drain proof. A root model-instance baseline is
  established at model startup; an empty tracker is meaningful only with this
  baseline and all request producers stopped. Maintenance clients must not bypass
  tracking while automatic sleep is enabled.
- Kiosk control uses pidfds, exact process identity and bounded inventories.
  The launcher is paused and browser workers removed. Explicitly pinned session
  processes, including `kwin_x11`, are neither paused nor killed.
- Immediately before submission, fresh input and cancellation are checked again.
  Kernel entry still has an unavoidable interval after the final input sample.
- An effective systemd sleep guard requires a live, transition-bound root permit.
  Successful resume requires exactly one kernel suspend-success increment,
  inactive/job-free sleep units and an unchanged model PID—not just a successful
  `systemctl` command. The fence is released only after terminal proof.

The optional `--once --test-wake-after 60` acceptance mode retains the full saved
idle delay and schedules a 60-second RTC fallback immediately before sleep. It
does not change preferences. Normal operation does not set a periodic RTC alarm.

## Failure handling and limits

The production controller holds `sleep:handle-power-key` inhibition. The second
inhibitor prevents logind from treating the wake button's short press as a new
poweroff request after kernel resume. It does not disable the hardware wake IRQ
or guarantee protection from a hardware long-press reset. Protection lasts while
the controller/inhibitor is running; boot/controller-down policy remains a
separate appliance-hardening consideration.

Missing, malformed or stale evidence denies sleep. In-flight or orphaned
inference markers prevent a transition **before** private services are stopped;
they are not expired or automatically deleted. Interrupted inference may therefore
require an operator-controlled model reset and reconciliation before automatic
sleep is available again. This recovery workflow remains a release task.

A failure after fencing retains the journal/fence and does not silently restart
authentication. Recovery requires operator inspection and fresh completion
evidence; removing a marker is not a recovery procedure. `/run` state survives
process death, not power loss. Pending inference markers persist on disk.

The capability shown in Settings is a root-owned, boot-bound heartbeat expiring
after 15 seconds, not a user-editable preference or a claim that the device is
already suspended. The store continues to save only user delays.

This does not establish vault-key unloading, full-disk encryption, hostile-OS
isolation or radio power-off. Root and cooperating service users remain trusted.
Touch wake from display-off and wake from actual suspend are distinct acceptance
tests. The board's power button is the intended suspend-wake control.

## Unit A native findings, 2026-09-06

Before deployment, read-only kiosk validation found the vendor
`/usr/lib/chromium` directory root-owned but mode **0777**. Its mode was tightened
to **0755**, without modifying the running browser binary. The package manifest
for `chromium-x11 126.0.6478.126` did not match the installed binary:

- Installed binary MD5: `7a492329c6113230ccef0deb274e6878`
- Package-recorded MD5: `d9c96f52910f3110c55e3d8995a83608`

This is recorded as unresolved vendor-image provenance/integrity drift, **not**
proof of compromise or a verified clean binary. Audit or replace the browser from
a verified base image before a release/security sign-off. The directory change
only closes future unprivileged replacement through that directory.

Native test execution uses an installed-source overlay plus the selected changes
and an isolated, offline pytest tool directory. Legacy tests now explicitly mock
their intended unlocked environment rather than consulting Unit A's real lock
marker. The production access check is not bypassed.

## Deployment status

Source compatibility note (later community workflow work, not deployed): voice
drain receipts now include `community_drained`; the root validator requires the
same eighth check. Update both helpers and the six added community/message
dependencies together. The source staging bundle now has 32 files. The deployed
26-file acceptance record below remains historical evidence for its exact
earlier code, not acceptance of these later changes.

Unit A automatic suspend is deployed and boot-enabled. Native regression passes
624 tests and 390 subtests. The first automatic cycle observed the complete saved
300-second delay, entered kernel deep sleep and resumed through a 60-second RTC
test fallback. Normal operation does not configure this RTC fallback.

On 2026-09-06 (Asia/Kuala_Lumpur), kernel deep entry was 09:19:21, exit 09:20:24,
and controller completion 09:20:27. The suspend success counter advanced 1→2
with zero failures. Boot ID stayed `d846aa3a-9313-4b0d-beba-397e81672253`,
LLM PID stayed 239342, and private SAGE PID stayed 1601. Auth, voice, camera,
UI and display services returned active with no restarts; auth reported locked,
and the public fence and active transition marker were cleared.

The initial 26-file manifest SHA256 was
`9fd016385e0727e81180abaeab98ee3a01f926fe12f5aee19d468e5e26a576fa`.
Backups are under `/var/backups/sage-lantern-sleep-20260906T011236Z`.
After the successful cycle, independently reviewed hardening added prior-auth
receipt checks and removed a graphical-target ordering cycle. Its two installed
files were hash-verified, native regression rerun, and `--check` passed:

- Helper SHA256: `41a67f5d4bd4a44b69380fe144ee021531fc7e59c606dae45f759154c9e3d2b8`
- Unit SHA256: `c6fabeda7347cb37aa32e098deabf0c43e0059b7413fea71e5c16f5afd7cd2ec`

The prior two files are backed up in `final-hardening`. Production activation
uses `/etc/sage-lantern-idle-suspend.enabled` and the enabled systemd unit.
Owner power-button wake, PIN, microphone and camera acceptance after this
automatic deployment remains pending, alongside repeated cycles and reboot
validation. The RTC test does not prove power-button wake.

Systemd verification additionally reported a world-writable vendor
`/lib/systemd/system/rkaiq_3A.service`; this was left unchanged and added to the
base-image audit. More than 1024 prior auth receipts fails readiness closed;
safe compaction of confirmed receipts remains a release task.

`install-idle-suspend-local.py` builds/checks a hash-frozen **staging bundle** only;
it does not install or enable services. The controlled Unit A deployer backs up
code/drop-ins, installs guards, stops consumers, establishes a fresh model
baseline once, and restores a locked device before a separate sleep test.

## Wake-button shutdown correction — 2026-09-06

Owner reported an apparent reboot after attempting wake. Previous-boot evidence
shows a successful kernel resume followed immediately by an orderly poweroff:

- 09:38:00: kernel `PM: suspend entry (deep)`.
- 10:13:42: kernel `PM: suspend exit`.
- 10:13:42: logind `Power key pressed short.`, then `Powering off...`.
- 10:13:52: `poweroff.target` reached; a fresh boot followed.

The boot ID changed from `d846aa3a-9313-4b0d-beba-397e81672253` to
`7fc2a334-26e4-48e8-b585-42e1072c4346`. This incident was not evidence of a
failed kernel resume: the wake key was subsequently handled as shutdown.
The earlier manual test explicitly inhibited power-key handling; the initial
automatic service held only the broader `sleep` inhibitor, which did not stop
logind's separate power-key action.

At 10:17 local time, deployed **only** the idle service unit, adding
`handle-power-key` to `--what` and retaining guarded sleep handling. Its SHA256 is
`58a9b4379a18f537d45dc59ef37bfec77f50dd806beb12d18633d53956de517c`.
The old unit is backed up under `/var/backups/sage-lantern-power-key-20260906`.
Both hashes were verified; no active transition/fence existed before or after
stopping the controller. A temporary power-key inhibitor covered maintenance
and was removed after the permanent inhibitor was verified through logind D-Bus.

The service is active and boot-enabled; D-Bus lists
`sleep:handle-power-key`, mode `block`, owner `SAGE-Lantern`.
Boot ID and LLM PID 797 / private SAGE PID 1077 remained unchanged during the
fix. Live selected settings were revision 2, display-off 300 seconds and deep
sleep 600 seconds; they were not modified. Twelve staging/unit tests pass.
Systemd verification repeated the already tracked vendor `rkaiq_3A.service`
permission warning, with no new validation failure.

The later community source changes, including the eighth drain receipt field,
were **not** deployed. Another owner physical wake cycle after this correction
is still required; active inhibition is not itself a physical acceptance test.
