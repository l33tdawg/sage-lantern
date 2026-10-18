"""Negative and boundary checks for the GNOME session-boundary analysis candidate.

The point of these tests is adversarial: they try to make the analyser report a
closed boundary when it is not closed, and they try to make it accept evidence
that does not support a verdict. Every such attempt must fail.

Review revision, 2 October 2026. The second block of tests exists specifically
because the first revision accepted five classes of unsupported evidence:
caller-asserted authority, omitted inventories, notes-based mask detection,
truthy flags, and name-based precedence. Each of those has its own section below
and is tested in both directions — the defect must be refused, and the honest
record must still be scorable.

Read-only. No device, no service, no BPF, no network, no mutation.

Run from the repository root:

    PYTHONPATH=.:software/runtime/src python3 -m pytest scripts/tests/test_ember_gnome_boundary.py -q
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE = ROOT / "software" / "security" / "gnome-session-boundary-candidate"
_PREFIX = "software.security.gnome_session_boundary_candidate"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if _PREFIX not in sys.modules:
    import importlib.util
    import types

    package = types.ModuleType(_PREFIX)
    package.__path__ = [str(_CANDIDATE)]  # type: ignore[attr-defined]
    sys.modules[_PREFIX] = package

    spec = importlib.util.spec_from_file_location(
        f"{_PREFIX}.analysis", _CANDIDATE / "analysis.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PREFIX}.analysis"] = module
    package.analysis = module  # type: ignore[attr-defined]
    spec.loader.exec_module(module)

    contract_spec = importlib.util.spec_from_file_location(
        f"{_PREFIX}.evidence_contract", _CANDIDATE / "evidence_contract.py"
    )
    assert contract_spec is not None and contract_spec.loader is not None
    contract_module = importlib.util.module_from_spec(contract_spec)
    sys.modules[f"{_PREFIX}.evidence_contract"] = contract_module
    package.evidence_contract = contract_module  # type: ignore[attr-defined]
    contract_spec.loader.exec_module(contract_module)

from software.security.gnome_session_boundary_candidate import analysis  # noqa: E402
from software.security.gnome_session_boundary_candidate import evidence_contract  # noqa: E402

AncestryEvidence = analysis.AncestryEvidence
AttackClass = analysis.AttackClass
DbusInventory = analysis.DbusInventory
DbusSurface = analysis.DbusSurface
DirectoryInventory = analysis.DirectoryInventory
EvidenceError = analysis.EvidenceError
ManagerMechanism = analysis.ManagerMechanism
ManagerMutationInventory = analysis.ManagerMutationInventory
ObservationState = analysis.ObservationState
PathComponent = analysis.PathComponent
PathKind = analysis.PathKind
PathObservation = analysis.PathObservation
Precedence = analysis.Precedence
RequiredUnitEvidence = analysis.RequiredUnitEvidence
SessionSelectionEvidence = analysis.SessionSelectionEvidence
Severity = analysis.Severity
SystemView = analysis.SystemView
Tri = analysis.Tri
UnitPathProposal = analysis.UnitPathProposal
UnitPathRefusal = analysis.UnitPathRefusal
UnitResolution = analysis.UnitResolution
analyse = analysis.analyse
classify_unit_path = analysis.classify_unit_path
derive_authority = analysis.derive_authority
summarise = analysis.summarise
validate_unit_path_override = analysis.validate_unit_path_override
view_from_inventory = analysis.view_from_inventory

admit = evidence_contract.admit
contract_document = evidence_contract.contract_document
section_digest = evidence_contract.section_digest
verify_bundle = evidence_contract.verify_bundle

FROZEN_INVENTORY = ROOT / "work" / "c-desktop-launch-closure-20261002" / "launch-inventory.json"

KAS = 1000


# --- Builders --------------------------------------------------------------


def component(path: str, *, uid: int = 0, gid: int = 0, mode: int = 0o755,
              kind: PathKind = PathKind.DIRECTORY, symlink_target: str | None = None,
              writable: Tri = Tri.NO, acl: Tri = Tri.NO,
              size: int | None = None) -> PathComponent:
    """One typed, canonical, fully-specified path component."""
    return PathComponent(
        path=path, prescribed_uid=uid, prescribed_gid=gid, mode=mode, kind=kind,
        symlink_target=symlink_target, writable_by_kiosk=writable,
        acl_writable_by_kiosk=acl, size_bytes=size,
    )


def chain_components(path: str, *, root_owned: bool = True,
                     writable: bool = False) -> list[PathComponent]:
    """Every component from the root (or a terminator) down to `path`.

    Derived from the module's own `_ancestor_chain`, so the fixtures cannot
    drift from the chain shape the analyser requires. Root-owned by default;
    `writable=True` marks uid 1000 / kiosk-writable.
    """
    built: list[PathComponent] = []
    for component_path in analysis._ancestor_chain(path):
        built.append(component(
            component_path,
            uid=0 if root_owned else KAS,
            gid=0 if root_owned else KAS,
            writable=Tri.YES if writable else Tri.NO,
        ))
        # Include the leaf always. A user-controlled subtree root (`/home/sage`,
        # `/run/user/1000`) terminates the walk only when it is a strict ancestor
        # of the leaf, never when it is the leaf itself.
        if component_path == path:
            break
        if analysis._is_chain_terminator(component_path):
            continue
    return built


def ancestry(path: str, *, root_owned: bool = True, writable: bool = False,
             leaf: PathComponent | None = None) -> AncestryEvidence:
    components = chain_components(path, root_owned=root_owned, writable=writable)
    if leaf is not None:
        components[-1] = leaf
    return AncestryEvidence(components=tuple(components))


def root_path(path: str) -> PathObservation:
    """A genuinely root-authoritative path: root-owned the whole way down."""
    return PathObservation(path=path, ancestry=ancestry(path))


def user_path(path: str) -> PathObservation:
    """Writable by the desktop user, so not authoritative however strict its mode."""
    return PathObservation(path=path, ancestry=ancestry(path, root_owned=False, writable=True))


def leaf_of(observation: PathObservation) -> PathComponent:
    leaf = observation.ancestry.leaf
    assert leaf is not None
    return leaf


def observed_empty(**kwargs) -> DirectoryInventory:
    return DirectoryInventory(ObservationState.OBSERVED_EMPTY, **kwargs)


def selection_evidence(**overrides) -> SessionSelectionEvidence:
    base = dict(
        root_controlled=True,
        chain=("/etc/gdm3/custom.conf", "/usr/lib/gdm3/gdm-wayland-session"),
        greeter_independent=True,
        root_ssh_independent=True,
        owner_ssh_key_only=True,
    )
    base.update(overrides)
    return SessionSelectionEvidence(**base)


def complete_resolution(**overrides) -> UnitResolution:
    """The full resolution Rook's condition requires, not a fragment-only read."""
    base = dict(
        capture_method="systemctl --user show -p FragmentPath,DropInPaths,Environment",
        effective_environment="root-owned unit environment, attributed by the capture",
    )
    base.update(overrides)
    return UnitResolution(**base)


def root_unit(unit: str = "org.gnome.Shell@wayland.service",
              fragment: str = "/usr/lib/systemd/user/org.gnome.Shell@wayland.service",
              kind: PathKind = PathKind.REGULAR_FILE,
              size: int | None = 4096,
              resolution: UnitResolution | None = None) -> RequiredUnitEvidence:
    return RequiredUnitEvidence(
        unit=unit,
        fragment=component(fragment, kind=kind, size=size),
        resolution=resolution if resolution is not None else complete_resolution(),
    )


def fragment_only_unit(unit: str = "org.gnome.Shell@wayland.service",
                       fragment: str = "/usr/lib/systemd/user/org.gnome.Shell@wayland.service"
                       ) -> RequiredUnitEvidence:
    """Revision 2's whole required-unit model: the fragment path and nothing else."""
    return RequiredUnitEvidence(
        unit=unit,
        fragment=component(fragment, kind=PathKind.REGULAR_FILE, size=4096),
    )


def proposal(**overrides) -> UnitPathProposal:
    """A Shape A proposal that satisfies every condition Rook named."""
    base = dict(
        entries=("/etc/systemd/user", "/usr/lib/systemd/user"),
        establishing_file=root_path(
            "/etc/systemd/system/user@1000.service.d/10-unit-path.conf"),
        ordering_evidence=(
            "EnvironmentFile= in user@1000.service sets SYSTEMD_UNIT_PATH before "
            "the user manager starts; the drop-in is read by PID 1"
        ),
        observer_environment="root-owned /etc/sage-lantern/session-env.conf",
    )
    base.update(overrides)
    return UnitPathProposal(**base)


def closed_view(**overrides) -> SystemView:
    """A deliberately closed baseline, so each test can open exactly one hole.

    Every path is a canonical absolute path and every one is root-owned with
    captured ancestors. Required-unit resolution and an empty observed D-Bus
    surface are supplied, because a view that omits them is *unobserved* and
    therefore open by design. Revision 3 adds two more things a closed baseline
    now needs: a validated Shape A narrow path (Rook selected Shape A as source
    direction) and an explicit statement that none of the manager's other
    load-changing mechanisms is reachable. Both are modelled here; neither has
    been measured on C.
    """
    etc = "/etc/systemd/user"
    usr = "/usr/lib/systemd/user"
    base = dict(
        unit_search_path=(etc, usr),
        paths={etc: root_path(etc), usr: root_path(usr)},
        extension_inventory=observed_empty(description="gnome-shell extensions"),
        dbus_inventory=DbusInventory(ObservationState.OBSERVED_EMPTY),
        required_units=(root_unit(),),
        session_selection=selection_evidence(),
        manager_mutations=ManagerMutationInventory(ObservationState.OBSERVED_EMPTY),
        unit_path_proposal=proposal(),
        captured_at="2026-10-02T00:00:00Z",
        capture_provenance="systemd-analyze --user unit-paths under the kiosk session env",
    )
    base.update(overrides)
    return SystemView(**base)


