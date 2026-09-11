import importlib.util
import json
import io
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest
from sage_lantern.suspend_kiosk import KioskBinding, Process


ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


DISPLAY = module('fresh_display_tests', 'scripts/lantern/activate-fresh-display.py')
SESSION = module('fresh_display_real_session', 'scripts/lantern/lantern-display-session.py')


@pytest.fixture
def backend(monkeypatch):
    runner = object.__new__(DISPLAY.Native)
    binding = KioskBinding('radxa', 1000, 'unit-b')
    launcher = Process(99, 1, 10, 1000, '/usr/bin/bash', binding.launcher_argv[0], 'S')
    current = SESSION.Session(binding, '92', launcher, ':1', '/run/user/1000/gdm/Xauthority', (1,), (2,))
    runner.session = SESSION
    runner.native = SESSION.Native()
    runner.native.check_x11 = Mock(return_value=current)
    runner.expected = {'unit_id': 'unit-b', 'session': DISPLAY.session_public(current),
        'files': {str(Path('/usr/bin/python3').resolve()): 'python'}, 'policy': {'state': 'absent'},
        'auth_dropins': {}}
    runner.files = Mock()
    runner.setup = Mock()
    runner.policy = Mock()
    runner.empty = Mock()
    runner.acceptance = None
    runner.display_process = None
    runner.deadline = DISPLAY.time.monotonic() + 30
    properties = dict(LoadState='loaded', ActiveState='inactive', MainPID='0', ControlPID='0',
        InvocationID='abcd', Job='', FragmentPath=str(DISPLAY.UNIT_PATH), DropInPaths='',
        NeedDaemonReload='no', Transient='no', UnitFileState='disabled', ControlGroup='/system.slice/' + DISPLAY.UNIT,
        KillMode='control-group', NRestarts='0')
    runner.command = Mock(side_effect=lambda *args: '\n'.join(f'{key}={value}' for key, value in properties.items()))
    monkeypatch.setattr(SESSION, 'binding_for', lambda mode, unit: binding if unit == 'unit-b' else None)
    monkeypatch.setattr(DISPLAY.os.path, 'lexists', lambda path: False)
    monkeypatch.setattr(DISPLAY, 'in_unit', Mock())
    return runner, properties, current


def test_real_session_and_systemd_interfaces_nonblanking(backend):
    runner, properties, current = backend
    runner.check()
    runner.native.check_x11.assert_called_once_with(current.binding)
    assert all(call.args[0] == 'show' for call in runner.command.call_args_list)


@pytest.mark.parametrize('field,value', [('Job', '24'), ('NeedDaemonReload', 'yes'),
    ('DropInPaths', '/unexpected'), ('FragmentPath', '/run/fake'), ('Transient', 'yes'),
    ('ControlPID', '9'), ('KillMode', 'process'), ('UnitFileState', 'enabled')])
def test_real_manager_predicates_reject_changes(backend, field, value):
    runner, properties, current = backend
    properties[field] = value
    with pytest.raises(ValueError):
        runner.check()


def test_session_replacement_refused(backend):
    runner, properties, current = backend
    runner.expected['session']['session_id'] = 'different'
    with pytest.raises(ValueError):
        runner.check()


def test_same_process_preexec_then_python(backend, monkeypatch):
    runner, properties, current = backend
    properties.update(ActiveState='active', MainPID='101')
    runner.expected['files']['/usr/lib/systemd/systemd'] = 'systemd'
    before = Process(101, 1, 123, 0, '/usr/lib/systemd/systemd', ('systemd',), 'S')
    after = Process(101, 1, 123, 0, '/usr/bin/python3',
        ('/opt/sage-lantern/venv/bin/python', '-I', '-B', str(DISPLAY.SUPERVISOR), '--serve'), 'R')
    child = Process(102, 101, 124, 1000, '/usr/bin/python3', ('/usr/bin/python3', str(DISPLAY.POWER)), 'S')
    runner.native.processes = SimpleNamespace(read=Mock(side_effect=[before, after, child, after]))
    runner.native.snapshot = Mock(return_value={102: child})
    runner.process = Mock(side_effect=[{'image_sha256': 'systemd'}, {'image_sha256': 'python'}, {'image_sha256': 'python'}])
    monkeypatch.setattr(Path, 'open', lambda *args, **kwargs: io.BytesIO(b'python'))
    monkeypatch.setattr(DISPLAY.hashlib, 'file_digest', lambda *args: SimpleNamespace(hexdigest=lambda: 'python'))
    monkeypatch.setattr(DISPLAY.time, 'sleep', lambda seconds: None)
    assert runner.ready() == {'image_sha256': 'python'}


