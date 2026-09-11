# Matched native/SDK candidate handoff

Host-only build, no sibling writes, target execution, deployment or physical
acceptance. Runtime V2 is **not selected**: await Lovelace's V3 compact-prompt
delta and reviewed new wheel/audit pins before canonical staging.

## Artifacts

Root: `/private/tmp/lantern-contact-policy-candidates-20260908-v2`.
Native version: `lantern-local.20260908-contact-private-policy`.

| Artifact | SHA256 |
| --- | --- |
| `native-a/sage-gui` (identical native-b) | `387aee03d725c13bb064e24572de98096405831e929bc4aea616428ab2107421` |
| `native-a/manifest.json` (identical native-b) | `6ec887feb44cef470233425c25c08272230a8e69fa8936c10fb97d189f21ec38` |
| `sdk-a/sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl` (identical sdk-b) | `05fa8da878fb35906c0e50cfc74adfe713339486a911ba8b1c67e19b9cf9abd7` |
| `BUILD-AUDIT.json` | `5973fc2e4bca169e7febe64de124afe3ca9b06a47d4cb6c3eecf3f4721454e3d` |
| `SOURCE-INPUTS.json` | `02870f6f72af46acf135a872cff43fe292adb6fa6c765ad08904af6aa8a97c76` |

Both native builds passed the existing offline source/dependency-graph, ELF64
AArch64, version and toolchain inventory checks. No ARM64 binary was executed.
Both SDK wheels passed the existing exact payload/metadata gates; 142 SDK
workflow/guard tests passed importing the actual produced wheel. Five narrow
overlay tests pass. This is not a full native integration/reboot acceptance.

## Private-policy and resident-key preservation

The reviewed contact-only isolated tree is not a complete sage-gui release tree.
Its `internal/vault/vault.go` lacks the deployed resident-key `SameKey` behavior;
copying it wholesale would regress the device. It also has unrelated store
differences. None were copied into this candidate.

Base is the exact reviewed private-policy/resident build, binary `5b8542bd...`,
manifest `49d78fd38adc97aa60930b58a48aacb2b2f7b74ec4a199d45b5096d27fd2cb90`.
Every base build source was rehashed before snapshot. Only:

- `api/rest/workflow_journal_handler.go`
- `internal/store/workflow_journal.go`

were replaced, after exact reviewed pre-patch and post-patch hash checks. All
other compiled sources, including config serialization/private transport policy,
resident vault, sqlite and badger, remain byte-identical to the base. The SDK
uses the independently preserved original source plus only pinned client/models
changes. No native identity, encrypted configuration, keys or device receipts
were consulted/copied. The recorded V2 public audit supplies contact source
provenance only, not runtime selection authorization.

## UI evidence

Parent-reviewed `/private/tmp/lantern-ui-typefix-dist-20260908.tgz` SHA256
`08e44968c1cd77e371a59b11c74b817dfac493da8cc294dd1bc11b6cc1bff9db`.
All 114 regular archive files exactly match the isolated dist tree; no symlink
or traversal members. All 112 entries in isolated `SOURCE_SHA256SUMS` match.
Source inventory SHA256:
`42d5f1a94ef405ffc9a8a3d744ed6944d1b893f4887851fa6919c84ebd5cf222`.
These checks do not imply equivalence with deployed assets or physical acceptance.

## Preserved unsuccessful attempt

`/private/tmp/lantern-contact-policy-candidates-20260908-v1` and its log remain.
SDK builds succeeded, then native build stopped on sandbox-denied default Go
cache writes. No native binary/completion was produced there. V2 uses a new
isolated cache and work directory. No failed evidence was overwritten.

## Build entry point

`scripts/lantern/build-contact-policy-candidates.py` uses pinned base/contact/audit
inputs, exclusive new output directories, existing native/SDK builders and two
build comparisons. It does not select a combined runtime, change canonical
stack locks, copy complete sibling trees, install or start services.

After V3 review: combine the agreed V3 runtime/source, these native+SDK artifacts,
the reviewed full UI, and verified unchanged dependencies in a **new** artifact
root/lock. Canonical source matching, fresh manifest verification and independent
release review must still pass. Do not reuse B's old manifest or V2 runtime.
