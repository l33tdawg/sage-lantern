"""Disabled lifecycle-settlement owner facade.

Composes the release binding, durable journal and native probe into one
source-level owner. It is not a service, installer or activation path.
"""
from __future__ import annotations

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


L = _load("sage_lantern_lifecycle_settlement", "lifecycle_settlement.py")
N = _load("sage_lantern_native_probe", "native_probe.py")


class SettlementOwner:
    """The disabled owner. A caller supplies a journal and a native probe."""
    def __init__(self, journal, probe):
        if journal is None or probe is None:
            raise ValueError("journal_and_probe_required")
        self.journal = journal
        self.probe = probe

    def track_release(self, release, capture_id, policy_sha256, resources):
        obligation = L.bind_release(
            release, capture_id, policy_sha256, resources)
        attempt = self.journal.record_attempt(obligation)
        if getattr(attempt, "status", None) != "attempted":
            reason = getattr(attempt, "reason", "") or "attempt_not_durable"
            raise L.SettlementRefused(reason)
        return obligation, attempt

    def settle(self, obligation):
        return N.settle_from_probe(self.journal, self.probe, obligation)

    def recover(self):
        """Probe durable attempts after a trusted-owner restart."""
        outcomes = []
        try:
            pending = self.journal.pending()
        except (ValueError, RuntimeError, OSError) as error:
            return (N.ProbeOutcome("uncertain", str(error)),)
        for record in pending:
            payload = record.obligation
            try:
                if type(payload) is not dict or set(payload) != {
                        "binding", "resources"}:
                    raise ValueError("journal_obligation_invalid")
                binding = payload["binding"]
                if type(binding) is not dict or set(binding) != {
                        "machine_id", "boot_id", "capture_id", "generation",
                        "policy_sha256", "launch_id"}:
                    raise ValueError("journal_obligation_invalid")
                resources = payload["resources"]
                if type(resources) is not list:
                    raise ValueError("journal_obligation_invalid")
                obligation = L.SettlementObligation(
                    L.LaunchBinding(**binding),
                    tuple(L.ResourceRef(item["kind"], item["identity"])
                          for item in resources))
            except (KeyError, TypeError, ValueError):
                outcomes.append(N.ProbeOutcome(
                    "uncertain", "journal_obligation_invalid"))
                continue
            outcomes.append(self.settle(obligation))
        return tuple(outcomes)
