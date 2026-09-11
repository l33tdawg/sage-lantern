# Runtime maintenance complete source candidate

Status: source-only, awaiting independent integration review. No A deployment,
capture, inference, or benchmark is claimed. The CLI rejects `--apply` before
loading dependencies. Promotion requires a separately reviewed source hash;
this document does not authorize removing that gate.

## Concrete route

`prepare-runtime-maintenance.py` now calls the same-process
`complete-runtime-maintenance-audio.py` tail after its concrete replacement and
basic-service restoration. It is not a callback-only or preparation-only path.

1. Validate the sealed offline maintenance environment, exact installed/candidate
   wheels, native process/listener/governance, owner access/storage epochs,
   settled signed work, all service definitions and bounded process exclusion.
2. Retain an exclusive attempt, inhibit UI/voice/broker with runtime-directory
   preservation, request the exact voice-instance shutdown, and verify empty
   cgroups. Local history/HTTP uncertainty refuses continuation; historical
   auth outcomes remain unknown and unchanged.
3. Freeze the signed handoff, stop the current auth instance with a fresh
   positive receipt, inhibit/stop all other runtime importers, and replace only
   the pinned runtime from the independent sealed environment. Native and model
   remain running. Retain the old wheel; no automatic rollback.
4. Restore newly locked auth and basic services with process/invocation checks.
   Failed restoration re-inhibits and contains the attempted services.
5. Wait a bounded interval for a new owner unlock. Preserve old commissioning,
   supervisor and runtime records, then perform one explicitly approved local
   two-second capture using the installed candidate validator. Never repin old
   diagnostic evidence or retain/upload audio.
6. Start only through `ColdBrokerBootstrap`, verify the actual storage epoch and
   operational readiness, write a distinct durable lifetime, and publish the
   matched coordinator/policy/latest pointer. Preserve existing lifetime and
   failure journals. Post-start failure invokes bounded containment, not a
   claim that retained start guards stopped already-running processes.

Cleanup uses separate bounded budgets. SIGKILL, power loss, partial replacement,
or a failed checkpoint requires explicit diagnosis; the CLI never retries a
retained attempt. No native restart, vault close, historical drain claim, or
automatic record replay is part of this lane.

## Required input and invocation contract

The exact top-level expected schema is enforced by `validate()`:

```
schema boot_id access_epoch storage_epoch workflow_sha256 actor
helper collector installed_wheel candidate_wheel site files services
receipts receipt_parents maintenance_site runtime_tool native_health
native_listener_inode native_observer extra_services environment_builder
environment_complete voice_instance audio_tail audio_tools capture_snapshot
audio_inventory model_files model_argv startup_before
```

Schema is `sage.lantern.runtime-maintenance.v1`. Pins are absolute `path` plus
full `sha256`. `services` covers UI, voice, broker, auth, native, model, camera,
display and status with exact unit fields and process identities. Extra units
are idle-suspend, device-agent and startup; genuinely absent units stay absent.
Receipt parent identities are separately scoped to the service-owned hierarchy.
`voice_instance` binds instance UUID, PID, start ticks, boot and invocation.
`audio_tools` pins helper/collector/candidate wheel/adapter/coordinator;
`startup_before` pins the existing config/latest/coordinator. Capture inputs must
be the exact reviewed candidate snapshot, not invented readiness values.

Build the independent maintenance venv using
`build-runtime-maintenance-env.py` and explicit offline pinned wheels. Run the
read-only preflight from that venv's Python with `-I -B`, supplying
`--expected`, `--expected-sha256`, `--helper`, `--helper-sha256`.
The future reviewed apply entry additionally requires explicit
`--approve-microphone-capture`; `--wait-owner-seconds` is bounded 1–300.
No credential is an input. Current source still rejects all `--apply` calls.

Live expected pins and the sealed environment have not been collected/built on
A for this candidate. They require a fresh read-only preflight after review;
an old boot/access snapshot is not reusable.

## Coordinator compatibility and evidence

The coordinator retains exact R7 support and additionally accepts only the
reviewed retrieval wheel SHA
`36a94a18a83889ceb5860d40ace998410d1bc3fcfbb806af9731199f331306a3`.
It recognizes both the existing per-boot lifetime path and the narrowly named
`/var/lib/sage-lantern-maintained-audio/<UUID>/new-lifetime.json`. Existing
records are not overwritten to fit the original namespace.

Revision 2 fixes owner unlock between the initial epoch observation and the
restore-only locked check. The bounded wait retains all `current()` checks,
adopts only an epoch observed unchanged across those checks, then revalidates
through the real post-auth checker and a final epoch read. Revocation and
storage/service failures are not retried as successful unlocks. Normal basic
restore still requires locked auth. Apply remains disabled.

Final source regression run: 268 passed, one existing SDK warning. Tests cover
handoff ordering, importer exclusion, real local child-process containment,
archive rename identity, capture/late-publication failure containment and
coordinator compatibility. Native providers in orchestration tests are fixtures,
not proof of on-device deployment or performance. The owner baseline observer
terminated after 900 seconds with 0/3 completed runs and was not restarted.
Matched before/after benchmarks remain outstanding.

Frozen source, tests, this document, test log and full SHA256 inventory are under
`/private/tmp/lantern-runtime-maintenance-review-20260907-v2`. Revision 1 remains
unchanged. Revision 2 includes the missing installer, benchmark helpers and
imported recovery fixture, plus runtime Python sources and the pinned SDK wheel
as test support (not deployment inputs). Run `sh review-tests.sh` from the
snapshot; Python/pytest and their installed dependencies are local prerequisites.
That inventory,
not a claim that the entire working tree is identical, defines the review set.
