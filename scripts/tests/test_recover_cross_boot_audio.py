import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4, uuid5

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/runtime/src'))
from sage_lantern import conversation_records as records
from sage_lantern.mesh_receiver import _validate as validate_receiver

SPEC = importlib.util.spec_from_file_location('cross_boot', ROOT / 'scripts/lantern/recover-cross-boot-audio.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ACTOR = dict(chain_id='private', agent_id='a' * 64)
SLOT = str(uuid4())


def row(kind, payload, identifier):
    return dict(schema='sage.workflow-journal.v1', agent_id=ACTOR['agent_id'], record_id=identifier,
                revision=1, kind=kind, payload=payload, trust='untrusted_auxiliary')


def control():
    payload = dict(schema=records.CONTROL_SCHEMA, binding=ACTOR, enabled=True, generation=str(uuid4()),
                   history_generation=None, dispatch_epoch=str(uuid4()), clear_epoch=str(uuid4()), suppressed=[])
    return row('conversation_control', payload, records.record_id(ACTOR))


def inventory(rows):
    return MODULE.settled_inventory(rows, ACTOR, records, SLOT, validate_receiver)


def session(state='accepted'):
    identifier = str(uuid4())
    item = dict(session_id=identifier, created_at='2026-09-07T10:00:00Z', question='synthetic question',
                answer='synthetic answer', has_photo=False)
    operation = records.new_operation()
    operation.update(state=state, memory_id='synthetic-memory', owner=str(uuid4()))
    payload = dict(schema=records.SESSION_SCHEMA, binding=ACTOR, session_id=identifier,
        item=item, item_hash=records.checksum(item), consent_generation=str(uuid4()), history_generation=None,
        dispatch_epoch=str(uuid4()), clear_epoch=str(uuid4()), operations={'interaction': operation}, media=None)
    return row('conversation_session', payload, records.record_id(ACTOR, identifier))


@pytest.mark.parametrize('state', ['idle', 'interrupted'])
def test_actual_shape_control_and_idle_receiver_is_supported_without_readiness_claim(state):
    rows = [control(), row('mesh_inbound', dict(schema='sage.lantern.mesh-receiver.v1', state=state,
                                            operation_id=str(uuid4()) if state == 'interrupted' else None,
                                            raw_frame=None), SLOT)]
    before = copy.deepcopy(rows)
    result = inventory(rows)
    assert result == dict(sha256=MODULE.sha(MODULE.canonical(rows)), records=2, historical_outcomes='unknown')
    assert rows == before


@pytest.mark.parametrize('state', ['accepted', 'recall_ready', 'cancelled'])
def test_settled_conversation_preserved(state):
    rows = [control(), session(state)]
    before = copy.deepcopy(rows)
    assert inventory(rows)['records'] == 2
    assert rows == before


@pytest.mark.parametrize('state', ['queued', 'dispatching', 'unknown'])
def test_pending_conversation_is_not_replayed(state):
    rows = [control(), session(state)]
    before = copy.deepcopy(rows)
    with pytest.raises((ValueError, RuntimeError)):
        inventory(rows)
    assert rows == before


@pytest.mark.parametrize('kind', ['mesh_outbound', 'public_proposal', 'unknown'])
def test_unsupported_rows_block(kind):
    with pytest.raises(ValueError):
        inventory([control(), row(kind, {}, str(uuid4()))])


@pytest.mark.parametrize('fault', ['wrong_slot', 'receiving', 'extra_field', 'unknown_schema'])
def test_receiver_requires_exact_idle_configured_slot(fault):
    receiver = row('mesh_inbound', dict(schema='sage.lantern.mesh-receiver.v1', state='idle',
                                      operation_id=None, raw_frame=None), SLOT)
    if fault == 'wrong_slot':
        receiver['record_id'] = str(uuid4())
    elif fault == 'receiving':
        receiver['payload'].update(state='receiving', operation_id=str(uuid4()))
    elif fault == 'extra_field':
        receiver['payload']['unexpected'] = True
    else:
        receiver['payload']['schema'] = 'unknown'
    with pytest.raises((ValueError, RuntimeError)):
        inventory([control(), receiver])


@pytest.mark.parametrize('fault', ['no_control', 'duplicate', 'wrong_actor', 'bad_hash', 'deprecation_unknown'])
def test_invalid_inventory_refused(fault):
    rows = [control(), session()]
    if fault == 'no_control':
        rows.pop(0)
    elif fault == 'duplicate':
        rows.append(copy.deepcopy(rows[0]))
    elif fault == 'wrong_actor':
        rows[1]['agent_id'] = 'b' * 64
    elif fault == 'bad_hash':
        rows[1]['payload']['item']['answer'] = 'changed'
    else:
        rows[1]['payload']['operations']['interaction']['deprecation_status'] = 'unknown'
    with pytest.raises((ValueError, RuntimeError)):
        inventory(rows)


class Backend:
    def __init__(self, fault=None):
        self.events = []
        self.fault = fault
        self.owner = SimpleNamespace(fresh=lambda: self.action('fresh'), start=self.start)
    def action(self, name):
        self.events.append(name)
        if self.fault == name:
            raise RuntimeError('synthetic failure')
    def __getattr__(self, name):
        return lambda: self.action(name)
    def start(self):
        self.action('broker_start')
        return {'broker_active': True, 'voice_active': True}
    def completed(self):
        self.action('durable_new_lifetime')
        return {'boot_id': 'synthetic boot'}
    def record(self, name, details=None):
        self.events.append(name)


def test_order_inhibition_archive_model_release_broker_owned_start():
    backend = Backend()
    result = MODULE.transition(backend, backend.record)
    events = backend.events
    assert events.index('inhibit_done') < events.index('archive_intent') < events.index('model_intent')
    assert events.index('release_done') < events.index('bootstrap_intent') < events.index('broker_start')
    assert events.index('durable_new_lifetime') < events.index('bootstrap_done')
    assert result['old_drain_verified'] is False and result['historical_outcomes'] == 'unknown'
    assert result['automatic_boot_enabled'] is False
    assert not any(name in events for name in ('stop_voice', 'start_voice', 'reset_failed', 'rollback', 'replay'))


@pytest.mark.parametrize('failure', ['reconcile', 'inhibit', 'archive', 'model', 'current',
                                   'release', 'fresh', 'broker_start', 'durable_new_lifetime'])
def test_each_failure_retains_guard_and_never_retries(failure):
    backend = Backend(failure)
    with pytest.raises(RuntimeError):
        MODULE.transition(backend, backend.record)
    assert backend.events[-3:] == ['retain_failure_guards_intent', 'retain', 'retain_failure_guards_done']
    assert backend.events.count('broker_start') <= 1
    assert 'bootstrap_done' not in backend.events


def test_no_old_boot_proof_or_historical_success_assumption_in_recovery_contract():
    assert 'prior_bootstrap' not in MODULE.Recovery.__init__.__code__.co_consts
    with pytest.raises(SystemExit):
        MODULE.main(['--enable'])


def test_exclusive_journal_preserves_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    path = tmp_path / 'intent.json'
    MODULE.write(path, b'original')
    with pytest.raises(FileExistsError):
        MODULE.write(path, b'replacement')
    assert path.read_bytes() == b'original'
    assert path.stat().st_mode & 0o777 == 0o600


def test_model_health_is_not_used_as_historical_work_receipt():
    backend = Backend()
    result = MODULE.transition(backend, backend.record)
    assert result['historical_outcomes'] == 'unknown'


def test_signed_scan_requires_access_before_loading_identity(monkeypatch):
    import sage_lantern.device_access as access
    import sage_lantern.mesh_daemon as daemon
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.current = lambda: None
    monkeypatch.setattr(access, 'access_epoch', lambda: None)
    monkeypatch.setattr(daemon, 'load_identity', lambda config: pytest.fail('identity read while locked'))
    with pytest.raises(ValueError):
        backend.reconcile()


@pytest.mark.parametrize('change', [None, 'pid', 'invocation', 'failed'])
def test_model_waits_for_same_lifetime_only(monkeypatch, change):
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.current = lambda: None
    commands = []
    backend.command = commands.append
    values = [dict(ActiveState='active', MainPID='12', InvocationID='a' * 32, Job='')]
    second = dict(values[0])
    if change == 'pid':
        second['MainPID'] = '13'
    elif change == 'invocation':
        second['InvocationID'] = 'b' * 32
    elif change == 'failed':
        second['ActiveState'] = 'failed'
    values.append(second)
    observations = iter(values)
    backend.system = SimpleNamespace(clock=lambda: 1, _show=lambda *args: next(observations))
    readiness = iter([None, dict(invocation='a' * 32)])
    backend.model_ready = lambda: next(readiness)
    monkeypatch.setattr(MODULE.time, 'sleep', lambda duration: None)
    if change:
        with pytest.raises(ValueError):
            backend.model()
    else:
        backend.model()
        assert backend.model_started is True
    assert commands == [['start', '--no-block', MODULE.MODEL]]


@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    directory = tmp_path / 'supervisor'
    directory.mkdir(mode=0o700)
    (directory / 'serial').mkdir(mode=0o700)
    for name, raw in (('owner.lock', b''), ('serial/mesh-123.lock', b''),
                      ('fence.json', b'{"schema":"sage.lantern.storage-supervisor.v1","broker_id":"' + b'a' * 32 + b'","phase":"active"}')):
        (directory / name).write_bytes(raw)
        (directory / name).chmod(0o600)
    original_lstat = Path.lstat
    original_fstat = os.fstat
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(
        **(MODULE.metadata(original_lstat(path)) | {'st_uid': 0})))
    monkeypatch.setattr(os, 'fstat', lambda descriptor: SimpleNamespace(
        **(MODULE.metadata(original_fstat(descriptor)) | {'st_uid': 0})))
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    monkeypatch.setattr(Path, 'is_mount', lambda path: False)
    return directory


