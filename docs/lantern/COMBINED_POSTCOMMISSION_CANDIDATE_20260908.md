# Combined V3 candidate — independent review handoff

Host staging and separate frozen-payload verification completed with exit 0.
No device deployment, reboot, sibling writes, provider activation or physical
acceptance. Existing B manifest and earlier input snapshots remain untouched.

Root: `/private/tmp/lantern-combined-postcommission-20260908-v1`.
Canonical payload: `canonical/payload` (31,358 files).
Release ID: `lantern-contact-public-compact-postcommission-20260908`.

| File | SHA256 |
| --- | --- |
| `canonical/manifest.json` | `2026a8a2cd2f8782fb4f0a8f547218f7ea2bffbf0eff965ff3780799873de37b` |
| `canonical/selection.json` | `2ebf78aa42d4acf81be960b704efc23443f89646811a0dcdb1c7a0f0c6a05ac0` |
| `candidate.json` | `902ab137bf30ef06c30d8a8de19a5daec2622b5feca4a18b880eb87f3f5d9cdb` |

## Selected components

- Runtime V3: `f6b09e046f4e803c0c1fb9095e409d59922d42f2682d55a93ad8fa56ebcb11ef`.
- Staged compact context: `5129ad541c7ac7e3ccdaf86cdbe8c7587ca895f7d8058a7ca0467fa7bf2b815b`.
- Native: `387aee03d725c13bb064e24572de98096405831e929bc4aea616428ab2107421`.
- Native manifest: `6ec887feb44cef470233425c25c08272230a8e69fa8936c10fb97d189f21ec38`.
- SDK: `05fa8da878fb35906c0e50cfc74adfe713339486a911ba8b1c67e19b9cf9abd7`.
- UI source archive: `08e44968c1cd77e371a59b11c74b817dfac493da8cc294dd1bc11b6cc1bff9db`.

Native preserves the reviewed encrypted private-policy/resident-key base and
adds only the two contact admission files. SDK uses the corresponding reviewed
client/models. See `CONTACT_POLICY_CANDIDATE_BUILD_20260908.md` for full evidence.
All five V3 reviewed UI companions match the isolated UI source; its dist matches
the approved archive. The new artifact lock binds the replacements without
editing the frozen B lock. No V1/V2 runtime is selected.

Both generic handoff and postcommission runner are present with `review_only`
and null destinations. Clean-install orchestration exposes explicit phases;
packaging alone never installs/enables them or supplies device identity/setup/
commissioning/lifetime inputs. Per-unit live evidence is still required.

## Reproduction and verification

`scripts/lantern/assemble-postcommission-candidate.py`:
`a4b22e06b5e6b8b0d77d5a6c8d40ec366f5190486a113473d7411c5e10644223`.
This host assembler pins the exact selected artifacts, creates new source and
artifact roots, and delegates to the existing canonical stager. Old input trees
and manifests are never updated. Partial output is preserved on failure.

Assembly log: `/private/tmp/lantern-combined-postcommission-20260908-v1.log`.
Separate verification log: `/private/tmp/lantern-combined-postcommission-20260908-v1-verify.log`.

```sh
PYTHONDONTWRITEBYTECODE=1 \
/private/tmp/lantern-r8-installed-validation-20260907/bin/python -B \
/private/tmp/lantern-combined-postcommission-20260908-v1/canonical/payload/scripts/appliance-release-manifest.py verify \
--root /private/tmp/lantern-combined-postcommission-20260908-v1/canonical/payload \
--manifest /private/tmp/lantern-combined-postcommission-20260908-v1/canonical/manifest.json
```

Combined source suites: 138 tests and 103 subtests passed, one existing SDK
schema-shadowing warning. Manifest verification reports deployment readiness,
runtime acceptance and signed authenticity all false. Independent native/SDK
overlay and complete release review remain required before any deployment.
