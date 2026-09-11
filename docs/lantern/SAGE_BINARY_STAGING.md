# Offline SAGE ARM64 staging

2026-09-06. Local, unpromoted build procedure for Units A/B; **not an installation or public release**.

## Command

From `sage-commons`, with the sibling `../sage` worktree frozen:

```sh
python3 scripts/build-sage-release.py \
  --source ../sage \
  --output /private/tmp/lantern-sage-arm64-20260906-a \
  --build-id 20260906-workflows-a \
  --source-date-epoch 1788652800
```

Use a new output directory. Omit `--build-id` for a unique timestamp/UUID local ID. For a reproducibility comparison, use the **same** explicit ID and epoch, unchanged sources/dependencies, and the same toolchain, but a second output directory. Compare binary SHA256 and both manifests. Never overwrite an installed binary with this command.

The builder invokes, inside a temporary source-only staging tree:

```text
GOOS=linux GOARCH=arm64 GOARM64=v8.0 CGO_ENABLED=0
GOENV=off GOWORK=off GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off
GOVCS=*:off GONOPROXY=none GONOSUMDB=none GOFLAGS= GOEXPERIMENT=
go build -mod=readonly -buildvcs=false -trimpath -p=2 \
  -ldflags '-buildid= -s -w -X main.version=lantern-local.<id> -X main.commit=source-<inventory-prefix> -X main.date=<UTC-date>' \
  -o ../artifact/sage-gui ./cmd/sage-gui
```

An isolated `TEST_TELEMETRY_DIR` contains `mode=off`; the builder checks `go env GOTELEMETRY` reports `off`. This avoids altering the operator's Go configuration. The manifest records the exact command and controlled environment.

## Provenance boundary

- `go list -mod=readonly -deps -json` discovers the selected Linux ARM64 Go, assembly, header, object, and embed files. Source selection is **not based on git tracking**: new workflow Go files enter the snapshot. Test-only and other-platform Go files are not claimed as compiled inputs.
- Main-module and in-tree replacement inputs, including `third_party/cometbft` and its module metadata, are copied to a sterile tree. Only declared build inputs are copied, not repository state, SDK wheels, private keys, `.sage`, configuration directories, or caches. Secret-like paths/extensions and symlinks/hardlinks fail closed.
- `manifest.json` hashes selected local inputs, selected dependency/standard-library inputs, module metadata, and installed Go/compiler/assembler/linker binaries plus assembler headers. It records the final binary hash. This is local provenance, **not a signed supply-chain attestation** or independent authentication of the existing dependency cache.
- Dependency graphs and input hashes must match before/after the build and between live source and sterile stage. Failed builds or drift do not publish an output directory. Producers must remain fenced: before/after checks do not defeat malicious change-and-restore races.
- `build-info.txt` is host-side `go version -m` output. ELF header checks require little-endian ELF64 AArch64, and build metadata requires Linux/ARM64/CGO-disabled. Go omits linker flags from build-info when `-trimpath` is enabled; the manifest records those flags and checks linked version/source/date strings in the artifact. **The ARM64 `version` command is not executed here.**

## Dependencies and acceptance still required

Requires an already-installed compatible Go toolchain and complete cached dependencies. Missing modules/toolchains fail offline; the script never downloads, runs `go generate`, rebuilds browser assets, starts a service, or executes the target. Embedded browser assets are the exact existing `web/static` inputs, not a claim they were rebuilt from frontend sources. Go's normal build cache is used but never packaged.

The local version label deliberately does not impersonate `11.19.10` or another public release. No upstream version files change. Retain the matching separately staged SDK wheel/hash alongside this binary manifest; the SDK is not a Go input, so changing it does not change this binary. Pairing still requires the workflow API/SDK tests and explicit release evidence, not matching labels alone.

Cross-compilation and static inspection are not native startup, migration, API, secure-vault, messaging, or A/B acceptance. Target-side `sage-gui version`, controlled startup, and API validation remain separate owner-authorized gates. No device deployment is performed by this builder.

## Local staging evidence — 2026-09-06

**Historical checkpoint, superseded:** these artifacts predate the parent's SQLite `synchronous=FULL` DSN/PRAGMA change. Do not promote them as the final source freeze. A replacement snapshot must include that production change. The new connection/reopen test is validation evidence, not a compiled binary input; configuration checks alone are not a physical power-cut acceptance test.

- Installed toolchain: `go1.26.0` on Darwin/amd64. Dependencies were available offline; no toolchain/module fetch was needed.
- Two independent sterile source directories produced byte-identical `sage-gui`, `manifest.json`, and normalized `build-info.txt`, using build ID `20260906-workflows-a` and epoch `1788652800`.
- Outputs: `/private/tmp/lantern-sage-arm64-20260906-a/` and `/private/tmp/lantern-sage-arm64-20260906-b/`. These are temporary, unpromoted staging artifacts, not durable distribution locations.
- Version label: `lantern-local.20260906-workflows-a`; linked commit label: `source-dd372c296d07e8b5`.
- Binary SHA256: `0671d2a0c0b79a779734bd9ce446831077db330250cb5cbdb1411c9a5949f058`.
- Inventory SHA256: `dd372c296d07e8b5c713c20e33ad451eadddea80f111e086c8f9086b01001ed0`.
- Inventory: 751 local inputs including `api/rest/workflow_journal_handler.go`, `internal/store/workflow_journal.go`, and 43 `web/static` assets; 5,024 dependency/standard-library inputs. No git-tracked-only filtering.
- Host `file` inspection: ELF64 little-endian AArch64, statically linked, stripped. Target not executed, installed, or deployed. Builder-specific tests: 8 passed.

## Replacement FULL-durability checkpoint — 2026-09-06

This supersedes the preceding binary, but remains **unpromoted and subject to the parent's final source freeze**, not itself a final release. No media API changes are included.

```sh
python3 scripts/build-sage-release.py \
  --source ../sage \
  --output /private/tmp/lantern-sage-arm64-fullsync-20260906-a \
  --build-id 20260906-workflows-fullsync-a \
  --source-date-epoch 1788652800
```

- The sole changed compiled input relative to the preceding checkpoint is `internal/store/sqlite.go`: SHA256 `e73cd6492fa12689716257344210e8ef369a4f3ee4909853fc133bdee0d0e64c`. Selected dependency hashes are unchanged. Both SQLite DSN and explicit initialization PRAGMA now select `synchronous=FULL`.
- `TestSQLiteDurableCommitPragmasAcrossConnectionsAndReopen` passes locally: three simultaneously held connections, then close/reopen; `synchronous=2` and `journal_mode=wal`. This establishes configuration coverage, not hardware power-cut behavior or RF acknowledgement acceptance.
- A second build at `/private/tmp/lantern-sage-arm64-fullsync-20260906-b/`, with the same ID/epoch, yields byte-identical binary, manifest, and build-info. Both builds pass before/after source checks and ARM64/Linux static validation.
- Version label: `lantern-local.20260906-workflows-fullsync-a`.
- Binary SHA256: `68fae9d1b3af12f566ec8a2386d4e6a69290eae13b46de0c854c6a7952d6e43b`.
- Inventory SHA256: `d56b8c8b30e922dfc46c9a379cfd04686249c939573a3b4ee579cb15c1da6f86`.
- No target execution, installation, service operation, SDK modification, or upstream source edit was performed by this restaging step.
