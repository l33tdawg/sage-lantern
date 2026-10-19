# SAGE Lantern

**Intelligence. For all of us.**

A [SAGE Foundation](https://sage-foundation.net/) project, created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg). Part of the broader [SAGE project](https://github.com/l33tdawg/sage/).

**Development preview, public since 19 October 2026.** This is the product repository for the Lantern device: runtime, touchscreen apps, provisioning tools, firmware glue and the offline map packs. The [Foundation site](https://sage-foundation.net/) carries the programme and contact details. [Offline maps](maps/README.md) are published as release assets. [Device status](docs/lantern/DELIVERY_BACKLOG.md) records what is proven and what is not.

[Documentation](docs/index.md) · [Project family](#the-sage-project-family) · [Offline maps](maps/README.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

A curious mind shouldn't need a better postcode. SAGE is an initiative to
democratise intelligence: local AI that works without reliable internet, and
community-owned networks that keep people connected when the usual connections
don't. SAGE Commons is the fabric underneath that: core mesh technology and a
governed public network that community networks, services and devices join.
SAGE Lantern is the first deployment on it.

This repository is the development home of that work: device runtime,
touchscreen interface, firmware, provisioning tools, public website and the
engineering documentation behind them. It opened publicly on 19 October 2026,
ahead of the January 2027 launch plan, so the device work and map packs can be
reviewed and used now. Opening the repository does not change the device's
acceptance status.

Current device allocation: **A/B are single-LoRa Lamplighter, admin, knowledge
delivery and repeater support units with dashboard screens. C/D/E/F are the real user Lanterns: Orange
Pi 5 Plus, dual LoRa, GPS and WiFi via the Pi.** See the [device-role map](docs/lantern/DEVICE_ROLES.md)
for verified readiness and remaining integration work. Unit C is the first unit
running the 3B chat model in production; the 2B remains its rollback.

## What we're building

### The SAGE project family

| Project | Repository and scope |
| --- | --- |
| **SAGE** | [Main SAGE repository](https://github.com/l33tdawg/sage/) — agent memory, governance and core infrastructure. |
| **Meshticulum** | [Meshticulum](https://github.com/l33tdawg/meshticulum) — reusable transport, reliable flow, radio and IP carrier work, and verified resource protocols. |
| **Lantern** | This repository — product apps and UI, device provisioning, runtime orchestration and private SAGE integrations. |

Lantern consumes the SAGE SDK and Meshticulum. Neither depends on Lantern. The
model weights live in [lantern-3b](https://github.com/l33tdawg/lantern-3b).
See [project family and repository boundaries](docs/PROJECT_FAMILY.md) for the
ownership detail.

**Meshticulum** is the reusable transport underneath Lantern: authenticated
messages, reliable flow, and verified resources across LoRa and IP. It is
public as `1.0.0-alpha`, with the tested SDK wheel, source archive, firmware
build path, notices and proof records. Start with the
[repository](https://github.com/l33tdawg/meshticulum),
[project site](https://l33tdawg.github.io/meshticulum/),
[alpha release](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha),
[documentation](https://github.com/l33tdawg/meshticulum/blob/main/docs/index.md)
or the [HTTP gateway notes](https://github.com/l33tdawg/meshticulum/blob/main/docs/HTTP_GATEWAY.md).
Its published figures are near-bench application goodput, not field-range or
carrier-capacity claims.

### The Foundation and the mission

SAGE Foundation is an initiative in formation. It exists to steward the
mission, not to own the commons: it holds releases, the signing trust root and
public reporting, and it is designed to hand them on. It is not a registered
entity, holds no funds on anyone's behalf, and this site does not take payments
or device orders. It does not hold private knowledge, the commons itself or the
MeshCore standard; each shared item records its own licence.

The mission is to democratise intelligence: help people learn, create and solve
problems regardless of income or connectivity. That means local AI that works
without a reliable network, community-owned radio networks that keep working
during outages, and a public knowledge commons built from deliberate, reviewed
contributions. Private knowledge stays on the owner's device unless they choose
to share it.

Read more at [sage-foundation.net](https://sage-foundation.net/). The
[Foundation page](https://sage-foundation.net/foundation/#limits) sets out what
it holds and what it does not, with the
[ownership table](https://sage-foundation.net/foundation/#ownership) beside it.
[SAGE Commons](https://sage-foundation.net/commons/) explains how the parts fit,
and [Lantern hardware](https://sage-foundation.net/hardware/) covers the device
programme. To talk about the mission, a build or a community pilot, write to
[dhillon@sage.ngo](mailto:dhillon@sage.ngo).

**SAGE Lantern — a handheld AI companion.** A 6.25-inch portrait touchscreen, a
physical hold-to-talk control, an outward camera for visual questions and a
10,000 mAh USB-C pack in a serviceable tray, running local intelligence on an
Orange Pi 5 Plus (RK3588) platform. Ask a question, explore an idea, or work
through a problem with the model running on the device itself.

**Community connections.** Every Lantern is intended to join a local MeshCore
radio network for short messages and shared knowledge, with community gateways
planned to carry authorised requests beyond the local mesh when backhaul is
available. Local AI and short radio messages are the fallback that keeps
working during outages.

**The commons fabric.** SAGE Commons is the fabric the devices join: MeshCore
transport plus the SAGE public chain, with the contracts that make them one
interoperable, community-owned commons. One person's discovery can become
everyone's possibility, deliberately and only that far: personal knowledge
stays personal, and anything shared is reviewed before it travels.

## How it works

### The parts, and how they relate

```mermaid
flowchart TB
  F["SAGE Foundation<br/>steward: signs releases and packs, publishes specifications"]
  C["SAGE Commons — the fabric<br/>MeshCore transport plus the SAGE public chain,<br/>and the contracts that make them one commons"]
  N["Community networks<br/>the local MeshCore networks that run on the fabric"]
  L["SAGE Lantern — the first deployment<br/>its own private SAGE and local AI, on the device"]
  F -->|"stewards and publishes into"| C
  N -->|"operate and extend"| C
  C -->|"first deployment on it"| L
  L <-->|"short messages and compact knowledge"| C
```

The Foundation does not own the fabric and does not hold the networks. It signs
what it publishes, stewards the specifications and holds the trust root; the
networks belong to whoever runs the nodes, and each Lantern's private knowledge
belongs to its owner.

### How knowledge moves: private, then public, then offline again

```mermaid
flowchart LR
  A["Private knowledge on a Lantern<br/>the owner's own SAGE"] -->|"deliberate share:<br/>sanitised derivative"| B["Signed proposal envelope<br/>bound to a nonce and timestamp"]
  B --> C["Commons intake and quarantine<br/>schema, licence, policy and abuse checks"]
  C -->|"approved"| R["The Lantern submits again<br/>under its ordinary proposer identity"]
  R --> D["Independent validators<br/>public-chain consensus"]
  D --> E["Committed public knowledge<br/>with checkable provenance"]
  E --> F["Signed knowledge packs<br/>content-addressed, licence per item"]
  F -->|"radio, peer swap or removable media"| G["Another Lantern<br/>verified offline, no account, no connection"]
```

Nothing leaves a device by default. A share is a bounded, signed derivative;
public knowledge is carried as signed packs that any copy can verify without an
account or a connection. The public chain, intake and the pack channel are
specified in [`docs/MASTER_TECHNICAL_PRD.md`](docs/MASTER_TECHNICAL_PRD.md) and
[`docs/lantern/KNOWLEDGE_PACK_V2.md`](docs/lantern/KNOWLEDGE_PACK_V2.md); how
much of it runs today is in the next section, not in these diagrams.

## Where the project actually is

Honesty is a design requirement here. Implemented, staged, deployed and
physically accepted are separate milestones, and this repository treats them
that way.

- **Two test units (A and B) are built and running.** Local voice, on-device
  answers, camera capture, the touchscreen interface and the radio foundation
  are under active integration and validation. The fleet build in flight is
  C–F, and no part has been printed yet: first-article prints and a measured
  tolerance report come before locked hardware choices or any fleet date.
- **Unit C runs the 3B chat model in production.** V79.6 is live on Unit C's
  loopback chat port through the RKLLM token engine and response guards. It
  measures 625 ms median TTFT and 7.82 tok/s decode against 670 ms and 8.03
  tok/s for stock, with 28/28 wrapped action prompts and the measured English
  failures fixed. The device export is 16k context (RKLLM toolkit cap); the
  host model remains 32k. The live service currently serves 16k context /
  8192 output; long-form requests are buffered and paragraph counts are
  normalized, while exact bullet and retired-value handling remain open. The
  2B service remains the rollback. See
  [`docs/lantern/3B_MODEL_PRODUCTION.md`](docs/lantern/3B_MODEL_PRODUCTION.md).
- **Meshticulum 1.0.0-alpha is public; Lantern messaging is not yet a verified
  end-to-end service.** The protocol/transport project is released at
  [`l33tdawg/meshticulum` v1.0.0-alpha](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha)
  from source `673eb60…`, package `1.0.0a1`. Its published results are
  near-bench application goodput (GFSK250 about 2.0 kB/s; one LoRa SF7/BW500
  run at 1.07 kB/s), not field-range, modulation-equivalence or
  upstream-superiority claims. Lantern's transport, storage, contacts and
  discovery exist; real message exchange, delivery and read receipts still
  have open acceptance gates.
- **Hardware imagery is an AI-generated concept**, not a photograph of a
  product. Final dimensions, screen resolution, battery runtime and pricing
  will be published after validation.
- **The campaign figure is an ambition, not funds raised.** Programme delivery
  plans live in [`docs/roadmap/`](docs/roadmap/); campaign, funding and
  federation research is kept privately outside this tree.

The device acceptance gate is
[`docs/lantern/DELIVERY_BACKLOG.md`](docs/lantern/DELIVERY_BACKLOG.md). Treat it
as the current source of truth for what is proven and what is still open.

## Repository layout

| Path | What lives there |
| --- | --- |
| [`software/runtime/`](software/runtime/) | Device runtime: local voice loop, mesh/LoRa broker and transport, SAGE vault integration, storage-supervisor lifecycle, audio readiness, device lock and community services |
| [`software/npu/`](software/npu/) | RKLLM text and vision engines, HTTP servers, response guards and the Unit C 3B deployment notes |
| [`apps/lantern-fleet-admin/`](apps/lantern-fleet-admin/) | Device touchscreen UI and local fleet-admin console (Next.js/React): ask-and-answer flow, history, messages, camera, sharing, settings and read-only operator views |
| [`apps/sage-website/`](apps/sage-website/) | Public [sage.ngo](https://sage.ngo/) site — mission, hardware and programme copy, exported as static files |
| [`firmware/`](firmware/) | Espressif audio-bridge project and the MeshCore companion-radio patch series used by the units |
| [`scripts/lantern/`](scripts/lantern/) | Provisioning, installers, update and recovery tooling, evidence capture and acceptance runners |
| [`docs/lantern/`](docs/lantern/) | Device PRD, hardware BOM and contract, bring-up and validation, protocol notes, acceptance evidence, delivery backlog |
| [`docs/`](docs/) | Programme, architecture, distribution, roadmap, governance and open-source policy documents |
| [`deploy/`](deploy/) | Server-side configuration used by the hosted services |
| [`maps/`](maps/) | Offline map pack catalog, licence notes and install commands |

`docs/` is the published documentation tree: device, hardware and engineering
material only. Business material — pitch decks, exported programme documents,
concept imagery, and funding, campaign or federation research — lives locally in
`internal-docs/` (`pitch/`, `imagery/`, `funding/`), which git ignores. Local
development evidence in `work/` and `evidence/` is likewise not published.

## Building and testing

Prerequisites: Node 22+ for the web apps, Python 3.11+ for the runtime, and the
respective vendor toolchains for firmware.

```bash
# Device UI and fleet console
cd apps/lantern-fleet-admin
npm ci
npm test          # unit tests
npx tsc --noEmit  # typecheck
npm run build     # production build

# Public website
cd apps/sage-website
npm ci
npm run build     # static export into dist/client

# Device runtime
cd software/runtime
PYTHONPATH=src python3 -m pytest tests

# Read-only platform snapshot on a Lantern unit
./scripts/capture-lantern-evidence.sh evidence/<test-id>
```

Firmware builds and device provisioning are documented in
[`docs/lantern/`](docs/lantern/) and [`scripts/lantern/`](scripts/lantern/);
they expect the pinned toolchains and the unit's own identity material.

## Design principles

1. **Offline first.** Local questions are answered on the device. Connectivity
   adds capability; it is never a dependency for the core experience.
2. **Private by default, shared on purpose.** Personal knowledge stays in the
   owner's vault. Anything that leaves it — a message, a public share, a photo —
   requires explicit consent and a bounded, reviewable path.
3. **No fabricated success.** Measurements, receipts and acceptance evidence are
   recorded; a component is not "done" because it deployed or because an order
   confirmation arrived.
4. **Serviceable and understandable.** Replaceable packs, ventilated
   enclosures, documented pinouts and repair paths are part of the design, not
   an afterthought.
5. **Honest limits.** Radio bandwidth is bounded, coverage needs repeaters and
   power, and satellite service can be unavailable. The documentation says so.

## Documentation map

Start with these, then follow the links inside them:

- [`docs/lantern/PRD.md`](docs/lantern/PRD.md) — product intent, scope and acceptance criteria.
- [`docs/lantern/DELIVERY_BACKLOG.md`](docs/lantern/DELIVERY_BACKLOG.md) — current device gate, verified state and open work.
- [`docs/lantern/3B_MODEL_PRODUCTION.md`](docs/lantern/3B_MODEL_PRODUCTION.md) — V79.6 3B production deployment, device timings, guards, rollback and known limits.
- [`docs/lantern/HARDWARE_SOFTWARE_CONTRACT.md`](docs/lantern/HARDWARE_SOFTWARE_CONTRACT.md) — the hardware/software ownership boundary.
- [`docs/lantern/HARDWARE_BOM.md`](docs/lantern/HARDWARE_BOM.md) — the A/B test-unit parts list and order history as of 11 September 2026. Superseded for the C–F fleet build, which the hardware workstream records in its own bill of materials and build status; kept here as the bring-up record.
- [`docs/lantern/BRINGUP_AND_VALIDATION.md`](docs/lantern/BRINGUP_AND_VALIDATION.md) — staged assembly, test procedure and evidence checklist.
- [`docs/lantern/UNIT_A_ACCEPTANCE_EVIDENCE.md`](docs/lantern/UNIT_A_ACCEPTANCE_EVIDENCE.md) — Unit A build, offline voice, timing and recovery evidence.
- [`docs/lantern/SSH_MANAGEMENT.md`](docs/lantern/SSH_MANAGEMENT.md) — operator access, the `sage` maintenance account and key rotation.
- [`docs/lantern/MESHCORE_TRANSPORT_PROTOCOL.md`](docs/lantern/MESHCORE_TRANSPORT_PROTOCOL.md) and [`MESHCORE_HOST_PROTOCOL_TEST_PLAN.md`](docs/lantern/MESHCORE_HOST_PROTOCOL_TEST_PLAN.md) — the radio protocol as implemented and its test matrix.
- [`docs/MASTER_TECHNICAL_PRD.md`](docs/MASTER_TECHNICAL_PRD.md), [`docs/TERMINOLOGY.md`](docs/TERMINOLOGY.md) and [`docs/DOCUMENTATION_GOVERNANCE.md`](docs/DOCUMENTATION_GOVERNANCE.md) — programme contract, canonical vocabulary and document hierarchy.
- [`docs/OPEN_SOURCE_POLICY.md`](docs/OPEN_SOURCE_POLICY.md) and [`GOVERNANCE.md`](GOVERNANCE.md) — licensing scope and how decisions are made.

## Direction and roadmap

The project's public plan, as published on the website:

| Stage | Target | Status |
| --- | --- | --- |
| Now | Build and prove the prototypes end to end | In development |
| Next | First-article prints and a measured tolerance report for the C–F fleet | Gates every hardware decision and date |
| 1 January 2027 | Public launch announcement and campaign | Repositories opened 19 October 2026; announcement target 1 January 2027 |
| 1 February 2027 | Campaign live, and the first backers can place orders | Target; receiving entity and checkout still to settle |
| Next, with funding | A supported 20–30 unit community pilot | Proposed first stage |

These are targets and proposals, not completed milestones. Progress is reported
as evidence rather than announcements. An earlier "six complete demonstration
Lanterns in December 2026" target has been withdrawn: the fleet is C–F, nothing
has been printed yet, and no fleet date is being worked to until the first
article is measured.

## Contact

SAGE Foundation is an initiative in formation, and Lantern is in development.
To talk about the mission, the hardware or a community pilot, write to
<dhillon@sage.ngo>.

## Contributing, security and licence

- Contributions: read [`CONTRIBUTING.md`](CONTRIBUTING.md) and the documentation
  governance rules before opening a change. Cross-subsystem, security, privacy,
  protocol or governance changes need an RFC; durable local choices need an ADR.
- Security: report vulnerabilities through [`SECURITY.md`](SECURITY.md) rather
  than a public issue.
- Licence: project-authored code, documentation, schemas and tests are
  [Apache-2.0](LICENSE). Third-party models, data, voices, firmware and other
  artifacts keep their upstream licences — see
  [`docs/OPEN_SOURCE_POLICY.md`](docs/OPEN_SOURCE_POLICY.md) and [`NOTICE`](NOTICE).
