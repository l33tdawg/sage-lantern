# SAGE Commons canonical product terminology

**Status:** CANDIDATE normative  
**Last updated:** 2026-09-21  
**Licence:** Apache-2.0

## Product and infrastructure names

| Name | Canonical meaning | Authority boundary |
|---|---|---|
| **SAGE Commons** | The commons fabric: the core mesh technology and governed public network — MeshCore for transport plus SAGE running the public chain, with the contracts that make them one interoperable, community-owned commons — that community networks, services and devices join | Not the mission itself; not one device, operator or company |
| **SAGE Lantern** | Portable local-first personal AI device with sovereign personal SAGE plus bounded public proposer/query/cache functions | Proposer/query client only; never a public validator |
| **Knowledge Point** | A physical or institutionally operated station/facility for research, publishing, caching, synchronization, local services and support | Facility ownership does not grant public consensus power |
| **SAGE Lamplighter** | Untrusted offline super-seed and field-service system that carries verified knowledge/model/runtime packs between connected and disconnected locations | Transport/custody only; never release signer, knowledge authority, Root or validator |
| **SAGE Lightbearer** | Stable public-service node/profile serving the Commons from cloud, datacenter or suitable Knowledge Point infrastructure | Service responsibility only; no automatic validator power or Foundation ownership |

## The Commons fabric and the Lantern's place in it

**Owner direction, 2026-09-21.** This corrects the earlier wording of this table and of decision D-009 ("the overall mission, governed public knowledge network, ecosystem and community programme"), which described the Commons as the programme surrounding the device rather than as the fabric the device joins.

- **The Commons is the fabric.** Core mesh technology — MeshCore for transport, SAGE with the public chain for governed knowledge — plus the protocols and contracts that let independent community networks, services and devices interoperate as one commons. It is a network layer that communities and their institutions hold, not a platform one operator runs.
- **The Lantern is the first deployment on the fabric.** Each Lantern carries its own sovereign private SAGE (its own private chain) and joins the Commons as a proposer/query client. It is never a public validator (D-013).
- **The Foundation is the steward, not the owner of the fabric** — trademarks, releases, the public specification process and shared infrastructure, once constituted.
- **Knowledge travels over the fabric; it is not the fabric.** "Shared knowledge network" describes what the Commons carries, not what it is.

## Lightbearer profiles

- **Lightbearer Validator** — stable independently operated Lightbearer admitted to the public validator set through network governance. Every Commons validator is a Lightbearer Validator, but most Lightbearers need not validate.
- **Foundation Lightbearer** — public query, export, catalogue, intake, archive or other infrastructure operated by the SAGE Commons Foundation under its exact authority plane. Foundation operation does not confer another validator's vote.
- **Community Lightbearer** — stable service operated by a community, institution or partner under disclosed policy and credentials.
- **Lightbearer Query/Export Service** — authenticated canonical-query or derived-export service connected to an authorized public full node, without validator private-key custody.
- **Lightbearer Cache/Seed** — stable regional pack/cache service. Pack custody does not grant publishing, governance or validation authority.

One deployment may implement multiple Lightbearer profiles only with separate identities, keys, authorization checks, storage boundaries and receipts where the trust planes differ.

## Knowledge Point versus Lightbearer

Knowledge Point describes the station or facility. Lightbearer describes a stable public-service responsibility running on suitable infrastructure. A Knowledge Point may host no Lightbearer service, one service, or several isolated profiles. A cloud server can be a Lightbearer without being a community Knowledge Point. A Knowledge Point becomes a Lightbearer Validator only after separate validator governance and operational qualification.

## Lamplighter role and equipment

The complete field system is a **SAGE Lamplighter**. Its portable storage, networking, power and operator laptop may be called the **Lamplighter Kit**. The human carrying or operating it is a **Lamplighter Operator** or plane-qualified **Village Field Operator**. The operator does not inherit authority from the equipment.

“Data Donkey” is a retired internal codename. Existing `DATA_DONKEY_*` filenames and stable `DD-*`/`DDO-*` decision identifiers remain temporarily for link and history stability; their contents use the canonical Lamplighter name. New product copy, requirements, schemas and interfaces MUST use `Lamplighter`.

## Naming rules

1. Use `SAGE Lamplighter` and `SAGE Lightbearer` on first mention; the short form may follow within one document.
2. Never use `Lightbearer` as an alias for Foundation control, SAGE Admin/Root, validator power or truth.
3. Never call every Knowledge Point a validator.
4. UI labels qualify the plane/profile: `Foundation Lightbearer`, `Community Lightbearer`, `Lightbearer Validator`, `Lamplighter Operator`.
5. Existing authority, privacy, classification, consensus and evidence terms retain their exact meanings.
