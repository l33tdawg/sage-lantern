import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/lantern-display-power.py"
SPEC = importlib.util.spec_from_file_location("lantern_display_power", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DisplayPowerTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.locked = True
        self.idle = 60.0
        self.command = Mock(return_value=True)
        self.policy = dict(revision=0, display_off_seconds=30, deep_sleep_seconds=300, deep_sleep_available=False)
        self.controller = MODULE.DisplayPower(
            state=lambda: self.locked, command=self.command, clock=lambda: self.now,
            idle=lambda: self.idle,
            policy=lambda: self.policy,
        )

    def tick(self, seconds=1):
        for _ in range(seconds):
            self.controller.step()
            self.now += 1

    def actions(self):
        return [call.args[0] for call in self.command.call_args_list]

    def test_policy_delay_is_dynamic_and_sleep_never_executes(self):
        self.policy.update(display_off_seconds=60, deep_sleep_seconds=600)
        self.tick(60)
        self.assertEqual(self.actions(), [])
        self.tick()
        self.assertEqual(self.actions(), ["timers_off", "enable", "off"])

    def test_policy_change_wakes_and_restarts_full_grace(self):
        self.tick(31)
        self.policy.update(revision=1, display_off_seconds=60, deep_sleep_seconds=600)
        self.tick(60)
        self.assertEqual(self.actions(), ["timers_off", "enable", "off", "on"])
        self.tick()
        self.assertEqual(self.actions().count("off"), 2)

    def test_missing_policy_wakes_and_never_defaults(self):
        self.tick(31)
        self.policy = None
        self.tick(100)
        self.assertEqual(self.actions(), ["timers_off", "enable", "off", "on"])

    def test_unknown_policy_and_invalid_delay_never_blank(self):
        for value in (None, {}, dict(self.policy, revision=True), dict(self.policy, deep_sleep_available=1),
                      dict(self.policy, display_off_seconds=300)):
            self.controller.policy = lambda: value
            self.tick(100)
            self.assertEqual(self.actions(), [])

    def test_policy_change_during_xset_prevents_blank(self):
        self.tick(30)
        self.controller.policy = Mock(side_effect=[self.policy, dict(self.policy, revision=1)])
        self.tick()
        self.assertEqual(self.actions(), ["timers_off", "enable"])

    def test_policy_revision_safe_integer_boundary(self):
        self.policy["revision"] = (1 << 53) - 1
        self.assertEqual(MODULE.validated_policy(self.policy), self.policy)
        self.tick(31)
        self.policy["revision"] += 1
        self.assertIsNone(MODULE.validated_policy(self.policy))
        self.tick(100)
        self.assertEqual(self.actions(), ["timers_off", "enable", "off", "on"])

    def test_thirty_seconds_positive_lock_and_fixed_order(self):
        self.tick(30)
        self.assertEqual(self.actions(), [])
        self.tick()
        self.assertEqual(self.actions(), ["timers_off", "enable", "off"])

    def test_never_blank_unlocked_or_unknown(self):
        for state in (False, None, 1, "locked"):
            with self.subTest(state=state):
                self.locked = state
                self.tick(100)
                self.assertEqual(self.actions(), [])

    def test_unknown_resets_countdown(self):
        self.tick(29)
        self.locked = None
        self.tick()
        self.locked = True
        self.tick(30)
        self.assertEqual(self.actions(), [])
        self.tick()
        self.assertEqual(self.actions().count("off"), 1)

    def test_pin_input_delays_initial_blank_until_thirty_seconds_idle(self):
        self.idle = 0
        self.tick(100)
        self.assertEqual(self.actions(), [])
        self.idle = 29.999
        self.tick()
        self.assertEqual(self.actions(), [])
        self.idle = 30
        self.tick()
        self.assertEqual(self.actions(), ["timers_off", "enable", "off"])

    def test_unavailable_or_invalid_idle_never_blanks(self):
        for idle in (None, -1, True, "60", float("nan"), float("inf")):
            with self.subTest(idle=idle):
                self.idle = idle
                self.tick(100)
                self.assertEqual(self.actions(), [])

    def test_input_during_configuration_aborts_blank(self):
        self.tick(30)
        self.controller.idle = Mock(side_effect=[60, 0])
        self.tick()
        self.assertEqual(self.actions(), ["timers_off", "enable"])

    def test_poll_gap_or_backwards_clock_resets_countdown(self):
        for jump in (100, -100):
            with self.subTest(jump=jump):
                self.setUp()
                self.tick(29)
                self.now += jump
                self.tick(30)
                self.assertEqual(self.actions(), [])
                self.tick()
                self.assertEqual(self.actions().count("off"), 1)

    def test_no_input_never_reblanked_in_same_lock_epoch(self):
        self.tick(31)
        self.command.reset_mock()
        self.tick(180)
        self.assertEqual(self.actions(), [])

    def test_unknown_wakes_but_does_not_rearm(self):
        self.tick(31)
        self.locked = None
        self.tick(10)
        self.locked = True
        self.tick(100)
        self.assertEqual(self.actions(), ["timers_off", "enable", "off", "on"])

    def test_unlock_wakes_and_next_lock_rearms(self):
        self.tick(31)
        self.locked = False
        self.tick(10)
        self.locked = True
        self.tick(31)
        self.assertEqual(self.actions(), ["timers_off", "enable", "off", "on", "timers_off", "enable", "off"])

    def test_configuration_failure_has_bounded_backoff(self):
        for failure in ("timers_off", "enable"):
            with self.subTest(failure=failure):
                self.setUp()
                self.command.side_effect = lambda action: action != failure
                self.tick(60)
                self.assertEqual(self.actions().count(failure), 1)
                self.tick()
                self.assertEqual(self.actions().count(failure), 2)
                self.tick(59)
                self.assertEqual(self.actions().count(failure), 2)
                self.tick(100)
                self.assertNotIn("off", self.actions())
                self.assertEqual(self.actions().count(failure), 3)

    def test_touch_wake_rearms_only_after_fresh_grace_and_idle(self):
        self.tick(31)
        self.idle = 0
        self.tick()
        self.idle = 60
        self.tick(29)
        self.assertEqual(self.actions().count("off"), 1)
        self.tick()
        self.assertEqual(self.actions().count("off"), 2)
        self.tick(100)
        self.assertEqual(self.actions().count("off"), 2)

    def test_continued_pin_entry_prevents_reblank(self):
        self.tick(31)
        for _ in range(5):
            self.idle = 0
            self.tick()
            self.idle = 10
            self.tick(20)
        self.assertEqual(self.actions().count("off"), 1)

    def test_idle_failure_cannot_synthesize_input(self):
        self.tick(31)
        self.idle = None
        self.tick()
        self.idle = 0
        self.tick()
        self.idle = 60
        self.tick(100)
        self.assertEqual(self.actions().count("off"), 1)

    def test_config_retry_can_recover(self):
        self.command.side_effect = lambda action: action != "enable"
        self.tick(31)
        self.command.side_effect = None
        self.tick(30)
        self.assertEqual(self.actions().count("off"), 1)

    def test_uncertain_off_retries_only_after_new_input(self):
        self.command.side_effect = lambda action: action != "off"
        self.tick(100)
        self.assertEqual(self.actions().count("off"), 1)
        self.idle = 0
        self.tick()
        self.idle = 60
        self.tick(30)
        self.assertEqual(self.actions().count("off"), 2)

    def test_recheck_after_configuration_prevents_stale_blank(self):
        for state in (None, False):
            with self.subTest(state=state):
                self.setUp()
                self.tick(30)
                self.controller.state = Mock(side_effect=[True, state])
                self.controller.step()
                self.assertEqual(self.actions(), ["timers_off", "enable"])

    def test_uncertain_off_attempt_wakes_without_reblanking(self):
        self.command.side_effect = lambda action: action != "off"
        self.tick(100)
        self.assertEqual(self.actions(), ["timers_off", "enable", "off", "on"])

    def test_failed_wake_retries_on_unknown(self):
        self.tick(31)
        self.locked = None
        self.command.side_effect = [False, True]
        self.tick(3)
        self.assertEqual(self.actions()[-2:], ["on", "on"])
        self.assertFalse(self.controller.maybe_off)

    def test_cleanup_wakes_once(self):
        self.tick(31)
        self.controller.wake()
        self.controller.wake()
        self.assertEqual(self.actions().count("on"), 1)


class PublicStateTests(unittest.TestCase):
    def read(self, body, status=200, content_type="application/json"):
        response = Mock(status=status)
        response.getheader.return_value = content_type
        response.read1.side_effect = io.BytesIO(body).read
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(MODULE.http.client, "HTTPConnection", return_value=connection) as factory:
            result = MODULE.read_locked()
        factory.assert_called_once_with("127.0.0.1", 3196, timeout=0.5)
        connection.request.assert_called_once_with("GET", "/state", headers={"Connection": "close"})
        connection.close.assert_called_once()
        return result

    def test_exact_contract(self):
        self.assertIs(self.read(b'{"locked":true}'), True)
        self.assertIs(self.read(b'{"locked":false}'), False)

    def test_malformed_duplicate_extra_or_oversized_state_is_unknown(self):
        for body in (b'{}', b'null', b'[]', b'{"locked":1}', b'{"locked":"true"}',
                     b'{"locked":true,"locked":false}', b'{"locked":true,"token":"x"}',
                     b'{"locked":true}' + b' ' * 256, b'\xff', b'{'):
            with self.subTest(body=body):
                self.assertIsNone(self.read(body))

    def test_status_and_content_type_rejected(self):
        self.assertIsNone(self.read(b'{"locked":true}', status=503))
        self.assertIsNone(self.read(b'{"locked":true}', content_type="text/html"))

    def test_transport_failure_is_unknown_and_closed(self):
        connection = Mock()
        connection.connect.side_effect = TimeoutError
        with patch.object(MODULE.http.client, "HTTPConnection", return_value=connection):
            self.assertIsNone(MODULE.read_locked())
        connection.close.assert_called_once()

    def test_total_body_deadline(self):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.return_value = "application/json"
        with patch.object(MODULE.http.client, "HTTPConnection", return_value=connection), \
                patch.object(MODULE.time, "monotonic", side_effect=[0, 0.1, 0.6]):
            self.assertIsNone(MODULE.read_locked())
        response.read1.assert_not_called()


class XsetTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(MODULE.os.environ, DISPLAY=":0")
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_selected_local_display_reaches_commands_and_idle_child(self):
        for display in (":1", ":12.0"):
            with self.subTest(display=display), patch.dict(MODULE.os.environ, DISPLAY=display), \
                    patch.object(MODULE.subprocess, "run", return_value=Mock(returncode=0, stdout=b"30000")) as run:
                self.assertTrue(MODULE.run_xset("on"))
                self.assertEqual(MODULE.read_idle_seconds(), 30)
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs["env"]["DISPLAY"], display)

    def test_missing_or_remote_display_never_runs_x_commands(self):
        for display in (None, "", "localhost:10.0", "host:0", ":1\n", ":-1", ":12345", ":0;id"):
            with self.subTest(display=display), patch.dict(MODULE.os.environ, {}, clear=True), \
                    patch.object(MODULE.subprocess, "run") as run:
                if display is not None:
                    MODULE.os.environ["DISPLAY"] = display
                self.assertFalse(MODULE.run_xset("off"))
                self.assertIsNone(MODULE.read_idle_seconds())
                run.assert_not_called()

    def test_missing_xprintidle_uses_bounded_same_script_child(self):
        with patch.object(MODULE.subprocess, "run", side_effect=[
            FileNotFoundError(), Mock(returncode=0, stdout=b"31000\n"),
        ]) as run:
            self.assertEqual(MODULE.read_idle_seconds(), 31)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args, ((MODULE.sys.executable, str(SCRIPT), "--query-idle"),))
        self.assertEqual(run.call_args.kwargs["timeout"], 2.0)
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_child_failure_timeout_crash_or_malformed_is_unknown(self):
        for result in (Mock(returncode=0, stdout=b"oops"), Mock(returncode=-11, stdout=b"30000"),
                       Mock(returncode=1, stdout=b""), subprocess.TimeoutExpired("child", 2)):
            with self.subTest(result=result), patch.object(MODULE.subprocess, "run", side_effect=[FileNotFoundError(), result]):
                self.assertIsNone(MODULE.read_idle_seconds())

    def test_existing_xprintidle_failure_does_not_fallback(self):
        with patch.object(MODULE.subprocess, "run", return_value=Mock(returncode=1, stdout=b"")) as run:
            self.assertIsNone(MODULE.read_idle_seconds())
        self.assertEqual(run.call_count, 1)

    def test_idle_query_is_fixed_bounded_and_read_only(self):
        with patch.object(MODULE.subprocess, "run", return_value=Mock(returncode=0, stdout=b"30000\n")) as run:
            self.assertEqual(MODULE.read_idle_seconds(), 30)
        self.assertEqual(run.call_args.args, (("/usr/bin/xprintidle",),))
        self.assertEqual(run.call_args.kwargs["timeout"], 2.0)
        self.assertEqual(run.call_args.kwargs["env"]["DISPLAY"], ":0")
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_idle_query_invalid_or_failed_is_unknown(self):
        for raw in (b"", b"-1", b"1.0", b"nan", b"4294967296", b"9" * 100, b"1\n2"):
            with self.subTest(raw=raw), patch.object(MODULE.subprocess, "run", return_value=Mock(returncode=0, stdout=raw)):
                self.assertIsNone(MODULE.read_idle_seconds())
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("xprintidle", 2)):
            with self.subTest(error=error), patch.object(MODULE.subprocess, "run", side_effect=error):
                self.assertIsNone(MODULE.read_idle_seconds())
        with patch.object(MODULE.subprocess, "run", return_value=Mock(returncode=1, stdout=b"60000")):
            self.assertIsNone(MODULE.read_idle_seconds())

    def test_commands_are_fixed_bounded_and_silent(self):
        with patch.object(MODULE.subprocess, "run", return_value=Mock(returncode=0)) as run:
            for action, command in MODULE.COMMANDS.items():
                self.assertTrue(MODULE.run_xset(action))
                self.assertEqual(run.call_args.args, (command,))
                options = run.call_args.kwargs
                self.assertEqual(options["env"]["DISPLAY"], ":0")
                self.assertEqual(options["timeout"], 2.0)
                self.assertIs(options["shell"], False)
                for stream in ("stdin", "stdout", "stderr"):
                    self.assertEqual(options[stream], subprocess.DEVNULL)

    def test_command_failures(self):
        for error in (OSError(), subprocess.TimeoutExpired("xset", 2)):
            with self.subTest(error=error), patch.object(MODULE.subprocess, "run", side_effect=error):
                self.assertFalse(MODULE.run_xset("off"))
        with patch.object(MODULE.subprocess, "run", return_value=Mock(returncode=1)):
            self.assertFalse(MODULE.run_xset("off"))

    def test_arbitrary_commands_rejected(self):
        with patch.object(MODULE.subprocess, "run") as run:
            with self.assertRaises(KeyError):
                MODULE.run_xset("force off; anything")
        run.assert_not_called()


