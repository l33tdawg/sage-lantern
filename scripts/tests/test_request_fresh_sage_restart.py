import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('fresh_restart',
    Path(__file__).resolve().parents[1] / 'lantern/request-fresh-sage-restart.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('fault', [None, 'redirect', 'refused', 'timeout', 'oversize', 'false', 'numeric',
                                 'cookie', 'wrong_id', 'http200', 'wrong_status', 'empty_boot', 'numeric_boot'])
def test_only_exact_signed_restart_no_retry_or_browser_headers(monkeypatch, fault):
    events = []
    headers = {'X-Agent-ID': 'a' * 64, 'X-Timestamp': '123', 'X-Signature': 'b' * 128, 'X-Nonce': 'c' * 16}
    if fault == 'cookie':
        headers['Cookie'] = 'forbidden'
    if fault == 'wrong_id':
        headers['X-Agent-ID'] = 'd' * 64
    def sign(method, path, body):
        events.append('sign')
        assert (method, path, body) == ('POST', '/v1/dashboard/settings/update/restart', b'')
        return headers
    identity = SimpleNamespace(agent_id='a' * 64, sign_request=sign)
    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ('127.0.0.1', 8080, 25)
        def request(self, method, path, body, headers):
            events.append('request')
            assert (method, path, body) == ('POST', MODULE.ROUTE, b'')
            assert set(headers) == {'X-Agent-ID', 'X-Timestamp', 'X-Signature', 'X-Nonce'}
        def getresponse(self):
            if fault == 'timeout':
                raise TimeoutError('synthetic unknown delivery')
            def read(limit):
                assert limit == 4097
                if fault == 'oversize':
                    return b' ' * 4097
                return json.dumps(dict(ok=1 if fault == 'numeric' else fault != 'false',
                    status='complete' if fault == 'wrong_status' else 'draining',
                    boot_id='' if fault == 'empty_boot' else 1 if fault == 'numeric_boot' else 'process-boot',
                    message='SAGE is closing cleanly, then restarting…')).encode()
            return SimpleNamespace(status=302 if fault == 'redirect' else 403 if fault == 'refused'
                                   else 200 if fault == 'http200' else 202,
                                   read=read)
        def close(self):
            events.append('close')
    monkeypatch.setattr(MODULE.http.client, 'HTTPConnection', Connection)
    if fault is None:
        assert MODULE.request_restart(identity) == {'accepted': True, 'completed': False}
    else:
        with pytest.raises((RuntimeError, TimeoutError)):
            MODULE.request_restart(identity)
    assert events == (['sign'] if fault in ('cookie', 'wrong_id') else ['sign', 'request', 'close'])


@pytest.mark.parametrize('fault', [None, 'link', 'hardlink', 'mode', 'owner', 'oversize'])
def test_descriptor_pinning_rejects_unsafe_key_metadata(tmp_path, monkeypatch, fault):
    path = tmp_path / 'synthetic-seed'
    path.write_bytes(b'x' * (33 if fault == 'oversize' else 32))
    path.chmod(0o644 if fault == 'mode' else 0o600)
    if fault == 'hardlink':
        os.link(path, tmp_path / 'alias')
    if fault == 'link':
        alias = tmp_path / 'alias'
        alias.symlink_to(path)
        path = alias
    real_lstat = Path.lstat
    def lstat(value):
        if value in path.parents:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        return real_lstat(value)
    monkeypatch.setattr(Path, 'lstat', lstat)
    def open_key():
        return MODULE.opened(path, os.geteuid() + (1 if fault == 'owner' else 0),
                             0o600, 32, parent_owners={0})
    if fault is None:
        descriptor, info = open_key()
        try:
            assert info.st_size == 32
            assert MODULE.signature(info) == MODULE.signature(os.fstat(descriptor))
        finally:
            os.close(descriptor)
    else:
        with pytest.raises((RuntimeError, OSError)):
            open_key()


def test_retained_intent_is_exclusive_and_records_no_credentials(tmp_path):
    path = tmp_path / 'intent'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        MODULE.record(descriptor, {'phase': 'intent'})
        MODULE.record(descriptor, {'phase': 'accepted_not_completed', 'completed': False})
    finally:
        os.close(descriptor)
    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {'phase': 'intent'}, {'phase': 'accepted_not_completed', 'completed': False}]
    with pytest.raises(FileExistsError):
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


