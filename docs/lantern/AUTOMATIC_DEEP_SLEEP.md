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

## C–F (Wayland) status, 2026-09-30 — adaptation written, staged, not deployed

The reviewed controller describes the **A/B profile**: a GDM **X11** kiosk for the
`radxa` account, with `lantern-display-power` reading idle seconds over
`DISPLAY=:0` and a `bash /usr/local/bin/sage-lantern-kiosk` launcher tree for the
kiosk fence. Read on C (29 Sep 2026), the C–F profile does not match it, and the
controller therefore cannot observe idle or fence the console there:

- no `radxa` account — uid 1000 is `sage`; `runuser -u radxa` exits 1, which is
  the first thing `lantern-idle-suspend.py` does to sample idle time;
- no X11 kiosk session — the session is `systemd --user` + GNOME Shell on Wayland
  (Xwayland is rootless `:0`), and the console is a Chromium kiosk started
  directly by `sage-lantern-mode-supervisor`, not by the X11 kiosk launcher, so
  the reviewed kiosk fence finds no launcher to bind;
- `sage-lantern-display-power.service` and `sage-lantern-camera.service` are
  **not-found** on C, and the display-power / idle-suspend helpers are not
  installed.

That adaptation is now written and unit-verified read-only; nothing has been
deployed and no cycle has been run. It is **D-018** in `DECISIONS.md`, and it
consists of:

- `/etc/sage-lantern/session-profile` — a root-owned record naming
  `x11-kiosk` or `wayland-mutter`; an absent record means the reviewed X11
  profile, anything else refuses by gate. The C–F installer stages
  `wayland-mutter`.
- `sage_lantern.suspend_session` — idle seconds from
  `org.gnome.Mutter.IdleMonitor.GetIdletime` over the operator's session bus;
  display blank/unblank through `org.gnome.ScreenSaver.SetActive`. A blank is
  lowered only when all of these hold: `lock-enabled` is not true, the operator
  session can be named, that session is not authentication-locked, and no blank
  is already up. On resume only the blank this cycle confirmed is taken down; a
  blank or lock it did not create is preserved and reported, never dismissed
  (`preserved_unowned`, `preserved_preexisting`, `preserved_authentication`,
  `preserved_session_unverified`, `preserved_session_replaced`). A skipped or
  preserved blank is not evidence that blank/unblank still works — see the
  [console-lane activation runbook](CF_CONSOLE_LANE_ACTIVATION.md) and the
  shield's own coverage in `software/runtime/tests/test_suspend_session.py`;
- a console fence over the mode supervisor and its browser group: the supervisor
  (MainPID of the GNOME application scope `app-gnome-sage\x2dlantern\x2dconsole-<pid>.scope`)
  keeps running while exactly one Chromium child holding the console profile, in
  its own process group, and all its grouped processes are SIGSTOPped for the
  cycle and SIGCONTinued after it;
- a stop set that follows the installed profile: auth, voice and fleet-admin on
  C–F (no camera, no display-power unit exists there), with the A/B set unchanged;
- `scripts/lantern/install-cf-idle-suspend-local.py`, which builds, stages as root
  and checks the bundle against a frozen plan and never deploys or activates.

Read on the live unit (30 Sep 2026), the C–F session layer behaves as designed:
the Mutter idle query returns milliseconds, `lock-enabled` is false, and the
signal-free console-fence preflight resolves the supervisor, the single kiosk
browser and its nine grouped child processes. Two assumptions died on contact
with the unit and are now recorded in the code: the DRM connector `dpms`
attribute is read-only (0444) even for root, so kernel sysfs is not a display
path, and Chromium rewrites `/proc/<pid>/cmdline` into one space-joined entry,
so the profile match must split it the way the reviewed X11 fence already does.
The staged bundle verified on C at `/var/lib/lantern-work/cf-sleep-20260930`
(manifest `46f3cfb7…`, `deployed:false`, `activated:false`).

**The voice lane is optional (D-018 rule 7).** It joins the drain contract only
once its unit is enabled, like the second model unit: without it the controller
requires no voice instance, publishes no drain request and starts nothing on
wake, while an enabled-but-inactive lane still refuses. A missing mesh, radio or
voice component therefore never stops the console, the on-screen keyboard, the
device lock or automatic sleep. This was found the hard way: C carries a
dangling `SAGE_LANTERN_MESH_BROKER=1` from the fleet installer with no mesh
runtime or `/etc/sage-lantern/mesh-runtime.json` (whose reviewed preparation
needs a radio fingerprint, and C's radio is declared absent). The C–F voice
drop-in therefore selects the reviewed non-mesh drain path, and the deployer
refuses only a unit whose mesh runtime is actually installed.

**Cycled for real on C, 30 Sep 2026.** One reviewed one-shot cycle
(`--once --test-wake-after 60`) completed: fence → auth stop with a confirmed
drain → fleet-admin stop → console freeze → suspend → 60-second RTC wake →
verified resume → services restarted → fence released. Kernel suspend counter 1,
boot id unchanged, both model units the same instances, console browser resumed
and answering, `--check` green afterwards. The unit still has no opt-in marker,
so automatic sleep remains off by choice.

Two configuration facts belong to every C–F unit: the image ships
`/etc/systemd/sleep.conf.d/00-disable.conf` with `AllowSuspend=no` (the lane
needs `/etc/systemd/sleep.conf.d/90-sage-lantern.conf` with `AllowSuspend=yes`),
and a refused attempt leaves `systemd-suspend.service` failed plus
`suspend.target` active, which must be cleared (`systemctl reset-failed
systemd-suspend.service`, stop the targets) before the next attempt. The panel
blank is best-effort on C–F and reported per cycle; the panel is dark during S3
in any case. The photo-in-flight rehearsal needs the camera and voice lane — no
CSI camera is fitted on C — so its bench substitute is the model-busy proof, and
the real photo rehearsal belongs on a unit with the camera fitted.

Two more rules belong to the C–F bundle. The voice sleep drop-in replaces the
voice `ExecStart`, so it must keep the unit's own capture binding
(`--config /etc/sage-lantern/lantern.toml --alsa-device <binding>` from
`/etc/sage-lantern/appliance-bindings.json`): the runtime default is a
placeholder device, and dropping the binding silently points capture at a
device that does not exist on the unit. Both installers and
`plan-fresh-vision.py` generate the same bytes for a given binding, and the
tests assert that equality. And because the lane installs `AllowSuspend=yes`,
the operator session must never ask logind to sleep on its own:
`configure-kiosk-session-settings.py` pins the GNOME power plugin's automatic
suspend off (see the console-lane activation runbook). Only the controller is a
reviewed suspend source.

Evidence and the full checklist: [C unit leftovers, 29 Sep 2026](../../work/c-unit-leftovers-20260929/README.md).
