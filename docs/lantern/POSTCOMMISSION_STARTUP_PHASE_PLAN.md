# Explicit post-commissioning startup phase — source plan

## Clean-install integration (source-only 2026-09-08)

The canonical stage now requires both generic entry points as `review_only`,
without an automatic install destination. Frozen releases remain unchanged;
rebuild and review a new manifest before using this integration.

The clean-install orchestrator exposes `postcommission-handoff` and
`postcommission-startup`. Invoke explicitly after app installation, completed
owner setup/recovery, real audio commissioning and first guarded broker bootstrap.
Matched static helper/coordinator installation remains a prerequisite; this
integration does not synthesize per-unit tools, process pins or acceptance.

Use existing `--output`, `--manifest-sha256`, `--unit-id` plus the phase and
`--startup-dependencies PATH --startup-dependencies-sha256 SHA`.
Without `--execute`, it validates the release closure and prints a child command.
With `--execute` alone, it runs the actual read-only target inspection.
After reviewing that result, explicitly add `--apply-postcommission`,
`--startup-expected PATH --startup-expected-sha256 SHA` and a new
`--startup-attempt-id UUID` alongside `--execute`. Each invocation applies only
one phase; collect a separate inspection before install-startup.

Handoff/installer/service paths and hashes must match the canonical payload.
Coordinator/adapter/recovery/collector/setup-observer and runtime wheel hashes
must also match the release. The child rechecks their actual installed paths,
identity, current access and lifetime. Mixed identities, expected plans or
base/app options refuse. Child failures stop without retry or the next phase.
No same-boot start, physical acceptance or live deployment is implied.

No live actions, reboot, deployment or claim of fresh-A acceptance. Existing B
and historical A receipts and installed helpers remain unchanged.

## Ownership boundary for coordination

Proposed owned files: new `handoff-fresh-audio-lifetime.py`, new
`provision-postcommission-startup.py`, their focused tests and this document.
Preserve `handoff-first-audio-lifetime.py` as the reviewed B historical implementation;
other pinned helpers depend on its exact SHA. Do not edit Curie's maintenance,
vision, recovery or active A/B configuration files. Coordinate this new-file scope
through the parent before implementation; no direct Curie notification is claimed.
Canonical source-map changes follow review, not this plan.

## 1. Independently bound generic handoff

Derive the new handoff from the existing reviewed first-lifetime implementation,
preserving its real bootstrap/broker, wheel, commissioning, source, model,
access-epoch and repeated-snapshot checks. Make only the unit-binding changes
needed for explicit fresh A/B operation. Require a declared `unit_id` with no
default, limited to `unit-a` or `unit-b`; require equality across tools, unit.env,
installed appliance plan/receipt, mesh configuration, commissioned audio receipt,
inspected plan and output receipt. Validate operator account and independent actor
identity through the existing installed records; never derive them from the unit
letter or accept another unit's files.

Use a distinct versioned schema for this new generic entry point. Do not reinterpret
old B receipts as new generic receipts. Keep generated startup policy/config in
the established coordinator schemas so the existing reviewed installer can consume
them. Retain exclusive writes, immutable real lifetime evidence and no service
starts/enables in the handoff. Existing per-boot directories cause refusal, not
resume/overwrite or a request to wipe history.

## 2. Explicit phase runner, not an app-install side effect

The new post-commissioning CLI takes reviewed pins for the generic handoff,
tools, startup installer, service unit and installed coordinator. Default inspection
must validate independent unit/setup completion, actual commissioning, active
verified broker lifetime, current boot/access epoch and fresh install destinations.
It returns an exact phase plan; it never tries to commission audio or start a
broker to satisfy its own prerequisites.

Expose separately reviewed operations rather than one implicit boot activation:

1. `inspect`: read-only fresh snapshot and predicted policy/config hashes.
2. `handoff`: consume that exact inspected plan once and write the independently
   bound real lifetime/policy candidates using the generic handoff.
3. `install-startup`: require the actual successful handoff receipt and all output
   hashes, revalidate current readiness, then invoke the pinned existing startup
   installer. This phase installs/enables startup for a later boot but never
   starts it in the commissioning boot.

Each mutation uses an exclusive phase intent and completion receipt. Installer
default preflight requires candidate files to exist, so run that real preflight
after successful handoff; do not fabricate a passing preflight before files exist.
If it fails, retain the valid handoff and failed phase evidence. No automatic
retry or destructive rollback. If a failure occurs after installing/enabling,
report actual partial state explicitly; never claim the service was not enabled
merely because the final receipt could not be written.

Do not call this phase automatically from the existing base/app installer. The
provisioning runbook must explicitly place it after owner onboarding, hardware
commissioning and first guarded broker bootstrap. A future orchestration change
can call the same phase only with those actual verified receipts.

## 3. Acceptance and tests before canonical inclusion

- Parameterized unit-a and unit-b positive paths with independent actor and device
  records, plus A/B swaps rejected at every binding boundary. Invalid/absent unit
  input must never fall back to B or legacy A.
- Uninitialized setup, missing commissioning, stale bootstrap, changed broker,
  model/source/wheel, boot or access epoch refuse before a handoff write.
- Existing historical directories/receipts remain untouched. Default inspect
  issues no service actions or persistent writes.
- Test the real generic handoff and existing installer interfaces together with
  synthetic filesystem/systemd. Missing candidate files must fail the installer
  preflight; full verified handoff must supply actual generated candidates.
- The install phase may call daemon-reload and enable only the startup unit;
  assert no start/restart, display changes, idle-sleep approval or audio mutation.
- Interruption, partial writes, installer failures and journal failures preserve
  exact evidence and do not authorize blind rerun.
- Explicitly separate installed/enabled startup from successful cross-boot
  recovery. Reboot acceptance remains per-unit, owner-authorized and unclaimed
  by these source tests. Existing B power acceptance remains pending.

After review, require both new entry points in the canonical review-only map,
rebuild a matched immutable release, and update the end-to-end runbook. Do not
copy current WIP runtime into the R7 wheel or transplant A/B identity/state.
