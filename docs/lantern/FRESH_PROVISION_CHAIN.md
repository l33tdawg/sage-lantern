# Fresh provision command: wired phases and remaining handoffs

2026-09-06. Source implementation only; no native install, build, service action,
identity operation or release freeze performed. Synthetic tests run injected
commands only. This runner is not a claim of complete owner onboarding.

## Exact existing boundaries

1. `scripts/bootstrap-lantern-os.sh` checks Debian Bookworm ARM64/Python 3.11,
   compiler, ALSA and USB prerequisites. It does **not** supply Node >=22.13,
   GDM3/Chromium/X11 or an operator desktop account. Those remain reviewed OS-image
   prerequisites, not implicit downloads added by this runner.
   Before executing any base command, the phase runner also requires Node 22.13+
   at the app unit's exact /usr/bin/node path. An operator-shell PATH entry at
   /usr/local/bin/node is insufficient. Missing/old Node stops before apt or
   installation-root creation; supplying Node remains an explicit OS prerequisite.
2. `scripts/install-lantern-unit.sh` owns the destructive fresh-target guard and
   exclusive install-root claim. It creates the service account, installs the
   matched SAGE/runtime/SDK, builds pinned llama/Whisper sources offline and
   installs disabled base units. It creates no identity. Its runtime TOML is a
   generic template, not protected per-unit config.
3. `scripts/provision-lantern-unit.sh prepare` writes actual unit metadata and
   independent identity requirements. Its separate `verify` action writes a
   legacy PROVISIONED inventory marker; **the runner never calls it**.
4. The explicit `identity` phase now delegates independent per-unit private
   chain/Root/Companion and unenrolled public-key generation to SAGE's new
   initialization-only command, on the native unit only. Operator-reviewed initial
   YAML and protected runtime/media configuration remain separate inputs. No A
   state is cloned or chain ID guessed. `assemble-protected-config.py` produces
   disabled candidates, not automatic activation. See `FRESH_IDENTITY_SETUP.md`.
5. `install-fleet-appliance-local.py` checks matched installed sources and the
   fresh app target, then installs locked app/auth/status/voice/mesh/kiosk wiring.
   Explicit kiosk opt-in can enable app/auth/status for next boot, never unlock
   SAGE or create a mesh start grant.
6. **Onboarding startup is separate from voice activation:** personal SAGE now
   requires the lock-required marker, completed app file-install receipt, and
   nonempty independently provisioned config/Root/genesis/validator/node/state
   files at the fixed personal-SAGE layout. It no longer requires
   `LOCAL_VOICE_READY`; voice still does. These existence checks are not identity
   verification or encryption evidence. Operator-reviewed YAML must use that
   layout. Owner PIN initialization remains explicitly authorized separately;
   ordinary unlock still rejects encryption-disabled SAGE. The app installer
   rejects an old installed personal-SAGE unit or policy drop-ins rather than
   silently changing them. Existing targets require a reviewed base-unit update.
   No native startup/onboarding acceptance is claimed.
7. Socrates owns root mesh/public configuration and activation. Mesh preparation
   requires verified inventory/provisioning evidence, while fresh app installation
   rejects an existing PROVISIONED marker. Therefore app installation precedes
   that evidence-completion handoff; do not prematurely run legacy `verify`.
   `prepare-mesh-runtime.py` currently also needs explicit canonical selection;
   its ownership/config protocol is not modified or invoked by this runner.

## Actual phase runner

### Per-unit camera activation handoff

The current source camera installer requires an explicit stable USB camera path;
it no longer silently copies Unit A's Logitech serial to Unit B. Preview without
root, capture, hardware probing or installation:

```sh
bash scripts/lantern/install-camera-local.sh --device "$VERIFIED_CAMERA_BY_ID_PATH"
```

After matched runtime/app installation and mandatory device-lock provisioning,
on the authorized fresh native unit add `--install` with the same reviewed path.
This checks a real character-device target, rejects existing helper/unit/service
state, requires the mandatory access gate to deny anonymous access, writes the
per-unit ExecStart, then enables/starts only the on-demand camera service.
Startup/health performs no capture. A successful health response does not prove
focus, live preview, photo retention or vision inference; test those separately.
Do not rerun a fresh installer over Unit A's already installed camera service.

The plan's generated unit retains the existing privilege/network confinement.
Only `/dev/v4l/by-id/...-video-indexN` paths without shell/systemd expansion syntax
are accepted. Operator path selection is required; plan output is not physical
hardware detection. This source change is not yet in frozen r3/r4 or deployed.
The main phase runner still does not invoke this activation handoff automatically.

`scripts/lantern/provision-fresh-appliance.py` is now required as an exact
canonical installer source. It consumes `OUTPUT/payload`, `selection.json`, and
`manifest.json`, requires the operator's independently reviewed manifest SHA256,
and verifies canonical inventory and matching current source closure. It
re-verifies before each executed stage. No new progress/attestation marker is
created; the underlying installers own their actual fresh-state claims.

Plan the base sequence without target actions:

```sh
python3 scripts/lantern/provision-fresh-appliance.py \
  --output "$CANONICAL_OUTPUT" --manifest-sha256 "$REVIEWED_MANIFEST_SHA256" \
  --unit-id unit-b --phase base --allow-native-build
```

Only on the authorized fresh target, add `--execute` to run bootstrap `--check`,
the guarded base installer, and metadata `prepare`, in that order. Native source
compilation requires `--allow-native-build` even when planning so it is explicit.
`--install-os-packages` separately authorizes the existing bootstrap's apt/index
changes; absent this flag no package installation is attempted. Existing base
roots reject execution before bootstrap. The base installer's deeper unit guards
are retained. Failure stops the sequence; there is no automatic cleanup/resume.

After independent provisioning/config review, use the app phase:

```sh
python3 scripts/lantern/provision-fresh-appliance.py \
  --output "$CANONICAL_OUTPUT" --manifest-sha256 "$REVIEWED_MANIFEST_SHA256" \
  --unit-id unit-b --phase app --execute -- \
  --account "$OPERATOR_ACCOUNT" \
  --radio-inventory "$RADIO_INVENTORY" --radio-usb-serial "$RADIO_SERIAL" \
  --radio-vendor-id "$RADIO_VID" --radio-product-id "$RADIO_PID" \
  --radio-stable-path "$RADIO_STABLE_PATH" --radio-public-fingerprint "$RADIO_FINGERPRINT" \
  --audio-usb-serial "$AUDIO_SERIAL" --audio-stable-path "$AUDIO_STABLE_PATH" \
  --audio-vendor-id "$AUDIO_VID" --audio-product-id "$AUDIO_PID" \
  --alsa-device "$VERIFIED_ALSA_CAPTURE_DEVICE"
```

This executes the app installer's check-only validation first, then its actual
`--install`. The runner fixes unit, canonical release paths, and target metadata;
passthrough cannot override them or introduce private key arguments. Existing
explicit owner-vault-setup and reviewed GDM opt-in flags may be supplied after
`--`; see `FRESH_APP_INSTALLER.md`. Omitting `--execute` validates the release and
prints the commands without running even the target app check.

JSON output reports executed phase/commands and the next boundary. `ready` and
`provisioned` remain false: successful commands are not proof of hardware,
onboarding, encryption, RF delivery, or boot acceptance. This intentionally stops
short of an unsafe all-in-one identity/onboarding/startup script.
