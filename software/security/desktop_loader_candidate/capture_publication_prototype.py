"""Disabled publication/revocation sequencing prototype; no production backend.

No filesystem, socket, process, device or native-loader calls. The default ports
refuse every operation. Injected ports are a source-test boundary, not evidence
of authenticated authority, durable storage or a rollback-resistant anchor.
All observations/outcomes have runtime_authorized=False.

The future native owner must serialize all publishers, revokers and exact-child
release callbacks in ONE authority transaction, including across restarts. It
must implement independently protected durable monotonic compare/exchange and
complete root/bootstrap/asset/child qualification. This Python prototype does
not supply that owner, a Python fork/preexec path, an installer or a config flag.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import re

MAX_COUNTER = 2**64 - 1
MAX_RECORD_BYTES = 4096
MAX_PATH_BYTES = 1024
CURRENT_SCHEMA = "sage.lantern.root-gnome-current-capture.v1"
_FIELDS = {"schema", "state", "machine_id", "boot_id", "capture_id",
           "generation", "policy_path", "policy_sha256"}
_MACHINE = re.compile(r"[0-9a-f]{32}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PHASES = {"closed", "reserved", "active", "revoked", "release_attempted"}


class ProtocolRefused(ValueError):
    """A known refusal. Never permission to fall back to earlier evidence."""


class BackendUnavailable(RuntimeError):
    pass


class OutcomeUnknown(RuntimeError):
    """A callback may have acted; recovery must inspect fresh persisted state."""


@dataclass(frozen=True)
class Identity:
    machine_id: str
    boot_id: str


@dataclass(frozen=True)
class Anchor:
    """Independent CAS state; revision prevents phase/identity ABA.

    'reserved' invalidates the earlier generation BEFORE depot publication.
    A release-attempt marker is persisted BEFORE the release callback. It is
    terminal for that generation, including after an unknown callback or restart.
    The outstanding obligation survives later publication, revocation and boots.
    This disabled prototype has no qualified lifecycle-settlement operation.
    A provisioned generation-zero anchor is explicit; missing state is refused.
    """
    machine_id: str
    revision: int
    generation: int
    phase: str
    current_sha256: str | None = None
    launch_id: str | None = None
    outstanding: ReleaseObligation | None = None


@dataclass(frozen=True)
class CurrentRecord:
    identity: Identity
    capture_id: str
    generation: int
    state: str
    policy_path: str
    policy_sha256: str
    data: bytes
    sha256: str


@dataclass(frozen=True)
class StageReceipt:
    current_sha256: str
    policy_sha256: str
    capture_id: str
    generation: int


@dataclass(frozen=True)
class Selection:
    """An exact request selector, never a credential or a launch permission."""
    identity: Identity
    anchor_revision: int
    generation: int
    current_sha256: str


@dataclass(frozen=True)
class ReleaseObligation:
    """Exact attempted launch retained until a future native owner settles it.

    Observed callback completion does not mean the child exited. A different
    generation, boot or UUID cannot erase the prior child/resource obligation.
    """
    selection: Selection
    launch_id: str


@dataclass(frozen=True)
class ChildReceipt:
    """Test port receipt; a UUID does NOT attest real stopped-child ownership."""
    launch_id: str
    selection: Selection


@dataclass(frozen=True)
class ReleaseReceipt:
    launch_id: str
    observed_complete: bool


@dataclass(frozen=True)
class Observation:
    selection: Selection | None
    reason: str
    runtime_authorized: bool = False


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str = ""
    generation: int | None = None
    selection: Selection | None = None
    runtime_authorized: bool = False


class UnavailableBackend:
    """Default: no anchor provisioning, publication or native release exists."""
    @contextmanager
    def transaction(self):
        raise BackendUnavailable("no_qualified_backend")
        yield  # pragma: no cover - specifies a context-manager interface


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _text(value, pattern):
    return type(value) is str and bool(pattern.fullmatch(value))


def _counter(value, *, minimum=0):
    return type(value) is int and minimum <= value <= MAX_COUNTER


def _identity(value):
    if (type(value) is not Identity or not _text(value.machine_id, _MACHINE)
            or not _text(value.boot_id, _UUID)):
        raise ProtocolRefused("platform_identity_invalid")
    return value


def _anchor(value, identity):
    if (type(value) is not Anchor or value.machine_id != identity.machine_id
            or not _counter(value.revision) or not _counter(value.generation)
            or value.generation > value.revision or type(value.phase) is not str
            or value.phase not in _PHASES):
        raise ProtocolRefused("anchor_invalid_or_missing")
    if value.phase == "closed":
        valid = (value.revision == value.generation == 0
                 and value.current_sha256 is None and value.launch_id is None)
    elif value.phase == "revoked":
        valid = (value.generation > 0 and value.current_sha256 is None
                 and value.launch_id is None)
    else:
        valid = value.generation > 0 and _text(value.current_sha256, _DIGEST)
        valid = valid and (_text(value.launch_id, _UUID)
                           if value.phase == "release_attempted"
                           else value.launch_id is None)
    if not valid:
        raise ProtocolRefused("anchor_shape_invalid")
    _outstanding(value, identity)
    return value


def _selection(value):
    if (type(value) is not Selection or not _counter(value.anchor_revision, minimum=1)
            or not _counter(value.generation, minimum=1)
            or value.generation > value.anchor_revision
            or not _text(value.current_sha256, _DIGEST)):
        raise ProtocolRefused("release_selector_or_launch_invalid")
    _identity(value.identity)
    return value


def _outstanding(anchor, identity):
    obligation = anchor.outstanding
    if obligation is None:
        if anchor.phase == "release_attempted":
            raise ProtocolRefused("anchor_obligation_missing")
        return
    try:
        if type(obligation) is not ReleaseObligation or not _text(obligation.launch_id, _UUID):
            raise ProtocolRefused("invalid_obligation")
        selected = _selection(obligation.selection)
        if (anchor.phase == "closed" or selected.identity.machine_id != identity.machine_id
                or selected.anchor_revision >= anchor.revision
                or selected.generation > anchor.generation):
            raise ProtocolRefused("invalid_obligation_binding")
        if anchor.phase == "release_attempted":
            if (selected.generation != anchor.generation
                    or selected.anchor_revision + 1 != anchor.revision
                    or selected.current_sha256 != anchor.current_sha256
                    or obligation.launch_id != anchor.launch_id):
                raise ProtocolRefused("invalid_attempt_binding")
        elif selected.generation >= anchor.generation:
            raise ProtocolRefused("invalid_carried_obligation")
    except ProtocolRefused:
        raise ProtocolRefused("anchor_obligation_invalid") from None


def _pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ProtocolRefused("duplicate_record_key")
        result[name] = value
    return result


def _invalid_constant(_value):
    raise ProtocolRefused("invalid_record_constant")


def parse_current(data):
    """Exact existing eight-field record schema, with explicit bounded input."""
    if type(data) is not bytes or not 0 < len(data) <= MAX_RECORD_BYTES:
        raise ProtocolRefused("current_record_size_or_type")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_invalid_constant)
        if type(value) is not dict or set(value) != _FIELDS:
            raise ProtocolRefused("current_record_schema")
        if canonical(value) != data:
            raise ProtocolRefused("noncanonical_current_record")
        identity = _identity(Identity(value["machine_id"], value["boot_id"]))
        path = value["policy_path"]
        if (type(path) is not str or not path.startswith("/") or path == "/"
                or "\x00" in path or any(p in ("", ".", "..") for p in path.split("/")[1:])
                or len(path.encode("utf-8")) > MAX_PATH_BYTES):
            raise ProtocolRefused("policy_path_invalid")
        if (value["schema"] != CURRENT_SCHEMA or value["state"] not in ("active", "revoked")
                or not _text(value["capture_id"], _UUID)
                or not _counter(value["generation"], minimum=1)
                or not _text(value["policy_sha256"], _DIGEST)):
            raise ProtocolRefused("current_record_values")
        return CurrentRecord(identity, value["capture_id"], value["generation"],
                             value["state"], path, value["policy_sha256"], data,
                             hashlib.sha256(data).hexdigest())
    except ProtocolRefused:
        raise
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ProtocolRefused("current_record_decode") from None


class Coordinator:
    """Sequencing over explicit test ports; no runtime authority is returned.

    Backend contract:
      transaction(): one non-reentrant serialized authority across ALL clients;
      identity(), read_anchor(): authenticated identity/exact independent state;
      compare_exchange(before, after): exact durable receipt or None on conflict;
      stage_durable(record): complete bound immutable bundle -> StageReceipt;
      publish_current_durable(bytes): durable selected bytes -> exact digest;
      read_current(): exact held bytes or None, bounded by MAX_RECORD_BYTES;
      withdraw_current_durable(): durable absence (return None);
      verify_release_ready(selection, launch_id): all real native prerequisites
          and the owner's exact unreleased stopped child -> ChildReceipt;
      release_stopped_child(receipt): serialized exact-child release -> receipt.

    Every default port is unavailable. Test receipts model these requirements;
    they do not prove them. Ports must raise OutcomeUnknown for uncertain writes
    and release; OSError is conservatively uncertain too. No method reinitializes
    a missing anchor, retries a callback, rolls back an epoch, unpins or kills.
    Transaction cleanup must propagate operation exceptions without suppressing
    or downgrading them. Defensive uncertain outcomes for broken cleanup do not
    qualify an adapter that violates this contract.
    The anchor carries one outstanding attempt across epochs. Publication and
    revocation preserve it; every further release refuses. No source settlement
    port exists: an actual native lifecycle owner must qualify that separately.
    """
    def __init__(self, backend=None):
        self.backend = UnavailableBackend() if backend is None else backend

    def _read_anchor(self, identity):
        return _anchor(self.backend.read_anchor(), identity)

    def _cas(self, before, after, identity):
        _anchor(after, identity)
        if (after.revision != before.revision + 1
                or after.generation < before.generation
                or after.generation > before.generation + 1):
            raise ProtocolRefused("nonmonotonic_transition")
        if before.outstanding is not None:
            if after.outstanding != before.outstanding:
                raise ProtocolRefused("outstanding_release_must_be_preserved")
        elif after.outstanding is not None:
            expected = ReleaseObligation(Selection(identity, before.revision,
                                                  before.generation, before.current_sha256),
                                         after.launch_id)
            if (before.phase != "active" or after.phase != "release_attempted"
                    or after.outstanding != expected):
                raise ProtocolRefused("outstanding_release_binding_invalid")
        receipt = self.backend.compare_exchange(before, after)
        if receipt is None:
            raise ProtocolRefused("anchor_compare_conflict")
        try:
            _anchor(receipt, identity)
        except ProtocolRefused:
            raise OutcomeUnknown("anchor_receipt_mismatch") from None
        if receipt != after:
            raise OutcomeUnknown("anchor_receipt_mismatch")
        try:
            persisted = self._read_anchor(identity)
        except (ProtocolRefused, BackendUnavailable):
            # The exact CAS receipt already reports a committed transition.
            # Missing, malformed or unavailable confirmation cannot turn that
            # transition into a known refusal; recovery must read fresh state.
            raise OutcomeUnknown("anchor_readback_unconfirmed") from None
        if persisted != after:
            raise OutcomeUnknown("anchor_readback_mismatch")
        return after

    def _current(self, identity, expected, *, phase="active"):
        anchor = self._read_anchor(identity)
        if anchor != expected or anchor.phase != phase:
            raise ProtocolRefused("anchor_not_selected")
        record = parse_current(self.backend.read_current())
        if (record.identity != identity or record.state != "active"
                or record.generation != anchor.generation
                or record.sha256 != anchor.current_sha256):
            raise ProtocolRefused("current_anchor_binding_mismatch")
        if _identity(self.backend.identity()) != identity:
            raise ProtocolRefused("platform_identity_changed")
        # Final anchor comparison shares the SAME serialized transaction.
        if self._read_anchor(identity) != anchor:
            raise ProtocolRefused("anchor_changed_during_observation")
        return record, Selection(identity, anchor.revision, anchor.generation, record.sha256)

    def _execute(self, operation):
        result = None
        operation_uncertain = False
        try:
            with self.backend.transaction():
                try:
                    identity = _identity(self.backend.identity())
                    anchor = self._read_anchor(identity)
                    result = operation(identity, anchor)
                except (OutcomeUnknown, OSError):
                    operation_uncertain = True
                    raise
            # An adapter must propagate operation errors. If it suppresses one,
            # there is no completed result to return and no authority to infer.
            if result is None:
                return Outcome("uncertain", "transaction_suppressed_error")
            return result
        except (ProtocolRefused, BackendUnavailable) as error:
            if result is not None or operation_uncertain:
                # Cleanup cannot turn a completed or uncertain operation into
                # a refusal, including after the release callback has acted.
                return Outcome("uncertain", "transaction_exit_unconfirmed")
            return Outcome("refused", str(error))
        except (OutcomeUnknown, OSError) as error:
            return Outcome("uncertain", str(error))

    def observe(self):
        def operation(identity, anchor):
            _, selection = self._current(identity, anchor)
            return Outcome("selected", generation=anchor.generation, selection=selection)
        result = self._execute(operation)
        return Observation(result.selection, result.reason or result.status)

    def publish(self, current_bytes):
        def operation(identity, anchor):
            record = parse_current(current_bytes)
            if record.identity != identity or record.state != "active":
                raise ProtocolRefused("publication_identity_or_state")
            if anchor.generation == MAX_COUNTER or anchor.revision > MAX_COUNTER - 2:
                raise ProtocolRefused("anchor_exhausted")
            if record.generation != anchor.generation + 1:
                raise ProtocolRefused("publication_generation_not_next")
            reserved = Anchor(identity.machine_id, anchor.revision + 1,
                              record.generation, "reserved", record.sha256,
                              outstanding=anchor.outstanding)
            self._cas(anchor, reserved, identity)  # older evidence fenced FIRST
            staged = self.backend.stage_durable(record)
            expected = StageReceipt(record.sha256, record.policy_sha256,
                                    record.capture_id, record.generation)
            if (type(staged) is not StageReceipt
                    or not _counter(staged.generation, minimum=1)
                    or not _text(staged.current_sha256, _DIGEST)
                    or not _text(staged.policy_sha256, _DIGEST)
                    or not _text(staged.capture_id, _UUID) or staged != expected):
                raise ProtocolRefused("staged_bundle_receipt_mismatch")
            published = self.backend.publish_current_durable(record.data)
            if type(published) is not str or published != record.sha256:
                raise OutcomeUnknown("publication_receipt_mismatch")
            self._current(identity, reserved, phase="reserved")
            active = replace(reserved, revision=reserved.revision + 1, phase="active")
            self._cas(reserved, active, identity)
            try:
                _, selection = self._current(identity, active)
            except (ProtocolRefused, BackendUnavailable):
                # Activation already committed. A failed final observation
                # cannot establish that publication was refused or rolled back.
                raise OutcomeUnknown("publication_readback_unconfirmed") from None
            return Outcome("published", generation=record.generation, selection=selection)
        return self._execute(operation)

    def revoke(self):
        """Advance/fence independent authority before withdrawing depot bytes."""
        def operation(identity, anchor):
            if anchor.generation == MAX_COUNTER or anchor.revision == MAX_COUNTER:
                raise ProtocolRefused("anchor_exhausted")
            revoked = Anchor(identity.machine_id, anchor.revision + 1,
                             anchor.generation + 1, "revoked", outstanding=anchor.outstanding)
            self._cas(anchor, revoked, identity)
            self.backend.withdraw_current_durable()
            try:
                if self.backend.read_current() is not None:
                    raise OutcomeUnknown("withdrawal_not_confirmed")
                if (self._read_anchor(identity) != revoked
                        or _identity(self.backend.identity()) != identity):
                    raise OutcomeUnknown("revocation_readback_mismatch")
            except (ProtocolRefused, BackendUnavailable):
                raise OutcomeUnknown("revocation_readback_unconfirmed") from None
            return Outcome("revoked", generation=revoked.generation)
        return self._execute(operation)

    def attempt_release(self, selection, launch_id):
        """One outstanding durable attempt; selector is never permission."""
        def operation(identity, anchor):
            _selection(selection)
            if not _text(launch_id, _UUID):
                raise ProtocolRefused("release_selector_or_launch_invalid")
            if anchor.outstanding is not None:
                raise ProtocolRefused("prior_release_unresolved")
            _, current = self._current(identity, anchor)
            if current != selection:
                raise ProtocolRefused("stale_release_selection")
            child = self.backend.verify_release_ready(selection, launch_id)
            expected = ChildReceipt(launch_id, selection)
            if type(child) is not ChildReceipt or not _text(child.launch_id, _UUID):
                raise ProtocolRefused("exact_child_receipt_mismatch")
            _selection(child.selection)
            if child != expected:
                raise ProtocolRefused("exact_child_receipt_mismatch")
            _, current = self._current(identity, anchor)
            if current != selection:
                raise ProtocolRefused("release_selection_changed")
            if anchor.revision == MAX_COUNTER:
                raise ProtocolRefused("anchor_exhausted")
            attempted = replace(anchor, revision=anchor.revision + 1,
                                phase="release_attempted", launch_id=launch_id,
                                outstanding=ReleaseObligation(selection, launch_id))
            self._cas(anchor, attempted, identity)  # persists before any release
            self._current(identity, attempted, phase="release_attempted")
            receipt = self.backend.release_stopped_child(child)
            if (type(receipt) is not ReleaseReceipt or receipt.launch_id != launch_id
                    or not _text(receipt.launch_id, _UUID)
                    or type(receipt.observed_complete) is not bool
                    or not receipt.observed_complete):
                raise OutcomeUnknown("release_not_confirmed")
            return Outcome("release_observed", generation=anchor.generation)
        return self._execute(operation)
