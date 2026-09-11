import copy
import importlib.util
import json
import os
import stat
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest


SPEC = importlib.util.spec_from_file_location('resident_native',
    Path(__file__).resolve().parents[1] / 'lantern/deploy-resident-vault-native.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def pins():
    def artifact(name, checksum):
        return dict(binary=str(MODULE.NATIVE) if name == 'old' else '/stage/sage-gui',
            manifest='/stage/' + name + '.json', binary_sha256=checksum * 64,
            manifest_sha256='c' * 64, inventory_sha256='d' * 64, version=name)
    return dict(schema='sage.lantern.resident-native-transition.v1', transition_id=str(uuid.uuid4()),
        boot_id=str(uuid.uuid4()), root_id='a' * 64, initialized_sha256='b' * 64,
        native=dict(pid=100, start_ticks=200, invocation='c' * 32, dashboard_boot=str(uuid.uuid4())),
        before=artifact('old', 'e'), after=artifact('new', 'f'), auth_invocation='a' * 32,
        auth_process=dict(pid=101, start_ticks=201, invocation='a' * 32, image_sha256='f' * 64),
        auth_before_sha256='c' * 64,
        historical_reports={'b' * 32 + '.json': 'a' * 64}, containment_sha256='a' * 64,
        scan_sha256='b' * 64, restart_helper='/stage/request-fresh-sage-restart.py', restart_helper_sha256='c' * 64)


def snapshot(expected, phase):
    before = phase == 'preflight'
    complete = phase == 'complete'
    return dict(boot_id=expected['boot_id'],
        native={key: expected['native'][key] for key in ('pid', 'start_ticks', 'invocation')},
        image=expected['after' if complete else 'before'],
        installed_sha256=expected['before' if phase in ('preflight', 'auth_stopped') else 'after']['binary_sha256'],
        dashboard_boot=str(uuid.uuid4()) if complete else expected['native']['dashboard_boot'],
        listener_verified=True, encrypted=True, vault_locked=complete, guards_verified=True,
        cgroups_verified=True, work_verified=True, app_version=27, pending_plan=None, active_proposal=None,
        historical_reports=copy.deepcopy(expected['historical_reports']),
        auth=dict(invocation=expected['auth_invocation'], active=before,
            process=expected['auth_process'] if before else None,
            receipt=None if before else dict(invocation_id=expected['auth_invocation'], closing=True,
                drained=True, local_revoked=True, upstream_cleanup='confirmed')))


@pytest.mark.parametrize('phase', MODULE.PHASES)
def test_valid_observation_contract(phase):
    expected = pins()
    MODULE.validate_pins(expected)
    MODULE.validate_snapshot(snapshot(expected, phase), expected, phase)


@pytest.mark.parametrize('field,value', [
    ('boot_id', str(uuid.uuid4())), ('listener_verified', False), ('guards_verified', False),
    ('cgroups_verified', False), ('work_verified', False), ('work_verified', 1),
    ('encrypted', False), ('app_version', 28), ('app_version', '27'),
    ('pending_plan', {}), ('active_proposal', {}), ('historical_reports', {}),
    ('installed_sha256', '0' * 64), ('vault_locked', False),
])
def test_incomplete_or_changed_completion_refuses(field, value):
    expected = pins()
    actual = snapshot(expected, 'complete')
    actual[field] = value
    with pytest.raises(RuntimeError):
        MODULE.validate_snapshot(actual, expected, 'complete')


@pytest.mark.parametrize('outcome', ['unknown', 'pending', None, False])
def test_current_auth_unknown_never_admits_swap(outcome):
    expected = pins()
    actual = snapshot(expected, 'auth_stopped')
    actual['auth']['receipt']['upstream_cleanup'] = outcome
    with pytest.raises(RuntimeError):
        MODULE.validate_snapshot(actual, expected, 'auth_stopped')


@pytest.mark.parametrize('key', ['pid', 'start_ticks', 'invocation'])
def test_native_process_identity_cannot_change(key):
    expected = pins()
    actual = snapshot(expected, 'complete')
    actual['native'][key] = 999 if key != 'invocation' else 'f' * 32
    with pytest.raises(RuntimeError):
        MODULE.validate_snapshot(actual, expected, 'complete')


def test_auth_pid_is_pinned_before_stop():
    expected = pins()
    actual = copy.deepcopy(snapshot(expected, 'preflight'))
    actual['auth']['process']['pid'] += 1
    with pytest.raises(RuntimeError):
        MODULE.validate_snapshot(actual, expected, 'preflight')


def test_same_dashboard_boot_is_not_completion():
    expected = pins()
    actual = snapshot(expected, 'complete')
    actual['dashboard_boot'] = expected['native']['dashboard_boot']
    with pytest.raises(RuntimeError):
        MODULE.validate_snapshot(actual, expected, 'complete')


def test_historical_unknown_reports_preserved_not_reclassified():
    expected = pins()
    actual = snapshot(expected, 'auth_stopped')
    MODULE.validate_snapshot(actual, expected, 'auth_stopped')
    actual['historical_reports']['b' * 32 + '.json'] = 'f' * 64
    with pytest.raises(RuntimeError):
        MODULE.validate_snapshot(actual, expected, 'auth_stopped')


def test_exclusive_journal_retains_failure_evidence(tmp_path):
    state = tmp_path / 'new'
    journal = MODULE.Journal(state, pins())
    journal.append('request_restart_intent')
    before = journal.path.read_bytes()
    with pytest.raises(FileExistsError):
        MODULE.Journal(state, pins())
    assert journal.path.read_bytes() == before
    assert json.loads(before.splitlines()[-1])['phase'] == 'request_restart_intent'


@pytest.mark.parametrize('fault', [None, 'containment', 'stop', 'swap', 'request', 'complete'])
def test_coordinator_orders_and_never_retries_or_rolls_back(fault, monkeypatch):
    expected = pins()
    events = []
    records = []
    def action(name):
        events.append(name)
        if fault == name:
            raise RuntimeError('unknown synthetic outcome')
    class Backend:
        completed = snapshot(expected, 'complete')
        def verify_containment(self, containment, scan):
            assert (containment, scan) == (expected['containment_sha256'], expected['scan_sha256'])
            action('containment')
            return True
        def observe(self, phase):
            events.append('observe:' + phase)
            return self.completed if phase == 'complete' else snapshot(expected, phase)
        def stop_pinned_auth(self, service):
            assert service == MODULE.AUTH
            action('stop')
        def backup_and_swap(self, state, received):
            assert received == expected
            action('swap')
        def wait_for_native_exec(self):
            action('complete')
            return self.completed
    monkeypatch.setattr(MODULE, 'Journal', lambda *args: SimpleNamespace(
        append=lambda phase, details=None: records.append(phase)))
    monkeypatch.setattr(MODULE, 'restart_child', lambda *args: action('request'))
    state = Path('/var/lib/sage-lantern-resident-native-' + expected['transition_id'])
    if fault:
        with pytest.raises(RuntimeError):
            MODULE.transition(expected, Backend(), state)
    else:
        assert MODULE.transition(expected, Backend(), state) == dict(
            native_updated=True, appliance_ready=False, user_unlock_required=True)
    attempted = [event for event in events if not event.startswith('observe:')]
    phases = ['containment', 'stop', 'swap', 'request', 'complete']
    assert attempted == (phases if fault is None else phases[:phases.index(fault) + 1])
    if fault == 'request':
        assert records[-1] == 'request_restart_intent'
        assert 'accepted_not_completed' not in records
    if fault == 'stop':
        assert records[-1] == 'stop_auth_intent'
    assert ('native_verified_services_inhibited' in records) is (fault is None)


@pytest.mark.parametrize('service', ['sage-lantern-personal-sage.service', MODULE.AUTH + ' other',
    'sage-lantern-voice-loop.service'])
def test_native_systemd_stop_impossible(service, monkeypatch):
    monkeypatch.setattr(MODULE.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must not run'))
    with pytest.raises(RuntimeError):
        MODULE.stop_auth_only(service, lambda: pytest.fail('must not validate'))


def test_auth_stop_requires_immediately_fresh_observation(monkeypatch):
    monkeypatch.setattr(MODULE.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must not run'))
    def stale():
        raise RuntimeError('PID changed')
    with pytest.raises(RuntimeError):
        MODULE.stop_auth_only(MODULE.AUTH, stale)


def test_restart_child_uses_unique_intent_and_public_args_only(tmp_path, monkeypatch):
    expected = pins()
    helper = b'reviewed helper source'
    expected['restart_helper_sha256'] = MODULE.digest(helper)
    monkeypatch.setattr(MODULE, 'trusted', lambda *args: helper)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{"accepted":true,"completed":false}')
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    MODULE.restart_child(expected, tmp_path)
    command, options = calls[0]
    assert command[:4] == [MODULE.PYTHON, '-I', '-B', '-c']
    assert str(tmp_path / 'signed-request.jsonl') in command
    assert '--root-id' in command and '--initialized-sha256' in command
    assert options['env'] == MODULE.ENV
    assert 'systemctl' not in ' '.join(command)
    assert 'agent.key' not in ' '.join(command)


def test_existing_restart_intent_refuses_even_with_valid_helper(tmp_path, monkeypatch):
    expected = pins()
    helper = b'helper'
    expected['restart_helper_sha256'] = MODULE.digest(helper)
    monkeypatch.setattr(MODULE, 'trusted', lambda *args: helper)
    monkeypatch.setattr(MODULE.subprocess, 'run', lambda *args, **kwargs: pytest.fail('no retries'))
    (tmp_path / 'signed-request.jsonl').write_text('prior unknown outcome')
    with pytest.raises(RuntimeError):
        MODULE.restart_child(expected, tmp_path)


def test_restart_helper_hash_mismatch_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'trusted', lambda *args: b'changed source')
    monkeypatch.setattr(MODULE.subprocess, 'run', lambda *args, **kwargs: pytest.fail('no execution'))
    with pytest.raises(RuntimeError):
        MODULE.restart_child(pins(), tmp_path)


def test_swap_backs_up_before_atomic_replace(tmp_path, monkeypatch):
    expected = pins()
    old, new = b'old native', b'new native'
    manifest = b'old manifest'
    expected['before']['binary_sha256'] = MODULE.digest(old)
    expected['before']['manifest_sha256'] = MODULE.digest(manifest)
    expected['after']['binary_sha256'] = MODULE.digest(new)
    target = tmp_path / 'sage-gui'
    target.write_bytes(old)
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(MODULE, 'NATIVE', target)
    monkeypatch.setattr(MODULE, 'trusted', lambda path: Path(path).read_bytes())
    monkeypatch.setattr(MODULE, 'checked_artifact', lambda value:
        (old, manifest) if value is expected['before'] else (new, b'new manifest'))
    def reverify():
        assert target.read_bytes() == old
        assert (state / 'native-before').read_bytes() == old
        assert (state / 'manifest-before.json').read_bytes() == manifest
    MODULE.backup_and_swap(state, expected, reverify)
    assert target.read_bytes() == new
    assert (state / 'native-before').read_bytes() == old


def test_cli_requires_pinned_envelope():
    result = MODULE.subprocess.run([__import__('sys').executable, str(SPEC.origin), '--apply'],
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert '--expected, --expected-sha256' in result.stderr


@pytest.mark.parametrize('version', ['sage-gui new (commit abc, max-app-v28)',
    'sage-gui wrong (commit abc, max-app-v27)', 'sage-gui new (commit abc, max-app-v26)'])
def test_artifact_version_cannot_escape_v27(version, monkeypatch):
    artifact = pins()['after']
    raw = b'candidate'
    manifest = json.dumps(dict(schema='sage.lantern.binary-staging.v1',
        binary_sha256=MODULE.digest(raw), inventory_sha256=artifact['inventory_sha256'], version='new')).encode()
    artifact['binary_sha256'] = MODULE.digest(raw)
    artifact['manifest_sha256'] = MODULE.digest(manifest)
    monkeypatch.setattr(MODULE, 'trusted', lambda path, *args: raw if path == artifact['binary'] else manifest)
    monkeypatch.setattr(MODULE.subprocess, 'run', lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout=version.encode()))
    with pytest.raises(RuntimeError):
        MODULE.checked_artifact(artifact)


@pytest.mark.parametrize('field', ['inventory_sha256', 'binary_sha256', 'version'])
def test_manifest_content_must_match_source_inventory(field, monkeypatch):
    artifact = pins()['after']
    raw = b'candidate'
    values = dict(schema='sage.lantern.binary-staging.v1', binary_sha256=MODULE.digest(raw),
        inventory_sha256=artifact['inventory_sha256'], version=artifact['version'])
    values[field] = 'mismatch'
    manifest = json.dumps(values).encode()
    artifact['binary_sha256'] = MODULE.digest(raw)
    artifact['manifest_sha256'] = MODULE.digest(manifest)
    monkeypatch.setattr(MODULE, 'trusted', lambda path, *args: raw if path == artifact['binary'] else manifest)
    monkeypatch.setattr(MODULE.subprocess, 'run', lambda *args, **kwargs: pytest.fail('not executable'))
    with pytest.raises(RuntimeError):
        MODULE.checked_artifact(artifact)


def test_concrete_observation_runs_fullscan_not_health_idle(monkeypatch):
    backend = object.__new__(MODULE.Native)
    backend.pins = pins()
    backend.expected = {'chain_id': 'sage-personal-a'}
    backend.completed = backend.auth_stopped = False
    backend.guarded = backend.model_stopped = True
    backend.receipt = dict(storage_epoch='epoch', reconciliation={'stable_epoch': True})
    calls = []
    backend.contained = lambda: calls.append('containment')
    backend.backend = SimpleNamespace(reconcile=lambda:
        (calls.append('fullscan') or ('epoch', {'stable_epoch': True})))
    backend.auth = lambda: snapshot(backend.pins, 'preflight')['auth']
    def get(port, route):
        calls.append(route)
        if route == '/state':
            return {'locked': True}
        if route == '/v1/dashboard/health':
            return dict(sage='running', version='old', encrypted=True, vault_locked=False,
                boot_id=backend.pins['native']['dashboard_boot'])
        return {}
    backend.observer = SimpleNamespace(get=get)
    backend.preflight = SimpleNamespace(governance=lambda *args: calls.append('governance'))
    backend.update = SimpleNamespace(listener_owner=lambda *args: calls.append('listener') or '123')
    raw = b'old'
    backend.pins['before']['binary_sha256'] = MODULE.digest(raw)
    monkeypatch.setattr(MODULE, 'trusted', lambda *args: raw)
    actual = backend.observe('preflight')
    MODULE.validate_snapshot(actual, backend.pins, 'preflight')
    assert calls.count('fullscan') == 1
    assert calls.count('listener') == 2
    assert calls.count('containment') == 2
    assert 'governance' in calls
    assert '/health' not in calls


@pytest.mark.parametrize('epoch,scan', [('different', {'stable_epoch': True}),
    ('epoch', {'stable_epoch': False})])
def test_concrete_observer_refuses_epoch_or_work_change(epoch, scan):
    backend = object.__new__(MODULE.Native)
    backend.completed = False
    backend.contained = lambda: None
    backend.receipt = dict(storage_epoch='epoch', reconciliation={'stable_epoch': True})
    backend.backend = SimpleNamespace(reconcile=lambda: (epoch, scan))
    with pytest.raises(RuntimeError):
        backend.observe('preflight')


def test_auth_stop_does_not_upgrade_unknown_receipt(tmp_path, monkeypatch):
    backend = object.__new__(MODULE.Native)
    backend.pins = pins()
    backend.pins['historical_reports'] = {}
    backend.superseded = {}
    backend.auth_stopped = True
    backend.observer = SimpleNamespace(uid=994)
    path = tmp_path / (backend.pins['auth_invocation'] + '.json')
    raw = json.dumps(dict(version=1, instance_id=backend.pins['auth_invocation'],
        closing=True, drained=True, local_revoked=True, upstream_cleanup='unknown')).encode()
    path.write_bytes(raw)
    backend.inspector = SimpleNamespace(decode=json.loads)
    def report(value, invocation, current):
        assert invocation == backend.pins['auth_invocation']
        assert current is False
        MODULE.require(value['upstream_cleanup'] == 'confirmed')
    backend.preflight = SimpleNamespace(REPORTS=tmp_path, trusted=lambda *args: raw, report=report)
    with pytest.raises(RuntimeError):
        backend.auth()
    assert path.read_bytes() == raw


def test_source_never_calls_native_stop_or_restart():
    source = Path(SPEC.origin).read_text()
    assert "'stop', self.sage" not in source
    assert "'restart', self.sage" not in source
    assert "'stop', self.model" in source
    assert 'self.backend.reconcile()' in source
    assert "'accepted_not_completed'" in source


def test_concrete_prepare_stops_only_pinned_model(tmp_path, monkeypatch):
    backend = object.__new__(MODULE.Native)
    backend.guard_paths = {}
    backend.guarded = backend.model_stopped = False
    backend.model = 'sage-lantern-llm.service'
    backend.pins = pins()
    raw = b'auth before'
    backend.pins['auth_before_sha256'] = MODULE.digest(raw)
    backend.envelope = {'pins': backend.pins}
    backend.state = tmp_path
    backend.preflight = SimpleNamespace(REPORTS=tmp_path, trusted=lambda *args: raw)
    backend.observer = SimpleNamespace(uid=994)
    calls = []
    backend.contained = lambda: calls.append(('verify', backend.guarded, backend.model_stopped))
    monkeypatch.setattr(MODULE.subprocess, 'run', lambda command, **kwargs: calls.append(command))
    journal = SimpleNamespace(append=lambda phase, details=None: calls.append(phase))
    backend.prepare(journal)
    commands = [item for item in calls if isinstance(item, list)]
    assert commands == [['/usr/bin/systemctl', '--no-pager', 'daemon-reload'],
        ['/usr/bin/systemctl', '--no-pager', 'stop', backend.model]]
    assert calls[-1] == 'stop_model_done'
    stop_index = calls.index(commands[-1])
    assert calls[stop_index - 1] == ('verify', True, False)
    assert calls[stop_index + 1] == ('verify', True, True)


def test_read_atime_change_does_not_invalidate_artifact_identity():
    values = dict(st_dev=1, st_ino=2, st_mode=0o100600, st_uid=0, st_gid=0,
        st_nlink=1, st_size=100, st_mtime_ns=4, st_ctime_ns=5, st_atime_ns=6, st_atime=6)
    before = SimpleNamespace(**values)
    after = SimpleNamespace(**dict(values, st_atime_ns=99, st_atime=99))
    assert MODULE.file_identity(before) == MODULE.file_identity(after)


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
    'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'])
