# Isolated native PUBLIC acceptance ceremony

Source implementation; **not yet exercised against Docker**. Unit results are
command-contract simulations, not four-validator consensus evidence. Actual run
requires parent review and `--reviewed-run`. There is no permanently blocked
run facade and no image pulling, image building or governance timing override.

## Prerequisites

Explicit run also requires `--expected-native-version public-test.b0f677449d4fbb45`
and `--expected-native-fork 1` for the new source-pinned artifact, pending build
review. The version
must match the pinned binary's complete version-output shape before generation.
Fork 1 is an operator-reviewed source pin from `fork_version.go`, not a value
queried from the version command. The runner creates `version.txt` and
`fork-version.txt` exclusively in newly created node homes before any node starts.
Any existing node home, genesis or terminal result refuses preparation; this
never repairs failed homes or bypasses upstream migration guards. New artifacts
require review of their version and fork pins rather than a `dev` build bypass.

- Local Docker Unix socket and existing **Linux** images identified by exact
  `sha256:<64 lowercase hex>` local image IDs. Remote Docker contexts/SSH/proxy
  environment is not inherited. Compose is not required for execution.
  Each Docker command uses a fresh empty private `HOME`/`DOCKER_CONFIG`, so host
  credential helpers and configured container proxies are not read. Generator
  containers are limited to one CPU, 2 GiB memory and 256 PIDs as well as having
  networking disabled.
- Native image: `/app/sage-gui` built from the reviewed production source **without
  `v119testfixture`**, plus `/app/v119-governance-fixture`, the existing signed
  HTTP client built from `../sage/cmd/v119-governance-fixture`. That client requires
  its build tag; it does not run ABCI or change consensus timing. Do not use the
  fixture-tagged native node from the old chaos image. The production
  `deploy/Dockerfile.abci` contains only `amid` and is NOT a suitable native image.
- Generator image: compatible reviewed CometBFT v0.38 binary available as
  `cometbft`, supporting `testnet` and `show-node-id`. Pin the image corresponding
  to the native node's reviewed Comet source. Both images must already exist;
  the launcher does not construct them or acquire dependencies.
- Images must run under the host numeric UID:GID, with read-only root filesystem,
  no capabilities, a writable `/fixture`, and a 128-MiB `/tmp`. The native image
  can use the static, pure-Go SQLite build described in
  [the offline image recipe](PUBLIC_ACCEPTANCE_IMAGE_BUILD.md). Native `version` and
  signed-client `identity` are checked before services start. Version text
  containing `fixture` is rejected, but text is NOT a replacement for reviewing
  the pinned artifact's production build provenance.
- Python 3 with PyNaCl, used only to generate fresh synthetic seeds on explicit
  run. No SDK default identity lookup. Minimum preflight: four Docker CPUs,
  12 GiB Docker memory, 8 GiB free workspace disk. Runtime allocations are four
  CPUs total and 2 GiB per native container; resource exhaustion fails the run.
- Production upgrade delays are retained. Default whole-ceremony deadline is
  12 hours, configurable between 300 and 86400 seconds. Each activation wait is
  bounded at 30 minutes. No claim is made that the minimum timeout can succeed.

Building the pinned native image is an explicit operator prerequisite, not an
upstream missing enrollment feature. `scripts/build-public-acceptance-image.py`
snapshots selected source/dependencies and prepares an offline host cross-build
of all three binaries with separate tags. See the offline image recipe for
review pins and explicit compilation/packaging steps. The acceptance launcher
does not execute these builds, fetch modules, or select a base image.

## Commands, from sage-commons

```sh
python3 scripts/lantern/run-public-acceptance.py --phase dry-run \
  --native-image "$NATIVE_IMAGE_ID" --comet-image "$COMET_IMAGE_ID"
```

`dry-run` has no Docker/filesystem mutation. `build-config` writes an owner marker
and reviewable four-native-node Compose equivalent into a new private workspace,
but generates no keys and starts nothing. `preflight` only inspects Docker and
the explicitly supplied images; a successful preflight reports acceptance
`not_run`, never `passed`.

After parent review only:

```sh
python3 scripts/lantern/run-public-acceptance.py --phase run --reviewed-run \
  --native-image "$NATIVE_IMAGE_ID" --comet-image "$COMET_IMAGE_ID" \
  --expected-native-version public-test.b0f677449d4fbb45 --expected-native-fork 1 \
  --timeout-seconds 43200
```