# --- The baseline must actually be closed, or nothing below means anything --


def test_closed_baseline_reports_closed():
    verdict = analyse(closed_view())
    assert verdict.closed is True
    assert verdict.findings == ()
    assert verdict.checked > 0


def test_summary_of_closed_view_does_not_overclaim():
    text = summarise(analyse(closed_view()))
    assert "CLOSED" in text


# --- NEGATIVE: unobserved evidence must never be treated as closed ---------


def test_empty_view_is_not_closed():
    verdict = analyse(SystemView(unit_search_path=(), paths={}))
    assert verdict.closed is False
    assert set(verdict.missing_evidence) >= {
        "unit_search_path", "paths", "captured_at", "capture_provenance",
        "required_units", "session_selection",
    }


def test_wrong_container_type_for_paths_fails_fast():
    """A tuple where a mapping belongs must raise a clear error, not a deep one."""
    with pytest.raises(EvidenceError, match="paths must be a mapping"):
        SystemView(unit_search_path=(), paths=())  # type: ignore[arg-type]


def test_wrong_value_type_in_paths_fails_fast():
    with pytest.raises(EvidenceError, match="PathObservation"):
        SystemView(unit_search_path=(), paths={"/x": "not an observation"})  # type: ignore[dict-item]


def test_unobserved_writable_search_entry_is_a_finding():
    """The predecessor review says a missing leaf is not closure. Prove it."""
    etc = "/etc/systemd/user"
    view = closed_view(
        unit_search_path=("/run/user/1000/systemd/transient", etc),
        paths={etc: root_path(etc)},
    )
    verdict = analyse(view)
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.UNIT_SHADOWING)


def test_unobserved_root_entry_alone_does_not_block_but_absence_of_all_does():
    etc = "/etc/systemd/user"
    view = closed_view(unit_search_path=(etc,), paths={})
    verdict = analyse(view)
    assert verdict.closed is False


def test_missing_capture_timestamp_is_missing_evidence():
    view = closed_view(captured_at="")
    verdict = analyse(view)
    assert "captured_at" in verdict.missing_evidence
    assert verdict.closed is False


# --- NEGATIVE: each attack class opens the boundary ------------------------


def test_user_control_shadowing_opens_the_boundary():
    """The highest-precedence user path is kiosk-writable on C today."""
    user_control = "/home/sage/.config/systemd/user.control"
    etc = "/etc/systemd/user"
    view = closed_view(
        unit_search_path=(user_control, etc),
        paths={user_control: user_path(user_control), etc: root_path(etc)},
    )
    verdict = analyse(view)
    assert verdict.closed is False
    findings = verdict.by_class(AttackClass.UNIT_SHADOWING)
    assert findings and findings[0].severity == Severity.CRITICAL
    assert findings[0].blocking is True


@pytest.mark.parametrize("directory", [
    "/run/user/1000/systemd/transient",
])
def test_transient_directory_opens_the_boundary(directory):
    etc = "/etc/systemd/user"
    view = closed_view(
        unit_search_path=(directory, etc),
        paths={directory: user_path(directory), etc: root_path(etc)},
    )
    verdict = analyse(view)
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.TRANSIENT_INJECTION)


@pytest.mark.parametrize("directory", [
    "/run/user/1000/systemd/generator.early",
    "/run/user/1000/systemd/generator",
])
def test_early_and_normal_generator_directories_open_the_boundary(directory):
    etc = "/etc/systemd/user"
    view = closed_view(
        unit_search_path=(directory, etc),
        paths={directory: user_path(directory), etc: root_path(etc)},
    )
    verdict = analyse(view)
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.GENERATOR_INJECTION)


def test_writable_manager_environment_opens_the_boundary():
    etc = "/etc/systemd/user"
    usr = "/usr/lib/systemd/user"
    env = "/home/sage/.config/environment.d"
    view = closed_view(
        paths={etc: root_path(etc), usr: root_path(usr), env: user_path(env)},
    )
    verdict = analyse(view)
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.MANAGER_ENVIRONMENT)


def test_writable_extension_directory_opens_the_boundary():
    view = closed_view(
        extension_inventory=DirectoryInventory(
            ObservationState.OBSERVED_POPULATED,
            entries=(user_path("/home/sage/.local/share/gnome-shell/extensions"),),
        ),
    )
    verdict = analyse(view)
    assert verdict.closed is False
    findings = verdict.by_class(AttackClass.EXTENSION_CODE)
    assert findings and findings[0].severity == Severity.CRITICAL


def test_root_owned_extension_directory_is_acceptable():
    view = closed_view(
        extension_inventory=DirectoryInventory(
            ObservationState.OBSERVED_POPULATED,
            entries=(root_path("/usr/share/gnome-shell/extensions"),),
        ),
    )
    assert analyse(view).closed is True


def test_unauthenticated_code_execution_dbus_surface_opens_the_boundary():
    view = closed_view(dbus_inventory=DbusInventory(
        ObservationState.OBSERVED_POPULATED,
        surfaces=(DbusSurface(name="org.gnome.Shell", interface="org.gnome.Shell.Eval",
                              reachable_by_user=True, authenticated=False,
                              permits_code_execution=True),),
    ))
    verdict = analyse(view)
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.SHELL_DBUS_EXEC)


def test_authenticated_dbus_surface_does_not_open_the_boundary():
    view = closed_view(dbus_inventory=DbusInventory(
        ObservationState.OBSERVED_POPULATED,
        surfaces=(DbusSurface(name="org.gnome.Shell", interface="org.gnome.Shell.Screenshot",
                              reachable_by_user=True, authenticated=True,
                              permits_code_execution=False),),
    ))
    assert analyse(view).closed is True


def test_writable_autostart_opens_the_boundary():
    etc = "/etc/systemd/user"
    usr = "/usr/lib/systemd/user"
    autostart = "/home/sage/.config/autostart"
    view = closed_view(
        paths={etc: root_path(etc), usr: root_path(usr), autostart: user_path(autostart)},
    )
    verdict = analyse(view)
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.AUTOSTART_INJECTION)


def test_non_root_controlled_session_selection_opens_the_boundary():
    verdict = analyse(closed_view(session_selection=selection_evidence(root_controlled=False)))
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.SEARCH_PATH_REDIRECT)


def test_missing_greeter_independence_opens_the_boundary():
    verdict = analyse(closed_view(session_selection=selection_evidence(greeter_independent=False)))
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.GREETER_OR_SSH_BLAST_RADIUS)


def test_missing_root_ssh_independence_opens_the_boundary():
    """Root SSH is the recovery path; losing it is its own failure mode."""
    verdict = analyse(closed_view(session_selection=selection_evidence(root_ssh_independent=False)))
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.GREETER_OR_SSH_BLAST_RADIUS)


def test_root_ssh_without_key_only_opens_the_boundary():
    """Surviving is not enough: a password-auth root SSH is its own surface."""
    verdict = analyse(closed_view(session_selection=selection_evidence(owner_ssh_key_only=False)))
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.GREETER_OR_SSH_BLAST_RADIUS)


# ===========================================================================
# FIX 1 — authority is DERIVED, never asserted by the caller
# ===========================================================================


def test_fix1_authority_is_derived_from_ancestry():
    assert root_path("/etc/systemd/user").authoritative is True
    assert user_path("/home/sage/.config/systemd/user").authoritative is False


def test_fix1_strict_leaf_mode_under_writable_parent_is_not_authoritative():
    """The ancestor rule, now actually enforced rather than encoded.

    The leaf is root-owned mode 0700. Its parent is uid 1000. The parent can
    replace the leaf, so no leaf mode under it establishes closure.
    """
    leaf = "/home/sage/.config/systemd/user"
    components = chain_components(leaf, root_owned=False, writable=True)
    components[-1] = component(leaf, uid=0, gid=0, mode=0o700, writable=Tri.NO)
    observation = PathObservation(path=leaf, ancestry=AncestryEvidence(components=tuple(components)))

    assert observation.authoritative is False
    assert any("writable by the desktop user" in r for r in observation.authority.reasons)
    verdict = analyse(closed_view(
        unit_search_path=(leaf, "/etc/systemd/user"),
        paths={leaf: observation, "/etc/systemd/user": root_path("/etc/systemd/user")},
    ))
    assert verdict.closed is False


def test_fix1_caller_claim_of_root_ownership_does_not_override_ancestry():
    """The exact defect: a root-owned *claim* under a writable parent."""
    leaf = "/home/sage/.config/systemd/user.control"
    observation = PathObservation(
        path=leaf,
        ancestry=ancestry(leaf, root_owned=False, writable=True),
        claimed_owned_by_root=True,
        claimed_writable_by_user=False,
    )
    assert observation.authoritative is False
    assert observation.inconsistent is True
    verdict = analyse(closed_view(
        unit_search_path=(leaf, "/etc/systemd/user"),
        paths={leaf: observation, "/etc/systemd/user": root_path("/etc/systemd/user")},
    ))
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.INCONSISTENT_EVIDENCE)


