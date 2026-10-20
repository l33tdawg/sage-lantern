"""Tests for the disabled Linux probe against a synthetic procfs root."""
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


P = load("linux_probe_tested", "linux_probe.py")
L = P.L
J = load("settlement_journal_for_linux", "settlement_journal.py")

MACHINE = "a" * 32
BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BINDING = L.LaunchBinding(
    MACHINE, BOOT, "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa", 7,
    "1" * 64, "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc")
RESOURCES = (
    L.ResourceRef("pidfd", "pidfd:7"),
    L.ResourceRef("child", "child:123:456"),
    L.ResourceRef("cgroup", "/sys/fs/cgroup/sage-session-7"),
    L.ResourceRef("socket", "unix:/run/sage/native-7"),
    L.ResourceRef("mount", "/run/sage/mnt"),
    L.ResourceRef("fd", "fd:8"),
)
OBLIGATION = L.SettlementObligation(BINDING, RESOURCES)


def write(root, path, text):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class LinuxProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        write(self.tmp, "etc/machine-id", MACHINE + "\n")
        write(self.tmp, "proc/sys/kernel/random/boot_id", BOOT + "\n")
        write(self.tmp, "sys/fs/cgroup/sage-session-7/cgroup.events",
              "populated 0\nfrozen 0\n")
        write(self.tmp, "proc/net/unix",
              "Num RefCount Protocol Flags Type St Inode Path\n")
        write(self.tmp, "proc/self/mountinfo",
              "1 0 0:1 / / rw - tmpfs tmpfs rw\n")
        (Path(self.tmp) / "proc/self/fd").mkdir(parents=True)

    def probe(self, alive=False):
        return P.LinuxProbe(self.tmp, pidfd_alive=lambda fd: alive)

    def test_all_terminal_resources_produce_proof(self):
        result = self.probe().observe(OBLIGATION)
        self.assertIsNotNone(result.proof)
        self.assertEqual(result.proof.observed_machine_id, MACHINE)

    def test_live_pidfd_is_not_terminal(self):
        result = self.probe(alive=True).observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("pidfd_not_terminal", result.reason)

    def test_populated_cgroup_is_not_terminal(self):
        write(self.tmp, "sys/fs/cgroup/sage-session-7/cgroup.events",
              "populated 1\n")
        result = self.probe().observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("cgroup_not_terminal", result.reason)

    def test_present_socket_is_not_terminal(self):
        write(self.tmp, "proc/net/unix",
              "0000: 00000002 00000000 00010000 0001 01 1 /run/sage/native-7\n")
        result = self.probe().observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("socket_not_terminal", result.reason)

    def test_present_mount_is_not_terminal(self):
        write(self.tmp, "proc/self/mountinfo",
              "1 0 0:1 / /run/sage/mnt rw - tmpfs tmpfs rw\n")
        result = self.probe().observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("mount_not_terminal", result.reason)

    def test_open_fd_is_not_terminal(self):
        target = Path(self.tmp) / "open-file"
        target.write_text("x", encoding="utf-8")
        (Path(self.tmp) / "proc/self/fd/8").symlink_to(target)
        result = self.probe().observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("fd_not_terminal", result.reason)

    def test_machine_mismatch_is_refused(self):
        write(self.tmp, "etc/machine-id", "b" * 32 + "\n")
        result = self.probe().observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("machine_mismatch", result.reason)

    def test_absent_cgroup_is_terminal(self):
        (Path(self.tmp) / "sys/fs/cgroup/sage-session-7").rename(
            Path(self.tmp) / "sys/fs/cgroup/moved")
        result = self.probe().observe(OBLIGATION)
        self.assertIsNotNone(result.proof)

    @staticmethod
    def child_stat(pid, state, start):
        # Fields after comm begin with state; starttime is field 22 overall.
        fields = ["0"] * 20
        fields[0], fields[19] = state, str(start)
        return f"{pid} (gnome-shell) {' '.join(fields)}\n"

    def test_running_child_is_not_terminal(self):
        write(self.tmp, "proc/123/stat", self.child_stat(123, "S", 456))
        result = self.probe().observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("child_not_terminal", result.reason)

    def test_unreaped_zombie_is_not_terminal(self):
        write(self.tmp, "proc/123/stat", self.child_stat(123, "Z", 456))
        result = self.probe().observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("child_not_terminal", result.reason)

    def test_reused_pid_with_different_start_time_is_absent(self):
        write(self.tmp, "proc/123/stat", self.child_stat(123, "S", 999))
        result = self.probe().observe(OBLIGATION)
        self.assertIsNotNone(result.proof)

    def test_missing_platform_identity_is_refused(self):
        (Path(self.tmp) / "etc/machine-id").unlink()
        result = self.probe().observe(OBLIGATION)
        self.assertIsNone(result.proof)
        self.assertIn("identity_unavailable", result.reason)

    def test_probe_settles_through_durable_journal(self):
        proof = self.probe().observe(OBLIGATION).proof
        self.assertIsNotNone(proof)

        class ProofBackend:
            def read_settlement_proof(self, obligation):
                return proof

        journal_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, journal_dir, True)
        journal = J.SettlementJournal(
            J.FileJournalBackend(journal_dir),
            L.SettlementAuthority(ProofBackend()))
        self.assertEqual(journal.record_attempt(OBLIGATION).status,
                         "attempted")
        outcome = journal.settle(OBLIGATION, proof)
        self.assertEqual(outcome.status, "settled")
        self.assertEqual(outcome.record.revision, 1)


if __name__ == "__main__":
    unittest.main()
