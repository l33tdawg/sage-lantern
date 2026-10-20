"""Validated launch selection from the frozen GNOME capture decision.

This module grants no launch, settlement or device authority. It only proves
that a trusted consumer's eligible decision agrees with the exact protected
capture policy and current platform identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re


SCHEMA = "sage.lantern.root-gnome-capture-policy.v1"
_FIELDS = {
    "schema", "contract_version", "machine_id", "boot_id", "capture_id",
    "generation", "authority_file", "source_directory", "capture_directory",
    "consumer_sha256", "python_sha256", "payload_digests",
}
_MACHINE = re.compile(r"[0-9a-f]{32}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class LaunchSelectionRefused(ValueError):
    pass


@dataclass(frozen=True)
class LaunchSelection:
    machine_id: str
    boot_id: str
    capture_id: str
    generation: int
    policy_sha256: str
    analysis_view_digest: str
    unit_path: str
    runtime_authorized: bool = False


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise LaunchSelectionRefused("duplicate_json_key")
        value[key] = item
    return value


def from_capture_decision(policy_path, decision, *, read_file, platform_identity):
    """Bind one eligible consumer decision to the held policy bytes.

    ``decision`` must come from the reviewed frozen-consumer process. It is not
    a credential: this function still requires the exact root-owned policy bytes
    and current machine/boot identity.
    """
    if (type(policy_path) is not str or not policy_path.startswith("/")
            or getattr(decision, "evidence_consumable", None) is not True
            or getattr(decision, "reasons", None) != ()
            or getattr(decision, "runtime_authorized", None) is not False):
        raise LaunchSelectionRefused("capture_not_eligible")
    machine_id, boot_id = platform_identity
    if (type(machine_id) is not str or not _MACHINE.fullmatch(machine_id)
            or type(boot_id) is not str or not _UUID.fullmatch(boot_id)):
        raise LaunchSelectionRefused("platform_identity_invalid")
    raw = read_file(policy_path)
    if type(raw) is not bytes or not raw or len(raw) > 32768:
        raise LaunchSelectionRefused("capture_policy_invalid")
    policy_digest = hashlib.sha256(raw).hexdigest()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeDecodeError, ValueError, LaunchSelectionRefused):
        raise LaunchSelectionRefused("capture_policy_decode") from None
    if (type(value) is not dict or set(value) != _FIELDS
            or value["schema"] != SCHEMA or value["contract_version"] != 3
            or value["machine_id"] != machine_id or value["boot_id"] != boot_id
            or type(value["capture_id"]) is not str
            or not _UUID.fullmatch(value["capture_id"])
            or type(value["generation"]) is not int
            or not 1 <= value["generation"] < 2**64):
        raise LaunchSelectionRefused("capture_policy_mismatch")
    if (getattr(decision, "policy_sha256", None) != policy_digest
            or getattr(decision, "capture_id", None) != value["capture_id"]
            or getattr(decision, "generation", None) != value["generation"]):
        raise LaunchSelectionRefused("capture_decision_policy_mismatch")
    view_digest = getattr(decision, "analysis_view_digest", None)
    unit_path = getattr(decision, "unit_path", None)
    if (type(view_digest) is not str or not _DIGEST.fullmatch(view_digest)
            or type(unit_path) is not str or not unit_path.startswith("/")
            or len(unit_path) > 4096 or "\x00" in unit_path):
        raise LaunchSelectionRefused("capture_decision_result_invalid")
    return LaunchSelection(
        machine_id, boot_id, value["capture_id"], value["generation"],
        policy_digest, view_digest, unit_path)
