# Combined metrics and static startup candidate

New root: `/private/tmp/lantern-combined-postcommission-20260908-v2`.
Canonical manifest SHA256:
`07f79d30d8116beab5acdf6d7c07ef62fb3e721efac7dd23b7cfecd0da859612`.

Reviewed static patch `2a6343065149fd4024a548c5e7d25df6d4f805363bb70cd5934c0c00d2e24947`
was integrated with apply_patch after exact original runner/stager/test base
comparisons. The assembler additionally requires the matched observer as a
review-only source; it no longer silently skips missing companion sources.

Selected existing double-built metrics wheel:
`bbc7f20f7faaa505455906e5fc270ee97129076f1f5c1caa2c2a93d23a1fc443`.
Voice: `e4924af93cf042a3afa6602786978dd2f11ce96078b8a9ef113a1865fcbc6a8a`.
Observer: `222b6372d9593548e14ded4e50f1115e19b1326dc5ebedc56793aa0e593e1197`.

No new runtime build was claimed or needed. The measured input audit at
`/private/tmp/lantern-metrics-combined-inputs-20260908-v1/BUILD-AUDIT.json`
has SHA `b8477b245bf1cfc9dbf6beb7cdec3769e0924dc64c24d89507f5ba36bcddee2b`.
It checks both supplied wheel bytes, embedded manifest, all 100 source inputs,
both companion hashes and all six compact conversation modules against V3.
The measurement script is `/private/tmp/verify-metrics-combined-inputs-20260908.py`.

Canonical source tests: 92 passed, 105 subtests. Produced metrics wheel with
produced contact SDK: 150 passed, 42 subtests; existing schema-shadow warning.
Metrics log: `/private/tmp/lantern-combined-v2-metrics-tests.log`.
Assembly session 99971 exited zero. Frozen payload's own manifest verifier
session 90674 exited zero. Verification log:
`/private/tmp/lantern-combined-postcommission-20260908-v2-frozen-verify.log`.

Independent post-assembly byte comparison verified all 187 selected sources,
99 wheel modules, exact voice/observer pins and unchanged V1 manifest.
Payload delta from V1: three added files (static installer, observer, metrics
module), nine changed files (runner, stager, voice, three metrics runtime
modules, runtime wheel, destinations and lock), no removed files.
Native, SDK, UI and all six compact conversation modules remain unchanged.

Deployment, runtime acceptance and signed authenticity gates remain false.
No target operations, provider activation or frozen V1 modifications occurred.

Parent independently reran the frozen manifest verifier: exit zero, 31,361
files, exact manifest `07f79d30d8116beab5acdf6d7c07ef62fb3e721efac7dd23b7cfecd0da859612`.
Log: `/private/tmp/lantern-parent-combined-v2-verify.log`.
The same three readiness/authenticity flags remain false. Canonical helper,
runner and stager tests independently pass (56 tests, 105 subtests).
