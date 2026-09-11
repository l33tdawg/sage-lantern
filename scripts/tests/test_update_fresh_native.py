import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('cold_native_update',
    Path(__file__).resolve().parents[1] / 'lantern/update-fresh-native.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_governance_passes_independent_info_observation():
    backend = object.__new__(MODULE.NativeUpdate)
    calls = []
    received = []
    def get(port, route):
        assert port == 26657
        calls.append(route)
        return {'route': route}
    backend.observer = SimpleNamespace(get=get)
    backend.expected = {'chain_id': 'sage-personal-a'}
    backend.preflight = SimpleNamespace(governance=lambda *args: received.append(args))
    backend.governance()
    assert calls == ['/abci_query?path=%22/upgrade/governance-status%22', '/status', '/abci_info']
    assert received == [({'route': calls[0]}, {'route': '/status'}, 'sage-personal-a', {'route': '/abci_info'})]


@pytest.mark.parametrize('fault', [None, 'inhibit_starts', 'drain_auth', 'swap_native', 'request_restart', 'completion'])
def test_transition_order_and_failures_never_retry_or_rollback(fault):
    events = []
    class Backend:
        def current(self):
            events.append('current')
        def action(self, name):
            events.append(name)
            if name == fault:
                raise RuntimeError('synthetic unknown outcome')
        def inhibit(self):
            self.action('inhibit_starts')
        def drain_auth(self):
            self.action('drain_auth')
        def swap(self):
            self.action('swap_native')
        def restart(self):
            self.action('request_restart')
        def completed(self):
            self.action('completion')
            return {'native_updated': True, 'appliance_ready': False}
    records = []
    journal = SimpleNamespace(append=lambda phase, details=None: records.append(phase))
    if fault is None:
        assert MODULE.transition(Backend(), journal)['appliance_ready'] is False
    else:
        with pytest.raises(RuntimeError):
            MODULE.transition(Backend(), journal)
    phases = ['inhibit_starts', 'drain_auth', 'swap_native', 'request_restart', 'completion']
    attempted = [event for event in events if event != 'current']
    assert attempted == (phases if fault is None else phases[:phases.index(fault) + 1])
    for phase in attempted:
        if phase != 'completion':
            assert phase + '_intent' in records
            assert (phase + '_done' in records) is (phase != fault)
    assert ('native_verified_services_inhibited' in records) is (fault is None)


def test_each_mutation_requires_fresh_validation():
    calls = []
    def refused():
        raise RuntimeError('stale preflight')
    backend = SimpleNamespace(current=refused, inhibit=lambda: calls.append('inhibit'),
        drain_auth=lambda: calls.append('auth'), swap=lambda: calls.append('swap'),
        restart=lambda: calls.append('restart'))
    with pytest.raises(RuntimeError):
        MODULE.transition(backend, SimpleNamespace(append=lambda *args: calls.append('journal')))
    assert calls == []


@pytest.mark.parametrize('command', [['stop', 'sage-lantern-personal-sage.service'],
    ['restart', 'sage-lantern-personal-sage.service'], ['start', 'sage-lantern-voice-loop.service'],
    ['stop', 'sage-lantern-device-auth.service', 'sage-lantern-personal-sage.service']])
def test_no_systemd_native_stop_restart_or_producer_start(command):
    backend = object.__new__(MODULE.NativeUpdate)
    backend.preflight = SimpleNamespace(AUTH='sage-lantern-device-auth.service')
    backend.observer = SimpleNamespace(deadline=10, system=SimpleNamespace(
        _command=lambda *args: pytest.fail('forbidden systemd action')))
    with pytest.raises(RuntimeError):
        backend.command(command)


def test_durable_exclusive_journal_retained_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'STATE', tmp_path / 'transition')
    journal = MODULE.Journal({'expected_sha256': 'a' * 64})
    try:
        journal.append('swap_native_intent')
        with pytest.raises(FileExistsError):
            MODULE.Journal({'expected_sha256': 'a' * 64})
    finally:
        journal.close()
    records = [json.loads(line) for line in (MODULE.STATE / 'journal.jsonl').read_text().splitlines()]
    assert [row['phase'] for row in records] == ['intent', 'swap_native_intent']
    assert MODULE.STATE.stat().st_mode & 0o777 == 0o700


def test_write_new_never_overwrites_backup_or_stage(tmp_path):
    path = tmp_path / 'native'
    MODULE.write_new(path, b'synthetic old bytes', 0o755)
    with pytest.raises(FileExistsError):
        MODULE.write_new(path, b'synthetic candidate bytes', 0o755)
    assert path.read_bytes() == b'synthetic old bytes'


