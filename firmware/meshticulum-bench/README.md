<!-- SPDX-License-Identifier: Apache-2.0 -->
# Lantern Meshticulum device tooling

This directory owns Lantern's Meshticulum build, host-runtime, provisioning, deployment and finite pilot tooling. [Meshticulum](https://github.com/l33tdawg/meshticulum/tree/a89c1ca6fdf1865d61367cf3a8d59ac1ab50cb39) owns the raw radio protocol sources, including `main.cpp` and `protocol.hpp`, and the reusable drivers and flow. Lantern consumes them through an explicit, reviewed dependency; it does not maintain a second core implementation here.

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

The [native mixed-path record](../../docs/lantern/evidence/meshticulum-native-mixed-20261010.json) now proves one finite C→A LoRa→B IP public request and B→A IP→C LoRa reply at its exact core/Lantern/runtime pins. C independently verified the publisher and 977-byte piece; all eight native children across four sessions were terminal and retired. It is a public-piece milestone, not a capacity or matched-performance result.

The mixed transaction's 89.067811 seconds includes management and final-receipt intervals. Its 21 charged radio attempts reserved 1,174,235 microseconds of calculated airtime; actual RF airtime remains unmeasured. Do not present either figure as sustained link capacity.

A separate fresh resume campaign verified an initial TCP piece and preserved it through a 512-stream-byte interrupted reply. B then refused the new LoRa receiver before ARM/TX because the fixed 90-second lease did not fit about 85 seconds left in the original 160-second resource window. The paired C child never started. Full radio resume and faster-IP return remain unqualified. The Lantern source correction reserves 15 seconds for setup, preserves the original expiry and waveform/power/attempt limits, and refuses insufficient windows before preparation. Focused and full integration source controls passed; no new runtime is deployed or RF-qualified.

Final native audits checked 18 retired children, 17 started terminal processes and the unused C child across those campaigns, with no visible radio holders, unchanged persistent debt and stable production lifetimes. C's handback was sent before 10:24 UTC on 2026-10-10. Future operation still requires fresh coordinated ownership and finite authority; these historical receipts cannot authorize a rerun.

The earlier [native public TCP record](../../docs/lantern/evidence/meshticulum-native-tcp-20261010.json) qualifies a finite C/B prerequisite at its exact core, Lantern and runtime pins: one independently publisher-verified 977-byte piece, then deliberate loss after 512 received stream bytes with the partial piece rejected and the first piece retained. All eight children across four TCP sessions were retired without debt refunds; native readiness and service lifetimes were checked. C SPI/RF was never opened or armed in that TCP-only campaign. It is separate from the later mixed pilot and is not a capacity benchmark.

Before any new preparation, apply the [private-state and staging checks](../../docs/lantern/MESHTICULUM_PUBLIC_PILOT.md#private-state-precheck-and-immutable-staging). Existing native state must be protected; digest-named staged inputs can be reused only as identical protected bytes. Native grants remain create-only. The earlier directory-permission refusal and sender-prompt timeout are retained in the record, with the all-handle polling correction bound to the qualified runtime.

Host source controls live in [`software/meshticulum-integration/tests`](../../software/meshticulum-integration/tests) and consume synthetic fixtures from the explicit core checkout. Native guards are modeled in those controls. The recorded public mixed piece advances actual C→A LoRa→B IP evidence; full failover with fresh admission and verified-piece resume, measured route return and private native owner/consent qualification remain open.

Retained radio results belong to their historical runtime; a new bundle cannot inherit that qualification. Matched MeshCore/Reticulum comparisons, actual airtime/energy/EIRP measurements, license/source/relinking closure, clean public history and release gates remain open. Read the [boundary RFC](../../docs/lantern/MESHTICULUM_REPOSITORY_BOUNDARY.md), [Open Source Policy](../../docs/OPEN_SOURCE_POLICY.md) and [CONTRIBUTING](../../CONTRIBUTING.md) before distributing an artifact or changing its trust boundary.