def test_tree_is_metadata_hash_only(supervisor):
    result = MODULE.tree(supervisor)
    assert set(result) == {'.', 'serial', 'owner.lock', 'fence.json', 'serial/mesh-123.lock'}
    assert 'phase' not in json.dumps(result)
    assert result['owner.lock']['sha256'] == MODULE.sha(b'')


def test_canonical_startup_lock_is_inventoried_and_locked(supervisor):
    path = supervisor / 'serial/mesh-42496.startup.lock'
    path.write_bytes(b'')
    path.chmod(0o600)
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.current = lambda: None
    backend.location = lambda: supervisor
    backend.expected = {'tree': MODULE.tree(supervisor)}
    backend.locks = []
    try:
        backend.lock()
        assert len(backend.locks) == 3
        assert 'serial/mesh-42496.startup.lock' in backend.expected['tree']
        descriptor = os.open(path, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                MODULE.fcntl.flock(descriptor, MODULE.fcntl.LOCK_EX | MODULE.fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
    finally:
        backend.close()


@pytest.mark.parametrize('fault', ['extra_file', 'nonempty_lock', 'link', 'mount', 'mode', 'hardlink'])
def test_tree_rejects_unreviewed_nodes(supervisor, monkeypatch, fault):
    if fault == 'extra_file':
        (supervisor / 'unexpected').write_bytes(b'preserve')
    elif fault == 'nonempty_lock':
        (supervisor / 'owner.lock').write_bytes(b'unknown')
    elif fault == 'link':
        (supervisor / 'owner.lock').unlink()
        (supervisor / 'owner.lock').symlink_to(supervisor / 'fence.json')
    elif fault == 'mount':
        monkeypatch.setattr(Path, 'is_mount', lambda path: path.name == 'serial')
    elif fault == 'mode':
        (supervisor / 'serial').chmod(0o755)
    else:
        os.link(supervisor / 'owner.lock', supervisor / 'serial/mesh-234.lock')
    with pytest.raises(ValueError):
        MODULE.tree(supervisor)


def test_released_files_each_reload_before_next_validation(tmp_path, monkeypatch):
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.guards = {MODULE.BROKER, MODULE.VOICE}
    backend.guard = lambda: b'exact guard'
    backend.guard_path = lambda service: tmp_path / service
    for service in backend.guards:
        backend.guard_path(service).write_bytes(backend.guard())
    loaded = set(backend.guards)
    def current():
        assert loaded == backend.guards
    backend.current = current
    def command(argv):
        assert argv == ['daemon-reload']
        loaded.clear()
        loaded.update(backend.guards)
    backend.command = command
    backend.audio = SimpleNamespace(admission_verified=lambda: True)
    backend.owner = SimpleNamespace(fresh=lambda: True)
    monkeypatch.setattr(MODULE, 'public', lambda path: path.read_bytes())
    backend.release()
    assert backend.guards == set() and list(tmp_path.iterdir()) == []


def test_default_inspection_never_creates_attempt_or_starts(monkeypatch, capsys):
    events = []
    backend = SimpleNamespace(lock=lambda: events.append('lock'),
        reconcile=lambda: {'historical_outcomes': 'unknown'}, close=lambda: events.append('close'))
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.sys, 'prefix', '/opt/sage-lantern/venv')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    monkeypatch.setattr(MODULE, 'pinned', lambda pin: b'{}')
    monkeypatch.setattr(MODULE, 'Recovery', lambda expected: backend)
    monkeypatch.setattr(MODULE, 'write', lambda *args: pytest.fail('write in inspection'))
    monkeypatch.setattr(MODULE, 'transition', lambda *args: pytest.fail('transition in inspection'))
    MODULE.main(['--expected', '/public/expected.json', '--expected-sha256', 'a' * 64])
    assert events == ['lock', 'close']
    assert json.loads(capsys.readouterr().out)['mutation_authorized'] is False


def test_disk_failure_does_not_skip_guard_restoration():
    backend = Backend()
    def broken_record(*args):
        raise OSError('disk full')
    with pytest.raises(OSError):
        MODULE.transition(backend, broken_record)
    assert 'retain' in backend.events


def test_suppressed_exception_context_preserved_without_private_messages_or_locals(capsys):
    backend = Backend()
    def unavailable():
        private_question = 'PRIVATE_QUESTION_NOT_TO_LOG'
        try:
            raise TimeoutError(private_question)
        except TimeoutError:
            raise RuntimeError('PRIVATE_RESPONSE_NOT_TO_LOG') from None
    backend.reconcile = unavailable
    with pytest.raises(RuntimeError):
        MODULE.transition(backend, backend.record)
    raw = capsys.readouterr().err
    assert 'PRIVATE_' not in raw
    outcome = json.loads(raw)
    assert [value['error_type'] for value in outcome['failure_frames']] == ['RuntimeError', 'TimeoutError']
    assert all(set(frame) == {'file', 'line', 'function'}
               for error in outcome['failure_frames'] for frame in error['frames'])
    assert 'retain' in backend.events


def test_failed_diagnostics_never_skip_cleanup(monkeypatch):
    backend = Backend('reconcile')
    def broken(error):
        raise MemoryError()
    monkeypatch.setattr(MODULE, 'failure_frames', broken)
    with pytest.raises(RuntimeError):
        MODULE.transition(backend, backend.record)
    assert 'retain' in backend.events


@pytest.mark.parametrize('fault', ['durable_new_lifetime', 'bootstrap_done'])
@pytest.mark.parametrize('contained', [True, False])
def test_failure_after_start_runs_bounded_containment(fault, contained, capsys):
    backend = Backend('durable_new_lifetime' if fault == 'durable_new_lifetime' else None)
    backend.owner.contain = lambda: backend.events.append('contain') or contained
    def record(phase, details=None):
        backend.record(phase, details)
        if phase == fault:
            raise OSError('disk full')
    with pytest.raises((RuntimeError, OSError)):
        MODULE.transition(backend, record)
    assert backend.events.index('retain') < backend.events.index('contain')
    outcome = json.loads(capsys.readouterr().err)
    assert outcome['containment_verified'] is contained
    assert outcome['producer_outcome'] == ('contained' if contained else 'active_or_unknown')


@pytest.mark.parametrize('exception', [InterruptedError, KeyboardInterrupt])
def test_interrupt_runs_cleanup_without_retry(exception):
    backend = Backend()
    def interrupted():
        raise exception()
    backend.owner.start = interrupted
    backend.owner.contain = lambda: backend.events.append('contain') or True
    with pytest.raises(exception):
        MODULE.transition(backend, backend.record)
    assert 'retain' in backend.events and 'contain' in backend.events


@pytest.fixture
def current_backend(supervisor, monkeypatch):
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.boot = str(uuid4())
    backend.state = supervisor.parent / 'attempt'
    backend.state.mkdir(mode=0o700)
    backend.archived = False
    backend.guards = set()
    backend.locks = []
    backend.model_started = False
    source = supervisor.parent / 'source'
    source.write_bytes(b'pinned source')
    backend.file_pins = {str(source): MODULE.metadata(source.lstat())}
    monkeypatch.setattr(MODULE, 'SUPERVISOR', supervisor)
    systemd = supervisor.parent / 'systemd'
    systemd.mkdir()
    monkeypatch.setattr(MODULE, 'SYSTEMD', systemd)
    for service in (MODULE.BROKER, MODULE.VOICE):
        (systemd / (service + '.d')).mkdir()
    receipt = {'synthetic': True}
    backend.audio = SimpleNamespace(commissioned_receipt=lambda: receipt, admission_verified=lambda: True)
    backend.expected = dict(inventory={}, tree=MODULE.tree(supervisor),
        commission_sha256=MODULE.sha(MODULE.canonical(receipt)), files={str(systemd / MODULE.MODEL): 'hash'})
    original_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda path, *args, **kwargs:
        backend.boot if str(path) == '/proc/sys/kernel/random/boot_id' else original_read(path, *args, **kwargs))
    loaded = {service: set() for service in (MODULE.BROKER, MODULE.VOICE)}
    def show(service, deadline, fields):
        values = dict(LoadState='loaded', NeedDaemonReload='no', FragmentPath=str(systemd / service),
            DropInPaths=' '.join(sorted(loaded.get(service, set()))), UnitFileState='disabled',
            Restart='no', ExecMainStartTimestampMonotonic='0', NRestarts='0')
        return {key: values[key] for key in fields}
    def command(arguments):
        assert arguments == ['daemon-reload']
        for service in loaded:
            loaded[service] = {str(backend.guard_path(service))} if backend.guard_path(service).exists() else set()
    backend.command = command
    backend.system = SimpleNamespace(clock=lambda: 0, _show=show,
        verify_sleep_idle=lambda: SimpleNamespace(confirmed=True), _guard=lambda *args: None)
    backend.owner = SimpleNamespace(inactive=lambda service: None, fresh=lambda: None)
    return backend, source