def test_unknown_auth_receipt_is_not_reconciled(tmp_path):
    invocation = 'a' * 32
    path = tmp_path / (invocation + '.json')
    raw = b'{"closing":false,"drained":false,"upstream_cleanup":"unknown"}'
    path.write_bytes(raw)
    path.chmod(0o600)
    backend = object.__new__(MODULE.NativeUpdate)
    backend.expected = {'services': {'auth': {'invocation': invocation}}}
    backend.reports = {path.name: 'original'}
    def report(*args, **kwargs):
        assert kwargs == {'current': False}
        raise ValueError('unknown auth cleanup')
    backend.preflight = SimpleNamespace(AUTH='auth', REPORTS=tmp_path,
        trusted=lambda *args: raw, decode=json.loads, report=report)
    backend.observer = SimpleNamespace(uid=os.getuid())
    with pytest.raises(ValueError):
        backend.drained_reports()
    assert path.read_bytes() == raw


def test_guards_are_persistent_and_do_not_clear_prior_conditions():
    assert str(MODULE.STATE).startswith('/var/lib/')
    assert MODULE.GUARD == ('[Unit]\nConditionPathExists=!' + str(MODULE.STATE)
                            + '\n[Service]\nRestart=no\n').encode()
    assert b'ConditionPathExists=\n' not in MODULE.GUARD


@pytest.mark.parametrize('fault', [None, 'same_version', 'v28', 'wrong_version'])
def test_candidate_requires_distinct_version_and_v27_ceiling(monkeypatch, fault):
    backend = object.__new__(MODULE.NativeUpdate)
    backend.expected = {'native_after': {'binary': '/reviewed/candidate', 'manifest': 'new'},
                        'native_before': {'manifest': 'old'}}
    def manifest(path):
        return dict(schema='sage.lantern.binary-staging.v1', version='old' if path == 'old'
                    or fault == 'same_version' else 'new')
    backend.preflight = SimpleNamespace(trusted=manifest, decode=lambda value: value)
    calls = []
    def execute(arguments, **kwargs):
        calls.append(arguments)
        assert arguments == ['/reviewed/candidate', 'version']
        return SimpleNamespace(returncode=0, stdout=('sage-gui ' + ('wrong' if fault == 'wrong_version' else 'new')
            + ' (commit source-test, built date, max-app-v' + ('28' if fault == 'v28' else '27') + ')\n').encode())
    monkeypatch.setattr(MODULE.subprocess, 'run', execute)
    if fault is None:
        assert backend.candidate_version() == 'new'
    else:
        with pytest.raises(RuntimeError):
            backend.candidate_version()
    assert len(calls) == (0 if fault == 'same_version' else 1)


@pytest.mark.parametrize('fault', [None, 'drain', 'governance', 'version', 'late_baseline'])
def test_swap_preserves_backup_and_refuses_before_rename(tmp_path, fault):
    native, backup, candidate = (tmp_path / name for name in ('sage-gui', 'old-backup', 'candidate'))
    native.write_bytes(b'old')
    backup.write_bytes(b'old')
    candidate.write_bytes(b'new')
    backend = object.__new__(MODULE.NativeUpdate)
    backend.guarded, backend.auth_stopped, backend.swapped = True, True, False
    backend.preflight = SimpleNamespace(BINARY=str(native), trusted=lambda path: Path(path).read_bytes(),
                                       sha=lambda raw: MODULE.hashlib.sha256(raw).hexdigest())
    backend.expected = {'native_after': {'binary': str(candidate)},
                        'files': {str(candidate): MODULE.hashlib.sha256(b'new').hexdigest()}}
    events = []
    def check(name):
        events.append(name)
        if fault == name:
            raise RuntimeError('synthetic changed state')
    backend.drained_reports = lambda: check('drain')
    backend.governance = lambda: check('governance')
    backend.candidate_version = lambda: check('version')
    backend.current = lambda: check('late_baseline')
    if fault is None:
        backend.swap()
    else:
        with pytest.raises(RuntimeError):
            backend.swap()
    assert native.read_bytes() == (b'new' if fault is None else b'old')
    assert backup.read_bytes() == b'old' and candidate.read_bytes() == b'new'
    assert backend.swapped is (fault is None)


