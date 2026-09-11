from dataclasses import replace
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from sage_lantern.suspend_journal import SuspendJournal, SuspendJournalError
from sage_lantern.suspend_transition import Acknowledgement, Gate, Outcome, Phase, Snapshot, SuspendTransition


class SuspendJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / "journal"
        self.journal = self.open()
        self.snapshot = Snapshot("transition-0001", revision=1)

    def open(self):
        journal = SuspendJournal(directory=self.directory, owner_uid=os.geteuid())
        self.addCleanup(journal.close)
        return journal

    def terminal(self, phase=Phase.RESUMED):
        return replace(self.snapshot, phase=phase, revision=2)

    def test_coordinator_does_not_rearm_fence_after_verified_resume(self):
        events = []

        def perform(request):
            events.append(request.gate)
            if request.gate is Gate.RETAIN_FENCE:
                self.journal.retain_fence(request.snapshot.transition_id)
            elif request.gate is Gate.CHECKPOINT:
                self.journal.checkpoint(request.snapshot)
            elif request.gate is Gate.RELEASE_FENCE:
                self.journal.clear(request.snapshot, verified=True)
            elif request.gate is Gate.START_AUTH_LOCKED:
                self.assertFalse(self.journal.load().active)
            return Acknowledgement(request, Outcome.CONFIRMED)

        coordinator = SuspendTransition(self.snapshot.transition_id, perform)
        coordinator.prepare()
        coordinator.request_sleep()
        coordinator.resume()
        self.assertTrue(coordinator.snapshot.completed)
        self.assertFalse(self.journal.load().active)
        self.assertEqual(events[-2:], [Gate.RELEASE_FENCE, Gate.START_AUTH_LOCKED])

    def test_default_is_root_only(self):
        with patch("os.geteuid", return_value=12345):
            with self.assertRaises(SuspendJournalError):
                SuspendJournal(directory=self.directory)

    def test_checkpoint_fence_and_reopen(self):
        self.journal.checkpoint(self.snapshot)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for name in ("active", "checkpoint.json"):
            self.assertEqual(stat.S_IMODE((self.directory / name).stat().st_mode), 0o600)
        self.journal.close()
        reopened = self.open()
        state = reopened.load()
        self.assertTrue(state.active)
        self.assertTrue(state.needs_recovery)
        self.assertEqual(state.snapshot, self.snapshot)

    def test_sentinel_is_synced_before_checkpoint_replace(self):
        original_replace = os.replace
        original_fsync = os.fsync
        events = []

        def replace_file(source, target, **kwargs):
            events.append(("replace", target))
            return original_replace(source, target, **kwargs)

        def sync(descriptor):
            events.append(("sync", "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"))
            return original_fsync(descriptor)

        with patch("os.replace", side_effect=replace_file), patch("os.fsync", side_effect=sync):
            self.journal.checkpoint(self.snapshot)
        self.assertEqual(events, [("sync", "file"), ("replace", "active"), ("sync", "directory"),
                                  ("sync", "file"), ("replace", "checkpoint.json"), ("sync", "directory")])

    def test_active_without_checkpoint_recovers(self):
        self.journal.retain_fence(self.snapshot.transition_id)
        self.journal.close()
        state = self.open().load()
        self.assertTrue(state.needs_recovery)
        self.assertIsNone(state.snapshot)
        self.assertEqual(state.transition_id, self.snapshot.transition_id)

    def test_close_and_context_exit_never_clear(self):
        self.journal.close()
        with self.open() as journal:
            journal.checkpoint(self.snapshot)
        self.assertTrue((self.directory / "active").exists())

    def test_exact_terminal_and_explicit_verification_only(self):
        self.journal.checkpoint(self.snapshot)
        with self.assertRaises(SuspendJournalError):
            self.journal.clear(self.snapshot, verified=True)
        terminal = self.terminal()
        self.journal.checkpoint(terminal)
        for verified in (False, None, 1, "yes"):
            with self.assertRaises(SuspendJournalError):
                self.journal.clear(terminal, verified=verified)
        with self.assertRaises(SuspendJournalError):
            self.journal.clear(replace(terminal, revision=3), verified=True)
        self.assertTrue((self.directory / "active").exists())
        self.journal.clear(terminal, verified=True)
        self.assertFalse(self.journal.load().active)

    def test_reopen_terminal_still_requires_verification_when_fenced(self):
        terminal = replace(self.terminal(Phase.ABORTED_VERIFIED), completed=True)
        self.journal.checkpoint(terminal)
        self.journal.close()
        reopened = self.open()
        self.assertTrue(reopened.needs_recovery)
        with self.assertRaises(SuspendJournalError):
            reopened.clear(terminal)
        reopened.clear(terminal, verified=True)
        reopened.close()
        self.assertFalse(self.open().needs_recovery)

    def test_completion_checkpoint_rearms(self):
        terminal = self.terminal()
        self.journal.checkpoint(terminal)
        self.journal.clear(terminal, verified=True)
        complete = replace(terminal, revision=3, completed=True)
        self.journal.checkpoint(complete)
        self.assertTrue(self.journal.load().active)

    def test_revision_and_transition_conflicts(self):
        self.journal.checkpoint(self.snapshot)
        for other in (replace(self.snapshot, revision=0), replace(self.snapshot, prepared=True),
                      replace(self.snapshot, transition_id="transition-other")):
            with self.assertRaises(SuspendJournalError):
                self.journal.checkpoint(other)
        self.assertEqual(self.journal.load().snapshot, self.snapshot)
        self.journal.checkpoint(self.snapshot)

    def test_new_transition_after_verified_clear_and_crash_before_checkpoint(self):
        terminal = self.terminal()
        self.journal.checkpoint(terminal)
        self.journal.clear(terminal, verified=True)
        self.journal.retain_fence("transition-next")
        self.journal.close()
        reopened = self.open()
        self.assertIsNone(reopened.load().snapshot)
        self.assertEqual(reopened.load().transition_id, "transition-next")
        reopened.checkpoint(Snapshot("transition-next"))

    def test_checkpoint_failure_preserves_fence_and_prior_snapshot(self):
        self.journal.checkpoint(self.snapshot)
        original = os.replace

        def fail_checkpoint(source, target, **kwargs):
            if target == "checkpoint.json":
                raise OSError("private-path-canary")
            return original(source, target, **kwargs)

        with patch("os.replace", side_effect=fail_checkpoint):
            with self.assertRaisesRegex(SuspendJournalError, "^journal_unavailable$"):
                self.journal.checkpoint(self.terminal())
        self.journal.close()
        self.assertEqual(self.open().load().snapshot, self.snapshot)
        self.assertTrue((self.directory / "active").exists())
        self.assertFalse(list(self.directory.glob(".pending-*")))

    def test_fence_fsync_failure_never_writes_checkpoint(self):
        with patch("os.fsync", side_effect=OSError("disk")):
            with self.assertRaises(SuspendJournalError):
                self.journal.checkpoint(self.snapshot)
        self.assertFalse((self.directory / "checkpoint.json").exists())
        self.assertTrue(self.journal.needs_recovery)
        self.assertTrue(self.journal.load().needs_recovery)

    def test_uncertain_clear_stays_recovery_until_verified_retry(self):
        terminal = self.terminal()
        self.journal.checkpoint(terminal)
        with patch("os.fsync", side_effect=OSError("disk")):
            with self.assertRaises(SuspendJournalError):
                self.journal.clear(terminal, verified=True)
        self.assertTrue(self.journal.load().needs_recovery)
        with patch("os.fsync", wraps=os.fsync) as sync:
            self.journal.clear(terminal, verified=True)
        sync.assert_called_once()
        self.assertFalse(self.journal.needs_recovery)

    def test_lifetime_lock_denies_second_instance(self):
        with self.assertRaises(SuspendJournalError):
            self.open()
        self.journal.close()
        self.open()

    def test_strict_json_invalid_versions_duplicates_and_bounds(self):
        self.journal.checkpoint(self.snapshot)
        path = self.directory / "checkpoint.json"
        original = path.read_bytes()
        for raw in (b'{"version":1,"version":1}', b'{"version":true}', b'{"version":NaN}',
                    b'[]', b'bad', b'x' * 4097):
            path.write_bytes(raw)
            with self.assertRaises(SuspendJournalError):
                self.journal.load()
            self.assertTrue(self.journal.needs_recovery)
        path.write_bytes(original)

    def test_malformed_snapshot_and_missing_fence_fail_closed(self):
        for value in (replace(self.snapshot, revision=True), replace(self.snapshot, transition_id="../bad"),
                      replace(self.snapshot, phase=Phase.RESUMED, prepared=True),
                      replace(self.snapshot, completed=True)):
            with self.assertRaises(SuspendJournalError):
                self.journal.checkpoint(value)
        self.assertFalse((self.directory / "active").exists())
        self.journal.checkpoint(self.snapshot)
        (self.directory / "active").unlink()
        with self.assertRaises(SuspendJournalError):
            self.journal.load()

    def test_symlink_hardlink_bad_modes_and_special_files_not_followed(self):
        sentinel = self.directory / "sentinel"
        sentinel.write_text("private")
        sentinel.chmod(0o600)
        active = self.directory / "active"
        for kind in ("symlink", "hardlink", "mode", "fifo"):
            if kind == "symlink":
                active.symlink_to(sentinel)
            elif kind == "hardlink":
                os.link(sentinel, active)
            elif kind == "fifo":
                os.mkfifo(active, 0o600)
            else:
                active.write_text("bad")
                active.chmod(0o644)
            with self.assertRaises(SuspendJournalError):
                self.journal.retain_fence(self.snapshot.transition_id)
            self.assertEqual(sentinel.read_text(), "private")
            active.unlink()

    def test_unsafe_directory_and_replacement(self):
        self.directory.chmod(0o755)
        with self.assertRaises(SuspendJournalError):
            self.journal.load()
        self.directory.chmod(0o700)
        self.directory.rename(self.directory.with_name("old"))
        self.directory.mkdir(mode=0o700)
        with self.assertRaises(SuspendJournalError):
            self.journal.checkpoint(self.snapshot)

    def test_death_after_checkpoint_rename_reopens_fenced(self):
        class ProcessDeath(BaseException):
            pass

        original = os.replace

        def die_after_publish(source, target, **kwargs):
            original(source, target, **kwargs)
            if target == "checkpoint.json":
                raise ProcessDeath

        with patch("os.replace", side_effect=die_after_publish):
            with self.assertRaises(ProcessDeath):
                self.journal.checkpoint(self.snapshot)
        self.journal.close()
        state = self.open().load()
        self.assertTrue(state.active)
        self.assertTrue(state.needs_recovery)
        self.assertEqual(state.snapshot, self.snapshot)


if __name__ == "__main__":
    unittest.main()
