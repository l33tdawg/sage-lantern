# Fresh A/B application installer

2026-09-06. `scripts/lantern/install-fleet-appliance-local.py` installs a matched
prebuilt application and private-auth wiring on a fresh Linux ARM64 target.
This is a source-only implementation, tested against synthetic directories.
No target installation, artifact build, owner onboarding or hardware proof occurred.

## Invocation

Use the same explicit binding arguments as `lantern-unit-config.py`. The installer
recomputes that plan; it does not trust an uploaded plan's verification booleans.
All values below must come from this unit's operator-selected public evidence:

```sh
python3 scripts/lantern/install-fleet-appliance-local.py \
  --unit-id "$UNIT_ID" --account "$OPERATOR_ACCOUNT" \
  --prepared-metadata /etc/sage-lantern/unit.env \
  --radio-inventory "$RADIO_INVENTORY" \
  --radio-usb-serial "$RADIO_SERIAL" \
  --radio-vendor-id "$RADIO_VID" --radio-product-id "$RADIO_PID" \
  --radio-stable-path "$RADIO_STABLE_PATH" \
  --radio-public-fingerprint "$RADIO_FINGERPRINT" \
  --audio-usb-serial "$AUDIO_SERIAL" --audio-stable-path "$AUDIO_STABLE_PATH" \
  --audio-vendor-id "$AUDIO_VID" --audio-product-id "$AUDIO_PID" \
  --alsa-device "$VERIFIED_ALSA_CAPTURE_DEVICE" \
  --release-root "$MATCHED_STERILE_RELEASE" --manifest "$MATCHED_MANIFEST" \
  --operator-approved-release
```

Without `--install`, this verifies supplied bindings and the closed matched
release only; it does not inspect target readiness. Add `--install` only on the
fresh target as root. Add `--allow-owner-vault-setup` only with explicit operator
permission to expose owner PIN initialization; the flag neither chooses a PIN
nor initializes/encrypts a vault. Neither mode fetches packages or runs builds.

## Matched-release coordination

Beauvoir's explicit selection must include the new installer under role
`installer`, and the current `lantern-unit-config.py`, appliance manifest verifier,
runtime verifier, runtime/SDK/SAGE builders, `unit-a-device-auth.py`, and its
systemd unit. The installer rejects missing entries and mismatched local bytes.
UI entries must include built server, package/lock, dependencies and the actual
`node_modules/vinext/dist/cli.js` entry; npm/.bin symlinks are unnecessary.
Reuse `appliance-release-manifest.py.verify`, including its matched-component
checks. This remains integrity checking of operator-approved inputs, not signed
authenticity or native acceptance. No release was rebuilt for this change.

## Installed scope and containment

- Root-owned app at `/opt/sage-lantern/fleet-admin`; service runs as
  `sage-lantern`, loopback 3100, with `LANTERN_DEVICE_LOCK=1` and a unit-specific
  operator label. No `/home/radxa` app dependency or A profile is copied.
- Existing private-auth script/unit, gated on the lock marker and final install
  receipt; the installer creates only fresh service-owned 0700
  `/var/lib/sage-lantern/device-lock` and its mandatory `drain` subdirectory.
  It resolves the actual `sage-lantern` user/group IDs (never assumes 1000) and
  always appends `--drain-report-dir /var/lib/sage-lantern/device-lock/drain` to
  the installed auth command. The empty directory is not a drain receipt/proof.
  Initial session state is created by the broker at first start, not imported.
  Any existing lock directory/receipt/symlink is rejected and preserved, never
  overwritten or adopted. Creation failure retains lock policy without a final
  install receipt. The configured Root key path is referenced but never opened.
- Unit-specific audio udev rule, normalized public radio inventory, and complete
  operator-binding plan. These do not prove audio/RF operation.
- Root-owned lock-required marker precedes app writes. Optional root setup marker
  permits the existing guarded PIN workflow, never automatic key/vault creation.
- File-install completion receipt is written last for that phase. Services remain
  disabled unless the separate kiosk opt-in below succeeds; none are started.
  `ready`, `provisioned`, `backend_verified`, `hardware_verified`, and
  `owner_setup_complete` remain false. There is no `PROVISIONED` write.

