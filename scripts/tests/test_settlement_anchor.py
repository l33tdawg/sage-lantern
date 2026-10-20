"""Independent settlement-journal anchor controls."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
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
        second = self.store.compare_exchange(
            first, A.AnchorValue(2, "b" * 64, 0))
        self.assertEqual(second.generation, 2)
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_generation_mismatch"):
            self.store.compare_exchange(second, A.AnchorValue(2, "c" * 64, 0))

    def test_record_payload_is_part_of_seal(self):
        first = records("attempted")
        second = (SettlementRecord("1" * 64, "attempted", 0, obligation={
            "binding": {}, "resources": []}),)
        self.assertNotEqual(A.journal_digest(first), A.journal_digest(second))

    def test_owner_policy_is_private_and_typed(self):
        owner = A.OwnerIdentity(
            "a" * 32, "sage-native-owner", "b" * 64, os.geteuid())
        path = Path(self.tmp.name) / "owner.json"
        path.write_text(json.dumps({
            "schema": A.OWNER_SCHEMA,
            "machine_id": owner.machine_id,
            "owner_id": owner.owner_id,
            "executable_sha256": owner.executable_sha256,
            "uid": owner.uid,
        }) + "\n")
        path.chmod(0o600)
        self.assertEqual(A.load_owner(path, os.geteuid()), owner)
        path.chmod(0o644)
        with self.assertRaisesRegex(A.AnchorRefused, "owner_policy_identity"):
            A.load_owner(path, os.geteuid())
        path.chmod(0o600)
        link = Path(self.tmp.name) / "owner-link.json"
        link.symlink_to(path)
        with self.assertRaisesRegex(A.AnchorRefused, "owner_policy_unavailable"):
            A.load_owner(link, os.geteuid())

    def test_owner_bound_store_refuses_substitution(self):
        owner = A.OwnerIdentity(
            "a" * 32, "sage-native-owner", "b" * 64, os.geteuid())
        path = Path(self.tmp.name) / "owner-store"
        path.mkdir(mode=0o700)
        store = A.FileAnchorStore(path, owner)
        value = A.AnchorValue(
            1, "a" * 64, 0, owner_sha256=A.owner_digest(owner))
        store.create(value)
        self.assertEqual(store.read(), value)
        other = A.OwnerIdentity(
            "a" * 32, "other-owner", "b" * 64, os.geteuid())
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_owner_mismatch"):
            A.FileAnchorStore(path, other).read()

    def test_anchor_is_bound_to_a_monotonic_counter(self):
        class Counter:
            def __init__(self, value):
                self.value = value

            def read(self):
                return A.CounterValue(self.value)

            def advance(self, expected):
                if expected != self.value:
                    raise A.CounterRefused("counter_race")
                self.value += 1
                return A.CounterValue(self.value)

        path = Path(self.tmp.name) / "counter-store"
        path.mkdir(mode=0o700)
        counter = Counter(4)
        store = A.FileAnchorStore(path, counter=counter)
        first = store.create(A.AnchorValue(1, "a" * 64, 0))
        self.assertEqual(first.counter_value, 4)
        self.assertEqual(store.read(), first)
        (path / "anchor.json").unlink()
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_counter_mismatch"):
            store.create(A.AnchorValue(1, "a" * 64, 0, counter_value=3))
        first = store.create(A.AnchorValue(1, "a" * 64, 0))
        second = store.compare_exchange(
            first, A.AnchorValue(2, "b" * 64, 0))
        self.assertEqual(second.counter_value, 5)
        self.assertEqual(counter.value, 5)
        counter.value = 4
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_counter_mismatch"):
            store.read()

    def test_unavailable_counter_refuses_creation(self):
        path = Path(self.tmp.name) / "unavailable-counter"
        path.mkdir(mode=0o700)
        store = A.FileAnchorStore(path, counter=A.UnavailableCounter())
        with self.assertRaisesRegex(A.AnchorRefused, "anchor_counter_unavailable"):
            store.create(A.AnchorValue(1, "a" * 64, 0))

    def test_tpm2_counter_adapter_uses_nvread_and_nvincrement(self):
        state = {"value": 4}
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[0].endswith("tpm2_nvread"):
                output = Path(argv[argv.index("-o") + 1])
                output.write_bytes(state["value"].to_bytes(8, "big"))
            elif argv[0].endswith("tpm2_nvincrement"):
                state["value"] += 1
            else:
                return subprocess.CompletedProcess(argv, 1)
            return subprocess.CompletedProcess(argv, 0)

        counter = A.Tpm2NvCounter(
            0x01500001, runner=runner)
        self.assertEqual(counter.read().value, 4)
        self.assertEqual(counter.advance(4).value, 5)
        self.assertEqual(counter.selector, "0x01500001")
        self.assertEqual(len(calls), 3)

    def test_tpm2_counter_command_failure_refuses(self):
        def runner(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 1)

        counter = A.Tpm2NvCounter(0x01500001, runner=runner)
        with self.assertRaisesRegex(A.CounterRefused, "tpm2_command_failed"):
            counter.read()


if __name__ == "__main__":
    unittest.main()
