"""Bind a broker-staged child to the durable settlement owner."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys


def _load(name, filename):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


L = _load("sage_lantern_lifecycle_settlement_for_recorder",
          "lifecycle_settlement.py")


@dataclass(frozen=True)
class _Identity:
    machine_id: str
    boot_id: str


@dataclass(frozen=True)
class _Selection:
    identity: _Identity
    generation: int


@dataclass(frozen=True)
class _Release:
    selection: _Selection
    launch_id: str


class LaunchSettlementRecorder:
    """Persist one stopped child before the broker release callback."""
    def __init__(self, owner):
        if owner is None:
            raise ValueError("settlement_owner_required")
        self.owner = owner

    def __call__(self, child, request, selection):
        if (request.capture_id != selection.capture_id
                or request.generation != selection.generation
                or request.policy_sha256 != selection.policy_sha256
                or not 1 <= child.start_ticks < 2**64):
            raise L.SettlementRefused("launch_selection_changed")
        release = _Release(
            _Selection(
                _Identity(selection.machine_id, selection.boot_id),
                selection.generation),
            request.launch_id)
        resource = L.ResourceRef(
            "child", f"child:{child.pid}:{child.start_ticks}")
        obligation, attempt = self.owner.track_release(
            release, selection.capture_id, selection.policy_sha256, (resource,))
        if attempt.status != "attempted":
            raise L.SettlementRefused(
                getattr(attempt, "reason", "") or "attempt_not_durable")
        return True