def test_real_archive_then_real_current_and_release(current_backend):
    backend, source = current_backend
    backend.current()
    backend.lock()
    try:
        backend.inhibit()
        backend.current()
        previous = backend.expected['tree']
        backend.archive()
        assert not MODULE.SUPERVISOR.exists()
        assert MODULE.tree(backend.location())['.']['metadata']['st_ino'] == previous['.']['metadata']['st_ino']
        backend.current()
        backend.release()
        backend.current()
        assert backend.guards == set()
        assert (backend.location() / 'fence.json').read_bytes()
    finally:
        backend.close()


def test_real_current_rejects_source_drift(current_backend):
    backend, source = current_backend
    source.write_bytes(b'changed source')
    with pytest.raises(ValueError):
        backend.current()


def test_archival_ctime_exception_does_not_cover_file_changes(current_backend):
    backend, source = current_backend
    backend.lock()
    try:
        backend.inhibit()
        backend.archive()
        backend.current()
        path = backend.location() / 'owner.lock'
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        with pytest.raises(ValueError):
            backend.current()
    finally:
        backend.close()


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    target = tmp_path / 'aplay'
    target.write_bytes(b'synthetic ALSA executable')
    target.chmod(0o755)
    link = tmp_path / 'arecord'
    link.symlink_to('aplay')
    mapping = {'/usr/bin/arecord': link, '/usr/bin/aplay': target}
    original_lstat, original_open = Path.lstat, os.open
    original_fstat, original_readlink = os.fstat, os.readlink
    def mapped(path):
        return mapping.get(str(path), path)
    def owned(info):
        return SimpleNamespace(**(MODULE.metadata(info) | {'st_uid': 0}))
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    monkeypatch.setattr(Path, 'lstat', lambda path: owned(original_lstat(mapped(path))))
    monkeypatch.setattr(os, 'open', lambda path, *args, **kwargs: original_open(mapped(path), *args, **kwargs))
    monkeypatch.setattr(os, 'fstat', lambda descriptor: owned(original_fstat(descriptor)))
    monkeypatch.setattr(os, 'readlink', lambda path: original_readlink(mapped(path)))
    return link, target


