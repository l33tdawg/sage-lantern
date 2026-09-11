import importlib.util
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('constructor_recovery',
    Path(__file__).resolve().parents[1] / 'lantern/recover-constructor-bootstrap.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def service_values():
    baseline = {key: '0' for key in MODULE.FIELDS}
    baseline.update(LoadState='loaded', ActiveState='inactive', SubState='dead', MainPID='0', Job='',
        ControlGroup='', Slice='system.slice', KillMode='control-group', Restart='no', Result='success',
        DropInPaths='', NeedDaemonReload='no')
    voice = dict(baseline, FragmentPath='/etc/systemd/system/' + MODULE.VOICE,
        ActiveState='failed', SubState='failed', Result='exit-code', ExecMainCode='1', ExecMainStatus='1')
    broker = dict(baseline, FragmentPath='/etc/systemd/system/' + MODULE.BROKER,
        ActiveState='failed', SubState='failed', Result='exit-code', ExecMainStartTimestampMonotonic='100',
        ExecMainExitTimestampMonotonic='200', ExecMainCode='1', ExecMainStatus='1')
    model = dict(baseline, FragmentPath='/etc/systemd/system/' + MODULE.MODEL,
        ActiveState='active', SubState='running', MainPID='99', InvocationID='m' * 32)
    for service, value in ((MODULE.VOICE, voice), (MODULE.BROKER, broker)):
        value.update(zip(('ExecMainPID', 'InvocationID', 'ExecMainStartTimestampMonotonic',
                          'ExecMainExitTimestampMonotonic'), MODULE.FAILED[service]))
    return {MODULE.VOICE: voice, MODULE.BROKER: broker, MODULE.MODEL: model}


@pytest.mark.parametrize('fault', [None, 'voice_started', 'voice_restarted', 'voice_pid', 'broker_job',
    'broker_running', 'empty_unverified', 'model_stopped', 'exit_order', 'configuration_changed'])
def test_constructor_services_require_exact_history_and_current_containment(monkeypatch, fault):
    expected = service_values()
    if fault == 'voice_started':
        expected[MODULE.VOICE]['ExecMainStartTimestampMonotonic'] = '50'
    if fault == 'voice_restarted':
        expected[MODULE.VOICE]['NRestarts'] = '1'
    if fault == 'voice_pid':
        expected[MODULE.VOICE]['MainPID'] = '22'
    if fault == 'broker_job':
        expected[MODULE.BROKER]['Job'] = 'queued'
    if fault == 'broker_running':
        expected[MODULE.BROKER]['ActiveState'] = 'active'
    if fault == 'model_stopped':
        expected[MODULE.MODEL]['ActiveState'] = 'inactive'
    if fault == 'exit_order':
        expected[MODULE.BROKER]['ExecMainExitTimestampMonotonic'] = '99'
    def show(service):
        value = dict(expected[service])
        if fault == 'configuration_changed':
            value['DropInPaths'] = '/unknown.conf'
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    empties = []
    def empty(service):
        empties.append(service)
        if fault == 'empty_unverified':
            raise ValueError
    monkeypatch.setattr(MODULE, 'empty', empty)
    if fault:
        with pytest.raises(ValueError):
            MODULE.services(expected)
    else:
        MODULE.services(expected)
        assert empties == [MODULE.VOICE, MODULE.BROKER]


def test_reset_failed_does_not_rewrite_recorded_exit_identity(monkeypatch):
    expected = service_values()
    def show(service):
        value = dict(expected[service])
        if service == MODULE.BROKER:
            value.update(ActiveState='inactive', SubState='dead', Result='success')
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    monkeypatch.setattr(MODULE, 'empty', lambda service: None)
    MODULE.services(expected, reset={MODULE.BROKER})
    assert expected[MODULE.BROKER]['ActiveState'] == 'failed'
    assert expected[MODULE.BROKER]['ExecMainExitTimestampMonotonic'] == MODULE.FAILED[MODULE.BROKER][3]


@pytest.mark.parametrize('service', [MODULE.VOICE, MODULE.BROKER])
@pytest.mark.parametrize('mask', range(64))
def test_reset_accepts_only_complete_original_or_complete_gc_identity(monkeypatch, mask, service):
    expected = service_values()
    original = json.loads(json.dumps(expected))
    cleared = {'InvocationID': '', 'ExecMainStartTimestampMonotonic': '0',
               'ExecMainExitTimestampMonotonic': '0', 'ExecMainCode': '0', 'ExecMainStatus': '0', 'ExecMainPID': '0'}
    def show(current_service):
        value = dict(expected[current_service])
        if current_service == service:
            value.update(ActiveState='inactive', SubState='dead', Result='success')
            for index, (field, reset_value) in enumerate(cleared.items()):
                if mask & (1 << index):
                    value[field] = reset_value
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    monkeypatch.setattr(MODULE, 'empty', lambda service: None)
    if mask in (0, 63):
        MODULE.services(expected, reset={service})
    else:
        with pytest.raises(ValueError):
            MODULE.services(expected, reset={service})
    assert expected == original


def test_gc_alternative_never_applies_before_reset(monkeypatch):
    expected = service_values()
    def show(service):
        value = dict(expected[service])
        if service == MODULE.BROKER:
            value.update(ActiveState='inactive', SubState='dead', Result='success', InvocationID='',
                ExecMainStartTimestampMonotonic='0', ExecMainExitTimestampMonotonic='0', ExecMainCode='0', ExecMainStatus='0', ExecMainPID='0')
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    monkeypatch.setattr(MODULE, 'empty', lambda service: None)
    with pytest.raises(ValueError):
        MODULE.services(expected)


@pytest.mark.parametrize('fault', [None, 'current', 'marker', 'receipt', 'commission', 'bootstrap', 'supervisor', 'reset_' + MODULE.VOICE, 'reset_' + MODULE.BROKER])
def test_ordered_archive_retains_failure_and_never_retries(fault):
    events = []
    names = {MODULE.MARKER: 'marker', MODULE.RECEIPT: 'receipt', MODULE.COMMISSION: 'commission', MODULE.BOOTSTRAP: 'bootstrap'}
    def action(name):
        events.append(name)
        if fault == name:
            raise ValueError
    backend = SimpleNamespace(current=lambda: action('current'), archive_file=lambda path: action(names[path]),
        archive_supervisor=lambda: action('supervisor'), reset_service=lambda service: action('reset_' + service))
    if fault:
        with pytest.raises(ValueError):
            MODULE.transition(backend, events.append)
        assert events[-1] == fault and 'reset_' + MODULE.BROKER + '_done' not in events
    else:
        result = MODULE.transition(backend, events.append)
        assert [item for item in events if item in names.values() or item in ('supervisor', 'reset_' + MODULE.VOICE, 'reset_' + MODULE.BROKER)] == [
            'marker', 'receipt', 'commission', 'bootstrap', 'supervisor', 'reset_' + MODULE.VOICE, 'reset_' + MODULE.BROKER]
        assert result['historical_containment_verified'] is False and result['drain_claimed'] is False
        assert result['retry_authorized'] is False


@pytest.mark.parametrize('arguments', [['start', MODULE.BROKER], ['restart', MODULE.VOICE], ['stop', MODULE.MODEL],
                                      ['reset-failed', MODULE.MODEL], ['daemon-reload']])
def test_mutation_command_allowlist(arguments):
    with pytest.raises(ValueError):
        MODULE.command(arguments)


def test_archive_copy_is_verified_before_original_unlink(tmp_path, monkeypatch):
    marker = tmp_path / 'marker'
    marker.write_bytes(b'original')
    archive = tmp_path / 'archive'
    archive.mkdir()
    monkeypatch.setattr(MODULE, 'MARKER', marker)
    monkeypatch.setattr(MODULE, 'ATTEMPT', archive)
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.moved = set()
    backend.expected = {'files': {str(marker): {'mode': 0o644, 'gid': 7}}}
    events = []
    def file(path, actual=None):
        chosen = path if actual is None else actual
        assert chosen.read_bytes() == b'original'
        events.append('source' if actual is None else 'verify_archive')
        return chosen.read_bytes()
    backend.file = file
    backend.current = lambda: events.append('current')
    def write(path, raw, mode, gid):
        assert (mode, gid) == (0o644, 7)
        with path.open('xb') as stream:
            stream.write(raw)
        events.append('durable_copy')
    monkeypatch.setattr(MODULE, 'write', write)
    monkeypatch.setattr(MODULE, 'sync', lambda path: events.append('sync_original_parent'))
    backend.archive_file(marker)
    assert events == ['source', 'durable_copy', 'verify_archive', 'current', 'sync_original_parent']
    assert not marker.exists() and (archive / marker.name).read_bytes() == b'original'
    assert marker in backend.moved


def test_existing_archive_refuses_without_touching_source(tmp_path, monkeypatch):
    marker, archive = tmp_path / 'marker', tmp_path / 'archive'
    marker.write_bytes(b'original')
    archive.mkdir()
    (archive / marker.name).write_bytes(b'existing')
    monkeypatch.setattr(MODULE, 'MARKER', marker)
    monkeypatch.setattr(MODULE, 'ATTEMPT', archive)
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.moved = set()
    backend.file = lambda path: path.read_bytes()
    backend.expected = {'files': {str(marker): {'mode': 0o644, 'gid': 0}}}
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    with pytest.raises(FileExistsError):
        backend.archive_file(marker)
    assert marker.read_bytes() == b'original'


def test_duplicate_expected_fields_refuse():
    with pytest.raises(ValueError):
        MODULE.decode(b'{"boot_id":1,"boot_id":2}')


@pytest.mark.parametrize('fault', [None, 'wrong_uid', 'wrong_mode', 'link', 'other_parent', 'other_leaf'])
def test_service_owned_parent_exception_is_marker_only(monkeypatch, fault):
    def info(path):
        uid, mode = 0, 0o40755
        if path == MODULE.MARKER.parent:
            uid, mode = (995 if fault == 'wrong_uid' else 994), (0o40750 if fault == 'wrong_mode' else 0o40700)
            if fault == 'link':
                mode = 0o120777
        if fault == 'other_parent' and path == MODULE.MARKER.parent.parent:
            uid = 994
        return SimpleNamespace(st_uid=uid, st_mode=mode)
    monkeypatch.setattr(Path, 'lstat', info)
    path = MODULE.MARKER.parent / 'other' if fault == 'other_leaf' else MODULE.MARKER
    if fault:
        with pytest.raises(ValueError):
            MODULE.parents(path, marker_parent={'uid': 994, 'mode': 0o700})
    else:
        MODULE.parents(path, marker_parent={'uid': 994, 'mode': 0o700})
        with pytest.raises(ValueError):
            MODULE.parents(path)


def test_kernel_owner_flock_exclusion_and_release(tmp_path, monkeypatch):
    (tmp_path / 'owner.lock').touch(mode=0o600)
    monkeypatch.setattr(MODULE, 'SUPERVISOR', tmp_path)
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=994))
    monkeypatch.setattr(MODULE.Recovery, 'current', lambda self: None)
    monkeypatch.setattr(MODULE.Recovery, 'failure_evidence', lambda self: None)
    conversations = tmp_path / 'conversations-v1'
    conversations.mkdir()
    (conversations / 'owner.lock').touch(mode=0o600)
    monkeypatch.setattr(MODULE, 'CONVERSATIONS', conversations)
    expected = dict(schema='sage.lantern.constructor-recovery.v1', boot_id='boot', broker_id='a' * 32,
        files={str(path): {} for path in MODULE.FILES}, services={}, configuration={}, marker_parent={'uid': 994, 'mode': 0o700}, history={}, sources={}, runtime_sources={}, journal={})
    competing = os.open(tmp_path / 'owner.lock', os.O_RDONLY)
    try:
        fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            MODULE.Recovery(expected)
        fcntl.flock(competing, fcntl.LOCK_UN)
        recovery = MODULE.Recovery(expected)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            recovery.close()
        fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(competing)