def test_security_relevant_file_metadata_changes_invalidate_identity(field):
    values = dict(st_dev=1, st_ino=2, st_mode=0o100600, st_uid=0, st_gid=0,
        st_nlink=1, st_size=100, st_mtime_ns=4, st_ctime_ns=5)
    after = dict(values)
    after[field] += 1
    assert MODULE.file_identity(SimpleNamespace(**values)) != MODULE.file_identity(SimpleNamespace(**after))


def test_service_owned_private_parent_accepts_only_root_marker():
    parent = SimpleNamespace(st_mode=0o40700, st_uid=994, st_gid=993)
    leaf = SimpleNamespace(st_mode=0o100644, st_uid=0, st_gid=0, st_nlink=1)
    MODULE.marker_metadata(parent, leaf, 994, 993)


@pytest.mark.parametrize('object_name,field,value', [('parent', 'st_mode', 0o40770),
    ('parent', 'st_uid', 1000), ('parent', 'st_gid', 1000), ('parent', 'st_mode', 0o120700),
    ('leaf', 'st_uid', 994), ('leaf', 'st_gid', 993), ('leaf', 'st_mode', 0o120644),
    ('leaf', 'st_mode', 0o100664), ('leaf', 'st_nlink', 2)])
def test_marker_exception_never_weakens_owner_or_symlink_checks(object_name, field, value):
    values = dict(parent=SimpleNamespace(st_mode=0o40700, st_uid=994, st_gid=993),
        leaf=SimpleNamespace(st_mode=0o100644, st_uid=0, st_gid=0, st_nlink=1))
    setattr(values[object_name], field, value)
    with pytest.raises(RuntimeError):
        MODULE.marker_metadata(values['parent'], values['leaf'], 994, 993)