@pytest.mark.parametrize('destination', ['aplay', '/usr/bin/aplay'])
def test_exact_arecord_alias_hashes_target_and_pins_both(recorder, destination):
    link, target = recorder
    link.unlink()
    link.symlink_to(destination)
    binding = MODULE.recorder_binding()
    assert binding['link'] == destination and binding['target_path'] == '/usr/bin/aplay'
    assert binding['requested']['st_ino'] != binding['target']['st_ino']
    assert MODULE.file_hash('/usr/bin/arecord') == MODULE.sha(target.read_bytes())


@pytest.mark.parametrize('fault', ['foreign_link', 'chained_target', 'writable_target', 'extra_hardlinks'])
def test_recorder_rejects_unreviewed_variants(recorder, fault):
    link, target = recorder
    if fault == 'foreign_link':
        link.unlink()
        link.symlink_to('/unreviewed/executable')
    elif fault == 'chained_target':
        target.unlink()
        target.symlink_to('other-executable')
    elif fault == 'writable_target':
        target.chmod(0o777)
    else:
        os.link(target, target.parent / 'copy1')
        os.link(target, target.parent / 'copy2')
    with pytest.raises(ValueError):
        MODULE.file_hash('/usr/bin/arecord')


def test_two_hardlinks_allowed_only_through_recorder_contract(recorder):
    link, target = recorder
    os.link(target, target.parent / 'second-name')
    assert MODULE.file_hash('/usr/bin/arecord') == MODULE.sha(target.read_bytes())
    with pytest.raises(ValueError):
        MODULE.file_hash('/usr/bin/aplay')


