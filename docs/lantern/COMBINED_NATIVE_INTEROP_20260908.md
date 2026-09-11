# Combined native + produced SDK host interop

Completed on host darwin/amd64, Go 1.26.0. No device endpoints, sibling source
writes, deployment, native ARM64 execution or physical/reboot acceptance.

Work: `/private/tmp/lantern-combined-native-interop-20260908-v2`.
`RESULT.json` SHA256:
`afca821371e42427d7d249e30e798d3052b51c38252993ab2697a1a64571a4c9`.

## Actual source and artifacts

All **757** production source inputs were copied from the combined candidate and
rehashed after tests against native manifest
`6ec887feb44cef470233425c25c08272230a8e69fa8936c10fb97d189f21ec38`.
None changed. This includes contact admission, resident-key handling and explicit
private-policy serialization, rather than the prior contact-only tree.

Four separately inventoried Darwin-only files from the reviewed isolated source
support host compilation. They do not replace Linux production inputs. Test
fixtures/declarations are isolated additions with their hashes in RESULT.json.
The fixture extractor retains selected Go AST declarations and their dependent
mock methods; it does not modify production behavior or fabricate test responses.
Extractor SHA256: `d8a9bb3432bd21081db2fbd3127a8b6cc80f7058f0907eab6a3cc9c9695e406f`.

The HTTP child imports the actual SDK wheel
`05fa8da878fb35906c0e50cfc74adfe713339486a911ba8b1c67e19b9cf9abd7`
and V3 runtime wheel
`f6b09e046f4e803c0c1fb9095e409d59922d42f2682d55a93ad8fa56ebcb11ef`.
No SDK source-tree fallback. The SDK wheel import path appears in the interop log.

## Exercised contracts

**11 ordinary Go tests + 9 subtests, then 1 real HTTP/Python interop test passed.**
Both commands exited 0.

- Real synthetic encrypted SQLite workflow storage: admitted contact kind,
  ciphertext excludes plaintext canary and kind, actor isolation and CAS conflict.
- Native and SDK unknown-kind rejection; owner/access guard denial.
- Real SDK → WorkflowStore → signed HTTP → native encrypted vault reads/writes.
- Owner preview/confirmation persists issued contact; reconstructed ledger retains
  reserved state and rejects a further reservation rather than blindly retrying.
- Removing the vault denies read/write; store-level unlock restores the record.
- Resident-key equivalence, concurrent publication, expected-state repair and
  lock/publication serialization regressions passed on the combined source.
- Explicit private policy survives serialization and toggling with protected
  identity/vault fields retained; generic defaults remain unchanged.

The listener is an ephemeral `127.0.0.1:0` httptest server, with synthetic keys
and vault files in test temporary directories. No installed device access epoch,
real agent identity, radio or public-chain governance was involved.

## Logs / reproduction

- `ordinary.log`: `77b90aa0fdb15cbdaa74abdac6739735bb11191126c5b852eacdd56bcdca94f1`.
- `interop.log`: `6101ebdc7f647f2438e1579471336adf9315ad9ddf8fa5fb3355711aea273e89`.

From the work directory's `source`:

```sh
export PATH=/private/tmp/lantern-r8-installed-validation-20260907/bin:/usr/local/bin:/usr/bin:/bin
export GOCACHE=/private/tmp/lantern-combined-interop-host-cache
export GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GOTELEMETRY=off
export CONTACT_INTEROP_ARM64_PYTHON=1
export CONTACT_INTEROP_PYTHONPATH=/private/tmp/lantern-public-share-matched-20260908-v3/build-a/sage_lantern_runtime-0.2.0-py3-none-any.whl:/private/tmp/lantern-contact-policy-candidates-20260908-v2/sdk-a/sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl
go test -p=2 ./api/rest ./internal/store ./internal/vault ./cmd/sage-gui -run '^(TestContactIssuanceNativeAdmission|TestContactIssuanceEncryptedStoreContract|TestResident|TestLantern)' -count=1 -v -timeout=180s
go test -p=2 -tags=integration ./api/rest -run '^TestContactIssuancePythonNativeInterop$' -count=1 -v -timeout=180s
```

V1 is retained separately: its incomplete extracted mocks failed API compilation
before interop assertions ran. V2 adds the existing interface methods to the
test-only fixture. No unrelated baseline or production fixes were made.

## Parent reproduction

Parent independently reran both commands against this candidate. Ordinary
tests exited zero (`/private/tmp/lantern-parent-combined-native.log`). The first
interop run was denied permission to bind localhost before exercising HTTP;
that result remains in `/private/tmp/lantern-parent-combined-interop.log`.
With approved ephemeral-localhost access, the same interop command exited zero
and verified the real SDK/workflow/HTTP/vault contracts and locked-vault denial.
Successful log: `/private/tmp/lantern-parent-combined-interop-localhost.log`.
This remains host-only synthetic evidence, not ARM64 or RF acceptance.
