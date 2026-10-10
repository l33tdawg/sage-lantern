<!-- SPDX-License-Identifier: Apache-2.0 -->
# Lantern Meshticulum device tooling

This directory owns Lantern's Meshticulum build, host-runtime, provisioning, deployment and finite pilot tooling. [Meshticulum](https://github.com/l33tdawg/meshticulum/tree/0e240641d04b373402267c68cdf261a516a5156e) owns the raw radio protocol sources, including `main.cpp` and `protocol.hpp`, and the reusable drivers and flow. Lantern consumes them through an explicit, reviewed dependency; it does not maintain a second core implementation here.

These are experimental tools with inherited lab assumptions. A fresh clone does not supply board-support trees, vendor toolchains, rollback firmware, device identities, management credentials or live grants. No automatic deployment, flashing or production qualification is implied.

## Source and artifact pins

[`source_core.py`](source_core.py) requires `MESHTICULUM_SOURCE`, or an explicit core path where a command offers that argument. It compares the inventoried core module and raw firmware files plus LICENSE/NOTICE with [`meshticulum-source-lock.json`](../../software/meshticulum-integration/meshticulum-source-lock.json). A package version or nearby directory is insufficient. Review the exact recorded revision as well as its content hashes.

Lantern source, selected Meshticulum source, external BSP/toolchains, resulting firmware and host bundle are distinct inputs or artifacts. Record their hashes and provenance separately. Hash verification establishes artifact identity; it does not establish current host ownership, physical state or dispatch permission.

## Build a public host runtime

From the Lantern repository root, after preparing the explicit core dependency:

```sh
export MESHTICULUM_SOURCE=/absolute/path/to/meshticulum
python firmware/meshticulum-bench/build_runtime.py \
  --meshticulum-root "$MESHTICULUM_SOURCE" \
  --output-dir build/meshticulum-runtime
```

The builder freezes the selected core and Lantern's `lantern_meshticulum.bench`/`ip` integration, source lock, notices and supporting provenance into a content-addressed archive and manifest. It preserves the accepted flow pins and compiles included Python sources as a source check. It includes neither repository's tests nor the private `lantern_meshticulum.sage` provider. The manifest keeps private services disabled.

An optional `--source-test-report` must name a nonempty JUnit report with no failures or errors within the selected output tree. The builder records its hash; reviewers must still check the candidate, coverage and skips. A compiled import or a report hash is not RF evidence.

Building creates local artifacts only. It does not install them, change services, prepare grants, open a socket or arm a radio. Root/native bootstrap and launcher checks remain separate from current device and dispatch authority.

## Raw radio firmware and inherited BSP

[`prepare_build.py`](prepare_build.py) obtains the raw radio source from Meshticulum, then prepares an isolated build using the retained, locally patched MeshCore-derived board-support tree and pinned PlatformIO tools. The donor is reconstructed and is not asserted byte-identical to pristine upstream. [`run_build.py`](run_build.py) owns the subsequent build step. Neither source preparation nor compilation proves board timing, antenna behavior, RF output or application delivery.

The retained BSP, RadioLib and platform components keep their own licenses. Preserve their original notices and source/build manifests; project Apache-2.0 attribution does not relicense a donor. A portable firmware/BSP recipe and distributed-artifact license inventory still require review.

Historical helper defaults refer to retained evidence directories and A/B/C lab roles. They are not a portable provisioning installer. Inspect their input and output paths and current device assumptions before any use. Keep builds isolated from retained rollback and identity material.

## Native public pilot boundary

The public pipeline separates installation, synthetic public seed preparation, child provisioning, actual radio/TCP runs, quarantine forwarding and recipient verification. A native provisioner obtains current host facts and original clocks locally. Every child binds its peers, object, service, boot, original expiry, byte/frame limits and current leases.

Receivers have no management-supplied body. Bridge senders use the bytes actually received into local authenticated quarantine. The resource server uses the received MR1 request and configured verified public store; the recipient independently verifies its original request, manifest/publisher and whole chunk. See [the pilot guide](../../docs/lantern/MESHTICULUM_PUBLIC_PILOT.md) for the bounded sequence and evidence needed.

Root guards, source pins and controller stop bounds each address one boundary. They cannot replace a fresh finite device handoff, actual exclusive ownership, valid radio/antenna records, native permissions or production-service preservation. A helper that requests a flash or remote action must be reviewed in its own operational context; merely appearing in this directory grants no authority to execute it.

Keep DATA, request, response, ACK and retry costs charged. TCP stream-byte evidence cannot settle RF debt. After failure or cancellation, preserve raw receipts and journals, stop only owned jobs, disarm and join actual workers, and verify terminal cleanup before returning a device. Expiry or a state file alone cannot prove quiescence.

## Validation and release scope

Host source controls live in [`software/meshticulum-integration/tests`](../../software/meshticulum-integration/tests) and consume synthetic fixtures from the explicit core checkout. Native guards are modeled in those controls. Real C→A LoRa→B IP, failover with fresh admission and verified-piece resume, measured route return and private-backend qualification remain separate physical/application gates.

Retained radio results belong to their historical runtime; a new bundle cannot inherit that qualification. Matched MeshCore/Reticulum comparisons and public release gates remain open. Read the [boundary RFC](../../docs/lantern/MESHTICULUM_REPOSITORY_BOUNDARY.md), [Open Source Policy](../../docs/OPEN_SOURCE_POLICY.md) and [CONTRIBUTING](../../CONTRIBUTING.md) before distributing an artifact or changing its trust boundary.