def test_fix1_consistent_claims_are_accepted_and_still_derived():
    etc = "/etc/systemd/user"
    leaf = leaf_of(root_path(etc))
    observation = PathObservation(
        path=etc,
        ancestry=ancestry(etc),
        claimed_present=leaf.kind is not PathKind.ABSENT,
        claimed_owned_by_root=leaf.is_root_owned,
        claimed_writable_by_user=False,
    )
    assert observation.inconsistent is False
    assert observation.authoritative is True
    assert analyse(closed_view(paths={etc: observation,
                                      "/usr/lib/systemd/user": root_path("/usr/lib/systemd/user")})).closed is True


def test_fix1_incomplete_ancestry_is_refused_not_scored():
    """Half a chain is not evidence. It must not yield an authoritative verdict."""
    partial = AncestryEvidence(components=(component("/etc"), component("/etc/systemd/user")))
    verdict_authority = derive_authority(partial)
    assert verdict_authority.authoritative is False
    assert any("incomplete" in r for r in verdict_authority.reasons)


def test_fix1_a_symlink_anywhere_in_the_chain_prevents_authority():
    """A symlinked ancestor can be repointed, so it cannot establish authority."""
    target = "/etc/systemd/user"
    components = list(ancestry(target).components)
    components[-2] = component("/etc/systemd", kind=PathKind.SYMLINK,
                               symlink_target="/var/lib/elsewhere")
    observation = PathObservation(path=target,
                                  ancestry=AncestryEvidence(components=tuple(components)))
    assert observation.authoritative is False
    assert any("symlink" in r for r in observation.authority.reasons)


def test_fix1_unobserved_acl_state_blocks_authority():
    """Not looking at ACLs is not the same as looking and finding none."""
    etc = "/etc/systemd/user"
    components = list(ancestry(etc).components)
    components[-1] = component(etc, acl=Tri.UNKNOWN)
    observation = PathObservation(path=etc,
                                  ancestry=AncestryEvidence(components=tuple(components)))
    assert observation.authoritative is False
    assert any("ACL state was not observed" in r for r in observation.authority.reasons)


def test_fix1_relative_and_tilde_paths_are_refused():
    """The first revision reasoned about `~/.config/...` as though it were a path."""
    for bad in ("~/.config/systemd/user.control", "etc/systemd/user",
                "/etc/systemd/user/", "/etc//systemd/user", "/etc/../etc/systemd/user"):
        with pytest.raises(EvidenceError):
            component(bad)


# ===========================================================================
# FIX 2 — three-state inventories: omission is not an empty result
# ===========================================================================


def test_fix2_unobserved_extension_inventory_blocks_closure():
    """The exact weakest point: supplying no directories used to score clean."""
    view = closed_view()
    view = SystemView(**{**view.__dict__,
                         "extension_inventory": DirectoryInventory(ObservationState.UNOBSERVED)})
    verdict = analyse(view)
    assert verdict.closed is False
    findings = verdict.by_class(AttackClass.INCOMPLETE_EVIDENCE)
    assert any(f.path == "extension_dirs" for f in findings)


def test_fix2_unobserved_dbus_inventory_blocks_closure():
    view = closed_view()
    view = SystemView(**{**view.__dict__,
                         "dbus_inventory": DbusInventory(ObservationState.UNOBSERVED)})
    verdict = analyse(view)
    assert verdict.closed is False
    assert any(f.path == "dbus_surfaces" for f in verdict.by_class(AttackClass.INCOMPLETE_EVIDENCE))


def test_fix2_observed_empty_is_distinguishable_and_permits():
    view = closed_view(
        extension_inventory=observed_empty(description="looked, found none"),
        dbus_inventory=DbusInventory(ObservationState.OBSERVED_EMPTY),
    )
    assert analyse(view).closed is True


def test_fix2_observed_empty_cannot_smuggle_entries():
    """Claiming you looked and found none while listing entries is a lie."""
    with pytest.raises(EvidenceError, match="OBSERVED_EMPTY"):
        DirectoryInventory(ObservationState.OBSERVED_EMPTY,
                           entries=(root_path("/usr/share/gnome-shell/extensions"),))
    with pytest.raises(EvidenceError, match="OBSERVED_EMPTY"):
        DbusInventory(ObservationState.OBSERVED_EMPTY,
                      surfaces=(DbusSurface(name="a", interface="b", reachable_by_user=False,
                                            authenticated=True, permits_code_execution=False),))


def test_fix2_unobserved_cannot_smuggle_entries():
    with pytest.raises(EvidenceError, match="UNOBSERVED"):
        DirectoryInventory(ObservationState.UNOBSERVED,
                           entries=(root_path("/usr/share/gnome-shell/extensions"),))


def test_fix2_populated_without_entries_is_refused():
    with pytest.raises(EvidenceError, match="OBSERVED_POPULATED"):
        DirectoryInventory(ObservationState.OBSERVED_POPULATED)


def test_fix2_absent_capture_provenance_opens_the_boundary():
    """An unattributed search path may be one nobody redirected, or one somebody did."""
    verdict = analyse(closed_view(capture_provenance=""))
    assert verdict.closed is False
    assert "capture_provenance" in verdict.missing_evidence
    assert verdict.by_class(AttackClass.MANAGER_ENVIRONMENT)


# ===========================================================================
# FIX 3 — typed mask detection, and resolved required-unit evidence
# ===========================================================================


def test_fix3_empty_regular_file_is_a_mask():
    """systemd.unit(5): file size 0 -> masked."""
    assert component("/x", kind=PathKind.REGULAR_FILE, size=0).is_masking_file() is True
    assert component("/x", kind=PathKind.REGULAR_FILE, size=1).is_masking_file() is False


def test_fix3_dev_null_symlink_is_a_mask():
    assert component("/x", kind=PathKind.SYMLINK,
                     symlink_target="/dev/null").is_masking_file() is True
    assert component("/x", kind=PathKind.SYMLINK,
                     symlink_target="/usr/lib/systemd/user/x.service").is_masking_file() is False


def test_fix3_free_text_note_no_longer_triggers_a_mask_finding():
    """A note saying 'mask' proves nothing; only size and target do.

    The first revision substring-matched 'mask', 'dev/null' and 'empty' in a note
    string. An honest, non-masked file whose note happens to mention masking used
    to produce a finding; it must not now.
    """
    etc = "/etc/systemd/user"
    components = list(ancestry(etc).components)
    components[-1] = component(etc, kind=PathKind.REGULAR_FILE, size=4096)
    observation = PathObservation(path=etc, ancestry=AncestryEvidence(components=tuple(components)))
    verdict = analyse(closed_view(
        unit_search_path=(etc,),
        paths={etc: observation},
    ))
    assert verdict.by_class(AttackClass.MASK_DENIAL) == ()


def test_fix3_search_path_entry_that_is_a_dev_null_symlink_opens_the_boundary():
    entry = "/home/sage/.config/systemd/user.control"
    components = chain_components(entry, root_owned=False, writable=True)
    components[-1] = component(entry, uid=KAS, gid=KAS, kind=PathKind.SYMLINK,
                               symlink_target="/dev/null", writable=Tri.YES)
    observation = PathObservation(path=entry,
                                  ancestry=AncestryEvidence(components=tuple(components)))
    etc = "/etc/systemd/user"
    verdict = analyse(closed_view(
        unit_search_path=(entry, etc),
        paths={entry: observation, etc: root_path(etc)},
    ))
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.MASK_DENIAL)


def test_fix3_required_unit_resolving_to_a_mask_opens_the_boundary():
    """Direct evidence: this is what the session actually loads."""
    verdict = analyse(closed_view(required_units=(
        root_unit(fragment="/home/sage/.config/systemd/user/org.gnome.Shell@wayland.service",
                  kind=PathKind.REGULAR_FILE, size=0),
    )))
    assert verdict.closed is False
    findings = verdict.by_class(AttackClass.REQUIRED_UNIT_MASKED)
    assert findings and findings[0].severity == Severity.CRITICAL


def test_fix3_required_unit_resolving_to_dev_null_opens_the_boundary():
    masked = RequiredUnitEvidence(
        unit="org.gnome.Shell@wayland.service",
        fragment=component("/home/sage/.config/systemd/user/org.gnome.Shell@wayland.service",
                           kind=PathKind.SYMLINK, symlink_target="/dev/null"),
    )
    verdict = analyse(closed_view(required_units=(masked,)))
    assert verdict.closed is False
    findings = verdict.by_class(AttackClass.REQUIRED_UNIT_MASKED)
    assert findings and findings[0].severity == Severity.CRITICAL


def test_fix3_unresolved_required_unit_is_incomplete_evidence():
    """Not capturing the resolution is not the same as the unit being fine."""
    unresolved = RequiredUnitEvidence(
        unit="org.gnome.Shell@wayland.service",
        fragment=component("/usr/lib/systemd/user/org.gnome.Shell@wayland.service",
                           kind=PathKind.REGULAR_FILE, size=4096),
        observed=False,
    )
    verdict = analyse(closed_view(required_units=(unresolved,)))
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.INCOMPLETE_EVIDENCE)


def test_fix3_no_required_units_is_missing_evidence():
    verdict = analyse(closed_view(required_units=()))
    assert verdict.closed is False
    assert "required_units" in verdict.missing_evidence


# ===========================================================================
# FIX 4 — exact types, not truthy flags
# ===========================================================================


def test_fix4_truthy_string_is_not_a_boolean():
    """`"false"` is truthy. The first revision accepted it as a flag."""
    with pytest.raises(EvidenceError, match="must be a Tri"):
        component("/x", writable="false")  # type: ignore[arg-type]
    with pytest.raises(EvidenceError, match="must be a Tri"):
        component("/x", acl="no")  # type: ignore[arg-type]


