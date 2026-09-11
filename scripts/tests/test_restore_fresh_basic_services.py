import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('basic_restore',
    Path(__file__).resolve().parents[1] / 'lantern/restore-fresh-basic-services.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


EXEC = ('{ path=/opt/python ; argv[]=/opt/python /usr/local/libexec/auth --idle-seconds 120 ; '
        'ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')


@pytest.mark.parametrize('failure', [None, 'install_voice_loop', 'install_status',
    'release_' + MODULE.AUTH, 'start_' + MODULE.AUTH,
    *['start_' + service for service in MODULE.BASIC]])
def test_transition_exact_order_no_voice_or_native_start(failure):
    events = []
    def action(name):
        events.append(name)
        if failure == name:
            raise ValueError('failed')
    backend = SimpleNamespace(current=lambda: action('current'),
        install=lambda key: action('install_' + key),
        release=lambda name, record: action('release_' + name), start=lambda name: action('start_' + name))
    if failure:
        with pytest.raises(ValueError):
            MODULE.transition(backend, events.append)
        assert events[-1] == failure
        assert failure + '_done' not in events
    else:
        result = MODULE.transition(backend, events.append)
        assert result['auth_locked'] is True and result['voice_activated'] is False
        assert events.index('install_status_done') < events.index('release_' + MODULE.AUTH)
        assert events.index('start_' + MODULE.AUTH + '_done') < events.index('release_' + MODULE.BASIC[0])
    assert not any('restart' in item or 'rollback' in item for item in events)
    assert not any(item.startswith('start_sage-lantern-' + name)
                   for item in events for name in ('voice-loop', 'mesh-runtime', 'llm', 'personal-sage'))


@pytest.mark.parametrize('fault', [None, 'missing_done', 'changed_pin', 'extra', 'backup', 'wheel'])
def test_completion_requires_entire_exact_runtime_journal(tmp_path, monkeypatch, fault):
    state = tmp_path / 'runtime'
    state.mkdir(mode=0o700)
    expected = {'old_wheel': {'sha256': 'old'}, 'new_wheel': {'sha256': 'new'}}
    raw = json.dumps(expected).encode()
    (state / 'expected.json').write_bytes(raw)
    (state / 'old.whl').write_bytes(b'old')
    phases = [phase + suffix for phase in ('inhibit_consumers', 'stop_consumers',
              'install_runtime', 'verify_runtime') for suffix in ('_intent', '_done')]
    for index, phase in enumerate(phases, 1):
        (state / f'{index:02d}-{phase}.json').write_text(json.dumps(dict(phase=phase,
            expected_sha256=raw.decode(), old_wheel_sha256='old', new_wheel_sha256='new')))
    if fault == 'missing_done':
        (state / '08-verify_runtime_done.json').unlink()
    if fault == 'changed_pin':
        (state / '08-verify_runtime_done.json').write_text('{}')
    if fault == 'extra':
        (state / 'unreviewed').touch()
    if fault == 'backup':
        (state / 'old.whl').write_bytes(b'changed')
    original = Path.lstat
    def root_info(path):
        info = original(path)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
    monkeypatch.setattr(Path, 'lstat', root_info)
    def verify(value):
        if fault == 'wheel':
            raise ValueError
        assert value == b'new'
    runtime = SimpleNamespace(STATE=state, BACKUP='old.whl', WHEEL_SHA='new',
        read=lambda path: path.read_bytes(), sha=lambda value: value.decode(),
        pinned=lambda value: b'new', verify_installed=verify)
    if fault:
        with pytest.raises((ValueError, FileNotFoundError)):
            MODULE.completion(runtime, raw)
    else:
        assert MODULE.completion(runtime, raw) == expected


@pytest.mark.parametrize('service', [MODULE.AUTH, *MODULE.BASIC])
def test_guard_is_exact_maintenance_only(service):
    backend = MODULE.Restore.__new__(MODULE.Restore)
    backend.runtime = SimpleNamespace(GUARD_NAME='zzzz-fresh-runtime-update.conf')
    path = backend.guard(service)
    assert path.parent == Path('/etc/systemd/system', service + '.d')
    assert path.name == ('zzzz-fresh-native-update.conf' if service == MODULE.AUTH
                         else 'zzzz-fresh-runtime-update.conf')


@pytest.mark.parametrize('service', ['sage-lantern-voice-loop.service', 'sage-lantern-personal-sage.service',
                                   'sage-lantern-mesh-runtime.service', 'sage-lantern-llm.service'])
def test_cannot_release_producer_or_native_guard(service):
    with pytest.raises(ValueError):
        MODULE.Restore.__new__(MODULE.Restore).guard(service)


@pytest.mark.parametrize('failure', [None, 'unlocked', 'foreign', 'refused_once', 'refused_forever'])
def test_auth_start_only_retries_unavailable_listener(monkeypatch, failure):
    backend = MODULE.Restore.__new__(MODULE.Restore)
    calls = []
    backend.released, backend.running, backend.ticks = {MODULE.AUTH}, {}, {}
    backend.observer = SimpleNamespace(deadline=100,
        system=SimpleNamespace(_command=lambda args, deadline: calls.append(args)))
    backend.show = lambda name: {'LoadState': 'loaded', 'ExecStart': EXEC, 'identity': 'same'}
    backend.process = lambda name, value: SimpleNamespace(start_ticks=123)
    attempts = []
    def locked():
        attempts.append(True)
        if failure in ('unlocked', 'foreign'):
            raise ValueError
        if failure == 'refused_forever' or failure == 'refused_once' and len(attempts) == 1:
            raise ConnectionRefusedError
    backend.locked = locked
    clock = iter(range(0, 100, 2))
    monkeypatch.setattr(MODULE.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(MODULE.time, 'sleep', lambda delay: None)
    if failure in ('unlocked', 'foreign', 'refused_forever'):
        with pytest.raises(ValueError):
            backend.start(MODULE.AUTH)
    else:
        backend.start(MODULE.AUTH)
    assert calls == [['start', MODULE.AUTH]]
    if failure in ('unlocked', 'foreign'):
        assert len(attempts) == 1


def test_duplicate_input_fields_reject():
    with pytest.raises(ValueError):
        MODULE.decode(b'{"schema":1,"schema":2}')


@pytest.mark.parametrize('fault', [None, 'unknown_dropin', 'missing_original', 'changed_guard',
                                 'active_cold', 'job', 'wrong_exec', 'released_guard_present'])
def test_current_preserves_exact_fences_and_empty_producers(monkeypatch, fault):
    backend = MODULE.Restore.__new__(MODULE.Restore)
    service = MODULE.AUTH
    original = '/etc/systemd/system/' + service + '.d/30-original.conf'
    guard = str(backend.guard(service))
    fields = dict(LoadState='loaded', ActiveState='inactive', MainPID='0', InvocationID='', Job='',
        ControlGroup='', Slice='system.slice', KillMode='control-group', User='sage-lantern',
        FragmentPath='/etc/systemd/system/' + service, DropInPaths=original + ' ' + guard,
        NeedDaemonReload='no', Restart='no', ExecStart=EXEC)
    current = dict(fields)
    backend.installed, backend.released, backend.running, backend.receipts = set(), set(), {}, {}
    backend.removed = set()
    backend.raw = b'expected'
    backend.prior_raw = b'expected'
    backend.native = lambda: None
    backend.expected = dict(runtime_updater={}, runtime_expected={}, prior_runtime_updater={},
        prior_runtime_expected={}, services={service: fields},
        files={original: 'original', fields['FragmentPath']: 'fragment'},
        old_helpers={key: {'sha256': key} for key in MODULE.TARGETS})
    data = {original: b'original', fields['FragmentPath']: b'fragment', guard: MODULE.NATIVE_GUARD}
    data.update({path: key.encode() for key, path in MODULE.TARGETS.items()})
    if fault == 'unknown_dropin':
        current['DropInPaths'] += ' /unknown.conf'
    if fault == 'missing_original':
        current['DropInPaths'] = guard
    if fault == 'changed_guard':
        data[guard] += b'\nConditionPathExists=\n'
    if fault == 'active_cold':
        current['ActiveState'], current['MainPID'] = 'active', '123'
    if fault == 'job':
        current['Job'] = 'queued'
    if fault == 'wrong_exec':
        current['ExecStart'] = EXEC.replace('--idle-seconds 120', '--idle-seconds 121')
    if fault == 'released_guard_present':
        backend.released.add(service)
        backend.removed.add(Path(guard))
        current['DropInPaths'] = original
    backend.runtime = SimpleNamespace(read=lambda path: data[str(path)], sha=lambda raw: raw.decode())
    backend.prior_runtime = backend.runtime
    backend.preflight = SimpleNamespace(SAGE='sage', FIELDS=())
    empty = []
    backend.observer = SimpleNamespace(deadline=10, system=SimpleNamespace(
        _guard=lambda *args: None, _empty_tree=lambda *args: empty.append(True), _cgroup=lambda path: path))
    backend.show = lambda name: dict(current)
    monkeypatch.setattr(MODULE, 'load', lambda item: backend.raw)
    monkeypatch.setattr(MODULE, 'completion', lambda *args, **kwargs: None)
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: True)
    if fault:
        with pytest.raises(ValueError):
            backend.current()
    else:
        backend.current()
        assert empty == [True]


def test_release_cannot_precede_helper_install_or_auth(monkeypatch):
    backend = MODULE.Restore.__new__(MODULE.Restore)
    backend.installed, backend.released, backend.running = set(), set(), {}
    with pytest.raises(ValueError):
        backend.release(MODULE.AUTH, lambda phase: None)
    backend.installed = set(MODULE.TARGETS)
    with pytest.raises(ValueError):
        backend.release(MODULE.BASIC[0], lambda phase: None)


@pytest.mark.parametrize('service', MODULE.BASIC)
def test_r7_release_removes_both_exact_guards_with_individual_journal(monkeypatch, service):
    backend = MODULE.Restore.__new__(MODULE.Restore)
    backend.installed, backend.released, backend.running = set(MODULE.TARGETS), {MODULE.AUTH}, {MODULE.AUTH: {}}
    backend.removed = set()
    backend.prior_runtime = SimpleNamespace(STATE=Path('/var/lib/sage-lantern-cold-runtime-update'),
        GUARD_NAME='zzzz-fresh-runtime-update.conf', GUARD=b'r6')
    events = []
    backend.runtime = SimpleNamespace(STATE=Path('/var/lib/sage-lantern-cold-runtime-update-r7'),
        GUARD_NAME='zzzz-fresh-runtime-r7-update.conf', GUARD=b'r7',
        sync=lambda path: events.append('sync'))
    paths = {path: raw for path, raw, state in backend.guards(service)}
    backend.runtime.read = lambda path: paths[path]
    backend.current = lambda: events.append('current')
    backend.observer = SimpleNamespace(deadline=1, system=SimpleNamespace(
        _command=lambda args, deadline: events.append(args)))
    monkeypatch.setattr(Path, 'unlink', lambda path: events.append(path))
    backend.release(service, events.append)
    assert backend.removed == set(paths)
    assert len(paths) == 2 and service in backend.released
    assert [event for event in events if isinstance(event, Path)] == list(paths)
    for path in paths:
        prefix = 'remove_' + service + '_' + path.name
        assert events.index(prefix + '_intent') < events.index(path) < events.index(prefix + '_done')
    assert events.count(['daemon-reload']) == 2


@pytest.mark.parametrize('fault', [None, 'old_wheel', 'new_wheel', 'supersession', 'later_helpers', 'consumers'])
def test_only_explicit_r7_after_r6_lineage(fault):
    prior = dict(unit_id='unit-a', new_wheel={'sha256': 'r6'}, supersession={'proof': 'same'},
                 later_helpers={'pins': 'same'})
    final = dict(prior, old_wheel={'sha256': 'r6'}, new_wheel={'sha256': MODULE.R7_WHEEL_SHA},
                 consumers={name: None for name in MODULE.BASIC})
    if fault:
        final[fault] = {'sha256': 'changed'} if fault.endswith('wheel') else {}
        with pytest.raises(ValueError):
            MODULE.lineage(prior, final, MODULE.BASIC)
    else:
        MODULE.lineage(prior, final, MODULE.BASIC)


def test_exec_dynamic_fields_do_not_change_static_snapshot():
    before = {'LoadState': 'loaded', 'ExecStart': EXEC, 'MainPID': '100', 'InvocationID': 'same'}
    after = dict(before, ExecStart=EXEC.replace('start_time=[n/a]', 'start_time=[Sun 2026-09-06 12:00:00 UTC]')
                 .replace('stop_time=[n/a]', 'stop_time=[Sun 2026-09-06 12:01:00 UTC]')
                 .replace('pid=0', 'pid=99').replace('code=(null)', 'code=exited').replace('status=0/0', 'status=1/FAILURE'))
    assert MODULE.snapshot(before, MODULE.AUTH) == MODULE.snapshot(after, MODULE.AUTH)
    for field in ('MainPID', 'InvocationID'):
        assert MODULE.snapshot(before, MODULE.AUTH) != MODULE.snapshot(dict(after, **{field: 'changed'}), MODULE.AUTH)


@pytest.mark.parametrize('raw', ['', EXEC + ' ' + EXEC, EXEC.replace(' ; pid=0', ' ; pid=0 ; pid=1'),
    EXEC.replace('ignore_errors=no', 'ignore_errors=maybe'), EXEC.replace('path=/opt/python', 'path=relative'),
    EXEC.replace('argv[]=/opt/python', 'argv[]=/different'), EXEC.replace('--idle-seconds', '"--idle-seconds"')])
def test_exec_malformed_multiple_or_unsupported_definitions_refuse(raw):
    with pytest.raises(ValueError):
        MODULE.exec_definition(raw)


@pytest.mark.parametrize('field,replacement', [('path=/opt/python ; argv[]=/opt/python',
    'path=/opt/other ; argv[]=/opt/other'), ('--idle-seconds 120', '--idle-seconds 121'),
    ('ignore_errors=no', 'ignore_errors=yes')])
def test_exec_static_changes_remain_detectable(field, replacement):
    assert MODULE.exec_definition(EXEC) != MODULE.exec_definition(EXEC.replace(field, replacement))


@pytest.mark.parametrize('service,load,omit,accepted', [
    ('sage-lantern-idle-suspend.service', 'not-found', True, True),
    ('sage-lantern-idle-suspend.service', 'not-found', False, True),
    ('sage-lantern-idle-suspend.service', 'loaded', True, False),
    (MODULE.AUTH, 'not-found', True, False), (MODULE.AUTH, 'loaded', True, False),
    (MODULE.AUTH, 'loaded', False, True)])
def test_show_omitted_exec_only_for_absent_idle(service, load, omit, accepted):
    backend = MODULE.Restore.__new__(MODULE.Restore)
    backend.preflight = SimpleNamespace(FIELDS=('LoadState',))
    value = dict(LoadState=load, Restart='no')
    if not omit:
        value['ExecStart'] = '' if load == 'not-found' else EXEC
    calls = []
    def command(args, deadline):
        calls.append(args)
        return '\n'.join(key + '=' + content for key, content in value.items())
    backend.observer = SimpleNamespace(deadline=10, system=SimpleNamespace(_command=command))
    if accepted:
        assert backend.show(service) == value
    else:
        with pytest.raises(ValueError):
            backend.show(service)
    assert calls == [['show', '--no-pager', '--all', '-p', 'LoadState', '-p', 'Restart', '-p', 'ExecStart', service]]
