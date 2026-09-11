import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('audio_config', ROOT / 'scripts/lantern/install-audio-commissioning-config.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SOURCE = (ROOT / 'software/runtime/src/sage_lantern/audio_readiness.py').read_bytes()


def test_exact_current_constants_without_executing_module():
    value = MODULE.constants(SOURCE + b'\nraise RuntimeError("must not execute")\n')
    assert value['VOICE'] == MODULE.VOICE and value['BROKER'] == MODULE.BROKER
    assert value['GUARD'].endswith(' -m sage_lantern.audio_readiness --admit\n')
    assert str(value['ALSA_PATH']) in value['GUARD']
    assert value['ALSA_CONFIG'].startswith('pcm.hw {\n')


@pytest.mark.parametrize('change', [b'\nGUARD = __import__("os").getcwd()\n',
                                  b'\nGUARD = "changed"\n', b'\nVOICE = "other.service"\n'])
def test_constant_redefinition_and_executable_expressions_refuse(change):
    with pytest.raises(ValueError):
        MODULE.constants(SOURCE + change)


@pytest.mark.parametrize('fault', [None, 'current', 'write'])
def test_two_exclusive_writes_journaled_no_activation(monkeypatch, fault):
    outputs = {Path('/alsa'): b'alsa', Path('/guard'): b'guard'}
    events = []
    def current():
        events.append('current')
        if fault == 'current':
            raise ValueError
    def durable(path, raw, mode):
        events.append(('write', path, mode))
        assert raw == outputs[path]
        if fault == 'write':
            raise FileExistsError
    monkeypatch.setattr(MODULE, 'durable', durable)
    backend = SimpleNamespace(outputs=outputs, installed=set(), current=current)
    if fault:
        with pytest.raises((ValueError, FileExistsError)):
            MODULE.transition(backend, lambda *args: events.append(args))
        assert not any(isinstance(item, tuple) and item[0] == 'install_done' for item in events)
    else:
        result = MODULE.transition(backend, lambda *args: events.append(args))
        assert backend.installed == set(outputs)
        assert result['capture_attempted'] is False and result['services_started'] is False
        assert result['daemon_reload_required'] is True and result['readiness_marker_created'] is False
        for path in outputs:
            assert events.index(('install_intent', str(path))) < events.index(('write', path, 0o644))
            assert events.index(('write', path, 0o644)) < events.index(('install_done', str(path)))


@pytest.mark.parametrize('fault', [None, 'active', 'pid', 'job', 'populated', 'procs', 'missing_cgroup'])
def test_inactive_empty_kernel_cgroup_proof_and_show_only(monkeypatch, fault):
    value = dict(LoadState='loaded', ActiveState='active' if fault == 'active' else 'inactive',
        MainPID='1' if fault == 'pid' else '0', Job='waiting' if fault == 'job' else '',
        ControlGroup='', FragmentPath='/etc/systemd/system/' + MODULE.VOICE, DropInPaths='', NeedDaemonReload='no')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs['timeout'] == 5 and kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
        return SimpleNamespace(returncode=0, stdout='\n'.join(key + '=' + item for key, item in value.items()).encode())
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: fault != 'missing_cgroup')
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=0o40755, st_uid=0))
    monkeypatch.setattr(MODULE, 'read', lambda path, limit: (b'populated 1\n' if fault == 'populated' else b'populated 0\nfrozen 0\n')
        if path.name == 'cgroup.events' else b'42\n' if fault == 'procs' else b'')
    if fault not in (None, 'missing_cgroup'):
        with pytest.raises(ValueError):
            MODULE.show(MODULE.VOICE)
    else:
        assert MODULE.show(MODULE.VOICE) == value
    assert calls[0][:2] == ['/usr/bin/systemctl', 'show']
    assert calls[0][-1] == MODULE.VOICE


@pytest.mark.parametrize('fault', [None, 'source', 'old_guard', 'existing_output', 'changed_service'])
def test_current_preserves_all_existing_guards_and_absent_only(monkeypatch, fault):
    backend = MODULE.Install.__new__(MODULE.Install)
    backend.args = SimpleNamespace(source='/source', source_sha256=MODULE.digest(b'source'))
    backend.files = {'/existing-native-guard': MODULE.digest(b'guard')}
    backend.outputs = {Path('/new-alsa'): b'alsa', Path('/new-guard'): b'guard'}
    backend.installed = set()
    backend.guard_path = Path('/new-guard')
    value = {key: 'same' for key in MODULE.FIELDS}
    backend.services = {MODULE.VOICE: value}
    def read(path):
        if path == '/source':
            return b'changed' if fault == 'source' else b'source'
        return b'changed' if fault == 'old_guard' else b'guard'
    monkeypatch.setattr(MODULE, 'read', read)
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: fault == 'existing_output')
    monkeypatch.setattr(MODULE, 'show', lambda service: dict(value, MainPID='changed') if fault == 'changed_service' else dict(value))
    if fault:
        with pytest.raises(ValueError):
            backend.current()
    else:
        backend.current()