@pytest.mark.parametrize('raw', [b'', b'nonempty lifetime'])
def test_recovery_archive_supports_empty_owner_lock_only_with_exact_hash(tmp_path, monkeypatch, raw):
    backend = object.__new__(MODULE.Native)
    backend.recovery = SimpleNamespace(STATE=tmp_path)
    archive = tmp_path / 'archive'
    archive.mkdir()
    path = archive / '004.bin'
    path.write_bytes(raw)
    path.chmod(0o600)
    calls = []
    def read(filename, limit, *, allow_empty):
        calls.append((filename, limit, allow_empty))
        return Path(filename).read_bytes()
    monkeypatch.setattr(MODULE, 'trusted', read)
    pin = {'archive_path': str(path), 'sha256': MODULE.digest(raw)}
    assert backend.archived_bytes(pin) == raw
    assert calls == [(path, 2097152, raw == b'')]


@pytest.mark.parametrize('fault', ['hash', 'mode', 'outside', 'name'])
def test_empty_archive_exception_refuses_unpinned_or_unsafe_archive(tmp_path, monkeypatch, fault):
    backend = object.__new__(MODULE.Native)
    backend.recovery = SimpleNamespace(STATE=tmp_path)
    archive = tmp_path / 'archive'
    archive.mkdir()
    path = archive / '004.bin'
    path.write_bytes(b'')
    path.chmod(0o644 if fault == 'mode' else 0o600)
    monkeypatch.setattr(MODULE, 'trusted', lambda filename, limit, *, allow_empty: Path(filename).read_bytes())
    pin = {'archive_path': str(path), 'sha256': 'a' * 64 if fault == 'hash' else MODULE.digest(b'')}
    if fault == 'outside':
        pin['archive_path'] = str(tmp_path / '004.bin')
    if fault == 'name':
        pin['archive_path'] = str(archive / 'owner.lock')
    with pytest.raises(RuntimeError):
        backend.archived_bytes(pin)