Run always creates a **fresh** workspace; it cannot adopt a config-only workspace,
retry an old proposal, or point at an existing SAGE home/API. It prints the owned
project/workspace before work and metadata-only upgrade progress. Success requires
both acceptance assertions and confirmed teardown. Failure exits 2, identifies
the phase, and sanitizes subprocess errors. No CLI output dumps keys, node logs,
or arbitrary HTTP errors.

## Actual production flow

1. Check native binary; generate four validator/node keypairs via the pinned
   Comet `testnet --v 4 --o /generated --hostname-prefix node
   --populate-persistent-peers` in a network-none, owner-labelled helper container.
   Inspect only public generated genesis and `show-node-id` output. Bind all four
   homes to one unique `lantern-public-<UUID>` chain/genesis and explicit P2P peers.
   Native Comet runtime uses `quorum` config, not generated `config.toml` overrides.
2. Generate one synthetic operator, heartbeat Member and PUBLIC proposer with
   independently generated Ed25519 seeds. The synthetic governance credential and
   test-agent credentials are installed in each isolated home so native key
   resolution works consistently; the four consensus validator/node keys remain
   distinct. This is one test governance authority, not four independent human
   operators or a recommendation to clone real user credentials.
3. Run four `/app/sage-gui serve` processes: embedded Comet plus independent
   SQLite/Badger stores, hash embeddings, required real per-node auto-voters,
   federation/libp2p disabled. No host ports are published or requested.
   Internal P2P stays on the owner-labelled internal Docker network. Comet reads
   use `docker exec <owned-node> /app/v119-governance-fixture --path <path> rpc`:
   unsigned GET only to fixed `127.0.0.1:26657`, without loading any key. The
   allowlist is `/status`, `/abci_info`, `/block?height=<positive decimal>` and
   `/validators?height=<positive decimal>&per_page=100`. No redirects or proxies;
   five-second HTTP deadline, 1-MiB JSON limit and ten-second exec deadline.
   The runner rejects duplicate JSON fields and still binds all four observed
   node identities/chain IDs and the common post-acceptance header. Both requested
   and effective host publication must remain empty. The helper requires a newly
   reviewed build/image; the previous image does not implement this RPC mode.
4. Verify live Comet node IDs and chain against generated expectations. Register
   the synthetic operator as legacy admin before app-v9 closes self-grant; also
   register the ordinary heartbeat Member. Wait for the native quorum watchdog's
   frozen legacy app-v6 transition/backfill, rather than racing it with v2–v5
   proposals. This is the production path in `upgrade_watchdog.go`, not writing
   an app version into storage.
5. Invoke native `upgrade propose --target N --yes --rpc
   http://127.0.0.1:26657 --agent-key /fixture/agent.key` inside node0 for every
   target 7 through 25. Wait for all four actual `/abci_info` versions after each
   proposal. The CLI implements the app-v20 ceremony. Use separately signed,
   idempotent Member registration heartbeats while waiting; do NOT rely on Root
   re-registration or the CLI's Root `--wait` heartbeat after app-v23. A failed
   upgrade command is never blindly reproposed.
6. Register the fresh PUBLIC proposer through actual `POST /v1/agent/register`.
   Verify pending approval through **`GET /v1/agent/me`**. Send current-Root-signed
   `PUT /v1/dashboard/network/access/agents/{agent_id}/policy` from node0's own
   loopback namespace with:

   ```json
   {"role":"member","profile":"standard","home_domain":"public-acceptance","clearance":0,"capabilities":0}
   ```

   The native handler resolves the exact target seed from `SAGE_HOME/identities`,
   signs target consent and submits real `LocalAgentApprove`. No store injection,
   browser-header impersonation, admin-as-proposer or fake active profile.
7. Require the exact active ordinary profile on all four nodes. Persist synthetic
   public-QA intent before a single signed `POST /v1/memory/submit` with
   `memory_type: inference`, `confidence_score: 0.5`, classification 0 and the owned
   home domain. Persist the returned UUID/hash/commit height. Lost response is
   unknown; no retry or semantic-search receipt invention.
8. Poll four signed `GET /v1/memory/{id}` responses for exact author, domain, type,
   classification, original content/hash and lifecycle `committed`. Submit's
   transaction commit plus lifecycle `proposed` alone never passes.
