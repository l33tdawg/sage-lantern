# SAGE Lantern

**Intelligence. For all of us.**

A curious mind shouldn't need a better postcode. SAGE is an initiative to
democratise intelligence: local AI that works without reliable internet, and
community-owned networks that keep people connected when the usual connections
don't. SAGE Lantern is the first device project; SAGE Commons is the shared
knowledge network being built around it.

This repository is the private development home of that work — device runtime,
touchscreen interface, firmware, provisioning tools, public website and the
engineering documentation behind them.

[Meshticulum](https://github.com/l33tdawg/meshticulum/tree/996edadfd5a88788a6480c6dfc5a9c48405f311c)
owns the reusable transport, crypto, flow, drivers and resource protocols.
Lantern consumes that pinned dependency and owns its
[private/native integration](software/meshticulum-integration/README.md) and
[device deployment tooling](firmware/meshticulum-bench/README.md). The dependency
is Lantern → Meshticulum; see the [candidate boundary RFC](docs/lantern/MESHTICULUM_REPOSITORY_BOUNDARY.md)
for ownership and open qualification gates.

## What we're building

**SAGE Lantern — a handheld AI companion.** A 6.5-inch portrait touchscreen, a
physical hold-to-talk control, an outward camera for visual questions and a
replaceable rear battery pack, running local intelligence on a ROCK 5B+ class
platform. Ask a question, explore an idea, or work through a problem with the
model running on the device itself.

**Community connections.** Every Lantern is intended to join a local MeshCore
radio network for short messages and shared knowledge, with community gateways
planned to carry authorised requests beyond the local mesh when backhaul is
available. Local AI and short radio messages are the fallback that keeps
working during outages.

**SAGE Commons.** One person's discovery can become everyone's possibility.
Personal knowledge stays personal, and sharing is deliberate: you choose what
leaves your private space, and shared knowledge can be reviewed and improved by
others.

## Where the project actually is

Honesty is a design requirement here. Implemented, staged, deployed and
physically accepted are separate milestones, and this repository treats them
that way.

- **Two prototype units are built and running.** Local voice, on-device answers,
  camera capture, the touchscreen interface and the radio foundation are under
  active integration and validation.
- **Radio messaging is not yet a verified end-to-end service.** Transport,
  storage, contacts and discovery exist; real message exchange, delivery and
  read receipts still have open acceptance gates.
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
| [`apps/lantern-fleet-admin/`](apps/lantern-fleet-admin/) | Device touchscreen UI and local fleet-admin console (Next.js/React): ask-and-answer flow, history, messages, camera, sharing, settings and read-only operator views |
| [`apps/sage-website/`](apps/sage-website/) | Public [sage.ngo](https://sage.ngo/) site — mission, hardware and programme copy, exported as static files |
| [`firmware/`](firmware/) | Espressif audio-bridge project and the MeshCore companion-radio patch series used by the units |
| [`scripts/lantern/`](scripts/lantern/) | Provisioning, installers, update and recovery tooling, evidence capture and acceptance runners |
| [`docs/lantern/`](docs/lantern/) | Device PRD, hardware BOM and contract, bring-up and validation, protocol notes, acceptance evidence, delivery backlog |
| [`docs/`](docs/) | Programme, architecture, distribution, roadmap, governance and open-source policy documents |
| [`deploy/`](deploy/) | Server-side configuration used by the hosted services |

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
- [`docs/lantern/HARDWARE_BOM.md`](docs/lantern/HARDWARE_BOM.md) and [`HARDWARE_SOFTWARE_CONTRACT.md`](docs/lantern/HARDWARE_SOFTWARE_CONTRACT.md) — authoritative parts list and the hardware/software ownership boundary.
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
| December 2026 | Six complete demonstration Lanterns (eight to ten as a stretch) | Demonstration target |
| January 2027 | Public campaign launch with demonstration evidence | Public launch target |
| Next, with funding | A supported 20–30 unit community pilot | Proposed first stage |

These are targets and proposals, not completed milestones. Progress is reported
as evidence rather than announcements.

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