@pytest.mark.parametrize('fault', [None, 'version', 'missing_boot', 'invalid_boot', 'zero_boot', 'not_running'])
def test_dashboard_identity_uses_public_health_contract(fault):
    backend = object.__new__(MODULE.NativeUpdate)
    response = dict(sage='running', version='candidate', boot_id='11111111-1111-4111-8111-111111111111')
    if fault == 'version':
        response['version'] = 'old'
    elif fault == 'missing_boot':
        del response['boot_id']
    elif fault == 'invalid_boot':
        response['boot_id'] = 'invalid'
    elif fault == 'zero_boot':
        response['boot_id'] = '00000000-0000-0000-0000-000000000000'
    elif fault == 'not_running':
        response['sage'] = 'stopped'
    def get(port, route):
        assert (port, route) == (8080, '/v1/dashboard/health')
        return response
    backend.observer = SimpleNamespace(get=get)
    if fault is None:
        assert backend.dashboard_boot('candidate') == response['boot_id']
    else:
        with pytest.raises((RuntimeError, ValueError)):
            backend.dashboard_boot('candidate')


@pytest.mark.parametrize('fault', [None, 'old_boot', 'changing_boot', 'old_inode', 'old_process', 'late_old_inode'])
def test_completion_requires_new_process_boot_and_inode_not_new_invocation(monkeypatch, fault):
    backend = object.__new__(MODULE.NativeUpdate)
    backend.preflight = SimpleNamespace(SAGE='sage', BINARY='/installed/sage-gui')
    invocation = 'a' * 32
    backend.expected = {'services': {'sage': {'pid': 123, 'invocation': invocation, 'start_ticks': 42}},
                        'boot_id': 'host-boot',
                        'native_after': {'binary': '/candidate'}, 'files': {'/candidate': 'b' * 64}}
    backend.requested = backend.swapped = True
    backend.running_inode = (1, 10)
    backend.old_dashboard_boot = '11111111-1111-4111-8111-111111111111'
    new_boot = '22222222-2222-4222-8222-222222222222'
    polls, health, processes = [], [], []
    def remaining():
        polls.append(True)
        if len(polls) > 2:
            raise TimeoutError('synthetic deadline')
        return 1
    backend.observer = SimpleNamespace(remaining=remaining)
    backend.current = lambda: None
    backend.governance = lambda: None
    backend.candidate_version = lambda: 'candidate'
    def inode(path):
        if str(path).startswith('/proc/'):
            processes.append(True)
            if fault == 'old_process' or (fault == 'late_old_inode' and len(processes) == 2):
                return (1, 10)
        return (1, 10) if fault == 'old_inode' else (1, 20)
    backend.inode = inode
    def dashboard(version, *, locked):
        assert version == 'candidate'
        assert locked is True
        health.append(True)
        if fault == 'old_boot' or (fault == 'changing_boot' and len(health) == 2):
            return backend.old_dashboard_boot
        return new_boot
    backend.dashboard_boot = dashboard
    monkeypatch.setattr(MODULE, 'listener_owner', lambda pid, ticks, boot: '321')
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: None)
    if fault is None:
        result = backend.completed()
        assert result['pid'] == 123 and result['invocation'] == invocation
        assert result['dashboard_boot_before'] == backend.old_dashboard_boot
        assert result['dashboard_boot_after'] == new_boot
        assert result['appliance_ready'] is False
    else:
        with pytest.raises((RuntimeError, TimeoutError)):
            backend.completed()


@pytest.mark.parametrize('encrypted,vault_locked', [(True, True), (False, True), (True, False), (1, True), (True, 1), (None, None)])
def test_completion_health_requires_literal_encrypted_and_locked(encrypted, vault_locked):
    backend = object.__new__(MODULE.NativeUpdate)
    value = dict(sage='running', version='candidate', boot_id='11111111-1111-4111-8111-111111111111',
                 encrypted=encrypted, vault_locked=vault_locked)
    backend.observer = SimpleNamespace(get=lambda *args: value)
    if encrypted is True and vault_locked is True:
        assert backend.dashboard_boot('candidate', locked=True) == value['boot_id']
    else:
        with pytest.raises(RuntimeError):
            backend.dashboard_boot('candidate', locked=True)


@pytest.mark.parametrize('fault', [None, 'foreign_owner', 'wildcard', 'duplicate', 'not_listening',
                                 'reused_pid', 'wrong_boot', 'malformed'])
