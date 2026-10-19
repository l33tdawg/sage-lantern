"""Tests for the disabled native probe contract."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "software/security/desktop_loader_candidate"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


L = load("lifecycle_settlement_for_probe", "lifecycle_settlement.py")
J = load("settlement_journal_for_probe", "settlement_journal.py")
N = load("native_probe_tested", "native_probe.py")

MACHINE = "a" * 32
BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CAPTURE = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
LAUNCH = "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc"
BINDING = L.LaunchBinding(MACHINE, BOOT, CAPTURE, 7, "1" * 64, LAUNCH)
RESOURCES = (
    L.ResourceRef("pidfd", "pidfd:4242"),
    L.ResourceRef("cgroup", "/sys/fs/cgroup/sage-session-7"),
)
OBLIGATION = L.SettlementObligation(BINDING, RESOURCES)


def make_proof():
    digest = J.obligation_digest(OBLIGATION)
    observations = (
        L.ResourceObservation(RESOURCES[0], "dead", "2" * 64),
        L.ResourceObservation(RESOURCES[1], "empty", "2" * 64),
    )
    unsigned = L.SettlementProof(digest, MACHINE, BOOT, "native-owner",
                                 observations, "0" * 64)
    return L.SettlementProof(digest, MACHINE, BOOT, "native-owner",
                             observations, unsigned.expected_sha256())


class ProofBackend:
    def __init__(self, proof):
        self.proof = proof

    def read_settlement_proof(self, obligation):
        return self.proof


class Probe(N.NativeProbe):
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def observe(self, obligation):
        if self.error:
            raise self.error
        return self.result


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def journal(self, proof):
        backend = J.FileJournalBackend(self.tmp)
        authority = L.SettlementAuthority(ProofBackend(proof))
        return J.SettlementJournal(backend, authority)

    def test_unavailable_probe_is_uncertain(self):
        result = N.settle_from_probe(self.journal(make_proof()),
                                     N.UnavailableProbe(), OBLIGATION)
        self.assertEqual(result.status, "uncertain")
        self.assertIn("native_probe", result.reason)

    def test_valid_probe_settles_through_journal(self):
        proof = make_proof()
        j = self.journal(proof)
        self.assertEqual(j.record_attempt(OBLIGATION).status, "attempted")
        result = N.settle_from_probe(
            j, Probe(N.ProbeResult(proof)), OBLIGATION)
        self.assertEqual(result.status, "settled")
        self.assertEqual(result.journal.status, "settled")

    def test_probe_error_is_uncertain(self):
        result = N.settle_from_probe(
            self.journal(make_proof()), Probe(error=OSError("probe failed")),
            OBLIGATION)
        self.assertEqual(result.status, "uncertain")
        self.assertIn("probe failed", result.reason)

    def test_bad_probe_result_is_uncertain(self):
        result = N.settle_from_probe(self.journal(make_proof()), Probe("bad"),
                                     OBLIGATION)
        self.assertEqual(result.status, "uncertain")
        self.assertIn("result_invalid", result.reason)


if __name__ == "__main__":
    unittest.main()
