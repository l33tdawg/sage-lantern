<!-- SPDX-License-Identifier: Apache-2.0 -->
# Lantern integration for Meshticulum

This package owns Lantern's application and device policy around [Meshticulum](https://github.com/l33tdawg/meshticulum/tree/996edadfd5a88788a6480c6dfc5a9c48405f311c). The dependency goes one way: **Lantern → Meshticulum**. The reusable crypto, wire protocols, frozen flow, scheduler, generic drivers and verified-resource code remain in Meshticulum. They are not forked into this source tree.

The package name is `sage-lantern-meshticulum`; its Python namespace is `lantern_meshticulum`. It is candidate integration code, not an installed or qualified device service. See the [candidate boundary RFC](../../docs/lantern/MESHTICULUM_REPOSITORY_BOUNDARY.md).

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

## Native execution and application boundaries

The finite public radio entry point is `sage-lantern-meshticulum-service`. Its `--help` opens no device. Actual execution belongs to a fresh coordinated device window, current native admission and the trusted exclusive worker. The public radio/TCP deployment bundle disables private services; installation and inspection do not arm a radio or authorize a transfer.

Private receive integration persists the original intention before DATA/ACK dispatch, retains sealed bytes and original boot/epoch/monotonic deadlines, and reconciles uncertain writes explicitly. The trusted native owner/consent/revocation lifecycle must be held for the exact grant. A Python policy object or a callback returning `True` is not evidence that this lifecycle exists. Real encrypted-backend and owner qualification remain open.

Public receive bytes enter durable quarantine, not a trusted cache. Bridge forwarding uses the actual local authenticated capture. A response comes from the received MR1 request and a configured verified public store; the recipient still checks its original request binding, pinned publisher/manifest and complete chunk before caching. Kernel acceptance, local RF completion, peer receipt, durable capture, publisher verification and SAGE admission are separate stages.

New children retain original resource deadlines and prior attempt/stream-byte/airtime debt. A new grant, cancellation, restart or source migration cannot renew expired work or infer settlement. Keep unknown outcomes and raw failures, and let the native owner establish bounded disarm and cleanup.

## Build and review

[Firmware and runtime tooling](../../firmware/meshticulum-bench/README.md) explains public-only host bundles and the separately pinned raw radio build inputs. The [public-pilot guide](../../docs/lantern/MESHTICULUM_PUBLIC_PILOT.md) describes the coordinated qualification sequence. Neither guide authorizes automatic installation or flashing.

Read [CONTRIBUTING](../../CONTRIBUTING.md), the [requirement register](../../docs/REQUIREMENTS_TRACEABILITY.md) and the [candidate boundary RFC](../../docs/lantern/MESHTICULUM_REPOSITORY_BOUNDARY.md) before changing the integration. Preserve Apache-2.0 attribution and third-party notices. Keep device addresses, keys, private payloads and unpublished operational evidence out of source, fixtures and public documentation.