def test_fixed_listener_requires_actual_matching_process_fd(tmp_path, fault):
    process = tmp_path / '123'
    (process / 'fd').mkdir(parents=True)
    (tmp_path / 'net').mkdir()
    boot_path = tmp_path / 'sys/kernel/random/boot_id'
    boot_path.parent.mkdir(parents=True)
    boot_path.write_text('actual-boot')
    (process / 'stat').write_text('123 (synthetic process) ' + ' '.join(['S'] + ['0'] * 18 + ['42']))
    (process / 'fd/4').symlink_to('socket:[999]' if fault == 'foreign_owner' else 'socket:[321]')
    address = '00000000:1F90' if fault == 'wildcard' else '0100007F:1F90'
    state = '01' if fault == 'not_listening' else '0A'
    row = f'0: {address} 00000000:0000 {state} 0 0 0 0 0 321\n'
    (tmp_path / 'net/tcp').write_text('header\n' + ('invalid row\n' if fault == 'malformed' else row)
                                    + (row if fault == 'duplicate' else ''))
    arguments = (123, 43 if fault == 'reused_pid' else 42,
                 'other-boot' if fault == 'wrong_boot' else 'actual-boot')
    if fault is None:
        assert MODULE.listener_owner(*arguments, proc=tmp_path) == '321'
    elif fault == 'not_listening':
        with pytest.raises(MODULE.StartupUnavailable):
            MODULE.listener_owner(*arguments, proc=tmp_path)
    else:
        with pytest.raises(RuntimeError) as error:
            MODULE.listener_owner(*arguments, proc=tmp_path)
        assert not isinstance(error.value, MODULE.StartupUnavailable)


@pytest.fixture
def completion_poll(monkeypatch):
    backend = object.__new__(MODULE.NativeUpdate)
    backend.preflight = SimpleNamespace(SAGE='sage', BINARY='/installed/sage-gui')
    backend.expected = dict(boot_id='host-boot', services={'sage': dict(pid=123, invocation='a' * 32, start_ticks=42)},
                            native_after={'binary': '/candidate'}, files={'/candidate': 'b' * 64})
    backend.requested = backend.swapped = True
    backend.running_inode = (1, 10)
    backend.old_dashboard_boot = '11111111-1111-4111-8111-111111111111'
    backend.inode = lambda path: (1, 20)
    calls = []
    backend.current = lambda: calls.append('current')
    backend.governance = lambda: None
    backend.candidate_version = lambda: 'candidate'
    backend.dashboard_boot = lambda *args, **kwargs: '22222222-2222-4222-8222-222222222222'
    backend.restart = lambda: pytest.fail('completion polling must never resend restart')
    now = [0.0]
    backend.observer = SimpleNamespace(remaining=lambda: max(0, 0.6 - now[0]))
    def sleep(seconds):
        assert seconds == 0.2
        now[0] += seconds
    monkeypatch.setattr(MODULE.time, 'sleep', sleep)
    monkeypatch.setattr(MODULE, 'listener_owner', lambda *args: '321')
    return backend, now, calls


@pytest.mark.parametrize('where', ['governance', 'listener_first', 'listener_second', 'health_first', 'health_second'])
def test_only_expected_startup_unavailability_is_polled(completion_poll, monkeypatch, where):
    backend, now, calls = completion_poll
    attempts = []
    def probe(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == (2 if where.endswith('second') else 1):
            if where.startswith('listener'):
                raise MODULE.StartupUnavailable('synthetic no listener')
            raise ConnectionRefusedError('synthetic startup connection refusal')
        if where.startswith('listener'):
            return '321'
        return '22222222-2222-4222-8222-222222222222'
    if where == 'governance':
        backend.governance = probe
    elif where.startswith('listener'):
        monkeypatch.setattr(MODULE, 'listener_owner', probe)
    else:
        backend.dashboard_boot = probe
    assert backend.completed()['native_updated'] is True
    assert now[0] == 0.2 and len(calls) >= 3


@pytest.mark.parametrize('where', ['governance', 'listener', 'health'])
@pytest.mark.parametrize('error', [ValueError, RuntimeError, PermissionError, TimeoutError])
def test_conflicting_or_unknown_completion_evidence_is_not_polled(completion_poll, monkeypatch, where, error):
    backend, now, calls = completion_poll
    def reject(*args, **kwargs):
        raise error('synthetic invalid evidence')
    if where == 'governance':
        backend.governance = reject
    elif where == 'listener':
        monkeypatch.setattr(MODULE, 'listener_owner', reject)
    else:
        backend.dashboard_boot = reject
    with pytest.raises(error):
        backend.completed()
    assert now[0] == 0


@pytest.mark.parametrize('where', ['listener', 'health'])
def test_startup_polling_never_extends_completion_deadline(completion_poll, monkeypatch, where):
    backend, now, calls = completion_poll
    def absent(*args, **kwargs):
        if where == 'listener':
            raise MODULE.StartupUnavailable('synthetic absent listener')
        raise ConnectionRefusedError('synthetic connection refused')
    if where == 'listener':
        monkeypatch.setattr(MODULE, 'listener_owner', absent)
    else:
        backend.dashboard_boot = absent
    with pytest.raises(TimeoutError):
        backend.completed()
    assert 0.6 <= now[0] < 0.81
    assert len(calls) <= 4


@pytest.mark.parametrize('changed', [False, True])
def test_current_preserves_absent_idle_using_preflight_empty_tree_contract(tmp_path, monkeypatch, changed):
    backend = object.__new__(MODULE.NativeUpdate)
    idle = 'sage-lantern-idle-suspend.service'
    original = dict(LoadState='not-found', Job='', MainPID='0', ControlGroup='')
    backend.services = {idle: original}
    backend.expected = {'boot_id': 'host-boot', 'files': {}}
    backend.preflight = SimpleNamespace(ABSENT=())
    calls = []
    def service(name, expected, files):
        assert (name, expected, files) == (idle, None, {})
        calls.append('preflight_service_revalidates_empty_cgroup_and_no_job')
        return dict(original, Job='123') if changed else dict(original)
    backend.observer = SimpleNamespace(service=service, deadline=10, remaining=lambda: 1,
        system=SimpleNamespace(_show=lambda *args: pytest.fail('do not treat absent unit as loaded'),
                               _sleep_idle=lambda deadline: None))
    monkeypatch.setattr(MODULE, 'STATE', tmp_path)
    original_lstat, original_read = Path.lstat, Path.read_text
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=0o40700, st_uid=0)
                        if path == tmp_path else original_lstat(path))
    monkeypatch.setattr(Path, 'read_text', lambda path: 'host-boot'
                        if str(path) == '/proc/sys/kernel/random/boot_id' else original_read(path))
    if changed:
        with pytest.raises(RuntimeError):
            backend.current()
    else:
        backend.current()
    assert len(calls) == 1


