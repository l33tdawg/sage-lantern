import importlib.util
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('vision_activation',
    Path(__file__).resolve().parents[1] / 'lantern/preflight-fresh-vision-activation.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
BOOT = '8f1b0d28-8c03-4aa7-b015-dc6f45bb6b51'
ACTIVE = dict(broker_id='a' * 32, vault_epoch='b' * 64 + ':2', broker_pid=123, voice_pid=456)


@pytest.fixture
def observed(monkeypatch):
    monkeypatch.setattr(Path, 'read_text', lambda self: BOOT)
    def service(name):
        return dict.fromkeys(MODULE.FIELDS, '') | dict(LoadState='loaded', ActiveState='active',
            MainPID=str(123 if 'mesh-runtime' in name else 456), NeedDaemonReload='no')
    monkeypatch.setattr(MODULE, 'service', service)
    monkeypatch.setattr(MODULE, 'broker_active', lambda: dict(ACTIVE))
    monkeypatch.setattr(MODULE, 'public_file', lambda name, **kwargs: (
        dict(path=name, sha256='c' * 64, metadata={}), b''))
    candidate = dict(evidence_requirements={name: dict(path='/public/' + name, sha256='c' * 64)
                                           for name in ('model', 'projector', 'binary')})
    planner = SimpleNamespace(plan=lambda unit, fresh_audio: candidate)
    return lambda **kwargs: MODULE.observe(planner, {'sha256': 'd' * 64}, 'unit-a', 'c' * 64, **kwargs)


def test_good_observations_are_never_apply_authority(observed):
    result = observed()
    assert result['activation_authorized'] is False
    assert result['drain_verified'] is False
    assert result['history_outcome'] == 'unobserved'
    assert result['capture_performed'] is False
    assert result['pins']['broker'] == ACTIVE
    assert len(result['blockers']) == 5


def test_consent_does_not_capture_or_clear_other_gates(observed):
    result = observed(capture_approved=True)
    assert len(result['blockers']) == 4
    assert result['capture_performed'] is False
    assert result['activation_authorized'] is False


@pytest.mark.parametrize('failure', ['broker', 'services', 'artifact'])
def test_observation_failures_remain_blockers_without_exception_contents(observed, monkeypatch, failure):
    def failed(*args, **kwargs):
        raise RuntimeError('sensitive exception must not be printed')
    monkeypatch.setattr(MODULE, {'broker': 'broker_active', 'services': 'service',
                                'artifact': 'public_file'}[failure], failed)
    result = observed()
    assert len(result['blockers']) > 5
    assert 'sensitive' not in json.dumps(result)


def test_broker_lifetime_change_rejected(observed, monkeypatch):
    values = iter([dict(ACTIVE), dict(ACTIVE, broker_id='e' * 32)])
    monkeypatch.setattr(MODULE, 'broker_active', lambda: next(values))
    assert 'broker_lifetime_changed' in observed()['blockers']


def test_service_pid_mismatch_rejected(observed, monkeypatch):
    original = MODULE.service
    monkeypatch.setattr(MODULE, 'service', lambda name: original(name) | {'MainPID': '999'})
    assert any('broker_service_binding_mismatch' in item for item in observed()['blockers'])


def test_absent_idle_controller_is_not_warm_deployment_ready(observed, monkeypatch):
    original = MODULE.service
    monkeypatch.setattr(MODULE, 'service', lambda name: original(name) |
        ({'LoadState': 'not-found'} if 'idle-suspend' in name else {}))
    assert 'warm_deployment_idle_controller_unavailable' in observed()['blockers']


@pytest.mark.parametrize('fault', ['link', 'writable', 'oversized'])
def test_public_file_rejects_unsafe_leaf(tmp_path, monkeypatch, fault):
    target = tmp_path / 'input'
    target.write_bytes(b'public')
    if fault == 'writable':
        target.chmod(0o666)
    if fault == 'link':
        link = tmp_path / 'link'
        link.symlink_to(target)
        target = link
    original = Path.lstat
    def root_info(path):
        info = original(path)
        fields = MODULE.identity(info)
        fields['st_uid'] = 0
        if path != target:
            fields['st_mode'] = 0o40755
        return SimpleNamespace(**fields)
    monkeypatch.setattr(Path, 'lstat', root_info)
    original_fstat = MODULE.os.fstat
    monkeypatch.setattr(MODULE.os, 'fstat', lambda descriptor: SimpleNamespace(
        **(MODULE.identity(original_fstat(descriptor)) | {'st_uid': 0})))
    with pytest.raises((OSError, ValueError)):
        MODULE.public_file(target, limit=1 if fault == 'oversized' else 100)


def test_systemctl_is_show_only_with_separate_properties(monkeypatch):
    def run(command, **kwargs):
        assert command[:4] == ['/usr/bin/systemctl', 'show', MODULE.SERVICES[0], '--all']
        assert command[4:] == [entry for field in MODULE.FIELDS for entry in ('-p', field)]
        assert kwargs['timeout'] == 5
        return SimpleNamespace(stdout='\n'.join(field + '=' for field in MODULE.FIELDS))
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    assert set(MODULE.service(MODULE.SERVICES[0])) == set(MODULE.FIELDS)


@pytest.mark.parametrize('fault', [None, 'uid', 'pid', 'nonce', 'duplicate', 'length', 'bool_pid', 'epoch'])
def test_concrete_ipc_only_readonly_nonce_root_bound(monkeypatch, fault):
    class Connection:
        raw = b''
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, timeout):
            assert timeout == 5
        def connect(self, path):
            assert path == MODULE.SOCKET
        def getsockopt(self, *args):
            return struct.pack('3i', 999 if fault == 'pid' else 123, 1000 if fault == 'uid' else 0, 0)
        def sendall(self, raw):
            request = json.loads(raw[4:])
            assert set(request) == {'op', 'nonce'}
            assert request['op'] == 'verify-deployment-active'
            value = dict(ACTIVE, nonce=request['nonce'])
            if fault == 'nonce':
                value['nonce'] = 'incorrect'
            if fault == 'bool_pid':
                value['voice_pid'] = True
            if fault == 'epoch':
                value['vault_epoch'] = 'b' * 64 + ':0'
            body = json.dumps({'result': value}).encode()
            if fault == 'duplicate':
                body = b'{"result":{},"result":{}}'
            self.raw = (2048 if fault == 'length' else len(body)).to_bytes(4, 'big') + body
        def recv(self, size):
            chunk, self.raw = self.raw[:min(size, 3)], self.raw[min(size, 3):]
            return chunk
    monkeypatch.setattr(MODULE.socket, 'SO_PEERCRED', 17, raising=False)
    monkeypatch.setattr(MODULE.socket, 'socket', lambda *args: Connection())
    if fault:
        with pytest.raises(ValueError):
            MODULE.broker_active()
    else:
        assert MODULE.broker_active() == ACTIVE


def test_source_loader_checks_digest_before_execution(monkeypatch):
    monkeypatch.setattr(MODULE, 'public_file', lambda *args, **kwargs: (
        {'sha256': 'a' * 64}, b'raise AssertionError("must not execute")'))
    with pytest.raises(ValueError):
        MODULE.planner('/reviewed/planner.py', 'b' * 64)


def test_no_apply_cli():
    with pytest.raises(SystemExit) as error:
        MODULE.main(['--apply'])
    assert error.value.code == 2
