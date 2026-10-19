"""Disabled lifecycle-settlement contract for the native launch authority.

This module validates a proof that an attempted release and every resource it
owned are terminal. It has no filesystem, process, socket, device or loader
access. The default backend refuses. A successful result is a source-level
receipt, never runtime authority, and it does not settle anything on a unit.

The native owner must persist settlement in the same serialized authority that
records the attempt. This Python contract does not supply that owner, durable
storage, a pidfd probe or a cgroup probe.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_MACHINE = re.compile(r"[0-9a-f]{32}\Z")
_MAX_TEXT = 256

TERMINAL_STATES = {
    "pidfd": frozenset({"dead"}),
    "child": frozenset({"reaped", "absent"}),
    "cgroup": frozenset({"absent", "empty"}),
    "fd": frozenset({"closed"}),
    "socket": frozenset({"closed", "absent"}),
    "mount": frozenset({"unmounted", "absent"}),
}


class SettlementRefused(ValueError):
    """Known invalid evidence. Never permission to settle."""


class SettlementBackendUnavailable(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _text(value, pattern=None):
    if type(value) is not str or not 0 < len(value) <= _MAX_TEXT or "\x00" in value:
        return False
    return pattern is None or bool(pattern.fullmatch(value))


def _positive(value):
    return type(value) is int and 1 <= value <= 2**64 - 1


@dataclass(frozen=True)
class LaunchBinding:
    """The exact launch the obligation belongs to."""
    machine_id: str
    boot_id: str
    capture_id: str
    generation: int
    policy_sha256: str
    launch_id: str


@dataclass(frozen=True)
class ResourceRef:
    """One owned kernel or lifecycle resource."""
    kind: str
    identity: str


@dataclass(frozen=True)
class SettlementObligation:
    """The unresolved attempt, bound to the launch and its exact resources."""
    binding: LaunchBinding
    resources: tuple[ResourceRef, ...]


def bind_release(release, capture_id, policy_sha256, resources):
    """Map a frozen release attempt to a settlement obligation.

    `release` is the release record's selection and launch UUID. This function
    does not validate or settle it; `SettlementAuthority` does that later.
    """
    selection = getattr(release, "selection", None)
    identity = getattr(selection, "identity", None)
    if (identity is None or not _text(getattr(identity, "machine_id", None))
            or not _text(getattr(identity, "boot_id", None))
            or not _positive(getattr(selection, "generation", None))
            or not _text(getattr(release, "launch_id", None))):
        raise SettlementRefused("release_binding_invalid")
    if type(resources) is not tuple or not resources:
        raise SettlementRefused("release_resources_missing")
    return SettlementObligation(
        LaunchBinding(
            identity.machine_id, identity.boot_id, capture_id,
            selection.generation, policy_sha256, release.launch_id),
        resources)


@dataclass(frozen=True)
class ResourceObservation:
    """A terminal observation for one expected resource."""
    resource: ResourceRef
    state: str
    evidence_sha256: str


@dataclass(frozen=True)
class SettlementProof:
    """Evidence presented by the native owner for validation."""
    obligation_sha256: str
    observed_machine_id: str
    observed_boot_id: str
    owner: str
    observations: tuple[ResourceObservation, ...]
    proof_sha256: str

    def unsigned(self):
        return {
            "obligation_sha256": self.obligation_sha256,
            "observed_machine_id": self.observed_machine_id,
            "observed_boot_id": self.observed_boot_id,
            "owner": self.owner,
            "observations": [
                {
                    "resource": {
                        "kind": item.resource.kind,
                        "identity": item.resource.identity,
                    },
                    "state": item.state,
                    "evidence_sha256": item.evidence_sha256,
                }
                for item in self.observations
            ],
        }

    def expected_sha256(self):
        return hashlib.sha256(canonical(self.unsigned())).hexdigest()


@dataclass(frozen=True)
class SettlementReceipt:
    """Source-level proof acceptance; persistence is the native owner's job."""
    binding: LaunchBinding
    obligation_sha256: str
    proof_sha256: str
    runtime_authorized: bool = False


@dataclass(frozen=True)
class SettlementOutcome:
    status: str
    reason: str = ""
    receipt: SettlementReceipt | None = None


class UnavailableBackend:
    """Default: no native probe or persisted settlement exists."""
    def read_settlement_proof(self, obligation):
        raise SettlementBackendUnavailable("no_qualified_settlement_backend")