def test_inhibit_never_creates_absent_idle_fragment_or_dropin():
    backend = object.__new__(MODULE.NativeUpdate)
    backend.services = {'sage-lantern-idle-suspend.service': {'LoadState': 'not-found'}}
    backend.guard_path = lambda service: pytest.fail('absent unit must remain absent')
    commands = []
    backend.command = lambda arguments: commands.append(arguments)
    backend.inhibit()
    assert commands == [['daemon-reload']]


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    specification = importlib.util.spec_from_file_location('supersession_contract',
        Path(__file__).resolve().parents[1] / 'lantern/auth-capability-supersession.py')
    validator = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(validator)
    old_boot, new_boot = '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'
    unknown, current = 'a' * 32, 'b' * 32
    def receipt(instance, confirmed):
        return json.dumps(dict(version=1, instance_id=instance, closing=confirmed, drained=confirmed,
            local_revoked=confirmed, upstream_cleanup='confirmed' if confirmed else 'unknown')).encode()
    reports = tmp_path / 'reports'
    reports.mkdir(mode=0o700)
    for instance, confirmed in ((unknown, False), (current, True)):
        (reports / (instance + '.json')).write_bytes(receipt(instance, confirmed))
        (reports / (instance + '.json')).chmod(0o600)
    digest = lambda raw: MODULE.hashlib.sha256(raw).hexdigest()
    old_image = dict(binary_sha256='c' * 64, manifest_sha256='d' * 64, inventory_sha256='e' * 64, version='old')
    new_image = dict(binary_sha256='f' * 64, manifest_sha256='1' * 64, inventory_sha256='2' * 64, version='new')
    binding = dict(unit_id='unit-a', boot_id=old_boot, root_id='3' * 64, chain_id='sage-personal-test',
        initialized_sha256='4' * 64, native_before=old_image, native_after=new_image,
        sage_before=dict(pid=123, start_ticks=42, invocation='5' * 32, uid=994, application_boot_id=old_boot),
        current_auth=dict(pid=124, start_ticks=43, instance_id=current, uid=994),
        unknown_receipts={unknown: digest(receipt(unknown, False))})
    backend = object.__new__(MODULE.RecoveryNativeUpdate)
    backend.preflight = SimpleNamespace(REPORTS=reports, trusted=lambda path, *args: Path(path).read_bytes(),
                                       sha=digest, decode=json.loads)
    backend.observer = SimpleNamespace(uid=994)
    original_lstat = Path.lstat
    def lstat(path):
        if path == reports:
            info = original_lstat(path)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=994)
        return original_lstat(path)
    monkeypatch.setattr(Path, 'lstat', lstat)
    backend.validator, backend.recovery_binding = validator, binding
    backend.expected = {'boot_id': old_boot}
    backend.reports = {entry.name: digest(entry.read_bytes()) for entry in reports.iterdir()}
    backend.guarded = backend.auth_stopped = backend.swapped = backend.requested = True
    result = dict(native_updated=True, dashboard_boot_after=new_boot, encrypted=True, vault_locked=True)
    backend.completed = lambda: dict(result)
    backend.current = lambda: None
    state = tmp_path / 'transition'
    state.mkdir(mode=0o700)
    monkeypatch.setattr(MODULE, 'STATE', state)
    return backend, result, reports, unknown, current


