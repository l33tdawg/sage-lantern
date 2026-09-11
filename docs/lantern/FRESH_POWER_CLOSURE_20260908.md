# Fresh A/B power closure

Source-only plan. No target changes or power activation authorization.

This describes the original source-closure task. Later B display installation,
activation and inhibitor reconciliation are recorded separately in
`UNIT_B_RELEASE_POSTCHECK_20260908.md`; physical and reboot acceptance remain
pending. No A device observations were made during this B postcheck.

## Actual gap

`UNIT_A_POWER_ACCEPTANCE_20260908.md` establishes a present A display approval
marker but disabled/inactive display service and absent idle-suspend controller.
Do not infer policy absence from ConditionResult=no. Neither that marker nor A's
historical physical wake result establishes B hardware capability.

`provision-fresh-appliance.py` delegates the app phase to
`install-fleet-appliance-local.py`, which explicitly excludes camera/display/
suspend acceptance and does not claim readiness. Onboarding is not a power
activation hook. There is no canonical post-onboarding power completion phase.

`stage-appliance-release.py` already carries display/session/idle helpers,
their units and the local installers. Its missing standalone first-audio/startup
closure is now required as review-only payload: installer, first-lifetime handoff,
coordinator, adapter, recovery helper, collector and startup unit. This does not
install them, modify the R7 wheel, mint evidence or enable a service. Rebuild and
review a new matched release; the frozen closure2 release is not changed.

## Concrete implementation order

1. Preserve independent per-unit identity/onboarding. Use the real first broker
   lifetime with `handoff-first-audio-lifetime.py --tools ... --tools-sha256 ...`;
   review its actual plan before `--expected ... --expected-sha256 ... --apply`.
   `install-startup-coordinator.py` installs/enables the resulting policy with
   pinned coordinator/config/unit but does not start another same-boot owner.
   This audio ownership prerequisite is not power acceptance.
2. Build a fresh A/B display activation helper, separate from A's historical
   `finish-fresh-display.py` camera-checkpoint continuation. Reuse
   `lantern-display-session.py --check --unit-id unit-a|unit-b`, followed by
   `--check-x11` (non-blanking). Pin actual session, X server/auth metadata,
   helper/unit and explicit fresh unit identity. Require completed owner setup.
   `install-display-power-local.sh --unit-id ...` installs only and deliberately
   leaves boot enablement unchanged; never overwrite a present policy blindly.
3. Record separate approved display activation and physical screen-off,
   touch-wake and PIN acceptance. Only then explicitly enable the existing
   display unit for boot, verify its enabled link and test reboot. A's current
   disabled service is the concrete unfinished activation/enablement step.
4. Replace the legacy `install-idle-suspend-local.py` deployment gap with a
   fresh matched A/B planner/installer. The existing command is a UnitA-stage-only
   plan, not a deployer: it replaces runtime modules/auth/voice arguments and
   stages model ExecStartPost plus sleep guards. Do not apply that overlay to
   exact-wheel R7 or treat its stage directory as an installed controller.
   Select/build one matched runtime; pin every final auth/voice/model drop-in,
   drain contract and source. Coordinate the model baseline hook and audio
   commissioning/startup inventories in the same release, rather than appending
   drop-ins after those inventories have been accepted.
5. Collect B-specific kernel sleep modes, wake devices, display session and
   systemd capability evidence. Keep the idle approval marker absent until
   guarded drain, suspend, wake, locked-session and model/broker recovery have
   passed on that unit. Enable the controller separately only after those tests;
   verify settings timers and reboot persistence. Do not infer suspend-to-disk,
   wake source, fan behavior or power-key semantics from A.

## Required follow-up tests and release artifacts

- The reviewed fresh display activator is now required in the canonical installer
  map, review-only without a destination or automatic activation. See
  `FRESH_DISPLAY_ACTIVATION.md` for its read-only collector candidate and phased
  activation contract. Fresh sleep commissioning still needs a reviewed generic
  installer. Do not package A's historical recovery journals.
- Require unknown/present policy handling, independent B identity, incomplete
  onboarding, changed X session, missing dependency, mask/inhibitor retention,
  partial installation and no-auto-enable regression coverage.
- Produce per-unit acceptance receipts that distinguish installed, activated,
  physically tested and boot-enabled. Never derive them from a file's presence.
- Preserve B's failed final inhibitor-release state for its separately reviewed
  reconciliation. No power commissioning or audio restart is part of this patch.

## Boot reproducibility review after B reconciliation

The installed B coordinator/adapter/recovery/collector hashes match current host
source and their configured pins. Startup is enabled but not started this boot.
The coordinator defaults to indefinite waiting for owner unlock, then admits one
attempt for the current boot; it selects a verified prior lifetime from
`latest.json` or the configured initial lifetime and requires a different boot.
The adapter remains the sole full-recovery owner. The installer enables startup
without starting a competing same-boot model/broker lifecycle.

Concrete closure gaps before calling fresh A/B provisioning reproducible:

1. `handoff-first-audio-lifetime.py` explicitly requires unit-b in unit metadata,
   tools, commissioning, plan and completion schema checks. Packaging that helper
   does not create an independent fresh-A first-lifetime handoff path. Generalize
   only through a reviewed per-unit binding and A/B negative identity tests;
   never copy B lifetime/actor/policy files to A or reuse historical A recovery.
2. Canonical staging includes the startup closure as review-only artifacts, but
   app provisioning does not invoke the first-lifetime handoff/install phase.
   Add an explicit post-onboarding/post-audio-commissioning phase using actual
   per-unit lifetime evidence and the matched runtime release. Do not silently
   enable startup against fabricated initial evidence.
3. Frozen closure2 remains immutable. Build and review a new matched release
   containing the accepted startup and display paths; do not treat current WIP
   runtime source as equivalent to the pinned R7 wheel. R7's installed modules
   must continue matching its wheel until a coordinated runtime upgrade.
4. Display activation and boot enablement remain separate. Enable display at
   boot only after that unit's screen-off/touch-wake/PIN acceptance. Fresh idle
   suspend installation and hardware-specific wake acceptance remain independent.
5. After owner approval, B's first reboot must demonstrate locked boot, owner
   unlock, one successful startup attempt, new durable lifetime and updated
   pointer, operational audio, and no stale provisioning masks. A's installed
   boot closure needs its own sole-owner inventory/acceptance; B's evidence is
   not an A acceptance result.
