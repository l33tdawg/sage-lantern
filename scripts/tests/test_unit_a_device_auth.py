import importlib.util
import base64
import io
import http.client
import json
import os
from pathlib import Path
import tempfile
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sage_lantern.device_lock import DeviceLock, DeviceLockError, SagePINVerifier
from sage_lantern.vault_setup import VaultResponse, LEDGER, RECOVERY, REVOKE


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-device-auth.py"
SPEC = importlib.util.spec_from_file_location("unit_a_device_auth", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RequestSocket:
    def __init__(self, raw):
        self.input = io.BytesIO(raw)
        self.output = io.BytesIO()
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def makefile(self, mode, buffering):
        return self.input

    def sendall(self, data):
        self.output.write(data)


class DeviceAuthTests(unittest.TestCase):
    def test_broker_voice_and_status_share_one_context_through_rotation(self):
        """Real source composition; the PIN verifier remains synthetic."""
        from sage_lantern import device_access
        import argparse
        import uuid
        def load(name, filename):
            spec = importlib.util.spec_from_file_location(name, SCRIPT.parent / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        voice = load("context_voice_integration", "unit-a-voice-loop.py")
        status_server = load("context_status_integration", "unit-a-status-server.py")
        token = self.unlock()
        original_context = device_access.session_context
        original_authorized = device_access.authorized
        def transport(method, path, body):
            status, value, _ = self.request(method, path, json.loads(body), origin=False)
            return status, json.dumps(value, separators=(",", ":")).encode()
        with patch.object(device_access, "_marker_state", return_value=True), \
                patch.object(device_access, "session_context", side_effect=lambda value:
                    original_context(value, transport=transport)), \
                patch.object(device_access, "authorized", side_effect=lambda value:
                    original_authorized(value, transport=transport)):
            loop = voice.VoiceLoop(argparse.Namespace(max_seconds=30, min_seconds=0.1,
                state=self.directory / "voice.json", audio_dir=self.directory / "audio"))
            _, credentials = loop.control.start(str(uuid.uuid4()), device_token=token)
            session = loop.control.take(False)
            loop.device_session = (session["session_id"], token, True)
            first = device_access.session_context(token)
            loop.write_state("processing", session_id=session["session_id"], transcript="synthetic private question", response="")
            publication = json.loads(loop.args.state.read_text())
            self.assertEqual(publication["session_context"], first.to_dict())
            self.assertEqual(status_server._fence_voice(publication, first), publication)
            self.broker.lock(token)
            replacement = self.unlock()
            current = device_access.session_context(replacement)
            self.assertNotEqual(first, current)
            self.assertEqual(status_server._fence_voice(publication, current)["transcript"], "")
            with self.assertRaises(voice.ControlError) as denied:
                loop.control.confirm_start(loop.control.owner["request_id"], session["session_id"],
                                           credentials["token"], device_token=replacement)
            self.assertEqual((denied.exception.status, denied.exception.code), (403, "device_locked"))

    def test_session_context_helper_round_trips_real_broker_source(self):
        from sage_lantern import device_access
        token = self.unlock()
        def transport(method, path, body):
            status, value, _ = self.request(method, path, json.loads(body), origin=False)
            return status, json.dumps(value, separators=(",", ":")).encode()
        with patch.object(device_access, "_marker_state", return_value=True):
            first = device_access.session_context(token, transport=transport)
            self.assertEqual(first.to_dict(), self.broker.session_context(token))
            self.broker.lock(token)
            self.assertIsNone(device_access.session_context(token, transport=transport))

    def test_session_context_is_token_bound_and_contains_no_credential(self):
        token = self.unlock()
        for origin in (False, True):
            status, value, _ = self.request("POST", "/session-context", {"token": token}, origin=origin)
            self.assertEqual(status, 200)
            self.assertEqual(set(value), {"schema", "epoch", "session_ref"})
            self.assertEqual(value["schema"], "sage.lantern.session-context.v1")
            self.assertRegex(value["epoch"], r"^[0-9a-f]{32}$")
            self.assertRegex(value["session_ref"], r"^[0-9a-f]{64}$")
            self.assertNotIn(token, json.dumps(value))
        self.assertEqual(value, self.broker.session_context(token))
        # Voice publication does not participate in this authority at all.
        self.assertEqual(value, self.broker.session_context(token))
        self.assertNotEqual(value, MODULE.AuthBroker(self.device).session_context(token))
        self.broker.lock(token)
        replacement = self.unlock()
        self.assertNotEqual(value, self.broker.session_context(replacement))
        self.assertEqual(self.request("POST", "/session-context", {"token": token}, origin=False)[0], 401)

    def test_session_context_never_activates_or_renews_a_ticket(self):
        pending = self.device.unlock("12345678")
        self.assertEqual(self.request("POST", "/session-context", {"token": pending}, origin=False)[0], 401)
        self.assertTrue(self.device.pending(pending))
        self.assertFalse(self.device.authorized(pending))
        token = self.unlock()
        self.now += 5
        self.broker.session_context(token)
        self.now += 2
        self.assertEqual(self.request("POST", "/session-context", {"token": token}, origin=False)[0], 401)

    def test_session_context_refuses_epoch_change_and_shutdown(self):
        token = self.unlock()
        before = self.broker.access_epoch()
        with patch.object(self.broker, "access_epoch", side_effect=[before, {**before, "epoch": "b" * 32}]):
            self.assertEqual(self.request("POST", "/session-context", {"token": token}, origin=False)[0], 401)
        self.broker.shutdown()
        self.assertEqual(self.request("POST", "/session-context", {"token": token}, origin=False)[0], 503)

    def test_access_epoch_helper_only_and_old_state_unchanged(self):
        status, first, headers = self.request("GET", "/access-epoch", origin=False)
        self.assertEqual(status, 200)
        self.assertEqual(set(first), {"schema", "locked", "epoch"})
        self.assertEqual(first["schema"], "sage.lantern.access-epoch.v1")
        self.assertRegex(first["epoch"], r"^[0-9a-f]{32}$")
        self.assertTrue(first["locked"])
        self.assertEqual(self.request("GET", "/access-epoch")[0], 403)
        self.assertEqual(self.request("GET", "/state", origin=False)[1], {"locked": True})
        token = self.unlock()
        active = self.request("GET", "/access-epoch", origin=False)[1]
        self.assertFalse(active["locked"])
        self.assertNotEqual(active["epoch"], first["epoch"])
        self.device.presence(token)
        self.assertEqual(active, self.request("GET", "/access-epoch", origin=False)[1])
        self.broker.lock(token)
        self.unlock()
        self.assertNotEqual(active["epoch"], self.request("GET", "/access-epoch", origin=False)[1]["epoch"])
        other = MODULE.AuthBroker(self.device)
        self.assertNotEqual(other.access_epoch()["epoch"], self.broker.access_epoch()["epoch"])

    def test_desktop_grants_unreachable_from_browser_or_loopback_http(self):
        for origin in (False, True):
            for path in ('/desktop/open', '/desktop/unlock', '/desktop/activate', '/desktop/check'):
                status, value, _ = self.request('POST', path, {'pin': '12345678'}, origin=origin)
                self.assertEqual(status, 404)
                self.assertEqual(value, {'error': 'not_found'})
        self.assertIsNone(self.broker.desktop.guard)
        self.verifier.assert_not_called()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / "private"
        self.now = 1000.0
        self.verifier = Mock(return_value=True)
        self.device = DeviceLock(self.directory, verifier=self.verifier, clock=lambda: self.now,
                                 wall_clock=lambda: self.now, session_seconds=120)
        self.addCleanup(self.device.close)
        self.profile = MODULE.DeviceProfileStore(self.directory / "profile")
        self.power = MODULE.PowerSettingsStore(self.directory / "power")
        self.broker = MODULE.AuthBroker(self.device, self.profile, power=self.power)
        self.server = SimpleNamespace(broker=self.broker)

    def test_power_auth_cas_and_public_policy(self):
        status, value, _ = self.request("GET", "/power-policy", origin=False)
        self.assertEqual(status, 200)
        self.assertEqual(value, dict(revision=0, display_off_seconds=30, deep_sleep_seconds=300, deep_sleep_available=False))
        self.assertEqual(self.request("POST", "/settings/power/get", {"token": "invalid"})[0], 401)
        token = self.unlock()
        payload = dict(token=token, expected_revision=0, display_off_seconds=60, deep_sleep_seconds=600)
        status, value, _ = self.request("POST", "/settings/power/set", payload)
        self.assertEqual(status, 200)
        self.assertEqual(value["revision"], 1)
        self.assertEqual(self.request("POST", "/settings/power/set", payload)[0], 409)
        self.assertEqual(self.request("POST", "/settings/power/get", {"token": token})[1], value)
        self.assertEqual(self.request("GET", "/power-policy", origin=False)[1], value)
        self.assertEqual(self.request("POST", "/settings/power/set", payload, origin=False)[0], 403)
        for changes in ({"expected_revision": True}, {"deep_sleep_seconds": 60}, {"deep_sleep_available": True}):
            self.assertEqual(self.request("POST", "/settings/power/set", {**payload, **changes})[0], 400)
        self.device.lock()
        self.assertEqual(self.request("POST", "/settings/power/set", {**payload, "expected_revision": 1})[0], 401)

    def test_power_corruption_returns_unavailable_not_defaults(self):
        self.power.load()
        path = self.power.directory / "power.json"
        path.write_text("corrupt")
        path.chmod(0o600)
        self.assertEqual(self.request("GET", "/power-policy", origin=False)[0], 503)

    def test_lock_settings_auth_persistence_and_timing(self):
        self.assertEqual(self.request("POST", "/settings/lock/get", {"token": "invalid"})[0], 401)
        token = self.unlock()
        self.assertEqual(self.request("POST", "/settings/lock/get", {"token": token})[1], dict(revision=0, lock_seconds=300))
        payload = dict(token=token, expected_revision=0, lock_seconds=300)
        self.assertEqual(self.request("POST", "/settings/lock/set", payload)[0], 200)
        self.assertEqual(MODULE.LockSettingsStore(self.power.directory).load(), dict(revision=1, lock_seconds=300))
        self.assertEqual(self.power.load()["revision"], 0)
        self.assertEqual(self.request("POST", "/settings/lock/set", payload)[0], 409)
        self.assertEqual(self.request("POST", "/settings/lock/set", payload, origin=False)[0], 403)
        for unused in range(299):
            self.now += 1
            self.assertTrue(self.device.presence(token))
        self.now += 1
        self.assertFalse(self.device.authorized(token))
        self.assertEqual(self.request("POST", "/settings/lock/get", {"token": token})[0], 401)

    def test_lock_settings_invalid_or_uncertain_save(self):
        token = self.unlock()
        payload = dict(token=token, expected_revision=0, lock_seconds=300)
        for changes in ({"lock_seconds": True}, {"lock_seconds": 0}, {"expected_revision": True}, {"extra": 1}):
            self.assertEqual(self.request("POST", "/settings/lock/set", {**payload, **changes})[0], 400)
        with patch.object(MODULE.LockSettingsStore, "update", side_effect=MODULE.PowerSettingsError):
            self.assertEqual(self.request("POST", "/settings/lock/set", payload)[0], 503)
        self.assertEqual(self.device.status(), {"locked": True})

    def test_cli_saved_lock_delay_overrides_legacy_flag(self):
        MODULE.LockSettingsStore(self.power.directory).update(expected_revision=0, lock_seconds=600)
        server = Mock()
        server.handle_request.side_effect = RuntimeError("stop")
        with patch.object(MODULE, "DeviceLock") as constructor, patch.object(MODULE, "DeviceAuthServer", return_value=server), patch.object(MODULE.signal, "signal"), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            MODULE.main(["--state-dir", str(self.directory), "--idle-seconds", "120"])
        constructor.assert_called_once_with(self.directory, session_seconds=300)
        constructor.return_value.set_session_seconds.assert_called_once_with(600)

    def test_power_actual_http_to_display_helper(self):
        token = self.unlock()
        server = MODULE.DeviceAuthServer(self.device, power=self.power, bind_and_activate=False)
        server.server_address = ("127.0.0.1", 0)
        try:
            server.server_bind()
            server.server_activate()
        except Exception:
            server.server_close()
            raise
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=2)
            payload = dict(token=token, expected_revision=0, display_off_seconds=60, deep_sleep_seconds=600)
            connection.request("POST", "/settings/power/set", json.dumps(payload), headers={
                "Host": "127.0.0.1:3196", "Origin": "http://127.0.0.1:3100",
                "Content-Type": "application/json", "X-Lantern-Auth": "control",
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            saved = json.loads(response.read())
            connection.close()
            connection = http.client.HTTPConnection(*server.server_address, timeout=2)
            connection.request("GET", "/sleep-ready", headers={"Host": "127.0.0.1:3196"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"ready": False})
            connection.close()
            spec = importlib.util.spec_from_file_location("power_integration_helper", SCRIPT.parent / "lantern-display-power.py")
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            real_connection = http.client.HTTPConnection

            def test_connection(host, port, timeout):
                self.assertEqual((host, port, timeout), ("127.0.0.1", 3196, 0.5))
                client = real_connection(*server.server_address, timeout=timeout)
                original_request = client.request

                def request(method, path, **kwargs):
                    kwargs["headers"]["Host"] = "127.0.0.1:3196"
                    return original_request(method, path, **kwargs)

                client.request = request
                return client

            with patch.object(helper.http.client, "HTTPConnection", side_effect=test_connection):
                self.assertEqual(helper.read_power_policy(), saved)
                self.device.lock()
                now = [0]
                commands = []
                controller = helper.DisplayPower(clock=lambda: now[0], idle=lambda: 100,
                                                 command=lambda action: commands.append(action) or True)
                for tick in range(61):
                    now[0] = tick
                    controller.step()
                self.assertEqual(commands, ["timers_off", "enable", "off"])
        finally:
            server.shutdown()
            worker.join(2)
            server.server_close()

    def request(self, method, path, payload=None, *, origin=True, headers=None, body=None,
                host="127.0.0.1:3196", address="127.0.0.1", raw_headers=None):
        if body is None:
            body = b"" if payload is None else json.dumps(payload).encode()
        standard = [("Host", host)]
        if origin:
            standard.append(("Origin", "http://127.0.0.1:3100" if origin is True else origin))
        if method == "POST":
            standard.extend([("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
            if origin:
                standard.append(("X-Lantern-Auth", "control"))
        if headers:
            for name, value in headers.items():
                standard = [(old_name, old_value) for old_name, old_value in standard if old_name.lower() != name.lower()]
                if value is not None:
                    standard.append((name, value))
        if raw_headers:
            standard.extend(raw_headers)
        raw = (f"{method} {path} HTTP/1.1\r\n" + "".join(f"{name}: {value}\r\n" for name, value in standard) + "\r\n").encode() + body
        connection = RequestSocket(raw)
        MODULE.AuthHandler(connection, (address, 10000), self.server)
        head, response_body = connection.output.getvalue().split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        status = int(lines[0].split()[1])
        response_headers = dict(line.split(": ", 1) for line in lines[1:])
        result = json.loads(response_body) if response_body else None
        self.assertEqual(connection.timeout, MODULE.READ_TIMEOUT)
        self.assertNotIn("Set-Cookie", response_headers)
        self.assertNotIn("Access-Control-Allow-Credentials", response_headers)
        self.assertEqual(response_headers["Cache-Control"], "no-store")
        return status, result, response_headers

    def unlock(self, *, activate=True):
        status, result, headers = self.request("POST", "/unlock", {"pin": "12345678"})
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"token"})
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://127.0.0.1:3100")
        if activate:
            self.assertEqual(self.request("POST", "/activate", {"token": result["token"]})[:2],
                             (200, {"authorized": True}))
        return result["token"]

    def advance_present(self, token, seconds):
        for unused in range(seconds):
            self.now += 1
            self.assertEqual(self.request("POST", "/presence", {"token": token})[:2],
                             (200, {"authorized": True}))

    def test_state_unlock_authorize_lock(self):
        self.assertEqual(self.request("GET", "/state", origin=False)[:2], (200, {"locked": True}))
        token = self.unlock()
        self.assertEqual(self.request("GET", "/state", origin=False)[:2], (200, {"locked": False}))
        self.assertEqual(self.request("POST", "/authorize", {"token": token}, origin=False)[:2], (200, {"authorized": True}))
        self.assertEqual(self.request("POST", "/lock", {"token": token})[:2], (200, {"locked": True}))
        self.assertEqual(self.request("POST", "/authorize", {"token": token}, origin=False)[:2], (200, {"authorized": False}))

    def test_lost_unlock_response_cannot_activate_global_state(self):
        token = self.unlock(activate=False)
        self.assertEqual(self.broker.state(), {"locked": True})
        for route in ("/authorize", "/activity", "/presence"):
            self.assertEqual(self.request("POST", route, {"token": token})[:2], (200, {"authorized": False}))
        self.assertEqual(self.request("POST", "/profile/get", {"token": token})[0], 401)
        self.now += 30
        self.assertEqual(self.request("POST", "/activate", {"token": token})[:2], (200, {"authorized": False}))

    def test_lost_activate_response_presence_lease_locks_device(self):
        token = self.unlock()
        for unused in range(5):
            self.now += 1
            self.assertEqual(self.broker.authorize(token), {"authorized": True})
            self.assertEqual(self.broker.state(), {"locked": False})
        self.now += 1
        self.assertEqual(self.broker.state(), {"locked": True})
        for route in ("/authorize", "/activity", "/presence", "/activate"):
            self.assertEqual(self.request("POST", route, {"token": token})[:2], (200, {"authorized": False}))

    def test_cancel_unknown_activation_revokes_only_own_token(self):
        active = self.unlock()
        pending = self.unlock(activate=False)
        self.assertEqual(self.request("POST", "/cancel", {"token": pending})[:2], (200, {"cancelled": True}))
        self.assertEqual(self.request("POST", "/activate", {"token": pending})[1], {"authorized": False})
        self.assertTrue(self.device.authorized(active))
        self.assertEqual(self.request("POST", "/cancel", {"token": "invalid"})[1], {"cancelled": False})
        self.assertTrue(self.device.authorized(active))
        self.assertEqual(self.request("POST", "/cancel", {"token": active})[1], {"cancelled": True})
        self.assertEqual(self.broker.state(), {"locked": True})

    def test_lock_pending_cancels_without_false_global_lock_acknowledgement(self):
        active = self.unlock()
        pending = self.unlock(activate=False)
        self.assertEqual(self.request("POST", "/lock", {"token": pending})[1], {"locked": False})
        self.assertFalse(self.device.activate(pending))
        self.assertTrue(self.device.authorized(active))
        self.broker.lock(active)
        pending = self.unlock(activate=False)
        self.assertEqual(self.request("POST", "/lock", {"token": pending})[1], {"locked": True})
        self.assertFalse(self.device.activate(pending))

    def test_activation_invalidates_previous_session_and_pending_tokens(self):
        old = self.unlock()
        other = self.unlock(activate=False)
        current = self.unlock()
        self.assertFalse(self.device.authorized(old))
        self.assertEqual(self.request("POST", "/activate", {"token": other})[1], {"authorized": False})
        self.assertEqual(self.request("POST", "/cancel", {"token": old})[1], {"cancelled": False})
        self.assertTrue(self.device.authorized(current))

    def test_no_origin_only_allows_state_and_authorize(self):
        for path in ("/unlock", "/lock", "/activity", "/activate", "/presence", "/cancel"):
            payload = {"pin": "12345678"} if path == "/unlock" else {"token": "invalid"}
            self.assertEqual(self.request("POST", path, payload, origin=False)[0], 403)
        self.verifier.assert_not_called()

    def test_host_origin_and_client_address_are_exact(self):
        for host in ("attacker.invalid", "localhost:31960", "127.0.0.1", "127.0.0.1:3196.attacker.invalid"):
            self.assertEqual(self.request("GET", "/state", host=host)[0], 403)
        for origin in ("null", "http://127.0.0.1:3100/", "https://127.0.0.1:3100", "http://localhost:31000"):
            self.assertEqual(self.request("GET", "/state", origin=origin)[0], 403)
        self.assertEqual(self.request("GET", "/state", host="localhost:3196", origin="http://localhost:3100")[0], 200)
        self.assertEqual(self.request("GET", "/state", address="10.0.0.1")[0], 403)

    def test_browser_control_header_and_content_type_required(self):
        for value in (None, "Control", "other"):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"}, headers={"X-Lantern-Auth": value})[0], 403)
        for value in ("text/plain", "application/x-www-form-urlencoded", "application/json; charset=utf-8"):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"}, headers={"Content-Type": value})[0], 415)
        self.verifier.assert_not_called()

    def test_cors_preflight_and_disallowed_extra_headers(self):
        headers = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-lantern-auth"}
        status, result, response = self.request("OPTIONS", "/unlock", headers=headers)
        self.assertEqual((status, result), (204, None))
        self.assertEqual(response["Access-Control-Allow-Headers"], "Content-Type, X-Lantern-Auth")
        self.assertEqual(response["Access-Control-Allow-Methods"], "POST")
        self.assertEqual(self.request("OPTIONS", "/unlock", headers={**headers, "Access-Control-Request-Headers": "content-type,x-lantern-auth,authorization"})[0], 403)
        self.assertEqual(self.request("OPTIONS", "/state", origin=False, headers={"Access-Control-Request-Method": "GET"})[0], 403)

    def test_unknown_paths_methods_and_proxy_targets_rejected(self):
        for path in ("/", "/health", "/profile", "/onboarding", "/state?token=secret", "http://127.0.0.1:3196/state", "/%73tate"):
            self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("PUT", "/state")[0], 405)
        self.assertEqual(self.request("POST", "/state", {})[0], 405)

    def test_duplicate_headers_cookie_and_forwarding_rejected(self):
        for name, value in (("Host", "127.0.0.1:3196"), ("Origin", "http://127.0.0.1:3100"),
                            ("Content-Length", "19"), ("Content-Type", "application/json"), ("X-Lantern-Auth", "control")):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"}, raw_headers=[(name, value)])[0], 400)
        for name in ("Cookie", "Forwarded", "X-Forwarded-Host", "X-Forwarded-For", "Proxy-Authorization", "Transfer-Encoding", "Expect"):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"}, headers={name: "secret"})[0], 400)
        self.verifier.assert_not_called()

    def test_body_lengths_and_schema_are_strict(self):
        for length in (None, "-1", "+19", "019", "1,19", "١٩"):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"}, headers={"Content-Length": length})[0], 400)
        self.assertEqual(self.request("POST", "/unlock", body=b"x" * 513)[0], 413)
        self.assertEqual(self.request("POST", "/unlock", body=b"{}", headers={"Content-Length": "5"})[0], 400)
        for body in (b'{"pin":"12345678","pin":"87654321"}', b'{"pin":"12345678","extra":true}',
                     b'[]', b'null', b'{"pin":12345678}', b'{"pin":NaN}', b'{"pin":"\xff"}', b'{}'):
            self.assertEqual(self.request("POST", "/unlock", body=body)[0], 400)
        self.verifier.assert_not_called()

    def test_pin_validation_and_rate_errors_are_generic(self):
        for unused in range(5):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678\n"})[:2], (400, {"error": "invalid_pin"}))
        status, result, headers = self.request("POST", "/unlock", {"pin": "12345678"})
        self.assertEqual((status, result), (429, {"error": "rate_limited"}))
        self.assertEqual(headers["Retry-After"], "60")
        self.verifier.assert_not_called()

    def test_wrong_token_cannot_lock_other_session(self):
        token = self.unlock()
        self.assertEqual(self.request("POST", "/lock", {"token": "invalid"})[:2], (401, {"error": "unauthorized"}))
        self.assertTrue(self.device.authorized(token))

    def test_expired_token_cannot_lock_new_session_but_already_locked_is_safe(self):
        old = self.unlock()
        self.now += 120
        self.assertEqual(self.request("POST", "/lock", {"token": old})[:2], (200, {"locked": True}))
        token = self.unlock()
        self.assertEqual(self.request("POST", "/lock", {"token": old})[0], 401)
        self.assertTrue(self.device.authorized(token))

    def test_activity_renews_but_polling_never_renews(self):
        token = self.unlock()
        self.advance_present(token, 110)
        self.assertEqual(self.request("POST", "/activity", {"token": token})[:2], (200, {"authorized": True}))
        for unused in range(11):
            self.advance_present(token, 10)
            self.assertEqual(self.request("GET", "/state", origin=False)[1], {"locked": False})
            self.assertEqual(self.request("POST", "/authorize", {"token": token}, origin=False)[1], {"authorized": True})
        self.now += 10
        self.assertEqual(self.request("POST", "/activity", {"token": token})[1], {"authorized": False})
        self.assertEqual(self.request("GET", "/state", origin=False)[1], {"locked": True})

    def test_auth_storage_failure_denies_without_secret_details(self):
        token = self.unlock()
        self.device._db.execute("DELETE FROM rate_state")
        self.assertEqual(self.request("GET", "/state", origin=False)[:2], (503, {"error": "unavailable"}))
        self.assertEqual(self.request("POST", "/authorize", {"token": token}, origin=False)[1], {"authorized": False})
        self.assertEqual(self.request("POST", "/lock", {"token": token})[0], 503)

    def test_profile_get_setup_rename_exact_schema_and_revision(self):
        token = self.unlock()
        status, result, headers = self.request("POST", "/profile/get", {"token": token})
        self.assertEqual((status, result), (200, {"version": 1, "configured": False,
                         "display_name": "", "public_discoverable": False, "revision": 0, "setup_required": True}))
        self.assertEqual(self.request("GET", "/state", origin=False)[1], {"locked": False})
        status, result, headers = self.request("POST", "/profile/setup", {"token": token, "display_name": "  Unit   A  ", "expected_revision": 0})
        self.assertEqual(status, 200)
        self.assertEqual(result, {"version": 1, "configured": True,
                         "display_name": "Unit A", "public_discoverable": False, "revision": 1, "setup_required": False})
        status, result, headers = self.request("POST", "/profile/rename", {"token": token, "display_name": "Kitchen", "expected_revision": 1})
        self.assertEqual(status, 200)
        self.assertEqual(result["display_name"], "Kitchen")
        self.assertEqual(result["revision"], 2)
        self.assertFalse(result["public_discoverable"])
        self.assertEqual(self.request("POST", "/profile/rename", {"token": token, "display_name": "Other", "expected_revision": 1})[:2],
                         (409, {"error": "revision_conflict"}))

    def test_profile_requires_token_and_browser_before_any_storage_access(self):
        for path in ("/profile/get", "/profile/setup", "/profile/rename"):
            payload = {"token": "invalid"}
            if path != "/profile/get":
                payload.update(display_name="Unit A", expected_revision=0)
            self.assertEqual(self.request("POST", path, payload)[:2], (401, {"error": "unauthorized"}))
            self.assertEqual(self.request("POST", path, payload, origin=False)[0], 403)
            self.assertEqual(self.request("POST", path, payload, headers={"X-Lantern-Auth": None})[0], 403)
        self.assertFalse(self.profile.directory.exists())
        self.assertEqual(self.request("GET", "/profile/get")[0], 405)
        self.assertEqual(self.request("GET", "/discovery")[0], 404)

    def test_profile_exact_fields_revision_types_and_size(self):
        token = self.unlock()
        for payload in ({"token": token}, {"token": token, "display_name": "A", "expected_revision": True},
                        {"token": token, "display_name": "A", "expected_revision": -1},
                        {"token": token, "display_name": "A", "expected_revision": 1 << 63},
                        {"token": token, "display_name": "A", "expected_revision": "0"},
                        {"token": token, "display_name": "A", "expected_revision": 0, "pin": "12345678"}):
            self.assertEqual(self.request("POST", "/profile/setup", payload)[0], 400)
        self.assertEqual(self.request("POST", "/profile/get", {"token": token, "extra": True})[0], 400)
        self.assertEqual(self.request("POST", "/profile/setup", {"token": token, "display_name": "A" * 512, "expected_revision": 0})[0], 413)
        self.assertFalse(self.profile.directory.exists())
        self.assertEqual(self.request("POST", "/profile/setup", {"token": token, "display_name": "A\nB", "expected_revision": 0})[:2],
                         (400, {"error": "invalid_profile"}))

    def test_profile_read_rechecks_expiry_before_returning_data(self):
        token = self.unlock()
        self.profile.complete_setup("Private name", expected_revision=0)
        original = self.profile.load

        def load():
            result = original()
            self.now += 120
            return result

        with patch.object(self.profile, "load", side_effect=load):
            self.assertEqual(self.request("POST", "/profile/get", {"token": token})[:2], (401, {"error": "unauthorized"}))

    def test_profile_write_may_commit_but_lock_blocks_its_response(self):
        token = self.unlock()
        original = self.profile.complete_setup

        def setup(name, expected_revision=None):
            result = original(name, expected_revision=expected_revision)
            self.broker.lock(token)
            return result

        with patch.object(self.profile, "complete_setup", side_effect=setup):
            self.assertEqual(self.request("POST", "/profile/setup", {"token": token, "display_name": "Private name", "expected_revision": 0})[:2],
                             (401, {"error": "unauthorized"}))
        self.assertTrue(self.profile.load().configured)
        self.assertEqual(self.device.status(), {"locked": True})

    def test_profile_errors_after_lock_do_not_disclose_revision(self):
        token = self.unlock()

        def setup(name, expected_revision=None):
            self.device.lock()
            raise MODULE.ProfileRevisionConflict("private revision detail")

        with patch.object(self.profile, "complete_setup", side_effect=setup):
            self.assertEqual(self.request("POST", "/profile/setup", {"token": token, "display_name": "Unit A", "expected_revision": 0})[:2],
                             (401, {"error": "unauthorized"}))

    def test_profile_storage_and_extra_response_fields_fail_closed(self):
        token = self.unlock()
        self.profile.complete_setup("A", expected_revision=0)
        (self.profile.directory / "profile.json").write_text("private corrupt content")
        self.assertEqual(self.request("POST", "/profile/get", {"token": token})[:2], (503, {"error": "profile_unavailable"}))
        malicious = SimpleNamespace(to_dict=lambda: {"cookie": "secret"})
        with patch.object(self.profile, "load", return_value=malicious):
            self.assertEqual(self.request("POST", "/profile/get", {"token": token})[:2], (503, {"error": "profile_unavailable"}))

    def test_profile_get_never_extends_idle(self):
        token = self.unlock()
        self.advance_present(token, 119)
        self.assertEqual(self.request("POST", "/profile/get", {"token": token})[0], 200)
        self.now += 1
        self.assertEqual(self.request("POST", "/profile/get", {"token": token})[0], 401)

    def vault(self, *, enabled=True, confirmed=True):
        status = {"enabled": enabled, "recovery_backup_confirmed": confirmed,
                  "has_existing_vault": enabled, "can_initialize": not enabled}
        key = base64.b64encode(b"K" * 32).decode("ascii")
        vault = Mock()
        vault.status.return_value = status
        vault.enable.return_value = {**status, "recovery_key": key}
        vault.recovery.return_value = {"recovery_key": key}
        vault.confirm.return_value = status
        self.broker.vault = vault
        return vault, key

    def test_setup_ui_schemas_and_no_sessions_or_secret_persistence(self):
        vault, key = self.vault()
        self.assertEqual(self.request("POST", "/setup/status", {})[:2], (200, vault.status.return_value))
        self.assertEqual(self.request("POST", "/setup/enable", {"pin": "12345678", "confirmation": "12345678"})[:2], (200, {"recovery_key": key}))
        vault.enable.assert_called_once_with("12345678", "12345678")
        self.assertEqual(self.request("POST", "/setup/recovery", {"pin": "12345678"})[:2], (200, {"recovery_key": key}))
        self.assertEqual(self.request("POST", "/setup/confirm", {"pin": "12345678"})[:2], (200, {"recovery_backup_confirmed": True}))
        self.assertEqual(self.device.status(), {"locked": True})
        self.verifier.assert_not_called()
        for path in self.directory.rglob("*"):
            if path.is_file():
                self.assertNotIn(key.encode(), path.read_bytes())
                self.assertNotIn(b"12345678", path.read_bytes())

    def test_setup_disabled_without_operator_backend(self):
        for path, payload in (("/setup/status", {}), ("/setup/enable", {"pin": "12345678", "confirmation": "12345678"}),
                              ("/setup/recovery", {"pin": "12345678"}), ("/setup/confirm", {"pin": "12345678"})):
            self.assertEqual(self.request("POST", path, payload)[:2], (503, {"error": "setup_unavailable"}))

    def test_setup_browser_only_exact_fields_and_types(self):
        vault, key = self.vault()
        for path, payload in (("/setup/status", {}), ("/setup/enable", {"pin": "12345678", "confirmation": "12345678"}),
                              ("/setup/recovery", {"pin": "12345678"}), ("/setup/confirm", {"pin": "12345678"})):
            self.assertEqual(self.request("POST", path, payload, origin=False)[0], 403)
            self.assertEqual(self.request("POST", path, payload, headers={"X-Lantern-Auth": None})[0], 403)
        for path, payload in (("/setup/status", {"pin": "12345678"}), ("/setup/enable", {"pin": "12345678"}),
                              ("/setup/recovery", {"pin": 12345678}), ("/setup/confirm", {"pin": "12345678", "token": "extra"})):
            self.assertEqual(self.request("POST", path, payload)[0], 400)
        self.assertEqual(vault.mock_calls, [])

    def test_setup_pin_checks_and_unlock_share_one_budget(self):
        vault, key = self.vault()
        vault.recovery.side_effect = MODULE.VaultSetupError("vault_authorization_or_pin_rejected")
        for unused in range(4):
            self.assertEqual(self.request("POST", "/setup/recovery", {"pin": "12345678"})[0], 401)
        self.unlock()
        self.assertEqual(self.request("POST", "/setup/confirm", {"pin": "12345678"})[:2], (429, {"error": "rate_limited"}))
        vault.confirm.assert_not_called()
        self.assertEqual(len(self.device._state()[1]), 5)

    def test_onboarding_unlock_requires_enabled_confirmed_backup(self):
        vault, key = self.vault(enabled=False, confirmed=False)
        self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"})[:2], (409, {"error": "setup_required"}))
        vault.status.return_value = {"enabled": True, "recovery_backup_confirmed": False, "has_existing_vault": True, "can_initialize": False}
        self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"})[0], 409)
        self.verifier.assert_not_called()
        vault.status.return_value["recovery_backup_confirmed"] = True
        self.unlock()

    def test_setup_unknown_outcome_never_retries_or_discloses_reconciled_payload(self):
        vault, key = self.vault()
        vault.enable.side_effect = MODULE.VaultSetupError("vault_enable_uncertain_reconcile_required",
                                                        reconciled_status={"recovery_key": key, "pin": "12345678"})
        self.assertEqual(self.request("POST", "/setup/enable", {"pin": "12345678", "confirmation": "12345678"})[:2],
                         (503, {"error": "setup_uncertain_reconcile_required"}))
        self.assertEqual(vault.enable.call_count, 1)
        self.assertEqual(self.request("POST", "/setup/status", {})[1], vault.status.return_value)
        self.assertEqual(vault.enable.call_count, 1)

    def test_setup_validation_pin_policy_and_recovery_key_shape(self):
        vault, key = self.vault()
        vault.enable.side_effect = MODULE.VaultSetupError("pin_confirmation_mismatch")
        self.assertEqual(self.request("POST", "/setup/enable", {"pin": "12345678", "confirmation": "87654321"})[:2],
                         (400, {"error": "invalid_pin"}))
        vault.enable.side_effect = MODULE.VaultSetupError("vault_enable_not_allowed")
        self.assertEqual(self.request("POST", "/setup/enable", {"pin": "12345678", "confirmation": "12345678"})[:2],
                         (403, {"error": "setup_not_allowed"}))
        for invalid in ("short", base64.b64encode(b"K" * 31).decode(), "!" * 44):
            vault.recovery.return_value = {"recovery_key": invalid}
            self.assertEqual(self.request("POST", "/setup/recovery", {"pin": "12345678"})[:2],
                             (503, {"error": "setup_unavailable"}))

    def test_setup_and_unlock_serialized_without_blocking_lock(self):
        token = self.unlock()
        vault, key = self.vault()
        entered = threading.Event()
        release = threading.Event()

        def recovery(pin):
            entered.set()
            if not release.wait(2):
                raise TimeoutError
            return {"recovery_key": key}

        vault.recovery.side_effect = recovery
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(self.broker.setup_request, "/setup/recovery", {"pin": "12345678"})
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"})[:2], (409, {"error": "verification_busy"}))
                self.assertEqual(self.broker.lock(token), {"locked": True})
            finally:
                release.set()
            with self.assertRaises(DeviceLockError) as raised:
                future.result(timeout=1)
            self.assertEqual(raised.exception.code, "unlock_cancelled")
        self.assertEqual(self.verifier.call_count, 1)
        self.assertEqual(len(self.device._state()[1]), 3)

    def test_lock_during_backup_precheck_prevents_unlock_verification(self):
        token = self.unlock()
        vault, key = self.vault()
        status = vault.status.return_value

        def check():
            self.broker.lock(token)
            return status

        vault.status.side_effect = check
        self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"})[:2], (409, {"error": "unlock_cancelled"}))
        self.assertEqual(self.verifier.call_count, 1)

    def test_fresh_enable_marker_is_explicit_root_owned_readable_and_exact(self):
        self.assertFalse(MODULE.initialization_allowed(None))
        marker = self.directory / "allow-initialize"
        marker.write_bytes(b"allow-vault-initialize\n")
        marker.chmod(0o644)
        original = marker.stat()
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_nlink=1, st_size=original.st_size)
        with patch.object(MODULE.os, "fstat", return_value=metadata):
            self.assertTrue(MODULE.initialization_allowed(marker))
        for field, value in (("st_uid", 1000), ("st_mode", stat.S_IFREG | 0o600),
                             ("st_mode", stat.S_IFREG | 0o664), ("st_mode", stat.S_IFREG | 0o646),
                             ("st_mode", stat.S_IFREG | 0o666), ("st_mode", stat.S_IFREG | 0o4644),
                             ("st_mode", stat.S_IFDIR | 0o644), ("st_nlink", 2), ("st_size", 0)):
            altered = SimpleNamespace(**{**vars(metadata), field: value})
            with self.subTest(field=field, value=value), patch.object(MODULE.os, "fstat", return_value=altered):
                self.assertFalse(MODULE.initialization_allowed(marker))
        symlink = self.directory / "alias-marker"
        symlink.symlink_to(marker)
        self.assertFalse(MODULE.initialization_allowed(symlink))
        self.assertFalse(MODULE.initialization_allowed(Path("relative-marker")))
        marker.write_bytes(b"deny-vault-initialize!\n")
        with patch.object(MODULE.os, "fstat", return_value=metadata):
            self.assertFalse(MODULE.initialization_allowed(marker))
        marker.unlink()
        self.assertFalse(MODULE.initialization_allowed(marker))

    def test_backend_error_and_malformed_state_are_sanitized(self):
        self.verifier.side_effect = RuntimeError("root-cookie-and-pin-secrets")
        self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"})[:2], (401, {"error": "verification_failed"}))
        with patch.object(self.device, "status", return_value={"locked": False, "cookie": "secret"}):
            self.assertEqual(self.request("GET", "/state", origin=False)[:2], (503, {"error": "unavailable"}))
        with patch.object(self.device, "unlock", side_effect=DeviceLockError("secret")):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"})[:2], (503, {"error": "unavailable"}))

    def test_authorized_lock_fences_inflight_unlock_without_waiting(self):
        token = self.unlock()
        entered = threading.Event()
        release = threading.Event()

        def verify(pin):
            entered.set()
            if not release.wait(2):
                raise TimeoutError
            return True

        self.device._verifier = verify
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(self.broker.unlock, "12345678")
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(self.broker.lock(token), {"locked": True})
            finally:
                release.set()
            with self.assertRaises(DeviceLockError) as raised:
                future.result(timeout=1)
            self.assertEqual(raised.exception.code, "unlock_cancelled")

    def test_invalid_lock_while_already_locked_does_not_cancel_pending_unlock(self):
        entered = threading.Event()
        release = threading.Event()

        def verify(pin):
            entered.set()
            if not release.wait(2):
                raise TimeoutError
            return True

        self.device._verifier = verify
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(self.broker.unlock, "12345678")
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(self.broker.lock("invalid"), {"locked": True})
            finally:
                release.set()
            token = future.result(timeout=1)["token"]
        self.assertFalse(self.device.authorized(token))
        self.assertEqual(self.broker.activate(token), {"authorized": True})
        self.assertTrue(self.device.authorized(token))

    def test_broker_fences_token_return_after_device_unlock_completed(self):
        old = self.unlock()
        original = self.device.unlock

        def unlock(pin):
            token = original(pin)
            self.broker.lock(old)
            return token

        with patch.object(self.device, "unlock", side_effect=unlock):
            with self.assertRaises(DeviceLockError) as raised:
                self.broker.unlock("12345678")
        self.assertEqual(raised.exception.code, "unlock_cancelled")

    def test_bounded_worker_overload_rejects_without_starting_thread(self):
        with patch.object(MODULE.HTTPServer, "__init__", return_value=None):
            server = MODULE.DeviceAuthServer(self.device, bind_and_activate=False)
        for unused in range(MODULE.MAX_WORKERS):
            server.slots.acquire()
        request = Mock()
        with patch.object(server, "shutdown_request") as shutdown, patch.object(MODULE.ThreadingMixIn, "process_request") as dispatch:
            server.process_request(request, ("127.0.0.1", 12345))
        dispatch.assert_not_called()
        shutdown.assert_called_once_with(request)
        self.assertIn(b"503 Service Unavailable", request.sendall.call_args.args[0])
        self.assertFalse(server.verify_request(request, ("192.168.1.1", 1)))

    def test_worker_slot_released_after_handler_failure(self):
        with patch.object(MODULE.HTTPServer, "__init__", return_value=None):
            server = MODULE.DeviceAuthServer(self.device, bind_and_activate=False)
        request = Mock()
        server.slots.acquire()
        server.active_requests.add(request)
        with patch.object(MODULE.ThreadingMixIn, "process_request_thread", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                server.process_request_thread(request, ("127.0.0.1", 1))
        self.assertEqual(server.active_requests, set())
        for unused in range(MODULE.MAX_WORKERS):
            self.assertTrue(server.slots.acquire(blocking=False))

    def test_body_read_timeout_returns_generic_error(self):
        class TimeoutBody(io.BytesIO):
            def read(self, size=-1):
                raise TimeoutError("sensitive input")

        raw = (b"POST /authorize HTTP/1.1\r\nHost: 127.0.0.1:3196\r\n"
               b"Content-Type: application/json\r\nContent-Length: 10\r\n\r\n")
        connection = RequestSocket(raw)
        connection.input = TimeoutBody(raw)
        MODULE.AuthHandler(connection, ("127.0.0.1", 1234), self.server)
        response = connection.output.getvalue()
        self.assertIn(b" 408 ", response)
        self.assertIn(b'{"error":"request_timeout"}', response)
        self.assertNotIn(b"sensitive input", response)

    def test_server_close_revokes_before_interrupting_connections(self):
        token = self.unlock()
        with patch.object(MODULE.HTTPServer, "__init__", return_value=None):
            server = MODULE.DeviceAuthServer(self.device, bind_and_activate=False)
        request = Mock()
        request.shutdown.side_effect = lambda how: self.assertFalse(self.device.authorized(token))
        server.active_requests.add(request)
        with patch.object(MODULE.ThreadingMixIn, "server_close") as close, patch.object(MODULE, "DRAIN_TIMEOUT", 0.01):
            server.server_close()
        close.assert_called_once()
        request.shutdown.assert_called_once_with(MODULE.socket.SHUT_RDWR)
        self.assertEqual(self.device.status(), {"locked": True})

    def test_cli_requires_private_absolute_path_and_bounded_idle(self):
        for args in ([], ["--state-dir", "relative"], ["--state-dir", str(self.directory), "--idle-seconds", "29"],
                     ["--state-dir", str(self.directory), "--idle-seconds", "3601"],
                     ["--state-dir", str(self.directory), "--profile-dir", "relative"],
                     ["--state-dir", str(self.directory), "--operator-key", "relative"],
                     ["--state-dir", str(self.directory), "--setup-marker", str(self.directory / "marker")]):
            with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit), patch.object(MODULE, "DeviceLock") as constructor:
                MODULE.main(args)
            constructor.assert_not_called()

    def test_cli_default_idle_cleanup_and_no_extra_bind_options(self):
        device = Mock()
        server = Mock()
        server.handle_request.side_effect = RuntimeError("private failure")
        profile = Mock()
        with patch.object(MODULE, "DeviceLock", return_value=device) as constructor, patch.object(MODULE, "DeviceProfileStore", return_value=profile) as profile_constructor, patch.object(MODULE, "DeviceAuthServer", return_value=server) as server_constructor, patch.object(MODULE.signal, "signal"), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            MODULE.main(["--state-dir", str(self.directory)])
        constructor.assert_called_once_with(self.directory, session_seconds=300)
        profile_constructor.assert_called_once_with(self.directory / "profile")
        power = server_constructor.call_args.kwargs["power"]
        self.assertIsInstance(power, MODULE.PowerSettingsStore)
        self.assertEqual(power.directory, self.directory / "power")
        server_constructor.assert_called_once_with(device, profile=profile, vault=None, power=power,
                                                 capability=MODULE.suspend_available)
        server.server_close.assert_called_once()
        device.close.assert_called_once()

    def test_cli_onboarding_is_explicit_and_never_initializes_a_vault(self):
        device, server, vault, signer = Mock(), Mock(), Mock(), Mock()
        server.handle_request.side_effect = RuntimeError("stop test server")
        operator_key = self.directory / "configured-root-seed"
        with patch.object(MODULE, "DeviceLock", return_value=device), patch.object(MODULE, "DeviceAuthServer", return_value=server) as server_constructor, patch.object(MODULE, "RootFileSigner", return_value=signer) as signer_constructor, patch.object(MODULE, "VaultSetup", return_value=vault) as vault_constructor, patch.object(MODULE.signal, "signal"), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            MODULE.main(["--state-dir", str(self.directory), "--operator-key", str(operator_key)])
        signer_constructor.assert_called_once_with(operator_key)
        self.assertEqual(vault_constructor.call_args.args, (signer,))
        self.assertFalse(vault_constructor.call_args.kwargs["enable_allowed"]())
        self.assertIs(server_constructor.call_args.kwargs["vault"], vault)
        vault.enable.assert_not_called()
        self.assertFalse(operator_key.exists())
        server.server_close.assert_called_once()
        device.close.assert_called_once()

    def test_power_capability_is_live_overlay_never_persisted(self):
        token = self.unlock()
        self.broker.capability = Mock(return_value=True)
        self.assertTrue(self.request("GET", "/power-policy", origin=False)[1]["deep_sleep_available"])
        payload = dict(token=token, expected_revision=0, display_off_seconds=60, deep_sleep_seconds=600)
        self.assertTrue(self.request("POST", "/settings/power/set", payload)[1]["deep_sleep_available"])
        self.assertFalse(self.power.load()["deep_sleep_available"])
        for value in (False, 1, "true", None):
            self.broker.capability.return_value = value
            self.assertFalse(self.request("POST", "/settings/power/get", {"token": token})[1]["deep_sleep_available"])
        self.broker.capability.side_effect = RuntimeError("private path")
        self.assertFalse(self.broker.power_policy()["deep_sleep_available"])

    def test_sleep_ready_exact_public_contract_and_unknown_components(self):
        self.assertEqual(self.request("GET", "/sleep-ready", origin=False)[:2], (200, {"ready": False}))
        self.device._verifier = SagePINVerifier()
        self.assertEqual(self.request("GET", "/sleep-ready", origin=False)[:2], (200, {"ready": True}))
        self.broker.vault = SimpleNamespace(cleanup_confirmed=True)
        self.assertEqual(self.broker.sleep_ready(), {"ready": True})
        for value in (False, None, 1, "true"):
            self.broker.vault.cleanup_confirmed = value
            self.assertEqual(self.broker.sleep_ready(), {"ready": False})
        self.broker.vault = None
        self.assertTrue(self.broker._begin_upstream())
        self.assertEqual(self.broker.sleep_ready(), {"ready": False})
        self.broker._end_upstream()
        self.assertEqual(self.broker.sleep_ready(), {"ready": True})
        self.broker.cleanup_unknown = True
        self.assertEqual(self.broker.sleep_ready(), {"ready": False})

    def test_sleep_ready_keeps_host_origin_and_method_guards(self):
        for options in ({"host": "attacker.invalid"}, {"origin": "http://attacker.invalid"},
                        {"address": "192.0.2.1"}, {"headers": {"Cookie": "secret"}},
                        {"headers": {"X-Forwarded-For": "127.0.0.1"}},
                        {"headers": {"Content-Length": "1"}}):
            with self.subTest(options=options):
                self.assertGreaterEqual(self.request("GET", "/sleep-ready", **options)[0], 400)
        self.assertEqual(self.request("POST", "/sleep-ready", {})[0], 405)
        self.assertEqual(self.request("GET", "/sleep-ready?extra=1")[0], 404)

    def test_routine_wrong_pin_does_not_poison_broker_sleep_readiness(self):
        self.device._verifier = SagePINVerifier()

        def response(status, body):
            return SimpleNamespace(status=status, close=Mock(),
                                   getheaders=lambda: [("Content-Type", "application/json")],
                                   read=lambda limit: json.dumps(body).encode()[:limit])

        connections = [Mock(), Mock()]
        connections[0].getresponse.return_value = response(200, {"auth_required": True, "authenticated": False})
        connections[1].getresponse.return_value = response(401, {"ok": False, "error": "wrong passphrase"})
        with patch("sage_lantern.device_lock.http.client.HTTPConnection", side_effect=connections):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"})[:2],
                             (401, {"error": "verification_failed"}))
        self.assertEqual(self.broker.sleep_ready(), {"ready": True})
        self.assertFalse(self.broker.cleanup_unknown)
        with patch("sage_lantern.device_lock.http.client.HTTPConnection", side_effect=TimeoutError("private response")):
            self.assertEqual(self.request("POST", "/unlock", {"pin": "12345678"})[0], 401)
        self.assertEqual(self.request("GET", "/sleep-ready", origin=False)[:2], (200, {"ready": False}))
        self.assertFalse(self.broker.closing)

    def test_shutdown_latches_every_route_and_direct_admission(self):
        token = self.unlock()
        self.broker.shutdown()
        generation = self.broker.generation
        self.broker.shutdown()
        self.assertEqual(self.broker.generation, generation)
        self.assertFalse(self.device.authorized(token))
        for path, method in MODULE.ROUTES.items():
            with self.subTest(path=path):
                if path == "/sleep-ready":
                    self.assertEqual(self.request("GET", path, origin=False)[:2], (200, {"ready": False}))
                    continue
                self.assertEqual(self.request(method, path, {} if method == "POST" else None)[0], 503)
        for operation in (lambda: self.broker.unlock("12345678"),
                          lambda: self.broker.activate(token), lambda: self.broker.presence(token),
                          lambda: self.broker.activity(token), lambda: self.broker.authorize(token),
                          lambda: self.broker.setup_request("/setup/status", {})):
            with self.assertRaises(DeviceLockError):
                operation()

    def test_real_verifier_revoke_failure_stays_unknown_after_success(self):
        verifier = SagePINVerifier()
        failed = [True]

        def request(method, action, *, pin=None, cookie=None, capture=None):
            if action == "login":
                capture([("Set-Cookie", "sage_session=" + "a" * 43 + "; Path=/; HttpOnly")])
                return {"ok": True}
            if action == "lock":
                if failed[0]:
                    raise TimeoutError("private cookie")
                return {"ok": True, "locked": True}
            return {"auth_required": True, "authenticated": cookie is not None and failed[0]}

        self.device._verifier = verifier
        with patch.object(verifier, "_request", side_effect=request):
            with self.assertRaises(DeviceLockError):
                self.broker.unlock("12345678")
        self.assertFalse(verifier.cleanup_confirmed)
        revoked = [False]

        def succeeding(method, action, *, pin=None, cookie=None, capture=None):
            if action == "login":
                capture([("Set-Cookie", "sage_session=" + "b" * 43 + "; Path=/; HttpOnly")])
                return {"ok": True}
            if action == "lock":
                revoked[0] = True
                return {"ok": True, "locked": True}
            return {"auth_required": True, "authenticated": cookie is not None and not revoked[0]}

        with patch.object(verifier, "_request", side_effect=succeeding):
            self.broker.unlock("12345678")
        self.broker.shutdown()
        self.assertEqual(self.broker.drain_state(True)["upstream_cleanup"], "unknown")
        directory = self.directory / "drain"
        directory.mkdir(mode=0o700)
        report = MODULE.DrainReport(directory, "d" * 32)
        self.addCleanup(report.close)
        report.finish(self.broker.drain_state(True))
        self.assertEqual(json.loads((directory / ("d" * 32 + ".json")).read_bytes()),
                         dict(version=1, instance_id="d" * 32, closing=True, drained=True,
                              local_revoked=True, upstream_cleanup="unknown"))

    def test_shutdown_during_verifier_finally_waits_for_positive_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        verifier = SagePINVerifier()
        revoked = [False]

        def request(method, action, *, pin=None, cookie=None, capture=None):
            if action == "login":
                capture([("Set-Cookie", "sage_session=" + "c" * 43 + "; Path=/; HttpOnly")])
                return {"ok": True}
            if action == "lock":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError
                revoked[0] = True
                return {"ok": True, "locked": True}
            return {"auth_required": True, "authenticated": cookie is not None and not revoked[0]}

        self.device._verifier = verifier
        with patch.object(verifier, "_request", side_effect=request), ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(self.broker.unlock, "12345678")
            try:
                self.assertTrue(entered.wait(1))
                self.broker.shutdown()
                self.assertEqual(self.broker.upstream_active, 1)
                with self.assertRaises(DeviceLockError):
                    self.broker.unlock("12345678")
            finally:
                release.set()
            with self.assertRaises(DeviceLockError):
                future.result(timeout=1)
        self.assertEqual(self.broker.drain_state(True), dict(closing=True, drained=True,
                         local_revoked=True, upstream_cleanup="confirmed"))

    def test_real_setup_revoke_failure_and_lost_response_are_sticky(self):
        for lost in (False, True):
            with self.subTest(lost=lost):
                signer = Mock()
                signer.sign_request.return_value = {name: "test" for name in
                    ("X-Agent-ID", "X-Signature", "X-Timestamp", "X-Nonce")}
                failures = [True]
                calls = []

                def transport(method, path, body, headers):
                    calls.append(path)
                    if path == LEDGER:
                        return VaultResponse(200, json.dumps(dict(enabled=True, recovery_backup_confirmed=True,
                            algorithm="AES-256-GCM", kdf="Argon2id", vault_path="test")).encode())
                    if path == RECOVERY:
                        if lost:
                            raise TimeoutError("lost private response")
                        return VaultResponse(200, json.dumps(dict(recovery_key=base64.b64encode(b"a" * 32).decode())).encode(),
                                             [("Set-Cookie", "sage_session=" + "d" * 43)])
                    self.assertEqual(path, REVOKE)
                    if failures[0]:
                        raise TimeoutError("private cookie")
                    return VaultResponse(200, b'{"ok":true,"locked":true}')

                vault = MODULE.VaultSetup(signer, transport, enable_allowed=lambda: False)
                broker = MODULE.AuthBroker(self.device, vault=vault)
                self.device._verifier = SagePINVerifier()
                with self.assertRaises(MODULE.VaultSetupError):
                    broker.setup_request("/setup/recovery", {"pin": "12345678"})
                self.assertFalse(vault.cleanup_confirmed)
                if not lost:
                    self.assertIn(REVOKE, calls)
                failures[0] = False
                broker.setup_request("/setup/status", {})
                broker.shutdown()
                self.assertEqual(broker.drain_state(True)["upstream_cleanup"], "unknown")

    def test_bounded_drain_timeout_never_becomes_positive_later(self):
        with patch.object(MODULE.HTTPServer, "__init__", return_value=None):
            server = MODULE.DeviceAuthServer(self.device, bind_and_activate=False)
        server.active_requests.add(Mock())
        with patch.object(MODULE.ThreadingMixIn, "server_close"), patch.object(MODULE, "DRAIN_TIMEOUT", 0.01):
            state = server.server_close()
        self.assertFalse(state["drained"])
        self.assertEqual(state["upstream_cleanup"], "unknown")
        server.active_requests.clear()
        self.assertEqual(server.broker.drain_state(True)["upstream_cleanup"], "unknown")

    def test_setup_close_between_admission_and_operation_never_runs(self):
        vault, _ = self.vault()
        begin = self.broker._begin_upstream

        def racing_begin():
            admitted = begin()
            self.broker.shutdown()
            return admitted

        with patch.object(self.broker, "_begin_upstream", side_effect=racing_begin):
            with self.assertRaises(DeviceLockError):
                self.broker.setup_request("/setup/status", {})
        vault.status.assert_not_called()
        self.assertEqual(self.broker.upstream_active, 0)

    def test_shutdown_during_real_setup_revoke_reports_only_after_completion(self):
        entered, release = threading.Event(), threading.Event()
        signer = Mock()
        signer.sign_request.return_value = {name: "test" for name in
            ("X-Agent-ID", "X-Signature", "X-Timestamp", "X-Nonce")}

        def transport(method, path, body, headers):
            if path == LEDGER:
                return VaultResponse(200, json.dumps(dict(enabled=True, recovery_backup_confirmed=True,
                    algorithm="AES-256-GCM", kdf="Argon2id", vault_path="test")).encode())
            if path == RECOVERY:
                return VaultResponse(200, json.dumps(dict(recovery_key=base64.b64encode(b"a" * 32).decode())).encode(),
                                     [("Set-Cookie", "sage_session=" + "d" * 43)])
            self.assertEqual(path, REVOKE)
            entered.set()
            if not release.wait(2):
                raise TimeoutError
            return VaultResponse(200, b'{"ok":true,"locked":true}')

        self.device._verifier = SagePINVerifier()
        self.broker.vault = MODULE.VaultSetup(signer, transport, enable_allowed=lambda: False)
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(self.broker.setup_request, "/setup/recovery", {"pin": "12345678"})
            try:
                self.assertTrue(entered.wait(1))
                self.broker.shutdown()
                self.assertFalse(self.broker.vault.cleanup_confirmed)
                self.assertEqual(self.broker.upstream_active, 1)
            finally:
                release.set()
            with self.assertRaises(DeviceLockError):
                future.result(timeout=1)
        self.assertEqual(self.broker.drain_state(True)["upstream_cleanup"], "confirmed")

    def test_closing_server_rejects_dispatch_and_failed_local_lock_denies(self):
        with patch.object(MODULE.HTTPServer, "__init__", return_value=None):
            server = MODULE.DeviceAuthServer(self.device, bind_and_activate=False)
        with patch.object(self.device, "lock", side_effect=RuntimeError("private state")):
            server.broker.shutdown()
        with patch.object(server, "shutdown_request") as close, \
                patch.object(MODULE.ThreadingMixIn, "process_request") as dispatch:
            request = Mock()
            server.process_request(request, ("127.0.0.1", 1234))
        close.assert_called_once_with(request)
        dispatch.assert_not_called()
        self.assertFalse(server.broker.drain_state(True)["local_revoked"])

    def test_drain_receipt_exclusive_instance_atomic_and_no_secrets(self):
        directory = self.directory / "drain"
        directory.mkdir(mode=0o700)
        instance = "a" * 32
        report = MODULE.DrainReport(directory, instance)
        self.addCleanup(report.close)
        path = directory / (instance + ".json")
        initial = path.read_bytes()
        self.assertLessEqual(len(initial), 512)
        self.assertEqual(json.loads(initial), dict(version=1, instance_id=instance, closing=False,
                         drained=False, local_revoked=False, upstream_cleanup="unknown"))
        with self.assertRaises(ValueError):
            MODULE.DrainReport(directory, instance)
        self.assertEqual(path.read_bytes(), initial)
        self.broker.shutdown()
        state = self.broker.drain_state(True)
        with patch.object(MODULE.os, "replace", side_effect=OSError("private path")):
            with self.assertRaisesRegex(ValueError, "^drain_report_unavailable$"):
                report.finish(state)
        self.assertEqual(path.read_bytes(), initial)
        self.assertEqual(list(directory.glob(".drain-*")), [])
        report.finish(state)
        self.assertEqual(json.loads(path.read_bytes()), dict(version=1, instance_id=instance, **state))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_drain_receipt_rejects_unsafe_directory_and_instance(self):
        directory = self.directory / "drain"
        directory.mkdir(mode=0o700)
        for instance in (None, "../bad", "a" * 31, "A" * 32, "0" * 32):
            with self.assertRaises(ValueError):
                MODULE.DrainReport(directory, instance)
        alias = self.directory / "alias"
        alias.symlink_to(directory)
        with self.assertRaises(ValueError):
            MODULE.DrainReport(alias, "a" * 32)
        directory.chmod(0o755)
        with self.assertRaises(ValueError):
            MODULE.DrainReport(directory, "a" * 32)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)

    def test_drain_receipt_refuses_unsafe_existing_file_without_chmod(self):
        directory = self.directory / "drain"
        directory.mkdir(mode=0o700)
        report = MODULE.DrainReport(directory, "c" * 32)
        self.addCleanup(report.close)
        path = directory / ("c" * 32 + ".json")
        path.chmod(0o644)
        self.broker.shutdown()
        with self.assertRaises(ValueError):
            report.finish(self.broker.drain_state(True))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertFalse(json.loads(path.read_bytes())["drained"])
        target = directory / "sentinel"
        target.write_bytes(b"untouched")
        target.chmod(0o600)
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            report.finish(self.broker.drain_state(True))
        path.unlink()
        os.link(target, path)
        with self.assertRaises(ValueError):
            report.finish(self.broker.drain_state(True))
        path.unlink()
        os.mkfifo(path, 0o600)
        with self.assertRaises(ValueError):
            report.finish(self.broker.drain_state(True))
        self.assertEqual(target.read_bytes(), b"untouched")

    def test_cli_publishes_instance_report_after_server_drain(self):
        directory = self.directory / "drain"
        directory.mkdir(mode=0o700)
        device, server = Mock(), Mock()
        state = dict(closing=True, drained=True, local_revoked=True, upstream_cleanup="confirmed")
        server.server_close.return_value = state
        server.handle_request.side_effect = RuntimeError("stop test")
        with patch.object(MODULE, "DeviceLock", return_value=device), \
                patch.object(MODULE, "DeviceAuthServer", return_value=server), \
                patch.object(MODULE.signal, "signal"), patch.dict(os.environ, INVOCATION_ID="b" * 32), \
                patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            MODULE.main(["--state-dir", str(self.directory), "--drain-report-dir", str(directory)])
        self.assertEqual(json.loads((directory / ("b" * 32 + ".json")).read_bytes()),
                         dict(version=1, instance_id="b" * 32, **state))


if __name__ == "__main__":
    unittest.main()