def test_recovery_publishes_only_separate_schema_and_preserves_unknown(recovery):
    backend, result, reports, unknown, current = recovery
    original = {path.name: path.read_bytes() for path in reports.iterdir()}
    phases = []
    outcome = backend.publish_supersession(result, SimpleNamespace(append=lambda phase, details: phases.append(phase)))
    assert outcome['historical_request_completion'] == 'unknown' and outcome['mutation_authorized'] is False
    assert phases == ['capability_supersession_intent', 'capability_supersession_done']
    path = Path(outcome['path'])
    assert path.stat().st_mode & 0o777 == 0o600
    evidence = json.loads(path.read_bytes())
    assert evidence['unknown_receipts'] == backend.recovery_binding['unknown_receipts']
    assert {path.name: path.read_bytes() for path in reports.iterdir()} == original
    with pytest.raises(FileExistsError):
        backend.publish_supersession(result, SimpleNamespace(append=lambda *args: None))


@pytest.mark.parametrize('fault', ['current_unknown', 'unknown_modified', 'new_report', 'uncompleted', 'unlocked', 'same_boot', 'unfenced'])
def test_recovery_never_supersedes_without_exact_completed_boundary(recovery, fault):
    backend, result, reports, unknown, current = recovery
    if fault == 'current_unknown':
        raw = (reports / (unknown + '.json')).read_bytes().replace(unknown.encode(), current.encode())
        (reports / (current + '.json')).write_bytes(raw)
    elif fault == 'unknown_modified':
        with (reports / (unknown + '.json')).open('ab') as stream:
            stream.write(b'\n')
    elif fault == 'new_report':
        (reports / ('6' * 32 + '.json')).write_bytes(b'{}')
    elif fault == 'uncompleted':
        backend.completed = lambda: {'accepted': True}
    elif fault == 'unlocked':
        result['vault_locked'] = False
    elif fault == 'same_boot':
        result['dashboard_boot_after'] = backend.recovery_binding['sage_before']['application_boot_id']
    else:
        backend.guarded = False
    with pytest.raises((RuntimeError, ValueError)):
        backend.publish_supersession(result, SimpleNamespace(append=lambda *args: None))
    assert not (MODULE.STATE / 'capability-supersession.json').exists()


@pytest.mark.parametrize('fault', [None, 'root', 'old_boot', 'unknown_hash', 'auth_identity'])
def test_recovery_binding_must_match_separately_reviewed_observation(recovery, fault):
    backend, result, reports, unknown, current = recovery
    binding = json.loads(json.dumps(backend.recovery_binding))
    manifests = {}
    files = {'/etc/sage-lantern/initialized-public.json': binding['initialized_sha256']}
    expected = {name: binding[name] for name in ('unit_id', 'boot_id', 'root_id', 'chain_id')}
    for name in ('native_before', 'native_after'):
        artifact = binding[name]
        raw = json.dumps(dict(inventory_sha256=artifact['inventory_sha256'], version=artifact['version'])).encode()
        manifests['/' + name + '.json'] = raw
        artifact['manifest_sha256'] = backend.preflight.sha(raw)
        files['/' + name] = artifact['binary_sha256']
        expected[name] = {'manifest': '/' + name + '.json', 'binary': '/' + name}
    expected['files'] = files
    expected['services'] = {'sage': {name: binding['sage_before'][name] for name in ('pid', 'start_ticks', 'invocation')},
        'auth': dict(pid=binding['current_auth']['pid'], start_ticks=binding['current_auth']['start_ticks'], invocation=current)}
    backend.expected = expected
    backend.preflight.SAGE, backend.preflight.AUTH, backend.preflight.CONFIG = 'sage', 'auth', '/etc/sage-lantern/'
    original_trusted = backend.preflight.trusted
    backend.preflight.trusted = lambda path, *args: manifests[str(path)] if str(path) in manifests else original_trusted(path, *args)
    backend.old_dashboard_boot = binding['sage_before']['application_boot_id']
    current_path = reports / (current + '.json')
    current_path.write_bytes((reports / (unknown + '.json')).read_bytes().replace(unknown.encode(), current.encode()))
    backend.reports[current_path.name] = backend.preflight.sha(current_path.read_bytes())
    observed_unknown = dict(binding['unknown_receipts'])
    if fault == 'root':
        binding['root_id'] = '9' * 64
    elif fault == 'old_boot':
        binding['sage_before']['application_boot_id'] = result['dashboard_boot_after']
    elif fault == 'unknown_hash':
        binding['unknown_receipts'][unknown] = '8' * 64
    elif fault == 'auth_identity':
        binding['current_auth']['pid'] += 1
    if fault is None:
        backend.bind_recovery(backend.validator, binding, observed_unknown)
        binding['unknown_receipts'][unknown] = '8' * 64
        assert backend.recovery_binding['unknown_receipts'] == observed_unknown
    else:
        with pytest.raises((RuntimeError, ValueError)):
            backend.bind_recovery(backend.validator, binding, observed_unknown)