def test_fix4_truthy_string_is_not_a_boolean_on_a_surface():
    with pytest.raises(EvidenceError, match="exactly bool"):
        DbusSurface(name="a", interface="b", reachable_by_user="false",  # type: ignore[arg-type]
                    authenticated=True, permits_code_execution=False)


def test_fix4_tri_accepts_only_the_three_enum_members():
    for value in (Tri.YES, Tri.NO, Tri.UNKNOWN):
        assert component("/x", writable=value).writable_by_kiosk is value
    with pytest.raises(EvidenceError, match="must be a Tri"):
        component("/x", writable=True)  # type: ignore[arg-type]


def test_fix4_bool_is_refused_where_an_int_is_required():
    """`True` is an instance of `int`, so this needs an explicit guard."""
    with pytest.raises(EvidenceError, match="exactly int"):
        component("/x", uid=True)  # type: ignore[arg-type]


def test_fix4_float_is_refused_where_an_int_is_required():
    with pytest.raises(EvidenceError, match="exactly int"):
        component("/x", mode=493.0)  # type: ignore[arg-type]  # 0o755 as a float


def test_fix4_negative_and_out_of_range_ints_are_refused():
    with pytest.raises(EvidenceError, match=">= 0"):
        component("/x", uid=-1)
    # Mode is permission bits only: 0o7777 is the ceiling, so a value carrying
    # file-type bits (0o100000) is rejected rather than silently masked off.
    with pytest.raises(EvidenceError, match="<= 4095"):
        component("/x", mode=0o100000)
    with pytest.raises(EvidenceError):
        component("/x", kind=PathKind.REGULAR_FILE, size=-5)


def test_fix4_nan_and_infinity_are_refused():
    assert analysis._require_finite_float(1.5, "x") == 1.5
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(EvidenceError, match="finite"):
            analysis._require_finite_float(bad, "x")


def test_fix4_symlink_without_a_target_is_refused():
    with pytest.raises(EvidenceError, match="must record its target"):
        component("/x", kind=PathKind.SYMLINK)


def test_fix4_symlink_target_on_a_non_symlink_is_refused():
    with pytest.raises(EvidenceError, match="only meaningful for kind=SYMLINK"):
        component("/x", kind=PathKind.DIRECTORY, symlink_target="/dev/null")


def test_fix4_absent_component_cannot_be_writable():
    with pytest.raises(EvidenceError, match="absent component is not writable"):
        component("/x", kind=PathKind.ABSENT, writable=Tri.YES)


def test_fix4_empty_identifiers_are_refused():
    with pytest.raises(EvidenceError, match="must not be empty"):
        DbusSurface(name="   ", interface="b", reachable_by_user=False,
                    authenticated=True, permits_code_execution=False)
    with pytest.raises(EvidenceError, match="must not be empty"):
        RequiredUnitEvidence(
            unit="",
            fragment=component("/x", kind=PathKind.REGULAR_FILE, size=1),
        )


def test_fix4_unknown_bool_flag_is_refused_rather_than_coerced():
    with pytest.raises(EvidenceError, match="exactly bool"):
        DbusSurface(name="a", interface="b", reachable_by_user="no",  # type: ignore[arg-type]
                    authenticated=True, permits_code_execution=False)


def test_fix4_session_selection_chain_must_name_something():
    """A bare `True` used to certify the launch path. An empty chain cannot."""
    with pytest.raises(EvidenceError, match="at least one inspected component"):
        SessionSelectionEvidence(root_controlled=True, chain=(), greeter_independent=True,
                                 root_ssh_independent=True, owner_ssh_key_only=True)
    with pytest.raises(EvidenceError):
        SessionSelectionEvidence(root_controlled=True, chain=("not/absolute",),
                                 greeter_independent=True, root_ssh_independent=True,
                                 owner_ssh_key_only=True)


def test_fix4_paths_mapping_key_must_match_its_observation():
    with pytest.raises(EvidenceError, match="disagrees with its observation"):
        SystemView(unit_search_path=("/etc/systemd/user",),
                   paths={"/etc/systemd/user": root_path("/usr/lib/systemd/user")})


def test_fix4_enums_must_be_the_right_enum():
    with pytest.raises(EvidenceError, match="ObservationState"):
        DirectoryInventory("unobserved")  # type: ignore[arg-type]


# ===========================================================================
# FIX 5 — real precedence, and refusal of missing root entries
# ===========================================================================


def test_fix5_generator_late_is_classified_late_not_shadowing():
    """My own over-broad flagging, corrected.

    generator.late sorts after /usr/lib/systemd/user. A unit there cannot replace
    or mask a root-provided unit.
    """
    late = "/run/user/1000/systemd/generator.late"
    etc = "/etc/systemd/user"
    usr = "/usr/lib/systemd/user"
    view = closed_view(
        unit_search_path=(etc, usr, late),
        paths={etc: root_path(etc), usr: root_path(usr), late: user_path(late)},
    )
    classified = {entry.path: entry.precedence for entry in classify_unit_path(view)}
    assert classified[late] is Precedence.LATE

    verdict = analyse(view)
    assert verdict.closed is True, "generator.late must not block closure"
    findings = verdict.by_class(AttackClass.GENERATOR_LATE_INJECTION)
    assert findings and findings[0].blocking is False
    assert findings[0].severity == Severity.INFO


def test_fix5_normal_generator_is_not_mistaken_for_generator_late():
    """The normal generator sorts above every root-owned entry.

    It was demoted to non-blocking because the *writability-marker* test was a
    substring match: `"/systemd/generator" in "/run/user/1000/systemd/generator"`
    is True, and the same marker list is used to describe the late directory. A
    substring test cannot tell `generator` from `generator.late`, so the normal
    directory inherited the late directory's benign classification. Markers are
    now compared as whole `/`-delimited components.
    """
    normal = "/run/user/1000/systemd/generator"
    etc = "/etc/systemd/user"
    view = closed_view(unit_search_path=(normal, etc),
                       paths={normal: user_path(normal), etc: root_path(etc)})
    classified = {e.path: e.precedence for e in classify_unit_path(view)}
    assert classified[normal] is Precedence.SHADOWING
    findings = analyse(view).by_class(AttackClass.GENERATOR_INJECTION)
    assert findings and findings[0].blocking is True


def test_fix5_writable_by_design_marker_matches_whole_components_only():
    assert analysis._matches_marker("/run/user/1000/systemd/generator",
                                    analysis.WRITABLE_BY_DESIGN_MARKERS) is True
    assert analysis._matches_marker("/run/user/1000/systemd/generator.late",
                                    analysis.WRITABLE_BY_DESIGN_MARKERS) is True
    # A path that merely starts with a marker's text is not a marker match.
    assert analysis._matches_marker("/etc/systemd/user.control.bak",
                                    ("/systemd/user.control",)) is False
    # And the two generator directories are distinguishable from each other.
    assert analysis._matches_marker("/run/user/1000/systemd/generator",
                                    ("/systemd/generator.late",)) is False


def test_fix5_generator_early_is_classified_shadowing():
    early = "/run/user/1000/systemd/generator.early"
    etc = "/etc/systemd/user"
    view = closed_view(
        unit_search_path=(early, etc),
        paths={early: user_path(early), etc: root_path(etc)},
    )
    classified = {entry.path: entry.precedence for entry in classify_unit_path(view)}
    assert classified[early] is Precedence.SHADOWING
    assert analyse(view).closed is False


def test_fix5_an_unobserved_root_path_entry_is_refused_not_ignored():
    """The first revision skipped unobserved entries that looked root-owned."""
    usr = "/usr/lib/systemd/user"
    view = closed_view(unit_search_path=("/usr/share/systemd/user", usr),
                       paths={usr: root_path(usr)})
    verdict = analyse(view)
    assert verdict.closed is False
    findings = verdict.by_class(AttackClass.INCOMPLETE_EVIDENCE)
    assert any(f.path == "/usr/share/systemd/user" for f in findings)


def test_fix5_precedence_is_computed_from_the_captured_order():
    """Order decides, not the name: the same entry at two positions differs."""
    entry = "/opt/vendor/systemd/user"
    etc = "/etc/systemd/user"
    shadowing = closed_view(unit_search_path=(entry, etc),
                            paths={entry: user_path(entry), etc: root_path(etc)})
    assert analyse(shadowing).closed is False

    trailing = closed_view(unit_search_path=(etc, entry),
                           paths={entry: user_path(entry), etc: root_path(etc)})
    classified = {e.path: e.precedence for e in classify_unit_path(trailing)}
    assert classified[entry] is not Precedence.SHADOWING


def test_fix5_verdict_exposes_the_precedence_table():
    verdict = analyse(closed_view())
    assert verdict.precedence
    assert all(isinstance(entry.path, str) for entry in verdict.precedence)


def test_fix5_writable_by_design_is_only_used_for_unobserved_entries():
    transient = "/run/user/1000/systemd/transient"
    entries = classify_unit_path(closed_view(unit_search_path=(transient,), paths={}))
    assert entries[0].writable_by_design is True
    observed = classify_unit_path(closed_view(
        unit_search_path=(transient,), paths={transient: root_path(transient)}))
    assert observed[0].writable_by_design is True


# --- NEGATIVE: contradictory evidence is refused, not resolved -------------


