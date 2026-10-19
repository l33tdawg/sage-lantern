import stat
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sage_lantern import device_access as access


class DeviceAccessTests(unittest.TestCase):
    def test_session_context_is_live_strict_and_token_authorized(self):
        value = {"schema": "sage.lantern.session-context.v1", "epoch": "a" * 32, "session_ref": "b" * 64}
        transport = Mock(return_value=(200, json.dumps(value).encode()))
        with patch.object(access, "_marker_state", return_value=True):
            self.assertEqual(access.session_context("T" * 43, transport=transport).to_dict(), value)
        transport.assert_called_once_with("POST", "/session-context", b'{"token":"' + b"T" * 43 + b'"}')
        for marker in (False, None):
            transport.reset_mock()
            with patch.object(access, "_marker_state", return_value=marker):
                self.assertIsNone(access.session_context("T" * 43, transport=transport))
            transport.assert_not_called()
        with patch.object(access, "_marker_state", side_effect=[True, False]):
            self.assertIsNone(access.session_context("T" * 43, transport=transport))

    def test_session_context_rejects_untrusted_wire_fields_without_coercion(self):
        base = {"schema": "sage.lantern.session-context.v1", "epoch": "a" * 32, "session_ref": "b" * 64}
        invalid = [json.dumps({**base, **change}).encode() for change in (
            {"epoch": "A" * 32}, {"epoch": None}, {"session_ref": "b" * 63},
            {"session_ref": True}, {"token": "secret"}, {"schema": "other"})]
        invalid += [b'null', b'x' * 257, b'{"schema":"duplicate","schema":"duplicate"}']
        with patch.object(access, "_marker_state", return_value=True):
            for raw in invalid:
                self.assertIsNone(access.session_context("T" * 43, transport=lambda *args: (200, raw)))
            self.assertIsNone(access.session_context("T" * 43, transport=lambda *args: (401, json.dumps(base).encode())))
            self.assertIsNone(access.session_context("bad-token", transport=Mock(side_effect=AssertionError())))

    def test_access_epoch_exact_protected_snapshot(self):
        value = {"schema": "sage.lantern.access-epoch.v1", "locked": False, "epoch": "a" * 32}
        transport = Mock(return_value=(200, json.dumps(value).encode()))
        with patch.object(access, "_marker_state", return_value=True):
            self.assertEqual(access.access_epoch(transport=transport), "a" * 32)
        transport.assert_called_once_with("GET", "/access-epoch", None)
        for marker in (False, None):
            with patch.object(access, "_marker_state", return_value=marker):
                transport.reset_mock()
                self.assertIsNone(access.access_epoch(transport=transport))
                transport.assert_not_called()
        with patch.object(access, "_marker_state", side_effect=[True, False]):
            self.assertIsNone(access.access_epoch(transport=transport))

    def test_access_epoch_malformed_and_locked_failclosed(self):
        base = {"schema": "sage.lantern.access-epoch.v1", "locked": False, "epoch": "a" * 32}
        bad = [json.dumps({**base, **change}).encode() for change in (
            {"locked": True}, {"locked": 0}, {"schema": "other"}, {"epoch": "A" * 32},
            {"epoch": "a" * 31}, {"epoch": "a" * 32 + "\n"}, {"token": "secret"})]
        bad.extend([b'{"locked":false,"locked":false}', b"x" * 257, b"null"])
        with patch.object(access, "_marker_state", return_value=True):
            for raw in bad:
                with self.subTest(raw=raw):
                    self.assertIsNone(access.access_epoch(transport=lambda *args: (200, raw)))
            self.assertIsNone(access.access_epoch(transport=lambda *args: (503, json.dumps(base).encode())))

    def test_access_epoch_observes_missed_transition_without_cache(self):
        values = iter(("a" * 32, "b" * 32))
        def transport(*args):
            return 200, json.dumps(dict(schema="sage.lantern.access-epoch.v1", locked=False, epoch=next(values))).encode()
        with patch.object(access, "_marker_state", return_value=True):
            first = access.access_epoch(transport=transport)
            second = access.access_epoch(transport=transport)
        self.assertNotEqual(first, second)

    def test_device_locked_requires_explicit_broker_state(self):
        locked = {"schema": "sage.lantern.access-epoch.v1", "locked": True, "epoch": "a" * 32}
        unlocked = {**locked, "locked": False}
        with patch.object(access, "_marker_state", return_value=True):
            self.assertIs(access.device_locked(transport=lambda *args: (200, json.dumps(locked).encode())), True)
            self.assertIs(access.device_locked(transport=lambda *args: (200, json.dumps(unlocked).encode())), False)
        for raw in (b"null", b"{}", json.dumps({**locked, "locked": "true"}).encode(),
                    json.dumps({**locked, "token": "secret"}).encode(), b"x" * 257):
            with self.subTest(raw=raw), patch.object(access, "_marker_state", return_value=True):
                self.assertIs(access.device_locked(transport=lambda *args: (200, raw)), False)
        with patch.object(access, "_marker_state", return_value=True):
            self.assertIs(access.device_locked(transport=lambda *args: (503, b"{}")), False)
        with patch.object(access, "_marker_state", return_value=False):
            self.assertIs(access.device_locked(transport=lambda *args: (200, json.dumps(locked).encode())), False)
        with patch.object(access, "_marker_state", side_effect=[True, False]):
            self.assertIs(access.device_locked(transport=lambda *args: (200, json.dumps(locked).encode())), False)

    def test_macos_default_alias_missing_marker_remains_legacy(self):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        alias = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
        for target in ("private/etc", "/private/etc"):
            with self.subTest(target=target), patch.object(access.sys, "platform", "darwin"), \
                    patch.object(Path, "lstat", side_effect=[directory, alias]), \
                    patch.object(access.os, "readlink", return_value=target):
                marker = access._default_marker()
                self.assertEqual(marker, Path("/private/etc/sage-lantern-device-lock.enabled"))
            with patch.object(Path, "lstat", side_effect=[directory] * len(marker.parents) + [FileNotFoundError()]):
                self.assertFalse(access.lock_enabled(marker=marker))

    def test_default_alias_does_not_resolve_untrusted_paths(self):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        for owner, target in ((501, "private/etc"), (0, "/tmp/etc")):
            alias = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=owner)
            with patch.object(access.sys, "platform", "darwin"), \
                    patch.object(Path, "lstat", side_effect=[directory, alias]), \
                    patch.object(access.os, "readlink", return_value=target):
                self.assertEqual(access._default_marker(), Path("/etc/sage-lantern-device-lock.enabled"))
        with patch.object(access.sys, "platform", "linux"), patch.object(Path, "lstat") as inspect:
            self.assertEqual(access._default_marker(), Path("/etc/sage-lantern-device-lock.enabled"))
            inspect.assert_not_called()
        alias = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
        with patch.object(Path, "lstat", side_effect=[directory, alias]):
            self.assertTrue(access.lock_enabled(marker=Path("/etc/sage-lantern-device-lock.enabled")))
        unsafe = SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)
        with patch.object(Path, "lstat", side_effect=[directory, unsafe]):
            self.assertFalse(access.device_unlocked(marker=Path("/private/etc/sage-lantern-device-lock.enabled")))

    def test_marker_does_not_inspect_private_configuration(self):
        marker = Path("/etc/sage-lantern-device-lock.enabled")
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        visited = []

        def inspect(path):
            visited.append(path)
            if path in (Path("/"), Path("/etc")):
                return directory
            if path == marker:
                raise FileNotFoundError
            raise PermissionError("private configuration inaccessible")

        with patch.object(Path, "lstat", inspect):
            self.assertFalse(access.lock_enabled(marker=marker))
        self.assertEqual(visited, [Path("/"), Path("/etc"), marker])

    def test_missing_marker_is_legacy_without_daemon(self):
        with patch.object(Path, "lstat", side_effect=FileNotFoundError):
            transport = Mock(side_effect=AssertionError)
            self.assertFalse(access.lock_enabled())
            self.assertTrue(access.authorized("", transport=transport))
            self.assertTrue(access.device_unlocked(transport=transport))
            transport.assert_not_called()

    def test_unreadable_marker_denies_without_transport(self):
        for failure in (PermissionError("secret"), OSError("secret")):
            with self.subTest(failure=type(failure)), patch.object(Path, "lstat", side_effect=failure):
                transport = Mock()
                self.assertTrue(access.lock_enabled())
                self.assertFalse(access.authorized("secret", transport=transport))
                self.assertFalse(access.device_unlocked(transport=transport))
                transport.assert_not_called()

    def test_unsafe_ancestors_deny(self):
        for mode, owner in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFDIR | 0o777, 0),
                            (stat.S_IFDIR | 0o755, 501), (stat.S_IFIFO | 0o600, 0)):
            metadata = SimpleNamespace(st_mode=mode, st_uid=owner)
            with self.subTest(mode=mode, owner=owner), patch.object(Path, "lstat", return_value=metadata):
                self.assertTrue(access.lock_enabled())
                self.assertFalse(access.device_unlocked(transport=Mock()))

    def test_marker_content_and_file_safety(self):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        for mode, owner, links, content, expected in (
            (stat.S_IFREG | 0o644, 0, 1, b"", True),
            (stat.S_IFREG | 0o600, 0, 1, b"1\n", True),
            (stat.S_IFREG | 0o644, 0, 1, b"0\n", None),
            (stat.S_IFREG | 0o644, 0, 1, b"1\nx", None),
            (stat.S_IFREG | 0o666, 0, 1, b"", None),
            (stat.S_IFREG | 0o600, 501, 1, b"", None),
            (stat.S_IFREG | 0o600, 0, 2, b"", None),
            (stat.S_IFIFO | 0o600, 0, 1, b"", None),
            (stat.S_IFLNK | 0o777, 0, 1, b"", None),
        ):
            metadata = SimpleNamespace(st_mode=mode, st_uid=owner, st_nlink=links, st_dev=1, st_ino=2)
            with self.subTest(mode=mode, owner=owner, links=links, content=content), \
                    patch.object(Path, "lstat", side_effect=[directory] * len(access.MARKER.parents) + [metadata]), \
                    patch.object(access.os, "open", return_value=7), \
                    patch.object(access.os, "fstat", return_value=metadata), \
                    patch.object(access.os, "read", return_value=content), \
                    patch.object(access.os, "close"):
                self.assertIs(access._marker_state(access.MARKER), expected)

    def test_invalid_marker_never_consults_allowing_daemon(self):
        with patch.object(access, "_marker_state", return_value=None):
            transport = Mock(return_value=(200, b'{"authorized":true}'))
            self.assertTrue(access.lock_enabled())
            self.assertFalse(access.authorized("token", transport=transport))
            self.assertFalse(access.device_unlocked(transport=transport))
            transport.assert_not_called()

    def test_safe_absence_and_open_race(self):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0,
                                   st_nlink=1, st_dev=1, st_ino=2)
        with patch.object(Path, "lstat", side_effect=[directory] * len(access.MARKER.parents) + [FileNotFoundError()]):
            self.assertFalse(access.lock_enabled())
        with patch.object(Path, "lstat", side_effect=[directory] * len(access.MARKER.parents) + [metadata]), \
                patch.object(access.os, "open", side_effect=FileNotFoundError()):
            self.assertIsNone(access._marker_state(access.MARKER))
        changed = SimpleNamespace(st_dev=1, st_ino=3)
        with patch.object(Path, "lstat", side_effect=[directory] * len(access.MARKER.parents) + [metadata]), \
                patch.object(access.os, "open", return_value=7), \
                patch.object(access.os, "fstat", return_value=changed), \
                patch.object(access.os, "close"):
            self.assertIsNone(access._marker_state(access.MARKER))

    def test_hardware_requires_exact_false_boolean(self):
        with patch.object(access, "_marker_state", return_value=True):
            for raw in (b'{"locked":true}', b'{"locked":0}', b'{"locked":null}',
                        b'{"locked":false,"extra":true}', b'{"locked":true,"locked":false}'):
                self.assertFalse(access.device_unlocked(transport=lambda *args: (200, raw)))

    def test_fixed_browser_and_hardware_contracts(self):
        with patch.object(access, "_marker_state", return_value=True):
            transport = Mock(return_value=(200, b'{"authorized":true}'))
            self.assertTrue(access.authorized("00001234", transport=transport))
            transport.assert_called_once_with("POST", "/authorize", b'{"token":"00001234"}')
            transport.return_value = (200, b'{"locked":false}')
            self.assertTrue(access.device_unlocked(transport=transport))
            transport.assert_called_with("GET", "/state", None)
            self.assertFalse(access.authorized("token", transport=transport))

    def test_strict_response_denials(self):
        bodies = [b"", b"null", b"[]", b'{"authorized":1}', b'{"authorized":"true"}',
                  b'{"authorized":false}', b'{"authorized":true,"extra":0}',
                  b'{"authorized":false,"authorized":true}', b'{"authorized":NaN}',
                  b'{"authorized":true} trailing', b"\xff", b" " * 257,
                  b'{"locked":false}', '{"authorized":true}']
        with patch.object(access, "_marker_state", return_value=True):
            for body in bodies:
                with self.subTest(body=body):
                    self.assertFalse(access.authorized("secret", transport=lambda *args: (200, body)))
            for status in (True, 200.0, 201, 302, 401, 503):
                self.assertFalse(access.authorized("secret", transport=lambda *args: (status, b'{"authorized":true}')))

    def test_token_validation_and_sanitized_failures(self):
        with patch.object(access, "_marker_state", return_value=True):
            transport = Mock(side_effect=TimeoutError("private token content"))
            for token in (None, True, 123, "", "x" * 4097, "é" * 3000, "\ud800"):
                self.assertFalse(access.authorized(token, transport=transport))
            transport.assert_not_called()
            self.assertFalse(access.authorized("private token content", transport=transport))
            self.assertFalse(access.device_unlocked(transport=transport))

    def test_no_authorization_or_marker_cache(self):
        with patch.object(access, "_marker_state", side_effect=[True, True, None, False]) as marker:
            transport = Mock(side_effect=[(200, b'{"authorized":true}'), (200, b'{"authorized":false}')])
            self.assertTrue(access.authorized("token", transport=transport))
            self.assertFalse(access.authorized("token", transport=transport))
            self.assertFalse(access.authorized("token", transport=transport))
            self.assertTrue(access.authorized("token", transport=transport))
            self.assertEqual(marker.call_count, 4)
            self.assertEqual(transport.call_count, 2)

    def test_transport_fixed_destination_bounded_read_and_close(self):
        response = Mock(status=200)
        response.getheader.return_value = "application/json"
        response.read1.side_effect = [b'{"locked":false}', b""]
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(access.http.client, "HTTPConnection", return_value=connection) as factory:
            self.assertEqual(access._transport("GET", "/state", None), (200, b'{"locked":false}'))
            factory.assert_called_once_with("127.0.0.1", 3196, timeout=0.5)
            connection.close.assert_called_once()
            connection.request.assert_called_once_with("GET", "/state", body=None, headers={
                "Content-Type": "application/json", "Connection": "close",
            })
            self.assertTrue(all(0 < call.args[0] <= 0.5 for call in connection.sock.settimeout.call_args_list))
            self.assertLessEqual(response.read1.call_args_list[0].args[0], 257)

    def test_redirect_failure_and_deadline_deny(self):
        for status in (302, 503):
            connection = Mock()
            connection.getresponse.return_value.status = status
            with patch.object(access.http.client, "HTTPConnection", return_value=connection), \
                    patch.object(access, "_marker_state", return_value=True):
                self.assertFalse(access.authorized("token"))
                connection.close.assert_called_once()
                connection.request.assert_called_once()
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.getheader.return_value = "application/json"
        with patch.object(access.http.client, "HTTPConnection", return_value=connection), \
                patch.object(access.time, "monotonic", side_effect=[0, 0.1, 0.2, 0.6]), \
                patch.object(access, "_marker_state", return_value=True):
            self.assertFalse(access.device_unlocked())
            connection.getresponse.return_value.read1.assert_not_called()
            connection.close.assert_called_once()

    def test_real_http_closed_response_and_strict_denials(self):
        received = []
        reply = [b'{"authorized":true}']

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
                self.respond()

            def do_GET(self):
                received.append((self.path, None))
                self.respond()

            def respond(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply[0])))
                self.end_headers()
                self.wfile.write(reply[0])

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        connection_class = access.http.client.HTTPConnection

        def connect(host, port, *, timeout):
            self.assertEqual((host, port, timeout), ("127.0.0.1", 3196, 0.5))
            return connection_class("127.0.0.1", server.server_port, timeout=timeout)

        try:
            with patch.object(access.http.client, "HTTPConnection", side_effect=connect), \
                    patch.object(access, "_marker_state", return_value=True):
                self.assertTrue(access.authorized("test-capability"))
                self.assertEqual(received[-1], ("/authorize", b'{"token":"test-capability"}'))
                reply[0] = b'{"locked":false}'
                self.assertTrue(access.device_unlocked())
                self.assertEqual(received[-1], ("/state", None))
                for body in (b'{"authorized":true}' + b' ' * 239,
                             b'{"authorized":true} trailing',
                             b'{"authorized":false,"authorized":true}', b'\xff'):
                    with self.subTest(body=body):
                        reply[0] = body
                        self.assertFalse(access.authorized("test-capability"))
        finally:
            server.shutdown()
            worker.join(2)
            server.server_close()
        self.assertFalse(worker.is_alive())

    def test_closed_response_still_checks_final_deadline(self):
        response = Mock(status=200)
        response.getheader.return_value = "application/json"
        response.read1.return_value = b'{"locked":false}'
        response.isclosed.return_value = True
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(access.http.client, "HTTPConnection", return_value=connection), \
                patch.object(access.time, "monotonic", side_effect=[0, 0.1, 0.2, 0.3, 0.6]), \
                patch.object(access, "_marker_state", return_value=True):
            self.assertFalse(access.device_unlocked())
        response.read1.assert_called_once()
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
