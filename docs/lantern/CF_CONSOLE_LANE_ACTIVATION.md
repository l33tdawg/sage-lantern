# C–F console lane activation (typed questions, local question service)

This is the per-unit runbook for the lane D-019 decides: the console app bundle,
the question loop that answers typed text, and the reviewed local question gate
that lets the service run on a unit with no radio and no working capture path.
It is written for C–F (Orange Pi 5 Plus, Wayland session profile `wayland-mutter`)
and assumes the unit already carries the reviewed appliance release.

Nothing here is acceptance. A lane is activated when the service runs, the gate
is the reviewed file, `--check` stays green, and the owner has asked a question
on the glass.

## Preconditions on the unit

| Condition | Why | Check |
| --- | --- | --- |
| Appliance release installed (`/etc/sage-lantern/appliance-installed.json`) | the bundle is anchored on its payload | `cat /etc/sage-lantern/appliance-installed.json` |
| `/var/lib/sage-lantern/LOCAL_VOICE_READY` present | the gate's own condition, minted by the reviewed audio commissioning | `ls -l /var/lib/sage-lantern/LOCAL_VOICE_READY` |
| Sleep bundle staged and deployed (D-018) | the voice drain request, the sleep fence and the model baselines the lane shares | `ls /etc/systemd/system/sage-lantern-voice-loop.service.d/` |
| `/etc/sage-lantern/session-profile` = `wayland-mutter` | the C–F session profile | `cat /etc/sage-lantern/session-profile` |
| No mesh runtime (`/etc/sage-lantern/mesh-runtime.json` absent) | the gate is refused on a mesh-managed unit by design | `ls /etc/sage-lantern/mesh-runtime.json` |

## Build and stage (workstation, offline)

```bash
python3 scripts/lantern/build-console-bundle-local.py \
  --stage /private/tmp/console-lane-<unit>-<date> \
  --bundle-id console-<unit>-<date> \
  --anchor-from <release payload tree for that unit>
```

The stage carries the app UI set (`dist/`, `public/`, `package.json`,
`package-lock.json` — never `node_modules`), the question loop, the reviewed
gate, and `anchor/` built from the release payload. `manifest.json` records
every payload digest, the anchor digest, and every destination the release
payload does not carry (`anchor.absent_paths` — freshly hashed assets, which the
deployer only accepts when the unit does not already have them).

For the second and later local update on the same unit, the anchor is the
previous stage instead (`--predecessor-stage <previous stage>`); the release
payload no longer matches the unit once a local bundle has landed.

## Deploy (unit, root)

```bash
tar cf - bundle anchor check-console-lane-local.py deploy-staged-console-bundle-local.py \
  | ssh root@<unit> 'mkdir -p /var/lib/lantern-work/console-lane && tar xf - -C /var/lib/lantern-work/console-lane'
ssh root@<unit> 'cd /var/lib/lantern-work/console-lane && python3 deploy-staged-console-bundle-local.py \
  --stage bundle --backup-root /var/backups/sage-lantern-console-$(date -u +%Y%m%dT%H%M%SZ) \
  --predecessor anchor --restart-app'
```

Expected: `{"deployed": true, "activated": false, "app_restarted": true,
"replaced_unverifiable_targets": 0}`. A refusal names its gate
(`destination_differs_from_predecessor`, `dependency_change_requires_release`,
`mesh_question_lane_owns_voice_start`, …) and changes nothing before the first
write it refused.

## Activate (unit, root)

```bash
systemctl daemon-reload                       # the deployer already does this when a unit file is in the bundle
systemctl enable --now sage-lantern-voice-loop.service
systemctl reset-failed sage-lantern-voice-loop.service   # only if a previous stop left it failed
python3 configure-kiosk-session-settings.py --account <operator> --apply   # deliberate lock only, no accidental lock, no session auto-suspend (see below)
```

The service must start with the gate installed: the gate resets the mesh start
condition and the audio `ExecCondition`, so `systemctl show
sage-lantern-voice-loop.service -p Conditions` no longer requires the broker
authorization file and `-p ExecCondition` is empty.

## Acceptance (owner present)

