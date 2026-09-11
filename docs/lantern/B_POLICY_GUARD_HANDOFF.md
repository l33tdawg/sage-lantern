# B policy guard ownership transfer

Source-only implementation, 2026-09-08. No B execution or deployment acceptance.
New owner surface: `scripts/lantern/handoff-b-policy-guards.py` and its tests.
Mendel's policy coordinator, mesh installer, collector, runtime and startup
helpers are unchanged. SAGE lookup found no Mendel recipient; parent must relay
this source split and obtain independent review before deployment.

## Scope and invariants

- Exact v5 policy coordinator SHA (accepted by Lovelace per parent;
  handoff deployment remains separately approval-gated)
  `33a8963dbd7d1fdf9d0bfc11593f66d86034f2e8cf4b2fb854a2320b0280bb9e`.
  Reuses its pinned helper/runtime closure, exact B identity/config/binary
  checks, native PID/start/invocation/listener and healthy boot. No key export,
  configuration rewrite, native replacement, service start/stop or enable.
- Handoff reuses coordinator `Backend.system`, now constructed with
  `b_suspend_system(self.preflight.SuspendSystem)`. Its containment and passed-in
  mesh service checks therefore use v5's exact runtime-mask gate; no R7 patch or
  replacement system instance is needed here. Separate mesh establish/install/
  release and first-audio helpers still instantiate ordinary R7 systems. With
  masks retained, those routes remain blocked. The separately reviewed candidate
  `B_PROVISIONING_SLEEP_TRANSITION.md` retires exactly four masks under owned
  logind inhibition through actual commissioning, without changing R7.
  Failed-original-release support is an explicit, separately pinned recovery-v5
  receipt consumer, not a rewritten original success. Its implementation remains
  deployment-review gated and requires fresh actual completion pins.
- Current B **explicit absent-idle** only. Loaded/appearing idle refuses this
  route; its ownership requires a separate reviewed extension, not deleting it.
- Every phase defaults to inspection. `--apply` exclusively claims root0700
  `/var/lib/sage-lantern-b-policy-guard-handoff/{mesh,native}` and fsyncs intent
  before the first removal. Archives exact old guard bytes before unlinking.
  Originals and policy transition evidence remain unchanged.
- No retry/resume, including unknown unlink/reload/metadata publication. Retain
  all journals, archives and successor guards for manual reconciliation.
  SIGKILL/power loss can interrupt the sequence: receipts are not containment.

## 1. Cold policy guards → established mesh owner

Prerequisites: successful policy release; real owner unlock; UI inactive;
fresh B with no mesh installation or prior audio lifetime. Parent first runs
the existing reviewed `install-prepared-fresh-mesh.py`
`--establish-fresh-b-containment` route, with **all existing policy drop-ins
included as originals** in its `service_files`. That route owns installing and
verifying its four guards. This helper will not invent missing containment.

The handoff verifies that completed containment receipt, current signed storage
epoch and empty workflows, effective negative conditions against the existing
root-owned containment directory, inactive services and physically empty
cgroups. It removes only the three policy model/broker/voice drop-ins, checking
successor guards before each removal and after reload. Native `Restart=no`
remains throughout. Mesh preparation remains pinned to B's exact ordinary
identity/UID/endpoint; peer/policy values stay as explicitly reviewed in the
mesh expectation, not silently replaced by empty discovery defaults.

The existing mesh expectation would otherwise retain stale original-file pins.
Under the still-effective mesh guards this route archives, then atomically
replaces each of `B_GUARD/expected.json` and `B_GUARD/complete.json`, changing
**only** the three removed pins and corresponding expected-document hash.
The original signed storage epoch is preserved, not copied from the older
pre-policy native lifetime. Interrupted two-file publication is unknown, not
success or an invented fresh containment observation.

After successful `mesh/complete.json`, the executable continuation is the
unchanged mesh installer using `mesh/mesh-expected.json` and its reviewed SHA:
default inspection → `--apply` installation → reviewed daemon reload → default
`--release-fresh-b-containment` inspection → that flag with `--apply`.
The installer removes only its own guards, never originals. No start occurs.

**First commissioning is still mandatory.** Install/verify the existing audio
guard/ALSA files using `install-audio-commissioning-config.py`; run the existing
`sage_lantern.audio_readiness --inspect`, then its separately consented
`--capture --expected-inputs-sha256 SHA --approve-microphone-capture` route.
Do not synthesize `LOCAL_VOICE_READY`, commissioning evidence or a lifetime.
Use the reviewed model-start route and `ColdBrokerBootstrap` with its exact
inventory only after real admission. This helper neither starts them nor
changes their readiness conditions. No RF delivery or radio-silence inference
follows from the absence of a send command.

## 2. Native restart inhibition → verified startup owner

Native guard removal is a separate phase, not a side effect of mesh release.
It requires the successful mesh handoff bound to the same policy expectation,
plus `handoff-first-audio-lifetime.py`'s **current** snapshot: real completed
bootstrap, active matching broker/voice/model, valid B commissioning and owner
access. The first-audio helper is pinned to
`61d4b353797c3f56fa1405e99374ad84d71a2b6d68a107c281ad9973c670ab59`.