def _binding(value):
    if (type(value) is not LaunchBinding
            or not _text(value.machine_id, _MACHINE)
            or not _text(value.boot_id, _UUID)
            or not _text(value.capture_id, _UUID)
            or not _positive(value.generation)
            or not _text(value.policy_sha256, _DIGEST)
            or not _text(value.launch_id, _UUID)):
        raise SettlementRefused("launch_binding_invalid")
    return value


def _resource(value):
    if (type(value) is not ResourceRef or value.kind not in TERMINAL_STATES
            or not _text(value.identity)):
        raise SettlementRefused("resource_ref_invalid")
    return value


def _obligation(value):
    if type(value) is not SettlementObligation:
        raise SettlementRefused("obligation_invalid")
    binding = _binding(value.binding)
    if type(value.resources) is not tuple or not value.resources:
        raise SettlementRefused("obligation_resources_missing")
    if len(set(value.resources)) != len(value.resources):
        raise SettlementRefused("obligation_resources_duplicated")
    for item in value.resources:
        _resource(item)
    if not any(item.kind in ("pidfd", "child") for item in value.resources):
        raise SettlementRefused("obligation_missing_child_identity")
    return binding


def _proof(obligation, value):
    if type(value) is not SettlementProof:
        raise SettlementRefused("settlement_proof_invalid")
    if not _text(value.obligation_sha256, _DIGEST):
        raise SettlementRefused("obligation_digest_invalid")
    if value.obligation_sha256 != hashlib.sha256(canonical({
            "binding": {
                "machine_id": obligation.binding.machine_id,
                "boot_id": obligation.binding.boot_id,
                "capture_id": obligation.binding.capture_id,
                "generation": obligation.binding.generation,
                "policy_sha256": obligation.binding.policy_sha256,
                "launch_id": obligation.binding.launch_id,
            },
            "resources": [
                {"kind": item.kind, "identity": item.identity}
                for item in obligation.resources
            ],
        })).hexdigest():
        raise SettlementRefused("obligation_digest_mismatch")
    if value.observed_machine_id != obligation.binding.machine_id:
        raise SettlementRefused("settlement_machine_mismatch")
    if not _text(value.observed_boot_id, _UUID) or not _text(value.owner):
        raise SettlementRefused("settlement_owner_or_boot_invalid")
    if type(value.observations) is not tuple or not value.observations:
        raise SettlementRefused("settlement_observations_missing")
    expected = set(obligation.resources)
    seen = set()
    for item in value.observations:
        if type(item) is not ResourceObservation:
            raise SettlementRefused("resource_observation_invalid")
        _resource(item.resource)
        if item.resource not in expected:
            raise SettlementRefused("unexpected_resource_observation")
        if item.resource in seen:
            raise SettlementRefused("duplicate_resource_observation")
        seen.add(item.resource)
        if not _text(item.state) or item.state not in TERMINAL_STATES[item.resource.kind]:
            raise SettlementRefused("resource_not_terminal")
        if not _text(item.evidence_sha256, _DIGEST):
            raise SettlementRefused("resource_evidence_invalid")
    if seen != expected:
        raise SettlementRefused("resource_observation_missing")
    if not _text(value.proof_sha256, _DIGEST):
        raise SettlementRefused("proof_digest_invalid")
    if value.proof_sha256 != value.expected_sha256():
        raise SettlementRefused("proof_digest_mismatch")
    return value


class SettlementAuthority:
    """Validate settlement evidence; never settle on uncertainty."""
    def __init__(self, backend=None):
        self.backend = UnavailableBackend() if backend is None else backend
        self._settled = set()

    def settle(self, obligation, proof=None):
        try:
            binding = _obligation(obligation)
            obligation_sha256 = hashlib.sha256(canonical({
                "binding": {
                    "machine_id": obligation.binding.machine_id,
                    "boot_id": obligation.binding.boot_id,
                    "capture_id": obligation.binding.capture_id,
                    "generation": obligation.binding.generation,
                    "policy_sha256": obligation.binding.policy_sha256,
                    "launch_id": obligation.binding.launch_id,
                },
                "resources": [
                    {"kind": item.kind, "identity": item.identity}
                    for item in obligation.resources
                ],
            })).hexdigest()
            if obligation_sha256 in self._settled:
                raise SettlementRefused("already_settled")
            if proof is None:
                proof = self.backend.read_settlement_proof(obligation)
            _proof(obligation, proof)
            receipt = SettlementReceipt(binding, obligation_sha256,
                                        proof.proof_sha256)
            self._settled.add(obligation_sha256)
            return SettlementOutcome("settled", receipt=receipt)
        except (SettlementRefused, SettlementBackendUnavailable, OSError) as error:
            return SettlementOutcome("uncertain", str(error))