def test_config_symlinks_still_rejected(recorder):
    link, target = recorder
    with pytest.raises(OSError):
        MODULE.file_hash(link)


def test_same_bytes_replaced_target_invalidates_recorder_pin(recorder):
    link, target = recorder
    before = MODULE.recorder_binding()
    replacement = target.parent / 'replacement'
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(0o755)
    os.replace(replacement, target)
    assert MODULE.recorder_binding() != before


def test_recorder_target_drift_rejected_by_current(current_backend, monkeypatch):
    backend, source = current_backend
    backend.recorder_pin = {'target': 'pinned'}
    monkeypatch.setattr(MODULE, 'recorder_binding', lambda: {'target': 'replaced'})
    with pytest.raises(ValueError):
        backend.current()


def test_cleanup_cancels_execution_alarm_and_gets_fresh_budget(monkeypatch):
    alarms = []
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda seconds: alarms.append(seconds))
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    backend = Backend('archive')
    with pytest.raises(RuntimeError):
        MODULE.transition(backend, backend.record)
    assert alarms == [0, MODULE.CLEANUP_SECONDS, 0]
    assert 'retain' in backend.events


def test_expired_cleanup_does_not_begin_another_containment_budget(monkeypatch, capsys):
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    now = [0]
    monkeypatch.setattr(MODULE.time, 'monotonic', lambda: now[0])
    backend = Backend('durable_new_lifetime')
    def retain():
        now[0] = MODULE.CLEANUP_SECONDS + 1
        raise TimeoutError()
    backend.retain = retain
    backend.owner.contain = lambda: pytest.fail('new action after cleanup deadline')
    with pytest.raises(RuntimeError):
        MODULE.transition(backend, backend.record)
    result = json.loads(capsys.readouterr().err)
    assert result['guards_retained'] is False
    assert result['containment_verified'] is False and result['producer_outcome'] == 'active_or_unknown'