The installer rejects existing app/auth units or drop-ins, prior enablement,
lock/setup markers, private lock/history/conversation state, voice-ready and
PROVISIONED markers, linked targets, and writable/non-root installation parents.
It is deliberately not an update path. Run at the fresh app-install stage before
legacy provisioning completion; never delete a marker to bypass these guards.
Failure leaves partial files locked and no completion receipt; review recovery
instead of enabling services or blindly rerunning.

## Remaining dependency/acceptance gaps

1. Preinstalled Node >=22.13, Linux ARM64-compatible staged UI dependencies,
   non-root `sage-lantern` account, and runtime interpreter are required. This
   installer does not create accounts, install wheels, or certify native modules.
2. Matched **installed** runtime/SDK/SAGE, per-unit private-chain and current-Root
   binding, protected-storage config/media quotas and native space probe still
   need their own provisioning and acceptance. No private state/keys are copied.
3. The auth service requires the separately provisioned personal-SAGE service.
   Owner PIN, recovery backup, lock/wake and source-built UI lock-boundary behavior
   must pass acceptance before any readiness claim.
4. Status now requires the root-owned per-unit binding plan and reports only
   configured USB path presence; missing config never defaults to A. ALSA capture
   is explicit (`hw:CARD=<operator-observed ID>,DEV=<device>`), not inferred from
   a USB name or copied from A. Hardware capture remains unverified.
   The installed kiosk session checks its selected operator account and uses
   `/home/<account>/.config/sage-lantern/<unit-id>/chromium`; no existing browser
   profile is copied. Autologin, camera/PTT, display and suspend activation still
   require separate integration. The old fleet installer remains unchanged.
5. Default installation performs no daemon reload or service enable/start.
   The explicit kiosk opt-in reloads systemd and enables only app/auth/status
   for next boot. Neither mode starts services, triggers udev, opens serial,
   unlocks SAGE or issues network requests. Native startup acceptance remains.

## Explicit fresh GDM3 autologin opt-in

Add `--enable-kiosk-autologin --gdm-base-sha256 "$REVIEWED_GDM_SHA256"` to the
fresh `--install` invocation only after reviewing the exact existing bytes of
`/etc/gdm3/daemon.conf`. Both options are required together. Check-only reports
the request but deliberately does not claim native display checks passed.

Supported baseline is GDM3 already selected/boot-enabled with `graphical.target`,
currently stopped by the operator, and installed Chromium, kwin_x11, xprop, xset,
xrandr and curl. This is not a LightDM/SDDM migration or a running desktop update.
Existing explicit autologin/timed-login/session policy, duplicate GDM sections,
display/getty overrides, account session metadata or `.dmrc` cause rejection.
An image with existing AccountsService user preferences needs separately reviewed
migration; do not delete those files merely to satisfy the fresh guard.

After locked app installation, the opt-in backs up GDM config under root-private
`/var/lib/sage-lantern/recovery/kiosk/`, creates only the selected account's
AccountsService `Session`/`XSession=sage-lantern`, enables the installed
app/auth/status services for boot, then atomically configures GDM automatic OS
login with X11. Unrelated GDM settings/comments are preserved. No display-manager
restart or reboot is performed. The named kiosk session remains account-bound
and opens the lock/onboarding UI, not private SAGE content.

`/etc/sage-lantern/kiosk-boot.json` records successful boot configuration separately
from the earlier file-install receipt. It states `services_started=false`,
`private_sage_unlocked=false`, `ready=false`, `provisioned=false` and requires
reboot acceptance. It records configuration actions, not observed future login.
No PIN is stored and no vault unlock/session cookie/start grant is manufactured.
Mesh/voice are **not** enabled by this opt-in: Socrates's root daemon provisioning
and supervised voice start remain distinct gates.

On failure, enablement/account configuration can be partial. Lock policy and
backups are retained; before the final GDM commit, the original GDM file remains
unchanged. Failure after that commit can leave OS autologin configured without
a success receipt. Review state through the operator console, compare the backup
and current file, and undo only this installation's changes; never blindly rerun,
restore over unrelated edits, or remove private lock/recovery state. Reboot and
on-device first-run/PIN/lock acceptance are mandatory before claiming usable boot.

