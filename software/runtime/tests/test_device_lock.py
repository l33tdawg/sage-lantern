import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from sage_lantern.device_lock import DATABASE_NAME, DeviceLock, DeviceLockError, SagePINVerifier


class Clock:
    def __init__(self):
        self.monotonic = 100.0
        self.wall = 1000.0

    def advance(self, seconds):
        self.monotonic += seconds
        self.wall += seconds


class DeviceLockTests(unittest.TestCase):
    def test_access_epoch_tracks_missed_lock_unlock_and_restart(self):
        initial = self.device.access_epoch()
        token = self.device.unlock("12345678")
        self.assertEqual(self.device.access_epoch(), initial)
        self.assertTrue(self.device.activate(token))
        first = self.device.access_epoch()
        self.assertFalse(first["locked"])
        self.assertNotEqual(first["epoch"], initial["epoch"])
        self.device.lock()
        token = self.device.unlock("12345678")
        self.assertTrue(self.device.activate(token))
        second = self.device.access_epoch()
        self.assertFalse(second["locked"])
        self.assertNotEqual(first["epoch"], second["epoch"])
        self.assertEqual(self.device.status(), {"locked": False})
        self.device.close()
        reopened = self.make_device()
        self.assertNotEqual(reopened.access_epoch()["epoch"], second["epoch"])

    def test_access_epoch_heartbeat_stable_expiry_rotates(self):
        token = self.device.unlock("12345678")
        self.device.activate(token)
        first = self.device.access_epoch()
        self.clock.advance(1)
        self.assertTrue(self.device.presence(token))
        self.assertTrue(self.device.touch(token))
        self.assertTrue(self.device.activate(token))
        self.assertTrue(self.device.authorized(token))
        self.assertEqual(self.device.access_epoch(), first)
        self.clock.advance(6)
        expired = self.device.access_epoch()
        self.assertTrue(expired["locked"])
        self.assertNotEqual(expired["epoch"], first["epoch"])
        self.assertEqual(self.device.access_epoch(), expired)

    def test_access_epoch_pending_removal_and_explicit_lock(self):
        token = self.device.unlock("12345678")
        first = self.device.access_epoch()["epoch"]
        self.device.revoke(token)
        self.assertNotEqual(self.device.access_epoch()["epoch"], first)
        first = self.device.access_epoch()["epoch"]
        self.device.lock()
        self.assertNotEqual(self.device.access_epoch()["epoch"], first)
        self.device.unlock("12345678")
        first = self.device.access_epoch()["epoch"]
        self.clock.advance(30)
        self.assertNotEqual(self.device.access_epoch()["epoch"], first)

    def test_access_epoch_idle_expiry_and_clock_error(self):
        token = self.device.unlock("12345678")
        self.device.activate(token)
        first = self.device.access_epoch()["epoch"]
        self.device.set_session_seconds(1)
        self.clock.advance(1)
        self.assertNotEqual(self.device.access_epoch()["epoch"], first)
        token = self.device.unlock("12345678")
        self.device.activate(token)
        first = self.device.access_epoch()["epoch"]
        self.clock.monotonic -= 1
        with self.assertRaises(DeviceLockError):
            self.device.access_epoch()
        self.clock.monotonic += 1
        self.assertNotEqual(self.device.access_epoch()["epoch"], first)
        self.assertEqual(self.device.status(), {"locked": True})

    def test_access_epoch_database_error_revokes_and_rotates(self):
        token = self.device.unlock("12345678")
        self.device.activate(token)
        first = self.device.access_epoch()["epoch"]
        with patch.object(self.device, "_check_files", side_effect=OSError()):
            with self.assertRaises(DeviceLockError):
                self.device.access_epoch()
        recovered = self.device.access_epoch()
        self.assertTrue(recovered["locked"])
        self.assertNotEqual(recovered["epoch"], first)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / "lock"
        self.clock = Clock()
        self.verifier = Mock(return_value=True)
        self.device = self.make_device()

    def make_device(self, **kwargs):
        device = DeviceLock(self.directory, verifier=kwargs.pop("verifier", self.verifier),
                            clock=lambda: self.clock.monotonic, wall_clock=lambda: self.clock.wall,
                            session_seconds=kwargs.pop("session_seconds", 300), **kwargs)
        self.addCleanup(device.close)
        return device

    def error(self, code, callback):
        with self.assertRaises(DeviceLockError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def active_token(self, device=None, pin="12345678"):
        device = device or self.device
        token = device.unlock(pin)
        self.assertFalse(device.authorized(token))
        self.assertTrue(device.activate(token))
        return token

    def advance_present(self, token, seconds):
        for unused in range(seconds):
            self.clock.advance(1)
            self.assertTrue(self.device.presence(token))

    def test_initially_locked_and_bounded_status(self):
        self.assertEqual(self.device.status(), {"locked": True})
        self.assertFalse(self.device.authorized("anything"))
        token = self.device.unlock("12345678")
        self.assertEqual(self.device.status(), {"locked": True})
        self.assertFalse(self.device.authorized(token))
        self.assertTrue(self.device.activate(token))
        self.assertEqual(self.device.status(), {"locked": False})
        self.assertTrue(self.device.authorized(token))
        self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(token, "12345678")

    def test_updated_idle_delay_preserves_activity_and_presence(self):
        token = self.active_token()
        self.advance_present(token, 100)
        self.device.set_session_seconds(120)
        self.advance_present(token, 19)
        self.clock.advance(1)
        self.assertFalse(self.device.presence(token))
        self.device.set_session_seconds(600)
        self.assertFalse(self.device.authorized(token))

    def test_longer_delay_does_not_count_settings_as_activity(self):
        token = self.active_token()
        self.advance_present(token, 100)
        self.device.set_session_seconds(600)
        self.advance_present(token, 499)
        self.clock.advance(1)
        self.assertFalse(self.device.authorized(token))

    def test_shorter_delay_expires_immediately(self):
        token = self.active_token()
        self.advance_present(token, 100)
        self.device.set_session_seconds(60)
        self.assertFalse(self.device.authorized(token))

    def test_changing_idle_delay_does_not_renew_presence(self):
        token = self.active_token()
        self.clock.advance(5)
        self.device.set_session_seconds(600)
        self.clock.advance(1)
        self.assertFalse(self.device.authorized(token))

    def test_only_hashes_retained_no_credentials_on_disk(self):
        token = self.device.unlock("12345678")
        self.assertEqual(list(self.device._pending), [hashlib.sha256(token.encode()).digest()])
        self.assertEqual(self.device._sessions, {})
        for path in self.directory.iterdir():
            raw = path.read_bytes()
            self.assertNotIn(b"12345678", raw)
            self.assertNotIn(token.encode(), raw)
            self.assertNotIn(b"sage_session", raw)

    def test_lost_unlock_response_never_unlocks_and_ticket_expires(self):
        token = self.device.unlock("12345678")
        self.assertTrue(self.device.pending(token))
        self.assertFalse(self.device.touch(token))
        self.assertFalse(self.device.presence(token))
        self.assertEqual(self.device.status(), {"locked": True})
        self.clock.advance(30)
        self.assertFalse(self.device.pending(token))
        self.assertFalse(self.device.activate(token))
        self.assertEqual(self.device._pending, {})

    def test_presence_expires_even_when_idle_is_future_and_reads_do_not_extend(self):
        token = self.active_token()
        for unused in range(5):
            self.clock.advance(1)
            self.assertTrue(self.device.authorized(token))
            self.assertEqual(self.device.status(), {"locked": False})
            self.assertTrue(self.device.touch(token))
        self.clock.advance(1)
        for method in (self.device.authorized, self.device.touch, self.device.presence, self.device.activate):
            self.assertFalse(method(token))
        self.assertEqual(self.device.status(), {"locked": True})

    def test_activation_replaces_all_other_tokens_and_is_nonrenewing(self):
        old = self.active_token()
        pending = self.device.unlock("12345678")
        replacement = self.device.unlock("12345678")
        self.assertTrue(self.device.authorized(old))
        self.assertTrue(self.device.activate(replacement))
        self.assertFalse(self.device.authorized(old))
        self.assertFalse(self.device.activate(pending))
        self.clock.advance(5)
        self.assertTrue(self.device.activate(replacement))
        self.clock.advance(1)
        self.assertFalse(self.device.activate(replacement))

    def test_cancel_pending_does_not_revoke_other_session(self):
        active = self.active_token()
        pending = self.device.unlock("12345678")
        self.assertTrue(self.device.revoke(pending))
        self.assertFalse(self.device.revoke(pending))
        self.assertFalse(self.device.activate(pending))
        self.assertTrue(self.device.authorized(active))
        self.assertTrue(self.device.revoke(active))
        self.assertEqual(self.device.status(), {"locked": True})

    def test_lock_revokes_pending_and_restart_cannot_activate(self):
        token = self.device.unlock("12345678")
        self.device.lock()
        self.assertFalse(self.device.activate(token))
        pending = self.device.unlock("12345678")
        self.device.close()
        reopened = self.make_device()
        self.assertFalse(reopened.activate(pending))

    def test_activation_fences_inflight_verification(self):
        pending = self.device.unlock("12345678")
        entered = threading.Event()
        release = threading.Event()

        def verify(pin):
            entered.set()
            return release.wait(2)

        self.device._verifier = verify
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(self.device.unlock, "12345678")
            self.assertTrue(entered.wait(1))
            try:
                self.assertTrue(self.device.activate(pending))
            finally:
                release.set()
            self.error("unlock_cancelled", future.result)
        self.assertTrue(self.device.authorized(pending))

    def test_database_failure_clears_active_and_pending_tokens(self):
        token = self.active_token()
        pending = self.device.unlock("12345678")
        self.device._db.execute("DELETE FROM rate_state")
        self.assertFalse(self.device.authorized(token))
        self.assertEqual(self.device._pending, {})
        self.assertEqual(self.device._presence, {})
        self.assertFalse(self.device.activate(pending))

    def test_pin_ascii_fullmatch_boundaries(self):
        for pin in ("01234567", "012345678901"):
            self.assertTrue(self.device.authorized(self.active_token(pin=pin)))
        invalid = ("1234567", "1234567890123", "12345678\n", " 12345678", "12345678 ",
                   "１２３４５６７８", "١٢٣٤٥٦٧٨", "1234567a", "1234\x005678", b"12345678", 12345678, None)
        for pin in invalid:
            self.clock.advance(61)
            with self.subTest(pin=pin):
                self.error("invalid_pin", lambda: self.device.unlock(pin))
        self.assertEqual(self.verifier.call_count, 2)

    def test_all_sessions_lock_and_monotonic_expiry(self):
        first = self.active_token()
        second = self.active_token()
        self.assertNotEqual(first, second)
        self.clock.wall += 100000
        self.assertFalse(self.device.authorized(first))
        self.clock.monotonic += 5.99
        self.assertTrue(self.device.authorized(second))
        self.clock.monotonic += 0.01
        self.assertFalse(self.device.authorized(first))
        self.assertEqual(self.device.status(), {"locked": True})
        third = self.active_token()
        self.device.lock()
        self.assertFalse(self.device.authorized(third))

    def test_restart_invalidates_tokens_not_rate_limit(self):
        tokens = [self.device.unlock("12345678") for unused in range(5)]
        self.device.close()
        reopened = self.make_device()
        self.assertEqual(reopened.status(), {"locked": True})
        self.assertFalse(reopened.authorized(tokens[0]))
        self.error("rate_limited", lambda: reopened.unlock("12345678"))
        self.clock.advance(60)
        self.assertTrue(reopened.authorized(self.active_token(reopened)))

    def test_activity_renews_only_authenticated_live_token(self):
        token = self.active_token()
        other = self.device.unlock("12345678")
        self.advance_present(token, 299)
        self.assertTrue(self.device.touch(token))
        self.clock.advance(1)
        self.assertFalse(self.device.authorized(other))
        self.assertFalse(self.device.touch(other))
        self.assertTrue(self.device.authorized(token))
        self.advance_present(token, 298)
        self.clock.advance(1)
        self.assertFalse(self.device.touch(token))
        self.assertEqual(self.device.status(), {"locked": True})

    def test_status_authorize_and_invalid_activity_never_renew(self):
        token = self.active_token()
        for unused in range(299):
            self.clock.advance(1)
            self.assertTrue(self.device.presence(token))
            self.assertTrue(self.device.authorized(token))
            self.assertEqual(self.device.status(), {"locked": False})
            self.assertFalse(self.device.touch("invalid"))
        self.clock.advance(1)
        self.assertFalse(self.device.authorized(token))
        self.assertFalse(self.device.touch(token))

    def test_lock_and_storage_failure_block_activity(self):
        token = self.device.unlock("12345678")
        self.device.lock()
        self.assertFalse(self.device.touch(token))
        token = self.device.unlock("12345678")
        self.device._db.execute("DELETE FROM rate_state")
        self.assertFalse(self.device.touch(token))
        self.assertFalse(self.device.authorized(token))

    def test_failures_invalid_pins_and_lock_do_not_reset_limit(self):
        self.verifier.return_value = False
        for unused in range(3):
            self.error("verification_failed", lambda: self.device.unlock("12345678"))
        for unused in range(2):
            self.error("invalid_pin", lambda: self.device.unlock("invalid"))
        self.device.lock()
        self.error("rate_limited", lambda: self.device.unlock("12345678"))
        self.assertEqual(self.verifier.call_count, 3)

    def test_public_reservation_shares_budget_and_persists_without_session(self):
        for unused in range(4):
            self.assertIsNone(self.device.reserve_attempt())
        self.assertEqual(self.device.status(), {"locked": True})
        self.verifier.assert_not_called()
        self.device.close()
        reopened = self.make_device()
        token = reopened.unlock("12345678")
        self.assertTrue(reopened.activate(token))
        self.assertTrue(reopened.authorized(token))
        self.error("rate_limited", reopened.reserve_attempt)

    def test_public_reservation_fails_closed_on_storage_failure(self):
        self.device._db.execute("DELETE FROM rate_state")
        self.error("unavailable", self.device.reserve_attempt)
        self.verifier.assert_not_called()

    def test_rate_limit_is_rolling_not_fixed_window(self):
        for unused in range(3):
            self.device.unlock("12345678")
        self.clock.advance(30)
        for unused in range(2):
            self.device.unlock("12345678")
        self.clock.advance(30)
        for unused in range(3):
            self.device.unlock("12345678")
        self.error("rate_limited", lambda: self.device.unlock("12345678"))

    def test_wall_clock_rollback_cannot_reset_limiter(self):
        for unused in range(5):
            self.device.unlock("12345678")
        self.clock.wall -= 1000
        self.clock.monotonic += 60
        self.device.close()
        reopened = self.make_device()
        self.error("rate_limited", lambda: reopened.unlock("12345678"))
        self.clock.wall = 1059.99
        self.error("rate_limited", lambda: reopened.unlock("12345678"))
        self.clock.wall = 1060
        reopened.unlock("12345678")

    def test_global_limit_across_instances_and_threads(self):
        other = self.make_device()

        def attempt(index):
            try:
                (self.device if index % 2 else other).unlock("12345678")
                return "success"
            except DeviceLockError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=12) as workers:
            results = list(workers.map(attempt, range(20)))
        self.assertEqual(results.count("success") + results.count("verification_busy"), 5)
        self.assertEqual(results.count("rate_limited"), 15)
        self.assertEqual(self.verifier.call_count, results.count("success"))

    def test_busy_verification_is_bounded_counted_and_lock_remains_available(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def verify(pin):
            calls.append(pin)
            entered.set()
            if not release.wait(3):
                raise TimeoutError
            return True

        self.device._verifier = verify
        with ThreadPoolExecutor(max_workers=5) as workers:
            first = workers.submit(self.device.unlock, "12345678")
            self.assertTrue(entered.wait(1))
            try:
                attempts = [workers.submit(self.device.unlock, "12345678") for unused in range(4)]
                for attempt in attempts:
                    self.error("verification_busy", lambda: attempt.result(timeout=1))
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.device.lock(), {"locked": True})
                self.error("rate_limited", lambda: self.device.unlock("12345678"))
            finally:
                release.set()
            self.error("unlock_cancelled", first.result)
        self.assertEqual(self.device.status(), {"locked": True})
        self.clock.advance(60)
        self.assertTrue(self.device.authorized(self.active_token()))

    def test_generation_rechecked_after_acquiring_verification_slot(self):
        gate = Mock()

        def acquire(*, blocking):
            self.assertIs(blocking, False)
            self.device.lock()
            return True

        gate.acquire.side_effect = acquire
        self.device._verification_mutex = gate
        self.error("unlock_cancelled", lambda: self.device.unlock("12345678"))
        self.verifier.assert_not_called()
        gate.release.assert_called_once()
        self.assertEqual(len(self.device._state()[1]), 1)

    def test_verification_failure_releases_slot(self):
        self.verifier.side_effect = RuntimeError("private failure")
        self.error("verification_failed", lambda: self.device.unlock("12345678"))
        self.verifier.side_effect = None
        token = self.active_token()
        self.assertTrue(self.device.authorized(token))

    def test_inflight_unlock_cannot_beat_lock(self):
        entered = threading.Event()
        release = threading.Event()

        def verify(pin):
            entered.set()
            if not release.wait(2):
                raise TimeoutError
            return True

        self.device._verifier = verify
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(self.device.unlock, "12345678")
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.device.lock(), {"locked": True})
            release.set()
            self.error("unlock_cancelled", future.result)
        self.assertEqual(self.device.status(), {"locked": True})

    def test_attempt_is_committed_before_verifier_and_survives_exception(self):
        def verify(pin):
            observer = self.make_device()
            with observer._mutex:
                self.assertEqual(len(observer._state()[1]), 1)
            raise RuntimeError("PIN/private-cookie-must-not-escape")

        self.device._verifier = verify
        self.error("verification_failed", lambda: self.device.unlock("12345678"))
        self.device.close()
        reopened = self.make_device()
        self.assertEqual(len(reopened._state()[1]), 1)

    def test_verifier_must_return_exact_true(self):
        for value in (False, None, 1, "true", {"ok": True}):
            self.verifier.return_value = value
            self.error("verification_failed", lambda: self.device.unlock("12345678"))

    def test_private_files_and_database_durability_mode(self):
        self.device.unlock("12345678")
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        for path in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_uid, os.geteuid())
            self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(self.device._db.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(self.device._db.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_empty_missing_corrupt_database_fails_closed(self):
        self.device.close()
        path = self.directory / DATABASE_NAME
        original = path.read_bytes()
        for data in (b"", b"not a SQLite database"):
            path.write_bytes(data)
            self.error("unavailable", self.make_device)
        path.write_bytes(original)
        path.unlink()
        self.error("unavailable", self.make_device)

    def test_database_failure_revokes_session_and_never_calls_verifier(self):
        token = self.device.unlock("12345678")
        self.device._db.execute("DELETE FROM rate_state")
        self.assertFalse(self.device.authorized(token))
        self.error("unavailable", self.device.status)
        self.error("unavailable", lambda: self.device.unlock("12345678"))
        self.assertEqual(self.verifier.call_count, 1)
        self.assertEqual(self.device.lock(), {"locked": True})
        self.assertEqual(self.device._sessions, {})

    def test_database_write_failure_does_not_verify(self):
        self.device._db.execute("PRAGMA query_only=ON")
        self.error("unavailable", lambda: self.device.unlock("12345678"))
        self.verifier.assert_not_called()

    def test_bad_clock_fails_closed(self):
        token = self.device.unlock("12345678")
        self.clock.monotonic -= 1
        self.assertFalse(self.device.authorized(token))
        self.clock.monotonic = float("nan")
        self.error("unavailable", self.device.status)

    def test_file_symlink_hardlink_permissions_and_owner_rejected(self):
        self.device.close()
        path = self.directory / DATABASE_NAME
        path.chmod(0o644)
        self.error("unavailable", self.make_device)
        path.chmod(0o600)
        linked = self.directory.parent / "linked"
        os.link(path, linked)
        self.error("unavailable", self.make_device)
        linked.unlink()
        saved = self.directory.parent / "saved"
        path.rename(saved)
        path.symlink_to(saved)
        self.error("unavailable", self.make_device)
        path.unlink()
        saved.rename(path)
        with patch("sage_lantern.device_lock.os.geteuid", return_value=os.geteuid() + 1):
            self.error("unavailable", self.make_device)

    def test_symlink_directory_and_sidecar_rejected(self):
        self.device.close()
        alias = self.directory.parent / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        self.error("unavailable", lambda: DeviceLock(alias, verifier=self.verifier))
        journal = self.directory / (DATABASE_NAME + "-journal")
        journal.symlink_to(self.directory / DATABASE_NAME)
        self.error("unavailable", self.make_device)

    def test_bad_tokens_and_closed_instance(self):
        token = self.device.unlock("12345678")
        for candidate in (None, b"token", token + "\n", token + " ", token[:-1], "x" * 10000):
            self.assertFalse(self.device.authorized(candidate))
        self.device.close()
        self.assertFalse(self.device.authorized(token))
        self.error("unavailable", lambda: self.device.unlock("12345678"))


class Response:
    def __init__(self, body, *, status=200, cookie=None, headers=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = [("Content-Type", "application/json")]
        if cookie is not None:
            self.headers.append(("Set-Cookie", cookie))
        if headers is not None:
            self.headers = headers
        self.closed = False

    def getheaders(self):
        return self.headers

    def read(self, limit):
        return self.body[:limit]

    def close(self):
        self.closed = True


class SagePINVerifierTests(unittest.TestCase):
    cookie = "C" * 43

    def flow(self):
        return [Response({"auth_required": True, "authenticated": False}),
                Response({"ok": True}, cookie=f"sage_session={self.cookie}; Path=/; HttpOnly; SameSite=Strict"),
                Response({"auth_required": True, "authenticated": True}),
                Response({"ok": True, "locked": True}),
                Response({"auth_required": True, "authenticated": False})]

    def run_flow(self, responses, *, valid=True, verifier=None):
        connections = []
        verifier = SagePINVerifier() if verifier is None else verifier

        def connect(host, port, *, timeout):
            self.assertEqual((host, port, timeout), ("127.0.0.1", 8080, 3.0))
            connection = Mock()
            response = responses[len(connections)]
            if isinstance(response, Exception):
                connection.getresponse.side_effect = response
            else:
                connection.getresponse.return_value = response
            connections.append(connection)
            return connection

        with patch("sage_lantern.device_lock.http.client.HTTPConnection", side_effect=connect):
            if valid:
                self.assertIs(verifier("12345678"), True)
            else:
                with self.assertRaises(DeviceLockError) as raised:
                    verifier("12345678")
                self.assertEqual(str(raised.exception), "verification_failed")
                self.assertNotIn(self.cookie, str(raised.exception))
        for connection in connections:
            connection.close.assert_called_once()
        return connections

    def test_exact_fixed_endpoint_sequence_and_no_ambient_cookie_or_proxy(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://attacker.invalid", "ALL_PROXY": "http://attacker.invalid"}):
            connections = self.run_flow(self.flow())
        requests = [connection.request.call_args for connection in connections]
        self.assertEqual([request.args for request in requests], [
            ("GET", "/v1/dashboard/auth/check"), ("POST", "/v1/dashboard/auth/login"),
            ("GET", "/v1/dashboard/auth/check"), ("POST", "/v1/dashboard/auth/lock"),
            ("GET", "/v1/dashboard/auth/check")])
        self.assertNotIn("Cookie", requests[0].kwargs["headers"])
        self.assertNotIn("Cookie", requests[1].kwargs["headers"])
        self.assertEqual(json.loads(requests[1].kwargs["body"]), {"passphrase": "12345678"})
        for request in requests[2:]:
            self.assertEqual(request.kwargs["headers"]["Cookie"], "sage_session=" + self.cookie)
            self.assertIsNone(request.kwargs["body"])

    def test_no_cookie_retained_on_verifier(self):
        verifier = SagePINVerifier()
        self.assertEqual(vars(verifier), {"timeout": 3.0, "cleanup_confirmed": True})

    def test_exact_cookie_free_denials_preserve_prior_cleanup_evidence(self):
        for status, message in ((401, "wrong passphrase"), (429, "too many login attempts, try again later")):
            for previous in (True, False):
                with self.subTest(status=status, previous=previous):
                    verifier = SagePINVerifier()
                    verifier.cleanup_confirmed = previous
                    responses = [self.flow()[0], Response({"ok": False, "error": message}, status=status)]
                    connections = self.run_flow(responses, valid=False, verifier=verifier)
                    self.assertEqual(len(connections), 2)
                    self.assertIs(verifier.cleanup_confirmed, previous)
                    self.run_flow(self.flow(), verifier=verifier)
                    self.assertIs(verifier.cleanup_confirmed, previous)

    def test_malformed_or_cookie_bearing_denial_remains_uncertain(self):
        for response in (
            Response({"ok": 0, "error": "wrong passphrase"}, status=401),
            Response({"ok": False, "error": "wrong passphrase", "extra": True}, status=401),
            Response({"ok": False, "error": "unknown"}, status=401),
            Response(b'{"ok":false,"ok":false,"error":"wrong passphrase"}', status=401),
            Response(b" " * 8193, status=401),
            Response(b"not JSON", status=401),
            Response({"ok": False, "error": "wrong passphrase"}, status=401,
                     headers=[("Content-Type", "text/html")]),
            Response({"ok": False, "error": "wrong passphrase"}, status=401,
                     headers=[("Content-Type", "application/json"), ("Location", "/other")]),
            Response({"ok": False, "error": "wrong passphrase"}, status=401, cookie=""),
        ):
            with self.subTest(response=response.body):
                verifier = SagePINVerifier()
                self.run_flow([self.flow()[0], response], valid=False, verifier=verifier)
                self.assertFalse(verifier.cleanup_confirmed)
        verifier = SagePINVerifier()
        flow = self.flow()
        flow[1].status = 401
        flow[1].body = b'{"ok":false,"error":"wrong passphrase"}'
        self.assertEqual(len(self.run_flow([flow[0], flow[1], flow[3], flow[4]],
                                          valid=False, verifier=verifier)), 4)
        self.assertFalse(verifier.cleanup_confirmed)

    def test_lost_login_response_remains_unknown_after_definitive_denial(self):
        verifier = SagePINVerifier()
        self.run_flow([self.flow()[0], TimeoutError("private response")], valid=False, verifier=verifier)
        self.assertFalse(verifier.cleanup_confirmed)
        self.run_flow([self.flow()[0], Response({"ok": False, "error": "wrong passphrase"}, status=401)],
                      valid=False, verifier=verifier)
        self.assertFalse(verifier.cleanup_confirmed)

    def test_disabled_encryption_ambient_auth_and_malformed_check_rejected(self):
        for body in ({"auth_required": False, "authenticated": True},
                     {"auth_required": True, "authenticated": True},
                     {"auth_required": 1, "authenticated": False},
                     {"auth_required": True}, [], b'{"auth_required":true,"auth_required":false,"authenticated":false}'):
            with self.subTest(body=body):
                self.assertEqual(len(self.run_flow([Response(body)], valid=False)), 1)

    def test_login_without_cookie_cannot_authenticate(self):
        flow = self.flow()
        flow[1] = Response({"ok": True})
        self.assertEqual(len(self.run_flow(flow, valid=False)), 2)

    def test_bad_login_body_with_cookie_still_revokes_in_finally(self):
        flow = self.flow()
        flow[1].body = b"not json; private server response"
        connections = self.run_flow([flow[0], flow[1], flow[3], flow[4]], valid=False)
        self.assertEqual(connections[2].request.call_args.args[1], "/v1/dashboard/auth/lock")

    def test_failed_postcheck_still_revokes(self):
        flow = self.flow()
        flow[2] = Response({"auth_required": False, "authenticated": True})
        connections = self.run_flow(flow, valid=False)
        self.assertEqual(len(connections), 5)

    def test_timeout_during_check_revokes_and_never_succeeds(self):
        flow = self.flow()
        flow[2] = TimeoutError("12345678 secret cookie " + self.cookie)
        self.assertEqual(len(self.run_flow(flow, valid=False)), 5)

    def test_revocation_failure_or_still_authenticated_rejects(self):
        for replacement in (TimeoutError(), Response({"ok": True}), Response({"ok": 1, "locked": True})):
            flow = self.flow()
            flow[3] = replacement
            self.assertEqual(len(self.run_flow(flow, valid=False)), 4)
        flow = self.flow()
        flow[4] = Response({"auth_required": True, "authenticated": True})
        self.run_flow(flow, valid=False)

    def test_redirects_are_not_followed(self):
        response = Response({}, status=302, headers=[("Location", "http://attacker.invalid")])
        self.assertEqual(len(self.run_flow([response], valid=False)), 1)

    def test_bounded_response_and_content_type(self):
        for response in (Response(b" " * 8193), Response({}, headers=[("Content-Type", "text/html")]),
                         Response(b'{"auth_required":true,"authenticated":NaN}')):
            self.assertEqual(len(self.run_flow([response], valid=False)), 1)

    def test_cookie_attributes_invalid_but_known_cookie_is_revoked(self):
        flow = self.flow()
        flow[1] = Response({"ok": True}, cookie=f"sage_session={self.cookie}; Path=/; Domain=attacker.invalid; HttpOnly")
        self.assertEqual(len(self.run_flow([flow[0], flow[1], flow[3], flow[4]], valid=False)), 4)

    def test_invalid_pin_never_makes_http_request(self):
        with patch("sage_lantern.device_lock.http.client.HTTPConnection") as connect:
            for pin in ("12345678\n", "１２３４５６７８", "1234567", None):
                with self.assertRaises(DeviceLockError):
                    SagePINVerifier()(pin)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
