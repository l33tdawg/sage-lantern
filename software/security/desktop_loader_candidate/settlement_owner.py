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
        return obligation, self.journal.record_attempt(obligation)

    def settle(self, obligation):
        return N.settle_from_probe(self.journal, self.probe, obligation)
