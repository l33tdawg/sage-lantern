<!-- SPDX-License-Identifier: Apache-2.0 -->
# Lantern integration for Meshticulum

This package owns Lantern's application and device policy around [Meshticulum](https://github.com/l33tdawg/meshticulum/tree/4fa36741fb9dd1b4d990be8fff81ed20c36baa6c). The dependency goes one way: **Lantern → Meshticulum**. The reusable crypto, wire protocols, frozen flow, scheduler, generic drivers and verified-resource code remain in Meshticulum. They are not forked into this source tree.

The package name is `sage-lantern-meshticulum`; its Python namespace is `lantern_meshticulum`. Its finite public TCP prerequisite, [four-hop public LoRa/IP transfer](../../docs/lantern/evidence/meshticulum-native-mixed-20261010.json) and [fresh LoRa resume with measured IP return](../../docs/lantern/evidence/meshticulum-native-resume-20261010.json) have recorded native evidence at their respective exact pins. These qualify bounded public pilot scopes; our performance qualification, sourced published-comparison review, private production and public-release requirements remain open. See the [candidate boundary RFC](../../docs/lantern/MESHTICULUM_REPOSITORY_BOUNDARY.md).

## What lives here

| Namespace | Lantern responsibility |
| --- | --- |
| `lantern_meshticulum.sage` | Protected inbox, durable sealed capture, WorkflowStore/storage interfaces, private policy and service integration |
| `lantern_meshticulum.bench` | Native radio admission/profile policy, Linux roles and owner checks, finite public nodes, grant/resource preparation, pilot control and device management |
| `lantern_meshticulum.ip` | Native finite public TCP entry point using Meshticulum's authenticated carrier and stream-byte accounting |

These components consume `meshticulum.*` interfaces. Lantern chooses its admitted radio policy and supplies current native facts, authorization and storage; the core constructors do not discover a Lantern, enroll peers or grant access to its protected state.

The existing `sage_lantern.mesh_envelope` runtime import is a module-identity bridge to `meshticulum.privacy.mesh_envelope`. The wire implementation and its classes remain canonical in Meshticulum. This bridge does not change signature domains, sender/recipient pinning or encrypted bytes.

## Explicit core dependency

[`pyproject.toml`](pyproject.toml) pins the exact Meshticulum Git revision used by this candidate. [`meshticulum-source-lock.json`](meshticulum-source-lock.json) additionally records the source repository, reviewed revision and file digests used by Lantern's builders. Keep the dependency URL and source lock on the same reviewed commit.

The lock includes the core's [experimental v2 forward-session primitive](https://github.com/l33tdawg/meshticulum/blob/4fa36741fb9dd1b4d990be8fff81ed20c36baa6c/docs/FORWARD_SECRECY.md). It requires separate Noise static keys and pinned signing identities, rejects known legacy-key reuse and MPF1 framing, and preserves retired v1 ID/role claims. This pin supplies source for review. Lantern has experimental protected receive, parent-budget, Noise-purpose, native-owner and tracked-child adapters; their source and temporary encrypted-backend evidence are described in the [forward-capture contract](../../docs/lantern/MESHTICULUM_FORWARD_CAPTURE.md). The public runtime bundle excludes them. Production enrollment/key-purpose and owner/consent providers, cryptographic review and native private transport qualification remain open.

Set `MESHTICULUM_SOURCE` to an explicit checkout of that reviewed Meshticulum revision. The [builder source guard](../../firmware/meshticulum-bench/source_core.py) compares the inventoried Python/JSON modules, raw firmware inputs and LICENSE/NOTICE against the lock, refusing missing or changed input. It checks content rather than discovering a sibling checkout. Revision review must still establish that the selected checkout and installed dependency correspond to the recorded commit.

Updating the lock is a reviewed dependency change. A pending revision or an uncommitted source snapshot is preparation evidence, not a release candidate pin. Keep the exact Lantern commit, Meshticulum commit and produced artifact hashes together when validating a candidate.

## Host development and source controls

Python 3.11 or later is required. From the Lantern repository root, with the explicit pinned core checkout available:

```sh
export MESHTICULUM_SOURCE=/absolute/path/to/meshticulum
python -m venv .venv-meshticulum
. .venv-meshticulum/bin/activate
python -m pip install -e "$MESHTICULUM_SOURCE"
python -m pip install -e 'software/meshticulum-integration[dev]'
python -m pytest software/meshticulum-integration/tests \
  --ignore=software/meshticulum-integration/tests/integrations
```

The test harness consumes shared synthetic fixtures from that explicit core checkout. The installed core and `MESHTICULUM_SOURCE` must refer to the same reviewed source. Fixture imports are a development boundary: the package and device bundles do not deploy either repository's tests.

Private SAGE controls require the compatible local Lantern SDK build specified by the `sage` extra. Supply that wheel before installing the extra; an unrelated public SDK version is not a qualified substitute:

```sh
python -m pip install /path/to/sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl
python -m pip install -e 'software/meshticulum-integration[dev,sage]'
python -m pytest software/meshticulum-integration/tests/integrations/sage
```