def resume_document():
    pins = dict(expected_sha256='a' * 64, preflight_sha256='b' * 64,
                recovery_binding_sha256='c' * 64, supersession_helper_sha256='d' * 64)
    intent = dict(pins, capability_recovery=True, dashboard_boot_before='11111111-1111-4111-8111-111111111111',
                  listener_inode_before='1234')
    phases = ['intent', 'inhibit_starts_intent', 'inhibit_starts_done', 'drain_auth_intent', 'drain_auth_done',
              'swap_native_intent', 'swap_native_done', 'request_restart_intent']
    rows = [dict(phase=phase, details=intent if phase == 'intent' else None) for phase in phases]
    raw = ('\n'.join(json.dumps(row) for row in rows) + '\n').encode()
    checkpoint = dict(pins, schema='sage.lantern.cold-native-resume.v1',
        journal_sha256=MODULE.hashlib.sha256(raw).hexdigest(),
        replacement_helper={'path': '/reviewed/new-helper.py', 'sha256': 'e' * 64}, services={}, receipts={})
    return raw, checkpoint


@pytest.mark.parametrize('fault', [None, 'pin', 'missing_stage', 'already_accepted', 'extra_field', 'partial_line', 'strict_mode'])
def test_resume_admits_only_exact_interrupted_request_checkpoint(fault):
    raw, checkpoint = resume_document()
    rows = [json.loads(line) for line in raw.splitlines()]
    if fault == 'pin':
        checkpoint['expected_sha256'] = 'f' * 64
    elif fault == 'missing_stage':
        rows.pop(4)
    elif fault == 'already_accepted':
        rows.append(dict(phase='request_restart_done', details=None))
    elif fault == 'extra_field':
        rows[-1]['extra'] = True
    elif fault == 'strict_mode':
        rows[0]['details']['capability_recovery'] = False
    if fault in ('missing_stage', 'already_accepted', 'extra_field', 'strict_mode'):
        raw = ('\n'.join(json.dumps(row) for row in rows) + '\n').encode()
        checkpoint['journal_sha256'] = MODULE.hashlib.sha256(raw).hexdigest()
    if fault == 'partial_line':
        raw = raw.rstrip(b'\n')
        checkpoint['journal_sha256'] = MODULE.hashlib.sha256(raw).hexdigest()
    if fault is None:
        assert MODULE.checkpoint_journal(raw, checkpoint) == rows[0]['details']
    else:
        with pytest.raises(RuntimeError):
            MODULE.checkpoint_journal(raw, checkpoint)


def test_resume_journal_never_modifies_original_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'STATE', tmp_path)
    raw, checkpoint = resume_document()
    original = tmp_path / 'journal.jsonl'
    original.write_bytes(raw)
    journal = MODULE.ResumeJournal('f' * 64)
    try:
        journal.append('request_restart_intent')
        with pytest.raises(FileExistsError):
            MODULE.ResumeJournal('f' * 64)
    finally:
        journal.close()
    assert original.read_bytes() == raw
    assert (tmp_path / 'resume-request.jsonl').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('inspect', [True, False])
