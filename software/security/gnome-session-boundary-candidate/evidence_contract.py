"""The frozen-evidence consumer contract between the boundary analysis and the
root loader.

**DISABLED CANDIDATE. Pure. Enforces nothing, installs nothing, reads no file,
opens no socket and loads no BPF.** Hashes are computed over bytes the caller
supplies; this module never touches the filesystem.

Rook's decision of 2 October 2026
(`work/rook-gnome-inbox-20261002/DECISION-0e9ca7f3.json`) deferred the inspection
CLI with this condition:

    "Leave `scripts/lantern/ember-gnome-boundary.py` uncreated for now. The pure
    library and test runner are sufficient for this review; **finalize the
    loader's frozen-evidence consumer contract before adding a CLI.** No new
    collector, CLI or implementation scope is assigned by this response."

This module is that contract, and it is the reason no CLI was added.

## The one property it exists to provide

The predecessor review and Rook's decision both want the loader and the analyser
to agree on *one* predicate. A loader that re-implements the analysis can drift
from it, and a loader that trusts a summary can be fooled by a summary. So
consumption is defined as:

    a bundle is admissible if and only if
      (a) every section it claims is present, digest-bound to the payload the
          loader actually holds, and stated rather than implied, and
      (b) the analysis of the *same frozen view* reports the boundary closed.

Neither half is sufficient. A perfect bundle about an open boundary is refused,
and a closed-looking view that is not the frozen one is refused, because the
loader must show the view it analysed is the view the capture bound. There is no
"accept anyway" path and no partially-consumable bundle: refusal returns reasons
so the caller can report why, and the caller is never handed a value it can
mistake for permission.

## What is deliberately not here

- No collection. This module never says how evidence is gathered; it says what
  a bundle must be for a loader to act on it.
- No enforcement. Admission is a decision function; nothing is attached, mapped
  or launched.
- No device, no root, no environment mutation. Every value is supplied by the
  caller.
"""
from __future__ import annotations

import collections.abc
import enum
import types
import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from typing import Mapping, get_args, get_origin, get_type_hints

from . import analysis

CONTRACT_ID = "sage.lantern.gnome-boundary.frozen-evidence"
#: Version 3 carries full typed capture sections and analyses their decoded
#: view. Version 2 bound each section to its bytes and computed a view digest,
#: but did not bind those sections' contents to the view or require full shapes.
CONTRACT_VERSION = 3
DIGEST_ALGORITHM = "sha256"

#: Every section a consumable bundle must carry. Absence is a refusal, never a
#: default: this is the bundle-level form of "omission is not an empty result".
REQUIRED_SECTIONS: tuple[str, ...] = (
    "contract",
    "contract_version",
    "captured_at",
    "capture_provenance",
    "unit_search_path",
    "unit_path_proposal",
    "verified_unit_path",
    "paths",
    "required_units",
    "manager_mutations",
    "extension_inventory",
    "dbus_inventory",
    "session_selection",
    "section_digests",
    "section_payloads",
    "analysis_view_digest",
)

#: Sections that carry a three-state claim and must state it explicitly. A
#: section that is present but says nothing about whether it was observed is
#: refused for the same reason an absent one is.
THREE_STATE_SECTIONS: tuple[str, ...] = (
    "manager_mutations",
    "extension_inventory",
    "dbus_inventory",
)

#: Free-text sections a bundle may carry without affecting consumption. Anything
#: outside the known and optional sections is reported as a notice rather than
#: silently ignored, so an unexpected field is visible in the record.
OPTIONAL_SECTIONS: tuple[str, ...] = ("notes", "collector", "collector_version")

#: What the loader must not do, quoted from the disabled plan and Rook's
#: decision. Recorded in the contract so the consumer's obligations travel with
#: the evidence rather than living in a separate document.
LOADER_PROHIBITIONS: tuple[str, ...] = (
    "Do not silently return to a writable or default search path when a "
    "legitimate application fails to launch.",
    "Do not treat a root-owned leaf as sufficient; the whole ancestry decides.",
    "Do not treat a path name as evidence of ownership.",
    "Do not treat an unread path as safe.",
    "Do not set the manager environment from inside the session.",
    "Do not certify from a top-level boolean, including this bundle's own "
    "sections: each claim must be backed by the capture it names.",
    "Do not treat a narrowed unit path as closing transient units, runtime "
    "properties, generators, environment sources, reload/reexec or alternate "
    "launch; those are separate mechanisms.",
    "Do not admit a bundle whose digest does not match the bytes held, and do "
    "not substitute a re-read for a verification.",
)


