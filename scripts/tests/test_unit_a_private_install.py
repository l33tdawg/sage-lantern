import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/install-unit-a-private-access.sh"
SOURCE = SCRIPT.read_text()


def block(tag):
    return SOURCE.split("<<'" + tag + "'\n", 1)[1].split("\n" + tag, 1)[0]


def execute(tag, *arguments):
    with patch.object(sys, "argv", ["test-helper", *map(str, arguments)]):
        exec(compile(block(tag), str(SCRIPT) + ":" + tag, "exec"), {})


class PrivateInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.stage = self.root / "stage"
        self.stage.mkdir()
        (self.stage / "dist").mkdir()
        (self.stage / "dist/index.js").write_text("synthetic artifact")
        (self.stage / "dist/index.js").chmod(0o666)
        self.frozen = self.root / "frozen"
        self.manifest = self.root / "manifest.json"

    def freeze(self):
        execute("FREEZE_APP_PY", self.stage, self.frozen, self.manifest)

    def test_bash_syntax_only(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_frozen_app_is_independent_hashed_and_not_writable_by_others(self):
        self.freeze()
        (self.stage / "dist/index.js").write_text("changed after freeze")
        self.assertEqual((self.frozen / "dist/index.js").read_text(), "synthetic artifact")
        manifest = json.loads(self.manifest.read_text())
        self.assertIn("sha256", manifest["dist/index.js"])
        self.assertEqual(stat.S_IMODE(self.manifest.stat().st_mode), 0o600)
        for path in (self.frozen, self.frozen / "dist", self.frozen / "dist/index.js"):
            self.assertEqual(path.stat().st_mode & 0o022, 0)
            self.assertEqual(path.stat().st_uid, os.geteuid())
        candidate = self.root / "candidate"
        second_manifest = self.root / "candidate.json"
        execute("FREEZE_APP_PY", self.frozen, candidate, second_manifest)
        self.assertEqual(self.manifest.read_bytes(), second_manifest.read_bytes())

    def test_internal_absolute_symlink_relocated(self):
        (self.stage / "alias").symlink_to(self.stage / "dist/index.js")
        self.freeze()
        self.assertEqual(os.readlink(self.frozen / "alias"), "dist/index.js")
        self.assertEqual((self.frozen / "alias").resolve(), self.frozen / "dist/index.js")

    def test_external_link_rejected(self):
        sentinel = self.root / "sentinel"
        sentinel.write_text("not application content")
        (self.stage / "escape").symlink_to(sentinel)
        with self.assertRaises(SystemExit):
            self.freeze()
        self.assertFalse(self.manifest.exists())
        self.assertEqual(sentinel.read_text(), "not application content")

    def test_hardlink_and_fifo_rejected(self):
        os.link(self.stage / "dist/index.js", self.stage / "hardlink")
        with self.assertRaises(SystemExit):
            self.freeze()
        other = self.root / "fifo-stage"
        other.mkdir()
        os.mkfifo(other / "fifo")
        with self.assertRaises(SystemExit):
            execute("FREEZE_APP_PY", other, self.root / "fifo-target", self.root / "fifo.json")

    def test_masks_atomically_replace_fragments_and_sync_each(self):
        units = self.root / "units"
        units.mkdir()
        (units / "private.service").write_text("old unit")
        with patch("os.fsync", wraps=os.fsync) as sync:
            execute("CONTAIN_PY", units, "private", "auth")
        self.assertEqual(sync.call_count, 2)
        for name in ("private", "auth"):
            self.assertEqual(os.readlink(units / (name + ".service")), "/dev/null")
        execute("CONTAIN_PY", units, "private", "auth")
        self.assertEqual(len(list(units.iterdir())), 2)

    def test_mask_replace_failure_preserves_fragment(self):
        units = self.root / "units"
        units.mkdir()
        fragment = units / "private.service"
        fragment.write_text("original")
        with patch("os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                execute("CONTAIN_PY", units, "private")
        self.assertEqual(fragment.read_text(), "original")
        self.assertEqual(list(units.iterdir()), [fragment])

    def test_atomic_release_and_remask_after_release(self):
        units, ready = self.root / "units", self.root / "ready"
        units.mkdir()
        ready.mkdir()
        execute("CONTAIN_PY", units, "private")
        (ready / "private.service").write_text("guarded unit")
        with patch("os.fsync", wraps=os.fsync) as sync:
            execute("RELEASE_PY", ready, units)
        self.assertEqual(sync.call_count, 2)
        self.assertEqual((units / "private.service").read_text(), "guarded unit")
        execute("CONTAIN_PY", units, "private")
        self.assertEqual(os.readlink(units / "private.service"), "/dev/null")

    def test_release_failure_keeps_mask(self):
        units, ready = self.root / "units", self.root / "ready"
        units.mkdir()
        ready.mkdir()
        execute("CONTAIN_PY", units, "private")
        (ready / "private.service").write_text("guarded")
        with patch("os.fsync", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                execute("RELEASE_PY", ready, units)
        self.assertEqual(os.readlink(units / "private.service"), "/dev/null")
        self.assertEqual(len(list(units.iterdir())), 1)

    def test_partial_release_failure_can_remask_entire_set(self):
        units, ready = self.root / "units", self.root / "ready"
        units.mkdir()
        ready.mkdir()
        execute("CONTAIN_PY", units, "first", "second")
        for name in ("first", "second"):
            (ready / (name + ".service")).write_text("guarded")
        original = os.replace
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError("injected")
            return original(*args, **kwargs)

        with patch("os.replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                execute("RELEASE_PY", ready, units)
        self.assertFalse((units / "first.service").is_symlink())
        self.assertTrue((units / "second.service").is_symlink())
        execute("CONTAIN_PY", units, "first", "second")
        for name in ("first", "second"):
            self.assertEqual(os.readlink(units / (name + ".service")), "/dev/null")

    def test_app_swap_syncs_before_and_after_replacement(self):
        app, candidate, previous = (self.root / name for name in ("app", "candidate", "previous"))
        app.mkdir()
        candidate.mkdir()
        (app / "old").write_text("old")
        (candidate / "guarded").write_text("new")
        with patch("os.fsync", wraps=os.fsync) as sync:
            execute("SWAP_APP_PY", app, candidate, previous)
        self.assertEqual(sync.call_count, 2)
        self.assertTrue((app / "guarded").is_file())
        self.assertTrue((previous / "old").is_file())

    def test_barrier_failure_prevents_second_app_rename(self):
        app, candidate, previous = (self.root / name for name in ("app", "candidate", "previous"))
        app.mkdir()
        candidate.mkdir()
        with patch("os.fsync", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                execute("SWAP_APP_PY", app, candidate, previous)
        self.assertTrue(previous.is_dir())
        self.assertTrue(candidate.is_dir())
        self.assertFalse(app.exists())

    def test_cutover_order_and_individual_activity_checks(self):
        cutover = SOURCE.split("trap failed ERR INT TERM\n", 1)[1]
        self.assertLess(cutover.index("durable_containment\n"), cutover.index('install -o root -g root -m 0644 "$file"'))
        self.assertLess(cutover.index("SWAP_APP_PY\n"), cutover.index("release_containment\n"))
        self.assertLess(cutover.index('durable_tree "$runtime"'), cutover.index("release_containment\n"))
        self.assertNotIn("systemctl unmask", SOURCE)
        checks = [line.strip() for line in SOURCE.splitlines() if "systemctl is-active" in line]
        self.assertEqual(checks, ['systemctl is-active --quiet "$service"'] * 2)
        self.assertNotRegex(SOURCE, r"systemctl (?:start|restart) sage-lantern-(?:llm|personal-sage)")

    def test_failure_trap_remasks_before_stopping_with_stubbed_systemctl(self):
        function = SOURCE.split("failed() {\n", 1)[1].split("\n}\ntrap failed", 1)[0]
        trace = self.root / "trace"
        harness = '''
private_services=(private)
backup=backup app=app stamp=stamp stage=stage
durable_containment() { echo mask >> "$TRACE"; }
systemctl() {
  echo "$*" >> "$TRACE"
  if [[ $1 == show ]]; then echo inactive; fi
}
failed() {
''' + function + "\n}\nfailed\n"
        result = subprocess.run(["bash", "-c", harness], env={**os.environ, "TRACE": str(trace)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        actions = trace.read_text().splitlines()
        self.assertEqual(actions[:3], ["mask", "daemon-reload", "stop private sage-lantern-device-auth"])
        self.assertIn("Containment confirmed: yes", result.stderr)

    def test_installed_marker_consumer_runs_before_release(self):
        consumer = self.root / "installed-daemon"
        marker = self.root / "marker"
        marker.write_text("allow-vault-initialize\n")
        consumer.write_text("def initialization_allowed(path):\n    return path.read_text() == 'allow-vault-initialize\\n'\n")
        execute("MARKER_CHECK_PY", consumer, marker)
        marker.write_text("wrong")
        with self.assertRaises(SystemExit):
            execute("MARKER_CHECK_PY", consumer, marker)
        self.assertIn('runuser -u sage-lantern -- "$python" - /usr/local/libexec/sage-lantern-device-auth "$setup_marker"', SOURCE)
        self.assertLess(SOURCE.index("MARKER_CHECK_PY\n"), SOURCE.index("\nrelease_containment\n"))
        self.assertGreater(SOURCE.index("<<'MARKER_CHECK_PY'"), SOURCE.index("os.replace(temporary, path)"))

    def setup_smoke(self, value, status=200):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = status
        response.read.return_value = value if isinstance(value, bytes) else json.dumps(value).encode()
        with patch("http.client.HTTPConnection", return_value=connection) as factory:
            try:
                execute("SETUP_SMOKE_PY")
            finally:
                factory.assert_called_once_with("127.0.0.1", 3196, timeout=3)
                connection.close.assert_called_once()
                connection.request.assert_called_once_with('POST', '/setup/status', body=b'{}', headers={
                    'Origin': 'http://127.0.0.1:3100', 'Content-Type': 'application/json',
                    'X-Lantern-Auth': 'control', 'Connection': 'close',
                })

    def test_setup_smoke_fresh_and_existing_vault(self):
        self.setup_smoke(dict(enabled=False, recovery_backup_confirmed=False,
                              has_existing_vault=False, can_initialize=True))
        self.setup_smoke(dict(enabled=True, recovery_backup_confirmed=True,
                              has_existing_vault=True, can_initialize=False))

    def test_setup_smoke_rejects_disabled_initialization_and_invalid_booleans(self):
        base = dict(enabled=False, recovery_backup_confirmed=False,
                    has_existing_vault=False, can_initialize=True)
        invalid = [dict(base, can_initialize=False), dict(base, enabled=0),
                   dict(base, can_initialize="true"), dict(base, unexpected=True),
                   dict(base, recovery_backup_confirmed=True), dict(base, enabled=True)]
        for value in invalid:
            with self.assertRaises(SystemExit):
                self.setup_smoke(value)

    def test_setup_smoke_rejects_http_redirect_malformed_and_duplicate_fields(self):
        for status in (302, 403, 500):
            with self.assertRaises(SystemExit):
                self.setup_smoke({}, status)
        for payload in (b'not json', b'{"enabled":false,"enabled":true}'):
            with self.assertRaises(ValueError):
                self.setup_smoke(payload)
        with self.assertRaises(SystemExit):
            self.setup_smoke(b'x' * 2049)

    def private_smoke(self, *, body=b'{"locked":true}', headers=None, statuses=(200, 403, 403, 403),
                      failure_at=None, failure_method="getresponse"):
        connections = [MagicMock() for unused in statuses]
        for index, (connection, status) in enumerate(zip(connections, statuses)):
            response = connection.getresponse.return_value
            response.status = status
            response.getheaders.return_value = (
                headers if index == 0 and headers is not None else [('Content-Type', 'application/json')])
            response.read.return_value = body
        if failure_at is not None:
            target = connections[failure_at]
            if failure_method == "read":
                target = target.getresponse.return_value
            getattr(target, failure_method).side_effect = TimeoutError('private failure details')
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://proxy.invalid:8080',
                                     'ALL_PROXY': 'http://proxy.invalid:8080', 'NO_PROXY': ''}), \
                patch('http.client.HTTPConnection', side_effect=connections) as factory, \
                patch('urllib.request.urlopen', side_effect=AssertionError('proxy-capable client used')):
            try:
                execute('PRIVATE_SMOKE_PY')
            finally:
                ports = (3196, 3199, 3197, 3198)
                self.assertEqual(factory.call_args_list,
                                 [call('127.0.0.1', port, timeout=3) for port in ports[:factory.call_count]])
                for connection in connections[:factory.call_count]:
                    connection.close.assert_called_once()
                    self.assertEqual(connection.request.call_count, 1)
        return connections

    def test_private_smoke_uses_direct_fixed_routes_and_bounded_state(self):
        connections = self.private_smoke()
        connections[0].request.assert_called_once_with('GET', '/state', body=None, headers={'Connection': 'close'})
        connections[0].getresponse.return_value.read.assert_called_once_with(257)
        connections[1].request.assert_called_once_with('GET', '/status', body=None, headers={'Connection': 'close'})
        connections[2].request.assert_called_once_with('POST', '/history/list', body=b'{}', headers={
            'Connection': 'close', 'Origin': 'http://127.0.0.1:3100',
            'Content-Type': 'application/json', 'X-Lantern-Voice': 'control',
        })
        connections[3].request.assert_called_once_with('POST', '/preview', body=b'', headers={
            'Connection': 'close', 'Origin': 'http://127.0.0.1:3100', 'X-Lantern-Camera': 'preview',
        })
        for connection in connections[1:]:
            connection.getresponse.return_value.read.assert_not_called()

    def test_private_smoke_rejects_nonboolean_duplicate_and_invalid_json(self):
        invalid = (b'{"locked":1}', b'{"locked":0}', b'{"locked":"true"}', b'{"locked":false}',
                   b'{"locked":null}', b'{"locked":true,"locked":true}', b'{"locked":false,"locked":true}',
                   b'{"locked":true,"extra":0}', b'{}', b'[]', b'true', b'{"locked":NaN}',
                   b'{"locked":Infinity}', b'{"locked":true} trailing', b'\xff', b'')
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(SystemExit):
                self.private_smoke(body=body)

    def test_private_smoke_checks_size_limit_without_unbounded_read(self):
        self.private_smoke(body=b'{"locked":true}' + b' ' * 241)
        with self.assertRaises(SystemExit):
            self.private_smoke(body=b'{"locked":true}' + b' ' * 242)

    def test_private_smoke_requires_one_json_content_type_and_no_redirect(self):
        self.private_smoke(headers=[('Content-Type', 'application/json; charset=utf-8')])
        for headers in ([], [('Content-Type', 'text/html')],
                        [('Content-Type', 'application/json'), ('content-type', 'application/json')],
                        [('Content-Type', 'application/json'), ('Location', 'http://other.invalid/state')]):
            with self.subTest(headers=headers), self.assertRaises(SystemExit):
                self.private_smoke(headers=headers)

    def test_private_smoke_requires_exact_direct_statuses(self):
        for index in range(4):
            for wrong in ((204, 301, 302, 307, 403, 500) if index == 0 else (200, 301, 302, 307, 401, 404, 500)):
                statuses = [200, 403, 403, 403]
                statuses[index] = wrong
                with self.subTest(index=index, status=wrong), self.assertRaises(SystemExit):
                    self.private_smoke(statuses=statuses)

    def test_private_smoke_timeouts_fail_closed_and_close_connections(self):
        for index in range(4):
            for method in ('request', 'getresponse'):
                with self.subTest(index=index, method=method), self.assertRaises(SystemExit) as raised:
                    self.private_smoke(failure_at=index, failure_method=method)
                self.assertNotIn('private failure details', str(raised.exception))
        with self.assertRaises(SystemExit):
            self.private_smoke(failure_at=0, failure_method='read')

    def test_private_smoke_failure_is_inside_containment_trap(self):
        self.assertLess(SOURCE.index('trap failed ERR INT TERM'), SOURCE.index("<<'PRIVATE_SMOKE_PY'"))
        tail = SOURCE.split("<<'PRIVATE_SMOKE_PY'\n", 1)[1]
        self.assertLess(tail.index('\nPRIVATE_SMOKE_PY\n'), tail.index('trap - ERR INT TERM'))


if __name__ == "__main__":
    unittest.main()