@pytest.mark.parametrize('name,expected_mode', [('PUBLIC', 0o600), ('UNIT', 0o644), ('UNIT', 0o640)])
@pytest.mark.parametrize('mutated', [False, True])
@pytest.mark.parametrize('wrong_group', [False, True])
def test_public_input_modes_match_commissioned_files(tmp_path, monkeypatch, name, expected_mode, mutated, wrong_group):
    receipt = tmp_path / 'initialized-public.json'
    unit = tmp_path / 'unit.env'
    receipt.write_text(json.dumps(dict(schema='sage.lantern.initialized.v1', unit_id='unit-a', root_id='a' * 64)))
    unit.write_text('UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n')
    receipt.chmod(0o600)
    unit.chmod(0o644)
    monkeypatch.setattr(MODULE, 'PUBLIC', receipt)
    monkeypatch.setattr(MODULE, 'UNIT', unit)
    target = getattr(MODULE, name)
    target.chmod(expected_mode)
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_gid=993))
    if mutated:
        target.chmod(0o644 if expected_mode == 0o600 else 0o600)
    original_lstat, original_fstat = Path.lstat, os.fstat
    def root_owned(info):
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        return SimpleNamespace(**{field: 0 if field == 'st_uid' else (994 if wrong_group else 993) if field == 'st_gid'
                                  else getattr(info, field) for field in fields})
    def lstat(path):
        if path in target.parents:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        return root_owned(original_lstat(path))
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(os, 'fstat', lambda descriptor: root_owned(original_fstat(descriptor)))
    if mutated or (wrong_group and expected_mode == 0o640):
        with pytest.raises(RuntimeError):
            MODULE.public_bytes(target)
    else:
        assert MODULE.public_bytes(target) == target.read_bytes()


def test_public_reader_has_no_arbitrary_path_allowance(tmp_path):
    with pytest.raises(RuntimeError):
        MODULE.public_bytes(tmp_path / 'other')


@pytest.mark.parametrize('fault', [None, 'wrong_public_id', 'changed_key'])
def test_main_drops_credentials_before_sdk_and_retains_intent(tmp_path, monkeypatch, fault):
    boot = '11111111-1111-4111-8111-111111111111'
    root_id = 'a' * 64
    raw = json.dumps(dict(schema='sage.lantern.initialized.v1', unit_id='unit-a', root_id=root_id)).encode()
    seed = tmp_path / 'synthetic-seed'
    seed.write_bytes(b'x' * 32)
    seed.chmod(0o600)
    monkeypatch.setattr(MODULE, 'KEY', seed)
    monkeypatch.setattr(MODULE, 'INTENT', tmp_path / 'intent')
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE, 'public_bytes', lambda path: raw if path == MODULE.PUBLIC else
                        b'UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n')
    original_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda path, *args, **kwargs:
                        boot if str(path) == '/proc/sys/kernel/random/boot_id' else original_read(path, *args, **kwargs))
    original_lstat = Path.lstat
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=stat.S_IFREG | 0o755,
        st_uid=0, st_nlink=1) if str(path) == MODULE.__file__ else original_lstat(path))
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=1001, pw_gid=1002))
    def show(arguments, **kwargs):
        assert arguments == ['/usr/bin/systemctl', 'show', MODULE.SERVICE,
                             '-p', 'User', '-p', 'ActiveState', '-p', 'MainPID', '-p', 'Job', '--all']
        return SimpleNamespace(stdout='User=sage-lantern\nActiveState=active\nMainPID=123\nJob=0\n')
    monkeypatch.setattr(MODULE.subprocess, 'run', show)
    def opened(*args, **kwargs):
        descriptor = os.open(seed, os.O_RDONLY)
        return descriptor, os.fstat(descriptor)
    monkeypatch.setattr(MODULE, 'opened', opened)
    calls = []
    current_uid = [0]
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: current_uid[0])
    monkeypatch.setattr(MODULE.os, 'setgroups', lambda value: calls.append(('groups', value)))
    monkeypatch.setattr(MODULE.os, 'setgid', lambda value: calls.append(('gid', value)))
    def setuid(value):
        calls.append(('uid', value))
        current_uid[0] = value
    monkeypatch.setattr(MODULE.os, 'setuid', setuid)
    monkeypatch.setattr(MODULE.os, 'environ', {})
    monkeypatch.setattr(MODULE.resource, 'setrlimit', lambda *args: None)
    def load(path):
        assert calls == [('groups', []), ('gid', 1002), ('uid', 1001)]
        assert path.startswith('/proc/self/fd/')
        assert json.loads(MODULE.INTENT.read_text())['phase'] == 'intent'
        if fault == 'changed_key':
            seed.write_bytes(b'y' * 32)
        return SimpleNamespace(agent_id='b' * 64 if fault == 'wrong_public_id' else root_id)
    monkeypatch.setitem(sys.modules, 'sage_sdk', SimpleNamespace(AgentIdentity=SimpleNamespace(from_file=load)))
    requests = []
    monkeypatch.setattr(MODULE, 'request_restart', lambda identity:
                        requests.append(True) or dict(accepted=True, completed=False))
    arguments = ['--unit-id', 'unit-a', '--boot-id', boot, '--root-id', root_id,
                 '--initialized-sha256', MODULE.hashlib.sha256(raw).hexdigest(), '--request-restart']
    if fault is None:
        MODULE.main(arguments)
    else:
        with pytest.raises(RuntimeError):
            MODULE.main(arguments)
    assert requests == ([True] if fault is None else [])
    assert MODULE.INTENT.exists()
