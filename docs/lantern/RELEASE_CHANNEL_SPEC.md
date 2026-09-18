# Lantern release channel — specification

Status: implemented and proven on Unit B, 2026-09-18. Written the same day
immediately after the Unit A re-anchor (`work/a-reanchor-20260918/`), which was
performed by hand and is the worked example this document generalises; the tools
are `scripts/lantern/{release-snapshot.py,build-release.py,install-release.py}`
and the run record is `work/b-reanchor-20260918/README.md`. Four deltas the run
forced are recorded at the end of this document rather than silently absorbed.

## Why

Owner direction: stop installing by hand; pull a release and run an installer, so
that Units C and D can be brought up without repeating the manual sequence.

The failure this prevents is not "code arrived". It is **code arriving without its
pins**. Both units drifted by installing sanctioned work outside the lifecycle,
which broke the bindings the startup path enforces, and every subsequent cold boot
refused. A release is therefore a transaction over code *and* the pins that
describe it — never a file copy.

## The bindings a release must satisfy

Four independent bindings must hold for a cold boot to complete. All four were
violated on Unit A. They are enforced in three places, and fixing only some of
them is what caused repeated failed attempts:

1. **installed runtime ≡ pinned wheel.** Enforced in the collector's
   `runtime_sources()` (set equality over every `sage_lantern/**.py`) and again in
   the coordinator's `verify_wheel()`. Both hold allowlists of accepted wheel
   digests; a new wheel digest must be added to each.
2. **the commissioning receipt binds every runtime module.** `audio-commissioned.json`
   lists 129 inputs, 120 of them runtime modules. Any runtime edit invalidates the
   attestation, and a fresh capture is the only way to mint a new one.
3. **prior lifetime inventory ≡ live configuration.** Enforced in the adapter's
   `validate_prior()` *and* the coordinator's `lifetime()` via `previous()`.
4. **prior broker id ≡ live supervisor fence.** Enforced in `validate_prior()`.
   An out-of-lifecycle broker start rewrites the fence and orphans this binding.

Corollary for the collector: it compares with `rglob('*.py')`, so **macOS
AppleDouble `._*` files count as modules**. Any wheel describing a deployment must
include them, because the receipt already binds them.

## Evidence that shaped the design

- `read_source()` refuses zero-length files. A refusal that writes no stderr
  cannot be read with it; use `read_public()` for journal rows.
- The adapter's `main()` calls `collector.runtime_sources()` **before** `run()`,
  so a runtime mismatch masks every later binding. Diagnose in execution order.
- The adapter prints only `{"error_type": "ValueError"}` for any failure, so the
  journal cannot say which check refused. `work/a-reanchor-20260918/adapter_probe.py`
  loads the adapter as a module and calls `main()` so the traceback escapes.
- The coordinator's `Journal` opens its file `O_EXCL`: **one lifecycle attempt per
  boot**, and a mid-run refusal is permanent for that boot.
- An allowance added to relax a binding must be a **fallback, not an override**.
  The first version required the pinned deviations to exist, which would have made
  the *next* boot refuse once nothing deviated — the same "works once, fails on
  reboot" class of bug this work exists to remove. Strict binding must always win.
- A wheel must be built from **deployed** bytes, not the repo working tree: on A
  the working tree carried an undeployed `mesh_runtime.py` (`dd44433a`) while the
  unit ran `7899e7cb`.

## Required release contents

Each release carries, with digests, and installs them as one transaction:

| Artifact | Purpose |
|---|---|
| `sage_lantern_runtime-<ver>-py3-none-any.whl` | runtime, matching deployed bytes |
| `collect-cross-boot-audio-pins.py` | collector; carries the wheel allowlist |
| `recover-cross-boot-audio.py` | recovery helper |
| `startup-audio-adapter.py` | adapter; carries helper/collector digests |
| `startup-coordinator.py` | coordinator; carries the wheel allowlist |
| `baseline.json` | next generation's accepted starting state |
| `policy.json` | pins helper, collector, wheel, baseline |
| `startup.json` | pins adapter, policy, coordinator (and optionally an allowance) |
| `manifest.json` | digests of all of the above plus the target boot |

## Install sequence

1. **Verify** every digest in the manifest against the release, refusing on any
   mismatch. Refuse to run on a boot id other than the one the manifest names.
2. **Preflight (read-only).** Build the new policy and config in a root-`0700`
   staging directory, run the adapter with `--apply` omitted, and require
   `preflight_verified: true` with `services_started: false`. Nothing in
   production may change before this passes.
3. **Require a held owner session.** The lifecycle asserts the access epoch is
   constant across the whole run. At boot the unit can report `locked:false` with
   a transient epoch, which fails mid-run and burns the boot's only attempt. The
   installer must establish or require a held session, or refuse to proceed.
4. **Mint the baseline** from the verified live state (commissioning receipt, model
   files, supervisor tree and signed workflow scan all verified by the collector).
   This is a generation advance, not a bypass.
