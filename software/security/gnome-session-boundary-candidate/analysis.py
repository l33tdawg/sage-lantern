"""GNOME session launch and code-loading boundary — bounded analysis candidate.

**DISABLED CANDIDATE.** This module performs no I/O, opens no socket, spawns no
process, loads no BPF, and mutates nothing. It is a pure decision function plus a
parser for already-captured, read-only inventory data.

It answers one question:

    Given this observed system view, is the GNOME session launch and
    code-loading boundary CLOSED?

and it answers it with a list of concrete, individually-testable findings rather
than a boolean taken on faith. Closure is only ever reported when every required
path is *derived* root-authoritative, every required surface is *observed*, and
no required unit resolves to a mask.

Why this shape: the frozen predecessor review
(`docs/lantern/DESKTOP_LAUNCH_CLOSURE_REVIEW.md`) found that the user manager's
lookup path includes user-writable locations, and stated that "a missing leaf or
a root-owned leaf under a replaceable user parent does not establish closure."
This module turns that sentence into an executable predicate.

Review revision, 2 October 2026 (Rook, msg-e22b98eb). Five defects in the first
revision, all named in `REMAINING_GAPS.md` and none of them closed by it:

1. `authoritative` was a **caller-supplied boolean**. The first revision trusted
   the caller's assertion and merely cross-checked it against the caller's own
   other booleans. Authority is now **derived** by
   `derive_authority()` from complete, typed, canonical ancestor evidence, and
   incomplete or replaceable ancestry is **refused** rather than scored.
2. Absent extension and D-Bus inventories **read as acceptable**, because the
   checks iterated whatever the caller supplied. Omission and an observed-empty
   inventory were indistinguishable. Every required surface now carries an
   explicit three-state `ObservationState`: `UNOBSERVED` (blocks),
   `OBSERVED_EMPTY` (permits), `OBSERVED_POPULATED` (scored).
3. Mask detection was a **substring match on free-text notes**. It is now a typed
   check of the two conditions systemd actually defines — `st_size == 0` for a
   regular file, or a symlink whose target is `/dev/null` — plus resolved
   required-unit evidence, replacing an assumption with a measurement.
4. Evidence used **truthy flags**. `"false"` is a truthy string, `True` is an
   `int`, and `nan` is a float. Shape is now enforced exactly: bool is bool, int
   excludes bool, path strings must be canonical and absolute, and counts are
   finite and non-negative.
5. Unit-path precedence was **hardcoded**, so `generator.late` was flagged as
   equivalently dangerous as `user.control` even though it sorts *after* every
   root-owned entry. Precedence is now computed from the canonical table, and a
   missing root-owned entry is refused rather than ignored.

Upstream basis, reviewed 2 October 2026 against systemd.unit(5) as published for
systemd 262 (C runs `259.5-0ubuntu3.4`; see the residual-uncertainty note in the
companion matrix — the table below is upstream, not distribution-attested):

  - "User Unit Search Path". The user manager resolves a unit name by taking the
    **first** match in an ordered path; earlier entries win outright.
  - Masking is defined in "Unit File Load Path": a unit file that "is empty (i.e.
    has the file size 0) or is symlinked to /dev/null" has "a load state of
    masked, and cannot be activated".
  - The unit load path "may be augmented or changed using various environment
    variables", which "may in turn be set using environment generators".
  - `generator.late` is the **final** entry, after `/usr/lib/systemd/user`.
    A unit placed there cannot shadow a root unit; it can only supply a name that
    no root-owned directory provides.

    https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html

Scope discipline: this candidate does not claim to enforce anything. It is the
analysis half. Enforcement requires kernel load and a root loader, which are
explicitly reserved to Rook and prohibited to this work.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence

# --- Attack taxonomy -------------------------------------------------------


class AttackClass(str):
    """Stable identifiers for the code-loading and launch attack classes."""

    UNIT_SHADOWING = "unit_shadowing"
    TRANSIENT_INJECTION = "transient_injection"
    GENERATOR_INJECTION = "generator_injection"
    GENERATOR_LATE_INJECTION = "generator_late_injection"
    MASK_DENIAL = "mask_denial"
    REQUIRED_UNIT_MASKED = "required_unit_masked"
    MANAGER_ENVIRONMENT = "manager_environment"
    EXTENSION_CODE = "extension_code"
    SHELL_DBUS_EXEC = "shell_dbus_exec"
    AUTOSTART_INJECTION = "autostart_injection"
    SEARCH_PATH_REDIRECT = "search_path_redirect"
    GREETER_OR_SSH_BLAST_RADIUS = "greeter_or_ssh_blast_radius"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    INCONSISTENT_EVIDENCE = "inconsistent_evidence"
    # Revision 3, following Rook's source decision of 2 October 2026.
    UNIT_PATH_OVERRIDE = "unit_path_override"
    MANAGER_MUTATION = "manager_mutation"
    UNIT_RESOLUTION = "unit_resolution"


#: Classes that must be closed before the boundary can be called closed.
#: `GENERATOR_LATE_INJECTION` is deliberately **absent**: a unit under
#: `generator.late` sorts after every root-owned directory, so it can only add a
#: name that nothing else provides. It is reported at INFO and does not block.
BLOCKING_CLASSES = (
    AttackClass.UNIT_SHADOWING,
    AttackClass.TRANSIENT_INJECTION,
    AttackClass.GENERATOR_INJECTION,
    AttackClass.MASK_DENIAL,
    AttackClass.REQUIRED_UNIT_MASKED,
    AttackClass.MANAGER_ENVIRONMENT,
    AttackClass.EXTENSION_CODE,
    AttackClass.SHELL_DBUS_EXEC,
    AttackClass.AUTOSTART_INJECTION,
    AttackClass.SEARCH_PATH_REDIRECT,
    AttackClass.GREETER_OR_SSH_BLAST_RADIUS,
    AttackClass.INCOMPLETE_EVIDENCE,
    AttackClass.INCONSISTENT_EVIDENCE,
    AttackClass.UNIT_PATH_OVERRIDE,
    AttackClass.MANAGER_MUTATION,
    AttackClass.UNIT_RESOLUTION,
)


class Severity(str):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


class ObservationState(Enum):
    """Three states, because omission and observed-empty are not the same thing.

    The first revision scored only what the caller supplied, so a missing
    extension or D-Bus inventory was silently acceptable. That asymmetry was the
    weakest point in the candidate. Now a required surface that was never
    observed cannot be scored as passing.
    """

    UNOBSERVED = "unobserved"          # never captured -> blocks closure
    OBSERVED_EMPTY = "observed_empty"  # captured, and genuinely empty -> permits
    OBSERVED_POPULATED = "observed_populated"  # captured, with entries -> scored


class PathKind(Enum):
    """What the path component actually is, as captured."""

    DIRECTORY = "directory"
    REGULAR_FILE = "regular_file"
    SYMLINK = "symlink"
    ABSENT = "absent"


class Tri(Enum):
    """A three-valued predicate. `UNKNOWN` is not `NO`: unknown is refused."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class EvidenceError(ValueError):
    """Evidence is not the exact shape the analyser requires.

    Raised rather than coerced. A truthy string is not a boolean and a missing
    ancestor is not an absent one; accepting either would produce a verdict the
    evidence does not support.
    """


# --- Strict shape enforcement ---------------------------------------------


