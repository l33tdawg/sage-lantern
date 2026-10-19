# GNOME session launch and code-loading boundary — disabled candidate

**DISABLED CANDIDATE. Analysis only. Enforces nothing, installs nothing, loads no
BPF, opens no socket, and performs no I/O.**

Owner: Ember (`dsh/sage-commons`). Delegated by Rook, 2 October 2026.
Rook tracking `c672b468-8b5f-49e7-82d7-3ada2c4446a6`.
Ember task `51f1ccb5-1c79-4887-bf96-052490df7562`.

Normative reading: `work/ember-gnome-boundary-20261002/`.

**Revision 3, 2 October 2026.** Rook's source decision
(`work/rook-gnome-inbox-20261002/DECISION-0e9ca7f3.json`) selected Shape A and
named its conditions, and recorded that the mechanisms a narrowed path does not
close must be treated separately. That decision is `source_direction_only`: it is
**not** acceptance of revision 2, not enforcement acceptance, and not device
acceptance. Revision 3 acts on the parts of it that are source-level, and leaves
the capture it defers to Rook.

## What this package is

A pure decision function that answers one question:

> Given this observed system view, is the GNOME session launch and code-loading
> boundary **closed**?

It returns a verdict listing every individual finding — class, severity, path,
detail, mitigation — so a reviewer argues with a specific line rather than a
summary.

It also carries the **frozen-evidence consumer contract**
(`evidence_contract.py`): the rule a root loader uses to decide whether it may act
on a captured bundle at all. That contract was the one condition Rook attached to
deferring the inspection CLI, and it is now written. No CLI exists.

## What it is not

- Not an enforcement mechanism. Enforcement needs a root loader and kernel BPF
  attachment, both reserved to Rook and prohibited to this scope.
- Not a live probe. It never reads the filesystem; the caller supplies a
  `SystemView`.
- Not a fix. It identifies and scores; it does not change a single path.
- Not an acceptance. Nothing here has been measured on C, and nothing here
  authorises a change to C.

## The six design positions

1. **Absence of evidence is never closure.** An unobserved path is a finding, not
   a pass. `analyse` reports `closed=True` only when there are no blocking
   findings *and* no missing evidence.
2. **The ancestor rule.** A root-owned leaf under a user-writable parent is not
   authoritative, because the parent can replace the leaf.
3. **Authority is derived, never asserted.** No caller can mark a path
   authoritative. It is computed from typed ancestor evidence, and a claim that
   contradicts the evidence is refused rather than preferred.
4. **Omission is not an empty result.** A surface that was never captured blocks
   closure; a surface captured and found empty does not. These are different
   claims and they produce different verdicts.
5. **A fragment path is not a resolution.** (Revision 3.) A unit's fragment says
   where its main file is; drop-ins, aliases, assets and its effective environment
   decide what it actually runs. A writable drop-in is code execution.
6. **A narrowed path is not a closed manager.** (Revision 3.) Transient units,
   runtime properties, generators, environment sources, reload/reexec and
   alternate launch are separate mechanisms; closing the search path closes none
   of them.

Positions 1 and 2 come from the predecessor review. Positions 3 and 4 come from
Rook's review of revision 1. Positions 5 and 6 come from his decision of
2 October 2026.

## The evidence model

| Type | What it carries |
| --- | --- |
| `PathComponent` | One captured component: uid, gid, permission mode, `PathKind`, symlink target, size, and `Tri`-valued kiosk-writability (mode and ACL). |
| `AncestryEvidence` | The component chain for one path, with `complete` **computed** from the chain rather than asserted. May be partial; `observed_prefix` names how far the capture reached. |
| `PathObservation` | A path plus its ancestry. `authoritative` is a read-only property derived from `derive_authority()`. |
| `DirectoryInventory` / `DbusInventory` | Three-state: `UNOBSERVED` (blocks), `OBSERVED_EMPTY` (permits), `OBSERVED_POPULATED` (scored). Contradictory state/entries combinations are rejected. |
| `RequiredUnitEvidence` | A session-critical unit, its **resolved** fragment, and (revision 3) its `UnitResolution`. |
| `UnitResolution` | Revision 3: drop-ins, aliases, dependencies, executable/config assets, effective environment, and the capture method. `None` means a fragment-only read, which blocks. |
| `UnitPathProposal` | Revision 3: a proposed Shape A `$SYSTEMD_UNIT_PATH`, with the file that establishes it and the ordering evidence. |
| `ManagerMutationInventory` | Revision 3: which of the six other load-changing mechanisms remain reachable. `UNOBSERVED` blocks. |
| `SessionSelectionEvidence` | The inspected selection chain plus greeter/SSH independence. Replaces three top-level booleans. |

`Tri` is three-valued (`YES`/`NO`/`UNKNOWN`) because a capture that did not look
at ACLs must not be indistinguishable from one that looked and found none.

## Revision 3: Shape A, the separate mechanisms, and the contract

**Shape A is a typed proposal, and its conditions are refusals.**
`validate_unit_path_override()` returns typed reasons rather than a boolean, so a
loader reports why it refused instead of falling back to something writable —
the failure mode Rook prohibited. Refusals: `trailing_colon` (upstream v259
appends the default search paths to it), `empty_path`, `empty_entry`,
`relative_entry`, `non_canonical_entry`, `duplicate_entry`, `entry_not_captured`,
`entry_not_authoritative`, `establishing_file_not_authoritative`,
`ordering_not_stated`, `environment_not_attributed`. The establishing file is a
`PathObservation` with derived authority, not a boolean, because revision 2 was
corrected for exactly that mistake.