def trace_rows():
    messages = ['Traceback (most recent call last):',
        '  File "/usr/local/libexec/sage-lantern-voice-loop", line 1238, in __init__',
        '    self.memory = ConversationService.from_config(config)',
        '  File "/opt/sage-lantern/venv/lib/python3.11/site-packages/sage_lantern/conversation_service.py", line 224, in from_config',
        '    fence = ProtectedConversationOwner(Path(config.conversation_dir))',
        'TypeError: expected str, bytes or os.PathLike object, not NoneType']
    return [dict(_BOOT_ID='a' * 32, _SYSTEMD_INVOCATION_ID=MODULE.FAILED[MODULE.VOICE][1],
                 _PID='536553', __MONOTONIC_TIMESTAMP='59842000000', MESSAGE=message) for message in messages]


@pytest.mark.parametrize('fault', [None, 'wrong_hash', 'prefix_hash', 'row_count', 'later_line',
                                  'other_error', 'second_trace', 'reordered'])
def test_exact_constructor_trace_not_generic_failed_process(fault):
    rows = trace_rows()
    if fault == 'later_line':
        rows[3]['MESSAGE'] = rows[3]['MESSAGE'].replace('224', '228')
    if fault == 'other_error':
        rows[-1]['MESSAGE'] = 'RuntimeError: unknown'
    if fault == 'second_trace':
        rows.append(dict(rows[0]))
    if fault == 'reordered':
        rows.reverse()
    pin = dict(sha256=MODULE.sha(MODULE.canonical(rows)), rows=len(rows))
    if fault == 'wrong_hash':
        pin['sha256'] = '0' * 64
    if fault == 'prefix_hash':
        pin['sha256'] = pin['sha256'][:5]
    if fault == 'row_count':
        pin['rows'] += 1
    if fault:
        with pytest.raises(ValueError):
            MODULE.verify_trace(rows, pin)
    else:
        MODULE.verify_trace(rows, pin)


