# B display collection — 2026-09-08

## Latest activation checkpoint

The corrected caller uses the real auth contract: POST `{}` with the approved
loopback Origin, JSON content type and control header. A separate v2 collector
attempt succeeded with exit 0 and nonblanking preflight; all 106 existing
installed pins remained unchanged. Its expected-input SHA256 is
`4364b8024a9256b549f407138d9019590f12414fc25e79428d38675981a326f9`.

Subsequent guarded display-only activation succeeded with exit 0. Supervisor
PID 279119 and power child PID 279133 were verified, with zero restarts. The
controller is active but boot enablement remains off. Audio/auth/UI lifetimes,
sleep masks and the retained inhibitor were unchanged.

Completion receipt:
`/var/lib/sage-lantern-display-activation/284ae82c-25cb-46f8-a474-b88a93af71e9/complete.json`,
SHA256 `5b863c356aa58c6bda4b98d5995df97dbecadbb94453e866bb50b0c7686f352a`.
These are activation-time observations, not claims that process IDs remain
current indefinitely. Owner screen-off, touch-wake and PIN testing is pending;
no physical, deep-sleep, boot-enabled or reboot acceptance is claimed.

## Earlier installation and failed collection (preserved)

Display install-only completed earlier with exit 0. No display activation,
boot enablement, policy creation, audio change, or inhibitor release was performed.

Reviewed tools were staged exclusively at
`/opt/lantern-b-display-collection-20260908`:

- Activator: `b2019d49d29de8d054bb4f9578e08a594ff6004d875eba9006b2798f84ea8d35`.
- Collector: `d5a1a63e0c688e128f66c5f46c194e9a0e3d6803ac8153fc9df77acfb14ca54e`.
- `reviewed-inventory.json`: `4f2655dbb9d91c56d7d0f60e785df5a0787c3c5fe8f3add86d9f6e86477a3144`.

The inventory contains 107 pins. All 91 installed runtime Python sources matched
the frozen R7 wheel. Display helpers, kiosk, auth helper and generated auth unit
matched closure2 source or its exact installer transformation. B's generated
session launcher was preserved. Independent B receipt hashes were checked;
effective auth drop-ins were empty. These are provenance/preflight observations,
not physical power acceptance.

## Actual collector result

Collector exit **1**. Stdout was empty; no successful input envelope was produced.
The wrapper exited 0 after preserving the collector's result, not as a success
claim. Preserved files in the staging directory:

- `collection.stdout` contains a newline placeholder for empty stdout.
- `collection.stderr` SHA256:
  `4d40ead25f1155ea7e87a7a1d110d93cfda882ba2481fe8206fb12d8dce06ce9`.

The initial existing `check_x11` returned, but the complete collector preflight
failed at activator `setup()`: HTTP `/setup/status` returned **403**, 21 bytes,
JSON content type. No body or private vault data was exported. The pinned auth
source requires an allowed Origin for `/setup/status`; the activator sends none.
This is a concrete request-contract mismatch, not evidence of failed onboarding
or a need to unlock the vault. Correct and review the caller and its tests before
another collector attempt; preserve this attempt. Do not weaken auth validation.
Physical screen-off, touch-wake, PIN and reboot acceptance remain untested.

Subsequent source review also confirmed that this status route requires POST,
JSON `{}` and `X-Lantern-Auth: control`, not GET. The caller-only correction and
matched collector hash are awaiting review. The real HTTP auth-handler regression
covers missing/wrong Origin refusal, GET refusal with allowed Origin, correct
POST success, and the caller's before/after listener ownership checks using
synthetic proc evidence. Existing auth validation is not modified; no live retry
or replacement of the preserved failed-stage files has occurred.

## Separate security audit finding — unchanged

`systemd-analyze verify` reported a world-writable vendor unit. Read-only metadata
confirmed `/lib/systemd/system/rkaiq_3A.service` resolves to
`/usr/lib/systemd/system/rkaiq_3A.service`, root:root, mode **0666**, SHA256
`c0c011073f864ccd425c17f90cba29685cf6cdac4f6b3655b80a82653b837ebe`.

World-writable system service configuration permits local modification and can
affect privileged service behavior when systemd reloads/starts it. This observation
does not prove exploitation or establish which permissions the vendor intended.
Track a separate security remediation: verify package/vendor provenance and
contents, review dependent services, then approve a narrowly scoped permission
correction and validate persistence across reprovisioning. No chmod, unit edit,
reload for this finding, or vendor-service restart was performed. It did not make
the display unit validation fail and was not used as a reason to bypass checks.