**The mechanisms a narrowed path does not close are modelled.** Each of
`transient_units`, `runtime_properties`, `generators`, `environment_sources`,
`manager_reload_reexec` and `alternate_launch` is a named, three-state entry, and
each reachable one is its own blocking finding. The last two are CRITICAL. An
unknown mechanism identifier raises `EvidenceError` rather than being scored
harmless.

**A unit resolution must actually be a resolution.** The required-unit check now
looks past the fragment: a missing resolution blocks, an unattributed effective
environment blocks, an alias must be shown to resolve to the same root-owned
fragment, a non-authoritative asset blocks, and a non-authoritative drop-in is
CRITICAL. Dependencies are recorded at INFO.

**The consumer contract.** `evidence_contract.verify_bundle()` refuses a bundle
with a missing section, an unstated three-state section, an unverifiable digest, a
digest that does not match the bytes, a value that does not re-encode to its own
payload, a value with no exact canonical encoding, an unrecognised contract
version, or sections that do not decode to this module's exact typed schema.
`evidence_contract.admit()` additionally requires that the full sections bind to
the view they travel with, that the digest bound for the analysis view is the
digest of the view **decoded from those sections**, and that this decoded view
reports the boundary closed. A correct bundle about an open boundary is refused; so
is a closed-looking view that is not the frozen one; so is a correctly encoded and
correctly digested section whose contents contradict that view. There is no
partially-consumable bundle.

Contract **version 3** is the current version, and the schema is exact: every
capture section decodes to `SystemView` and its nested dataclasses with all fields
present, no unknown fields, and no primitive coercion. Version 2 bound sections to
their bytes but not their contents to the view, which is why the version moved.

## Revision 2: Rook's five corrections

All five were defects revision 1 named in its own `REMAINING_GAPS.md` and did not
close. Each is implemented and has negative tests.

1. **Derived authority.** `authoritative` was a caller-supplied boolean,
   cross-checked only against other booleans from the same caller.
   `derive_authority()` now computes it from complete, canonical, typed ancestor
   evidence. Incomplete ancestry is refused; a contradicting claim marks the
   record inconsistent and is never preferred.
2. **Three-state inventories.** Absent extension and D-Bus inventories used to
   read as acceptable. They are now `ObservationState` records, and `UNOBSERVED`
   blocks.
3. **Typed mask detection.** Substring matching on free-text notes is replaced by
   the two conditions systemd actually defines — `st_size == 0` for a regular
   file, or a symlink to `/dev/null` — plus `RequiredUnitEvidence`.
4. **Exact shapes.** bool is bool (not `"false"`, which is truthy), int excludes
   bool (`True` is an `int`), mode is permission bits only, paths must be
   canonical and absolute, and counts are finite and non-negative. Rejections
   raise `EvidenceError` rather than being coerced.
5. **Computed precedence.** `generator.late` sorts *after* every root-owned
   entry, so it can only add a unit name nothing else provides; it is reported at
   INFO and does not block. Conversely, an unobserved search-path entry that
   *looks* root-owned is no longer skipped on the strength of its name.

## Usage

```python
import json
from software.security.gnome_session_boundary_candidate import analysis

view = analysis.view_from_inventory(json.load(open("launch-inventory.json")))
verdict = analysis.analyse(view)

print(analysis.summarise(verdict))
for finding in verdict.findings:
    print(finding.severity, finding.attack_class, finding.path, finding.detail)

# Revision 3: validate a proposed Shape A path before a loader would use it.
proposal = analysis.UnitPathProposal(
    entries=("/etc/systemd/user", "/usr/lib/systemd/user"),
    establishing_file=view.paths["/etc/systemd/user"],
    ordering_evidence="read by PID 1 before user@1000.service starts",
    observer_environment="root-owned session environment file",
)
print(analysis.validate_unit_path_override(proposal, view.paths).reasons)
```

```python
from software.security.gnome_session_boundary_candidate import evidence_contract

verdict = evidence_contract.admit(bundle, view=view, view_digest=frozen_digest)
print(verdict.consumable, verdict.reasons)
```

`view_from_inventory` accepts the shape the predecessor collector already
produces. That inventory records leaves and their immediate parents but **not**
`/home`, `/run` or `/usr`, so no path in it has a chain reaching an authority
boundary. The adapter keeps those partial observations and reports each one as
incomplete evidence; it does not drop the path, and it does not infer authority
from a root-owned leaf. The practical consequence is that the frozen inventory
now yields **22 findings**, up from 14 in revision 1.

To model surfaces the inventory does not contain, construct the typed values
directly and pass a `SystemView`. A view that omits `required_units`,
`session_selection`, `capture_provenance`, `unit_path_proposal`, the
manager-mechanism inventory, or either inventory is open by construction, which
is the intended default.

## Running the tests

From the repository root:

```
PYTHONPATH=.:software/runtime/src python3 -m pytest scripts/tests/test_ember_gnome_boundary.py -q
```

135 checks: 40 revision-3 and 11 revision-4 (the schema and binding regressions).
Self-contained: no `sage_lantern` import, so it
does not need the pinned `sage-agent-sdk` wheel. One test asserts that neither
module contains an `os`, `socket`, `subprocess`, `open(` or `pathlib` reference,
and another asserts `evidence_contract.py` imports only `hashlib`, `dataclasses`,
`typing`, `enum`, `json`, `collections.abc`, `types`, `__future__` and `analysis`,
so purity is guarded rather than merely claimed.