@pytest.mark.parametrize('fault', [None, 'pid', 'boot', 'invocation', 'before', 'after', 'duplicate', 'failure'])
def test_journal_is_kernel_identity_and_monotonic_interval_bound(monkeypatch, fault):
    rows = trace_rows()
    if fault in ('pid', 'boot', 'invocation'):
        rows[0][{'pid': '_PID', 'boot': '_BOOT_ID', 'invocation': '_SYSTEMD_INVOCATION_ID'}[fault]] = 'wrong'
    if fault == 'before':
        rows[0]['__MONOTONIC_TIMESTAMP'] = '1'
    if fault == 'after':
        rows[0]['__MONOTONIC_TIMESTAMP'] = '999999999999'
    raw = b'\n'.join(MODULE.canonical(row) for row in rows)
    if fault == 'duplicate':
        raw = b'{"_PID":"536553","_PID":"other"}'
    calls = []
    def run(arguments, **keywords):
        calls.append(arguments)
        return SimpleNamespace(returncode=1 if fault == 'failure' else 0, stdout=raw)
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    expected = dict(boot_id='a' * 32, services=service_values())
    if fault:
        with pytest.raises(ValueError):
            MODULE.journal_rows(expected)
    else:
        assert MODULE.journal_rows(expected) == rows
    assert calls == [['/usr/bin/journalctl', '--no-pager', '--all', '-o', 'json', '_BOOT_ID=' + 'a' * 32,
                      '_SYSTEMD_INVOCATION_ID=' + MODULE.FAILED[MODULE.VOICE][1], '_PID=536553']]