@pytest.mark.parametrize('auth_stopped', [False, True])
@pytest.mark.parametrize('fault', [None, 'model_not_empty', 'auth_not_empty', 'native_changed',
    'changed_scan', 'model_work_after_scan'])
def test_actual_recovery_reconcile_uses_native_phase_checks_at_both_ends(monkeypatch, auth_stopped, fault):
    spec = importlib.util.spec_from_file_location('actual_recovery_adapter_test',
        Path(SPEC.origin).with_name('recover-stale-audio-epoch.py'))
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    backend = object.__new__(MODULE.Native)
    backend.sage = recovery.SAGE
    backend.model = 'sage-lantern-llm.service'
    backend.completed = False
    backend.guarded = backend.model_stopped = True
    backend.auth_stopped = auth_stopped
    backend.recovery = recovery
    backend.state = Path('/var/lib/test-transition')
    backend.guard = b'guard'
    backend.guard_paths = {service: Path('/etc/systemd/system') / (service + '.d') / 'new.conf'
        for service in (MODULE.AUTH, backend.model)}
    services = (*recovery.PRODUCERS, MODULE.AUTH, backend.model, backend.sage)
    backend.expected = dict(unit_id='unit-a', chain_id='sage-personal-test', agent_id='a' * 64,
        services={service: dict(dropins=[], pid=100 + index) for index, service in enumerate(services)})
    events = []
    def service_check(service, pin):
        events.append(('active', service))
        assert service != backend.model
        assert not (auth_stopped and service == MODULE.AUTH)
        if fault == 'native_changed' and service == backend.sage:
            raise RuntimeError('native PID/image changed')
    native = SimpleNamespace(deadline=recovery.time.monotonic() + 20, service=service_check)
    helper = SimpleNamespace(Native=lambda timeout: native)
    backend.backend = recovery.Backend(helper, backend.expected, b'expected', 'a' * 64)
    def empty(service):
        events.append(('empty', service))
        if ((fault == 'model_not_empty' and service == backend.model)
                or (fault == 'model_work_after_scan' and service == backend.model and len(scans) == 2)
                or (fault == 'auth_not_empty' and auth_stopped and service == MODULE.AUTH)):
            raise RuntimeError('cgroup has live work')
        return {'recursive_cgroup_empty': True}
    backend.backend.empty = empty
    backend.backend.guards = lambda: events.append(('guards', 'producers'))
    def properties(service, fields):
        if fields == ('Restart',):
            return {'Restart': 'no'}
        return dict(DropInPaths=str(backend.guard_paths[service]), Restart='no',
            NeedDaemonReload='no', FragmentPath='/etc/systemd/system/' + service)
    backend.backend.properties = properties
    backend.files = lambda: events.append(('files', 'checked'))
    backend.observer = SimpleNamespace(deadline=native.deadline, system=SimpleNamespace(
        _guard=lambda *args: events.append(('guard', args[0])),
        _sleep_idle=lambda *args: events.append(('sleep', 'idle'))))
    monkeypatch.setattr(MODULE, 'trusted', lambda *args: backend.guard)
    backend.bind_recovery_observer()
    assert backend.backend.unchanged.__self__ is backend
    assert backend.backend.unchanged.__func__ is MODULE.Native.contained
    assert backend.backend.contained.__func__ is MODULE.Native.contained
    config = dict(unit_id='unit-a', chain_id='sage-personal-test', agent_id='a' * 64, endpoint='http://127.0.0.1:8080')
    descriptor = os.open(os.devnull, os.O_RDONLY)
    monkeypatch.setitem(sys.modules, 'sage_lantern.mesh_daemon', SimpleNamespace(
        load_config=lambda: config, load_identity=lambda supplied: (SimpleNamespace(agent_id='a' * 64), descriptor)))
    monkeypatch.setitem(sys.modules, 'sage_lantern.message_contacts', SimpleNamespace(RecipientID=lambda *args: args))
    monkeypatch.setitem(sys.modules, 'sage_lantern.message_storage', SimpleNamespace(
        MessageStorageProbe=lambda *args, **kwargs: SimpleNamespace(snapshot=lambda: SimpleNamespace(epoch='a' * 64 + ':10'))))
    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs['trust_env'] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    monkeypatch.setitem(sys.modules, 'sage_sdk', SimpleNamespace(SageClient=Client))
    scans = []
    class Store:
        def __init__(self, *args, **kwargs):
            pass
        def scan(self, **kwargs):
            assert kwargs['access_check']() is True
            scans.append('scan')
            return [{'control': 2 if fault == 'changed_scan' and len(scans) == 2 else 1}]
    monkeypatch.setitem(sys.modules, 'sage_lantern.workflow_store', SimpleNamespace(WorkflowStore=Store))
    monkeypatch.setattr(recovery, 'control_inventory', lambda *args: {'complete': True})
    fails = fault is not None and not (fault == 'auth_not_empty' and not auth_stopped)
    try:
        if fails:
            with pytest.raises((RuntimeError, ValueError)):
                backend.backend.reconcile()
        else:
            result = backend.backend.reconcile()
            assert result == ('a' * 64 + ':10', dict(complete=True, stable_epoch=True, repeated_inventory_equal=True))
            assert scans == ['scan', 'scan']
            assert events.count(('files', 'checked')) == 6
            assert events.count(('empty', backend.model)) == 6
            if auth_stopped:
                assert events.count(('empty', MODULE.AUTH)) == 6
            else:
                assert events.count(('active', MODULE.AUTH)) == 6
            assert events.count(('active', backend.sage)) == 6
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


