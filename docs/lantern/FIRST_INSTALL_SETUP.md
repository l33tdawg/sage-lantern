# First-install setup implementation — 2026-09-06

Target experience: base OS → one bootstrap script → external browser for
network/hardware setup → touchscreen for passcode, recovery and device name.
This document distinguishes implemented components from the still-incomplete
fresh secure installer. Nothing described here has been deployed in this slice.

Subsequent narrow rollout: the power-settings module, auth/display helpers and
device settings UI are now on A. Network setup and boot theme remain staged.
See `UNIT_A_POWER_SETTINGS_20260906.md` for native tests, hashes and pending
owner acceptance; this does not complete the fresh install sequence.

## Browser network setup

The local admin Setup view links to `/admin/setup`. The new page talks only to
`127.0.0.1:3195`; it does not forward a device-unlock session or call SAGE APIs.
Its backend is `scripts/lantern/lantern-network-setup.py`, using
`sage_lantern.network_setup.NetworkSetup` for NetworkManager operations.

Initial access currently requires Ethernet (or separately established management
connectivity), authenticated SSH forwarding, and an explicitly started setup
broker. It is not an automatically advertised LAN portal or temporary hotspot.
Do not bind the UI/private daemons to all interfaces to make setup reachable.

After the reviewed software artifacts are installed on the target and prepared
unit metadata exists, the operator workflow is:

1. Keep management Ethernet connected and verify the target identity and Wi-Fi
   interface. Do not use A's address or credentials as a generic B default.
2. Forward operator-local ports 3100 and 3195 to the same target loopback ports
   using authenticated SSH. Browse `http://127.0.0.1:3100/admin/setup`, not a
   different hostname or port; the broker enforces exact Host and Origin.
3. In a controlling terminal on that target, explicitly start the broker with
   the installed runtime Python, `--serve --unit-id <prepared-unit>` and
   `--interface <verified-wifi-interface>`. Root authority is required for this
   narrow network operation. This is not an unattended systemd service.
4. Paste the freshly printed 15-minute setup capability into the browser. It
   stays in memory, never a URL, browser storage, environment variable or cookie.
   The backend retains a hash. It is not the device PIN or a universal password.
5. Scan, choose a visible supported network, enter its password, and connect.
   Verify the reported active SSID. Finish revokes the network capability; it
   does not certify complete provisioning or enable the remaining device stack.

The adapter supports visible open and WPA1/WPA2 PSK networks. Enterprise, WEP,
OWE, WPA3-only and hidden networks are not supported by this initial tool.
It does not replace an already active different Wi-Fi connection or touch
Ethernet. For a new profile, it verifies the active UUID/SSID before enabling
autoconnect, then reads the property back. Existing active profiles stay intact.
Wi-Fi association does not imply internet reachability.

Passwords reach nmcli through bounded stdin, not argv, logs, environment or a
helper-owned file. NetworkManager owns persistence of its profiles and secrets;
this is not a claim that OS credential storage is encrypted. Failed/uncertain
profiles are retained for review, not automatically deleted. Lost responses or
token expiry do not roll back an already-admitted network change: reconcile
status before retrying. The browser allows 180 seconds for a connect operation.

Native NetworkManager/Polkit behavior and reconnect-after-reboot still need a
controlled target acceptance test. Test fixtures do not establish these.

## Device name and screen/sleep settings

`Settings → Your Lantern → Change name` already exists and remains available.
It changes the local profile without rotating keys or losing memories. It does
not enroll or rename a verified federation directory identity.

New Screen and sleep controls save authenticated, revision-checked preferences:

- Screen off: 30 seconds, 1 minute, 2 minutes or 5 minutes.
- Deep sleep: 5, 10, 15 or 30 minutes, strictly later than screen off.
- Defaults: 30 seconds / 5 minutes. Delays are after locking and require idle
  input; they do not replace the separately persisted automatic-lock setting.
  Lock after now defaults to 300 seconds, with 60/120/300/600/900-second choices.
  Saved lock policy overrides the legacy service argument. See
  `UNIT_A_COMMUNITY_LOCK_20260906.md` for the deployed correction; a fresh image
  still needs the current runtime, auth helper and UI installed together.
- Deep sleep is explicitly **unavailable**. Its preference is saved only; no
  automatic suspend action has been added or enabled.

The auth service stores preferences privately under its state directory and
exposes only nonsensitive validated timing policy at `/power-policy`. The
display helper consumes it dynamically. Missing/corrupt policy prevents blanking
and attempts wake; a changed policy resets its eligibility period. No silent
default is applied by an old/missing backend.

Deploy this set together in order: runtime module → auth daemon → verify policy
endpoint → display helper → UI. Preserve the current A deployment until native
tests pass. Root-level automatic-suspend integration remains a separate gate.

## Boot screen

`software/plymouth/sage-lantern` contains a navy/yellow text-based Lantern theme
and retains password/question/message prompts and boot diagnostics. The
`install-boot-theme.py` tool defaults to prerequisite checks and a digest-bound
plan. Explicit root apply requires reviewed source/plan digests; it preserves
the prior theme configuration and initramfs for recovery.

No bootloader flashing, kernel command-line change, diagnostic suppression or
reboot is performed. Early RK boot text is outside Plymouth's scope. Native
rendering, initramfs compatibility and a recovery-backed reboot must be accepted
before calling this a finished branded boot experience.

## Remaining integration

- A complete sterile, authenticated release and one-script fresh secure install.
- Independent personal-SAGE/voice identity ceremony, guarded first exposure,
  hardware checks and owner passcode/recovery handoff without migration shortcuts.
- A simpler initial browser connection if we replace the current operator SSH
  tunnel; its pairing, encryption, timeout and teardown require their own design.
- Native Wi-Fi persistence, boot theme, preference-change and wake acceptance.
- Automatic deep sleep with positive service/request drain acknowledgements.

No PIN, Root key, recovery material, personal history or photographs should be
copied from A to provision B, or collected in setup evidence.

## Local validation

232 Python tests and 140 subtests pass across network adapter/broker, power
preferences, auth, shared access, display policy/service and boot-theme tooling.
Real ephemeral HTTP tests exercise token expiry, denied requests and the
authenticated preference → store → public timing policy → display-consumer path.
NetworkManager operations remain mocked; no native network changed.

74 frontend tests, lint and production build pass, including `/admin/setup`.
No browser visual QA, cloud deployment, Unit A rollout, Wi-Fi change or reboot
was performed. A real-HTTP test also exposed a final-response socket-close edge
case in the shared access reader; the targeted correction preserves deadline
and oversized/invalid-response rejection.