@pytest.mark.parametrize('fault', [None, 'missing_dropin', 'foreign_dropin', 'restart', 'reload', 'fragment', 'condition'])
def test_retain_requires_effective_guard_not_only_file_write(current_backend, fault):
    backend, source = current_backend
    original = backend.system._show
    def show(service, deadline, fields):
        value = original(service, deadline, fields)
        if fault == 'missing_dropin':
            value['DropInPaths'] = ''
        elif fault == 'foreign_dropin':
            value['DropInPaths'] += ' /unreviewed/override.conf'
        elif fault == 'restart':
            value['Restart'] = 'always'
        elif fault == 'reload':
            value['NeedDaemonReload'] = 'yes'
        elif fault == 'fragment':
            value['FragmentPath'] = '/unreviewed/unit'
        return value
    backend.system._show = show
    checked = []
    def guard(service, deadline, state):
        checked.append(service)
        if fault == 'condition':
            raise ValueError('missing effective condition')
    backend.system._guard = guard
    if fault:
        with pytest.raises(ValueError):
            backend.retain()
    else:
        backend.retain()
        assert checked == [MODULE.BROKER, MODULE.VOICE]


def receive_audit(**overrides):
    """Exactly the record sage_lantern.mesh_receive_recovery writes for this slot."""
    operation = str(uuid4())
    payload = dict(schema=MODULE.RECEIVE_AUDIT_SCHEMA, slot_id=SLOT, operation_id=operation,
                   original_revision=1, original_sha256='b' * 64, recovered_at=1700000000,
                   reason='synthetic interrupted capture', outcome=MODULE.RECEIVE_AUDIT_OUTCOME,
                   replayed=False, delivery_claim=False)
    payload.update(overrides)
    identifier = str(uuid5(MODULE.RECEIVE_AUDIT_NAMESPACE,
                           'receive-recovery:' + SLOT + ':' + payload['operation_id']))
    return row('mesh_inbound', payload, identifier)