@pytest.mark.parametrize('fault', [None, 'helper_intent', 'prior_resume', 'checkpoint_changed', 'live_refused'])
def test_resume_checkpoint_is_readonly_until_new_exclusive_intent(tmp_path, monkeypatch, inspect, fault):
    monkeypatch.setattr(MODULE, 'STATE', tmp_path)
    raw, checkpoint = resume_document()
    original = tmp_path / 'journal.jsonl'
    original.write_bytes(raw)
    original.chmod(0o600)
    binding_raw = b'reviewed-binding'
    (tmp_path / 'capability-binding.json').write_bytes(binding_raw)
    checkpoint['recovery_binding_sha256'] = MODULE.hashlib.sha256(binding_raw).hexdigest()
    rows = [json.loads(line) for line in raw.splitlines()]
    rows[0]['details']['recovery_binding_sha256'] = checkpoint['recovery_binding_sha256']
    raw = ('\n'.join(json.dumps(row) for row in rows) + '\n').encode()
    original.write_bytes(raw)
    checkpoint['journal_sha256'] = MODULE.hashlib.sha256(raw).hexdigest()
    if fault == 'checkpoint_changed':
        original.write_bytes(raw + b'{}\n')
    if fault == 'prior_resume':
        (tmp_path / 'resume-request.jsonl').write_bytes(b'prior attempt')
    checkpoint_raw = json.dumps(checkpoint).encode()
    monkeypatch.setattr(MODULE, 'pinned_source', lambda *args: checkpoint_raw)
    args = SimpleNamespace(capability_recovery=True, resume_checkpoint='/reviewed/checkpoint.json',
        resume_checkpoint_sha256='f' * 64, inspect_resume=inspect,
        **{name: checkpoint[name] for name in ('expected_sha256', 'preflight_sha256',
                                             'recovery_binding_sha256', 'supersession_helper_sha256')})
    preflight = SimpleNamespace(decode=json.loads, sha=lambda value: MODULE.hashlib.sha256(value).hexdigest(),
                                trusted=lambda path, **kwargs: Path(path).read_bytes())
    original_lstat, original_fstat = Path.lstat, os.fstat
    def root_info(info):
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink, st_size=info.st_size)
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=0o40700, st_uid=0)
                        if path == tmp_path else original_lstat(path))
    monkeypatch.setattr(os, 'fstat', lambda descriptor: root_info(original_fstat(descriptor)))
    original_exists = MODULE.os.path.lexists
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path:
        fault == 'helper_intent' if str(path) == '/run/sage-lantern-native-restart-request.jsonl' else original_exists(path))
    events = []
    result = {'native_updated': True}
    def restart():
        assert (tmp_path / 'resume-request.jsonl').exists()
        events.append('restart')
    backend = SimpleNamespace(current=lambda: events.append('current'), restart=restart,
        completed=lambda: events.append('completed') or result,
        publish_supersession=lambda *args: events.append('supersession') or {'historical_request_completion': 'unknown'})
    def inspection(*args):
        events.append('inspect')
        if fault == 'live_refused':
            raise RuntimeError('synthetic stale live checkpoint')
        return backend
    monkeypatch.setattr(MODULE.CheckpointResume, 'inspect', inspection)
    if fault is None:
        returned = MODULE.resume_checkpoint(args, preflight, {}, object(), {})
        assert events == (['inspect'] if inspect else ['inspect', 'current', 'restart', 'completed', 'current', 'supersession'])
        assert returned.get('restart_requested') is False if inspect else returned['native_updated'] is True
    else:
        with pytest.raises(RuntimeError):
            MODULE.resume_checkpoint(args, preflight, {}, object(), {})
        assert 'restart' not in events
    assert original.read_bytes() == (raw + b'{}\n' if fault == 'checkpoint_changed' else raw)
    if inspect and fault != 'prior_resume':
        assert not (tmp_path / 'resume-request.jsonl').exists()


def test_resume_uses_only_separately_pinned_helper_without_rebinding_original(monkeypatch):
    backend = object.__new__(MODULE.CheckpointResume)
    backend.swapped, backend.requested = True, False
    backend.checkpoint = {'replacement_helper': {'path': '/reviewed/helper-0640.py', 'sha256': 'b' * 64}}
    backend.expected = dict(restart_helper='/reviewed/original-helper.py', boot_id='host-boot', root_id='a' * 64,
        files={'/etc/sage-lantern/initialized-public.json': 'c' * 64, '/reviewed/original-helper.py': 'd' * 64},
        services={'sage': {'pid': 123, 'start_ticks': 42}})
    original = json.dumps(backend.expected, sort_keys=True)
    backend.preflight = SimpleNamespace(SAGE='sage', CONFIG='/etc/sage-lantern/', decode=json.loads)
    backend.old_version, backend.old_dashboard_boot, backend.old_listener = 'old', 'old-boot', '321'
    backend.drained_reports = backend.governance = lambda: None
    backend.dashboard_boot = lambda version: 'old-boot'
    backend.observer = SimpleNamespace(remaining=lambda: 20)
    monkeypatch.setattr(MODULE, 'listener_owner', lambda *args: '321')
    calls = []
    def execute(arguments, **kwargs):
        calls.append(arguments)
        assert arguments[:4] == [MODULE.PYTHON, '-I', '-B', '/reviewed/helper-0640.py']
        assert '/reviewed/original-helper.py' not in arguments
        return SimpleNamespace(returncode=0, stdout=b'{"accepted":true,"completed":false}')
    monkeypatch.setattr(MODULE.subprocess, 'run', execute)
    backend.restart()
    assert json.dumps(backend.expected, sort_keys=True) == original
    with pytest.raises(RuntimeError):
        backend.restart()
    assert len(calls) == 1
