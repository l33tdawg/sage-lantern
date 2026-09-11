# V3 compact payload verification

Parent independently inspected the actual V3 wheel, not just its build report.
Artifact: `/private/tmp/lantern-public-share-matched-20260908-v3/build-a/sage_lantern_runtime-0.2.0-py3-none-any.whl`.
SHA256: `f6b09e046f4e803c0c1fb9095e409d59922d42f2682d55a93ad8fa56ebcb11ef`.

All six `conversation*.py` archive members exactly match the approved compact
wheel in `/private/tmp/lantern-a-prompt-utf8-20260908-v1/build-a`:
context, records, sage, service, store, and workflow store.
Context SHA256 is `5129ad541c7ac7e3ccdaf86cdbe8c7587ca895f7d8058a7ca0467fa7bf2b815b`.
This prevents the context regression found in V2 from entering the matched release.

This proves payload preservation only. It is not device deployment, functional
acceptance of the combined release, or a measured latency improvement. A paired
post-update device benchmark is still required after audio recovery.

## Combined release verification

Parent separately ran the frozen payload's `appliance-release-manifest.py verify`
against `/private/tmp/lantern-combined-postcommission-20260908-v1/canonical`.
It exited zero for 31,358 files. The manifest independently hashes to
`2026a8a2cd2f8782fb4f0a8f547218f7ea2bffbf0eff965ff3780799873de37b`.
Output is retained in `/private/tmp/lantern-parent-combined-v1-verify.log`.

The verifier explicitly reports deployment readiness, runtime acceptance, and
signed authenticity as false. File integrity is not evidence of those claims.
Independent combined-source review and native integration testing remain open.