@pytest.mark.parametrize('change', ['pid', 'ticks', 'image', 'restart'])
def test_exec_change_refused(backend, monkeypatch, change):
    runner, properties, current = backend
    properties.update(ActiveState='active', MainPID='101')
    runner.expected['files']['/usr/lib/systemd/systemd'] = 'systemd'
    before = Process(101, 1, 123, 0, '/usr/lib/systemd/systemd', ('systemd',), 'S')
    after = Process(102 if change == 'pid' else 101, 1, 124 if change == 'ticks' else 123,
        0, '/unexpected' if change == 'image' else '/usr/lib/systemd/systemd', ('systemd',), 'S')
    runner.native.processes = SimpleNamespace(read=Mock(side_effect=[before, after]))
    runner.process = Mock(return_value={'image_sha256': 'systemd'})
    def advance(seconds):
        if change == 'restart':
            properties['NRestarts'] = '1'
    monkeypatch.setattr(DISPLAY.time, 'sleep', advance)
    with pytest.raises(ValueError):
        runner.ready()


def test_duplicate_fields_refused():
    with pytest.raises(ValueError):
        DISPLAY.decode('{"unit_id":"unit-a","unit_id":"unit-b"}')


def test_stat_identity_ignores_atime():
    fields = dict(st_dev=1, st_ino=2, st_mode=3, st_uid=0, st_gid=0, st_nlink=1,
        st_size=5, st_mtime_ns=6, st_ctime_ns=7, st_atime_ns=8)
    before = SimpleNamespace(**fields)
    fields['st_atime_ns'] = 9
    assert DISPLAY.identity(before) == DISPLAY.identity(SimpleNamespace(**fields))


def test_cleanup_runs_even_when_failure_recording_fails(monkeypatch):
    runner = Mock()
    runner.expected = {'policy': {'state': 'present'}}
    runner.check.side_effect = [None, None, RuntimeError()]
    runner.record.side_effect = OSError()
    monkeypatch.setattr(DISPLAY.signal, 'signal', lambda *args: None)
    with pytest.raises(RuntimeError):
        DISPLAY.perform(runner, b'{}')
    runner.cleanup.assert_called_once()


def test_activation_preserves_approved_policy_and_does_not_enable(monkeypatch):
    runner = Mock()
    runner.expected = {'unit_id': 'unit-b', 'boot_id': 'boot', 'policy': {'state': 'present'}}
    runner.display_process = {'pid': 101}
    writes = Mock()
    monkeypatch.setattr(DISPLAY, 'write', writes)
    result = DISPLAY.perform(runner, b'{}')
    writes.assert_not_called()
    runner.command.assert_called_once_with('start', DISPLAY.UNIT)
    assert result['display_active'] is True and result['boot_enabled'] is False
    assert result['physical_verified'] is False and result['reboot_verified'] is False


def test_boot_enable_is_separate_and_never_starts_service():
    runner = Mock()
    runner.expected = {'unit_id': 'unit-b', 'boot_id': 'boot'}
    runner.display_process = {'pid': 101}
    result = DISPLAY.perform(runner, b'{}', enable=True)
    runner.command.assert_called_once_with('enable', DISPLAY.UNIT)
    assert result['boot_enabled'] is True
    assert result['physical_verified'] is False


@pytest.mark.parametrize('field', ['screen_off', 'touch_wake', 'pin_required', 'operator_approved'])
def test_enable_requires_every_explicit_physical_attestation(field):
    value = dict(schema='sage.lantern.display-physical-acceptance.v1', unit_id='unit-b',
        activation={}, screen_off=True, touch_wake=True, pin_required=True, operator_approved=True)
    value[field] = False
    with pytest.raises(ValueError):
        DISPLAY.boot_acceptance(DISPLAY.encoded(value), {'unit_id': 'unit-b'})


def test_nonblanking_helper_receives_real_snapshot_interface(backend, monkeypatch):
    runner, properties, current = backend
    properties.update(ActiveState='active', MainPID='101')
    process = Process(101, 1, 123, 0, '/usr/bin/python3',
        ('/opt/sage-lantern/venv/bin/python', '-I', '-B', str(DISPLAY.SUPERVISOR), '--serve'), 'R')
    runner.native.processes = SimpleNamespace(read=lambda pid: process, max_processes=1024)
    runner.process = Mock(return_value={'image_sha256': 'python'})
    monkeypatch.setattr(Path, 'iterdir', lambda path: iter([]))
    def expire(seconds):
        runner.deadline = 0
        raise TimeoutError()
    monkeypatch.setattr(DISPLAY.time, 'sleep', expire)
    with pytest.raises(TimeoutError):
        runner.ready()