class NativeIdleTests(unittest.TestCase):
    def setUp(self):
        patch.dict(MODULE.os.environ, DISPLAY=":0").start()
        self.x11 = Mock()
        self.xss = Mock()
        self.x11.XOpenDisplay.return_value = 123
        self.x11.XDefaultRootWindow.return_value = 456
        self.xss.XScreenSaverQueryExtension.return_value = 1

        def query(display, root, info):
            MODULE.ctypes.cast(info, MODULE.ctypes.POINTER(MODULE.XScreenSaverInfo)).contents.idle = 32000
            return 1

        self.xss.XScreenSaverQueryInfo.side_effect = query
        self.loader = patch.object(MODULE.ctypes, "CDLL", side_effect=[self.x11, self.xss]).start()
        self.addCleanup(patch.stopall)

    def test_native_query_uses_selected_local_display(self):
        with patch.dict(MODULE.os.environ, DISPLAY=":1"):
            self.assertEqual(MODULE.query_native_idle(), 32000)
        self.x11.XOpenDisplay.assert_called_once_with(b":1")

    def test_missing_or_remote_display_never_opens_x11(self):
        for display in (None, "", "localhost:0", ":0\n"):
            with self.subTest(display=display), patch.dict(MODULE.os.environ, {}, clear=True):
                if display is not None:
                    MODULE.os.environ["DISPLAY"] = display
                self.assertIsNone(MODULE.query_native_idle())
                self.loader.assert_not_called()

    def test_native_query_exact_libraries_abi_and_cleanup(self):
        self.assertEqual(MODULE.query_native_idle(), 32000)
        self.assertEqual([call.args[0] for call in self.loader.call_args_list], ["libX11.so.6", "libXss.so.1"])
        self.x11.XOpenDisplay.assert_called_once_with(b":0")
        self.assertIs(self.x11.XOpenDisplay.restype, MODULE.ctypes.c_void_p)
        self.assertIs(self.x11.XDefaultRootWindow.restype, MODULE.ctypes.c_ulong)
        self.assertEqual(self.xss.XScreenSaverQueryInfo.argtypes, [
            MODULE.ctypes.c_void_p, MODULE.ctypes.c_ulong, MODULE.ctypes.POINTER(MODULE.XScreenSaverInfo),
        ])
        self.x11.XCloseDisplay.assert_called_once_with(123)

    def test_no_display_is_unknown(self):
        self.x11.XOpenDisplay.return_value = None
        self.assertIsNone(MODULE.query_native_idle())
        self.xss.XScreenSaverQueryInfo.assert_not_called()
        self.x11.XCloseDisplay.assert_not_called()

    def test_missing_library_is_unknown(self):
        self.loader.side_effect = OSError
        self.assertIsNone(MODULE.query_native_idle())
        self.x11.XOpenDisplay.assert_not_called()

    def test_missing_extension_is_unknown_and_closes_display(self):
        self.xss.XScreenSaverQueryExtension.return_value = 0
        self.assertIsNone(MODULE.query_native_idle())
        self.xss.XScreenSaverQueryInfo.assert_not_called()
        self.x11.XCloseDisplay.assert_called_once_with(123)

    def test_failed_query_is_unknown_and_closes_display(self):
        self.xss.XScreenSaverQueryInfo.side_effect = None
        self.xss.XScreenSaverQueryInfo.return_value = 0
        self.assertIsNone(MODULE.query_native_idle())
        self.x11.XCloseDisplay.assert_called_once_with(123)

    def test_child_mode_only_queries_and_prints_milliseconds(self):
        with patch.object(MODULE, "DisplayPower") as controller, patch("builtins.print") as output, \
                patch.object(MODULE.signal, "alarm") as alarm:
            self.assertEqual(MODULE.main(["--query-idle"]), 0)
        controller.assert_not_called()
        output.assert_called_once_with(32000)
        self.assertEqual([call.args for call in alarm.call_args_list], [(2,), (0,)])

    def test_child_no_display_fails_silently(self):
        self.x11.XOpenDisplay.return_value = None
        with patch("builtins.print") as output, patch.object(MODULE.signal, "alarm") as alarm:
            self.assertEqual(MODULE.main(["--query-idle"]), 1)
        output.assert_not_called()
        self.assertEqual([call.args for call in alarm.call_args_list], [(2,), (0,)])

    def test_unknown_cli_arguments_cannot_start_controller(self):
        with patch.object(MODULE, "DisplayPower") as controller:
            self.assertEqual(MODULE.main(["--query-idle", ":1"]), 2)
        self.loader.assert_not_called()
        controller.assert_not_called()


if __name__ == "__main__":
    unittest.main()
