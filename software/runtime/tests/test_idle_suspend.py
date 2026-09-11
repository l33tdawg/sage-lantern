import unittest

from sage_lantern.idle_suspend import IdleSuspendPolicy


class IdleSuspendTests(unittest.TestCase):
    def setUp(self):
        self.policy = IdleSuspendPolicy()
        self.now = 0

    def tick(self, idle=600, locked=True):
        result = self.policy.tick(now=self.now, idle_seconds=idle, locked=locked)
        self.now += 5
        return result

    def wait(self, ticks, idle=600, locked=True):
        return [self.tick(idle, locked) for _ in range(ticks)]

    def test_startup_requires_full_grace_even_with_old_idle(self):
        self.assertFalse(any(self.wait(60)))
        self.assertTrue(self.tick())

    def test_x11_idle_threshold_independent_of_grace(self):
        self.assertFalse(any(self.wait(100, idle=299.999)))
        self.assertTrue(self.tick(idle=300))

    def test_attempt_disarms_even_when_caller_does_nothing_or_fails(self):
        self.wait(60)
        self.assertTrue(self.tick())
        self.assertFalse(any(self.wait(200)))

    def test_fresh_input_rearms_with_full_grace(self):
        self.wait(60)
        self.assertTrue(self.tick())
        self.assertFalse(self.tick(idle=0))
        self.assertFalse(any(self.wait(59)))
        self.assertTrue(self.tick())

    def test_repeated_input_restarts_grace(self):
        self.wait(60)
        self.tick()
        for _ in range(3):
            self.assertFalse(self.tick(idle=0))
            self.assertFalse(any(self.wait(59)))
        self.assertTrue(self.tick())

    def test_unlock_disarms_without_input(self):
        self.wait(50)
        self.assertFalse(self.tick(locked=False))
        self.assertFalse(any(self.wait(200)))

    def test_input_while_unlocked_rearms_but_requires_full_delay_locked(self):
        self.tick(locked=False)
        self.tick(idle=0, locked=False)
        self.assertFalse(any(self.wait(60, locked=False)))
        self.assertFalse(any(self.wait(60)))
        self.assertTrue(self.tick())

    def test_unlock_and_input_same_snapshot_does_not_rearm(self):
        self.tick()
        self.tick(idle=0, locked=False)
        self.assertFalse(any(self.wait(100)))

    def test_gaps_backwards_and_duplicate_times_reset_grace(self):
        for jump in (16, -10, 0):
            with self.subTest(jump=jump):
                self.setUp()
                self.wait(59)
                self.now = self.now - 5 + jump
                self.assertFalse(any(self.wait(60)))
                self.assertTrue(self.tick())

    def test_fifteen_second_gap_is_allowed(self):
        self.wait(58)
        self.now += 10
        self.assertTrue(self.tick())

    def test_unknown_resets_evidence_without_rearming_attempt(self):
        self.wait(60)
        self.assertTrue(self.tick())
        self.assertFalse(self.tick(idle=None))
        self.assertFalse(self.tick(idle=0))
        self.assertFalse(any(self.wait(100)))

    def test_gap_cannot_manufacture_input_decrease(self):
        self.wait(60)
        self.assertTrue(self.tick())
        self.now += 1000
        self.assertFalse(self.tick(idle=0))
        self.assertFalse(any(self.wait(100)))

    def test_invalid_snapshots_deny_and_reset_timing(self):
        for field, invalid in (
            ("now", [None, True, "1", -1, float("nan"), float("inf"), 10 ** 400]),
            ("idle_seconds", [None, True, "300", -1, float("nan"), float("inf"), []]),
            ("locked", [None, 1, 0, "true", [], {}]),
        ):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    self.setUp()
                    self.wait(59)
                    snapshot = dict(now=self.now, idle_seconds=600, locked=True)
                    snapshot[field] = value
                    self.assertIs(self.policy.tick(**snapshot), False)
                    self.assertFalse(any(self.wait(60)))
                    self.assertTrue(self.tick())

    def test_missing_snapshot_denied(self):
        self.assertIs(self.policy.tick(), False)

    def test_all_saved_delays_require_full_observation(self):
        for delay in (300, 600, 900, 1800):
            with self.subTest(delay=delay):
                self.policy = IdleSuspendPolicy(delay_seconds=delay, revision=1)
                self.now = 0
                self.assertFalse(any(self.wait(delay // 5, idle=3600)))
                self.assertTrue(self.tick(idle=3600))
                self.assertFalse(any(self.wait(400, idle=3600)))

    def test_configured_delay_also_requires_native_idle_threshold(self):
        self.policy = IdleSuspendPolicy(delay_seconds=900)
        self.assertFalse(any(self.wait(200, idle=899)))
        self.assertTrue(self.tick(idle=900))

    def test_long_delay_requires_full_lock_after_unlocked_idle(self):
        self.policy = IdleSuspendPolicy(delay_seconds=600)
        self.tick(idle=3600, locked=False)
        self.tick(idle=0, locked=False)
        self.assertFalse(any(self.wait(120, idle=3600, locked=False)))
        self.assertFalse(any(self.wait(120, idle=3600)))
        self.assertTrue(self.tick(idle=3600))

    def test_same_configuration_each_tick_does_not_reset_progress(self):
        for _ in range(60):
            self.assertTrue(self.policy.configure(delay_seconds=300, revision=0))
            self.assertFalse(self.tick())
        self.assertTrue(self.tick())

    def test_revision_only_change_resets_observation(self):
        self.wait(59)
        self.assertTrue(self.policy.configure(delay_seconds=300, revision=1))
        self.assertFalse(any(self.wait(60)))
        self.assertTrue(self.tick())

    def test_delay_change_with_same_revision_resets_observation(self):
        for initial, changed in ((1800, 300), (300, 600)):
            with self.subTest(initial=initial, changed=changed):
                self.policy = IdleSuspendPolicy(delay_seconds=initial)
                self.now = 0
                self.wait(59, idle=3600)
                self.assertTrue(self.policy.configure(delay_seconds=changed))
                self.assertFalse(any(self.wait(changed // 5, idle=3600)))
                self.assertTrue(self.tick(idle=3600))

    def test_config_change_does_not_rearm_after_attempt(self):
        self.wait(60)
        self.assertTrue(self.tick())
        self.policy.configure(delay_seconds=600, revision=1)
        self.assertFalse(any(self.wait(200, idle=3600)))
        self.assertFalse(self.tick(idle=0))
        self.assertFalse(any(self.wait(119, idle=3600)))
        self.assertTrue(self.tick(idle=3600))

    def test_config_change_does_not_rearm_after_unlock(self):
        self.tick(locked=False)
        self.policy.configure(delay_seconds=300, revision=1)
        self.assertFalse(any(self.wait(200)))

    def test_configured_delay_restarts_after_evidence_loss(self):
        for missing in (None, float("nan")):
            with self.subTest(missing=missing):
                self.policy = IdleSuspendPolicy(delay_seconds=600)
                self.now = 0
                self.wait(119, idle=3600)
                self.assertFalse(self.tick(idle=missing))
                self.assertFalse(any(self.wait(120, idle=3600)))
                self.assertTrue(self.tick(idle=3600))

    def test_configured_delay_restarts_after_poll_gap(self):
        self.policy = IdleSuspendPolicy(delay_seconds=900)
        self.wait(179, idle=3600)
        self.now += 16
        self.assertFalse(any(self.wait(180, idle=3600)))
        self.assertTrue(self.tick(idle=3600))

    def test_invalid_configuration_disables_until_valid_fresh_evidence(self):
        for field, values in (
            ("delay_seconds", [None, True, 300.0, "300", 0, 301, 3600, [], float("nan")]),
            ("revision", [None, True, 1.0, "1", -1, 2 ** 63, {}, float("inf")]),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.setUp()
                    self.wait(59)
                    arguments = {"delay_seconds": 300, "revision": 0, field: value}
                    self.assertFalse(self.policy.configure(**arguments))
                    self.assertFalse(any(self.wait(100)))
                    self.assertTrue(self.policy.configure())
                    self.assertFalse(any(self.wait(60)))
                    self.assertTrue(self.tick())

    def test_invalid_constructor_policy_denies(self):
        self.policy = IdleSuspendPolicy(delay_seconds="300")
        self.assertFalse(any(self.wait(200)))


if __name__ == "__main__":
    unittest.main()