@pytest.fixture
def simulated_root_metadata(monkeypatch):
    original_lstat, original_fstat = Path.lstat, os.fstat
    def metadata(info):
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        value = {field: getattr(info, field) for field in fields}
        value.update(st_uid=0, st_gid=0)
        if stat.S_ISDIR(value['st_mode']):
            value['st_mode'] &= ~0o022
        return SimpleNamespace(**value)
    monkeypatch.setattr(Path, 'lstat', lambda path: metadata(original_lstat(path)))
    monkeypatch.setattr(os, 'fstat', lambda descriptor: metadata(original_fstat(descriptor)))


def test_real_archive_reader_zero_length_is_opt_in(tmp_path, simulated_root_metadata):
    path = tmp_path / '004.bin'
    path.write_bytes(b'')
    path.chmod(0o600)
    with pytest.raises(RuntimeError):
        MODULE.trusted(path, 2097152)
    assert MODULE.trusted(path, 2097152, allow_empty=True) == b''


@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'oversized', 'writable'])
def test_real_empty_reader_keeps_file_safety_checks(tmp_path, simulated_root_metadata, fault):
    path = tmp_path / '004.bin'
    path.write_bytes(b'x' * 5 if fault == 'oversized' else b'')
    path.chmod(0o666 if fault == 'writable' else 0o600)
    if fault == 'hardlink':
        os.link(path, tmp_path / 'duplicate')
    if fault == 'symlink':
        link = tmp_path / 'link'
        link.symlink_to(path)
        path = link
    with pytest.raises((RuntimeError, OSError)):
        MODULE.trusted(path, 4, allow_empty=True)