def test_durable_existing_file_never_overwritten(tmp_path, monkeypatch):
    path = tmp_path / 'existing'
    path.write_bytes(b'keep')
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    with pytest.raises(FileExistsError):
        MODULE.durable(path, b'replace', 0o644)
    assert path.read_bytes() == b'keep'


@pytest.mark.parametrize('fault', [None, 'not_written', 'extra', 'removed_original', 'duplicate',
                                 'broker_changed', 'guard_bytes', 'active'])
def test_own_written_guard_may_become_loaded_without_reload(monkeypatch, fault):
    backend = MODULE.Install.__new__(MODULE.Install)
    backend.args = SimpleNamespace(source='/source', source_sha256=MODULE.digest(b'source'))
    guard = Path('/etc/systemd/system/' + MODULE.VOICE + '.d/50-fresh-audio-validation.conf')
    original = '/etc/systemd/system/' + MODULE.VOICE + '.d/zzzz-fresh-native-update.conf'
    backend.guard_path = guard
    backend.files = {original: MODULE.digest(b'preserve')}
    backend.outputs = {guard: b'pinned-guard'}
    backend.installed = set() if fault == 'not_written' else {guard}
    baseline = {key: 'same' for key in MODULE.FIELDS}
    baseline['DropInPaths'], baseline['NeedDaemonReload'] = original, 'no'
    backend.services = {MODULE.VOICE: baseline, MODULE.BROKER: dict(baseline, DropInPaths='')}
    def read(path):
        if path == '/source':
            return b'source'
        if path == original:
            return b'preserve'
        return b'wrong' if fault == 'guard_bytes' else b'pinned-guard'
    monkeypatch.setattr(MODULE, 'read', read)
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=0o100644, st_gid=0))
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: False)
    calls = []
    def show(service):
        calls.append(service)
        value = dict(backend.services[service])
        if service == MODULE.VOICE:
            value['DropInPaths'] = str(guard) + ' ' + original
            if fault == 'extra':
                value['DropInPaths'] += ' /unknown.conf'
            if fault == 'removed_original':
                value['DropInPaths'] = str(guard)
            if fault == 'duplicate':
                value['DropInPaths'] += ' ' + str(guard)
            if fault == 'active':
                value['ActiveState'] = 'active'
        elif fault == 'broker_changed':
            value['DropInPaths'] = str(guard)
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    if fault:
        with pytest.raises(ValueError):
            backend.current()
    else:
        backend.current()
        assert calls == [MODULE.VOICE, MODULE.VOICE, MODULE.BROKER, MODULE.BROKER]


@pytest.mark.parametrize('mode', ['inspect', 'apply', 'existing_attempt'])
def test_main_readonly_default_and_exclusive_attempt(tmp_path, monkeypatch, capsys, mode):
    state = tmp_path / 'attempt'
    monkeypatch.setattr(MODULE, 'STATE', state)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    monkeypatch.setattr(MODULE, 'sync', lambda path: None)
    backend = SimpleNamespace(services={}, files={})
    calls = []
    monkeypatch.setattr(MODULE, 'Install', lambda args: calls.append('inspect') or backend)
    def durable(path, raw, permissions):
        with path.open('xb') as stream:
            stream.write(raw)
    monkeypatch.setattr(MODULE, 'durable', durable)
    def transition(value, record):
        calls.append('apply')
        record('install_intent', '/exact/file')
        return {'capture_attempted': False}
    monkeypatch.setattr(MODULE, 'transition', transition)
    args = ['--source', '/source', '--source-sha256', 'pin', '--guard-sha256', 'pin', '--alsa-sha256', 'pin']
    if mode == 'apply':
        args.append('--apply')
    if mode == 'existing_attempt':
        state.mkdir()
        with pytest.raises(ValueError):
            MODULE.main(args)
        assert calls == []
    else:
        MODULE.main(args)
        result = json.loads(capsys.readouterr().out)
        assert result['capture_attempted'] is False
        if mode == 'inspect':
            assert not state.exists() and calls == ['inspect'] and result['mutation_authorized'] is False
        else:
            assert calls == ['inspect', 'apply']
            assert {path.name for path in state.iterdir()} == {'expected.json', '01-install_intent.json'}
