"""Tests for the disabled lifecycle settlement journal."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "software/security/desktop_loader_candidate"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


L = load("lifecycle_settlement_for_journal", "lifecycle_settlement.py")
J = load("settlement_journal_tested", "settlement_journal.py")

MACHINE = "a" * 32
BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CAPTURE = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
LAUNCH = "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc"
BINDING = L.LaunchBinding(MACHINE, BOOT, CAPTURE, 7, "1" * 64, LAUNCH)
RESOURCES = (
    L.ResourceRef("pidfd", "pidfd:4242"),
    L.ResourceRef("cgroup", "/sys/fs/cgroup/sage-session-7"),
    L.ResourceRef("socket", "unix:/run/sage/native-7"),
)
OBLIGATION = L.SettlementObligation(BINDING, RESOURCES)


def make_proof(evidence="2" * 64):
    digest = J.obligation_digest(OBLIGATION)
    observations = tuple(
        L.ResourceObservation(item, state, evidence)
        for item, state in zip(RESOURCES, ("dead", "empty", "closed")))
    unsigned = L.SettlementProof(digest, MACHINE, BOOT, "native-owner",
                                 observations, "0" * 64)
    return L.SettlementProof(digest, MACHINE, BOOT, "native-owner",
                             observations, unsigned.expected_sha256())


class AuthorityBackend:
    def __init__(self, proof):
        self.proof = proof

    def read_settlement_proof(self, obligation):
        return self.proof


class JournalBackend:
    def __init__(self):
        self.records = {}
        self.create_fault = None
        self.cas_fault = None

    def read(self, digest):
        return self.records.get(digest)

    def create(self, record):
        if self.create_fault == "before":
            raise J.JournalUnknown("create_before")
        if record.obligation_sha256 in self.records:
            return None
        self.records[record.obligation_sha256] = record
        if self.create_fault == "after":
            raise J.JournalUnknown("create_after")
        return record

    def compare_exchange(self, before, after):
        if self.cas_fault == "before":
            raise J.JournalUnknown("cas_before")
        current = self.records.get(before.obligation_sha256)
        if current != before:
            return None
        self.records[after.obligation_sha256] = after
        if self.cas_fault == "after":
            raise J.JournalUnknown("cas_after")
        return after


def journal(backend, proof=None):
    authority = L.SettlementAuthority(AuthorityBackend(proof or make_proof()))
    return J.SettlementJournal(backend, authority)


class JournalTests(unittest.TestCase):
    def test_attempt_then_settle(self):
        backend = JournalBackend()
        j = journal(backend)
        attempt = j.record_attempt(OBLIGATION)
        self.assertEqual(attempt.status, "attempted")
        result = j.settle(OBLIGATION, make_proof())
        self.assertEqual(result.status, "settled")
        self.assertEqual(result.record.revision, 1)
        self.assertFalse(result.runtime_authorized)

    def test_second_settle_is_idempotent(self):
        backend = JournalBackend()
        j = journal(backend)
        j.record_attempt(OBLIGATION)
        first = j.settle(OBLIGATION, make_proof())
        second = j.settle(OBLIGATION, make_proof())
        self.assertEqual(first.status, "settled")
        self.assertEqual(second.status, "settled")
        self.assertEqual(second.record.revision, 1)

    def test_different_proof_does_not_replace_settlement(self):
        backend = JournalBackend()
        j = journal(backend)
        j.record_attempt(OBLIGATION)
        j.settle(OBLIGATION, make_proof("2" * 64))
        other = journal(backend, make_proof("3" * 64)).settle(
            OBLIGATION, make_proof("3" * 64))
        self.assertEqual(other.status, "uncertain")
        self.assertIn("different_proof", other.reason)

    def test_settle_without_attempt_is_refused(self):
        result = journal(JournalBackend()).settle(OBLIGATION, make_proof())
        self.assertEqual(result.status, "uncertain")
        self.assertIn("attempt_not_recorded", result.reason)

    def test_cas_conflict_preserves_attempt(self):
        backend = JournalBackend()
        j = journal(backend)
        j.record_attempt(OBLIGATION)
        original = backend.compare_exchange
        backend.compare_exchange = lambda before, after: None
        result = j.settle(OBLIGATION, make_proof())
        backend.compare_exchange = original
        self.assertEqual(result.status, "uncertain")
        self.assertIn("cas_conflict", result.reason)
        self.assertEqual(backend.read(J.obligation_digest(OBLIGATION)).state,
                         "attempted")

    def test_unknown_write_after_commit_is_recovered_by_readback(self):
        backend = JournalBackend()
        j = journal(backend)
        j.record_attempt(OBLIGATION)
        backend.cas_fault = "after"
        result = j.settle(OBLIGATION, make_proof())
        self.assertEqual(result.status, "uncertain")
        backend.cas_fault = None
        retry = j.settle(OBLIGATION, make_proof())
        self.assertEqual(retry.status, "settled")

    def test_unknown_write_before_commit_preserves_attempt(self):
        backend = JournalBackend()
        j = journal(backend)
        j.record_attempt(OBLIGATION)
        backend.cas_fault = "before"
        result = j.settle(OBLIGATION, make_proof())
        self.assertEqual(result.status, "uncertain")
        self.assertEqual(backend.read(J.obligation_digest(OBLIGATION)).state,
                         "attempted")

    def test_invalid_proof_writes_nothing(self):
        backend = JournalBackend()
        j = journal(backend, make_proof())
        j.record_attempt(OBLIGATION)
        bad = make_proof("2" * 64)
        bad = L.SettlementProof(
            bad.obligation_sha256, bad.observed_machine_id,
            bad.observed_boot_id, bad.owner, bad.observations, "f" * 64)
        result = j.settle(OBLIGATION, bad)
        self.assertEqual(result.status, "uncertain")
        self.assertEqual(backend.read(J.obligation_digest(OBLIGATION)).state,
                         "attempted")

    def test_unavailable_backend_is_uncertain(self):
        result = J.SettlementJournal().record_attempt(OBLIGATION)
        self.assertEqual(result.status, "uncertain")

    def test_attempt_creation_is_idempotent(self):
        backend = JournalBackend()
        j = journal(backend)
        first = j.record_attempt(OBLIGATION)
        second = j.record_attempt(OBLIGATION)
        self.assertEqual(first.status, "attempted")
        self.assertEqual(second.status, "attempted")
        self.assertEqual(first.record, second.record)


if __name__ == "__main__":
    unittest.main()