def test_self_contradictory_observation_is_refused():
    """A claim that contradicts its own ancestry: refuse rather than pick a side."""
    etc = "/etc/systemd/user"
    observation = PathObservation(path=etc, ancestry=ancestry(etc),
                                  claimed_writable_by_user=True)
    assert observation.inconsistent is True
    usr = "/usr/lib/systemd/user"
    view = closed_view(paths={etc: observation, usr: root_path(usr)})
    verdict = analyse(view)
    assert verdict.closed is False
    assert any("self-contradictory" in f.detail for f in verdict.findings)


# --- Real frozen evidence: the analysis must open the boundary on C's data --


@pytest.mark.skipif(not FROZEN_INVENTORY.exists(), reason="frozen inventory not present")
def test_frozen_predecessor_inventory_is_not_closed():
    inventory = json.loads(FROZEN_INVENTORY.read_text())
    view = view_from_inventory(inventory)
    verdict = analyse(view)
    assert verdict.closed is False, "the frozen inventory must never report closure"

    classes = {f.attack_class for f in verdict.findings}
    # The search path it reports includes user-writable entries the predecessor
    # inventory did not audit; those must surface as shadowing findings.
    assert AttackClass.UNIT_SHADOWING in classes
    # Session selection, greeter and root SSH independence are not established.
    assert AttackClass.SEARCH_PATH_REDIRECT in classes
    assert AttackClass.GREETER_OR_SSH_BLAST_RADIUS in classes
    # Extension and D-Bus inventories are absent from this capture: absent must
    # block, not read as clean.
    assert AttackClass.INCOMPLETE_EVIDENCE in classes

    # Every finding that asserts user-writability must be backed by it. A
    # root-owned entry whose ancestry is merely incomplete is a missing-proof
    # finding, not a writability claim, and the detail must not say otherwise.
    for finding in verdict.findings:
        if "writable by the desktop user" in finding.detail:
            observation = view.paths.get(finding.path)
            assert observation is not None, finding.path
            assert observation.writable_by_user is True, (
                f"{finding.path} is reported as writable by the desktop user but "
                f"the capture does not show that"
            )


@pytest.mark.skipif(not FROZEN_INVENTORY.exists(), reason="frozen inventory not present")
def test_frozen_inventory_root_owned_entries_are_incomplete_not_shadowing():
    """A root-owned entry with a leaf-only capture is missing proof, not a risk."""
    inventory = json.loads(FROZEN_INVENTORY.read_text())
    view = view_from_inventory(inventory)
    verdict = analyse(view)
    for path in ("/etc/systemd/user", "/usr/lib/systemd/user", "/usr/local/lib/systemd/user"):
        observation = view.paths.get(path)
        if observation is None:
            continue
        assert observation.writable_by_user is False
        shadowing = [f for f in verdict.by_class(AttackClass.UNIT_SHADOWING)
                     if f.path == path]
        assert shadowing == [], (
            f"{path} is root-owned and not writable; it must not be reported as "
            f"a shadowing risk"
        )
        incomplete = [f for f in verdict.by_class(AttackClass.INCOMPLETE_EVIDENCE)
                      if f.path == path]
        assert incomplete, f"{path} must be reported as incomplete evidence"


@pytest.mark.skipif(not FROZEN_INVENTORY.exists(), reason="frozen inventory not present")
def test_inventory_adapter_reads_the_reported_unit_path():
    inventory = json.loads(FROZEN_INVENTORY.read_text())
    view = view_from_inventory(inventory)
    assert view.unit_search_path, "must parse UnitPath= from the captured readback"
    assert any("user.control" in entry for entry in view.unit_search_path)
    assert view.captured_at


@pytest.mark.skipif(not FROZEN_INVENTORY.exists(), reason="frozen inventory not present")
def test_inventory_adapter_derives_non_authority_for_kiosk_writable_paths():
    """Derived from real ancestor rows: /home/sage is uid 1000, mode 0750."""
    inventory = json.loads(FROZEN_INVENTORY.read_text())
    view = view_from_inventory(inventory)
    writable = [p for p, o in view.paths.items() if o.writable_by_user]
    assert writable, "the frozen inventory records kiosk-writable paths"
    for path in writable:
        observation = view.paths[path]
        assert observation.authoritative is False
        assert observation.authority.reasons, "non-authority must state its reason"

    # The ancestor rule against real data, not a constructed example.
    user_dir = "/home/sage/.config/systemd/user"
    if user_dir in view.paths:
        reasons = view.paths[user_dir].authority.reasons
        assert any("/home/sage" in reason for reason in reasons), reasons


@pytest.mark.skipif(not FROZEN_INVENTORY.exists(), reason="frozen inventory not present")
def test_inventory_adapter_never_asserts_authority_without_a_complete_chain():
    """The frozen capture stops short of the root, so nothing may claim authority.

    This is the central invariant of the fix: the predecessor inventory records
    leaves and their immediate parents but not `/home`, `/run` and `/usr`, so no
    path in it has a chain reaching an authority boundary. Every observation must
    therefore refuse authority and say why, rather than inheriting the leaf's own
    `uid == 0` — which is exactly what the first revision did.
    """
    inventory = json.loads(FROZEN_INVENTORY.read_text())
    view = view_from_inventory(inventory)
    assert view.paths, "the adapter must keep partial observations, not drop them"

    authoritative_count = 0
    for path, observation in view.paths.items():
        assert observation.ancestry.components, path
        if observation.authoritative:
            authoritative_count += 1
            assert observation.ancestry.complete, (
                f"{path} claimed authority on an incomplete chain "
                f"reaching only {observation.observed_prefix!r}"
            )
            assert observation.authority.reasons == ()
        else:
            assert observation.authority.reasons, (
                f"{path} refused authority without stating a reason"
            )

    assert authoritative_count == 0, (
        "no path in the frozen inventory has a complete chain to an authority "
        "boundary, so none of them may be authoritative"
    )


@pytest.mark.skipif(not FROZEN_INVENTORY.exists(), reason="frozen inventory not present")
def test_inventory_adapter_reports_how_far_each_capture_reached():
    """A partial chain must name its own limit, not imply full coverage."""
    inventory = json.loads(FROZEN_INVENTORY.read_text())
    view = view_from_inventory(inventory)
    user_dir = view.paths.get("/home/sage/.config/systemd/user")
    assert user_dir is not None, "the adapter must retain this path"
    # Its evidence reaches the kiosk-writable subtree root, which is enough to
    # refuse authority even though the chain stops there.
    assert user_dir.observed_prefix == "/home/sage"
    assert user_dir.ancestry_reaches_leaf is False
    assert user_dir.authority.reasons

    system_unit = view.paths.get("/etc/systemd/user")
    if system_unit is not None:
        # Root-owned leaf, but only the leaf was captured: no ancestry at all.
        assert system_unit.ancestry_reaches_leaf is True
        assert system_unit.ancestry.complete is False
        assert system_unit.authoritative is False
        assert any("incomplete" in r for r in system_unit.authority.reasons)


# --- REVISION 3: Shape A, separate mechanisms, and the loader contract ------
#
# Rook's source decision of 2 October 2026 selected Shape A and named its
# conditions, and recorded that transient units, runtime properties,
# generators, environment sources, reload/reexec and alternate launch remain
# separate unresolved mechanisms. Every test below is an attempt to have the
# analysis or the contract accept something those conditions do not support.


def test_rev3_closed_baseline_requires_a_validated_narrow_path():
    """Omitting the proposal must not make Shape A's gate disappear."""
    verdict = analyse(closed_view(unit_path_proposal=None))
    assert verdict.closed is False
    assert "unit_path_proposal" in verdict.missing_evidence


def test_rev3_closed_baseline_with_the_full_baseline_is_closed():
    """The positive direction: the stricter revision 3 baseline still closes."""
    assert analyse(closed_view()).closed is True


def test_rev3_trailing_colon_reintroduces_the_default_path_and_is_refused():
    """Upstream v259 appends the default search paths to a trailing colon."""
    validation = validate_unit_path_override(
        proposal(entries=("/etc/systemd/user", "")), closed_view().paths)
    assert validation.accepted is False
    assert UnitPathRefusal.TRAILING_COLON in validation.reasons

    verdict = analyse(closed_view(unit_path_proposal=proposal(
        entries=("/etc/systemd/user", ""))))
    assert verdict.closed is False
    assert AttackClass.UNIT_PATH_OVERRIDE in {f.attack_class for f in verdict.findings}
    detail = " ".join(f.detail for f in verdict.findings
                      if f.attack_class == AttackClass.UNIT_PATH_OVERRIDE)
    assert "default search paths" in detail


def test_rev3_leading_or_doubled_colon_is_refused():
    for entries in (("", "/etc/systemd/user"), ("/etc/systemd/user", "", "/usr/lib/systemd/user")):
        validation = validate_unit_path_override(proposal(entries=entries),
                                                 closed_view().paths)
        assert validation.accepted is False, entries
        assert UnitPathRefusal.EMPTY_ENTRY in validation.reasons, entries


def test_rev3_empty_path_is_refused():
    validation = validate_unit_path_override(proposal(entries=()),
                                             closed_view().paths)
    assert validation.accepted is False
    assert UnitPathRefusal.EMPTY_PATH in validation.reasons


def test_rev3_relative_entry_is_refused():
    validation = validate_unit_path_override(
        proposal(entries=("etc/systemd/user",)), closed_view().paths)
    assert validation.accepted is False
    assert UnitPathRefusal.RELATIVE_ENTRY in validation.reasons


def test_rev3_non_canonical_entry_is_refused():
    for entry in ("/etc/systemd/user/", "/etc//systemd/user", "/etc/../etc/systemd/user"):
        validation = validate_unit_path_override(proposal(entries=(entry,)),
                                                 closed_view().paths)
        assert validation.accepted is False, entry
        assert UnitPathRefusal.NON_CANONICAL_ENTRY in validation.reasons, entry