class ContractRefusal(str):
    """Stable identifiers for why a bundle was refused."""

    NOT_A_MAPPING = "not_a_mapping"
    MISSING_SECTION = "missing_section"
    UNKNOWN_CONTRACT = "unknown_contract"
    UNSUPPORTED_VERSION = "unsupported_version"
    VERSION_NOT_INTEGER = "version_not_integer"
    VALUE_NULL = "value_null"
    VALUE_NOT_BOUND = "value_not_bound"
    INVALID_SECTION_SCHEMA = "invalid_section_schema"
    SECTION_NOT_BOUND = "section_not_bound"
    DIGEST_MISSING = "digest_missing"
    PAYLOAD_MISSING = "payload_missing"
    PAYLOAD_NOT_BYTES = "payload_not_bytes"
    DIGEST_MISMATCH = "digest_mismatch"
    STATE_NOT_EXPLICIT = "state_not_explicit"
    STATE_UNKNOWN = "state_unknown"
    STATE_NOT_BOUND = "state_not_bound"
    VIEW_DIGEST_MISMATCH = "view_digest_mismatch"
    VIEW_DIGEST_NOT_COMPUTED = "view_digest_not_computed"
    PATH_NOT_DERIVED_FROM_PROPOSAL = "path_not_derived_from_proposal"
    ANALYSIS_NOT_CLOSED = "analysis_not_closed"


@dataclass(frozen=True)
class ContractRefusalRecord:
    reason: str
    section: str
    detail: str


@dataclass(frozen=True)
class BundleVerdict:
    """The result of consuming a frozen bundle.

    `consumable` is the whole answer and is only ever `True` when nothing was
    refused. `analysis_closed` is exposed separately so a report can distinguish
    "the bundle is malformed" from "the bundle is sound and describes an open
    boundary" — two different actions for whoever reads it.
    """

    consumable: bool
    refusals: tuple[ContractRefusalRecord, ...] = ()
    digest_verified: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    analysis_closed: bool = False
    missing_evidence: tuple[str, ...] = ()

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(refusal.reason for refusal in self.refusals)

    def for_section(self, section: str) -> tuple[ContractRefusalRecord, ...]:
        return tuple(r for r in self.refusals if r.section == section)


def section_digest(payload: bytes) -> str:
    """`sha256` over the exact bytes. Pure: the bytes come from the caller."""
    if not isinstance(payload, (bytes, bytearray)):
        raise analysis.EvidenceError(
            f"payload must be bytes, got {type(payload).__name__}"
        )
    return hashlib.sha256(bytes(payload)).hexdigest()