@pytest.mark.parametrize('fault', [None, 'database', 'extra_root_file', 'owner_bytes', 'owner_link',
                                  'wrong_inode', 'wrong_uid', 'wrong_mode', 'held_owner'])
def test_later_operator_owner_check_preserved_not_erased(tmp_path, monkeypatch, fault):
    history = tmp_path / 'history'
    conversations = history / 'conversations-v1'
    history.mkdir(mode=0o700)
    conversations.mkdir(mode=0o700)
    owner = conversations / 'owner.lock'
    owner.touch(mode=0o600)
    monkeypatch.setattr(MODULE, 'HISTORY', history)
    monkeypatch.setattr(MODULE, 'CONVERSATIONS', conversations)
    monkeypatch.setattr(MODULE, 'parents', lambda *args, **kwargs: None)
    pins = {}
    for path in (history, conversations, owner):
        info = path.stat()
        pins[str(path)] = dict(uid=info.st_uid, gid=info.st_gid, mode=info.st_mode & 0o777,
                               dev=info.st_dev, ino=info.st_ino)
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.expected = dict(history=pins, marker_parent={'uid': os.geteuid(), 'mode': 0o700})
    backend.history_owner = None
    if fault == 'database':
        (conversations / 'history.db').touch()
    if fault == 'extra_root_file':
        (history / 'history.json').write_text('{}')
    if fault == 'owner_bytes':
        owner.write_bytes(b'not-empty')
    if fault == 'owner_link':
        owner.unlink()
        owner.symlink_to(tmp_path / 'elsewhere')
    if fault == 'wrong_inode':
        pins[str(owner)]['ino'] += 1
    if fault == 'wrong_uid':
        pins[str(owner)]['uid'] += 1
    if fault == 'wrong_mode':
        owner.chmod(0o644)
    descriptor = os.open(owner, os.O_RDONLY) if fault == 'held_owner' else None
    try:
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            backend.history_owner = os.open(owner, os.O_RDONLY)
        if fault:
            with pytest.raises((ValueError, BlockingIOError)):
                backend.history_preserved()
        else:
            backend.history_preserved()
            assert owner.read_bytes() == b'' and owner.stat().st_ino == pins[str(owner)]['ino']
    finally:
        if descriptor is not None:
            os.close(descriptor)
            os.close(backend.history_owner)