def test_rev3_duplicate_entry_is_refused():
    validation = validate_unit_path_override(
        proposal(entries=("/etc/systemd/user", "/etc/systemd/user")),
        closed_view().paths)
    assert validation.accepted is False
    assert UnitPathRefusal.DUPLICATE_ENTRY in validation.reasons


def test_rev3_uncaptured_entry_is_refused():
    """An entry nobody looked at is not a safe entry."""
    validation = validate_unit_path_override(
        proposal(entries=("/etc/systemd/user", "/opt/never-captured/systemd/user")),
        closed_view().paths)
    assert validation.accepted is False
    assert UnitPathRefusal.ENTRY_NOT_CAPTURED in validation.reasons


def test_rev3_writable_entry_is_refused_with_its_ancestor_named():
    """The loader must not be able to restore the writable path it removed."""
    user_dir = "/home/sage/.config/systemd/user"
    view = closed_view(paths={**closed_view().paths, user_dir: user_path(user_dir)})
    validation = validate_unit_path_override(
        proposal(entries=(user_dir, "/etc/systemd/user")), view.paths)
    assert validation.accepted is False
    assert UnitPathRefusal.ENTRY_NOT_AUTHORITATIVE in validation.reasons
    detail = " ".join(r.detail for r in validation.refusals
                      if r.reason == UnitPathRefusal.ENTRY_NOT_AUTHORITATIVE)
    assert "/home/sage" in detail, "non-authority must name the ancestor that decides it"


def test_rev3_asserted_establishment_is_not_evidence():
    """The establishing file's authority is derived, exactly like every path."""
    weak = user_path("/etc/systemd/system/user@1000.service.d/10-unit-path.conf")
    validation = validate_unit_path_override(
        proposal(establishing_file=weak), closed_view().paths)
    assert validation.accepted is False
    assert UnitPathRefusal.ESTABLISHING_FILE_NOT_AUTHORITATIVE in validation.reasons


def test_rev3_ordering_and_attribution_must_be_recorded_not_assumed():
    no_ordering = validate_unit_path_override(proposal(ordering_evidence=""),
                                              closed_view().paths)
    assert UnitPathRefusal.ORDERING_NOT_STATED in no_ordering.reasons
    no_environment = validate_unit_path_override(proposal(observer_environment=""),
                                                 closed_view().paths)
    assert UnitPathRefusal.ENVIRONMENT_NOT_ATTRIBUTED in no_environment.reasons


def test_rev3_validator_returns_reasons_and_never_a_fallback():
    """A refusal is a report, not a boolean a caller can ignore into a default."""
    observations = dict(closed_view().paths)
    snapshot = dict(observations)
    validation = validate_unit_path_override(proposal(), observations)
    assert validation.accepted is True
    assert validation.refusals == ()
    assert observations == snapshot, "validation must not mutate the capture"

    refused = validate_unit_path_override(proposal(entries=("/etc/systemd/user", "")),
                                          observations)
    assert refused.accepted is False
    assert refused.reasons, "a refusal must say why"
    assert all(r.detail for r in refused.refusals)


def test_rev3_unobserved_manager_mechanisms_block_closure():
    """Not looking is not the same as there being nothing to find."""
    view = closed_view(manager_mutations=ManagerMutationInventory(
        ObservationState.UNOBSERVED))
    verdict = analyse(view)
    assert verdict.closed is False
    assert AttackClass.MANAGER_MUTATION in {f.attack_class for f in verdict.findings}


def test_rev3_each_reachable_mechanism_is_its_own_finding():
    reachable = (ManagerMechanism.TRANSIENT_UNITS,
                 ManagerMechanism.MANAGER_RELOAD_REEXEC,
                 ManagerMechanism.ALTERNATE_LAUNCH)
    view = closed_view(manager_mutations=ManagerMutationInventory(
        ObservationState.OBSERVED_POPULATED, reachable=reachable))
    verdict = analyse(view)
    assert verdict.closed is False
    findings = [f for f in verdict.findings if f.attack_class == AttackClass.MANAGER_MUTATION]
    assert {f.path for f in findings} == {f"systemd/{m}" for m in reachable}
    assert all(f.blocking for f in findings)
    critical = {f.path for f in findings if f.severity == Severity.CRITICAL}
    assert "systemd/manager_reload_reexec" in critical
    assert "systemd/alternate_launch" in critical


def test_rev3_closing_the_environment_mechanism_alone_is_not_closure():
    """"An environment variable alone does not close them." """
    view = closed_view(manager_mutations=ManagerMutationInventory(
        ObservationState.OBSERVED_POPULATED,
        reachable=(ManagerMechanism.MANAGER_RELOAD_REEXEC,)))
    verdict = analyse(view)
    assert verdict.closed is False
    assert verdict.by_class(AttackClass.MANAGER_MUTATION)


def test_rev3_unknown_mechanism_is_refused_rather_than_scored_harmless():
    with pytest.raises(EvidenceError):
        ManagerMutationInventory(ObservationState.OBSERVED_POPULATED,
                                 reachable=("something_new",))
    with pytest.raises(EvidenceError):
        ManagerMutationInventory(ObservationState.UNOBSERVED,
                                 reachable=(ManagerMechanism.TRANSIENT_UNITS,))
    with pytest.raises(EvidenceError):
        ManagerMutationInventory(ObservationState.OBSERVED_EMPTY,
                                 reachable=(ManagerMechanism.GENERATORS,))
    with pytest.raises(EvidenceError):
        ManagerMutationInventory(ObservationState.OBSERVED_POPULATED, reachable=())


def test_rev3_fragment_only_unit_resolution_is_refused():
    """A fragment path is not a resolution."""
    view = closed_view(required_units=(fragment_only_unit(),))
    verdict = analyse(view)
    assert verdict.closed is False
    findings = verdict.by_class(AttackClass.UNIT_RESOLUTION)
    assert findings and all(f.blocking for f in findings)
    assert any("only the resolved fragment path was read" in f.detail for f in findings)


def test_rev3_writable_drop_in_is_critical_and_blocking():
    """A drop-in overrides ExecStart: a writable one is code execution."""
    drop_in = "/home/sage/.config/systemd/user/org.gnome.Shell@wayland.service.d/10-x.conf"
    view = closed_view(paths={**closed_view().paths, drop_in: user_path(drop_in)},
                       required_units=(root_unit(resolution=complete_resolution(
                           drop_ins=(user_path(drop_in),))),))
    verdict = analyse(view)
    assert verdict.closed is False
    findings = [f for f in verdict.by_class(AttackClass.UNIT_RESOLUTION)
                if f.path == drop_in]
    assert findings and findings[0].severity == Severity.CRITICAL
    assert "ExecStart" in findings[0].detail


def test_rev3_root_owned_drop_in_is_recorded_without_a_finding():
    drop_in = "/usr/lib/systemd/user/org.gnome.Shell@wayland.service.d/10-root.conf"
    view = closed_view(paths={**closed_view().paths, drop_in: root_path(drop_in)},
                       required_units=(root_unit(resolution=complete_resolution(
                           drop_ins=(root_path(drop_in),))),))
    verdict = analyse(view)
    assert verdict.closed is True
    assert [f for f in verdict.by_class(AttackClass.UNIT_RESOLUTION)
            if f.path == drop_in] == []


def test_rev3_alias_must_resolve_to_the_same_root_owned_fragment():
    view = closed_view(required_units=(root_unit(resolution=complete_resolution(
        aliases=("gnome-shell.service",))),))
    verdict = analyse(view)
    assert verdict.closed is False
    findings = verdict.by_class(AttackClass.UNIT_RESOLUTION)
    assert any("alias" in f.detail for f in findings)
    assert all(f.blocking for f in findings)


def test_rev3_unattributed_effective_environment_is_refused():
    view = closed_view(required_units=(root_unit(resolution=complete_resolution(
        effective_environment="")),))
    assert analyse(view).closed is False
    view = closed_view(required_units=(root_unit(resolution=complete_resolution(
        capture_method="")),))
    assert analyse(view).closed is False


def test_rev3_writable_asset_is_blocking():
    asset = "/home/sage/.local/share/sage-lantern/shell-overlay.js"
    view = closed_view(paths={**closed_view().paths, asset: user_path(asset)},
                       required_units=(root_unit(resolution=complete_resolution(
                           assets=(user_path(asset),))),))
    verdict = analyse(view)
    assert verdict.closed is False
    assert any(f.path == asset and f.blocking for f in verdict.by_class(AttackClass.UNIT_RESOLUTION))


def test_rev3_dependencies_are_recorded_at_info_and_do_not_block():
    view = closed_view(required_units=(root_unit(resolution=complete_resolution(
        dependencies=("dbus.socket", "basic.target"))),))
    verdict = analyse(view)
    assert verdict.closed is True
    info = [f for f in verdict.by_class(AttackClass.UNIT_RESOLUTION) if not f.blocking]
    assert info and "dbus.socket" in info[0].detail