Installing the extra makes dependencies available. It does not enable a private native provider, obtain consent, open a protected store or dispatch traffic. SDK MockTransport, modeled ownership and synthetic host facts remain source controls.

The [sealed-capture fence controls](tests/integrations/sage/test_mesh_binary_capture.py) cover closure from access/storage/peer and clock callbacks, storage snapshots, native lease exit and nested WorkflowStore checks. Closing callbacks cannot publish success; nested get/put checks refuse remote dispatch after closure. Committed original intentions retain their UUID and deadlines for explicit recovery. These use SDK MockTransport and a modeled lease, establishing no native qualification or private MPF2 wiring. The [forward-session capture contract](../../docs/lantern/MESHTICULUM_FORWARD_CAPTURE.md) records the implemented experimental joint receive journal and later capture import, with their separate evidence and remaining native gates.

## Recorded encrypted-backend compatibility

The [2026-10-10 compatibility evidence](../../docs/lantern/evidence/meshticulum-encrypted-backend-20261010.json) records the canonical Lantern adapters and SDK `11.19.10+lantern.20260906` against real temporary SAGE REST/SQLite/vault fixtures, locally on macOS and on actual B Linux/arm64. This used real signed REST requests, not SDK MockTransport. B used an isolated 14-wheel dependency target; its existing environment and native service lifetimes remained unchanged. No production stores, C or RF operation were involved.

Eight fixture checks passed: signed live storage status, atomic stale conversation-guard refusal, actor isolation, capture restart with signed admission, durable replay to the same record, explicit acknowledgement, reconciliation after losing a committed HTTP PUT return, and fixture-policy revocation refusal. The SQLite main/WAL files contained no fixture plaintext canary. The [Python adapter exercise](tests/interop/backend_interop.py) and [Go backend fixture](tests/interop/backend_interop_test.go.txt) define the checks; their hashes and execution pins are in the evidence.

The unknown-write injection loses the client return **after a real HTTP PUT commits**; it is not a network-disconnect experiment. Policy/lease callbacks and the capture boot ID remain modeled fixture inputs, with temporary Go fixture governance. This establishes isolated backend compatibility, not the production native owner/consent provider, private transport or production-store qualification. Private production remains disabled; private lifecycle, our performance qualification, published-comparison review, deployed-image/source correspondence, licensing, clean-public-history and release gates remain open.

With the compatible SDK installed, the [fixture runner](tests/interop/run_backend_fixture.py) uses an explicit SAGE/core checkout and a read-only Go overlay. It creates temporary fixture stores and saves a local log in a new output directory:

```sh
python tests/interop/run_backend_fixture.py \
  --sage-source /path/to/sage \
  --meshticulum-source /path/to/meshticulum \
  --output ./build/backend-fixture-run
```

The runner keeps dependency resolution offline. It opens loopback fixture servers and grants no device, private-production or RF permission.

## Native execution and application boundaries

