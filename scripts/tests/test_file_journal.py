"""Tests for the file-backed durable settlement journal."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
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


J = load("settlement_journal_for_file", "settlement_journal.py")
DIGEST = "a" * 64
PROOF = "b" * 64


def attempted():
    return J.SettlementRecord(DIGEST, "attempted", 0)


def settled():
    return J.SettlementRecord(
        DIGEST, "settled", 1, PROOF,
        J.settlement_digest(DIGEST, PROOF))


class FileJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.backend = J.FileJournalBackend(self.tmp)

    def test_create_read_and_persist(self):
        record = attempted()
        self.assertEqual(self.backend.create(record), record)
        self.assertEqual(self.backend.read(DIGEST), record)
        reopened = J.FileJournalBackend(self.tmp)
        self.assertEqual(reopened.read(DIGEST), record)

    def test_create_is_exclusive(self):
        self.assertIsNotNone(self.backend.create(attempted()))
        self.assertIsNone(self.backend.create(attempted()))

    def test_compare_exchange_and_conflict(self):
        record = attempted()
        self.backend.create(record)
        after = settled()
        self.assertEqual(self.backend.compare_exchange(record, after), after)
        self.assertIsNone(self.backend.compare_exchange(record, after))

    def test_private_directory_required(self):
        os.chmod(self.tmp, 0o755)
        with self.assertRaises(J.JournalRefused):
            J.FileJournalBackend(self.tmp)

    def test_symlink_record_refused(self):
        target = Path(self.tmp) / (DIGEST + ".json")
        target.symlink_to("/tmp/not-a-record")
        with self.assertRaises(J.JournalRefused):
            self.backend.read(DIGEST)

    def test_failure_before_replace_leaves_no_record(self):
        original = J.os.replace
        J.os.replace = lambda *_args: (_ for _ in ()).throw(
            OSError("replace failed"))
        try:
            with self.assertRaises(OSError):
                self.backend.create(attempted())
        finally:
            J.os.replace = original
        self.assertIsNone(self.backend.read(DIGEST))
        temp_files = [name for name in os.listdir(self.tmp)
                      if name.endswith(".tmp")]
        self.assertEqual(temp_files, [])

    def test_failure_after_replace_is_visible_on_readback(self):
        original = J.os.replace

        def replace_then_fail(source, target):
            original(source, target)
            raise OSError("replace reported failure after commit")

        J.os.replace = replace_then_fail
        try:
            with self.assertRaises(OSError):
                self.backend.create(attempted())
        finally:
            J.os.replace = original
        self.assertEqual(self.backend.read(DIGEST), attempted())

    def test_concurrent_create_has_one_winner(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: self.backend.create(attempted()), range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.backend.read(DIGEST), attempted())


if __name__ == "__main__":
    unittest.main()