def test_policy_wrong_unit_never_overwritten(backend, monkeypatch):
    runner, properties, current = backend
    runner.created_policy = False
    runner.expected['policy'] = {'state': 'present', 'sha256': DISPLAY.sha(b'fresh unit-a\n')}
    monkeypatch.setattr(DISPLAY, 'parents', lambda path: None)
    monkeypatch.setattr(DISPLAY, 'read', lambda path: b'fresh unit-a\n')
    monkeypatch.setattr(Path, 'stat', lambda path: SimpleNamespace(st_mode=0o100644))
    with pytest.raises(ValueError):
        DISPLAY.Native.policy(runner)


@pytest.fixture
def proc_tree(tmp_path):
    (tmp_path / 'net').mkdir()
    (tmp_path / 'sys/kernel/random').mkdir(parents=True)
    (tmp_path / 'sys/kernel/random/boot_id').write_text('boot')
    (tmp_path / '101/fd').mkdir(parents=True)
    (tmp_path / '101/stat').write_text('101 (auth) ' + ' '.join(['S'] + ['0'] * 18 + ['123']))
    (tmp_path / 'net/tcp').write_text('header\n0: 0100007F:0C7C remote 0A 0 0 0 0 0 987\n')
    (tmp_path / 'net/tcp6').write_text('header\n')
    (tmp_path / '101/fd/3').symlink_to('socket:[987]')
    return tmp_path


def test_actual_listener_inode_owned_by_pinned_auth(proc_tree):
    assert DISPLAY.listener_owner({'pid': 101, 'start_ticks': 123}, 'boot', proc_tree) == '987'


@pytest.mark.parametrize('failure', ['other_owner', 'wildcard', 'ipv6', 'reused_pid', 'boot', 'duplicate'])
def test_listener_concrete_negative_checks(proc_tree, failure):
    if failure == 'other_owner':
        (proc_tree / '101/fd/3').unlink()
        (proc_tree / '101/fd/3').symlink_to('socket:[988]')
    elif failure == 'wildcard':
        path = proc_tree / 'net/tcp'
        path.write_text(path.read_text().replace('0100007F', '00000000'))
    elif failure == 'ipv6':
        (proc_tree / 'net/tcp6').write_text('header\n0: ' + '0' * 32 + ':0C7C remote 0A 0 0 0 0 0 989\n')
    elif failure == 'reused_pid':
        path = proc_tree / '101/stat'
        path.write_text(path.read_text().replace('123', '124'))
    elif failure == 'boot':
        (proc_tree / 'sys/kernel/random/boot_id').write_text('other')
    else:
        path = proc_tree / 'net/tcp'
        path.write_text(path.read_text() + path.read_text().splitlines()[1] + '\n')
    with pytest.raises(ValueError):
        DISPLAY.listener_owner({'pid': 101, 'start_ticks': 123}, 'boot', proc_tree)


@pytest.mark.parametrize('group', ['/system.slice/other.service', '/user.slice/session.scope',
    '/system.slice/' + DISPLAY.UNIT + '/unreviewed'])
def test_real_process_cgroup_must_be_exact(tmp_path, group):
    (tmp_path / '101').mkdir()
    (tmp_path / '101/cgroup').write_text('0::' + group + '\n')
    with pytest.raises(ValueError):
        DISPLAY.in_unit(SimpleNamespace(pid=101), DISPLAY.UNIT, tmp_path)
    (tmp_path / '101/cgroup').write_text('0::/system.slice/' + DISPLAY.UNIT + '\n')
    DISPLAY.in_unit(SimpleNamespace(pid=101), DISPLAY.UNIT, tmp_path)


@pytest.mark.parametrize('uid,group', [(1000, '/system.slice/' + DISPLAY.UNIT), (0, '/other')])
def test_supervisor_uid_and_manager_cgroup_required(backend, uid, group):
    runner, properties, current = backend
    properties.update(ActiveState='active', MainPID='101', ControlGroup=group)
    runner.native.processes = SimpleNamespace(read=lambda pid:
        Process(101, 1, 123, uid, '/usr/bin/python3', (), 'S'))
    with pytest.raises(ValueError):
        runner.ready()