def test_rev3_resolution_shape_is_enforced():
    with pytest.raises(EvidenceError):
        RequiredUnitEvidence(unit="x.service",
                             fragment=component("/usr/lib/systemd/user/x.service",
                                                kind=PathKind.REGULAR_FILE),
                             resolution="complete, honest")
    with pytest.raises(EvidenceError):
        UnitResolution(capture_method="m", effective_environment="e",
                       drop_ins=("/etc/x.conf",))
    with pytest.raises(EvidenceError):
        UnitPathProposal(entries=(1, 2), establishing_file=root_path("/etc/x.conf"),
                         ordering_evidence="o")
    # A bare string is iterable, so it would otherwise be read as one entry per
    # character — each of which is relative, uncaptured, or both.
    with pytest.raises(EvidenceError):
        UnitPathProposal(entries="/etc/systemd/user",
                         establishing_file=root_path("/etc/x.conf"),
                         ordering_evidence="o")
    with pytest.raises(EvidenceError):
        UnitPathProposal(entries=("/etc/systemd/user",), establishing_file="/etc/x.conf",
                         ordering_evidence="o")


# --- REVISION 3: the frozen-evidence consumer contract ---------------------


def view_digest(view: SystemView) -> str:
    """The contract's own canonical digest of the whole view.

    This was a declared stand-in -- "the contract only compares" -- hashing four
    fields rather than the view. That made the digest a statement about a
    different object than the one being analysed, which is the defect the
    independent review reproduced: an open view's digest could travel beside a
    closed view and both strings would agree. Delegating means the fixture can no
    longer assert a binding the contract would not itself compute.
    """
    return evidence_contract.canonical_view_digest(view)


def frozen_bundle(view: SystemView | None = None, **overrides) -> dict:
    """A bundle that satisfies the contract, so a test can open one hole in it.

    Every payload is the contract's canonical encoding of the value it carries,
    computed rather than written out by hand. The previous fixture declared
    opaque placeholders -- `b"frozen path observations"` for a `{"frozen": True}`
    value -- so the bytes proved nothing about the value beside them, and could
    not, because the two never had to agree.
    """
    view = view if view is not None else closed_view()
    proposal = view.unit_path_proposal
    values = {
        "contract": evidence_contract.CONTRACT_ID,
        "contract_version": evidence_contract.CONTRACT_VERSION,
        **evidence_contract.frozen_view_sections(view),
        "verified_unit_path": ":".join(proposal.entries) if proposal else "",
        "analysis_view_digest": view_digest(view),
    }
    payloads = {name: evidence_contract.canonical_section_bytes(value)
                for name, value in values.items()}
    bundle = dict(values)
    bundle["section_digests"] = {name: section_digest(payload)
                                 for name, payload in payloads.items()}
    bundle["section_payloads"] = payloads
    bundle.update(overrides)
    return bundle


def test_rev3_contract_document_is_version_pinned_and_carries_prohibitions():
    document = contract_document()
    assert document["contract"] == evidence_contract.CONTRACT_ID
    assert document["contract_version"] == evidence_contract.CONTRACT_VERSION
    assert document["digest_algorithm"] == "sha256"
    assert set(evidence_contract.REQUIRED_SECTIONS) == set(document["required_sections"])
    assert any("silently return to a writable" in p
               for p in document["loader_prohibitions"])
    assert any("transient units" in p for p in document["loader_prohibitions"])
    refusal_reasons = document["refusal_reasons"]
    assert "digest_mismatch" in refusal_reasons
    assert "analysis_not_closed" in refusal_reasons


def test_rev3_complete_bundle_verifies_and_admits():
    view = closed_view()
    bundle = frozen_bundle(view)
    verdict = verify_bundle(bundle)
    assert verdict.consumable is True, verdict.refusals
    assert verdict.refusals == ()
    assert set(verdict.digest_verified) >= {"paths", "required_units", "session_selection"}

    admission = admit(bundle, view=view, view_digest=view_digest(view))
    assert admission.consumable is True, admission.refusals
    assert admission.analysis_closed is True


def test_rev3_bundle_missing_a_section_is_refused():
    for section in ("manager_mutations", "unit_path_proposal", "analysis_view_digest"):
        bundle = frozen_bundle()
        bundle.pop(section)
        verdict = verify_bundle(bundle)
        assert verdict.consumable is False, section
        assert "missing_section" in verdict.reasons, section


def test_rev3_unstated_three_state_section_is_refused():
    bundle = frozen_bundle(manager_mutations={"reachable": []})
    verdict = verify_bundle(bundle)
    assert verdict.consumable is False
    assert "state_not_explicit" in verdict.reasons
    assert verdict.for_section("manager_mutations")


def test_rev3_digest_mismatch_is_refused_not_repaired():
    bundle = frozen_bundle()
    bundle["section_digests"] = {**bundle["section_digests"], "paths": "0" * 64}
    verdict = verify_bundle(bundle)
    assert verdict.consumable is False
    assert "digest_mismatch" in verdict.reasons
    assert "paths" not in verdict.digest_verified


def test_rev3_digest_without_a_payload_is_unverifiable_and_refused():
    bundle = frozen_bundle()
    bundle["section_payloads"] = {k: v for k, v in bundle["section_payloads"].items()
                                  if k != "required_units"}
    verdict = verify_bundle(bundle)
    assert verdict.consumable is False
    assert "payload_missing" in verdict.reasons


def test_rev3_payload_must_be_exact_bytes():
    bundle = frozen_bundle()
    bundle["section_payloads"] = {**bundle["section_payloads"], "paths": "frozen paths"}
    verdict = verify_bundle(bundle)
    assert verdict.consumable is False
    assert "payload_not_bytes" in verdict.reasons


def test_rev3_undigested_section_is_refused():
    bundle = frozen_bundle()
    bundle["section_digests"] = {k: v for k, v in bundle["section_digests"].items()
                                 if k != "session_selection"}
    verdict = verify_bundle(bundle)
    assert verdict.consumable is False
    assert "digest_missing" in verdict.reasons


def test_rev3_unknown_contract_and_version_are_refused():
    bundle = frozen_bundle(contract="sage.something.else")
    assert verify_bundle(bundle).consumable is False
    bundle = frozen_bundle(contract_version=evidence_contract.CONTRACT_VERSION + 1)
    verdict = verify_bundle(bundle)
    assert verdict.consumable is False
    assert "unsupported_version" in verdict.reasons


def test_rev3_admission_refuses_a_bundle_about_an_open_boundary():
    """A perfect bundle about an open boundary is still a refusal."""
    open_view = closed_view(manager_mutations=ManagerMutationInventory(
        ObservationState.UNOBSERVED))
    bundle = frozen_bundle(open_view)
    assert verify_bundle(bundle).consumable is True, "the bundle itself is sound"
    admission = admit(bundle, view=open_view, view_digest=view_digest(open_view))
    assert admission.consumable is False
    assert admission.analysis_closed is False
    assert "analysis_not_closed" in admission.reasons


def test_rev3_admission_refuses_a_view_that_is_not_the_frozen_one():
    """A re-read of the live system must not be admitted in place of the capture."""
    view = closed_view()
    bundle = frozen_bundle(view)
    other = closed_view(captured_at="2026-10-02T01:00:00Z")
    admission = admit(bundle, view=other, view_digest=view_digest(other))
    assert admission.consumable is False
    assert "view_digest_mismatch" in admission.reasons


def test_rev3_unknown_section_is_noticed_not_silently_ignored():
    verdict = verify_bundle(frozen_bundle(experimental_extra={"anything": True}))
    assert verdict.consumable is True
    assert any("experimental_extra" in notice for notice in verdict.notices)


def test_rev3_unknown_digest_entry_is_noticed():
    bundle = frozen_bundle()
    bundle["section_digests"] = {**bundle["section_digests"],
                                 "not_a_section": "a" * 64}
    verdict = verify_bundle(bundle)
    assert verdict.consumable is True
    assert any("not_a_section" in notice for notice in verdict.notices)


def test_rev3_admit_rejects_wrong_view_type():
    with pytest.raises(TypeError):
        admit(frozen_bundle(), view={"not": "a view"}, view_digest="x")  # type: ignore[arg-type]


def test_rev3_section_digest_rejects_non_bytes():
    with pytest.raises(EvidenceError):
        section_digest("text, not bytes")  # type: ignore[arg-type]


# --- Purity and type safety ------------------------------------------------


def test_analyse_rejects_wrong_type():
    with pytest.raises(TypeError):
        analyse({"not": "a view"})  # type: ignore[arg-type]


