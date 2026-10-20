"""Independent settlement-journal anchor controls."""
from pathlib import Path
import importlib.util
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


A = load("settlement_anchor_tested", "settlement_anchor.py")
J = load("settlement_journal_for_anchor", "settlement_journal.py")
SettlementRecord = J.SettlementRecord
FileAnchorStore = A.FileAnchorStore


def records(*states):
    return tuple(
        SettlementRecord(str(index) * 64, state, 0)
        for index, state in enumerate(states, 1))


class AnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Path(self.tmp.name).chmod(0o700)
        self.store = FileAnchorStore(self.tmp.name)

    def test_anchor_detects_record_set_change_and_rollback(self):
        initial = records("attempted", "attempted")
        value = A.advance(self.store, initial)
        self.assertEqual(self.store.read(), value)
        self.assertEqual(A.advance(self.store, initial), value)
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_journal_mismatch"):
            A.advance(self.store, records("attempted"))
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_journal_mismatch"):
            A.advance(self.store, records("attempted", "settled"))

    def test_anchor_binds_outstanding_release_identity(self):
        initial = records("attempted")
        A.advance(self.store, initial, release_sha256="a" * 64)
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_journal_mismatch"):
            A.advance(self.store, initial, release_sha256="b" * 64)

    def test_cas_cannot_roll_generation_back(self):
        first = self.store.create(A.AnchorValue(1, "a" * 64, 0))
        self.assertEqual(first.generation, 1)
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_generation_mismatch"):
            self.store.compare_exchange(first, A.AnchorValue(1, "b" * 64, 0))

    def test_record_payload_is_part_of_seal(self):
        first = records("attempted")
        second = (SettlementRecord("1" * 64, "attempted", 0, obligation={
            "binding": {}, "resources": []}),)
        self.assertNotEqual(A.journal_digest(first), A.journal_digest(second))


if __name__ == "__main__":
    unittest.main()
