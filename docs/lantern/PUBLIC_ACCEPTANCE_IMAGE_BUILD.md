# Offline isolated PUBLIC image recipe

Source tooling only: no compilation, binary execution, Docker build or ceremony
is implied by unit tests or dependency preflight. Parent review is required before
compilation and packaging; the four-node run has its own review gate.

## Inputs and scope

`scripts/build-public-acceptance-image.py` reuses the release builder's selected
Go source/embed hashing and private snapshot primitives. It builds Linux static
binaries on the host with `CGO_ENABLED=0`; SAGE SQLite uses `modernc.org/sqlite`.
No Linux build container, C compiler, Alpine libraries or base-image pull is
needed. The supplied cached Go/Alpine image IDs are therefore not inputs.

The three targets are deliberately distinct:

- `sage-gui`: production source, no fixture build tag, native embedded consensus.
- `v119-governance-fixture`: existing signed HTTP helper only, with its required
  `v119testfixture` tag; not a consensus runtime.
- `cometbft`: cached upstream v0.38.23 CLI, used only for `testnet` and
  `show-node-id`. SAGE's local replacement is a library subset without this CLI.
  The generator is not SAGE's patched standalone consensus runtime. Its selected
  source bytes are hashed; the documented upstream commit is a reference, not
  independently established Git provenance.

Host Go and all module dependencies must already exist. Networking, module
downloads, automatic toolchain selection, workspace overrides and ambient SAGE
configuration are disabled. Preflight uses a private temporary build cache and
reads selected module-cache sources. Missing dependencies fail with diagnostics.
The inspected host toolchain is Go 1.26.0, not the cached container's Go 1.25.13.
The actual compiler/linker/assembler and inputs are pinned in the manifest.

## Review and compile

RPC-helper revision dependency preflight (arm64, Go 1.26.0):

- Source inventory: `b0f677449d4fbb456e7360dc5bddc43ab723061f6ae5805d34d62fea25554b1b`.
- Toolchain inventory: `b0c95061a4c9a11fd511fbbd6e459182b59a93f041f2f798c32b9735de97008b`.
- Expected native version after compilation: `public-test.b0f677449d4fbb45`.

This revision adds unsigned fixed-loopback Comet RPC to the helper. The builder
automatically includes its selected source in the inventory; no pin bypass or
builder algorithm change is required. Compilation/image creation is pending
review. The older `6764ae...25cd` image is incompatible with the updated runner.

From sage-commons, first collect source and toolchain review pins:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build-public-acceptance-image.py \
  --phase preflight --arch arm64
```

Set `SOURCE_PIN` and `TOOLCHAIN_PIN` to the reviewed full SHA-256 values from that
output. For a Linux amd64 Docker engine use `--arch amd64` consistently and obtain
new pins. Optional `--phase stage --output <new-private-directory>` materializes
source snapshots and the recipe without compiling; its context has no binaries
and is not yet buildable. Never reuse a stage output directory for compilation.

After explicit compilation approval:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build-public-acceptance-image.py \
  --phase compile --arch arm64 --source-date-epoch 0 --reviewed-build \
  --expected-inventory "$SOURCE_PIN" --expected-toolchain "$TOOLCHAIN_PIN" \
  --output "$NEW_BUILD_DIRECTORY"
```

Output must be new and outside upstream source. The builder checks original and
staged dependency graphs, pins before compilation, rechecks inputs afterward,
and rejects dynamically linked or wrong-architecture ELF output. It records
binary hashes, exact build commands and Go build metadata. It never executes
target binaries or calls Docker. Source changes require new review, not bypassing
the pins. No private identity, SAGE configuration or whole repository is copied.

## Package, only after review

`context/` contains only three binaries, provenance manifest, an allowlisting
`.dockerignore` and `FROM scratch` Dockerfile. There is no `RUN`, remote base or
external Dockerfile frontend. Source snapshots remain outside the Docker context.

Parent must supply `LOCAL_DOCKER_HOST` as the already-reviewed local Unix socket,
`EMPTY_CLIENT_HOME` as a fresh private empty directory, and a local `IMAGE_TAG`.
Do not inherit Docker contexts, credential helpers or configured proxies:

```sh
env -i PATH="$PATH" HOME="$EMPTY_CLIENT_HOME" DOCKER_CONFIG="$EMPTY_CLIENT_HOME" \
  docker --host "$LOCAL_DOCKER_HOST" build --network=none --pull=false \
  --build-arg SOURCE_DATE_EPOCH=0 --tag "$IMAGE_TAG" "$NEW_BUILD_DIRECTORY/context"
env -i PATH="$PATH" HOME="$EMPTY_CLIENT_HOME" DOCKER_CONFIG="$EMPTY_CLIENT_HOME" \
  docker --host "$LOCAL_DOCKER_HOST" image inspect "$IMAGE_TAG" --format '{{.Id}}'
```

Use the resulting full immutable image ID for **both** launcher `--native-image`
and `--comet-image`. Never substitute the tag in acceptance evidence. Context
timestamps and binary inputs are fixed; repeat-build/image-ID reproducibility
has not yet been demonstrated, and image metadata depends on Docker's builder.

This scratch image is scoped to the isolated hash-embedding/local-HTTP ceremony.
It has no shell, CA bundle or optional llama reranker libraries; it is not a
general production image. Actual UID/read-only-root startup and ceremony still
require reviewed execution. Successful compilation is not consensus evidence;
matching canonical records and the post-acceptance H−1 barrier are independently
required by the launcher. Neither result is a portable BFT inclusion proof.
