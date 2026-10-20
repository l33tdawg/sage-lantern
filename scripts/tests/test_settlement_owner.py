"""End-to-end test for the disabled settlement owner facade."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "software/security/desktop_loader_candidate"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


O = load("settlement_owner_tested", "settlement_owner.py")
L = O.L
P = load("linux_probe_for_owner", "linux_probe.py")
J = load("settlement_journal_for_owner", "settlement_journal.py")

MACHINE = "a" * 32
BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
LAUNCH = "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc"
CAPTURE = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
POLICY = "1" * 64


@dataclass
class Identity:
    machine_id: str
    boot_id: str


@dataclass
class Selection:
    identity: Identity
    anchor_revision: int
    generation: int
    current_sha256: str


@dataclass
class Release:
    selection: Selection
    launch_id: str


def write(root, path, text):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class ProbeBackend:
    def __init__(self, probe):
        self.probe = probe

    def read_settlement_proof(self, obligation):
        return self.probe.observe(obligation).proof


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        write(self.root, "etc/machine-id", MACHINE + "\n")
        write(self.root, "proc/sys/kernel/random/boot_id", BOOT + "\n")
        write(self.root, "sys/fs/cgroup/sage-session-7/cgroup.events",
              "populated 0\n")
        write(self.root, "proc/net/unix", "header\n")
        write(self.root, "proc/self/mountinfo", "1 0 0:1 / / rw - tmpfs t rw\n")
        (Path(self.root) / "proc/self/fd").mkdir(parents=True)
        self.probe = P.LinuxProbe(self.root, pidfd_alive=lambda fd: False)
        self.journal_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.journal_dir, True)
        self.journal = J.SettlementJournal(
            J.FileJournalBackend(self.journal_dir),
            L.SettlementAuthority(ProbeBackend(self.probe)))
        self.owner = O.SettlementOwner(self.journal, self.probe)

    def release(self):
        return Release(
            Selection(Identity(MACHINE, BOOT), 9, 7, "3" * 64), LAUNCH)

    def test_track_then_settle(self):
        obligation, attempt = self.owner.track_release(
            self.release(), CAPTURE, POLICY,
            (L.ResourceRef("pidfd", "pidfd:7"),
             L.ResourceRef("cgroup", "/sys/fs/cgroup/sage-session-7")))
        self.assertEqual(attempt.status, "attempted")
        settled = self.owner.settle(obligation)
        self.assertEqual(settled.status, "settled")
        self.assertEqual(settled.journal.record.revision, 1)

    def test_missing_resources_refused(self):
        with self.assertRaises(L.SettlementRefused):
            self.owner.track_release(
                self.release(), CAPTURE, POLICY, ())

    def test_uncertain_attempt_is_not_returned_for_release(self):
        owner = O.SettlementOwner(
            SimpleNamespace(record_attempt=lambda _: SimpleNamespace(
                status="uncertain", reason="attempt_readback_mismatch")),
            self.probe)
        with self.assertRaisesRegex(
                L.SettlementRefused, "attempt_readback_mismatch"):
            owner.track_release(
                self.release(), CAPTURE, POLICY,
                (L.ResourceRef("pidfd", "pidfd:7"),))

    def test_already_settled_attempt_cannot_start_a_new_release(self):
        owner = O.SettlementOwner(
            SimpleNamespace(record_attempt=lambda _: SimpleNamespace(
                status="settled", reason="")),
            self.probe)
        with self.assertRaisesRegex(
                L.SettlementRefused, "attempt_not_durable"):
            owner.track_release(
                self.release(), CAPTURE, POLICY,
                (L.ResourceRef("pidfd", "pidfd:7"),))

    def test_restarted_owner_recovers_and_settles_durable_attempt(self):
        obligation, _ = self.owner.track_release(
            self.release(), CAPTURE, POLICY,
            (L.ResourceRef("pidfd", "pidfd:7"),
             L.ResourceRef("cgroup", "/sys/fs/cgroup/sage-session-7")))
        pending = self.journal.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            pending[0].obligation_sha256, J.obligation_digest(obligation))
        reopened = J.SettlementJournal(
            J.FileJournalBackend(self.journal_dir),
            L.SettlementAuthority(ProbeBackend(self.probe)))
        owner = O.SettlementOwner(reopened, self.probe)
        outcomes = owner.recover()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "settled")
        self.assertEqual(reopened.pending(), ())

    def test_recovery_without_a_journal_is_uncertain(self):
        owner = O.SettlementOwner(J.SettlementJournal(), self.probe)
        outcomes = owner.recover()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "uncertain")

    def test_anchor_is_advanced_for_attempt_and_settlement(self):
        anchor_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, anchor_dir, True)
        Path(anchor_dir).chmod(0o700)
        store = O.A.FileAnchorStore(anchor_dir)
        owner = O.SettlementOwner(self.journal, self.probe, store)
        self.assertTrue(owner.initialize_anchor())
        self.assertEqual(owner.anchor_value.generation, 1)
        obligation, _ = owner.track_release(
            self.release(), CAPTURE, POLICY,
            (L.ResourceRef("child", "child:123:456"),))
        self.assertEqual(owner.anchor_value.generation, 2)
        self.assertEqual(len(owner.anchor_value.release_sha256), 64)
        self.assertEqual(owner.settle(obligation).status, "settled")
        self.assertEqual(owner.anchor_value.generation, 3)
        reopened = O.SettlementOwner(
            J.SettlementJournal(
                J.FileJournalBackend(self.journal_dir),
                L.SettlementAuthority(ProbeBackend(self.probe))),
            self.probe, O.A.FileAnchorStore(anchor_dir))
        self.assertEqual(reopened.recover(), ())

    def test_anchor_mismatch_blocks_recovery_and_new_attempts(self):
        anchor_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, anchor_dir, True)
        Path(anchor_dir).chmod(0o700)
        store = O.A.FileAnchorStore(anchor_dir)
        owner = O.SettlementOwner(self.journal, self.probe, store)
        self.assertTrue(owner.initialize_anchor())
        owner.track_release(
            self.release(), CAPTURE, POLICY,
            (L.ResourceRef("child", "child:123:456"),))
        record_path = Path(self.journal_dir) / (
            owner.journal.pending()[0].obligation_sha256 + ".json")
        record_path.unlink()
        self.assertEqual(owner.recover()[0].reason, "settlement_anchor_mismatch")
        with self.assertRaisesRegex(
                L.SettlementRefused, "settlement_anchor_mismatch"):
            owner.track_release(
                self.release(), CAPTURE, POLICY,
                (L.ResourceRef("child", "child:789:987"),))

    def test_owner_identity_is_bound_to_anchor_value(self):
        owner_identity = O.A.OwnerIdentity(
            MACHINE, "sage-native-owner", "2" * 64, 0)
        anchor_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, anchor_dir, True)
        Path(anchor_dir).chmod(0o700)
        store = O.A.FileAnchorStore(anchor_dir, owner_identity)
        owner = O.SettlementOwner(
            self.journal, self.probe, store, owner_identity)
        self.assertTrue(owner.initialize_anchor())
        owner.track_release(
            self.release(), CAPTURE, POLICY,
            (L.ResourceRef("child", "child:123:456"),))
        self.assertEqual(
            owner.anchor_value.owner_sha256,
            O.A.owner_digest(owner_identity))
        other = O.A.OwnerIdentity(
            MACHINE, "other-owner", "2" * 64, 0)
        with self.assertRaisesRegex(
                O.A.AnchorRefused, "anchor_owner_mismatch"):
            O.A.FileAnchorStore(anchor_dir, other).read()

    def test_counter_is_advanced_with_each_owner_transition(self):
        class Counter:
            def __init__(self):
                self.value = 4

            def read(self):
                return O.A.CounterValue(self.value)

            def advance(self, expected):
                if expected != self.value:
                    raise O.A.CounterRefused("counter_race")
                self.value += 1
                return O.A.CounterValue(self.value)

        anchor_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, anchor_dir, True)
        Path(anchor_dir).chmod(0o700)
        counter = Counter()
        store = O.A.FileAnchorStore(anchor_dir, counter=counter)
        owner = O.SettlementOwner(self.journal, self.probe, store)
        self.assertTrue(owner.initialize_anchor())
        self.assertEqual(owner.anchor_value.counter_value, 4)
        obligation, _ = owner.track_release(
            self.release(), CAPTURE, POLICY,
            (L.ResourceRef("child", "child:123:456"),))
        self.assertEqual(owner.anchor_value.counter_value, 5)
        self.assertEqual(owner.settle(obligation).status, "settled")
        self.assertEqual(owner.anchor_value.counter_value, 6)

    def test_counter_bound_restart_settles_the_durable_attempt(self):
        class Counter:
            def __init__(self):
                self.value = 4

            def read(self):
                return O.A.CounterValue(self.value)

            def advance(self, expected):
                if expected != self.value:
                    raise O.A.CounterRefused("counter_race")
                self.value += 1
                return O.A.CounterValue(self.value)

        anchor_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, anchor_dir, True)
        Path(anchor_dir).chmod(0o700)
        counter = Counter()
        store = O.A.FileAnchorStore(anchor_dir, counter=counter)
        owner = O.SettlementOwner(self.journal, self.probe, store)
        self.assertTrue(owner.initialize_anchor())
        obligation, _ = owner.track_release(
            self.release(), CAPTURE, POLICY,
            (L.ResourceRef("child", "child:123:456"),))
        reopened = O.SettlementOwner(
            J.SettlementJournal(
                J.FileJournalBackend(self.journal_dir),
                L.SettlementAuthority(ProbeBackend(self.probe))),
            self.probe, store)
        outcomes = reopened.recover()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "settled")
        self.assertEqual(counter.value, 6)

    def test_counter_ahead_after_interrupted_anchor_write_is_uncertain(self):
        class Counter:
            def __init__(self):
                self.value = 4

            def read(self):
                return O.A.CounterValue(self.value)

            def advance(self, expected):
                if expected != self.value:
                    raise O.A.CounterRefused("counter_race")
                self.value += 1
                return O.A.CounterValue(self.value)

        anchor_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, anchor_dir, True)
        Path(anchor_dir).chmod(0o700)
        counter = Counter()
        store = O.A.FileAnchorStore(anchor_dir, counter=counter)
        owner = O.SettlementOwner(self.journal, self.probe, store)
        self.assertTrue(owner.initialize_anchor())
        write = store._write
        store._write = lambda _value: (_ for _ in ()).throw(
            O.A.AnchorRefused("simulated_anchor_write_failure"))
        try:
            with self.assertRaises(O.L.SettlementRefused):
                owner.track_release(
                    self.release(), CAPTURE, POLICY,
                    (L.ResourceRef("child", "child:123:456"),))
        finally:
            store._write = write
        reopened = O.SettlementOwner(
            J.SettlementJournal(
                J.FileJournalBackend(self.journal_dir),
                L.SettlementAuthority(ProbeBackend(self.probe))),
            self.probe, store)
        outcomes = reopened.recover()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "uncertain")
        self.assertEqual(outcomes[0].reason, "settlement_anchor_mismatch")
        with self.assertRaisesRegex(
                O.L.SettlementRefused, "settlement_anchor_mismatch"):
            reopened.track_release(
                Release(
                    Selection(Identity(MACHINE, BOOT), 9, 7, "3" * 64),
                    "ffffffff-aaaa-bbbb-cccc-dddddddddddd"),
                CAPTURE, POLICY,
                (L.ResourceRef("child", "child:789:987"),))


if __name__ == "__main__":
    unittest.main()