def test_view_from_inventory_rejects_wrong_type():
    with pytest.raises(TypeError):
        view_from_inventory(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_analyse_does_not_mutate_its_input():
    view = closed_view()
    snapshot = copy.deepcopy(view)
    analyse(view)
    assert view == snapshot


def test_analyse_is_deterministic():
    view = closed_view(session_selection=selection_evidence(greeter_independent=False))
    assert analyse(view) == analyse(view)


def test_closed_view_survives_extra_root_owned_paths():
    extra = {f"/usr/share/systemd/user/{i}": root_path(f"/usr/share/systemd/user/{i}")
             for i in range(5)}
    view = closed_view(paths={**closed_view().paths, **extra})
    assert analyse(view).closed is True


def test_blocking_classes_are_a_closed_set():
    for name in analysis.BLOCKING_CLASSES:
        assert isinstance(name, str) and name == name.lower()
        assert " " not in name


def test_generator_late_is_not_a_blocking_class():
    assert AttackClass.GENERATOR_LATE_INJECTION not in analysis.BLOCKING_CLASSES


def test_module_performs_no_io_and_has_no_side_effects():
    """Purity is what makes this reviewable offline. Guard it."""
    for filename in ("analysis.py", "evidence_contract.py"):
        source = (_CANDIDATE / filename).read_text()
        for forbidden in ("import socket", "import subprocess", "import os",
                          "open(", "os.system", "pathlib.Path("):
            assert forbidden not in source, (
                f"{filename} must stay pure: found {forbidden!r}"
            )


def test_evidence_contract_imports_only_hashlib_and_the_analysis():
    """The contract may hash caller-supplied bytes and nothing else."""
    import ast

    tree = ast.parse((_CANDIDATE / "evidence_contract.py").read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            if node.module is None:
                imported.extend(module + alias.name for alias in node.names)
            else:
                imported.append(module)
    # Enumerated rather than bounded, so a new import has to be added here
    # deliberately. `enum` and `json` joined the list for the exact-schema
    # correction: `enum` reads the three states the analysis already defines and
    # `json` is how a canonical encoding is stated. `collections.abc` and `types`
    # inspect container and union kinds for the strict decoding version 3 binds
    # sections with; no defaulted fields or primitive coercions are accepted. All
    # of them are pure -- no filesystem, socket, subprocess or device surface --
    # which is the property this test exists to protect, and why `hashlib` was the
    # only one before.
    assert sorted(imported) == [".analysis", "__future__", "collections.abc", "dataclasses",
                                "enum", "hashlib", "json", "types", "typing"], imported


# --- Full-section schema and view binding (contract v3, writer-owned) -------
#
# Rook's revision-4 review held source acceptance on two families of attack that
# version 2 admitted: sections that were re-encoded and re-hashed *correctly* while
# their contents contradicted the view they travelled with, and required evidence
# replaced with a correctly hashed primitive. Version 3 refuses both in `admit`.
# The tests below pin that refusal per field, and they are written so that dropping
# the binding -- or loosening the decoder back to coercion -- fails here rather
# than in a review.


def rebind(bundle: dict, section: str, value) -> dict:
    """Carry `value` for `section` with a payload and digest that match it.

    This is what makes the attacks below well-formed rather than malformed: the
    bytes really are the contract's canonical encoding of the value, and the digest
    really is their sha256. Nothing is stale, mis-typed as bytes or mismatched, so
    only the *contents* can be what a refusal is about.
    """
    payload = evidence_contract.canonical_section_bytes(value)
    return {
        **bundle,
        section: value,
        "section_payloads": {**bundle["section_payloads"], section: payload},
        "section_digests": {**bundle["section_digests"], section: section_digest(payload)},
    }


def contradictory_sections() -> dict:
    """One schema-valid, content-different value for every view field.

    Each mutation keeps the shape the decoder accepts and changes something the
    capture is supposed to have bound: a timestamp, a provenance line, an extra
    search-path entry, an ancestry component's owner, an inventory state, the unit
    a resolution names, the selection's own flags, the proposal's environment.
    """
    sections = evidence_contract.frozen_view_sections(closed_view())
    paths = copy.deepcopy(sections["paths"])
    paths[sorted(paths)[0]]["ancestry"]["components"][-1]["prescribed_uid"] = KAS
    return {
        "captured_at": "2026-01-01T00:00:00Z",
        "capture_provenance": "a different provenance statement",
        "unit_search_path": [*sections["unit_search_path"], "/etc/xdg/systemd/user"],
        "paths": paths,
        "extension_inventory": {**sections["extension_inventory"], "state": "unobserved"},
        "dbus_inventory": {**sections["dbus_inventory"], "state": "unobserved"},
        "manager_mutations": {**sections["manager_mutations"], "state": "unobserved"},
        "required_units": [{**sections["required_units"][0], "unit": "org.gnome.Shell@x11.service"}],
        "session_selection": {**sections["session_selection"], "root_controlled": False},
        "unit_path_proposal": {**sections["unit_path_proposal"],
                               "observer_environment": "a different observer environment"},
    }


def test_rev3_every_view_field_is_bound_to_the_view_it_travels_with():
    """A re-encoded, re-hashed section that contradicts the view is refused.

    The bundle here is *eligible*: every byte and digest is correct, so a version
    that only checked bytes would call it consumable. Admission is where the
    contradiction has to be caught, for every field, including the ones an
    envelope check never looked inside -- ancestry, inventories, resolutions,
    selection and the proposal.
    """
    view = closed_view()
    mutations = contradictory_sections()
    assert set(mutations) == set(evidence_contract.frozen_view_sections(view))

    for section, value in mutations.items():
        bundle = rebind(frozen_bundle(view), section, value)
        eligible = verify_bundle(bundle)
        assert eligible.consumable is True, (section, eligible.refusals)

        admission = admit(bundle, view=view, view_digest=view_digest(view))
        assert admission.consumable is False, section
        assert "section_not_bound" in admission.reasons, (section, admission.reasons)
        assert admission.for_section(section), (section, admission.refusals)


def test_rev3_sections_must_decode_to_the_exact_typed_schema():
    """A correctly hashed primitive is not evidence for any field.

    `False` encodes canonically and digests correctly, which is exactly the shape
    version 2 admitted. Whether the refusal comes from the section's own
    three-state check or from the schema decode, no field may accept it.
    """
    view = closed_view()
    for section in evidence_contract.frozen_view_sections(view):
        verdict = verify_bundle(rebind(frozen_bundle(view), section, False))
        assert verdict.consumable is False, section
        assert (verdict.for_section(section)
                or "invalid_section_schema" in verdict.reasons), (section, verdict.refusals)


def _first_entry(mapping):
    """The first observation of a section that maps a path to what was observed."""
    return mapping[sorted(mapping)[0]]


def _drop_ancestry(value):
    _first_entry(value).pop("ancestry")
    return value


def _add_unknown_field(value):
    _first_entry(value)["component_of_record"] = "not a field of this dataclass"
    return value


def _bool_for_int(value):
    _first_entry(value)["ancestry"]["components"][-1]["prescribed_uid"] = True
    return value


def _int_for_str(value):
    _first_entry(value)["path"] = 7
    return value


@pytest.mark.parametrize("label,section,mutate", [
    ("a nested required field is missing", "paths", _drop_ancestry),
    ("an unknown nested field is present", "paths", _add_unknown_field),
    ("a boolean stands in for an integer", "paths", _bool_for_int),
    ("an integer stands in for a string", "paths", _int_for_str),
    ("an integer stands in for the unit name", "required_units",
     lambda value: [{**value[0], "unit": 5}]),
])
def test_rev3_nested_shapes_are_exact(label, section, mutate):
    """The decoder accepts this analysis module's exact dataclass schema or nothing.

    Missing fields, extra fields and primitive coercions are the three ways a
    permissive decoder hands authority to evidence that never carried it: a
    boolean is not an integer mode however truthy, and a default-filled field is a
    value nobody captured.
    """
    view = closed_view()
    sections = evidence_contract.frozen_view_sections(view)
    bundle = rebind(frozen_bundle(view), section, mutate(copy.deepcopy(sections[section])))
    verdict = verify_bundle(bundle)
    assert verdict.consumable is False, label
    assert "invalid_section_schema" in verdict.reasons, (label, verdict.refusals)


def test_rev3_a_value_with_no_exact_encoding_is_refused_for_its_own_section():
    """An unencodable value is a refusal, not an exception out of verification.

    The payload and digest here are the untouched originals, so the refusal can
    only come from the value itself. It must name the section that carries it, and
    the decode step must not run at all once anything has been refused.
    """
    bundle = frozen_bundle(closed_view())
    bundle["unit_search_path"] = [f"/p{index}" for index in range(4097)]
    verdict = verify_bundle(bundle)
    assert verdict.consumable is False
    assert "invalid_section_schema" in verdict.reasons
    assert verdict.for_section("unit_search_path")
    assert not verdict.for_section("analysis_view")


def test_rev3_the_admitted_path_comes_from_the_decoded_proposal():
    """A path is admitted only when the validated proposal establishes it.

    Both values below are correctly encoded and correctly digested, so the refusal
    is about the proposal rather than about the envelope: a path that the proposal
    does not establish is a contradiction, not evidence.
    """
    view = closed_view()
    unsupported = (
        ":".join(reversed(view.unit_path_proposal.entries)),
        "/home/kiosk/.local/share/systemd/user",
    )
    for path in unsupported:
        bundle = rebind(frozen_bundle(view), "verified_unit_path", path)
        assert verify_bundle(bundle).consumable is True, path
        admission = admit(bundle, view=view, view_digest=view_digest(view))
        assert admission.consumable is False, path
        assert "path_not_derived_from_proposal" in admission.reasons, (path, admission.reasons)


def test_rev3_a_refused_bundle_is_never_decoded_or_analysed():
    """Refusal is final: no decoded view, no closure verdict, no caller fallback.

    A loader that decoded anyway would be building an analysis view out of
    sections it had just refused, and one that fell back to a caller-supplied
    summary would let the caller decide the answer. Neither happens: the refusal
    comes back without an analysis, and `analysis_closed` stays False.
    """
    view = closed_view()
    bundle = rebind(frozen_bundle(view), "paths", False)
    admission = admit(bundle, view=view, view_digest=view_digest(view))
    assert admission.consumable is False
    assert "invalid_section_schema" in admission.reasons
    assert admission.analysis_closed is False
    assert "analysis_not_closed" not in admission.reasons


def test_rev3_view_decoding_refuses_shapes_a_verified_bundle_cannot_carry():
    """The decoder is strict on its own, not only behind `verify_bundle`.

    `view_from_verified_sections` is documented as post-verification, and this is
    the property that makes the ordering matter: handed a section shape no capture
    could have produced, it raises rather than defaulting a field.
    """
    view = closed_view()
    for section, value in (("paths", False), ("captured_at", False), ("unit_search_path", [5])):
        bundle = rebind(frozen_bundle(view), section, value)
        with pytest.raises(EvidenceError):
            evidence_contract.view_from_verified_sections(bundle)