@pytest.mark.parametrize('fault', [None, 'prefix', 'changed_old', 'changed_fixed', 'missing_runtime',
                                  'extra_source', 'old_already_configured'])
def test_full_source_pins_and_old_missing_config_are_required(monkeypatch, fault):
    source = (Path(__file__).resolve().parents[1] / 'lantern/unit-a-voice-loop.py').read_bytes()
    bodies = {MODULE.SOURCES[0]: b'reviewed old source', MODULE.SOURCES[1]: b'[sage]\nprotected_storage=true\n',
              MODULE.SOURCES[2]: source, MODULE.SOURCES[3]: b'[sage]\nprotected_storage=true\nconversation_dir="/var/lib/sage-lantern/history/conversations-v1"\nidentity_path="/var/lib/sage-lantern/root.key"\n'}
    if fault == 'old_already_configured':
        bodies[MODULE.SOURCES[1]] += b'conversation_dir="/already/configured"\n'
    pins = {path: MODULE.sha(raw) for path, raw in bodies.items()}
    runtime = {'/opt/sage-lantern/venv/lib/python3.11/site-packages/sage_lantern/' + name: MODULE.sha(b'runtime')
               for name in MODULE.RUNTIME_NAMES}
    bodies.update({path: b'runtime' for path in runtime})
    if fault == 'prefix':
        pins[MODULE.SOURCES[0]] = pins[MODULE.SOURCES[0]][:5]
    if fault == 'changed_old':
        bodies[MODULE.SOURCES[0]] += b'changed'
    if fault == 'changed_fixed':
        bodies[MODULE.SOURCES[3]] += b'changed'
    if fault == 'missing_runtime':
        runtime.pop(next(iter(runtime)))
    if fault == 'extra_source':
        pins['/unknown'] = 'a' * 64
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.expected = dict(sources=pins, runtime_sources=runtime)
    monkeypatch.setattr(MODULE, 'read', lambda path: bodies[str(path)])
    if fault:
        with pytest.raises(ValueError):
            backend.source_evidence()
    else:
        backend.source_evidence()


@pytest.mark.parametrize('bad_pin', [False, True])
def test_default_readonly_never_creates_attempt_or_resets(tmp_path, monkeypatch, capsys, bad_pin):
    attempt = tmp_path / 'attempt'
    monkeypatch.setattr(MODULE, 'ATTEMPT', attempt)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    raw = b'{"reviewed":"fixture"}'
    monkeypatch.setattr(MODULE, 'read', lambda path: raw)
    calls = []
    def backend(expected):
        calls.append('observe')
        return SimpleNamespace(close=lambda: calls.append('close'))
    monkeypatch.setattr(MODULE, 'Recovery', backend)
    monkeypatch.setattr(MODULE, 'write', lambda *args: pytest.fail('readonly write'))
    monkeypatch.setattr(MODULE, 'transition', lambda *args: pytest.fail('readonly mutation'))
    arguments = ['--expected', '/expected.json', '--expected-sha256', '0' * 64 if bad_pin else MODULE.sha(raw)]
    if bad_pin:
        with pytest.raises(ValueError):
            MODULE.main(arguments)
        assert calls == []
    else:
        MODULE.main(arguments)
        assert calls == ['observe', 'close']
        output = json.loads(capsys.readouterr().out)
        assert output['mutation_authorized'] is False and output['drain_claimed'] is False
    assert not attempt.exists()


@pytest.mark.parametrize('path', MODULE.ABSENT)
def test_runtime_socket_start_or_capture_claim_blocks_before_archival(monkeypatch, path):
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.expected = {'boot_id': 'boot'}
    backend.source_evidence = lambda: None
    backend.history_preserved = lambda: None
    monkeypatch.setattr(Path, 'read_text', lambda self: 'boot')
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda item: item == path)
    with pytest.raises(ValueError):
        backend.current()


def test_existing_attempt_blocks_even_readonly_without_loading_input(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'ATTEMPT', tmp_path)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE, 'read', lambda *args: pytest.fail('existing attempt'))
    with pytest.raises(ValueError):
        MODULE.main(['--expected', '/expected.json', '--expected-sha256', 'a' * 64])