def test_retained_receive_audit_is_admitted_as_unknown_without_replay_claim():
    rows = [control(), receive_audit()]
    before = copy.deepcopy(rows)
    result = inventory(rows)
    assert result == dict(sha256=MODULE.sha(MODULE.canonical(rows)), records=2,
                          historical_outcomes='unknown')
    assert rows == before


@pytest.mark.parametrize('fault', ['record_id', 'slot', 'outcome', 'replayed', 'delivery_claim',
                                   'revision', 'digest', 'reason', 'operation', 'extra_field'])
def test_receive_audit_must_be_exactly_the_reviewed_record(fault):
    audit = receive_audit()
    if fault == 'record_id':
        audit['record_id'] = str(uuid4())
    elif fault == 'slot':
        audit['payload']['slot_id'] = str(uuid4())
    elif fault == 'outcome':
        audit['payload']['outcome'] = 'drained'
    elif fault == 'replayed':
        audit['payload']['replayed'] = True
    elif fault == 'delivery_claim':
        audit['payload']['delivery_claim'] = True
    elif fault == 'revision':
        audit['payload']['original_revision'] = 0
    elif fault == 'digest':
        audit['payload']['original_sha256'] = 'not-a-digest'
    elif fault == 'reason':
        audit['payload']['reason'] = '   '
    elif fault == 'operation':
        audit['payload']['operation_id'] = 'not-a-uuid'
    else:
        audit['payload']['extra'] = 'field'
    with pytest.raises(ValueError):
        inventory([control(), audit])


def pending_outbox_row(state='queued'):
    body = dict(schema='sage.lantern.mesh-outbound.v1', state=state, receipts=[])
    payload = dict(body=body, nonce='synthetic-nonce', signature='synthetic-signature', timestamp=1)
    return row('mesh_outbound', payload, str(uuid4()))


@pytest.mark.parametrize('state', sorted(MODULE.PENDING_OUTBOX_STATES))
def test_pending_outbox_goes_through_the_verified_reader_not_the_terminal_path(state):
    seen = []

    def validate_outbox(entry):
        seen.append(entry)
        return 'retained_unknown'

    rows = [control(), pending_outbox_row(state)]
    result = MODULE.settled_inventory(rows, ACTOR, records, SLOT, validate_receiver,
                                      owner_ledgers=object(), validate_outbox=validate_outbox)
    assert result['records'] == 2
    assert seen == [rows[1]]


@pytest.mark.parametrize('state', ['delivered', 'read', 'invented'])
def test_non_pending_outbox_never_takes_the_pending_branch(state):
    def validate_outbox(entry):
        pytest.fail('pending branch must not claim this row')

    rows = [control(), pending_outbox_row(state)]
    with pytest.raises((ValueError, AttributeError)):
        MODULE.settled_inventory(rows, ACTOR, records, SLOT, validate_receiver,
                                 owner_ledgers=object(), validate_outbox=validate_outbox)