5. **Apply** by replacing the coordinator and config, then running the lifecycle
   in a separate operator journal, so the real boot journal is never rewritten.
   Advance the durable pointer only after verified completion.
6. **Record** `complete.json`, and set `another_reboot_tested` honestly.

## Non-negotiable properties

- Old artifacts are preserved byte-for-byte (`*.before`), never rewritten.
- No historical record is rewritten, replayed, deleted or declared drained.
- `historical_outcomes` stays `unknown`; no delivery or drain is ever claimed.
- One attempt per boot; no automatic retry and no boot loop.
- A partially applied transaction is rolled back from the preserved originals, and
  the rollback is recorded.

## Acceptance before Units C and D

The release path is not trusted until, on a unit that has already been through it:

1. `preflight_verified: true` on a clean tree,
2. a **cold boot with the installer holding the owner session completes the
   lifecycle** and advances the durable pointer to that boot's lifetime,
3. a **second consecutive cold boot** also completes, since the first-boot fix was
   what the 2026-09-11 repair lacked,
4. `systemctl --failed` is empty and every producer is active.

Unit B is currently one cold boot from the failure described above and must be
reconciled before it is rebooted, or before it is used as the installer's test
target without a rollback plan.

## Implementation record and deltas (2026-09-18)

Unit B was reconciled with release
`sage.lantern-release-lantern-b-20260918-152000`, installed on boot `e0871a92`
in `next-boot` mode while the unit was serving, then cold-booted twice:
`524bea46` and `05694f2b` both reached `operational` and advanced the durable
pointer, the second on the strict binding with no allowance consulted.
`systemctl --failed` was empty and every producer active. Full acceptance
question 3 is satisfied; the evidence is under
`work/b-reanchor-20260918/evidence/`.

Unit A then took the same generation — release
`sage.lantern-release-lantern-a-20260918-152500`, identical wheel/collector/
coordinator/helper digests, no allowance because A's prior binds strictly — and
passed both of its cold boots (`3bad3898`, `2ecc6e74`) with a held session,
`another_reboot_tested: true`, all six services active and no failed units. The
boot-time epoch defect that refused A on 2026-09-18 is closed on the unit it
was found on, and the two units now run identical startup artifacts.

1. **A release carries candidates; the installer mints.** Baseline, policy and
   config are shipped with digests *and* re-derived on the unit from live state.
   The install refuses if the live state no longer matches what the release
   describes. Step 4 above stays authoritative.
2. **Preflight has two modes.** The adapter preflight (with `--apply` omitted) is
   only possible when the current boot's attempt is unspent and the durable
   pointer does not name the current boot. Otherwise the installer runs a
   *structural* preflight — the same digest, wheel, baseline, prior/fence/delta
   and constant checks, without constructing a `Recovery` — and records which
   mode ran. A `next-boot` install requires the current boot's attempt to be
   spent so the installed release cannot race a pending one.
3. **An allowance is prior-scoped, and its fence binding is broker-scoped.** A
   tolerance names either the boot that spends it or the prior lifetime's boot,
   because a release installed on one boot is spent by the next. Its fence entry
   pins the broker the fence names and accepts the phases the lifecycle accepts
   (`active`, `stopping`, `stopped_verified`): a clean stop between the review
   and the boot legitimately rewrites the fence. The digest stays as audit
   evidence. Everything else — exact prior pin, exact inventory delta,
   `historical_outcomes: unknown` — is unchanged.
4. **The boot-time epoch rule is a settle window.** The coordinator requires one
   access epoch to survive 30 s before it creates the boot's single-attempt
   journal, and verifies the bindings before charging the attempt. A transient
   unlock therefore delays the lifecycle instead of burning it, and a session
   that lapses between the window and the verification is waited out rather than
   charged. This is the defect that refused Unit A's boot on 2026-09-18 and it is
   fixed in the coordinator this release installs.

## Second generation, same evening — the airtime-gate re-anchor (2026-09-18 late)

Release `sage.lantern-release-lantern-b-20260918-214500` took the release channel
over the messaging session's epoch-restart build *with* the airtime gate and push
capture: wheel `e236036f…`, collector `df9ab182…`, coordinator `019e1781…`,
adapter `88d180a8…`, helper `9d28f920…`. It needed an allowance for one
broker-scoped fence deviation and no inventory delta; both cold boots
(`19b890ee`, `9279602f`) reached `operational` and advanced the pointer, the
second on the strict binding. `accept` recorded `another_reboot_tested: true`.
Unit A took the same generation as
`sage.lantern-release-lantern-a-20260918-221500` (identical wheel and tool
digests, its own baseline and allowance) once the owner powered it back on at
22:07 — it had dropped off the network at 21:36 with nothing installed. Both of
its cold boots (`89e06274`, `f3b603c0`) also reached `operational` on the strict
binding for the second, and `accept` recorded `another_reboot_tested: true`.
Both units now run byte-identical runtime modules with the gate installed
dormant (no `airtime` config key). The run is
`work/reanchor-gate-20260918/README.md`.
