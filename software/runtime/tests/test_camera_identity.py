from pathlib import Path
import pwd
from types import SimpleNamespace

import pytest

from sage_lantern import camera_identity as module


DEVICE = '/dev/v4l/by-id/usb-camera_B_SERIAL-video-index0'


def properties():
    prefix = '{ path=' + str(module.PYTHON) + ' ; argv[]=' + str(module.PYTHON) + ' ' + str(module.HELPER)
    return dict(LoadState='loaded', ActiveState='active', MainPID='9001', InvocationID='a' * 32,
        User='sage-lantern-camera', DynamicUser='yes', ControlGroup='/system.slice/' + module.SERVICE,
        Slice='system.slice', KillMode='control-group', Job='', NeedDaemonReload='no',
        FragmentPath=str(module.UNIT), DropInPaths='', ExecStart=prefix + ' --device=' + DEVICE +
        ' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')


@pytest.mark.parametrize('fault', [None, 'dynamic', 'user', 'dropin', 'fragment', 'job', 'argv', 'multiple_exec', 'missing'])
def test_exact_fresh_unit_definition_and_dynamic_policy(fault):
    value = properties()
    if fault == 'dynamic':
        value['DynamicUser'] = 'no'
    if fault == 'user':
        value['User'] = 'root'
    if fault == 'dropin':
        value['DropInPaths'] = '/unreviewed.conf'
    if fault == 'fragment':
        value['FragmentPath'] = '/unreviewed.service'
    if fault == 'job':
        value['Job'] = 'queued'
    if fault == 'argv':
        value['ExecStart'] = value['ExecStart'].replace(DEVICE, '/dev/video0')
    if fault == 'multiple_exec':
        value['ExecStart'] += ' ' + value['ExecStart']
    if fault == 'missing':
        del value['DynamicUser']
    calls = []
    def command(args, deadline):
        calls.append(args)
        return '\n'.join(key + '=' + content for key, content in value.items())
    system = SimpleNamespace(_command=command)
    if fault:
        with pytest.raises(ValueError):
            module.show(system, 10, DEVICE)
    else:
        result = module.show(system, 10, DEVICE)
        value['ExecStart'] = value['ExecStart'].replace('[n/a]', '[Sun 2026-09-06 12:00:00 UTC]').replace('pid=0', 'pid=9001')
        assert module.show(system, 10, DEVICE) == result
    assert all(call[-1] == module.SERVICE and '--all' in call and call.count('-p') == len(module.FIELDS) for call in calls)


@pytest.mark.parametrize('fault', [None, 'pid', 'ticks', 'uid', 'invocation', 'root', 'argv', 'executable',
                                 'inode', 'cgroup', 'listener', 'health_false', 'unit_changed'])
def test_health_bound_identity_without_nss_and_restart_rejection(monkeypatch, fault):
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.sys, 'platform', 'linux')
    def no_nss(name):
        raise KeyError('No NSS entry for dynamic user')
    monkeypatch.setattr(pwd, 'getpwnam', no_nss)
    monkeypatch.setattr(Path, 'resolve', lambda path, strict: Path('/usr/bin/python3.11'))
    value = properties()
    process = SimpleNamespace(pid=9001, start_ticks=123, uid=63222, executable='/usr/bin/python3.11',
        argv=(str(module.PYTHON), str(module.HELPER), '--device=' + DEVICE), state='S')
    changed = []
    monkeypatch.setattr(module, 'show', lambda *args: dict(value))
    monkeypatch.setattr(module, 'trusted', lambda path, digest=None:
        (1, 2, 'changed' if changed and fault == 'unit_changed' and path == module.UNIT else 'stable'))
    monkeypatch.setattr(Path, 'stat', lambda path: SimpleNamespace(st_dev=1, st_ino=3 if changed and fault == 'inode' else 2))
    monkeypatch.setattr(Path, 'read_text', lambda path: ('b' * 8 + '-' + 'b' * 4 + '-' + 'b' * 4 + '-' + 'b' * 4 + '-' + 'b' * 12)
        if str(path).endswith('boot_id') else 'wrong' if changed and fault == 'cgroup'
        else '0::/system.slice/' + module.SERVICE + '\n')
    monkeypatch.setattr(module, 'listener', lambda pid: 'changed' if changed and fault == 'listener' else '12')
    def health():
        changed.append(True)
        if fault == 'pid':
            process.pid = 9002
            value['MainPID'] = '9002'
        if fault == 'ticks':
            process.start_ticks += 1
        if fault == 'uid':
            process.uid += 1
        if fault == 'root':
            process.uid = 0
        if fault == 'invocation':
            value['InvocationID'] = 'c' * 32
        if fault == 'argv':
            process.argv = ('/python', '/other')
        if fault == 'executable':
            process.executable = '/other'
        return fault != 'health_false'
    arguments = dict(system=SimpleNamespace(), processes=SimpleNamespace(read=lambda pid: process), health_check=health)
    if fault:
        with pytest.raises(ValueError):
            module.verify_camera_identity(DEVICE, 'a' * 64, 'b' * 64, **arguments)
    else:
        result = module.verify_camera_identity(DEVICE, 'a' * 64, 'b' * 64, **arguments)
        assert result.uid == 63222 and result.pid == 9001 and changed == [True]


@pytest.mark.parametrize('fault', [None, 'status', 'absent', 'fake_ready', 'oversize'])
def test_only_bounded_health_get_never_capture(monkeypatch, fault):
    calls = []
    raw = b'{"camera_present":true,"vision_ready":false}'
    if fault == 'absent':
        raw = b'{"camera_present":false,"vision_ready":false}'
    if fault == 'fake_ready':
        raw = b'{"camera_present":true,"vision_ready":true}'
    if fault == 'oversize':
        raw = b'x' * 4097
    response = SimpleNamespace(status=503 if fault == 'status' else 200, read=lambda limit: raw)
    connection = SimpleNamespace(request=lambda *args: calls.append(args),
        getresponse=lambda: response, close=lambda: calls.append('closed'))
    def connect(host, port, timeout):
        assert (host, port, timeout) == ('127.0.0.1', 3198, 2)
        return connection
    monkeypatch.setattr(module.http.client, 'HTTPConnection', connect)
    if fault:
        with pytest.raises(ValueError):
            module.health()
    else:
        assert module.health() is True
    assert calls == [('GET', '/health'), 'closed']


@pytest.mark.parametrize('fault', [None, 'foreign_owner', 'duplicate', 'wildcard', 'missing'])
def test_health_listener_must_belong_to_exact_camera_process(tmp_path, fault):
    (tmp_path / 'net').mkdir()
    (tmp_path / '9001/fd').mkdir(parents=True)
    (tmp_path / '9001/fd/7').symlink_to('socket:[777]')
    row = '0: 0100007F:0C7E 00000000:0000 0A 0:0 00:0 0 63222 0 777 1 0\n'
    if fault == 'foreign_owner':
        (tmp_path / '9001/fd/7').unlink()
    if fault == 'duplicate':
        row += row
    if fault == 'wildcard':
        row = row.replace('0100007F', '00000000')
    if fault == 'missing':
        row = ''
    (tmp_path / 'net/tcp').write_text('header\n' + row)
    if fault:
        with pytest.raises(ValueError):
            module.listener(9001, proc=tmp_path)
    else:
        assert module.listener(9001, proc=tmp_path) == '777'