9. **After all four accepted record reads**, capture each node's current height
   and persist their maximum as the acceptance-observation barrier B. It is not
   asserted to be the exact lifecycle transaction height. Keep this barrier
   fixed, heartbeat as necessary, and wait until all four nodes reach B+1.
   Query exactly header H=B+1 and validator sets at H on every node; verify the
   distinct identities, pinned four-validator key/power sets and equal nonempty
   AppHash. Header H covers state B. A header covering only the earlier submission
   height is insufficient. Persist evidence only after these checks.
   `historical_app_version` comes from the header's own `version.app`;
   `current_app_versions` are separately labelled point-in-time `/abci_info`
   observations, not historical evidence. No vote counting establishes truth.

Native sources: `../sage/cmd/sage-gui/node.go:690` (quorum runtime),
`../sage/cmd/sage-gui/upgrade_watchdog.go:152` (watchdog),
`../sage/cmd/sage-gui/upgrade.go:257` (upgrade CLI),
`../sage/cmd/sage-gui/rbac_keys.go:172` (identity inventory),
`../sage/web/appv23_access_handler.go:1004` and `:1117` (policy/consent),
`../sage/sdk/python/src/sage_sdk/client.py:719` (profile path),
`../sage/api/rest/memory_handler.go:36` (actual submission wire fields).

## Isolation, cleanup and evidence boundaries

On failure, before teardown, the runner attempts private startup diagnostics for
owned native containers only. It checks owner label, expected name and pinned
image before requesting the last 200 log lines by container ID. Each command has
a five-second deadline; at most five IDs are inspected, with only the four native
names eligible. At most 256 KiB per log is retained in exclusive mode-0600 files
inside the mode-0700 synthetic workspace, plus minimal exit/OOM state.
Private state metadata also preserves bounded
`NetworkSettings.Ports` and `HostConfig.PortBindings` from the initial admission
check and the pre-cleanup inspection, distinguishing requested from observed
publication. Only port/protocol keys and numeric IP/port bindings are retained
(32 ports, eight bindings per port); invalid metadata is marked unavailable.
Port admission checks are unchanged. Logs merge
stdout and stderr, remain local, and are never printed in CLI results. They may
contain synthetic sensitive material: do not publish raw diagnostics. Failure
to collect diagnostics does not skip cleanup or replace the original failure.

No shared upstream genesis, `.env`, named production volume, host network, Docker
socket mount, external P2P publication or default ports. All images have `--pull
never`. Temporary generator containers have no network. Owned workspaces are
0700; fresh credentials/evidence are exclusively created 0600. Generated symlinks
and hard-linked files are rejected before copying. Trusted local image, Docker
daemon and temporary-parent integrity are assumptions, not sandbox proofs.

The launcher handles SIGINT/SIGTERM through bounded teardown. It removes only
containers/networks with the exact owner label AND expected names, rechecks
absence, then deletes generated genesis/node directories (including synthetic
seeds). Metadata, intent, submission and result remain private in the workspace.
Cleanup uncertainty prevents a successful result and preserves files for review.

SIGKILL/host failure cannot run cleanup. Use the printed workspace, after review:

```sh
python3 scripts/lantern/run-public-acceptance.py --phase cleanup --reviewed-run \
  --workspace /absolute/path/printed/by/the/launcher
```

Cleanup validates the owner marker/mode/path and exact resource labels; it never
loads node keys or resumes a proposal. Do not run cleanup concurrently with its
active launcher. The same local UID/Docker operator remains trusted; metadata is
not an authorization boundary against that operator. No automatic `make down-clean`
or broad Docker prune exists.

This tests live canonical observations over an operator-controlled local network.
It does **not** verify commit signatures as a light client, prove factual truth,
produce a portable signed public bundle, or enable a gateway/offline cache. PUBLIC
classification does not itself confer universal domain read access. Hash
embeddings exercise persistence/consensus, not semantic retrieval quality.

## Offline validation

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  scripts/tests/test_public_acceptance.py
```

Tests simulate Docker/RPC commands explicitly, verify exact API/body/upgrade
ordering, bounded stalls, unknown submission, generated-path isolation, and
cleanup failures. They do not start containers or count as a BFT pass. The first
reviewed actual run is still needed to validate pinned image/UID/library
compatibility, production timings and the whole native ceremony.
