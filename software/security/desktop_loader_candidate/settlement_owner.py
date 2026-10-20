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
A = _load("sage_lantern_settlement_anchor", "settlement_anchor.py")


class SettlementOwner:
    """The disabled owner. A caller supplies a journal and a native probe."""
    def __init__(self, journal, probe, anchor=None, owner=None):
        if journal is None or probe is None:
            raise ValueError("journal_and_probe_required")
        self.journal = journal
        self.probe = probe
        self.anchor = anchor
        self.owner_sha256 = None if owner is None else A.owner_digest(owner)
        try:
            self.anchor_value = None if anchor is None else anchor.read()
        except (A.AnchorRefused, OSError):
            # A stale/missing counter or malformed seal is an uncertain
            # recovery state, never an empty journal that permits a new launch.
            self.anchor_value = None

    def initialize_anchor(self):
        """Provision an empty journal seal once; never rebase existing work."""
        if self.anchor is None or self.anchor_value is not None:
            return False
        if self.journal.records():
            raise A.AnchorRefused("anchor_initialization_requires_empty_journal")
        value = A.AnchorValue(
            1, A.journal_digest(()), 0, A.release_set_digest(()),
            self.owner_sha256)
        created = self.anchor.create(value)
        if created is None:
            current = self.anchor.read()
            if current != value:
                return False
        self.anchor_value = created or value
        return True

    def _verify_anchor(self, records):
        if self.anchor is None:
            return True
        value = self.anchor_value
        if value is None:
            return False
        return ((self.owner_sha256 is None
                 or value.owner_sha256 == self.owner_sha256)
                and value.journal_sha256 == A.journal_digest(records)
                and value.record_count == len(records)
                and value.release_sha256 == A.release_set_digest(records))

    def _advance_anchor(self, records):
        if self.anchor is None:
            return True
        before = self.anchor_value
        if before is None:
            return False
        after = A.AnchorValue(
            before.generation + 1, A.journal_digest(records),
            len(records), A.release_set_digest(records),
            self.owner_sha256)
        try:
            written = self.anchor.compare_exchange(before, after)
        except (A.AnchorRefused, OSError):
            return False
        if written is None:
            return False
        self.anchor_value = written
        return True

    def track_release(self, release, capture_id, policy_sha256, resources):
        if self.anchor is not None and not self._verify_anchor(
                self.journal.records()):
            raise L.SettlementRefused("settlement_anchor_mismatch")
        obligation = L.bind_release(
            release, capture_id, policy_sha256, resources)
        attempt = self.journal.record_attempt(obligation)
        if getattr(attempt, "status", None) != "attempted":
            reason = getattr(attempt, "reason", "") or "attempt_not_durable"
            raise L.SettlementRefused(reason)
        if self.anchor is not None and not self._advance_anchor(
                self.journal.records()):
            raise L.SettlementRefused("settlement_anchor_not_advanced")
        return obligation, attempt

    def settle(self, obligation):
        outcome = N.settle_from_probe(self.journal, self.probe, obligation)
        if (outcome.status == "settled" and self.anchor is not None
                and not self._advance_anchor(self.journal.records())):
            return N.ProbeOutcome(
                "uncertain", "settlement_anchor_not_advanced", outcome.journal)
        return outcome

    def recover(self):
        """Probe durable attempts after a trusted-owner restart."""
        outcomes = []
        try:
            records = self.journal.records()
            pending = self.journal.pending()
        except (ValueError, RuntimeError, OSError) as error:
            return (N.ProbeOutcome("uncertain", str(error)),)
        if not self._verify_anchor(records):
            return (N.ProbeOutcome("uncertain", "settlement_anchor_mismatch"),)
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
