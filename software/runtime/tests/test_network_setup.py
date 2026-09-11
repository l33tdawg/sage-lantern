import importlib
import subprocess
import sys
import time
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sage_lantern import network_setup as module


def result(output=b"", code=0):
    return SimpleNamespace(returncode=code, stdout=output)


def ap(ssid="Home", security="WPA2", signal=70, active=False, bssid="00:11:22:33:44:55"):
    def escape(value):
        return value.replace("\\", "\\\\").replace(":", "\\:")

    return (":".join(escape(value) for value in ("*" if active else "", bssid, ssid, str(signal), security)) + "\n").encode()


DISCONNECTED = b"wifi\n30 (disconnected)\n"
CONNECTED = b"wifi\n100 (connected)\n"
IDENTIFIER = "7d850b66-260f-42f4-96d8-554fddf9dcd7"


class NetworkSetupTests(unittest.TestCase):
    def setUp(self):
        identifier = patch.object(module.uuid, "uuid4", return_value=IDENTIFIER)
        identifier.start()
        self.addCleanup(identifier.stop)
        self.runner = Mock()
        self.network = module.NetworkSetup("wlan0", self.runner)

    def replies(self, *outputs):
        self.runner.side_effect = [result(output) for output in outputs]

    def successful_connect(self, ssid="Home", security="WPA2"):
        self.replies(DISCONNECTED, ap(ssid, security), b"", DISCONNECTED, b"", CONNECTED,
                     ap(ssid, security, active=True), (IDENTIFIER + "\n").encode(), b"",
                     (IDENTIFIER + "\nyes\n").encode())

    def test_import_and_constructor_do_not_operate(self):
        with patch.object(subprocess, "run") as run:
            importlib.reload(module)
            module.NetworkSetup("wlan0")
        run.assert_not_called()

    def test_interface_validation(self):
        for interface in (None, "", "-wlan0", "wlan0;reboot", "wlan0\n", "a" * 16, True):
            with self.subTest(interface=interface), self.assertRaises(module.NetworkSetupError):
                module.NetworkSetup(interface, self.runner)
        self.runner.assert_not_called()

    def test_disconnected_status_no_scan(self):
        self.replies(DISCONNECTED)
        self.assertEqual(self.network.status(), {"connected": False, "ssid": None})
        self.assertEqual(self.runner.call_count, 1)

    def test_connected_status_not_profile_name(self):
        self.replies(CONNECTED, ap("Actual SSID", active=True))
        self.assertEqual(self.network.status(), {"connected": True, "ssid": "Actual SSID"})
        self.assertEqual(self.runner.call_args.args[0][-2:], ["--rescan", "no"])

    def test_ethernet_and_malformed_state_refused(self):
        for state in (b"ethernet\n100 (connected)\n", b"wifi\nunknown\n", b"wifi\n100\n", b"wifi\n999 (bad)\n"):
            with self.subTest(state=state):
                self.replies(state)
                with self.assertRaises(module.NetworkSetupError):
                    self.network.status()

    def test_scan_security_and_escape_parser(self):
        self.replies(DISCONNECTED, ap(r"Cafe:one\two") + ap("Free", "--") + ap("Work", "WPA2 802.1X")
                     + ap("Old", "WEP") + ap("New", "WPA3") + ap("Hidden", "OWE"))
        entries = {entry["ssid"]: entry for entry in self.network.scan()}
        self.assertEqual(entries[r"Cafe:one\two"]["security"], "wpa-psk")
        self.assertEqual(entries["Free"]["security"], "open")
        for name in ("Work", "Old", "New", "Hidden"):
            self.assertEqual(entries[name]["security"], "unsupported")
        self.assertEqual(self.runner.call_args.args[0][-2:], ["--rescan", "yes"])
        self.assertTrue(all(set(entry) == {"ssid", "signal", "security"} for entry in entries.values()))

    def test_scan_deduplicates_and_refuses_security_ambiguity(self):
        self.replies(DISCONNECTED, ap(signal=50) + ap(signal=90) + ap("Mixed") + ap("Mixed", "--"))
        entries = self.network.scan()
        self.assertEqual(entries, [dict(ssid="Home", signal=90, security="wpa-psk"),
                                   dict(ssid="Mixed", signal=70, security="unsupported")])

    def test_scan_matches_broker_result_limit(self):
        self.replies(DISCONNECTED, b"".join(ap(ssid=f"network-{index:03}") for index in range(150)))
        self.assertEqual(len(self.network.scan()), 128)

    def test_malformed_escape_signal_and_active_rows_fail(self):
        for output in (b"bad\\q\n", b"bad\\\n", ap(signal=101), ap().replace(b"70", b"-1"),
                       ap(active=True) + ap("Other", active=True)):
            with self.subTest(output=output):
                self.replies(CONNECTED, output)
                with self.assertRaises(module.NetworkSetupError):
                    self.network.status()

    def test_hidden_networks_not_offered(self):
        self.replies(DISCONNECTED, ap(ssid=""))
        self.assertEqual(self.network.scan(), [])

    def test_output_bounds_and_bad_encoding(self):
        for output in (b"x" * 65537, b"\xff", "not bytes"):
            with self.subTest(output=type(output)):
                self.replies(output)
                with self.assertRaises(module.NetworkSetupError):
                    self.network.status()
        self.replies(DISCONNECTED, ap() * 257)
        with self.assertRaises(module.NetworkSetupError):
            self.network.scan()

    def test_password_only_in_stdin_no_shell_or_inherited_environment(self):
        password = "secret:;'$\\value"
        ssid = "Cafe;$(not-a-command)"
        self.successful_connect(ssid)
        self.assertEqual(self.network.connect(ssid, password, "wpa-psk"), {"connected": True, "ssid": ssid})
        secret_calls = []
        for call in self.runner.call_args_list:
            command = call.args[0]
            options = call.kwargs
            self.assertEqual(command[0], "/usr/bin/nmcli")
            self.assertNotIn(password, repr(command))
            self.assertNotIn(password, repr(options["env"]))
            self.assertIs(options["shell"], False)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            self.assertLessEqual(options["timeout"], 45)
            if options["input"]:
                secret_calls.append(call)
        self.assertEqual(len(secret_calls), 1)
        self.assertEqual(secret_calls[0].kwargs["input"], (password + "\n").encode())
        self.assertEqual(secret_calls[0].kwargs["stdout"], subprocess.DEVNULL)
        self.assertIn("--ask", secret_calls[0].args[0])
        self.assertIn("uuid", secret_calls[0].args[0])
        self.assertIn("ap", secret_calls[0].args[0])
        self.assertFalse(any("delete" in call.args[0] or "down" in call.args[0] for call in self.runner.call_args_list))

    def test_open_connect_has_no_prompt_or_secret(self):
        self.successful_connect(security="--")
        self.assertTrue(self.network.connect("Home", "", "open")["connected"])
        self.assertTrue(all(call.kwargs["input"] == b"" for call in self.runner.call_args_list))
        self.assertFalse(any("--ask" in call.args[0] for call in self.runner.call_args_list))

    def test_invalid_requests_never_call_runner(self):
        for ssid, password, security in (
            ("Home", "abcdefgh\n", "wpa-psk"), ("Home", "abcdefgh\r", "wpa-psk"),
            ("Home", "abcd\x00efgh", "wpa-psk"), ("Home", "abcd\tefgh", "wpa-psk"),
            ("Home", "short", "wpa-psk"), ("Home", "é" * 8, "wpa-psk"),
            ("Home", "z" * 64, "wpa-psk"), ("Home", "password", "open"),
            ("Home", None, "open"), ("Home", "password", "enterprise"),
            ("--ask", "password", "wpa-psk"), ("Home\nInjected", "password", "wpa-psk"),
            ("é" * 17, "password", "wpa-psk"), (None, "password", "wpa-psk"),
        ):
            with self.subTest(ssid=ssid, security=security), self.assertRaises(module.NetworkSetupError):
                self.network.connect(ssid, password, security)
        self.runner.assert_not_called()

    def test_psk_hex_supported(self):
        self.successful_connect()
        self.assertTrue(self.network.connect("Home", "a" * 64, "wpa-psk")["connected"])

    def test_existing_connection_not_replaced(self):
        self.replies(CONNECTED, CONNECTED, ap("Other", active=True))
        with self.assertRaises(module.NetworkSetupError):
            self.network.connect("Home", "password", "wpa-psk")
        self.assertFalse(any("connection" in call.args[0] for call in self.runner.call_args_list))

    def test_already_connected_is_idempotent(self):
        self.replies(CONNECTED, CONNECTED, ap(active=True))
        self.assertTrue(self.network.connect("Home", "password", "wpa-psk")["connected"])
        self.assertEqual(self.runner.call_count, 3)

    def test_busy_interface_not_interrupted(self):
        self.replies(b"wifi\n50 (configuring)\n")
        with self.assertRaises(module.NetworkSetupError):
            self.network.connect("Home", "password", "wpa-psk")
        self.assertEqual(self.runner.call_count, 1)

    def test_cannot_lie_about_network_security(self):
        for rows in (ap(security="WEP"), ap(security="WPA2 802.1X"), ap() + ap(security="--"), ap("Other")):
            self.runner.reset_mock()
            self.replies(DISCONNECTED, rows)
            with self.assertRaises(module.NetworkSetupError):
                self.network.connect("Home", "password", "wpa-psk")
            self.assertEqual(self.runner.call_count, 2)

    def test_state_race_leaves_new_profile_without_activation_or_deletion(self):
        self.replies(DISCONNECTED, ap(), b"", CONNECTED)
        with self.assertRaises(module.NetworkSetupError):
            self.network.connect("Home", "password", "wpa-psk")
        commands = [call.args[0] for call in self.runner.call_args_list]
        self.assertFalse(any("up" in command or "delete" in command for command in commands))
        self.assertIn("connection.autoconnect", commands[2])
        self.assertEqual(commands[2][commands[2].index("connection.autoconnect") + 1], "no")

    def test_failure_timeout_redaction_and_no_cleanup(self):
        password = "private-credential"
        for error in (subprocess.TimeoutExpired(password, 45, output=password, stderr=password),
                      RuntimeError(password), FileNotFoundError(password)):
            self.runner.side_effect = [result(DISCONNECTED), result(ap()), result(), result(DISCONNECTED), error]
            with self.assertRaises(module.NetworkSetupError) as caught:
                self.network.connect("Home", password, "wpa-psk")
            formatted = "".join(traceback.format_exception(caught.exception))
            self.assertNotIn(password, formatted)
            self.assertEqual(str(caught.exception), "network_setup_failed")
        self.assertFalse(any("delete" in call.args[0] for call in self.runner.call_args_list))

    def test_success_exit_without_matching_active_ssid_not_success(self):
        self.replies(DISCONNECTED, ap(), b"", DISCONNECTED, b"", CONNECTED, ap("Other", active=True))
        with self.assertRaises(module.NetworkSetupError):
            self.network.connect("Home", "password", "wpa-psk")

    def test_nmcli_nonzero_sanitized(self):
        self.runner.return_value = result(b"sensitive diagnostic", code=10)
        with self.assertRaisesRegex(module.NetworkSetupError, "^network_setup_failed$"):
            self.network.status()

    def test_persistence_only_after_ssid_and_active_uuid_verified(self):
        self.successful_connect()
        self.network.connect("Home", "password", "wpa-psk")
        commands = [call.args[0] for call in self.runner.call_args_list]
        self.assertEqual(commands[7][-5:], ["--get-values", "GENERAL.CON-UUID", "device", "show", "wlan0"])
        self.assertEqual(commands[8][-6:], ["connection", "modify", "uuid", IDENTIFIER, "connection.autoconnect", "yes"])
        self.assertEqual(commands[9][-6:], ["--get-values", "connection.uuid,connection.autoconnect", "connection", "show", "uuid", IDENTIFIER])
        self.assertEqual(sum(call.kwargs["timeout"] for call in self.runner.call_args_list), 117)

    def test_same_ssid_wrong_active_uuid_never_enables_persistence(self):
        self.replies(DISCONNECTED, ap(), b"", DISCONNECTED, b"", CONNECTED, ap(active=True), b"another-profile\n")
        with self.assertRaises(module.NetworkSetupError):
            self.network.connect("Home", "password", "wpa-psk")
        self.assertFalse(any("modify" in call.args[0] for call in self.runner.call_args_list))

    def test_persistence_readback_failure_disables_only_created_uuid(self):
        for readback in (b"bad\nyes\n", (IDENTIFIER + "\nno\n").encode(), b"", b"yes\n"):
            with self.subTest(readback=readback):
                self.runner.reset_mock()
                self.replies(DISCONNECTED, ap(), b"", DISCONNECTED, b"", CONNECTED, ap(active=True),
                             (IDENTIFIER + "\n").encode(), b"", readback, b"", (IDENTIFIER + "\nno\n").encode())
                with self.assertRaisesRegex(module.NetworkSetupError, "^network_setup_failed$"):
                    self.network.connect("Home", "password", "wpa-psk")
                changes = [call.args[0][-6:] for call in self.runner.call_args_list if "modify" in call.args[0]]
                self.assertEqual(changes, [["connection", "modify", "uuid", IDENTIFIER, "connection.autoconnect", setting]
                                          for setting in ("yes", "no")])
                self.assertEqual(sum(call.kwargs["timeout"] for call in self.runner.call_args_list), module.CONNECT_BUDGET_SECONDS)

    def test_uncertain_modify_and_rollback_failures_never_report_success(self):
        responses = [result(output) for output in (DISCONNECTED, ap(), b"", DISCONNECTED, b"", CONNECTED,
                                                  ap(active=True), (IDENTIFIER + "\n").encode())]
        self.runner.side_effect = responses + [subprocess.TimeoutExpired("nmcli", 8), OSError("unavailable")]
        with self.assertRaisesRegex(module.NetworkSetupError, "^network_setup_failed$"):
            self.network.connect("Home", "password", "wpa-psk")
        self.assertEqual(self.runner.call_args.args[0][-6:], ["connection", "modify", "uuid", IDENTIFIER, "connection.autoconnect", "no"])


