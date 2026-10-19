# SAGE project family

The work is split across public repositories so each part can be used on its own.

| Project | Scope | Depends on |
| --- | --- | --- |
| **SAGE core** | Agent memory, governance, consensus and the SDK | Nothing in this family |
| **Meshticulum** | Transport, reliable flow, radio and IP carriers, verified resources | No Lantern dependency |
| **Lantern** | Device apps, provisioning, runtime orchestration and private SAGE integration | SAGE SDK, Meshticulum |
| **Lantern 3B** | Model weights and model card | Qwen base model, research licence |

The dependency runs one way. Lantern consumes the SAGE SDK and Meshticulum.
Meshticulum began inside Lantern and was extracted as a standalone project. It
does not depend on Lantern, the protected device store or the fleet services.
The SAGE core depends on neither.

## Where things live

| Path | Owner |
| --- | --- |
| `software/runtime/` | Lantern runtime, voice loop, radio broker and vault client |
| `software/npu/` | RKLLM text and vision engines, response guards |
| `apps/` | Touchscreen UI, fleet console and public website |
| `firmware/` | Audio bridge and radio companion patch series |
| `scripts/lantern/` | Provisioning, installers, evidence capture |
| `maps/` | Offline map pack catalog and licence notes |

Device identity material, owner credentials and local evidence stay outside the
public tree. Documentation here describes how to provision a unit; it does not
contain the secrets used to do it.

## Repositories

- [SAGE core](https://github.com/l33tdawg/sage/): agent memory, governance and core infrastructure.
- [Meshticulum](https://github.com/l33tdawg/meshticulum): transport, reliable flow and verified resources.
- [Lantern](https://github.com/l33tdawg/sage-lantern): this repository.
- [Lantern 3B](https://github.com/l33tdawg/lantern-3b): model weights and model card.

The [Foundation site](https://sage-foundation.net/) carries the programme,
governance and contact details.