1. One command reports the configured state (read-only; add `--activate` to let
   it start a stopped service, and `--sleep-check` for the sleep guard):

   ```bash
   python3 check-console-lane-local.py --manifest bundle/manifest.json --sleep-check
   # → {"ok": true, "checks": {"bundle_files_verified": …, "question_service_active": …,
   #    "typed_lane_advertised": …, "console_build_matches_bundle": …, "sleep_check_green": …}}
   ```

   It verifies every deployed file against the bundle manifest, that the
   question service is enabled and active with `Result=success`, that the gate
   is installed, that `/health` advertises `typed_ready` and `review_ready`, that
   the console build id matches the bundle, and (with `--sleep-check`) that the
   sleep guard check still passes.
2. `/opt/sage-lantern/venv/bin/python /usr/local/libexec/sage-lantern-idle-suspend --check`
   → `Sleep guards, drain hooks and idle observation verified; no suspend
   requested` (the venv interpreter is required; the script's shebang alone
   lacks `sage_lantern`).
3. Reload the console (`sage-lantern-desktop-mode off`, or reboot) and unlock
   with the owner PIN. The reload is required: the kiosk browser holds the
   previous page until it is restarted, and a reload re-locks the device.
4. Ask a typed question on the glass with `Type`, submit it, and read the
   answer. With a photo attached, the same control asks the photo question.
5. Record the unit, the bundle id, the receipts, the health output and the
   sleep-check line. The rehearsal harness
   (`scripts/lantern/rehearse-typed-question-local.py`) is a runtime rehearsal
   only and never substitutes for this step.

## Recovery and rules

* A refused or aborted sleep attempt can leave `systemd-suspend.service` failed
  and `suspend.target` active; `systemctl reset-failed
  systemd-suspend.service` and stop the targets before the next cycle (D-018).
* Never install the gate on a unit whose mesh runtime is installed: there the
  broker authorizes the voice start itself.
* Never enable the retired CPU model unit alongside the vision unit (D-016).
* The answer path is unlock-gated: protected conversation memory needs the
  broker's unlocked access epoch, so a locked device answers nothing — typed or
  spoken.
* The panel must never show GNOME's lock screen *by accident* (the operator
  account password prompt): the appliance's lock is the Lantern PIN. Locking is
  therefore deliberate and only one thing asks for it — the mode supervisor, on
  the docked-desktop edge, because D-022 requires a fresh operator login there.
  `configure-kiosk-session-settings.py` unbinds the lock shortcut, keeps
  `idle-delay=0`, leaves `disable-lock-screen=false` and `lock-enabled=true` so
  that request can actually take effect, pins the GNOME power plugin's automatic
  suspend off (`sleep-inactive-*-type=nothing`, timeout 0) and verifies all of it
  by read-back. On C a stray `Super+L` locked the session on 30 Sep 2026 and
  stranded the panel until `loginctl unlock-session <id>` was run as root; that
  was the *shortcut* firing, which stays unbound, and PIN/credential recovery
  remains a deferred UAT item.
* **A skipped blank is not evidence that blank/unblank keeps working
  (corrected 9 October 2026).** With locking enabled the sleep lane's blank
  outcome is `display_off=skipped_lock_enabled`: nothing was blanked, the panel
  is dark only because the unit is in S3, and no panel-off path is verified on a
  single-panel C–F unit. The citation this bullet used to carry was wrong —
  `test_lantern_idle_suspend_cf.py` mocks `GnomeShield` and asserts the lane
  dispatches to it, not that a blank happened. The shield's own coverage is
  `software/runtime/tests/test_suspend_session.py` (`test_off_never_activates_a_shield_that_would_lock_the_kiosk`
  and the off/on ownership cases) — see the D-018 amendment in
  [DECISIONS.md](DECISIONS.md) for the D-018×D-022 ruling. On wake the lane takes
  down only a shield the same cycle confirmed (`restored`); a shield it did not
  create — an operator blank or an authentication lock — is preserved and
  reported as `display_on=preserved_unowned`, so the panel can legitimately come
  back behind a login. That is a state to observe, not a cycle failure, and it is
  never treated as the operator having authenticated on the panel.
* The session must never ask logind to sleep on its own. The sleep lane installs
  `AllowSuspend=yes` (the controller needs it), so GNOME's default
  fifteen-minute idle suspend would reach systemd with no transition, no fence
  and no drain; the reviewed guard refuses it, and a refusal latches
  `systemd-suspend.service` failed with `suspend.target` active. The pinned
  power-plugin settings above are what keep the controller the only suspend
  source; check them on every unit before a sleep acceptance run.
* A local bundle leaves the unit no longer byte-identical to the release
  payload. That is expected and recorded in the receipt; the next release cut
  restores one anchor for every unit.