## Mesh startup source integration

The canonical profile now requires
`software/systemd/sage-lantern-mesh-runtime.service` and
`software/systemd/sage-lantern-voice-loop.service.d/40-mesh-broker.conf` as exact
systemd/drop-in sources, not generic role fillers. The fresh installer copies
both without enabling or starting them. `30-unit-audio.conf` supplies explicit
ALSA capture and `/run/sage-lantern-mesh-control/voice-drain-request.json`; the
frozen `40-mesh-broker.conf` supplies broker selection, `Restart=no`, and the
transient start condition. Existing mesh configuration, supervisor state, runtime
control directory or service enablement causes fresh-install rejection.

No `voice-start-authorized.json` is ever generated by the installer. The root
daemon alone owns that transient condition; it is not a ready marker. The actual
root-owned 0600 per-unit `/etc/sage-lantern/mesh-runtime.json` and reviewed exact
`20-radio.conf` DeviceAllow still require operator provisioning under
`MESH_ROOT_DAEMON.md`. No A identity, peer list, receiver slot, key, or reset
approval is copied/defaulted. Missing configuration leaves mesh startup gated.

## Personal SAGE before owner PIN setup

Native app installation requires root-owned mode-0600
`/etc/sage-lantern/commissioning-complete.json`, bound to the same reviewed
manifest/unit and unchanged commissioned config/public-output hashes. It refuses
`commissioning.pending.json` (including a dangling symlink), missing/incomplete
receipts or changed outputs. It checks before native preparation and again before
installing files. Release check-only mode does not assert native commissioning.
See `IDENTITY_CONFIG_COMMISSIONING.md`; bindings alone cannot authorize app setup.

The base personal-SAGE unit no longer depends on `LOCAL_VOICE_READY`. It requires
the lock-required marker, app file-install receipt, and nonempty independently
provisioned config, Root key and CometBFT genesis/key/state files at its fixed
personal-SAGE paths. Operator-reviewed YAML must use that layout. File existence
is not verification of identity or encryption. No keys are generated by this
installer, and voice retains its separate readiness and broker gates.

Installation requires the installed personal-SAGE unit to match the reviewed
release exactly, with no `/etc` or `/run` unit drop-ins. An old base unit is
rejected without overwrite; updating an existing target requires separate review.
`--allow-owner-vault-setup` remains explicit: starting SAGE does not authorize
initialization or unlock it. The normal PIN verifier still rejects encryption
disabled, and protected-storage vault guards are unchanged. Native owner setup,
recovery confirmation and boot acceptance remain to be tested.

The personal-SAGE service explicitly overrides `REST_ADDR=127.0.0.1:8080`,
`SAGE_CMT_RPC_ADDR=tcp://127.0.0.1:26657`, `SAGE_TLS_ADDR=127.0.0.1:8443`
and the personal-mode `SAGE_CMT_P2P_ADDR=tcp://127.0.0.1:26656`. REST/TLS
environment overrides take precedence over YAML. The installer requires these
settings in the matched unit, the root-owned mode-0600
`/etc/sage/provisioning-profile.yaml`, and the `check-private-config` prestart
command. The profile declares `private_listeners: true`; it replaces the former
product-specific environment variable and command. The prestart command loads
the actual YAML with SAGE's own parser and environment precedence without
generating keys or starting listeners. Missing/default-enabled federation,
enabled federation, enabled quorum, or non-loopback effective
REST/TLS/RPC/personal-P2P addresses fail closed. `serve` repeats the same
validation on its own config load, avoiding reliance on a prestart-only check.
Public federation belongs in a separately configured gateway, not this
private-ledger service.

The service keeps `NoNewPrivileges=true` and a capability bounding set containing
only `CAP_DAC_READ_SEARCH`. That single read capability is required because the
profile remains root-owned `0600` while SAGE requires the initializing process to
run as the non-root service account.

An older binary lacks the prestart command and cannot start this unit: the next
matched SAGE artifact must include these source changes. The bundle now pins the
published 11.23.27 binary; no unit was updated or deployed here. The check does
not validate all identity/configuration policy or establish encryption. Native
acceptance must verify actual listeners, not infer isolation from a lock marker
or these source tests.