def test_reviewed_auth_dropins_exact_paths_and_bytes(backend, monkeypatch):
    runner, properties, current = backend
    path = '/etc/systemd/system/' + DISPLAY.AUTH + '.d/reviewed.conf'
    runner.expected['auth_dropins'] = {path: DISPLAY.sha(b'reviewed')}
    properties.update(FragmentPath='/etc/systemd/system/' + DISPLAY.AUTH, DropInPaths=path)
    monkeypatch.setattr(DISPLAY, 'read', lambda path: b'reviewed')
    runner.show(DISPLAY.AUTH)
    monkeypatch.setattr(DISPLAY, 'read', lambda path: b'changed')
    with pytest.raises(ValueError):
        runner.show(DISPLAY.AUTH)
    monkeypatch.setattr(DISPLAY, 'read', lambda path: b'reviewed')
    properties['DropInPaths'] += ' /run/systemd/system/' + DISPLAY.AUTH + '.d/unknown.conf'
    with pytest.raises(ValueError):
        runner.show(DISPLAY.AUTH)


def test_setup_rechecks_listener_after_http(backend, monkeypatch):
    runner, properties, current = backend
    runner.expected.update(auth_process={'pid': 101}, boot_id='boot')
    runner.process = Mock(return_value={'pid': 101})
    owner = Mock(side_effect=['987', '988'])
    monkeypatch.setattr(DISPLAY, 'listener_owner', owner)
    response = SimpleNamespace(status=200, read=lambda maximum: b'{"enabled":true,"recovery_backup_confirmed":true}')
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(DISPLAY.http.client, 'HTTPConnection', lambda *args, **kwargs: connection)
    with pytest.raises(ValueError):
        DISPLAY.Native.setup(runner)
    assert owner.call_count == 2


def test_setup_real_auth_server_contract_and_owned_listener(backend, proc_tree, monkeypatch):
    auth = module('display_setup_real_auth', 'scripts/lantern/unit-a-device-auth.py')
    device = Mock()
    vault = Mock()
    vault.status.return_value = dict(enabled=True, recovery_backup_confirmed=True,
        has_existing_vault=True, can_initialize=False)
    server = auth.DeviceAuthServer(device, vault=vault, bind_and_activate=False)
    server.server_address = ('127.0.0.1', 0)
    server.server_bind()
    server.server_activate()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection_type = DISPLAY.http.client.HTTPConnection
    try:
        headers = {'Host': '127.0.0.1:3196', 'Content-Type': 'application/json',
            'X-Lantern-Auth': 'control'}
        for origin, method, expected_status in [(None, 'POST', 403),
            ('http://untrusted.example', 'POST', 403), ('http://127.0.0.1:3100', 'GET', 405),
            ('http://127.0.0.1:3100', 'POST', 200)]:
            request_headers = dict(headers)
            if origin is not None:
                request_headers['Origin'] = origin
            connection = connection_type(*server.server_address, timeout=2)
            connection.request(method, '/setup/status', body=b'{}' if method == 'POST' else None,
                headers=request_headers)
            response = connection.getresponse()
            assert response.status == expected_status
            response.read()
            connection.close()
        runner, properties, current = backend
        process_pin = dict(pid=101, start_ticks=123, invocation='inv', image_sha256='hash')
        runner.expected.update(auth_process=process_pin, boot_id='boot')
        runner.process = Mock(return_value=process_pin)
        real_owner = DISPLAY.listener_owner
        owner = Mock(side_effect=lambda pinned, boot: real_owner(pinned, boot, proc_tree))
        monkeypatch.setattr(DISPLAY, 'listener_owner', owner)
        def connect(host, port, timeout):
            assert (host, port) == ('127.0.0.1', 3196)
            connection = connection_type(*server.server_address, timeout=timeout)
            request = connection.request
            def send(method, path, body=None, headers=None):
                request(method, path, body=body, headers={**headers, 'Host': '127.0.0.1:3196'})
            connection.request = send
            return connection
        monkeypatch.setattr(DISPLAY.http.client, 'HTTPConnection', connect)
        DISPLAY.Native.setup(runner)
        assert owner.call_count == 2
        assert runner.process.call_count == 2
        device.reserve_attempt.assert_not_called()
        vault.enable.assert_not_called()
        vault.confirm.assert_not_called()
        (proc_tree / '101/fd/3').unlink()
        with pytest.raises(ValueError):
            DISPLAY.Native.setup(runner)
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()
