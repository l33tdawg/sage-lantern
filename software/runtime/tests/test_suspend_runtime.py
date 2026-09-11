import os
from pathlib import Path
import tempfile
import unittest

from sage_lantern.suspend_journal import SuspendJournal
from sage_lantern.suspend_runtime import SleepCycle
from sage_lantern.suspend_transition import Phase, SuspendTransitionError


class Appliance:
    def __init__(self):
        self.fence = False
        self.slept = False
        self.started = False
        self.allow_idle = True
        self.allow_drain = True
        self.proven_resume = True
        self.calls = []

    def fenced(self):
        return self.fence

    def retain_fence(self):
        self.fence = True
        return True

    def stop_auth(self):
        return True

    def auth_inactive(self):
        return True

    def quiesce(self):
        return self.allow_drain

    def idle_eligible(self):
        return self.allow_idle

    def sleep(self):
        self.slept = True
        return True

    def resumed(self):
        return self.slept and self.proven_resume

    def aborted(self):
        return not self.slept

    def release_fence(self):
        self.calls.append("release")
        self.fence = False
        return True

    def start_locked(self):
        self.calls.append("start_locked")
        self.started = True
        return not self.fence


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.journal = SuspendJournal(directory=Path(temporary.name).resolve() / "journal", owner_uid=os.geteuid())
        self.addCleanup(self.journal.close)
        self.platform = Appliance()
        self.cycle = SleepCycle(self.journal, self.platform, "test-suspend-0001")

    def test_resume_clears_terminal_journal_before_start(self):
        self.assertEqual(self.cycle.run().phase, Phase.RESUMED)
        self.assertFalse(self.journal.load().active)
        self.assertEqual(self.platform.calls, ["release", "start_locked"])

    def test_new_input_aborts_without_sleep_and_returns_locked(self):
        self.platform.allow_idle = False
        self.assertEqual(self.cycle.run().phase, Phase.ABORTED_VERIFIED)
        self.assertFalse(self.platform.slept)
        self.assertTrue(self.platform.started)

    def test_unknown_drain_keeps_both_fences_and_no_restart(self):
        self.platform.allow_drain = False
        with self.assertRaises(SuspendTransitionError):
            self.cycle.run()
        self.assertTrue(self.journal.load().active)
        self.assertTrue(self.platform.fence)
        self.assertFalse(self.platform.started)

    def test_unproven_resume_keeps_fenced(self):
        self.platform.proven_resume = False
        with self.assertRaises(SuspendTransitionError):
            self.cycle.run()
        self.assertTrue(self.journal.load().active)
        self.assertFalse(self.platform.started)

    def test_existing_fence_is_not_implicitly_recovered(self):
        self.platform.fence = True
        with self.assertRaises(SuspendTransitionError):
            self.cycle.run()
        self.assertFalse(self.platform.slept)