def _require_exact_bool(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise EvidenceError(
            f"{what} must be exactly bool, got {type(value).__name__} ({value!r})"
        )
    return value


def _require_exact_int(value: object, what: str, *, minimum: int | None = None,
                       maximum: int | None = None) -> int:
    # bool is a subclass of int, so `True` would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceError(
            f"{what} must be exactly int, got {type(value).__name__} ({value!r})"
        )
    if minimum is not None and value < minimum:
        raise EvidenceError(f"{what} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise EvidenceError(f"{what} must be <= {maximum}, got {value}")
    return value


def _require_enum(value: object, enum_type: type[Enum], what: str) -> Enum:
    if not isinstance(value, enum_type):
        raise EvidenceError(
            f"{what} must be a {enum_type.__name__}, got {type(value).__name__}"
        )
    return value


def _require_text(value: object, what: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{what} must be a str, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise EvidenceError(f"{what} must not be empty or whitespace-only")
    return value


def _require_canonical_path(value: object, what: str) -> str:
    """A path the analyser is willing to reason about.

    Absolute, with no `//`, no `.` or `..` component and no trailing slash. The
    first revision accepted `~/.config/systemd/user.control` as a literal string
    and then reasoned about it as though it were a real location; every "not
    authoritative" conclusion it drew there was unfalsifiable, because the string
    names no file. A canonical absolute path either matches what was captured or
    is refused.
    """
    text = _require_text(value, what)
    if not text.startswith("/"):
        raise EvidenceError(
            f"{what} must be an absolute canonical path, got {text!r}; a relative "
            f"or shell-tilde path names no capturable location"
        )
    if text != "/" and text.endswith("/"):
        raise EvidenceError(f"{what} must not have a trailing slash: {text!r}")
    if "//" in text:
        raise EvidenceError(f"{what} must not contain an empty component: {text!r}")
    parts = [p for p in text.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise EvidenceError(f"{what} must not contain '.' or '..': {text!r}")
    return text


def canonicalise(path: str) -> str:
    """Public canonicaliser. Raises `EvidenceError` on a non-canonical path."""
    return _require_canonical_path(path, "path")


def _require_finite_float(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(f"{what} must be a finite number, got {type(value).__name__}")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise EvidenceError(f"{what} must be finite, got {value!r}")
    return number


# --- Observation model -----------------------------------------------------

#: A path chain may stop early at a location that is, by construction, the top of
#: a user-controlled subtree. Nothing above it needs to be walked, because the
#: subtree is already user-writable and that is what makes the leaf unsafe.
#: Every other path must be walked to the filesystem root.
_TERMINATOR_PATTERNS = (
    re.compile(r"^/home/[^/]+$"),
    re.compile(r"^/run/user/[^/]+$"),
)


def _is_chain_terminator(path: str) -> bool:
    return any(pattern.match(path) for pattern in _TERMINATOR_PATTERNS)


def _is_authority_boundary(path: str) -> bool:
    """The topmost component a chain is required to reach.

    Either the filesystem root, or a user-controlled subtree root such as
    `/home/<user>` — because everything beneath such a directory is
    user-writable, which is exactly what makes a leaf under it unsafe.
    """
    return path == "/" or _is_chain_terminator(path)


def _ancestor_chain(path: str) -> tuple[str, ...]:
    """Every component from the filesystem root down to `path`, inclusive.

    The filesystem root is always the first element, including for `path == "/"`,
    so that a chain rooted at `/` has a component to name as its origin.
    """
    parts = [p for p in path.split("/") if p]
    return ("/",) + tuple("/" + "/".join(parts[: i + 1]) for i in range(len(parts)))


@dataclass(frozen=True)
class PathComponent:
    """One captured path component, with the fields needed to judge it.

    Everything here is evidence. `writable_by_kiosk` and `acl_writable_by_kiosk`
    are `Tri` rather than `bool` because a capture that did not look must not be
    indistinguishable from a capture that looked and found nothing.
    """

    path: str
    prescribed_uid: int
    prescribed_gid: int
    mode: int
    kind: PathKind
    symlink_target: str | None
    writable_by_kiosk: Tri
    acl_writable_by_kiosk: Tri = Tri.UNKNOWN
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        _require_canonical_path(self.path, "PathComponent.path")
        _require_exact_int(self.prescribed_uid, f"{self.path}: uid", minimum=0, maximum=2**32 - 1)
        _require_exact_int(self.prescribed_gid, f"{self.path}: gid", minimum=0, maximum=2**32 - 1)
        # Permission bits only. 0o7777 keeps setuid/setgid/sticky observable.
        _require_exact_int(self.mode, f"{self.path}: mode", minimum=0, maximum=0o7777)
        _require_enum(self.kind, PathKind, f"{self.path}: kind")
        _require_enum(self.writable_by_kiosk, Tri, f"{self.path}: writable_by_kiosk")
        _require_enum(self.acl_writable_by_kiosk, Tri,
                      f"{self.path}: acl_writable_by_kiosk")

        if self.kind is PathKind.SYMLINK:
            if self.symlink_target is None:
                raise EvidenceError(f"{self.path}: a symlink must record its target")
            _require_text(self.symlink_target, f"{self.path}: symlink_target")
        elif self.symlink_target is not None:
            raise EvidenceError(
                f"{self.path}: symlink_target is only meaningful for kind=SYMLINK"
            )

        if self.size_bytes is not None:
            _require_exact_int(self.size_bytes, f"{self.path}: size_bytes", minimum=0)

        if self.kind is PathKind.ABSENT:
            if self.writable_by_kiosk is not Tri.NO or self.acl_writable_by_kiosk is not Tri.NO:
                raise EvidenceError(
                    f"{self.path}: an absent component is not writable; refusing a "
                    f"writability claim about something that is not there"
                )

    @property
    def is_root_owned(self) -> bool:
        return self.prescribed_uid == 0

    @property
    def is_absent(self) -> bool:
        return self.kind is PathKind.ABSENT

    def is_masking_file(self) -> bool:
        """Exactly the two conditions systemd defines as a mask.

        systemd.unit(5): a unit file that "is empty (i.e. has the file size 0) or
        is symlinked to /dev/null" appears as `masked` and cannot be activated.
        """
        if self.kind is PathKind.SYMLINK:
            return self.symlink_target == "/dev/null"
        if self.kind is PathKind.REGULAR_FILE:
            return self.size_bytes == 0
        return False


@dataclass(frozen=True)
class AncestryEvidence:
    """The ordered component chain for one path, root first, leaf last.

    `complete` is computed, not asserted. A chain is complete when it either
    reaches the filesystem root or stops at a user-controlled-subtree terminator,
    and each recorded component is the exact ancestor of the next. There is no
    way for a caller to declare a chain complete.
    """

    components: tuple[PathComponent, ...]

    def __post_init__(self) -> None:
        if isinstance(self.components, (str, bytes)) or not isinstance(self.components, Iterable):
            raise EvidenceError("components must be an iterable of PathComponent")
        for component in self.components:
            if not isinstance(component, PathComponent):
                raise EvidenceError("components must contain only PathComponent values")

    @property
    def leaf(self) -> PathComponent | None:
        return self.components[-1] if self.components else None

    @property
    def path(self) -> str:
        leaf = self.leaf
        return leaf.path if leaf is not None else ""

    @property
    def complete(self) -> bool:
        """A chain that actually reaches an authority boundary."""
        if not self.components:
            return False
        for index, component in enumerate(self.components):
            expected = _ancestor_chain(component.path)
            if index == 0:
                # The first component must be the boundary itself; anything else
                # means the capture started somewhere arbitrary.
                if not _is_authority_boundary(component.path):
                    return False
                continue
            previous = self.components[index - 1]
            if component.path not in expected or previous.path not in expected:
                return False
            if expected.index(component.path) != expected.index(previous.path) + 1:
                return False
        # The final component must be the boundary, or the chain must be rooted
        # at the real filesystem root.
        return _is_authority_boundary(self.components[-1].path) or self.components[0].path == "/"

    @property
    def is_symlinked(self) -> bool:
        return any(c.kind is PathKind.SYMLINK for c in self.components)

    def first_non_root_owned(self) -> PathComponent | None:
        for component in self.components:
            if not component.is_root_owned:
                return component
        return None

    def reasons(self) -> tuple[str, ...]:
        """Why this ancestry does not establish authority. Empty means it does."""
        reasons: list[str] = []
        if not self.components:
            return ("no ancestor evidence captured",)
        if not self.complete:
            return ("ancestor chain is incomplete or non-contiguous",)
        for component in self.components:
            if component.is_absent:
                reasons.append(f"{component.path} does not exist")
            if component.writable_by_kiosk is Tri.UNKNOWN:
                reasons.append(f"{component.path} kiosk-writability was not observed")
            elif component.writable_by_kiosk is Tri.YES:
                reasons.append(f"{component.path} is writable by the desktop user")
            if component.acl_writable_by_kiosk is Tri.UNKNOWN:
                reasons.append(f"{component.path} ACL state was not observed")
            elif component.acl_writable_by_kiosk is Tri.YES:
                reasons.append(f"{component.path} is ACL-writable by the desktop user")
            if not component.is_root_owned and not component.is_absent:
                reasons.append(
                    f"{component.path} is owned by uid {component.prescribed_uid}, not root"
                )
        if self.is_symlinked:
            targets = ", ".join(
                f"{c.path} -> {c.symlink_target}"
                for c in self.components if c.kind is PathKind.SYMLINK
            )
            reasons.append(f"chain contains a symlink, which can be repointed: {targets}")
        return tuple(reasons)


class AuthoritySource(Enum):
    DERIVED = "derived"
    INCONSISTENT = "inconsistent"


@dataclass(frozen=True)
class PathAuthority:
    """The derived verdict for one path. Never a caller's assertion."""

    authoritative: bool
    source: AuthoritySource
    reasons: tuple[str, ...]
    ancestry: AncestryEvidence

    @property
    def inconsistent(self) -> bool:
        return self.source is AuthoritySource.INCONSISTENT


def derive_authority(
    ancestry: AncestryEvidence,
    *,
    claimed_present: bool | None = None,
    claimed_writable_by_user: bool | None = None,
    claimed_owned_by_root: bool | None = None,
) -> PathAuthority:
    """Derive authority from ancestry. The caller may not declare it.

    A path is authoritative only when its recorded ancestry is complete and
    contiguous, every component exists, every component is root-owned, and no
    component is writable (by mode or by ACL) by the desktop user, and no
    component is a symlink.

    Any caller-supplied claim that contradicts the derivation does not override
    it; it marks the record **inconsistent**, which is itself a finding. The
    first revision's `authoritative` field was exactly such a claim, trusted and
    only cross-checked against other claims from the same source.
    """
    if not isinstance(ancestry, AncestryEvidence):
        raise EvidenceError("ancestry must be an AncestryEvidence")

    reasons = ancestry.reasons()
    derived = not reasons

    inconsistent = False
    leaf = ancestry.leaf
    if leaf is not None:
        if claimed_present is not None:
            _require_exact_bool(claimed_present, "claimed_present")
            if claimed_present != (leaf.kind is not PathKind.ABSENT):
                inconsistent = True
        if claimed_writable_by_user is not None:
            _require_exact_bool(claimed_writable_by_user, "claimed_writable_by_user")
            if claimed_writable_by_user != (leaf.writable_by_kiosk is Tri.YES):
                inconsistent = True
        if claimed_owned_by_root is not None:
            _require_exact_bool(claimed_owned_by_root, "claimed_owned_by_root")
            if claimed_owned_by_root != leaf.is_root_owned:
                inconsistent = True

    if inconsistent:
        reasons = reasons + (
            "a caller-supplied claim contradicts the captured ancestry",
        )
        return PathAuthority(
            authoritative=False,
            source=AuthoritySource.INCONSISTENT,
            reasons=reasons,
            ancestry=ancestry,
        )

    return PathAuthority(
        authoritative=derived,
        source=AuthoritySource.DERIVED,
        reasons=reasons,
        ancestry=ancestry,
    )


@dataclass(frozen=True)
class PathObservation:
    """One filesystem location relevant to the boundary.

    Authority is not a field. It is derived in `__post_init__` from `ancestry`,
    and `authoritative` is exposed as a read-only property so existing callers
    keep working while no caller can assert it.

    The ancestry may be **partial**: a capture that recorded a leaf and its
    immediate parent but not the path up to the root has still recorded something
    true. `observed_prefix` names how far the evidence reaches, and a partial
    chain is reported as incomplete evidence rather than being silently dropped
    or silently treated as authoritative.
    """

    path: str
    ancestry: AncestryEvidence
    claimed_present: bool | None = None
    claimed_writable_by_user: bool | None = None
    claimed_owned_by_root: bool | None = None

    def __post_init__(self) -> None:
        _require_canonical_path(self.path, "PathObservation.path")
        if not isinstance(self.ancestry, AncestryEvidence):
            raise EvidenceError("PathObservation.ancestry must be an AncestryEvidence")
        if not self.ancestry.components:
            raise EvidenceError(
                f"{self.path}: an observation with no captured components is not "
                f"evidence of anything"
            )
        # The recorded components must be a contiguous run of `path`'s real
        # ancestry. A capture may stop early — the run need not start at `/` —
        # but it may not skip a component or describe a different path, because
        # then the chain would not be evidence about this path at all.
        expected = _ancestor_chain(self.path)
        recorded = tuple(c.path for c in self.ancestry.components)
        if recorded[-1] not in expected:
            raise EvidenceError(
                f"PathObservation.path {self.path!r} has no component {recorded[-1]!r}"
            )
        end = expected.index(recorded[-1])
        start = end - len(recorded) + 1
        if start < 0 or expected[start: end + 1] != recorded:
            raise EvidenceError(
                f"PathObservation.path {self.path!r} does not have the contiguous "
                f"ancestry {recorded!r}; a capture with a gap in the middle is not "
                f"a shorter capture, it is a disconnected one"
            )
        for name, value in (("claimed_present", self.claimed_present),
                            ("claimed_writable_by_user", self.claimed_writable_by_user),
                            ("claimed_owned_by_root", self.claimed_owned_by_root)):
            if value is not None:
                _require_exact_bool(value, name)

    @property
    def authority(self) -> PathAuthority:
        return derive_authority(
            self.ancestry,
            claimed_present=self.claimed_present,
            claimed_writable_by_user=self.claimed_writable_by_user,
            claimed_owned_by_root=self.claimed_owned_by_root,
        )

    @property
    def authoritative(self) -> bool:
        """Derived. Present so readers of the first revision keep working."""
        return self.authority.authoritative

    @property
    def inconsistent(self) -> bool:
        return self.authority.inconsistent

    @property
    def present(self) -> bool:
        """Whether the leaf itself was observed to exist.

        A partial chain that does not reach the leaf cannot say, so this is
        `False` for the leaf and `observed_prefix` carries the honest answer.
        """
        leaf = self.ancestry.leaf
        return leaf is not None and leaf.path == self.path and leaf.kind is not PathKind.ABSENT

    @property
    def observed_prefix(self) -> str:
        """The deepest component the capture actually reached."""
        leaf = self.ancestry.leaf
        return leaf.path if leaf is not None else ""

    @property
    def ancestry_reaches_leaf(self) -> bool:
        return self.observed_prefix == self.path

    @property
    def owned_by_root(self) -> bool:
        leaf = self.ancestry.leaf
        return leaf is not None and leaf.is_root_owned

    @property
    def writable_by_user(self) -> bool:
        leaf = self.ancestry.leaf
        return leaf is not None and leaf.writable_by_kiosk is Tri.YES


@dataclass(frozen=True)
class DirectoryInventory:
    """A set of directories that was either observed or was not."""

    state: ObservationState
    entries: tuple[PathObservation, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        _require_enum(self.state, ObservationState, "DirectoryInventory.state")
        if isinstance(self.entries, (str, bytes)) or not isinstance(self.entries, Iterable):
            raise EvidenceError("DirectoryInventory.entries must be an iterable")
        for entry in self.entries:
            if not isinstance(entry, PathObservation):
                raise EvidenceError("DirectoryInventory.entries must be PathObservation")
        if self.state is ObservationState.UNOBSERVED and tuple(self.entries):
            raise EvidenceError(
                "an UNOBSERVED inventory cannot carry entries; that is a contradiction"
            )
        if self.state is ObservationState.OBSERVED_EMPTY and tuple(self.entries):
            raise EvidenceError(
                "an OBSERVED_EMPTY inventory cannot carry entries; use "
                "OBSERVED_POPULATED, or the emptiness assertion is a lie"
            )
        if self.state is ObservationState.OBSERVED_POPULATED and not tuple(self.entries):
            raise EvidenceError(
                "an OBSERVED_POPULATED inventory must carry entries; use "
                "OBSERVED_EMPTY to assert you looked and found none"
            )


@dataclass(frozen=True)
class DbusSurface:
    """A D-Bus method that can cause code to run or session state to change."""

    name: str
    interface: str
    reachable_by_user: bool
    authenticated: bool
    permits_code_execution: bool
    note: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "DbusSurface.name")
        _require_text(self.interface, "DbusSurface.interface")
        _require_exact_bool(self.reachable_by_user, f"{self.name}: reachable_by_user")
        _require_exact_bool(self.authenticated, f"{self.name}: authenticated")
        _require_exact_bool(self.permits_code_execution,
                            f"{self.name}: permits_code_execution")
        _require_text(self.note, f"{self.name}: note", allow_empty=True)


@dataclass(frozen=True)
class DbusInventory:
    """The reachable `org.gnome.Shell.*` surface list, observed or not."""

    state: ObservationState
    surfaces: tuple[DbusSurface, ...] = ()

    def __post_init__(self) -> None:
        _require_enum(self.state, ObservationState, "DbusInventory.state")
        if isinstance(self.surfaces, (str, bytes)) or not isinstance(self.surfaces, Iterable):
            raise EvidenceError("DbusInventory.surfaces must be an iterable")
        for surface in self.surfaces:
            if not isinstance(surface, DbusSurface):
                raise EvidenceError("DbusInventory.surfaces must be DbusSurface")
        if self.state is ObservationState.UNOBSERVED and tuple(self.surfaces):
            raise EvidenceError("an UNOBSERVED inventory cannot carry surfaces")
        if self.state is ObservationState.OBSERVED_EMPTY and tuple(self.surfaces):
            raise EvidenceError("an OBSERVED_EMPTY inventory cannot carry surfaces")
        if self.state is ObservationState.OBSERVED_POPULATED and not tuple(self.surfaces):
            raise EvidenceError("an OBSERVED_POPULATED inventory must carry surfaces")


@dataclass(frozen=True)
class SessionSelectionEvidence:
    """How the graphical session is chosen. Evidence, not a boolean.

    The first revision took `gdm_session_selection_root_controlled` as a bare
    bool, so a top-level `True` certified the launch path with no supporting
    capture at all. This type requires the chain that was actually inspected and
    refuses to be satisfied by an assertion.
    """

    root_controlled: bool
    chain: tuple[str, ...]
    greeter_independent: bool
    root_ssh_independent: bool
    owner_ssh_key_only: bool

    def __post_init__(self) -> None:
        _require_exact_bool(self.root_controlled, "root_controlled")
        _require_exact_bool(self.greeter_independent, "greeter_independent")
        _require_exact_bool(self.root_ssh_independent, "root_ssh_independent")
        _require_exact_bool(self.owner_ssh_key_only, "owner_ssh_key_only")
        if isinstance(self.chain, (str, bytes)) or not isinstance(self.chain, Iterable):
            raise EvidenceError("chain must be an iterable of canonical paths")
        if not tuple(self.chain):
            raise EvidenceError(
                "chain must name at least one inspected component; an empty chain "
                "cannot establish that selection is root-controlled"
            )
        for entry in self.chain:
            _require_canonical_path(entry, "SessionSelectionEvidence.chain entry")


@dataclass(frozen=True)
class RequiredUnitEvidence:
    """A session-critical unit and where it actually resolves.

    Replaces an assumption ("the root-owned copy must win") with a measurement
    (`systemctl --user show -p FragmentPath`). `fragment` is the resolved
    component, so masking is a typed check rather than a guess.
    """

    unit: str
    fragment: PathComponent
    observed: bool = True
    resolution: "UnitResolution | None" = None

    def __post_init__(self) -> None:
        _require_text(self.unit, "RequiredUnitEvidence.unit")
        if not isinstance(self.fragment, PathComponent):
            raise EvidenceError("RequiredUnitEvidence.fragment must be a PathComponent")
        _require_exact_bool(self.observed, f"{self.unit}: observed")
        if self.resolution is not None and not isinstance(self.resolution, UnitResolution):
            raise EvidenceError(
                "RequiredUnitEvidence.resolution must be a UnitResolution or None"
            )

    @property
    def is_masked(self) -> bool:
        return self.fragment.is_masking_file()


# --- Revision 3 surfaces, following Rook's source decision ------------------
#
# Rook's decision of 2 October 2026 (work/rook-gnome-inbox-20261002/
# DECISION-0e9ca7f3.json) selected Shape A — a root-controlled narrowed static
# unit path established before the user manager starts — as source direction for
# this disabled candidate, and named the conditions on it. The same decision
# records that narrowing the path does **not** close the other ways systemd can
# change what the manager loads: "Treat transient-unit APIs, runtime property
# changes, generators, environment sources, manager reload/reexec and alternate
# launch paths as separate unresolved mechanisms. An environment variable alone
# does not close them."
#
# Revision 2 could report a closed boundary while saying nothing about either.
# Revision 3 cannot: a view with no validated narrowed path, or with no statement
# about those mechanisms, is open by construction. That is a deliberate increase
# in what "closed" requires, and it is recorded in REMAINING_GAPS.md.


class ManagerMechanism(str):
    """Mechanisms that can change what the user manager loads, kept separate.

    They are named individually because Rook's direction is that closing one says
    nothing about the others. `MANAGER_RELOAD_REEXEC` and `ALTERNATE_LAUNCH` can
    undo a narrowed path without any writable directory being involved at all,
    which is why an environment-only fix is not a fix.
    """

    TRANSIENT_UNITS = "transient_units"
    RUNTIME_PROPERTIES = "runtime_properties"
    GENERATORS = "generators"
    ENVIRONMENT_SOURCES = "environment_sources"
    MANAGER_RELOAD_REEXEC = "manager_reload_reexec"
    ALTERNATE_LAUNCH = "alternate_launch"


ALL_MANAGER_MECHANISMS: tuple[str, ...] = (
    ManagerMechanism.TRANSIENT_UNITS,
    ManagerMechanism.RUNTIME_PROPERTIES,
    ManagerMechanism.GENERATORS,
    ManagerMechanism.ENVIRONMENT_SOURCES,
    ManagerMechanism.MANAGER_RELOAD_REEXEC,
    ManagerMechanism.ALTERNATE_LAUNCH,
)

#: Why each reachable mechanism is a finding in its own right. Written per
#: mechanism so a report names the actual route rather than a category.
MANAGER_MECHANISM_DETAIL: Mapping[str, str] = {
    ManagerMechanism.TRANSIENT_UNITS:
        "a transient unit can be started with `systemd-run --user`; it exists in "
        "no directory on the search path at all, so narrowing the path does not "
        "constrain it",
    ManagerMechanism.RUNTIME_PROPERTIES:
        "unit properties can be set at runtime (`systemctl --user set-property`, "
        "`systemd-run --user --property=`), which changes what runs without "
        "changing any file the analysis scores",
    ManagerMechanism.GENERATORS:
        "environment and unit generators run before the manager's own load and "
        "can write into runtime directories that no root-owned directory "
        "precedes",
    ManagerMechanism.ENVIRONMENT_SOURCES:
        "the manager environment can be changed from inside the session "
        "(`systemctl --user set-environment`), so an in-session control over the "
        "search path is a control the session user can undo",
    ManagerMechanism.MANAGER_RELOAD_REEXEC:
        "a manager reload or re-exec re-reads configuration and can rebind what "
        "is loaded, including after the narrow path was validated",
    ManagerMechanism.ALTERNATE_LAUNCH:
        "a session or application launched outside the user manager does not "
        "obey the user manager's search path at all",
}

MANAGER_MECHANISM_MITIGATION: str = (
    "treat this mechanism as unresolved until it is closed on its own terms; do "
    "not report it closed because the unit search path was narrowed"
)


@dataclass(frozen=True)
class ManagerMutationInventory:
    """Which load-changing mechanisms beyond the search path remain reachable.

    Three-state for the same reason the extension and D-Bus inventories are:
    "we did not look" and "we looked and found none" are different claims, and
    only the second can support closure. An unknown mechanism identifier is
    refused rather than scored, because a name the analyser does not recognise
    cannot be judged and must not be counted as harmless.
    """

    state: ObservationState
    reachable: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_enum(self.state, ObservationState, "ManagerMutationInventory.state")
        if isinstance(self.reachable, (str, bytes)) or not isinstance(self.reachable, Iterable):
            raise EvidenceError("ManagerMutationInventory.reachable must be an iterable")
        for mechanism in self.reachable:
            if not isinstance(mechanism, str) or mechanism not in ALL_MANAGER_MECHANISMS:
                raise EvidenceError(
                    f"ManagerMutationInventory.reachable contains {mechanism!r}, which "
                    f"is not one of the declared mechanisms {ALL_MANAGER_MECHANISMS!r}; "
                    f"an unrecognised mechanism cannot be judged and must not be "
                    f"silently treated as unreachable"
                )
        if self.state is ObservationState.UNOBSERVED and tuple(self.reachable):
            raise EvidenceError("an UNOBSERVED inventory cannot carry mechanisms")
        if self.state is ObservationState.OBSERVED_EMPTY and tuple(self.reachable):
            raise EvidenceError(
                "an OBSERVED_EMPTY inventory cannot carry reachable mechanisms; use "
                "OBSERVED_POPULATED, or the emptiness assertion is a lie"
            )
        if self.state is ObservationState.OBSERVED_POPULATED and not tuple(self.reachable):
            raise EvidenceError(
                "an OBSERVED_POPULATED inventory must name the mechanisms that "
                "remain reachable; use OBSERVED_EMPTY to assert there are none"
            )


@dataclass(frozen=True)
class UnitResolution:
    """How a required unit resolves, beyond its fragment path.

    Rook's condition: "Verify complete trusted ancestry and actual resolved
    fragments, drop-ins, aliases, dependencies, executable/config assets and
    effective environment; a fragment-only read is insufficient." The fragment
    path was revision 2's entire required-unit model.

    Empty `drop_ins`/`aliases`/`assets` tuples are a real claim — "this unit has
    none" — and are different from `RequiredUnitEvidence.resolution` being
    `None`, which means the fragment was read and nothing else was.
    """

    capture_method: str
    effective_environment: str
    drop_ins: tuple[PathObservation, ...] = ()
    aliases: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    assets: tuple[PathObservation, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.capture_method, "UnitResolution.capture_method",
                      allow_empty=True)
        _require_text(self.effective_environment,
                      "UnitResolution.effective_environment", allow_empty=True)
        for name, values in (("drop_ins", self.drop_ins), ("assets", self.assets)):
            if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
                raise EvidenceError(f"UnitResolution.{name} must be an iterable")
            for value in values:
                if not isinstance(value, PathObservation):
                    raise EvidenceError(
                        f"UnitResolution.{name} entries must be PathObservation, so "
                        f"each one carries the ancestry that decides it"
                    )
        for name, values in (("aliases", self.aliases), ("dependencies", self.dependencies)):
            if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
                raise EvidenceError(f"UnitResolution.{name} must be an iterable")
            for value in values:
                if not isinstance(value, str):
                    raise EvidenceError(f"UnitResolution.{name} entries must be strings")


class UnitPathRefusal(str):
    """Stable identifiers for why a proposed Shape A unit path was refused."""

    EMPTY_PATH = "empty_path"
    TRAILING_COLON = "trailing_colon"
    EMPTY_ENTRY = "empty_entry"
    RELATIVE_ENTRY = "relative_entry"
    NON_CANONICAL_ENTRY = "non_canonical_entry"
    DUPLICATE_ENTRY = "duplicate_entry"
    ENTRY_NOT_CAPTURED = "entry_not_captured"
    ENTRY_NOT_AUTHORITATIVE = "entry_not_authoritative"
    ESTABLISHING_FILE_NOT_AUTHORITATIVE = "establishing_file_not_authoritative"
    ORDERING_NOT_STATED = "ordering_not_stated"
    ENVIRONMENT_NOT_ATTRIBUTED = "environment_not_attributed"


@dataclass(frozen=True)
class UnitPathProposal:
    """A root-supplied `$SYSTEMD_UNIT_PATH` for the disabled kiosk candidate.

    The value is typed rather than a string the loader is trusted to have
    validated, because every one of Rook's conditions is a check on *this value*:
    reject a trailing colon (upstream v259 appends the default search paths to
    it, reintroducing every writable entry the narrowing removed), reject empty
    and relative entries, and require the exact path to be in place before the
    user manager starts.

    `establishing_file` is a `PathObservation`, not a boolean, because revision 2
    was corrected for exactly that mistake: a caller asserting "this is
    root-controlled" is not evidence that it is. `ordering_evidence` must name
    how the value is established before the manager starts, and
    `observer_environment` must attribute the path that was captured.
    """

    entries: tuple[str, ...]
    establishing_file: PathObservation
    ordering_evidence: str
    observer_environment: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.entries, (str, bytes)) or not isinstance(self.entries, Iterable):
            raise EvidenceError("UnitPathProposal.entries must be an iterable of paths")
        for entry in self.entries:
            # Deliberately not `_require_canonical_path`: an empty or relative
            # entry must come back as a typed refusal with a reason, so a loader
            # can report why it refused instead of failing to construct a report.
            if not isinstance(entry, str):
                raise EvidenceError(
                    f"UnitPathProposal.entries must be strings, got "
                    f"{type(entry).__name__} ({entry!r})"
                )
        if not isinstance(self.establishing_file, PathObservation):
            raise EvidenceError("UnitPathProposal.establishing_file must be a PathObservation")
        _require_text(self.ordering_evidence, "ordering_evidence", allow_empty=True)
        _require_text(self.observer_environment, "observer_environment", allow_empty=True)


@dataclass(frozen=True)
class UnitPathRefusalRecord:
    reason: str
    detail: str


@dataclass(frozen=True)
class UnitPathValidation:
    """The verdict on one proposed unit path. Refusals, never a fallback."""

    entries: tuple[str, ...]
    refusals: tuple[UnitPathRefusalRecord, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.refusals

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(refusal.reason for refusal in self.refusals)


UNIT_PATH_REFUSAL_MITIGATION: Mapping[str, str] = {
    UnitPathRefusal.EMPTY_PATH:
        "supply the exact root-controlled path, or do not claim a narrowed path",
    UnitPathRefusal.TRAILING_COLON:
        "remove the trailing colon; the default search paths must not be appended",
    UnitPathRefusal.EMPTY_ENTRY:
        "remove the empty field; a colon list with an empty element is not a path",
    UnitPathRefusal.RELATIVE_ENTRY:
        "supply absolute directories only; a relative entry resolves against the "
        "manager's working directory, which the session user can influence",
    UnitPathRefusal.NON_CANONICAL_ENTRY:
        "canonicalise the entry before it is supplied to the manager",
    UnitPathRefusal.DUPLICATE_ENTRY:
        "supply each directory once; duplicates hide a disagreement about the path",
    UnitPathRefusal.ENTRY_NOT_CAPTURED:
        "capture complete ancestry for this entry, or remove it from the path",
    UnitPathRefusal.ENTRY_NOT_AUTHORITATIVE:
        "remove the writable entry rather than checking it after the fact; Shape A "
        "was selected because writable directories are never consulted",
    UnitPathRefusal.ESTABLISHING_FILE_NOT_AUTHORITATIVE:
        "establish the path from a file whose whole ancestry is root-owned and "
        "non-writable by the session user",
    UnitPathRefusal.ORDERING_NOT_STATED:
        "record how the path is in place before the user manager starts; an "
        "in-session assignment is a control the session user can undo",
    UnitPathRefusal.ENVIRONMENT_NOT_ATTRIBUTED:
        "attribute the captured path to the environment that produced it; an "
        "unattributed path may be the default path",
}


def validate_unit_path_override(
    proposal: UnitPathProposal,
    observations: Mapping[str, PathObservation],
) -> UnitPathValidation:
    """Decide whether a proposed Shape A unit path may be relied on.

    Pure, and deliberately a refusal function: it returns every reason the
    proposal cannot be relied on, so a loader reports why rather than falling
    back to something writable. Falling back is the failure mode Rook named
    explicitly — "Do not silently return to writable paths when a legitimate
    application fails to launch" — and it is the reason this returns reasons
    instead of a boolean that a caller can ignore.
    """
    if not isinstance(proposal, UnitPathProposal):
        raise TypeError("proposal must be a UnitPathProposal")
    if not isinstance(observations, Mapping):
        raise EvidenceError("observations must be a mapping of path -> PathObservation")

    refusals: list[UnitPathRefusalRecord] = []
    entries = tuple(proposal.entries)

    if not entries:
        refusals.append(UnitPathRefusalRecord(
            UnitPathRefusal.EMPTY_PATH,
            "the proposed path has no entries; an empty SYSTEMD_UNIT_PATH is not "
            "a narrowing, it is an unstated path",
        ))

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if entry == "":
            if index == len(entries) - 1:
                refusals.append(UnitPathRefusalRecord(
                    UnitPathRefusal.TRAILING_COLON,
                    "the path ends with an empty entry. Upstream v259 treats a "
                    "trailing colon as a request to append the default search "
                    "paths, which reintroduces every writable entry the narrowing "
                    "removed",
                ))
            else:
                refusals.append(UnitPathRefusalRecord(
                    UnitPathRefusal.EMPTY_ENTRY,
                    f"entry {index} is empty (a leading or doubled colon); an "
                    f"empty field is not a directory",
                ))
            continue
        if not entry.startswith("/"):
            refusals.append(UnitPathRefusalRecord(
                UnitPathRefusal.RELATIVE_ENTRY,
                f"entry {index} ({entry!r}) is not absolute; a relative entry "
                f"resolves against the manager's working directory",
            ))
            continue
        try:
            canonical = canonicalise(entry)
        except EvidenceError as exc:
            refusals.append(UnitPathRefusalRecord(
                UnitPathRefusal.NON_CANONICAL_ENTRY,
                f"entry {index} is not canonical: {exc}",
            ))
            continue
        if canonical in seen:
            refusals.append(UnitPathRefusalRecord(
                UnitPathRefusal.DUPLICATE_ENTRY,
                f"entry {index} ({canonical}) appears more than once",
            ))
            continue
        seen.add(canonical)

        observation = observations.get(canonical)
        if observation is None:
            refusals.append(UnitPathRefusalRecord(
                UnitPathRefusal.ENTRY_NOT_CAPTURED,
                f"entry {index} ({canonical}) was not captured, so its ownership "
                f"is unknown. An unobserved entry is not a safe entry",
            ))
            continue
        if not observation.authoritative:
            refusals.append(UnitPathRefusalRecord(
                UnitPathRefusal.ENTRY_NOT_AUTHORITATIVE,
                f"entry {index} ({canonical}) is not authoritative: "
                + "; ".join(observation.authority.reasons),
            ))

    if not proposal.establishing_file.authoritative:
        refusals.append(UnitPathRefusalRecord(
            UnitPathRefusal.ESTABLISHING_FILE_NOT_AUTHORITATIVE,
            f"the file that establishes the path "
            f"({proposal.establishing_file.path}) is not authoritative: "
            + "; ".join(proposal.establishing_file.authority.reasons),
        ))
    if not proposal.ordering_evidence.strip():
        refusals.append(UnitPathRefusalRecord(
            UnitPathRefusal.ORDERING_NOT_STATED,
            "no ordering evidence was recorded for how the path is in place "
            "before the user manager starts. `systemctl --user set-environment` "
            "is available to the session user, so a path set from inside the "
            "session is not a control",
        ))
    if not proposal.observer_environment.strip():
        refusals.append(UnitPathRefusalRecord(
            UnitPathRefusal.ENVIRONMENT_NOT_ATTRIBUTED,
            "the proposed path is not attributed to the environment that produced "
            "it, so it cannot be distinguished from the default path",
        ))

    return UnitPathValidation(entries=entries, refusals=tuple(refusals))


@dataclass(frozen=True)
class SystemView:
    """Everything the analyser is allowed to know.

    Built by the caller from a read-only capture. The analyser never probes the
    live system itself, so a verdict can be replayed and a test never needs a
    device.
    """

    unit_search_path: tuple[str, ...]
    paths: Mapping[str, PathObservation]
    extension_inventory: DirectoryInventory = field(
        default_factory=lambda: DirectoryInventory(ObservationState.UNOBSERVED,
                                                   description="gnome-shell extensions"))
    dbus_inventory: DbusInventory = field(
        default_factory=lambda: DbusInventory(ObservationState.UNOBSERVED))
    required_units: tuple[RequiredUnitEvidence, ...] = ()
    session_selection: SessionSelectionEvidence | None = None
    #: Revision 3. Defaults to UNOBSERVED, so a view that says nothing about the
    #: mechanisms that can change what the manager loads is open by construction.
    manager_mutations: ManagerMutationInventory = field(
        default_factory=lambda: ManagerMutationInventory(ObservationState.UNOBSERVED))
    #: Revision 3. Shape A was selected as source direction, so a closure claim
    #: with no validated narrowed path is missing evidence rather than neutral.
    unit_path_proposal: UnitPathProposal | None = None
    captured_at: str = ""
    capture_provenance: str = ""

    def __post_init__(self) -> None:
        # Validate shape eagerly. A wrong container type should say so here,
        # not surface later as an AttributeError from inside a check.
        if not isinstance(self.paths, Mapping):
            raise EvidenceError("paths must be a mapping of str -> PathObservation")
        if isinstance(self.unit_search_path, (str, bytes)) or not isinstance(
                self.unit_search_path, Iterable):
            raise EvidenceError("unit_search_path must be an iterable of paths")
        for entry in self.unit_search_path:
            _require_canonical_path(entry, "unit_search_path entry")
        for name, value in (("extension_inventory", self.extension_inventory),
                            ("dbus_inventory", self.dbus_inventory)):
            if not isinstance(value, (DirectoryInventory, DbusInventory)):
                raise EvidenceError(f"{name} must be an inventory record")
        if isinstance(self.required_units, (str, bytes)) or not isinstance(
                self.required_units, Iterable):
            raise EvidenceError("required_units must be an iterable")
        for unit in self.required_units:
            if not isinstance(unit, RequiredUnitEvidence):
                raise EvidenceError("required_units must be RequiredUnitEvidence")
        if self.session_selection is not None and not isinstance(
                self.session_selection, SessionSelectionEvidence):
            raise EvidenceError("session_selection must be SessionSelectionEvidence or None")
        if not isinstance(self.manager_mutations, ManagerMutationInventory):
            raise EvidenceError("manager_mutations must be a ManagerMutationInventory")
        if self.unit_path_proposal is not None and not isinstance(
                self.unit_path_proposal, UnitPathProposal):
            raise EvidenceError("unit_path_proposal must be a UnitPathProposal or None")
        _require_text(self.captured_at, "captured_at", allow_empty=True)
        _require_text(self.capture_provenance, "capture_provenance", allow_empty=True)
        for key, value in self.paths.items():
            _require_canonical_path(key, "paths key")
            if not isinstance(value, PathObservation):
                raise EvidenceError("paths values must be PathObservation")
            if value.path != key:
                raise EvidenceError(
                    f"paths key {key!r} disagrees with its observation {value.path!r}"
                )


@dataclass(frozen=True)
class Finding:
    attack_class: str
    severity: str
    path: str
    detail: str
    mitigation: str
    blocking: bool


@dataclass(frozen=True)
class ClosureVerdict:
    closed: bool
    checked: int
    missing_evidence: tuple[str, ...]
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    precedence: tuple["UnitPathEntry", ...] = field(default_factory=tuple)

    def by_class(self, attack_class: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.attack_class == attack_class)


# --- The authoritative user unit search path -------------------------------

#: Canonical order from systemd.unit(5), reviewed 2 October 2026 against the
#: systemd 262 publication. Earlier entries win outright.
#:
#: Two properties this table is used for, and one it is *not*:
#:   - It supplies `LATE_ENTRIES`, which are structurally unable to shadow.
#:   - It supplies `writable_by_design`, used only for entries the capture did
#:     not observe, where the location's nature is known from upstream but its
#:     ownership is not.
#:   - It does **not** supply precedence. Precedence is computed from the
#:     captured search path, because the effective path is environment-dependent
#:     (systemd.unit(5): "directories listed here are just the defaults").
CANONICAL_USER_UNIT_PRECEDENCE: tuple[str, ...] = (
    "$XDG_CONFIG_HOME/systemd/user.control",
    "$XDG_RUNTIME_DIR/systemd/user.control",
    "$XDG_RUNTIME_DIR/systemd/transient",
    "$XDG_RUNTIME_DIR/systemd/generator.early",
    "$XDG_CONFIG_HOME/systemd/user",
    "$XDG_CONFIG_DIRS/systemd/user",
    "/etc/systemd/user",
    "$XDG_RUNTIME_DIR/systemd/user",
    "/run/systemd/user",
    "$XDG_RUNTIME_DIR/systemd/generator",
    "$XDG_DATA_HOME/systemd/user",
    "$XDG_DATA_DIRS/systemd/user",
    "/usr/local/lib/systemd/user",
    "/usr/lib/systemd/user",
    "$XDG_RUNTIME_DIR/systemd/generator.late",
)

#: Entries that sort after every root-owned location. A unit here cannot
#: replace or mask a root-provided unit; it can only supply a name nothing else
#: provides. The first revision flagged these as equivalent to `user.control`,
#: which was over-broad and is corrected here.
LATE_ENTRIES: tuple[str, ...] = ("$XDG_RUNTIME_DIR/systemd/generator.late",)

#: Directories that are writable **by design** — runtime-created content. This
#: describes the location's intended nature, not the ownership of any particular
#: instance, so it is only used when the capture did not observe the entry.
WRITABLE_BY_DESIGN_MARKERS = (
    "/systemd/user.control",
    "/systemd/transient",
    "/systemd/generator",
    "/systemd/generator.early",
    "/systemd/generator.late",
)


class Precedence(Enum):
    """How dangerous this entry is, given where it actually sorts."""

    SHADOWING = "shadowing"            # sorts before a root-owned entry
    LATE = "late"                      # sorts after every root-owned entry
    ROOT_AUTHORITATIVE = "root_authoritative"  # a root-owned entry itself
    UNOBSERVED = "unobserved"          # position known, ownership not captured


@dataclass(frozen=True)
class UnitPathEntry:
    """One entry of the effective search path, classified by real position."""

    index: int
    path: str
    precedence: Precedence
    observation: PathObservation | None
    writable_by_design: bool

    def __post_init__(self) -> None:
        _require_exact_int(self.index, "UnitPathEntry.index", minimum=0)
        _require_canonical_path(self.path, "UnitPathEntry.path")
        _require_enum(self.precedence, Precedence, "UnitPathEntry.precedence")
        _require_exact_bool(self.writable_by_design, "UnitPathEntry.writable_by_design")


def _matches_marker(path: str, markers: Sequence[str]) -> bool:
    """True when `path` ends with one of `markers` as a whole path component run.

    Compared as `/`-delimited suffixes rather than by substring, so
    `/run/user/1000/systemd/generator` never matches the `generator.late` marker
    and `/etc/systemd/user.control.bak` never matches `user.control`.
    """
    padded = path.rstrip("/") + "/"
    for marker in markers:
        if padded.endswith(marker.rstrip("/") + "/"):
            return True
    return False


def classify_unit_path(view: SystemView) -> tuple[UnitPathEntry, ...]:
    """Classify each captured entry by its real position in the effective path.

    An entry is a shadowing risk only if some root-owned entry sorts *after* it.
    That is computed from the captured order rather than assumed from the entry's
    name, which is what makes the `generator.late` classification correct: it is
    last, so nothing sorts after it, so it cannot shadow anything.

    A search-path entry that a root-owned capture did not observe is only
    reported as a risk when its location is user-writable **by design**. A
    missing root-owned entry is refused separately — see
    `_unit_path_findings` — because an unread root path is also an unaudited one.
    """
    if not isinstance(view, SystemView):
        raise EvidenceError("view must be a SystemView")

    observed: list[tuple[int, PathObservation]] = []
    for index, entry in enumerate(view.unit_search_path):
        observation = view.paths.get(entry)
        if observation is not None:
            observed.append((index, observation))

    # The first root-authoritative entry bounds shadowing: entries before it can
    # win against root-owned units, entries after it cannot.
    last_root_index: int | None = None
    for index, observation in observed:
        if observation.authoritative or observation.owned_by_root:
            last_root_index = index

    classified: list[UnitPathEntry] = []
    for index, entry in enumerate(view.unit_search_path):
        observation = view.paths.get(entry)
        writable_by_design = _matches_marker(entry, WRITABLE_BY_DESIGN_MARKERS)
        # `LATE_ENTRIES` is a tuple, so `entry in LATE_ENTRIES` is an equality
        # scan and is safe. The suffix test is belt-and-braces for a capture that
        # resolved the variable to a concrete runtime directory.
        is_late = entry in LATE_ENTRIES or entry.endswith("/systemd/generator.late")

        if is_late:
            precedence = Precedence.LATE
        elif observation is None:
            precedence = Precedence.UNOBSERVED
        elif observation.authoritative or observation.owned_by_root:
            precedence = Precedence.ROOT_AUTHORITATIVE
        elif last_root_index is not None and index < last_root_index:
            precedence = Precedence.SHADOWING
        elif last_root_index is None:
            # No root-owned entry observed at all, so nothing bounds this one.
            precedence = Precedence.SHADOWING
        else:
            # Sorts after the last root-owned entry, so it cannot win. Lower risk,
            # but not zero: it can still shadow a unit from *another* writable dir.
            precedence = Precedence.LATE

        classified.append(UnitPathEntry(
            index=index,
            path=entry,
            precedence=precedence,
            observation=observation,
            writable_by_design=writable_by_design,
        ))
    return tuple(classified)


# --- Analysis --------------------------------------------------------------


def _unit_path_findings(view: SystemView) -> list[Finding]:
    findings: list[Finding] = []
    for entry in classify_unit_path(view):
        path = entry.path
        observation = entry.observation

        if observation is None:
            # Not observed is NOT safe. The predecessor review says so directly:
            # a missing leaf under a replaceable parent does not establish
            # closure. This now also covers root-owned entries, which the first
            # revision skipped on the strength of their path name alone.
            if entry.writable_by_design:
                findings.append(Finding(
                    attack_class=AttackClass.UNIT_SHADOWING,
                    severity=Severity.HIGH,
                    path=path,
                    detail="user-writable-by-design search-path entry was not "
                           "observed; an unread path is a missing leaf, and a "
                           "missing leaf does not establish closure",
                    mitigation="capture this path and every one of its ancestors; "
                               "require a derived root-owned, non-user-writable "
                               "resolved location",
                    blocking=True,
                ))
            else:
                findings.append(Finding(
                    attack_class=AttackClass.INCOMPLETE_EVIDENCE,
                    severity=Severity.HIGH,
                    path=path,
                    detail="this search-path entry appears in the effective path "
                           "but was never observed. Its path name suggests a "
                           "root-owned location, and a path name is not evidence "
                           "of ownership; the first revision skipped it on exactly "
                           "that assumption",
                    mitigation="capture this entry and its ancestors; do not infer "
                               "root ownership from a directory name",
                    blocking=True,
                ))
            continue

        if observation.inconsistent:
            findings.append(Finding(
                attack_class=AttackClass.INCONSISTENT_EVIDENCE,
                severity=Severity.HIGH,
                path=path,
                detail="caller-supplied claims contradict the captured ancestry; "
                       "refusing to resolve the conflict in either direction",
                mitigation="re-capture this path from one source and discard the "
                           "contradicting claim",
                blocking=True,
            ))
            continue

        if observation.authoritative:
            continue

        # A late entry cannot shadow, so a writable one is informational rather
        # than blocking: it can only supply a name nothing else provides.
        if entry.precedence is Precedence.LATE:
            if observation.writable_by_user:
                findings.append(Finding(
                    attack_class=AttackClass.GENERATOR_LATE_INJECTION,
                    severity=Severity.INFO,
                    path=path,
                    detail="this entry is writable by the desktop user, but it "
                           "sorts after every root-owned entry, so it cannot "
                           "replace or mask a root-provided unit. It can only "
                           "supply a name that nothing else provides",
                    mitigation="ensure no session-critical unit name is left "
                               "unprovided by a root-owned directory",
                    blocking=False,
                ))
            continue

        # Not authoritative. Two distinct situations, and conflating them would
        # overstate the evidence in one direction or the other:
        #
        #  - The path is observed to be kiosk-writable (possibly through an
        #    ancestor). That is a shadowing risk on its own evidence, whether or
        #    not the chain reaches all the way down.
        #  - The path is root-owned at the deepest component captured, but the
        #    chain does not reach an authority boundary. Nothing here shows the
        #    path is *dangerous*; what is missing is the proof that it is safe.
        #    That is incomplete evidence, and calling it "writable by the desktop
        #    user" would be a false statement about the capture.
        if observation.writable_by_user:
            attack = (AttackClass.TRANSIENT_INJECTION
                      if "/transient" in path
                      else AttackClass.GENERATOR_INJECTION
                      if "/generator" in path
                      else AttackClass.UNIT_SHADOWING)
            findings.append(Finding(
                attack_class=attack,
                severity=Severity.CRITICAL if "/user.control" in path else Severity.HIGH,
                path=path,
                detail="search-path entry precedes root-owned units and is "
                       "writable by the desktop user, so it can shadow or "
                       "replace a session unit. Derived from ancestry: "
                       + "; ".join(observation.authority.reasons),
                mitigation="make the effective search path root-owned and "
                           "non-writable by the desktop user, or pin the "
                           "session units so a writable entry cannot win",
                blocking=True,
            ))
            continue

        findings.append(Finding(
            attack_class=AttackClass.INCOMPLETE_EVIDENCE,
            severity=Severity.HIGH,
            path=path,
            detail="this search-path entry's deepest captured component "
                   f"({observation.observed_prefix!r}) is root-owned, but the "
                   f"chain does not reach an authority boundary, so it is not "
                   f"established that every component above it is too. Missing: "
                   + ", ".join(observation.ancestry.reasons()),
            mitigation="capture every component from / down to this entry with "
                       "uid, gid, mode, symlink state and ACL state",
            blocking=True,
        ))
    return findings


def _mask_findings(view: SystemView) -> list[Finding]:
    """Typed mask detection, not a substring match on free text.

    Two distinct checks, because they are two distinct failures:

    - A search-path entry that *is* a masking file is a denial artifact sitting
      in the path. The first revision looked for the words "mask", "dev/null" or
      "empty" in a note string; `st_size` and `symlink_target` are checkable, and
      a note is not.
    - A required unit whose **resolved** fragment is a masking file is masked in
      fact. That is direct evidence, and it is what the session actually loads.
    """
    findings: list[Finding] = []

    for entry in classify_unit_path(view):
        observation = entry.observation
        if observation is None:
            continue
        leaf = observation.ancestry.leaf
        if leaf is None or not leaf.is_masking_file():
            continue
        if not observation.authoritative:
            findings.append(Finding(
                attack_class=AttackClass.MASK_DENIAL,
                severity=Severity.MEDIUM,
                path=entry.path,
                detail=f"a user-writable search-path entry is itself a masking "
                       f"artifact ({_describe_mask(leaf)}); an empty file or a "
                       f"/dev/null symlink masks whatever it names",
                mitigation="refuse to start the session when a required unit "
                           "resolves to a mask",
                blocking=True,
            ))

    for unit in view.required_units:
        if not unit.observed:
            findings.append(Finding(
                attack_class=AttackClass.INCOMPLETE_EVIDENCE,
                severity=Severity.HIGH,
                path=unit.unit,
                detail="required-unit resolution was not captured, so whether "
                       "this session-critical unit is masked is unknown",
                mitigation="capture `systemctl --user show -p FragmentPath` for "
                           "this unit in-session and resolve it",
                blocking=True,
            ))
            continue
        if unit.is_masked:
            findings.append(Finding(
                attack_class=AttackClass.REQUIRED_UNIT_MASKED,
                severity=Severity.CRITICAL,
                path=unit.fragment.path,
                detail=f"session-critical unit {unit.unit} resolves to a masking "
                       f"artifact ({_describe_mask(unit.fragment)}), so it has "
                       f"load state masked and cannot be activated",
                mitigation="refuse to start the session; restore the unit from "
                           "its root-owned source before launching",
                blocking=True,
            ))
    return findings


def _describe_mask(component: PathComponent) -> str:
    if component.kind is PathKind.SYMLINK:
        return f"symlink -> {component.symlink_target}"
    if component.kind is PathKind.REGULAR_FILE:
        return f"regular file, size {component.size_bytes} bytes"
    return "not a masking artifact"


def _extension_findings(view: SystemView) -> list[Finding]:
    inventory = view.extension_inventory
    if not isinstance(inventory, DirectoryInventory):
        raise EvidenceError("extension_inventory must be a DirectoryInventory")
    if inventory.state is ObservationState.UNOBSERVED:
        return []

    findings: list[Finding] = []
    for directory in inventory.entries:
        if directory.inconsistent:
            findings.append(Finding(
                attack_class=AttackClass.INCONSISTENT_EVIDENCE,
                severity=Severity.HIGH,
                path=directory.path,
                detail="extension-directory evidence contradicts itself",
                mitigation="re-capture from one source",
                blocking=True,
            ))
            continue
        if not directory.present:
            continue
        if not directory.authoritative:
            findings.append(Finding(
                attack_class=AttackClass.EXTENSION_CODE,
                severity=Severity.CRITICAL,
                path=directory.path,
                detail="extension directory is not root-authoritative; extensions "
                       "load in-process and run with the Shell's own privilege, so "
                       "placing one is equivalent to running code as the Shell. "
                       "Derived from ancestry: "
                       + "; ".join(directory.authority.reasons),
                mitigation="restrict the Shell's extension search path to "
                           "root-owned locations and refuse to load extensions "
                           "from writable directories",
                blocking=True,
            ))
    return findings


def _dbus_findings(view: SystemView) -> list[Finding]:
    inventory = view.dbus_inventory
    if not isinstance(inventory, DbusInventory):
        raise EvidenceError("dbus_inventory must be a DbusInventory")
    if inventory.state is ObservationState.UNOBSERVED:
        return []

    findings: list[Finding] = []
    for surface in inventory.surfaces:
        if not surface.reachable_by_user:
            continue
        if surface.permits_code_execution and not surface.authenticated:
            findings.append(Finding(
                attack_class=AttackClass.SHELL_DBUS_EXEC,
                severity=Severity.CRITICAL,
                path=surface.name,
                detail=f"{surface.interface} is reachable without "
                       f"authentication and can execute code",
                mitigation="disable the method, or require an authenticated "
                           "and session-bound grant before it can be called",
                blocking=True,
            ))
    return findings


def _autostart_findings(view: SystemView) -> list[Finding]:
    findings: list[Finding] = []
    for path, observation in view.paths.items():
        if not path.endswith("/autostart") or not observation.present:
            continue
        if not observation.authoritative:
            findings.append(Finding(
                attack_class=AttackClass.AUTOSTART_INJECTION,
                severity=Severity.HIGH,
                path=path,
                detail="autostart directory is not root-authoritative, so a "
                       "desktop entry placed there starts user code inside the "
                       "session. Derived from ancestry: "
                       + "; ".join(observation.authority.reasons),
                mitigation="disable user autostart for the kiosk session, or "
                           "require root-owned entries only",
                blocking=True,
            ))
    return findings


def _selection_findings(view: SystemView) -> list[Finding]:
    """Session selection, greeter and root-SSH independence.

    Scored from a typed evidence record, never from a top-level boolean. The
    first revision certified the launch path from three bare `bool` fields on the
    view, which meant a caller could certify the boundary by asserting it.
    """
    findings: list[Finding] = []
    evidence = view.session_selection

    if evidence is None:
        findings.append(Finding(
            attack_class=AttackClass.SEARCH_PATH_REDIRECT,
            severity=Severity.HIGH,
            path="gdm/session-selection",
            detail="no session-selection evidence was captured, so whether the "
                   "launched session is chosen inside the boundary is unknown",
            mitigation="capture the session-selection chain and record which "
                       "components were inspected",
            blocking=True,
        ))
        findings.append(Finding(
            attack_class=AttackClass.GREETER_OR_SSH_BLAST_RADIUS,
            severity=Severity.HIGH,
            path="gdm/greeter",
            detail="greeter and root-SSH independence were not captured; the "
                   "recovery paths are unknown, not merely unverified",
            mitigation="capture greeter and root key-only SSH independence "
                       "evidence before treating the boundary as closed",
            blocking=True,
        ))
        return findings

    if not evidence.root_controlled:
        findings.append(Finding(
            attack_class=AttackClass.SEARCH_PATH_REDIRECT,
            severity=Severity.HIGH,
            path="gdm/session-selection",
            detail="graphical session selection is recorded as not "
                   f"root-controlled across: {', '.join(evidence.chain)}",
            mitigation="select and launch the session from a root-controlled "
                       "chain covering stock GNOME/Ubuntu, the legacy X11 "
                       "selector and the failure fallback",
            blocking=True,
        ))
    if not evidence.greeter_independent:
        findings.append(Finding(
            attack_class=AttackClass.GREETER_OR_SSH_BLAST_RADIUS,
            severity=Severity.HIGH,
            path="gdm/greeter",
            detail="greeter independence not established; a boundary that "
                   "captures the greeter would lock the owner out",
            mitigation="keep the greeter session separate from the desktop "
                       "boundary and verify it still starts",
            blocking=True,
        ))
    if not evidence.root_ssh_independent:
        findings.append(Finding(
            attack_class=AttackClass.GREETER_OR_SSH_BLAST_RADIUS,
            severity=Severity.HIGH,
            path="ssh/root-key-only",
            detail="independent root key-only SSH management not established as "
                   "preserved",
            mitigation="verify root key-only SSH still works after the boundary "
                       "is in place; it is the recovery path",
            blocking=True,
        ))
    elif not evidence.owner_ssh_key_only:
        findings.append(Finding(
            attack_class=AttackClass.GREETER_OR_SSH_BLAST_RADIUS,
            severity=Severity.HIGH,
            path="ssh/root-key-only",
            detail="root SSH survives, but it is not key-only, so the recovery "
                   "path itself carries a credential-guessing surface",
            mitigation="require key-only root SSH before relying on it as the "
                       "recovery path",
            blocking=True,
        ))
    return findings


def _environment_findings(view: SystemView) -> list[Finding]:
    """A writable user-manager environment can redirect the whole search path.

    systemd.unit(5) notes that the user manager's load path may be augmented or
    changed through environment variables, and those may be set by environment
    generators. `systemctl --user set-environment` is available to the session
    user. So even a perfect on-disk search path is not sufficient unless the
    environment that produces it is itself controlled.
    """
    findings: list[Finding] = []
    if not view.capture_provenance:
        findings.append(Finding(
            attack_class=AttackClass.MANAGER_ENVIRONMENT,
            severity=Severity.HIGH,
            path="systemd/user-manager-environment",
            detail="capture provenance was not recorded, so the captured "
                   "effective search path cannot be attributed to a known "
                   "environment. An unattributed path may be the default path, "
                   "or one redirected by a variable nobody recorded",
            mitigation="record the environment that produced the captured unit "
                       "path, and pin XDG_* for the session from root",
            blocking=True,
        ))

    markers = [p for p in view.paths
               if p.endswith("/environment.d") or p.endswith("/systemd/environment")]
    for path in markers:
        observation = view.paths[path]
        if observation.present and not observation.authoritative:
            findings.append(Finding(
                attack_class=AttackClass.MANAGER_ENVIRONMENT,
                severity=Severity.HIGH,
                path=path,
                detail="user-writable environment directory can inject XDG_* "
                       "variables and silently change the effective unit search "
                       "path. Derived from ancestry: "
                       + "; ".join(observation.authority.reasons),
                mitigation="pin XDG_RUNTIME_DIR/XDG_DATA_* for the session from "
                           "root, and treat a writable environment source as "
                           "unclosed",
                blocking=True,
            ))
    return findings


def _inventory_findings(view: SystemView) -> list[Finding]:
    """Absent inventories block closure; observed-empty ones do not.

    This is the fix for the candidate's weakest point. Previously the extension
    check iterated whatever directories the caller supplied, so a caller who
    supplied none scored the same as one who looked and found none. Those are
    different claims and they now produce different verdicts.
    """
    findings: list[Finding] = []
    required: tuple[tuple[str, object], ...] = (
        ("extension_dirs", view.extension_inventory),
        ("dbus_surfaces", view.dbus_inventory),
    )
    for name, inventory in required:
        state = getattr(inventory, "state", None)
        if state is ObservationState.UNOBSERVED:
            findings.append(Finding(
                attack_class=AttackClass.INCOMPLETE_EVIDENCE,
                severity=Severity.HIGH,
                path=name,
                detail=f"the {name} inventory was never captured, so this class "
                       f"cannot be judged. Omission is not an empty result and "
                       f"must not read as one",
                mitigation=f"capture the {name} inventory on the target and "
                           f"record it as OBSERVED_EMPTY or OBSERVED_POPULATED; "
                           f"never leave it unstated",
                blocking=True,
            ))
    return findings


def _inconsistency_findings(view: SystemView) -> list[Finding]:
    """Contradictory captures are refused, never resolved by picking a side."""
    findings: list[Finding] = []
    for path, observation in view.paths.items():
        if observation.inconsistent:
            findings.append(Finding(
                attack_class=AttackClass.INCONSISTENT_EVIDENCE,
                severity=Severity.HIGH,
                path=path,
                detail="observation is self-contradictory: a supplied claim "
                       "disagrees with the captured ancestry (for example marked "
                       "root-owned while an ancestor is user-writable); refusing "
                       "to treat as closed",
                mitigation="re-capture this path from one source, including every "
                           "ancestor, and discard the contradicting claim",
                blocking=True,
            ))
    return findings


def _unit_path_override_findings(view: SystemView) -> list[Finding]:
    """Shape A's own gate: a proposed narrowed path is validated or refused.

    Rook selected Shape A and named these conditions on it. A proposal that fails
    any of them is reported as blocking rather than quietly ignored, because the
    alternative to a validated narrow path is a loader that falls back to
    something writable — the failure mode the decision prohibits.
    """
    proposal = view.unit_path_proposal
    if proposal is None:
        # Reported through `missing_evidence`, so that a caller cannot make the
        # gate disappear by omitting the section entirely.
        return []
    validation = validate_unit_path_override(proposal, view.paths)
    return [
        Finding(
            attack_class=AttackClass.UNIT_PATH_OVERRIDE,
            severity=Severity.HIGH,
            path=f"systemd/unit-path:{refusal.reason}",
            detail=refusal.detail,
            mitigation=UNIT_PATH_REFUSAL_MITIGATION[refusal.reason],
            blocking=True,
        )
        for refusal in validation.refusals
    ]


def _manager_mutation_findings(view: SystemView) -> list[Finding]:
    """The mechanisms a narrowed path does not close, reported separately.

    Revision 2 could return a closed verdict without ever considering these. The
    decision of 2 October 2026 is explicit that they are separate unresolved
    mechanisms, so each reachable one is its own finding, and an inventory nobody
    captured blocks rather than passing.
    """
    inventory = view.manager_mutations
    if inventory.state is ObservationState.UNOBSERVED:
        return [Finding(
            attack_class=AttackClass.MANAGER_MUTATION,
            severity=Severity.HIGH,
            path="systemd/manager-mutation-mechanisms",
            detail="no statement was made about the mechanisms that can change "
                   "what the user manager loads (" + ", ".join(ALL_MANAGER_MECHANISMS)
                   + "). Narrowing the unit search path does not close them: a "
                   "transient unit, a runtime property, a generator, an "
                   "in-session environment change, a reload or an alternate "
                   "launch can each load code with no writable directory on the "
                   "search path at all",
            mitigation="record each mechanism as reachable or not; an unobserved "
                       "mechanism is not an unreachable one",
            blocking=True,
        )]
    findings: list[Finding] = []
    for mechanism in inventory.reachable:
        findings.append(Finding(
            attack_class=AttackClass.MANAGER_MUTATION,
            severity=Severity.CRITICAL if mechanism in (
                ManagerMechanism.MANAGER_RELOAD_REEXEC,
                ManagerMechanism.ALTERNATE_LAUNCH,
            ) else Severity.HIGH,
            path=f"systemd/{mechanism}",
            detail=MANAGER_MECHANISM_DETAIL[mechanism],
            mitigation=MANAGER_MECHANISM_MITIGATION,
            blocking=True,
        ))
    return findings


def _unit_resolution_findings(view: SystemView) -> list[Finding]:
    """A fragment path is not a resolution.

    Rook's condition is that complete trusted ancestry and the actual resolved
    fragments, drop-ins, aliases, dependencies, assets and effective environment
    must be verified. A drop-in is the sharp case: it overrides properties such
    as `ExecStart`, so a writable drop-in on a root-owned unit is code execution
    by another name.
    """
    findings: list[Finding] = []
    for unit in view.required_units:
        resolution = unit.resolution
        if resolution is None:
            findings.append(Finding(
                attack_class=AttackClass.UNIT_RESOLUTION,
                severity=Severity.HIGH,
                path=unit.unit,
                detail="only the resolved fragment path was read. Drop-ins, "
                       "aliases, dependencies, executable/config assets and the "
                       "effective environment were not captured, and any of them "
                       "can change what this unit actually runs",
                mitigation="capture the full resolution for this unit; a "
                           "fragment-only read is insufficient",
                blocking=True,
            ))
            continue
        if not resolution.capture_method.strip():
            findings.append(Finding(
                attack_class=AttackClass.UNIT_RESOLUTION,
                severity=Severity.HIGH,
                path=unit.unit,
                detail="the unit resolution does not say how it was captured, so "
                       "it cannot be reproduced or attributed",
                mitigation="record the command or source that produced this "
                           "resolution",
                blocking=True,
            ))
        if not resolution.effective_environment.strip():
            findings.append(Finding(
                attack_class=AttackClass.UNIT_RESOLUTION,
                severity=Severity.HIGH,
                path=unit.unit,
                detail="the unit's effective environment is unattributed, so a "
                       "variable that changes what it executes cannot be ruled out",
                mitigation="record the effective environment for this unit and "
                           "attribute it to a root-controlled source",
                blocking=True,
            ))
        for label, observed in (("drop-in", resolution.drop_ins),
                                ("executable/config asset", resolution.assets)):
            for entry in observed:
                if not entry.authoritative:
                    findings.append(Finding(
                        attack_class=AttackClass.UNIT_RESOLUTION,
                        severity=Severity.CRITICAL if label == "drop-in" else Severity.HIGH,
                        path=entry.path,
                        detail=f"this {label} for {unit.unit} is not authoritative: "
                               + "; ".join(entry.authority.reasons)
                               + (". A drop-in overrides unit properties such as "
                                  "ExecStart, so a writable one executes code "
                                  "regardless of where the fragment resolves"
                                  if label == "drop-in" else ""),
                        mitigation=f"remove or root-own this {label}, and verify its "
                                   f"complete ancestry rather than its leaf",
                        blocking=True,
                    ))
        if resolution.aliases:
            findings.append(Finding(
                attack_class=AttackClass.UNIT_RESOLUTION,
                severity=Severity.HIGH,
                path=unit.unit,
                detail=f"{unit.unit} has alias name(s) "
                       + ", ".join(sorted(resolution.aliases))
                       + ". An alias redirects a name to this unit; until each "
                         "alias target is shown to resolve to the same "
                         "root-owned fragment, the name is not pinned",
                mitigation="resolve each alias to its target fragment and show "
                           "that every target is root-owned and non-replaceable",
                blocking=True,
            ))
        if resolution.dependencies:
            findings.append(Finding(
                attack_class=AttackClass.UNIT_RESOLUTION,
                severity=Severity.INFO,
                path=unit.unit,
                detail="recorded dependencies: "
                       + ", ".join(sorted(resolution.dependencies)),
                mitigation="none required from this finding; recorded so that the "
                           "resolution is complete rather than partial",
                blocking=False,
            ))
    return findings


def analyse(view: SystemView) -> ClosureVerdict:
    """Decide whether the launch and code-loading boundary is closed.

    Pure. Returns a verdict with every individual finding, so a reviewer can
    argue with a specific line rather than a summary.
    """
    if not isinstance(view, SystemView):
        raise TypeError("view must be a SystemView")

    required_evidence: list[str] = []
    if not view.unit_search_path:
        required_evidence.append("unit_search_path")
    if not view.paths:
        required_evidence.append("paths")
    if not view.captured_at:
        required_evidence.append("captured_at")
    if not view.capture_provenance:
        required_evidence.append("capture_provenance")
    if not view.required_units:
        required_evidence.append("required_units")
    if view.session_selection is None:
        required_evidence.append("session_selection")
    if view.unit_path_proposal is None:
        # Revision 3: Shape A was selected as source direction, so a closure
        # claim that supplies no validated narrowed path has not addressed the
        # strategy in force. Omitting the section must not silence the gate.
        required_evidence.append("unit_path_proposal")

    findings: list[Finding] = []
    findings.extend(_unit_path_findings(view))
    findings.extend(_unit_path_override_findings(view))
    findings.extend(_mask_findings(view))
    findings.extend(_unit_resolution_findings(view))
    findings.extend(_extension_findings(view))
    findings.extend(_dbus_findings(view))
    findings.extend(_autostart_findings(view))
    findings.extend(_environment_findings(view))
    findings.extend(_manager_mutation_findings(view))
    findings.extend(_selection_findings(view))
    findings.extend(_inventory_findings(view))
    findings.extend(_inconsistency_findings(view))

    # A verdict of "closed" additionally requires that the evidence needed to
    # judge was actually present. Absence of proof is not proof.
    blocking = tuple(f for f in findings if f.blocking)
    closed = not blocking and not required_evidence

    return ClosureVerdict(
        closed=closed,
        checked=len(view.unit_search_path) + len(view.paths)
                + len(view.extension_inventory.entries) + len(view.dbus_inventory.surfaces)
                + len(view.required_units) + len(view.manager_mutations.reachable)
                + (len(view.unit_path_proposal.entries)
                   if view.unit_path_proposal is not None else 0),
        missing_evidence=tuple(required_evidence),
        findings=tuple(findings),
        precedence=classify_unit_path(view),
    )


# --- Inventory adapter -----------------------------------------------------

#: Recorded location whose writability is known from upstream rather than from
#: the capture. Used only to describe an entry that was **not** observed.
def _component_from_record(path: str, record: Mapping) -> PathComponent:
    """Build one typed component from a captured inventory record.

    Every field is validated by `PathComponent`. An absent path carries no
    ownership, so it is recorded as uid 0 / mode 0 with explicit `NO`
    writability rather than inheriting defaults from a present path.
    """
    if not isinstance(record, Mapping):
        raise EvidenceError(f"{path}: inventory record must be a mapping")

    present = record.get("present")
    if not isinstance(present, bool):
        raise EvidenceError(
            f"{path}: 'present' must be exactly bool, got {record.get('present')!r}"
        )

    if not present:
        return PathComponent(
            path=path, prescribed_uid=0, prescribed_gid=0, mode=0,
            kind=PathKind.ABSENT, symlink_target=None,
            writable_by_kiosk=Tri.NO, acl_writable_by_kiosk=Tri.NO,
        )

    symlink = record.get("symlink")
    if not isinstance(symlink, bool):
        raise EvidenceError(
            f"{path}: 'symlink' must be exactly bool, got {record.get('symlink')!r}"
        )

    # A symlink whose target is recorded as /dev/null is a mask. If the target
    # was not recorded, the kind is still SYMLINK and the target is unknown, so
    # the mask check cannot fire — which is the honest outcome.
    target = record.get("symlink_target")
    if symlink:
        if target is not None and not isinstance(target, str):
            raise EvidenceError(f"{path}: symlink_target must be a str or absent")
        kind = PathKind.SYMLINK
        symlink_target = target if isinstance(target, str) else "(unrecorded)"
        size = None
    else:
        kind = PathKind.DIRECTORY if record.get("is_dir", True) else PathKind.REGULAR_FILE
        symlink_target = None
        raw_size = record.get("size")
        size = raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) else None

    writable = record.get("writable_by_kiosk")
    if not isinstance(writable, bool):
        raise EvidenceError(
            f"{path}: 'writable_by_kiosk' must be exactly bool, got {writable!r}; "
            f"an unobserved writability is UNKNOWN, not False"
        )

    acl_raw = record.get("acl_writable_by_kiosk")
    acl = (Tri.YES if acl_raw is True else Tri.NO if acl_raw is False else Tri.UNKNOWN)

    return PathComponent(
        path=path,
        prescribed_uid=record.get("uid", 0),
        prescribed_gid=record.get("gid", 0),
        mode=int(str(record.get("mode", "0o0")), 8) if isinstance(record.get("mode"), str)
             else record.get("mode", 0),
        kind=kind,
        symlink_target=symlink_target,
        writable_by_kiosk=Tri.YES if writable else Tri.NO,
        acl_writable_by_kiosk=acl,
        size_bytes=size,
    )


def _ancestry_from_records(
    path: str, records: Mapping[str, Mapping]
) -> tuple[AncestryEvidence, tuple[str, ...]]:
    """Build the component chain for `path` from captured records.

    Walks down from the filesystem root, taking a record for each component.
    Returns the chain actually supported by the capture, plus the components that
    were **not** captured. The chain may be a strict prefix of `path`: a capture
    that recorded a leaf and its immediate parent but not the path up to the root
    has still recorded something true, and the analyser reports the gap instead
    of inventing the missing ownership.

    A user-controlled subtree root (`/home/<user>`, `/run/user/<uid>`) terminates
    the walk when it is a strict ancestor, because everything beneath it is
    already user-writable — which is the finding, not a gap.
    """
    parts = [p for p in path.split("/") if p]
    chain = ["/"] + ["/" + "/".join(parts[: i + 1]) for i in range(len(parts))]

    missing: list[str] = []
    components: list[PathComponent] = []
    for component_path in chain:
        record = records.get(component_path)
        if record is None:
            if _is_chain_terminator(component_path):
                # The boundary itself was not captured. Its own ownership is part
                # of the argument, so this is a genuine gap, not a free pass.
                missing.append(component_path)
                continue
            missing.append(component_path)
            continue
        components.append(_component_from_record(component_path, record))
        if component_path == path:
            break
        if _is_chain_terminator(component_path):
            break

    return AncestryEvidence(components=tuple(components)), tuple(missing)


def view_from_inventory(inventory: Mapping, *, captured_at: str = "",
                        provenance: str = "") -> SystemView:
    """Build a SystemView from an already-captured read-only inventory.

    Accepts the shape produced by the frozen predecessor collector
    (`work/c-desktop-launch-closure-20261002/launch-inventory.json`) so the same
    analysis can be replayed against existing evidence without touching a device.

    The predecessor inventory records `uid`, `gid`, `mode`, `symlink` and
    `writable_by_kiosk` for a set of paths that happens to include the ancestors
    of the audited leaves, so ancestry is genuinely derivable from it. Where an
    ancestor was not captured, the leaf is recorded as
    `INCOMPLETE_EVIDENCE` rather than being assigned an `authoritative` value.

    Anything else the inventory does not contain — extension directories, D-Bus
    surfaces, required-unit resolution, session selection — is recorded as
    **unobserved**, which blocks closure. That is deliberate and it is the
    correction to the first revision, which let omission read as acceptable: the
    predecessor inventory covers 11 fixed launch files and does not cover the
    user-writable `user.control`, `transient` or `generator.*` directories at all.
    """
    if not isinstance(inventory, Mapping):
        raise TypeError("inventory must be a mapping")

    reported = inventory.get("user_unit_path")
    unit_path: tuple[str, ...] = ()
    if isinstance(reported, Mapping) and isinstance(reported.get("stdout"), str):
        for line in reported["stdout"].splitlines():
            if line.startswith("UnitPath="):
                unit_path = tuple(part for part in line[len("UnitPath="):].split() if part)

    raw_paths = inventory.get("paths")
    records: dict[str, Mapping] = {}
    if isinstance(raw_paths, Mapping):
        for path, record in raw_paths.items():
            if isinstance(record, Mapping):
                records[str(path)] = record

    paths: dict[str, PathObservation] = {}
    for path, record in records.items():
        try:
            canonical = canonicalise(path)
        except EvidenceError:
            # A non-canonical path cannot be reasoned about at all. It is not
            # treated as observed, so `analyse` reports the search-path entry that
            # would have used it as unobserved rather than silently passing.
            continue
        ancestry, _missing = _ancestry_from_records(canonical, records)
        if not ancestry.components:
            # Not even the path's first component was captured: nothing at all is
            # known, so the search-path check reports the entry as unobserved.
            continue
        paths[canonical] = PathObservation(path=canonical, ancestry=ancestry)

    return SystemView(
        unit_search_path=unit_path,
        paths=paths,
        extension_inventory=DirectoryInventory(
            ObservationState.UNOBSERVED, description="gnome-shell extensions"),
        dbus_inventory=DbusInventory(ObservationState.UNOBSERVED),
        required_units=(),
        session_selection=None,
        captured_at=captured_at or str(inventory.get("checked_at", "")),
        capture_provenance=provenance,
    )


def summarise(verdict: ClosureVerdict) -> str:
    """One honest line. Never claims closure the verdict does not support."""
    if verdict.closed:
        return f"boundary CLOSED across {verdict.checked} observed locations"
    parts = [f"boundary OPEN: {len(verdict.findings)} finding(s) across {verdict.checked} observed locations"]
    if verdict.missing_evidence:
        parts.append("missing evidence: " + ", ".join(verdict.missing_evidence))
    counts: dict[str, int] = {}
    for finding in verdict.findings:
        counts[finding.attack_class] = counts.get(finding.attack_class, 0) + 1
    parts.append("by class: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return "; ".join(parts)
