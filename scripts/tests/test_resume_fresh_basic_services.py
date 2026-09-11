import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('basic_resume',
    Path(__file__).resolve().parents[1] / 'lantern/resume-fresh-basic-services.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def proc_tree(tmp_path):
    process = tmp_path / '420488'
    (process / 'fd').mkdir(parents=True)
    (tmp_path / 'net').mkdir()
    (tmp_path / 'sys/kernel/random').mkdir(parents=True)
    (tmp_path / 'sys/kernel/random/boot_id').write_text('boot')
    fields = ['0'] * 20
    fields[19] = '4313493'
    (process / 'stat').write_text('420488 (python) ' + ' '.join(fields))
    (process / 'fd/7').symlink_to('socket:[1568684]')
    row = '0: 0100007F:0C7C 00000000:0000 0A 00000000:00000000 00:0 00000000 994 0 1568684 1 0 100 0 0\n'
    (tmp_path / 'net/tcp').write_text('header\n' + row)
    return process, row


@pytest.mark.parametrize('fault', [None, 'bookkeeping', 'foreign', 'duplicate', 'wildcard', 'pid_reused', 'boot', 'malformed'])
def test_listener_stable_identity_and_actual_fd_ownership(tmp_path, fault):
    process, row = proc_tree(tmp_path)
    baseline = MODULE.listener(420488, 4313493, 'boot', proc=tmp_path)
    if fault == 'bookkeeping':
        (tmp_path / 'net/tcp').write_text('header\n' + row.replace('1568684 1 0', '1568684 3 999'))
    if fault == 'foreign':
        (process / 'fd/7').unlink()
    if fault == 'duplicate':
        (tmp_path / 'net/tcp').write_text('header\n' + row + row)
    if fault == 'wildcard':
        (tmp_path / 'net/tcp').write_text('header\n' + row.replace('0100007F', '00000000'))
    if fault == 'pid_reused':
        (process / 'stat').write_text((process / 'stat').read_text().replace('4313493', '4313494'))
    if fault == 'boot':
        (tmp_path / 'sys/kernel/random/boot_id').write_text('different')
    if fault == 'malformed':
        (tmp_path / 'net/tcp').write_text('header\ninvalid\n')
    if fault in (None, 'bookkeeping'):
        assert MODULE.listener(420488, 4313493, 'boot', proc=tmp_path) == baseline
    else:
        with pytest.raises(ValueError):
            MODULE.listener(420488, 4313493, 'boot', proc=tmp_path)


@pytest.mark.parametrize('fault', [None, 'unlocked', 'listener_changed', 'pid', 'ticks', 'uid', 'invocation'])
def test_adopt_auth_requires_exact_pins_and_locked_state(monkeypatch, fault):
    auth = dict(pid=420488, start_ticks=4313493, uid=994, invocation='f' * 32)
    class Base:
        pass
    base = SimpleNamespace(Restore=Base, snapshot=lambda value, service: value, BASIC=('status',))
    backend = MODULE.resume_type(base, {'auth': auth}).__new__(MODULE.resume_type(base, {'auth': auth}))
    value = {'InvocationID': 'f' * 32}
    process = SimpleNamespace(pid=420488, start_ticks=4313493, uid=994)
    if fault in ('pid', 'ticks', 'uid'):
        setattr(process, 'start_ticks' if fault == 'ticks' else fault, 999)
    if fault == 'invocation':
        value['InvocationID'] = 'e' * 32
    backend.show = lambda service: value
    backend.running = {MODULE.AUTH: value}
    backend.process = lambda service, observed: process
    backend.binding = {'boot_id': 'boot'}
    backend.observer = SimpleNamespace(get=lambda port, route: {'locked': fault != 'unlocked'})
    calls = []
    def listener(*args):
        calls.append(args)
        return ('address', '0A', str(len(calls)) if fault == 'listener_changed' else 'inode')
    monkeypatch.setattr(MODULE, 'listener', listener)
    if fault:
        with pytest.raises(ValueError):
            backend.locked()
    else:
        backend.locked()
        assert len(calls) == 2


@pytest.mark.parametrize('service', [MODULE.AUTH, 'sage-lantern-voice-loop.service',
    'sage-lantern-llm.service', 'sage-lantern-mesh-runtime.service', 'sage-lantern-personal-sage.service'])
def test_resume_cannot_restart_auth_or_release_other_services(service):
    base = SimpleNamespace(Restore=object, BASIC=('status', 'camera', 'display'))
    kind = MODULE.resume_type(base, {})
    backend = kind.__new__(kind)
    with pytest.raises(ValueError):
        backend.start(service)
    with pytest.raises(ValueError):
        backend.release(service, lambda phase: None)
    with pytest.raises(ValueError):
        backend.install('status')


@pytest.mark.parametrize('fault', [None, 'current', 'release_status', 'start_status', 'start_camera'])
def test_resume_transition_no_reinstall_auth_or_retry(fault):
    events = []
    def action(name):
        events.append(name)
        if name == fault:
            raise ValueError
    backend = SimpleNamespace(current=lambda: action('current'),
        release=lambda service, record: action('release_' + service), start=lambda service: action('start_' + service))
    if fault:
        with pytest.raises(ValueError):
            MODULE.transition(backend, ('status', 'camera', 'display'), events.append)
        assert events[-1] == fault and fault + '_done' not in events
    else:
        result = MODULE.transition(backend, ('status', 'camera', 'display'), events.append)
        assert result['auth_restarted'] is False and result['voice_activated'] is False
        assert result['vault_keys_resident'] is True and result['cryptographic_lock_verified'] is False
    assert not any('install' in event or 'auth' in event for event in events)


@pytest.mark.parametrize('fault', [None, 'missing', 'extra', 'changed', 'backup', 'expected'])
def test_exact_checkpoint_journal_and_backups(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(MODULE, 'ORIGINAL', tmp_path)
    phases = ['install_voice_loop_intent', 'install_voice_loop_done', 'install_status_intent',
        'install_status_done', 'release_' + MODULE.AUTH + '_intent',
        'remove_' + MODULE.AUTH + '_zzzz-fresh-native-update.conf_intent',
        'remove_' + MODULE.AUTH + '_zzzz-fresh-native-update.conf_done',
        'release_' + MODULE.AUTH + '_done', 'start_' + MODULE.AUTH + '_intent']
    sha = MODULE.hashlib.sha256
    expected = {'old_helpers': {key: {'sha256': sha(key.encode()).hexdigest()} for key in ('voice_loop', 'status')}}
    raw = json.dumps(expected).encode()
    checkpoint = {'original_expected': {'path': str(tmp_path / 'expected.json'), 'sha256': sha(raw).hexdigest()}, 'journal': {}}
    (tmp_path / 'expected.json').write_bytes(raw)
    for key in expected['old_helpers']:
        (tmp_path / (key + '.before')).write_bytes(key.encode())
        (tmp_path / (key + '.before')).chmod(0o755)
    for index, phase in enumerate(phases, 1):
        name = f'{index:02d}-{phase}.json'
        content = json.dumps(dict(phase=phase, expected_sha256=sha(raw).hexdigest())).encode()
        (tmp_path / name).write_bytes(content)
        checkpoint['journal'][name] = sha(content).hexdigest()
    if fault == 'missing':
        (tmp_path / ('09-' + phases[-1] + '.json')).unlink()
    if fault == 'extra':
        (tmp_path / 'extra').touch()
    if fault == 'changed':
        (tmp_path / ('09-' + phases[-1] + '.json')).write_bytes(b'{}')
    if fault == 'backup':
        (tmp_path / 'status.before').write_bytes(b'changed')
    if fault == 'expected':
        (tmp_path / 'expected.json').write_bytes(b'{}')
    original = Path.lstat
    def info(path):
        result = original(path)
        return SimpleNamespace(st_uid=0, st_mode=(0o40700 if path == tmp_path else result.st_mode))
    monkeypatch.setattr(Path, 'lstat', info)
    monkeypatch.setattr(MODULE, 'read', lambda path: Path(path).read_bytes())
    base = SimpleNamespace(decode=json.loads, TARGETS=expected['old_helpers'])
    if fault:
        with pytest.raises((ValueError, FileNotFoundError)):
            MODULE.original_journal(base, checkpoint)
    else:
        assert MODULE.original_journal(base, checkpoint) == expected


@pytest.mark.parametrize('mode', ['inspect', 'apply', 'existing'])
def test_main_inspection_readonly_apply_exclusive_separate_journal(tmp_path, monkeypatch, mode, capsys):
    state = tmp_path / 'resume'
    monkeypatch.setattr(MODULE, 'STATE', state)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    source = b'import json\ndecode=json.loads\nBASIC=("status", "camera", "display")\n'
    digest = MODULE.hashlib.sha256(source).hexdigest()
    monkeypatch.setattr(MODULE, 'ORIGINAL_SHA', digest)
    raw = json.dumps({'original_helper': {'path': '/frozen/helper.py', 'sha256': digest}}).encode()
    monkeypatch.setattr(MODULE, 'pinned', lambda item: source if item['path'] == '/frozen/helper.py' else raw)
    events = []
    def durable(path, data):
        with path.open('xb') as stream:
            stream.write(data)
    backend = SimpleNamespace(runtime=SimpleNamespace(sync=lambda path: None, durable=durable),
        current=lambda: events.append('check'), release=lambda service, record: events.append('release_' + service),
        start=lambda service: events.append('start_' + service))
    def build(base, checkpoint):
        events.append('inspect')
        return backend
    monkeypatch.setattr(MODULE, 'build', build)
    args = ['--checkpoint', '/checkpoint.json', '--checkpoint-sha256', 'pin']
    if mode == 'apply':
        args.append('--apply')
    if mode == 'existing':
        state.mkdir()
        with pytest.raises(ValueError):
            MODULE.main(args)
        assert events == []
    else:
        MODULE.main(args)
        output = json.loads(capsys.readouterr().out)
        if mode == 'inspect':
            assert not state.exists() and events == ['inspect']
            assert output['mutation_authorized'] is False
            assert output['vault_keys_resident'] is True and output['cryptographic_lock_verified'] is False
        else:
            assert (state / 'checkpoint.json').read_bytes() == raw
            assert len(list(state.iterdir())) == 13
            assert output['auth_restarted'] is False


@pytest.mark.parametrize('ack', [False, None, 1, 'true', 'missing'])
def test_checkpoint_requires_literal_key_resident_acknowledgement(ack):
    checkpoint = dict(schema='sage.lantern.basic-restore-resume.v1', original_helper={'sha256': MODULE.ORIGINAL_SHA},
        original_expected={}, journal={}, auth={})
    if ack != 'missing':
        checkpoint['key_resident_basic_restore'] = ack
    with pytest.raises(ValueError):
        MODULE.build(SimpleNamespace(Restore=object), checkpoint)


@pytest.mark.parametrize('fault', [None, 'encrypted_false', 'encrypted_integer', 'vault_true', 'vault_integer',
    'vault_missing', 'boot', 'version', 'binary', 'initialized', 'inode', 'ticks', 'uid', 'argv',
    'process_stopped', 'listener_changed', 'governance'])
def test_key_resident_native_contract_preserves_all_other_proofs(monkeypatch, fault):
    base = SimpleNamespace(Restore=object)
    kind = MODULE.resume_type(base, {'key_resident_basic_restore': True})
    backend = kind.__new__(kind)
    backend.binding = dict(boot_id='hostboot', initialized_sha256='initialized', chain_id='chain',
                           native_after={'binary_sha256': 'binary', 'version': 'v27-reviewed'})
    backend.sup = dict(evidence={'path': 'evidence'}, binding={'path': 'binding'})
    backend.evidence = {'sage_after': dict(pid=123, uid=994, start_ticks=456, application_boot_id='appboot')}
    process = SimpleNamespace(uid=994, start_ticks=456, executable='/native', argv=('/native', 'serve'), state='S')
    if fault == 'ticks':
        process.start_ticks = 457
    if fault == 'uid':
        process.uid = 0
    if fault == 'argv':
        process.argv = ('/native', 'other')
    if fault == 'process_stopped':
        process.state = 'T'
    evidence_reads = []
    backend.runtime = SimpleNamespace(pinned=lambda item: evidence_reads.append(item['path']),
        sha=lambda raw: raw, read=lambda path: ('changed' if fault == ('binary' if path == '/native' else 'initialized')
                                               else 'binary' if path == '/native' else 'initialized'))
    governance = []
    def check_governance(*args):
        governance.append(args)
        if fault == 'governance':
            raise ValueError
    backend.preflight = SimpleNamespace(CONFIG='/config/', BINARY='/native', governance=check_governance,
                                         LinuxProcesses=lambda: SimpleNamespace(read=lambda pid: process))
    health = dict(sage='running', version='v27-reviewed', boot_id='appboot', encrypted=True, vault_locked=False)
    if fault == 'encrypted_false':
        health['encrypted'] = False
    if fault == 'encrypted_integer':
        health['encrypted'] = 1
    if fault == 'vault_true':
        health['vault_locked'] = True
    if fault == 'vault_integer':
        health['vault_locked'] = 0
    if fault == 'vault_missing':
        del health['vault_locked']
    if fault == 'boot':
        health['boot_id'] = 'different'
    if fault == 'version':
        health['version'] = 'different'
    backend.observer = SimpleNamespace(get=lambda port, route: health if port == 8080 else {'actual': route})
    listeners = []
    def listener(*args):
        listeners.append(args)
        return len(listeners) if fault == 'listener_changed' else 7
    backend.validator = SimpleNamespace(listener_owner=listener)
    monkeypatch.setattr(Path, 'read_text', lambda path: 'hostboot')
    monkeypatch.setattr(Path, 'stat', lambda path: SimpleNamespace(st_dev=1,
        st_ino=2 if fault == 'inode' and str(path).startswith('/proc/') else 1))
    if fault:
        with pytest.raises((ValueError, KeyError)):
            backend.native()
    else:
        backend.native()
        assert evidence_reads == ['evidence', 'binding']
        assert len(listeners) == 2 and len(governance) == 1
        assert governance[0][2] == 'chain' and len(governance[0]) == 4
