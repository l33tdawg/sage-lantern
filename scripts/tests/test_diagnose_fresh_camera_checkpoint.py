import ast
import importlib.util
import json
from pathlib import Path
import pwd
from types import SimpleNamespace

import pytest


PATH = Path(__file__).resolve().parents[1] / 'lantern/diagnose-fresh-camera-checkpoint.py'
SPEC = importlib.util.spec_from_file_location('camera_diagnostic', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_frames_never_print_message_locals_or_source(capsys):
    try:
        private_fixture = 'not-for-output'
        raise ValueError(private_fixture)
    except ValueError as error:
        MODULE.frames('test', error)
    raw = capsys.readouterr().out
    assert 'not-for-output' not in raw and 'private_fixture' not in raw
    value = json.loads(raw)
    assert value['mutation_authorized'] is False and value['error_type'] == 'ValueError'
    assert all(set(frame) == {'file', 'line', 'function'} for frame in value['frames'])


def test_no_mutation_method_calls():
    forbidden = {'start', 'release', 'install', 'unlink', 'mkdir', 'replace', 'durable', 'write_text', 'write_bytes'}
    for node in ast.walk(ast.parse(PATH.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden


@pytest.mark.parametrize('fault', [None, 'uid', 'ticks', 'invocation', 'dynamic_no', 'wrong_user',
    'cgroup', 'executable', 'argv', 'inode', 'restarted', 'missing_dynamic'])
def test_dynamic_camera_without_nss_requires_all_native_identity_proofs(monkeypatch, fault):
    def missing(name):
        raise KeyError('NSS deliberately unavailable')
    monkeypatch.setattr(pwd, 'getpwnam', missing)
    camera = dict(pid=422122, start_ticks=12345, uid=61234, invocation='f' * 32)
    value = dict(LoadState='loaded', ActiveState='active', MainPID='422122', InvocationID='f' * 32,
                 User='sage-lantern-camera', ControlGroup='/system.slice/' + MODULE.CAMERA, Job='')
    process = SimpleNamespace(pid=422122, uid=61234, start_ticks=12345, executable='/python',
                              argv=('/venv/python', '/camera'), state='S')
    if fault == 'uid':
        process.uid = 994
    if fault == 'ticks':
        process.start_ticks = 12346
    if fault == 'invocation':
        value['InvocationID'] = 'e' * 32
    if fault == 'wrong_user':
        value['User'] = 'other'
    if fault == 'executable':
        process.executable = '/other'
    if fault == 'argv':
        process.argv = ('/python', '/other')
    calls = []
    def command(args, deadline):
        calls.append(args)
        result = dict(value, DynamicUser='no' if fault == 'dynamic_no' else 'yes')
        if fault == 'missing_dynamic':
            del result['DynamicUser']
        if fault == 'restarted' and len(calls) == 2:
            result['MainPID'] = '422123'
        return '\n'.join(key + '=' + content for key, content in result.items())
    backend = SimpleNamespace(observer=SimpleNamespace(deadline=100, system=SimpleNamespace(_command=command)),
        expected={'start': {MODULE.CAMERA: {'executable': '/python', 'argv': ['/venv/python', '/camera']}}},
        preflight=SimpleNamespace(LinuxProcesses=lambda: SimpleNamespace(read=lambda pid: process)))
    monkeypatch.setattr(Path, 'read_text', lambda path: 'wrong' if fault == 'cgroup'
                        else '0::/system.slice/' + MODULE.CAMERA + '\n')
    monkeypatch.setattr(Path, 'stat', lambda path: SimpleNamespace(st_dev=1,
        st_ino=2 if fault == 'inode' and str(path).startswith('/proc/') else 1))
    if fault:
        with pytest.raises(ValueError):
            MODULE.dynamic_camera_process(backend, camera, value)
    else:
        assert MODULE.dynamic_camera_process(backend, camera, value) is process
        assert len(calls) == 2 and all(call[-1] == MODULE.CAMERA for call in calls)


def test_non_camera_services_keep_original_identity_validation():
    calls = []
    backend = SimpleNamespace(process=lambda service, value: calls.append((service, value)))
    MODULE.bind_camera_process(backend, {})
    backend.process('sage-lantern-display-power.service', {'User': 'root'})
    assert calls == [('sage-lantern-display-power.service', {'User': 'root'})]