def _scalar(value):
    """Reduce a value to JSON-representable scalars, deterministically.

    Dataclasses become field maps, enums become their values, and mappings and
    sequences recurse. Anything already scalar is returned untouched.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _scalar(getattr(value, f.name))
                for f in fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _scalar(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_scalar(v) for v in value]
    return value


def canonical_section_bytes(value) -> bytes:
    """Version 3: typed JSON for every section; strings never alias booleans.

    Container keys must already be strings. No coercion establishes authority.
    """
    def exact(value, depth=0):
        if depth > 32:
            raise analysis.EvidenceError("section_schema_depth")
        if is_dataclass(value) and not isinstance(value, type):
            return {f.name: exact(getattr(value, f.name), depth+1) for f in fields(value)}
        if isinstance(value, enum.Enum):
            return exact(value.value, depth+1)
        if value is None or type(value) in (str, int, bool):
            return value
        if type(value) in (list, tuple) and len(value) <= 4096:
            return [exact(v, depth+1) for v in value]
        if isinstance(value, Mapping) and len(value) <= 4096:
            if any(type(k) is not str for k in value):
                raise analysis.EvidenceError("section_schema_key")
            return {k: exact(v, depth+1) for k, v in value.items()}
        raise analysis.EvidenceError("section_schema_type")
    return json.dumps(exact(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def frozen_view_sections(view: analysis.SystemView) -> dict:
    """The full capture fields, including ancestry and inventory contents."""
    return {f.name: _scalar(getattr(view, f.name)) for f in fields(analysis.SystemView)}


def _decode_section(value, kind, depth=0):
    """Decode only this pinned analysis module's exact dataclass schema.

    Missing/defaulted and extra fields are refused. Dataclass constructors
    enforce semantic consistency after exact JSON shape/type checks.
    """
    if depth > 32:
        raise analysis.EvidenceError("section_schema_depth")
    origin, args = get_origin(kind), get_args(kind)
    if origin is types.UnionType:
        for choice in args:
            try:
                return _decode_section(value, choice, depth+1)
            except analysis.EvidenceError:
                pass
        raise analysis.EvidenceError("section_schema_union")
    if kind is type(None):
        if value is None:
            return None
    elif kind in (str, int, bool):
        if type(value) is kind:
            return value
    elif isinstance(kind, type) and issubclass(kind, enum.Enum):
        if type(value) is str:
            try:
                return kind(value)
            except ValueError:
                pass
    elif origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        if type(value) is list and len(value) <= 4096:
            return tuple(_decode_section(v, args[0], depth+1) for v in value)
    elif origin is collections.abc.Mapping:
        if type(value) is dict and len(value) <= 4096 and args[0] is str:
            if all(type(k) is str for k in value):
                return {k:_decode_section(v,args[1],depth+1) for k,v in value.items()}
    elif isinstance(kind,type) and is_dataclass(kind) and kind.__module__ == analysis.__name__:
        names = {f.name for f in fields(kind)}
        if type(value) is dict and set(value) == names:
            hints = get_type_hints(kind)
            return kind(**{k:_decode_section(value[k],hints[k],depth+1) for k in names})
    raise analysis.EvidenceError("section_schema_type_or_fields")


def view_from_verified_sections(bundle: Mapping) -> analysis.SystemView:
    """Reconstruct the one view from full sections whose bytes were verified.

    Pure decoding only. Callers must run verify_bundle before consumption.
    """
    values = {f.name:bundle[f.name] for f in fields(analysis.SystemView)}
    return _decode_section(values,analysis.SystemView)


def canonical_view_digest(view) -> str:
    """The canonical digest of the whole analysed view.

    Computed here, from the view object, so that the digest a bundle binds is
    always a statement about the view actually being analysed. A caller-supplied
    digest cannot confer authority, because it is compared with this value rather
    than trusted in place of it.
    """
    return section_digest(canonical_section_bytes(view))


def derived_unit_path(view: "analysis.SystemView") -> str | None:
    """The `$SYSTEMD_UNIT_PATH` the validated proposal actually establishes.

    Derived from the proposal rather than read from the bundle, so a correctly
    hashed path that no proposal supports cannot be admitted beside one that
    proposes something else.
    """
    proposal = getattr(view, "unit_path_proposal", None)
    if not isinstance(proposal, analysis.UnitPathProposal):
        return None
    return ":".join(proposal.entries)


def contract_document() -> dict:
    """The contract as data, so it can be reviewed and version-pinned."""
    return {
        "contract": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "digest_algorithm": DIGEST_ALGORITHM,
        "required_sections": list(REQUIRED_SECTIONS),
        "three_state_sections": list(THREE_STATE_SECTIONS),
        "optional_sections": list(OPTIONAL_SECTIONS),
        "refusal_reasons": [
            value for name, value in vars(ContractRefusal).items()
            if not name.startswith("_") and isinstance(value, str)
        ],
        "loader_prohibitions": list(LOADER_PROHIBITIONS),
        "admission_rule": (
            "consumable = every required section present, every three-state "
            "section explicitly stated, every declared digest equal to the "
            "sha256 of the payload supplied, the analysis view digest equal to "
            "the view being analysed, and that view reporting the boundary "
            "closed"
        ),
    }


def _three_state_is_explicit(section: Mapping) -> bool:
    """A three-state claim must name a real state. Quiet defaults are not states."""
    if not isinstance(section, Mapping):
        return False
    return "state" in section


#: The states a three-state section may name. `unobserved` is a legitimate value
#: for a bundle to carry -- an honest bundle about an open boundary says exactly
#: that -- so it is accepted here and bound to the analysis in `admit` instead.
#: Refusing it at verification would collapse "this evidence is malformed" into
#: "this evidence describes an open boundary", which are two different answers
#: for whoever reads the verdict.
_KNOWN_STATES = frozenset(state.value for state in analysis.ObservationState)

#: Which view attribute each three-state section summarises. A bundle's claim is
#: checked against the analysis it accompanies, because parsing a state is not
#: the same as binding it: a correctly hashed `observed_empty` could otherwise
#: travel beside a view whose surface was never observed.
_THREE_STATE_VIEW_FIELDS = {
    "manager_mutations": "manager_mutations",
    "extension_inventory": "extension_inventory",
    "dbus_inventory": "dbus_inventory",
}


def verify_bundle(bundle: Mapping) -> BundleVerdict:
    """Check a frozen bundle against the contract. Refusals, never a default.

    Pure. It does not build an analysis view and does not decide closure; it
    decides whether the evidence is *eligible* to be consumed at all.
    """
    if not isinstance(bundle, Mapping):
        raise TypeError("bundle must be a mapping")

    refusals: list[ContractRefusalRecord] = []
    notices: list[str] = []
    verified: list[str] = []

    for section in REQUIRED_SECTIONS:
        if section not in bundle:
            refusals.append(ContractRefusalRecord(
                ContractRefusal.MISSING_SECTION, section,
                f"the bundle does not carry {section!r}. A missing section is a "
                f"refusal, not an empty result",
            ))

    if bundle.get("contract") != CONTRACT_ID:
        refusals.append(ContractRefusalRecord(
            ContractRefusal.UNKNOWN_CONTRACT, "contract",
            f"contract must be exactly {CONTRACT_ID!r}, got "
            f"{bundle.get('contract')!r}",
        ))
    version = bundle.get("contract_version")
    # `type(...) is int`, not `isinstance`: `True == 1` in Python, so a boolean
    # passes an integer comparison and a truthy flag would be read as version 1.
    if type(version) is not int:
        refusals.append(ContractRefusalRecord(
            ContractRefusal.VERSION_NOT_INTEGER, "contract_version",
            f"contract_version must be the integer {CONTRACT_VERSION}, got "
            f"{version!r} of type {type(version).__name__}; a boolean or a string "
            f"is not a version, however truthy it is",
        ))
    elif version != CONTRACT_VERSION:
        refusals.append(ContractRefusalRecord(
            ContractRefusal.UNSUPPORTED_VERSION, "contract_version",
            f"contract_version must be exactly {CONTRACT_VERSION!r}, got "
            f"{version!r}; an unrecognised version cannot be assumed compatible",
        ))

    for section in THREE_STATE_SECTIONS:
        value = bundle.get(section)
        if value is None:
            continue  # refused as a null value below
        if not _three_state_is_explicit(value):
            refusals.append(ContractRefusalRecord(
                ContractRefusal.STATE_NOT_EXPLICIT, section,
                f"{section!r} does not state whether it was observed. A surface "
                f"that says nothing is not an observed-empty surface",
            ))
            continue
        state = value.get("state")
        if type(state) is not str or state not in _KNOWN_STATES:
            refusals.append(ContractRefusalRecord(
                ContractRefusal.STATE_UNKNOWN, section,
                f"{section!r} names the state {state!r}, which is not one of the "
                f"three states this contract defines "
                f"({', '.join(sorted(_KNOWN_STATES))}). An unrecognised state "
                f"cannot be scored",
            ))

    digests = bundle.get("section_digests")
    payloads = bundle.get("section_payloads")
    # Both maps are required to be mappings. Version 1 treated `None` as an
    # exemption, which skipped the verification loop *and* the per-section digest
    # loop, so a bundle with no maps at all was admitted with nothing checked.
    if not isinstance(digests, Mapping):
        refusals.append(ContractRefusalRecord(
            ContractRefusal.DIGEST_MISSING, "section_digests",
            "section_digests must be a mapping of section -> hex digest; it is "
            "absent or null, so no section's bytes can be shown to be the bytes "
            "that were frozen",
        ))
        digests = None
    if not isinstance(payloads, Mapping):
        refusals.append(ContractRefusalRecord(
            ContractRefusal.PAYLOAD_MISSING, "section_payloads",
            "section_payloads must be a mapping of section -> bytes; it is "
            "absent or null, so a claimed digest cannot be verified",
        ))
        payloads = None

    if isinstance(digests, Mapping) and isinstance(payloads, Mapping):
        for section in digests:
            if section not in REQUIRED_SECTIONS:
                notices.append(
                    f"section_digests names {section!r}, which is not a required "
                    f"section; it is recorded and ignored"
                )
        for section in REQUIRED_SECTIONS:
            if section in ("section_digests", "section_payloads"):
                continue
            value = bundle.get(section)
            if value is None:
                # Absent and null are the same refusal here: neither is captured
                # evidence, and a section present-but-null used to pass.
                refusals.append(ContractRefusalRecord(
                    ContractRefusal.VALUE_NULL, section,
                    f"{section!r} is null, which is not captured evidence. A "
                    f"section that carries no value cannot be verified",
                ))
                continue
            claimed = digests.get(section)
            if claimed is None:
                refusals.append(ContractRefusalRecord(
                    ContractRefusal.DIGEST_MISSING, section,
                    f"{section!r} carries no digest, so the loader cannot show "
                    f"the bytes it holds are the bytes that were frozen",
                ))
                continue
            payload = payloads.get(section)
            if payload is None:
                refusals.append(ContractRefusalRecord(
                    ContractRefusal.PAYLOAD_MISSING, section,
                    f"a digest is claimed for {section!r} but no payload was "
                    f"supplied, so the claim cannot be verified. An unverifiable "
                    f"digest is not a binding",
                ))
                continue
            if not isinstance(payload, (bytes, bytearray)):
                refusals.append(ContractRefusalRecord(
                    ContractRefusal.PAYLOAD_NOT_BYTES, section,
                    f"the payload for {section!r} is "
                    f"{type(payload).__name__}, not bytes; a digest only binds "
                    f"exact bytes",
                ))
                continue
            payload = bytes(payload)
            # The value must be the thing the bytes describe. Without this a
            # verified payload could sit beside a section value it never
            # mentioned, and the bytes would still hash correctly.
            try:
                canonical = canonical_section_bytes(value)
            except (analysis.EvidenceError, TypeError, ValueError, RecursionError):
                refusals.append(ContractRefusalRecord(
                    ContractRefusal.INVALID_SECTION_SCHEMA, section,
                    "the section has no valid exact-schema canonical encoding",
                ))
                continue
            if payload != canonical:
                refusals.append(ContractRefusalRecord(
                    ContractRefusal.VALUE_NOT_BOUND, section,
                    f"{section!r} does not match its own verified bytes: the "
                    f"payload is {payload!r} but the value canonically encodes to "
                    f"{canonical!r}. The loader must refuse rather than pick one",
                ))
                continue
            actual = section_digest(payload)
            if actual != claimed:
                refusals.append(ContractRefusalRecord(
                    ContractRefusal.DIGEST_MISMATCH, section,
                    f"{section!r} digest mismatch: the bundle claims {claimed!r}, "
                    f"the supplied bytes hash to {actual!r}. The loader must refuse "
                    f"rather than re-read or repair",
                ))
                continue
            verified.append(section)

    if not refusals:
        try:
            view_from_verified_sections(bundle)
        except (analysis.EvidenceError, KeyError, TypeError, ValueError, RecursionError):
            refusals.append(ContractRefusalRecord(
                ContractRefusal.INVALID_SECTION_SCHEMA, "analysis_view",
                "full frozen sections do not decode to the exact typed analysis schema",
            ))

    for key in bundle:
        if key not in REQUIRED_SECTIONS and key not in OPTIONAL_SECTIONS:
            notices.append(
                f"the bundle carries {key!r}, which the contract does not define; "
                f"it is recorded and ignored"
            )

    return BundleVerdict(
        consumable=not refusals,
        refusals=tuple(refusals),
        digest_verified=tuple(verified),
        notices=tuple(notices),
    )


def admit(bundle: Mapping, *, view: analysis.SystemView, view_digest: str) -> BundleVerdict:
    """The single admission predicate the loader and the analyser share.

    Refuses unless the bundle verifies, the digest names the identical frozen
    view, and that view reports the boundary closed. There is no mode that
    admits a bundle about an open boundary, and no mode that admits a view other
    than the one bound by the capture.
    """
    if not isinstance(view, analysis.SystemView):
        raise TypeError("view must be a SystemView")
    verified = verify_bundle(bundle)
    refusals = list(verified.refusals)

    # Eligibility failure cannot be consumed, decoded or replaced by a closed
    # caller view. There is no fallback to caller-supplied summaries.
    if not verified.consumable:
        return verified
    frozen_view = view_from_verified_sections(bundle)
    for section, value in frozen_view_sections(view).items():
        if canonical_section_bytes(bundle[section]) != canonical_section_bytes(value):
            refusals.append(ContractRefusalRecord(
                ContractRefusal.SECTION_NOT_BOUND, section,
                "the full verified section contradicts the supplied analysis view",
            ))
    frozen_digest = canonical_view_digest(frozen_view)
    if bundle.get("analysis_view_digest") != frozen_digest:
        refusals.append(ContractRefusalRecord(
            ContractRefusal.SECTION_NOT_BOUND, "analysis_view_digest",
            "the declared digest does not name the view decoded from verified sections",
        ))

    # Computed from the view in front of us, never taken from the caller. A
    # supplied view-plus-hash pair is a claim about a binding, not the binding:
    # version 1 compared the caller's string with the bundle's string, so the
    # digest of an open view could travel beside a different, closed one and both
    # strings would agree.
    computed_view_digest = canonical_view_digest(view)
    if view_digest != computed_view_digest:
        refusals.append(ContractRefusalRecord(
            ContractRefusal.VIEW_DIGEST_NOT_COMPUTED, "analysis_view_digest",
            f"the supplied view digest {view_digest!r} is not the digest of the "
            f"view being analysed, which is {computed_view_digest!r}. A caller "
            f"cannot establish what it is analysing by asserting it",
        ))
    claimed_view_digest = bundle.get("analysis_view_digest") \
        if isinstance(bundle, Mapping) else None
    if claimed_view_digest != computed_view_digest:
        refusals.append(ContractRefusalRecord(
            ContractRefusal.VIEW_DIGEST_MISMATCH, "analysis_view_digest",
            f"the view being analysed hashes to {computed_view_digest!r} but the "
            f"bundle binds {claimed_view_digest!r}. The loader must analyse the "
            f"frozen view, not a re-read of the live system",
        ))

    # The admitted unit path comes from the same validated proposal the rest of
    # the analysis uses. A correctly hashed path that the proposal does not
    # establish is a contradiction, not evidence.
    expected_path = derived_unit_path(frozen_view)
    if expected_path is not None:
        supplied_path = bundle.get("verified_unit_path") \
            if isinstance(bundle, Mapping) else None
        if supplied_path != expected_path:
            refusals.append(ContractRefusalRecord(
                ContractRefusal.PATH_NOT_DERIVED_FROM_PROPOSAL,
                "verified_unit_path",
                f"the bundle admits {supplied_path!r} but the validated proposal "
                f"establishes {expected_path!r}. The loader must load the proposed "
                f"path, so a contradictory value is refused rather than preferred",
            ))

    # A three-state section must describe the surface the analysis actually
    # considered. Parsing a state is not binding it: a correctly hashed
    # `observed_empty` could otherwise travel beside a view whose surface was
    # never observed, and a bundle would be admitted on evidence that contradicts
    # the thing it was admitted with.
    for section, attribute in _THREE_STATE_VIEW_FIELDS.items():
        claimed_state = None
        if isinstance(bundle, Mapping):
            value = bundle.get(section)
            if isinstance(value, Mapping):
                claimed_state = value.get("state")
        if claimed_state is None:
            continue  # already refused as null or not explicit
        actual = getattr(getattr(view, attribute, None), "state", None)
        actual_state = actual.value if isinstance(actual, analysis.ObservationState) else None
        if claimed_state != actual_state:
            refusals.append(ContractRefusalRecord(
                ContractRefusal.STATE_NOT_BOUND, section,
                f"the bundle states {section!r} is {claimed_state!r} but the view "
                f"being analysed has it {actual_state!r}. The claim and the "
                f"analysis must describe the same surface",
            ))

    verdict = analysis.analyse(frozen_view)
    if not verdict.closed:
        blocking = [f for f in verdict.findings if f.blocking]
        summary = "; ".join(
            f"{f.attack_class}:{f.path}" for f in blocking[:8]
        ) or "no blocking finding was reported, so the verdict is incomplete"
        refusals.append(ContractRefusalRecord(
            ContractRefusal.ANALYSIS_NOT_CLOSED, "analysis",
            f"the frozen view does not report a closed boundary: {summary}"
            + (f" (+{len(blocking) - 8} more)" if len(blocking) > 8 else ""),
        ))

    return BundleVerdict(
        consumable=not refusals,
        refusals=tuple(refusals),
        digest_verified=verified.digest_verified,
        notices=verified.notices,
        analysis_closed=verdict.closed,
        missing_evidence=verdict.missing_evidence,
    )
