import importlib.util
import json
from contextlib import ExitStack
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sage_lantern import suspend_capability


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/lantern-idle-suspend.py"
SPEC = importlib.util.spec_from_file_location("idle_suspend_flow", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PUBLISH_CAPABILITY = MODULE.capability
INFERENCE_READY = MODULE.inference_ready
POLICY = dict(revision=1, display_off_seconds=30, deep_sleep_seconds=300, deep_sleep_available=False)


class MockedPlatform(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.now = 100.0
        self.events = []
        self.publish = self.replace(MODULE, "publish")
        self.command = self.replace(MODULE, "command", return_value="")
        self.display = self.replace(MODULE, "display_power")
        self.public = self.replace(MODULE, "public_json")
        self.read = self.replace(MODULE, "read_json")
        self.prior_auth = self.replace(MODULE, "prior_auth_drained", return_value=True)
        self.replace(MODULE.subprocess, "run", side_effect=AssertionError("real subprocess forbidden"))
        self.replace(MODULE.os, "open", side_effect=AssertionError("real file open forbidden"))
        self.replace(MODULE.os, "geteuid", return_value=0)
        self.replace(MODULE.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=123))
        self.replace(MODULE.time, "monotonic", side_effect=lambda: self.now)
        self.replace(MODULE.time, "sleep", side_effect=self.advance)
        self.replace(Path, "read_text", side_effect=self.file_text)
        self.replace(Path, "unlink")
        self.replace(MODULE, "BOOT", Mock(read_text=Mock(return_value="test-boot\n")))
        self.fence = self.replace(MODULE, "FENCE")
        self.fence.exists.return_value = False
        self.fence.is_symlink.return_value = False
        self.capability = self.replace(MODULE, "capability")
        self.idle = self.replace(MODULE, "idle_seconds", return_value=600)
        self.inference = self.replace(MODULE, "inference_ready", return_value=True)
        self.pending = self.replace(MODULE, "pending_count", return_value=0)
        self.locked = self.replace(MODULE, "locked", return_value=True)
        self.identity = self.replace(MODULE, "service_identity", side_effect=lambda service:
                                     (22, "a" * 32) if service == MODULE.AUTH else (33, "b" * 32))
        self.system = Mock()
        for method in ("verify_stop_guards", "verify_sleep_idle", "stop_verified", "inactive_verified", "suspend_verified"):
            getattr(self.system, method).return_value = SimpleNamespace(confirmed=True)
        self.kiosk = Mock()
        self.kiosk.quiesce.return_value = True
        self.kiosk.resume.return_value = True

    def replace(self, target, name, *args, **options):
        return self.stack.enter_context(patch.object(target, name, *args, **options))

    def advance(self, seconds):
        self.now += seconds

    @staticmethod
    def file_text(*args, **kwargs):
        return "[deep]"

    def appliance(self):
        appliance = object.__new__(MODULE.Appliance)
        appliance.system = self.system
        appliance.kiosk = self.kiosk
        appliance.policy = dict(POLICY)
        appliance.transition_id = "transition-123"
        appliance.owner = 123
        appliance.auth_pid, appliance.auth_instance = 22, "a" * 32
        appliance.voice_pid, appliance.voice_invocation = 33, "b" * 32
        appliance.voice_identity = dict(instance_id="12345678-1234-4234-8234-123456789abc", pid=33,
                                        start_ticks="456", boot_id="test-boot", invocation_id="b" * 32)
        appliance.initial_idle = 600
        appliance.sampled_at = self.now
        appliance.submitted = False
        appliance.sleep_proof = None
        appliance.stopped_auth = True
        appliance.drained = False
        appliance.stopped = set()
        appliance.cancelled = lambda: False
        return appliance

    def receipts(self, appliance):
        auth = dict(version=1, instance_id=appliance.auth_instance, closing=True,
                    drained=True, local_revoked=True, upstream_cleanup="confirmed")
        voice = dict(appliance.voice_identity, schema="sage.lantern.voice-drain.v1",
                     transition_id=appliance.transition_id, drained=True, reconcile_required=False,
                     checks={key: True for key in ("http_drained", "http_outcome_known", "session_finalized",
                             "child_groups_absent", "memory_drained", "community_drained", "history_outcome_known", "deadline_met")})
        self.read.side_effect = lambda path, **kwargs: auth if path.parent == MODULE.AUTH_REPORTS else voice
        return auth, voice


class ApplianceFlowTests(MockedPlatform):
    def test_quiesce_orders_receipts_before_private_service_and_kiosk_stops(self):
        appliance = self.appliance()
        self.receipts(appliance)
        self.system.stop_verified.side_effect = lambda service: (self.events.append(service), SimpleNamespace(confirmed=True))[1]
        self.kiosk.quiesce.side_effect = lambda: (self.events.append("kiosk"), True)[1]
        self.display.side_effect = lambda action: self.events.append("display-" + action)
        self.inference.side_effect = lambda: (self.events.append("inference"), True)[1]
        self.replace(MODULE, "model_idle", side_effect=lambda value: (self.events.append("model"), True)[1])
        self.assertTrue(appliance.quiesce())
        self.assertEqual(self.events, [MODULE.VOICE, MODULE.CAMERA, MODULE.UI, MODULE.DISPLAY,
                                      "kiosk", "display-off", "inference", "model"])
        self.assertTrue(appliance.drained)
        self.publish.assert_called_once_with(MODULE.VOICE_REQUEST, {
            **appliance.voice_identity, "schema": "sage.lantern.voice-drain-request.v1",
            "transition_id": appliance.transition_id,
        }, mode=0o644)
        self.system.suspend_verified.assert_not_called()

    def test_missing_auth_receipt_never_stops_voice_or_submits_sleep(self):
        appliance = self.appliance()
        self.read.return_value = None
        self.assertFalse(appliance.quiesce())
        self.system.stop_verified.assert_not_called()
        self.system.suspend_verified.assert_not_called()

    def test_old_auth_uncertainty_blocks_even_with_current_positive_receipt(self):
        appliance = self.appliance()
        self.receipts(appliance)
        self.prior_auth.return_value = False
        self.assertFalse(appliance.auth_drained())
        self.assertFalse(appliance.quiesce())
        self.prior_auth.assert_called_with(appliance.auth_instance, appliance.owner)
        self.system.stop_verified.assert_not_called()
        self.system.suspend_verified.assert_not_called()
        self.publish.assert_not_called()
        self.display.assert_not_called()

    def test_missing_or_stale_voice_receipt_keeps_later_services_running(self):
        for missing in (None, {}, {"transition_id": "previous"}):
            with self.subTest(receipt=missing):
                appliance = self.appliance()
                auth, _ = self.receipts(appliance)
                self.read.side_effect = lambda path, **kwargs: auth if path.parent == MODULE.AUTH_REPORTS else missing
                self.system.stop_verified.reset_mock()
                self.assertFalse(appliance.quiesce())
                self.system.stop_verified.assert_called_once_with(MODULE.VOICE)
                self.kiosk.quiesce.assert_not_called()
                self.system.suspend_verified.assert_not_called()

    def test_unknown_model_activity_never_marks_drained(self):
        appliance = self.appliance()
        self.receipts(appliance)
        self.replace(MODULE, "model_idle", return_value=False)
        self.assertFalse(appliance.quiesce())
        self.assertFalse(appliance.drained)
        self.system.suspend_verified.assert_not_called()
        self.assertLessEqual(self.now, 131)

    def test_new_input_during_model_drain_prevents_sleep_submission(self):
        appliance = self.appliance()
        self.receipts(appliance)
        self.replace(MODULE, "model_idle", side_effect=[False, True])
        self.assertTrue(appliance.quiesce())
        self.idle.return_value = 0
        self.assertFalse(appliance.sleep())
        self.assertFalse(appliance.submitted)
        self.system.suspend_verified.assert_not_called()
        self.assertFalse(any(call.args[0] == MODULE.AUTHORIZATION for call in self.publish.call_args_list))

    def test_cancelled_sleep_does_not_read_idle_or_publish_authorization(self):
        appliance = self.appliance()
        appliance.cancelled = lambda: True
        self.assertFalse(appliance.sleep())
        self.idle.assert_not_called()
        self.publish.assert_not_called()
        self.system.suspend_verified.assert_not_called()

    def test_wake_waits_for_positive_lock_before_other_services(self):
        appliance = self.appliance()
        self.locked.side_effect = [False, ValueError("unknown"), True]
        self.assertTrue(appliance.start_locked())
        self.assertEqual([call.args[0] for call in self.command.call_args_list], [
            ["/usr/bin/systemctl", "start", MODULE.AUTH],
            ["/usr/bin/systemctl", "start", MODULE.VOICE, MODULE.CAMERA, MODULE.UI, MODULE.DISPLAY],
        ])
        self.assertEqual(self.locked.call_count, 3)
        self.kiosk.resume.assert_called_once()

    def test_wake_without_positive_lock_never_restarts_private_services(self):
        appliance = self.appliance()
        self.locked.return_value = False
        self.assertFalse(appliance.start_locked())
        self.command.assert_called_once_with(["/usr/bin/systemctl", "start", MODULE.AUTH], timeout=10)
        self.kiosk.resume.assert_not_called()
        self.display.assert_not_called()

    def test_display_on_only_after_locked_service_start_and_kiosk_resume(self):
        appliance = self.appliance()
        self.command.side_effect = lambda arguments, **kwargs: self.events.append(tuple(arguments[2:]))
        self.locked.side_effect = lambda: (self.events.append("locked"), True)[1]
        self.kiosk.resume.side_effect = lambda: (self.events.append("kiosk-resumed"), True)[1]
        self.display.side_effect = lambda action: self.events.append("display-" + action)
        self.assertTrue(appliance.start_locked())
        self.assertEqual(self.events, [(MODULE.AUTH,), "locked",
                                      (MODULE.VOICE, MODULE.CAMERA, MODULE.UI, MODULE.DISPLAY),
                                      "kiosk-resumed", "display-on"])

    def test_kiosk_quiesce_failure_does_not_blank_display(self):
        appliance = self.appliance()
        self.receipts(appliance)
        self.kiosk.quiesce.return_value = False
        self.assertFalse(appliance.quiesce())
        self.display.assert_not_called()
        self.system.suspend_verified.assert_not_called()

    def test_kiosk_resume_failure_does_not_turn_display_on(self):
        appliance = self.appliance()
        self.kiosk.resume.return_value = False
        self.assertFalse(appliance.start_locked())
        self.display.assert_not_called()

    def test_shutdown_during_authorization_publish_cancels_before_suspend(self):
        appliance = self.appliance()
        cancelled = [False]
        appliance.cancelled = lambda: cancelled[0]
        self.replace(Path, "read_text", side_effect=["1 (proc) " + " ".join(["S"] + ["0"] * 18 + ["456"]), "12"])
        self.publish.side_effect = lambda *args, **kwargs: cancelled.__setitem__(0, True)
        self.assertFalse(appliance.sleep())
        self.assertFalse(appliance.submitted)
        self.system.suspend_verified.assert_not_called()

    def test_input_during_authorization_publish_cancels_before_suspend(self):
        appliance = self.appliance()
        self.replace(Path, "read_text", side_effect=["1 (proc) " + " ".join(["S"] + ["0"] * 18 + ["456"]), "12"])
        self.publish.side_effect = lambda *args, **kwargs: setattr(self.idle, "return_value", 0)
        self.assertFalse(appliance.sleep())
        self.assertFalse(appliance.submitted)
        self.system.suspend_verified.assert_not_called()

    def test_inference_uncertainty_blocks_quiesce_before_model_query(self):
        appliance = self.appliance()
        self.receipts(appliance)
        self.inference.return_value = False
        self.assertFalse(appliance.quiesce())
        self.assertFalse(appliance.drained)
        self.public.assert_not_called()
        self.system.suspend_verified.assert_not_called()

    def test_real_cycle_orders_auth_drain_idle_sleep_and_locked_restart(self):
        journal = Mock()
        journal.load.return_value = SimpleNamespace(needs_recovery=False)
        platform = Mock()
        platform.fenced.return_value = False
        gates = ("retain_fence", "stop_auth", "auth_inactive", "quiesce", "idle_eligible",
                 "sleep", "resumed", "release_fence", "start_locked")
        for gate in gates:
            getattr(platform, gate).return_value = True
        MODULE.SleepCycle(journal, platform, "transition-123").run()
        calls = [call[0] for call in platform.mock_calls]
        sleep = calls.index("sleep")
        self.assertEqual(calls[:sleep + 1], [
            "fenced", "retain_fence", "stop_auth", "auth_inactive", "quiesce", "idle_eligible",
            "retain_fence", "auth_inactive", "quiesce", "idle_eligible", "sleep",
        ])
        self.assertLess(calls.index("resumed"), calls.index("release_fence"))
        self.assertLess(calls.index("release_fence"), calls.index("start_locked"))


class MainFlowTests(MockedPlatform):
    def main_platform(self, iterations=1):
        self.replace(MODULE, "SuspendSystem", return_value=self.system)
        self.replace(MODULE, "guard_loaded", return_value=True)
        self.hooks = self.replace(MODULE, "drain_hooks_ready", return_value=True)
        self.policy = self.replace(MODULE, "power_policy", return_value=dict(POLICY))
        self.replace(MODULE, "kiosk_fence", return_value=self.kiosk)
        self.replace(MODULE, "model_idle", return_value=True)
        self.journal = Mock()
        self.journal.load.return_value = SimpleNamespace(needs_recovery=False)
        self.journal.__enter__ = Mock(return_value=self.journal)
        self.journal.__exit__ = Mock(return_value=False)
        self.replace(MODULE, "SuspendJournal", return_value=self.journal)
        self.cycle = self.replace(MODULE, "SleepCycle")
        self.cycle.return_value.run.return_value = SimpleNamespace(phase=SimpleNamespace(value="resumed"))
        self.appliance_constructor = self.replace(MODULE, "Appliance")
        self.stopped = Mock()
        self.stopped.is_set.return_value = False
        self.stopped.set.side_effect = lambda: setattr(self.stopped.is_set, "return_value", True)
        waits = [0]

        def wait(seconds):
            self.now += seconds
            waits[0] += 1
            if waits[0] >= iterations:
                self.stopped.set()

        self.stopped.wait.side_effect = wait
        self.replace(MODULE.threading, "Event", return_value=self.stopped)
        self.signals = {}
        self.replace(MODULE.signal, "signal", side_effect=lambda number, callback: self.signals.__setitem__(number, callback))
        self.stack.enter_context(patch("sys.argv", [str(SCRIPT)]))
        self.stack.enter_context(patch("builtins.print"))

    def test_main_uses_saved_delay_and_starts_cycle_only_after_full_observation(self):
        self.main_platform(iterations=61)
        self.assertEqual(MODULE.main(), 0)
        self.cycle.return_value.run.assert_called_once()
        self.assertEqual(self.appliance_constructor.call_args.args[1], POLICY)
        self.assertEqual(self.appliance_constructor.call_args.kwargs["cancelled"], self.stopped.is_set)
        self.kiosk.close.assert_called_once()
        self.capability.assert_called_with(False)

    def test_once_timed_wake_preserves_full_saved_delay(self):
        self.main_platform(iterations=100)
        self.stack.enter_context(patch("sys.argv", [str(SCRIPT), "--once", "--test-wake-after", "60"]))
        self.assertEqual(MODULE.main(), 0)
        self.assertEqual(self.now, 400)
        self.assertEqual(self.stopped.wait.call_count, 60)
        self.cycle.return_value.run.assert_called_once()
        self.assertEqual(self.appliance_constructor.call_args.args[1]["deep_sleep_seconds"], 300)
        self.assertEqual(self.appliance_constructor.call_args.kwargs["test_wake_after"], 60)
        self.command.assert_not_called()
        self.kiosk.close.assert_called_once()
        self.capability.assert_called_with(False)

    def test_timed_wake_rejects_missing_once_and_arbitrary_short_timer(self):
        for arguments in (("--test-wake-after", "60"), ("--once", "--test-wake-after", "5")):
            with self.subTest(arguments=arguments), patch("sys.argv", [str(SCRIPT), *arguments]), \
                    patch("sys.stderr"), patch.object(MODULE, "SuspendSystem") as system:
                with self.assertRaises(SystemExit) as caught:
                    MODULE.main()
                self.assertEqual(caught.exception.code, 2)
                system.assert_not_called()
        self.command.assert_not_called()

    def test_malformed_policy_never_enters_cycle_and_revokes_capability(self):
        self.main_platform()
        self.policy.side_effect = ValueError("malformed")
        self.assertEqual(MODULE.main(), 0)
        self.cycle.assert_not_called()
        self.assertTrue(all(call.args == (False,) for call in self.capability.call_args_list))

    def test_missing_drain_hooks_never_advertise_readiness(self):
        self.main_platform()
        self.hooks.return_value = False
        self.assertEqual(MODULE.main(), 0)
        self.cycle.assert_not_called()
        self.assertTrue(all(call.args == (False,) for call in self.capability.call_args_list))

    def test_inference_not_ready_never_advertises_or_starts_cycle(self):
        self.main_platform()
        self.inference.return_value = False
        self.assertEqual(MODULE.main(), 0)
        self.cycle.assert_not_called()
        self.assertTrue(all(call.args == (False,) for call in self.capability.call_args_list))

    def test_new_input_near_eligibility_resets_countdown(self):
        self.main_platform(iterations=62)
        self.idle.side_effect = [600] * 59 + [0, 1, 2]
        self.assertEqual(MODULE.main(), 0)
        self.cycle.assert_not_called()

    def test_policy_change_at_final_recheck_does_not_start_cycle(self):
        self.main_platform(iterations=62)
        changed = {**POLICY, "revision": 2, "deep_sleep_seconds": 600}
        self.policy.side_effect = [dict(POLICY)] * 61 + [changed] * 4
        self.assertEqual(MODULE.main(), 0)
        self.cycle.assert_not_called()

    def test_signal_cancels_loop_and_clears_capability(self):
        self.main_platform(iterations=100)
        self.stopped.wait.side_effect = lambda seconds: self.signals[MODULE.signal.SIGTERM](MODULE.signal.SIGTERM, None)
        self.assertEqual(MODULE.main(), 0)
        self.stopped.wait.assert_called_once_with(5)
        self.cycle.assert_not_called()
        self.capability.assert_called_with(False)

    def test_existing_fence_refuses_new_cycle_and_revokes_capability(self):
        self.main_platform()
        self.fence.exists.return_value = True
        with self.assertRaisesRegex(RuntimeError, "sleep_recovery_required"):
            MODULE.main()
        self.cycle.assert_not_called()
        self.capability.assert_called_with(False)


class ReadinessGateTests(MockedPlatform):
    def model_baseline(self):
        baseline = dict(version=1, pid=44, invocation_id="c" * 32, start_ticks="456", boot_id="test-boot")
        self.read.return_value = baseline
        self.identity.side_effect = None
        self.identity.return_value = (44, "c" * 32)
        self.replace(Path, "read_text", return_value="44 (model) " + " ".join(["S"] + ["0"] * 18 + ["456"]))
        self.command.return_value = "SAGE_LANTERN_INFERENCE_GUARD=" + str(MODULE.INFERENCE_DIRECTORY)
        return baseline

    def test_inference_requires_same_model_identity_and_zero_pending(self):
        self.model_baseline()
        self.assertTrue(INFERENCE_READY())
        self.read.assert_called_with(MODULE.MODEL_BASELINE, owner_uid=0)
        self.identity.assert_called_with(MODULE.MODEL)
        self.pending.assert_called_once_with(MODULE.INFERENCE_DIRECTORY, owner_uid=123)
        self.command.assert_called_once_with(["/usr/bin/systemctl", "show", MODULE.VOICE,
                                              "--property=Environment", "--value"])

    def test_missing_or_changed_model_baseline_rejects(self):
        baseline = self.model_baseline()
        for value in (None, {}, {**baseline, "pid": 45}, {**baseline, "invocation_id": "d" * 32},
                      {**baseline, "start_ticks": "457"}, {**baseline, "boot_id": "previous-boot"}):
            with self.subTest(value=value):
                self.read.return_value = value
                self.pending.reset_mock()
                self.assertFalse(INFERENCE_READY())
                self.pending.assert_not_called()

    def test_pending_inference_prevents_ready_even_when_model_identity_matches(self):
        self.model_baseline()
        self.pending.return_value = 1
        self.assertFalse(INFERENCE_READY())
        self.command.assert_not_called()

    def test_inference_environment_binding_cannot_be_missing_or_prefix_match(self):
        self.model_baseline()
        for environment in ("", "SAGE_LANTERN_INFERENCE_GUARD=/wrong",
                            "PREFIX_SAGE_LANTERN_INFERENCE_GUARD=" + str(MODULE.INFERENCE_DIRECTORY)):
            with self.subTest(environment=environment):
                self.command.return_value = environment
                self.assertFalse(INFERENCE_READY())

    def test_sleep_ready_requires_exact_positive_public_response(self):
        for response in (None, {}, {"ready": False}, {"ready": 1}, {"ready": True, "extra": True}):
            with self.subTest(response=response):
                self.public.return_value = response
                self.assertFalse(MODULE.drain_hooks_ready())
        self.read.assert_not_called()
        self.public.assert_called_with(3196, "/sleep-ready", limit=256)

    def test_public_sleep_ready_does_not_replace_instance_receipts(self):
        self.public.return_value = {"ready": True}
        self.read.return_value = None
        self.assertFalse(MODULE.drain_hooks_ready())
        self.read.side_effect = [dict(version=1, instance_id="a" * 32, closing=False),
                                dict(pid=33, invocation_id="b" * 32, boot_id="test-boot")]
        self.assertTrue(MODULE.drain_hooks_ready())

    def test_prior_auth_uncertainty_blocks_public_readiness_before_fencing(self):
        self.public.return_value = {"ready": True}
        self.prior_auth.return_value = False
        self.assertFalse(MODULE.drain_hooks_ready())
        self.prior_auth.assert_called_once_with("a" * 32, 123)
        self.publish.assert_not_called()
        self.command.assert_not_called()


class HeartbeatFlowTests(MockedPlatform):
    def test_published_capability_expires_without_runner_refresh(self):
        self.stack.enter_context(patch.object(MODULE.time, "CLOCK_BOOTTIME", 7, create=True))
        self.replace(MODULE.time, "clock_gettime", return_value=100)
        PUBLISH_CAPABILITY(True)
        raw = self.publish.call_args.args[1]
        self.assertEqual(raw, dict(version=1, available=True, boot_id="test-boot", updated_boottime=100))
        path = Mock()
        path.resolve.return_value = path
        self.replace(suspend_capability.os, "open", return_value=42)
        self.replace(suspend_capability.os, "fstat", return_value=SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644, st_uid=0, st_nlink=1, st_size=100))
        self.replace(suspend_capability.os, "read", return_value=json.dumps(raw).encode())
        self.replace(suspend_capability.os, "close")
        self.assertTrue(suspend_capability.available(path=path, boot_path=MODULE.BOOT, now=115))
        self.assertFalse(suspend_capability.available(path=path, boot_path=MODULE.BOOT, now=115.01))
        self.assertFalse(suspend_capability.available(path=path, boot_path=MODULE.BOOT, now=99))


if __name__ == "__main__":
    unittest.main()