class BoundedCaptureTests(unittest.TestCase):
    def query(self, source, timeout=2):
        return module._bounded_run(
            [sys.executable, "-c", source], input=b"", stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=timeout, check=False, shell=False,
            start_new_session=True,
        )

    def test_query_captures_small_output(self):
        value = self.query("print('wifi')")
        self.assertEqual(value.stdout, b"wifi\n")
        self.assertEqual(value.returncode, 0)

    def test_query_exact_limit_allowed(self):
        value = self.query(f"import sys; sys.stdout.write('a' * {module.MAX_OUTPUT})")
        self.assertEqual(len(value.stdout), module.MAX_OUTPUT)

    def test_query_overflow_kills_before_unbounded_capture(self):
        with self.assertRaises(module.NetworkSetupError):
            self.query("import os\nwhile True: os.write(1, b'x' * 4096)")

    def test_silent_process_timeout_is_bounded(self):
        started = time.monotonic()
        with self.assertRaises(module.NetworkSetupError):
            self.query("import time; time.sleep(10)", timeout=0.1)
        self.assertLess(time.monotonic() - started, 3)

    def test_captured_branch_rejects_secret_input_before_spawn(self):
        with patch.object(module.subprocess, "Popen") as spawn, self.assertRaises(module.NetworkSetupError):
            module._bounded_run(["unused"], input=b"private", stdout=subprocess.PIPE, timeout=1, check=False)
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