The earlier [native TCP prerequisite](../../docs/lantern/MESHTICULUM_PUBLIC_PILOT.md#recorded-native-tcp-prerequisite) verified one 977-byte public piece from C's request through B's verified store to C's independent publisher/chunk check. A second response failed at 512 received stream bytes without caching the partial piece; the first verified piece survived. Four sessions/eight retired children retained one original 160-second expiry and their stream-byte debt. Actual receiver readiness and service lifetimes were checked; C SPI/RF was not opened in that TCP-only campaign. The 19.443863-second transaction includes SSH/controller gaps and is not link capacity or a matched benchmark. Claims remain bound to that evidence's exact commits/runtime.

### Recorded native mixed path and resume refusal

The [2026-10-10 mixed-path evidence](../../docs/lantern/evidence/meshticulum-native-mixed-20261010.json) records four actual public hops: C→A LoRa request, A→B IP request, B→A IP response and A→C LoRa response. C independently verified the original request binding, publisher/manifest and complete **977-byte public piece**. All eight native children were terminal and retired. This bounded exchange used Meshticulum `6605ba7`, Lantern code `a41851a` and runtime archive `d3194a9`; the evidence records the full pins.

A separate earlier resume campaign verified an initial TCP piece, then interrupted the next reply after **512 received stream bytes**. The first verified piece remained unchanged. B's fresh LoRa receiver then refused **before ARM/TX**: its fixed 90-second hardware lease exceeded the roughly 85 seconds remaining in the original 160-second resource window. That refusal is retained. The sizing repair was source-only at that checkpoint; the later successful campaign below supplies its own native evidence at new pins, without relabeling the failed campaign.

Final native audits covered 18 retired children: 17 terminal native jobs and one unused C child. Original debt was preserved, no visible radio holders remained, and production service lifetimes were unchanged. These observations apply to those completed campaigns and grant no authority for another run. They used the existing `mtb1-9816f92488` firmware; no firmware flash or full deployed-image hash equivalence is claimed.

This mixed-path result does not establish sustained RF goodput or a matched MeshCore/Reticulum speed advantage. Its original scope and firmware limitations remain unchanged by the later resume evidence.

### Recorded fresh LoRa resume and measured IP return

The [2026-10-10 resume evidence](../../docs/lantern/evidence/meshticulum-native-resume-20261010.json) records one original **160-second public-resource window**. The first TCP piece passed publisher/chunk verification. The next reply was interrupted at **512 received stream bytes**; its journal was fenced, the incomplete object was not durable or cached, and the first verified piece remained available. Fresh C↔B LoRa request/reply admission then verified the missing piece. A fresh IP generation supplied a faster verified timing sample, and a subsequent piece returned over IP and passed independent publisher verification.

All **four pieces / 3,908 useful bytes** were verified before the unchanged original expiry. The campaign used ten sessions and **20 terminal, retired native children**. All three owned management masters were terminal; the read-only audit matched persistent debt to terminal snapshots, found radio journals unfenced and children disarmed, observed no visible radio holders, and confirmed unchanged production service lifetimes. The finite window was handed back at **11:28:51 UTC**, before its **11:35 UTC** end. That completed handoff is not reusable authority.

This execution used Meshticulum **`a89c1ca`**, Lantern runtime code **`379025e`** and runtime archive **`856570c`**, with full commits and artifact hashes in the evidence. Adaptive lease sizing and owned management-connection reuse were exercised natively at these pins. They do not transfer qualification to a different dependency, runtime or production lifecycle. No firmware was flashed and private services remained disabled.

The LoRa piece's **54.349727-second** request-to-verification interval and the fresh IP sample's **9.529958 seconds** include management/preparation and final ACK windows. They support this campaign's measured faster-path decision for tiny 977-byte pieces; they are not carrier capacity, sustained RF goodput or a fair matched MeshCore/Reticulum benchmark. Actual RF airtime, energy and EIRP remain unavailable. Private native owner/consent/provider and private transport, full deployed-image/source equivalence, artifact notice/source/license closure, clean public history and public-release gates remain open.

The finite public radio entry point is `sage-lantern-meshticulum-service`. Its `--help` opens no device. Actual execution belongs to a fresh coordinated device window, current native admission and the trusted exclusive worker. The public radio/TCP deployment bundle disables private services; installation and inspection do not arm a radio or authorize a transfer.

Private receive integration persists the original intention before DATA/ACK dispatch, retains sealed bytes and original boot/epoch/monotonic deadlines, and reconciles uncertain writes explicitly. The trusted native owner/consent/revocation lifecycle must be held for the exact grant. A Python policy object or a callback returning `True` is not evidence that this lifecycle exists. The isolated backend checks above leave the production owner/consent provider, private transport and encrypted production-store qualification open.

Public receive bytes enter durable quarantine, not a trusted cache. Bridge forwarding uses the actual local authenticated capture. A response comes from the received MR1 request and a configured verified public store; the recipient still checks its original request binding, pinned publisher/manifest and complete chunk before caching. Kernel acceptance, local RF completion, peer receipt, durable capture, publisher verification and SAGE admission are separate stages.

New children retain original resource deadlines and prior attempt/stream-byte/airtime debt. A new grant, cancellation, restart or source migration cannot renew expired work or infer settlement. Keep unknown outcomes and raw failures, and let the native owner establish bounded disarm and cleanup.

## Published comparisons and competitive target

The competitive goal is **faster useful encrypted transfers and stronger encryption/security than both MeshCore and Reticulum**. Comparison uses their published figures and primary technical sources, starting from [the core comparison](https://github.com/l33tdawg/meshticulum/blob/4fa36741fb9dd1b4d990be8fff81ed20c36baa6c/docs/COMPARISON.md). We do not require or prepare our own counterpart-stack benchmark campaigns. Meshticulum's own native and sustained-performance qualification remains required.

Record each published figure's source URL, date/version, hardware, modem settings, topology, payload, encryption, completion boundary, units, loss/retry costs and measurement scope. Compare it with our evidence only where those conditions support the claimed advantage. Missing or incompatible reference measurements remain unavailable; they do not establish a speed win.

Security comparisons require explicit properties and threat models, including recipient encryption, sender/peer authentication, key lifecycle and forward secrecy, replay, authorization/consent and metadata exposure. A primitive name or key size alone cannot prove stronger security. State which properties the sources and our tests actually establish; the target is not an achieved superiority claim. Historical evidence and its original comparison fields remain unchanged, without imposing a new self-run comparative campaign.

## Build and review

[Firmware and runtime tooling](../../firmware/meshticulum-bench/README.md) explains public-only host bundles and the separately pinned raw radio build inputs. The [public-pilot guide](../../docs/lantern/MESHTICULUM_PUBLIC_PILOT.md) describes the coordinated qualification sequence. Neither guide authorizes automatic installation or flashing.

Read [CONTRIBUTING](../../CONTRIBUTING.md), the [requirement register](../../docs/REQUIREMENTS_TRACEABILITY.md) and the [candidate boundary RFC](../../docs/lantern/MESHTICULUM_REPOSITORY_BOUNDARY.md) before changing the integration. Preserve Apache-2.0 attribution and third-party notices. Keep device addresses, keys, private payloads and unpublished operational evidence out of source, fixtures and public documentation.
