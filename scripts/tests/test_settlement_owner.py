"""End-to-end test for the disabled settlement owner facade."""
from __future__ import annotations

from dataclasses import dataclass
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
        journal_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, journal_dir, True)
        self.journal = J.SettlementJournal(
            J.FileJournalBackend(journal_dir),
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


if __name__ == "__main__":
    unittest.main()