That helper must already have produced its real completion and exact linked
documents. The resulting startup config must be installed, not merely staged.
The canonical startup unit (SHA
`ce82967f6738fe074ea984f236c14c59f27a38ddfd058e716d395b1714e65293`)
must be loaded, inactive with an empty cgroup, no extra drop-ins/job/reload,
`Restart=no`, and its canonical multi-user enable link present. Installation
and enablement are separate owner actions; this helper does neither.

Only then does this phase archive/remove the policy native guard, reload and
verify the original native `Restart=on-failure` with unchanged PID/invocation,
boot, executable, config, listener and active commissioned lifetime. Any failure
independently attempts restoring the exact native `Restart=no` guard, reload
and verification; restoration failure cannot suppress the other checks.
No active producer is stopped here. A failure label distinguishes verified
existing containment/ownership from unverified state; it never claims drainage.

## Public expected documents and invocation

All input files and code must be root-owned, non-writable by other users, with
trusted non-symlink parents. Invoke the staged helper using installed venv
Python with `-I -B`; no dependency download/install is provided here.
A pin is `{"path":"/absolute/reviewed/path","sha256":"64 lowercase hex"}`.
Common JSON keys (no extras):

```json
{
  "schema": "sage.lantern.b-policy-guard-handoff.v1",
  "phase": "mesh",
  "handoff_sha256": "REVIEWED_SHA_OF_THIS_NEW_HELPER",
  "coordinator": {"path": "/ROOT_STAGE/deploy-b-private-policy.py", "sha256": "REVIEWED_V5_SHA"},
  "policy_expected": {"path": "/ROOT_STAGE/policy-expected.json", "sha256": "REVIEWED_SHA"},
  "mesh_expected": {"path": "/ROOT_STAGE/mesh-expected.json", "sha256": "REVIEWED_SHA"},
  "mesh_receipt": {"path": "/var/lib/sage-lantern-b-mesh-containment/complete.json", "sha256": "ACTUAL_RECEIPT_SHA"}
}
```

For `phase: native`, replace `mesh_expected` and `mesh_receipt` with exactly:
`audio_handoff` (pin), `audio_tools` (pin to the first-audio tools document),
`audio_plan_sha256` (SHA of that helper's canonical current snapshot),
`startup_service` (pin at `/etc/systemd/system/sage-lantern-startup.service`),
and `mesh_handoff_sha256` (actual `mesh/complete.json` SHA).
Placeholders are not accepted evidence; parent supplies current reviewed pins.
For recovered release, add the identical optional `recovery_evidence` object
specified in `B_PROVISIONING_SLEEP_TRANSITION.md` to both handoff phases and the
sleep transition. Original coordinator stays pinned to v5 `33a8963d…`; approved
recovery-v5 `a66eae81…` and its observer `c04ad465…` remain separate authorities.
The failed original journal and absent original `released.json` are mandatory.

```sh
/opt/sage-lantern/venv/bin/python -I -B /ROOT_STAGE/handoff-b-policy-guards.py \
  --expected /ROOT_STAGE/handoff-expected.json --expected-sha256 REVIEWED_SHA
```

Only after independent review, explicit parent consent and successful current
inspection, append `--apply` to the identical invocation. Never reuse an attempt
directory. No operator-created readiness/receipt/token extraction shortcut.
Read-only inspection includes the existing signed local workflow probe using
the ordinary key internally; it does not export keys or perform hardware I/O.

## Tests and remaining acceptance

61 handoff tests plus existing first-audio/mesh tests: **128 passed** locally.
Helper SHA: `3ac5ae9b542620176190511ab733b9dbed4affe9a7e1548b640298732d193930`.
The new `verify_completed_native` API is read-only: it requires the exact native
attempt intent/completion, then runs current native/audio/startup verification.
It cannot replay transfer into an existing exclusive attempt. It permits the
sleep-transition holder to verify real completion before relinquishing inhibition.
V5 integration tests verify the exact coordinator binding, constructor wiring,
real scoped mask-gate invocation from handoff containment, rejected jobs/masked
service units, and forbidden actual suspend. Observations remain simulated.

```sh
LANTERN_B_POLICY_REVIEW_ARCHIVE=/private/tmp/lantern-b-policy-mask-review-20260908-v5.tar.gz \
PYTHONPATH=software/runtime/src PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider \
  scripts/tests/test_handoff_b_policy_guards.py \
  scripts/tests/test_handoff_first_audio_lifetime.py \
  scripts/tests/test_install_prepared_fresh_mesh.py
```

Tests cover pin projection, actual temporary-file preservation/publication,
exclusive attempts, missing/ineffective successor fences and populated cgroups,
ambiguous removal/reload/journaling, partial metadata
updates, independent native-guard restoration, current epoch binding, and
negative audio/startup ownership gates. Linux/systemd/API observations are
simulated: this is not hardware acceptance or a claimed completed deployment.
Initial broader run lacked `PYTHONPATH`; the corrected command above passes.
Parent/Mendel review, exact staged hashes and default target inspection remain
required. No existing source or collector was modified.
