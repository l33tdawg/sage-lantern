# New postcommission/public release input closure

Source-only host staging completed; no device actions, reboot, canonical manifest
or deployable combined release. Historical B source/artifacts were read, not edited.

## Prepared input snapshot

`/private/tmp/lantern-postcommission-release-inputs-20260908-v1/source` freezes the
current required canonical sources, generic handoff/runner, runtime sources,
public build's companion sources and UI package/public inputs. `inputs.json`
SHA256: `d52ed0b0e1015114a7bd168a61886126699b72cc459c5b1cb2842f0981998199`.
This is an input inventory, **not** a canonical release manifest or acceptance.

New `scripts/lantern/prepare-postcommission-release.py` requires an exact pinned
public build audit, refuses existing work directories, compares wheel source
inventory and public companion hashes, and emits blockers with an explicit next
stage command. It never invokes canonical staging, installation or services.
The canonical source map now includes this preparer as review-only support.

## Concrete matching gaps

- All startup static helpers match the reviewed generic phase dependencies:
  coordinator `57d7fba9...`, adapter `4b9bb038...`, recovery `5f96803c...`,
  collector `40a4dd70...`, setup observer `81142b23...`, installer `d3fd22d0...`.
- Old artifact wheel R7 `6a8e99c7...` differs from the public candidate wheel
  `81f85b9a...`. Its embedded source manifest differs from this source snapshot
  in 25 paths (exact list in inputs.json). Do not label a repackaged R7 current.
- Public audit `/private/tmp/lantern-public-share-matched-20260908-v1/BUILD-AUDIT.json`
  SHA256 `15fd2025e8c2ca2cfcdac3bf64db06d89baf4e08f64573e62f14d2c110bc6789`
  records `workflow_store.py` as
  `a4f34198ecd0868605e3985b53b1bd836faa8e8f44e347af6763ebfcb5dc4d75`;
  current frozen input is
  `49279d577baa7eec3196d0be61aee67e0c0baded3dff94a63a034e8cf59c156e`.
  Resolve with the public/runtime owners, then rebuild a single wheel; do not
  splice the file into an already reviewed wheel or silently adopt old source.
- That audit explicitly has `ui_dist_built=false`, `sdk_native_selected=false`.
  UI distribution and native/SDK selection remain required, not implied by its
  reproducible runtime build. Preserve its no-activation status.
- Existing `assemble-independent-release.py` is deliberately hardcoded to old
  native `11f9a5cb...` and R7. It is untouched and must not produce this new
  combined candidate. Use the canonical stager with genuinely matched inputs.

## Next build sequence / parent coordination

Lovelace was not discoverable via the available SAGE agent lookup. Parent relay
is needed: reconcile the exact workflow-store mismatch and obtain the agreed
public/runtime/SDK/native/UI freeze before any combined build.

1. Review the resolved source snapshot; rebuild runtime twice using
   `scripts/build-runtime-release.py`, verify with `verify-runtime-release.py`.
2. Select independently reviewed ARM64 native and provenance manifest, matching
   SDK wheel/source snapshot, and UI dist/package-lock/public assets containing
   the reviewed HTTP/UI companions. No A keys, commissioning state or B receipts.
3. Populate a **new** artifact root with those files and matching lock entries;
   old caches may only be copied and reverified. Never rewrite the frozen B lock.
4. Re-run this preparer with the new pinned public audit and artifact root into a
   new work directory. Reconcile all blockers. `stage.inputs` passing is not the
   complete verifier: canonical staging must still run wheel/source verification.
5. Run the recorded canonical stage command with a new release ID/output, inspect
   generic entries and complete manifest, then review/package exact new hashes.
   No existing B manifest hash is reusable for this source closure.

Per-unit setup/lifetime inspection, guarded activation, and physical reboot
acceptance remain subsequent operations, not properties of this source snapshot.
