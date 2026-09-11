import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('b_transition',
    Path(__file__).resolve().parents[1] / 'lantern/deploy-b-private-policy.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def scan_config():
    return dict(module.SCAN_BINDING, messaging_enabled=False, dispatch_enabled=False,
        receive_while_locked=False, dispatch_while_locked=False, peers=[])


@pytest.mark.parametrize('service', [module.AUTH, module.UI])
@pytest.mark.parametrize('outcome', ['exec', 'already_exec', 'other_image', 'pid', 'start_ticks', 'invocation', 'timeout', 'exit', 'systemd_changed'])
def test_preexec_wait_only_same_process_to_required_image(monkeypatch, service, outcome):
    backend = object.__new__(module.Backend)
    backend.deadline = 100
    target_hash = 'c' * 64
    backend.expected = {'services': {service: dict(image_sha256=target_hash, invocation='a' * 32)}}
    clock, count = [0], [0]
    initial = dict(pid=123, start_ticks=456, invocation='b' * 32, image_sha256=module.SYSTEMD_SHA)
    backend.system = SimpleNamespace(_sleep_idle=lambda deadline: None)
    backend.services = lambda *args, **kwargs: None
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + 1))
    monkeypatch.setattr(module, 'read', lambda *args, **kwargs: b'systemd')
    monkeypatch.setattr(module, 'digest', lambda raw: 'bad' if outcome == 'systemd_changed' else module.SYSTEMD_SHA)
    def observe(ignored):
        count[0] += 1
        current = dict(initial)
        if outcome == 'already_exec' or (count[0] > 1 and outcome not in ('timeout', 'exit')):
            current['image_sha256'] = target_hash
        if count[0] > 1:
            if outcome == 'other_image':
                current['image_sha256'] = 'd' * 64
            if outcome in ('pid', 'start_ticks'):
                current[outcome] += 1
            if outcome == 'invocation':
                current['invocation'] = 'e' * 32
            if outcome == 'exit':
                raise RuntimeError('exited')
        return current
    backend.observe_process = observe
    ready = dict(initial, image_sha256=target_hash)
    backend.capture_process = lambda requested: ready if requested == service else {'auth': 'pinned'}
    auth = {'auth': 'pinned'} if service == module.UI else None
    if outcome in ('exec', 'already_exec'):
        assert backend.wait_initial_exec(service, auth) == ready
    else:
        with pytest.raises((RuntimeError, TimeoutError)):
            backend.wait_initial_exec(service, auth)
    assert clock[0] <= 10


def test_capture_after_readiness_never_accepts_systemd():
    backend = object.__new__(module.Backend)
    backend.expected = {'services': {module.AUTH: {'image_sha256': 'c' * 64}}}
    backend.observe_process = lambda service: {'image_sha256': module.SYSTEMD_SHA}
    with pytest.raises(RuntimeError):
        backend.capture_process(module.AUTH)


@pytest.mark.parametrize('service', [module.AUTH, module.UI])
@pytest.mark.parametrize('fault', [None, 'other_image', 'pid', 'invocation', 'start', 'strict', 'reverted'])
def test_real_services_predicate_allows_only_initial_exec_race(tmp_path, monkeypatch, service, fault):
    backend = object.__new__(module.Backend)
    systemd = tmp_path / 'systemd'
    executable = tmp_path / 'python'
    systemd.write_bytes(b'systemd')
    executable.write_bytes(b'python' if fault != 'other_image' else b'unapproved')
    monkeypatch.setattr(module, 'SYSTEMD_IMAGE', systemd)
    monkeypatch.setattr(module, 'SYSTEMD_SHA', module.digest(b'systemd'))
    monkeypatch.setattr(module, 'read', lambda path, **kwargs: Path(path).read_bytes())
    initial = dict(pid=123, start_ticks=456, invocation='b' * 32, image_sha256=module.SYSTEMD_SHA)
    required = dict(initial, image_sha256=module.digest(b'python'))
    fragment = '/etc/systemd/system/' + service
    backend.expected = dict(boot_id='boot', files={fragment: 'fixture'},
        services={service: dict(required, invocation='a' * 32)})
    backend.released = set()
    backend.deadline = module.time.monotonic() + 20
    backend.system = SimpleNamespace(_sleep_idle=lambda deadline: None)
    value = dict(LoadState='loaded', Transient='no', FragmentPath=fragment, NeedDaemonReload='no',
        Job='', KillMode='control-group', Slice='system.slice', DropInPaths='', ActiveState='active',
        MainPID='124' if fault == 'pid' else '123', InvocationID='c' * 32 if fault == 'invocation' else 'b' * 32,
        ControlGroup='/system.slice/' + service)
    backend.properties = lambda unit: dict(value)
    original_read = Path.read_text
    original_open = Path.open
    def read_text(path, *args, **kwargs):
        if str(path) == '/proc/sys/kernel/random/boot_id':
            return 'boot'
        if str(path) == '/proc/123/stat':
            return '123 (python) ' + ' '.join(['0'] * 19 + ['457' if fault == 'start' else '456'])
        return original_read(path, *args, **kwargs)
    def opened(path, *args, **kwargs):
        return original_open(executable if str(path) == '/proc/123/exe' else path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(Path, 'open', opened)
    if fault and fault != 'reverted':
        with pytest.raises(RuntimeError):
            backend.services(guarded=module.RETAINED, completed=True, fresh={service: initial},
                initial_exec=None if fault == 'strict' else service)
    else:
        observations = iter((initial, initial if fault == 'reverted' else required, required))
        backend.observe_process = lambda unit: next(observations) if unit == service else required
        capture = backend.capture_process
        backend.capture_process = lambda unit: required if unit != service else capture(unit)
        if fault == 'reverted':
            with pytest.raises(RuntimeError):
                backend.wait_initial_exec(service, required if service == module.UI else None)
        else:
            assert backend.wait_initial_exec(service, required if service == module.UI else None) == required


@pytest.mark.parametrize('tag', ['capture_initial', 'capture_before', 'services', 'sleep_gate', 'http_state', 'current_receipt'])
def test_readiness_diagnostic_has_exact_frame_without_private_message(tag):
    backend = object.__new__(module.Backend)
    backend.properties = lambda service: dict(ActiveState='active', MainPID='123', InvocationID='b' * 32)
    def rejected():
        private_value = 'never include this private body'
        raise RuntimeError(private_value)
    with pytest.raises(RuntimeError):
        backend.readiness_step(tag, rejected)
    diagnostic = backend.readiness_diagnostic
    assert diagnostic['failed_tag'] == tag
    sample = diagnostic['samples'][0]
    assert sample['before']['MainPID'] == sample['after']['MainPID'] == '123'
    assert sample['frames'][-1]['function'] == 'rejected'
    assert sample['frames'][-1]['source'] == Path(__file__).name
    assert sample['frames'][-1]['line'] > 0
    assert 'never include' not in json.dumps(diagnostic)
    assert all(set(frame) == {'source', 'function', 'line'} for frame in sample['frames'])


def test_readiness_diagnostic_is_bounded():
    backend = object.__new__(module.Backend)
    backend.properties = lambda service: dict(InvocationID='a' * 1000)
    for number in range(40):
        backend.readiness_step('capture_before', lambda: True)
    assert len(backend.readiness_diagnostic['samples']) == 32
    assert len(backend.readiness_diagnostic['samples'][0]['before']['InvocationID']) == 128


def test_http_diagnostic_records_status_not_body(monkeypatch):
    response = SimpleNamespace(status=403, read=lambda limit: b'{"private":"hidden"}')
    connection = SimpleNamespace(request=lambda *args: None, getresponse=lambda: response, close=lambda: None)
    monkeypatch.setattr(module.http.client, 'HTTPConnection', lambda *args, **kwargs: connection)
    diagnostic = {}
    with pytest.raises(RuntimeError):
        module.get(3196, '/state', diagnostics=diagnostic)
    assert diagnostic == {'status': 403, 'response_bytes': 20}


@pytest.mark.parametrize('outcome', ['delayed', 'exit', 'pid_change', 'timeout', 'unlocked'])
def test_type_simple_auth_wait_is_bounded_and_pinned(monkeypatch, outcome):
    backend = object.__new__(module.Backend)
    backend.deadline, backend.uid = 100, 994
    clock = [0]
    attempts = []
    pinned = dict(pid=123, invocation='b' * 32, start_ticks=10, image_sha256='c' * 64)
    def capture(service):
        if attempts and outcome == 'exit':
            raise RuntimeError('exited')
        return dict(pinned, pid=124) if attempts and outcome == 'pid_change' else pinned
    backend.capture_process = capture
    backend.wait_initial_exec = lambda service: pinned
    backend.services = lambda *args, **kwargs: None
    backend.system = SimpleNamespace(_sleep_idle=lambda deadline: None)
    backend.preflight = SimpleNamespace(report=lambda *args, **kwargs: None)
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + 1))
    monkeypatch.setattr(module, 'read', lambda *args: b'{}')
    def endpoint(*args, **kwargs):
        attempts.append(True)
        if len(attempts) < 3 or outcome == 'timeout':
            raise ConnectionRefusedError()
        return {'locked': outcome != 'unlocked'}
    monkeypatch.setattr(module, 'get', endpoint)
    if outcome == 'delayed':
        assert backend.wait_auth_ready() == pinned
        assert len(attempts) == 3
    else:
        with pytest.raises((RuntimeError, TimeoutError)):
            backend.wait_auth_ready()
        assert clock[0] <= 30


def test_auth_wait_handles_real_delayed_loopback_listener(monkeypatch):
    import http.server
    import socket
    import threading
    with socket.socket() as socket_probe:
        socket_probe.bind(('127.0.0.1', 0))
        port = socket_probe.getsockname()[1]
    backend = object.__new__(module.Backend)
    backend.deadline, backend.uid = module.time.monotonic() + 10, 994
    pin = dict(pid=123, invocation='b' * 32)
    backend.capture_process = lambda service: pin
    backend.wait_initial_exec = lambda service: pin
    backend.services = lambda *args, **kwargs: None
    backend.system = SimpleNamespace(_sleep_idle=lambda deadline: None)
    backend.preflight = SimpleNamespace(report=lambda *args, **kwargs: None)
    monkeypatch.setattr(module, 'read', lambda *args: b'{}')
    original_get = module.get
    monkeypatch.setattr(module, 'get', lambda ignored, route, **kwargs: original_get(port, route, **kwargs))
    servers = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"locked":true}')
        def log_message(self, *args):
            pass
    def start_after_refusal(seconds):
        server = http.server.HTTPServer(('127.0.0.1', port), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(module.time, 'sleep', start_after_refusal)
    try:
        assert backend.wait_auth_ready() == pin
        assert len(servers) == 1
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def sleep_fixture(monkeypatch):
    manager = 'UnitPath=/etc/systemd/system /run/systemd/system /run/systemd/transient /lib/systemd/system\n'
    values = {unit: dict(LoadState='loaded', ActiveState='inactive', Job='',
        FragmentPath='/lib/systemd/system/' + unit, UnitFileState='static', DropInPaths='',
        NeedDaemonReload='no', Transient='no') for unit in module.SLEEP_UNITS}
    for unit in module.MASKED_SLEEP_TARGETS:
        values[unit].update(LoadState='masked', FragmentPath='/run/systemd/system/' + unit,
            UnitFileState='masked-runtime')
    calls = []
    class Base:
        def _command(self, args, deadline):
            calls.append(('manager', tuple(args)))
            return manager
        def _show(self, unit, deadline, fields):
            calls.append(('unit', unit))
            return dict(values[unit])
        def _no_job(self, value):
            return value in ('', '0')
        def suspend_verified(self):
            pytest.fail('must never invoke actual suspend')
    monkeypatch.setattr(module, 'runtime_mask_snapshot', lambda unit, paths: (unit, 'stable'))
    return module.b_suspend_system(Base), values, calls


def test_b_masked_sleep_checks_all_nine_twice_and_cannot_suspend(monkeypatch):
    system, values, calls = sleep_fixture(monkeypatch)
    system._sleep_idle(100)
    assert len(values) == 9
    assert all(calls.count(('unit', unit)) == 2 for unit in values)
    with pytest.raises(RuntimeError, match='suspend_forbidden'):
        system.suspend_verified()


@pytest.mark.parametrize('field,value', [('LoadState', 'error'), ('ActiveState', 'active'),
    ('ActiveState', 'deactivating'), ('Job', '12'), ('NeedDaemonReload', 'yes'),
    ('Transient', 'yes'), ('UnitFileState', 'masked'),
    ('FragmentPath', '/etc/systemd/system/sleep.target'), ('DropInPaths', '/etc/override.conf')])
def test_b_sleep_rejects_unsafe_mask_manager_state(monkeypatch, field, value):
    system, values, calls = sleep_fixture(monkeypatch)
    values['sleep.target'][field] = value
    with pytest.raises(RuntimeError):
        system._sleep_idle(100)


@pytest.mark.parametrize('unit', [unit for unit in module.SLEEP_UNITS if unit not in module.MASKED_SLEEP_TARGETS])
def test_b_sleep_rejects_masked_services_and_other_target(monkeypatch, unit):
    system, values, calls = sleep_fixture(monkeypatch)
    values[unit]['LoadState'] = 'masked'
    with pytest.raises(RuntimeError):
        system._sleep_idle(100)


@pytest.mark.parametrize('race', ['link', 'manager', 'unit'])
def test_b_sleep_rechecks_link_and_manager_identities(monkeypatch, race):
    system, values, calls = sleep_fixture(monkeypatch)
    if race == 'link':
        counts = {}
        def snapshot(unit, paths):
            counts[unit] = counts.get(unit, 0) + 1
            return counts[unit]
        monkeypatch.setattr(module, 'runtime_mask_snapshot', snapshot)
    elif race == 'manager':
        original = system._command
        def command(args, deadline):
            raw = original(args, deadline)
            return raw if len([call for call in calls if call[0] == 'manager']) == 1 else raw + ' '
        system._command = command
    else:
        original = system._show
        def show(unit, deadline, fields):
            value = original(unit, deadline, fields)
            if calls.count(('unit', unit)) > 1:
                value['Job'] = '5'
            return value
        system._show = show
    with pytest.raises(RuntimeError):
        system._sleep_idle(100)


@pytest.mark.parametrize('problem', [None, 'persistent', 'wrong-link', 'regular', 'dropin', 'owner', 'parent-mode'])
def test_runtime_mask_real_symlink_layout(tmp_path, monkeypatch, problem):
    import stat
    root = tmp_path / 'root'
    for directory in ('run/systemd/system', 'etc/systemd/system'):
        (root / directory).mkdir(parents=True)
    target = root / 'run/systemd/system/sleep.target'
    if problem == 'regular':
        target.write_bytes(b'')
    else:
        target.symlink_to('/dev/zero' if problem == 'wrong-link' else '/dev/null')
    if problem == 'persistent':
        (root / 'etc/systemd/system/sleep.target').symlink_to('/dev/null')
    if problem == 'dropin':
        (root / 'run/systemd/system/sleep.target.d').mkdir()
    real_lstat = Path.lstat
    def lstat(path):
        info = real_lstat(path)
        fields = {name: getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
            'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
        fields.update(st_uid=0, st_gid=0)
        if problem == 'owner' and path == target:
            fields['st_uid'] = 994
        if problem == 'parent-mode' and path == target.parent:
            fields['st_mode'] |= stat.S_IWGRP
        return SimpleNamespace(**fields)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(module, 'Path', lambda value: Path(value) if str(value).startswith(str(root))
        else root / str(value).lstrip('/'))
    if problem is None:
        assert module.runtime_mask_snapshot('sleep.target', ['/run/systemd/system', '/etc/systemd/system'])
    else:
        with pytest.raises(RuntimeError):
            module.runtime_mask_snapshot('sleep.target', ['/run/systemd/system', '/etc/systemd/system'])


@pytest.mark.parametrize('field,value', [('endpoint', 'http://127.0.0.1:8081'),
    ('endpoint', 'http://localhost:8080'), ('chain_id', 'sage-personal-other'),
    ('agent_id', 'a' * 64), ('identity_file', '/var/lib/other.key'),
    ('voice_uid', 995), ('voice_uid', True), ('unit_id', 'unit-a')])
def test_scan_is_bound_to_exact_independent_b_store(field, value):
    config = scan_config()
    module.validate_scan(config)
    config[field] = value
    with pytest.raises(RuntimeError):
        module.validate_scan(config)


@pytest.mark.parametrize('boot', [None, '', 123, True, 'not-a-uuid',
    '00000000-0000-0000-0000-000000000000', '11111111-1111-4111-8111-111111111111'])
def test_completed_health_requires_new_canonical_nonzero_boot(boot):
    health = dict(boot_id=boot, encrypted=True, sage='running', version='fixture', vault_locked=True)
    with pytest.raises(RuntimeError):
        module.validate_health(health, '11111111-1111-4111-8111-111111111111', 'fixture', True)


def test_completed_health_accepts_only_locked_changed_boot():
    health = dict(boot_id='22222222-2222-4222-8222-222222222222', encrypted=True,
        sage='running', version='fixture', vault_locked=True)
    module.validate_health(health, '11111111-1111-4111-8111-111111111111', 'fixture', True)
    health['vault_locked'] = False
    with pytest.raises(RuntimeError):
        module.validate_health(health, '11111111-1111-4111-8111-111111111111', 'fixture', True)


@pytest.mark.parametrize('action', ['stop', 'start'])
@pytest.mark.parametrize('service', [module.SAGE, *module.COLD, 'reboot.target'])
def test_systemd_cannot_stop_or_start_native_or_audio(action, service, monkeypatch):
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must not execute'))
    with pytest.raises(RuntimeError):
        module.systemctl(action, service)


def test_unknown_stop_propagates_no_retry(monkeypatch):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        raise TimeoutError('unknown')
    monkeypatch.setattr(module.subprocess, 'run', run)
    with pytest.raises(TimeoutError):
        module.systemctl('stop', module.AUTH)
    assert len(calls) == 1


def test_release_requires_real_complete_phase(tmp_path, monkeypatch):
    backend = object.__new__(module.Backend)
    backend.state = tmp_path
    (tmp_path / 'transition.jsonl').write_text('{"phase":"swapped"}\n')
    monkeypatch.setattr(module, 'read', lambda path, *args, **kwargs: Path(path).read_bytes())
    monkeypatch.setattr(module, 'systemctl', lambda *args, **kwargs: pytest.fail('must not start'))
    with pytest.raises(RuntimeError):
        backend.release()


@pytest.mark.parametrize('fault', [None, 'auth_start', 'ui_start', 'final_check'])
def test_release_retains_cold_fences_and_contains_partial_start(tmp_path, monkeypatch, fault):
    backend = object.__new__(module.Backend)
    backend.state = tmp_path
    backend.uid = 994
    backend.released = set()
    backend.guards = {service: tmp_path / (str(index) + '.guard') for index, service in enumerate(module.SERVICES)}
    for path in backend.guards.values():
        path.write_bytes(b'guard')
    backend.expected = dict(boot_id='boot', services={module.AUTH: {'invocation': 'a' * 32}})
    (tmp_path / 'transition.jsonl').write_text('{"phase":"complete_locked_guards_retained"}\n')
    (tmp_path / 'complete.json').write_text('{"native_boot":"new","boot_id":"boot"}')
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / ('b' * 32 + '.json')).write_text('{}')
    backend.preflight = SimpleNamespace(report=lambda *args, **kwargs: None)
    def write(path, raw, *args):
        with Path(path).open('xb') as stream:
            stream.write(raw)
    backend.io = SimpleNamespace(write_new=write, sync_directory=lambda path: None)
    stages = []
    backend.record = stages.append
    checks = []
    def check(*args, **kwargs):
        checks.append(kwargs)
        if kwargs.get('fresh') and module.UI in kwargs['fresh'] and fault == 'final_check':
            raise RuntimeError('cold/native final check failed')
        return {'boot_id': 'new'}
    backend.check = check
    backend.properties = lambda service: {'ActiveState': 'active', 'MainPID': '123', 'InvocationID': 'b' * 32}
    backend.capture_process = lambda service: {'fixture': service}
    backend.wait_auth_ready = lambda: {'fixture': module.AUTH}
    backend.wait_ui_ready = lambda auth: {'fixture': module.UI}
    cleanup = []
    backend.contain_release_failure = lambda: cleanup.append('contain') or True
    commands = []
    def systemctl(*args, **kwargs):
        commands.append(args)
        if args == ('start', module.AUTH) and fault == 'auth_start':
            raise TimeoutError('auth unknown')
        if args == ('start', module.UI) and fault == 'ui_start':
            raise TimeoutError('ui unknown')
    monkeypatch.setattr(module, 'systemctl', systemctl)
    monkeypatch.setattr(module, 'get', lambda *args: {'locked': True})
    monkeypatch.setattr(module, 'read', lambda path, *args, **kwargs: Path(path).read_bytes())
    monkeypatch.setattr(module, 'REPORTS', reports)
    if fault:
        with pytest.raises((RuntimeError, TimeoutError)):
            backend.release()
        assert cleanup == ['contain']
        assert not (tmp_path / 'released.json').exists()
    else:
        assert backend.release()['cold_guards_retained'] is True
        assert checks[-1]['guarded'] == module.RETAINED
        assert set(checks[-1]['fresh']) == {module.AUTH, module.UI}
    for service in module.RETAINED:
        assert backend.guards[service].read_bytes() == b'guard'
    assert all(command[1] in (module.AUTH, module.UI) for command in commands if command[0] == 'start')


def test_cleanup_does_not_skip_stops_when_guard_restore_fails(tmp_path, monkeypatch):
    backend = object.__new__(module.Backend)
    backend.guards = {service: tmp_path / service for service in (module.AUTH, module.UI)}
    backend.released = {module.AUTH, module.UI}
    backend.guard = b'guard'
    backend.policy = SimpleNamespace(directory=lambda path, owners: os.open(path, os.O_RDONLY | os.O_DIRECTORY))
    backend.io = SimpleNamespace(write_new=lambda *args: (_ for _ in ()).throw(OSError('restore failed')))
    backend.properties = lambda service: {'MainPID': '100'}
    backend.capture_process = lambda service: {'pid': 100}
    backend.files = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('verification failed'))
    commands = []
    monkeypatch.setattr(module, 'systemctl', lambda *args, **kwargs: commands.append(args))
    assert backend.contain_release_failure() is False
    assert ('stop', module.UI) in commands and ('stop', module.AUTH) in commands


@pytest.mark.parametrize('drained,fault', [(False, 'changed'), (True, 'unknown'), (True, 'false_drained')])
def test_auth_receipt_never_inferred_from_inactive(tmp_path, monkeypatch, drained, fault):
    spec = importlib.util.spec_from_file_location('auth_report_contract',
        Path(__file__).resolve().parents[1] / 'lantern/preflight-fresh-native-update.py')
    source = Path(spec.origin).read_text()
    start = source.index('def report(')
    end = source.index('\ndef governance', start)
    scope = {'require': module.require}
    exec(source[start:end], scope)
    backend = object.__new__(module.Backend)
    backend.uid = 994
    backend.preflight = SimpleNamespace(report=scope['report'])
    invocation = 'a' * 32
    name = invocation + '.json'
    value = dict(version=1, instance_id=invocation, closing=drained, drained=drained,
        local_revoked=drained, upstream_cleanup='confirmed' if drained else 'unknown')
    raw = json.dumps(value).encode()
    backend.expected = dict(services={module.AUTH: {'invocation': invocation}}, auth_reports={name: module.digest(raw)})
    if fault == 'changed':
        raw += b'\n'
    elif fault == 'unknown':
        value['upstream_cleanup'] = 'unknown'
        raw = json.dumps(value).encode()
    else:
        value['drained'] = False
        raw = json.dumps(value).encode()
    (tmp_path / name).write_bytes(raw)
    monkeypatch.setattr(module, 'REPORTS', tmp_path)
    monkeypatch.setattr(module, 'read', lambda path, *args, **kwargs: Path(path).read_bytes())
    with pytest.raises(RuntimeError):
        backend.reports(drained)


def test_partial_release_still_requires_loaded_effective_guard(monkeypatch):
    backend = object.__new__(module.Backend)
    service = module.COLD[0]
    fragment = '/etc/systemd/system/' + service
    backend.expected = dict(boot_id='fixture', services={service: {'pid': 0}}, files={fragment: 'pin'})
    backend.guards = {service: Path(fragment + '.d/test.conf')}
    backend.released = {service}
    backend.state = Path('/var/lib/fixture')
    backend.guard = b'guard'
    backend.deadline = 1
    checks = []
    backend.system = SimpleNamespace(_guard=lambda *args: checks.append(args))
    props = dict(FragmentPath=fragment, NeedDaemonReload='yes', Job='', KillMode='control-group',
        Slice='system.slice', DropInPaths=str(backend.guards[service]), Restart='no',
        ActiveState='inactive', MainPID='0', ControlGroup='', LoadState='loaded', Transient='no')
    backend.properties = lambda name: props
    monkeypatch.setattr(Path, 'read_text', lambda *args, **kwargs: 'fixture')
    monkeypatch.setattr(Path, 'exists', lambda path: False)
    monkeypatch.setattr(module, 'read', lambda path: b'guard')
    backend.services(guarded=True)
    assert len(checks) == 1
    backend.services(guarded=module.RETAINED)
    props['DropInPaths'] = ''
    with pytest.raises(RuntimeError):
        backend.services(guarded=True)
    props['DropInPaths'] = str(backend.guards[service])
    monkeypatch.setattr(Path, 'exists', lambda path: True)
    monkeypatch.setattr(Path, 'rglob', lambda path, pattern: iter(()))
    monkeypatch.setattr(Path, 'read_text', lambda path, *args, **kwargs:
        'fixture' if str(path).endswith('/boot_id') else '123\n')
    with pytest.raises(RuntimeError):
        backend.services(guarded=module.RETAINED)


@pytest.mark.parametrize('fault', [None, 'drain_unknown', 'request_unknown', 'partial_guard', 'absent_idle'])
def test_apply_retains_real_files_and_never_retries(tmp_path, monkeypatch, fault):
    backend = object.__new__(module.Backend)
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'home').mkdir()
    native = tmp_path / 'bin/native'
    config = tmp_path / 'home/config.yaml'
    native.write_bytes(b'old-native')
    config.write_bytes(b'original-config')
    candidate = tmp_path / 'new-native'
    candidate.write_bytes(b'new-native')
    manifest = tmp_path / 'old-manifest'
    manifest.write_bytes(b'manifest')
    initialized = b'fixture-initialized'
    backend.expected = dict(before={'binary_sha256': module.digest(b'old-native'), 'manifest': str(manifest)},
        after={'binary': str(candidate)}, transition_id='fixture', boot_id='fixture-boot')
    expected = tmp_path / 'expected.json'
    expected.write_text(json.dumps(backend.expected))
    backend.state = tmp_path / 'state'
    backend.uid = 994
    backend.deadline = module.time.monotonic() + 10
    backend.guards = {name: tmp_path / 'guards' / (str(index) + '.conf') for index, name in enumerate(module.SERVICES)}
    if fault == 'absent_idle':
        backend.guards.pop(module.IDLE)
    backend.guard = b'guard'
    backend.guard_native = b'native-guard'
    commands = []
    requests = []
    def read(path, *args, **kwargs):
        if str(path).endswith('/agent.key') or str(path).endswith('/lantern-agent.key'):
            return b'k' * 32
        if str(path) == '/etc/sage-lantern/initialized-public.json':
            return initialized
        return Path(path).read_bytes()
    def write(path, raw, mode=0o600):
        if fault == 'partial_guard' and path == backend.guards[module.AUTH]:
            raise OSError('fixture partial write')
        with Path(path).open('xb') as stream:
            stream.write(raw)
        Path(path).chmod(mode)
    backend.io = SimpleNamespace(write_new=write, sync_directory=lambda path: None)
    backend.policy = SimpleNamespace(corrected=lambda raw, receipt: b'corrected-config',
        directory=lambda path, owners: os.open(path, os.O_RDONLY | os.O_DIRECTORY))
    backend.reports = lambda drained=False: b'drained' if drained else b'current-auth-before'
    def record(phase):
        with (backend.state / 'transition.jsonl').open('a') as stream:
            stream.write(json.dumps({'phase': phase}) + '\n')
    backend.record = record
    def check(stopped=(), **kwargs):
        if fault == 'drain_unknown' and module.AUTH in stopped:
            raise RuntimeError('unknown drain')
        return {'boot_id': 'new-native-boot'}
    backend.check = check
    backend.mesh = SimpleNamespace(b_empty_workflows=lambda config, epoch, guard: guard())
    backend.expected.update(scan_config={}, storage_epoch={})
    expected.write_text(json.dumps(backend.expected))
    def request(*args, **kwargs):
        requests.append(args)
        if fault == 'request_unknown':
            raise TimeoutError('delivery unknown')
        return SimpleNamespace(returncode=0, stdout=b'{"accepted":true,"completed":false}')
    monkeypatch.setattr(module, 'BINARY', native)
    monkeypatch.setattr(module, 'CONFIG', config)
    monkeypatch.setattr(module, 'read', read)
    monkeypatch.setattr(module, 'get', lambda *args: {'locked': False})
    monkeypatch.setattr(module, 'systemctl', lambda *args, **kwargs: commands.append(args))
    monkeypatch.setattr(module.subprocess, 'run', request)
    monkeypatch.setattr(module.os, 'chown', lambda *args: None)
    monkeypatch.setattr(module.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_gid=os.getgid()))
    if fault not in (None, 'absent_idle'):
        with pytest.raises((RuntimeError, TimeoutError, OSError)):
            backend.apply(str(expected), module.digest(expected.read_bytes()))
        assert not (backend.state / 'complete.json').exists()
    else:
        assert backend.apply(str(expected), module.digest(expected.read_bytes()))['completed'] is True
    assert (backend.state / 'before.yaml').read_bytes() == b'original-config'
    assert (backend.state / 'auth-before.json').read_bytes() == b'current-auth-before'
    assert len(requests) == (0 if fault in ('drain_unknown', 'partial_guard') else 1)
    assert native.read_bytes() == (b'old-native' if fault in ('drain_unknown', 'partial_guard') else b'new-native')
    assert any(path.exists() for path in backend.guards.values())
    assert all(not (command[0] in ('stop', 'start') and command[1] in (module.SAGE, *module.COLD)) for command in commands)
    assert (('stop', module.IDLE) in commands) is (fault != 'absent_idle' and fault != 'partial_guard')


def test_absent_idle_pin_is_explicit_and_exclusive():
    pin = dict(absent=True, pid=0, start_ticks=0, invocation='', image_sha256='')
    assert module.absent_idle_pin(module.IDLE, pin) is True
    assert module.absent_idle_pin(module.IDLE, {key: value for key, value in pin.items() if key != 'absent'}) is False
    for service in (module.AUTH, module.UI, module.SAGE, *module.COLD):
        with pytest.raises(RuntimeError):
            module.absent_idle_pin(service, pin)
    for field, value in [('absent', False), ('pid', False), ('pid', 1), ('start_ticks', 1), ('invocation', 'a' * 32)]:
        changed = dict(pin)
        changed[field] = value
        with pytest.raises(RuntimeError):
            module.absent_idle_pin(module.IDLE, changed)


def idle_fixture(tmp_path, monkeypatch):
    backend = object.__new__(module.Backend)
    value = dict(LoadState='not-found', ActiveState='inactive', MainPID='0', InvocationID='',
        FragmentPath='', DropInPaths='', ControlGroup='', Transient='no', NeedDaemonReload='no', Job='')
    backend.properties = lambda service: value.copy()
    paths = 'UnitPath=/etc/systemd/system /run/systemd/system /run/systemd/transient /run/systemd/generator\n'
    monkeypatch.setattr(module, 'systemctl', lambda *args: paths)
    monkeypatch.setattr(module, 'missing', lambda path: True)
    proc = tmp_path / '123'
    proc.mkdir()
    (proc / 'cgroup').write_text('0::/system.slice/unrelated.service\n')
    (proc / 'cmdline').write_bytes(b'/usr/bin/python3\0unrelated.py\0')
    original = Path.iterdir
    monkeypatch.setattr(Path, 'iterdir', lambda path: iter((proc,)) if str(path) == '/proc' else original(path))
    return backend, value, proc


@pytest.mark.parametrize('field,value', [('LoadState', 'loaded'), ('LoadState', 'masked'),
    ('ActiveState', 'active'), ('MainPID', '123'), ('InvocationID', 'a' * 32),
    ('FragmentPath', '/run/systemd/transient/idle.service'), ('DropInPaths', '/run/dropin.conf'),
    ('ControlGroup', '/system.slice/idle'), ('Transient', 'yes'), ('Job', '123'),
    ('NeedDaemonReload', 'yes')])
def test_absent_idle_rejects_unknown_or_loaded_state(tmp_path, monkeypatch, field, value):
    backend, observed, proc = idle_fixture(tmp_path, monkeypatch)
    backend.absent_idle(observed)
    observed[field] = value
    with pytest.raises(RuntimeError):
        backend.absent_idle(observed)


@pytest.mark.parametrize('fault', ['runtime_unit', 'dropin', 'cgroup', 'process_group', 'process_command',
    'permission_unknown', 'job_race', 'paths_race'])
def test_absent_idle_rejects_files_processes_and_races(tmp_path, monkeypatch, fault):
    backend, value, proc = idle_fixture(tmp_path, monkeypatch)
    if fault in ('runtime_unit', 'dropin', 'cgroup'):
        forbidden = {'runtime_unit': '/run/systemd/transient/' + module.IDLE,
            'dropin': '/run/systemd/system/' + module.IDLE + '.d',
            'cgroup': '/sys/fs/cgroup/system.slice/' + module.IDLE}[fault]
        monkeypatch.setattr(module, 'missing', lambda path: str(path) != forbidden)
    elif fault == 'process_group':
        (proc / 'cgroup').write_text('0::/other.slice/' + module.IDLE + '\n')
    elif fault == 'process_command':
        (proc / 'cmdline').write_bytes(b'python\0/usr/local/libexec/sage-lantern-idle-suspend\0')
    elif fault == 'permission_unknown':
        monkeypatch.setattr(module, 'missing', lambda path: (_ for _ in ()).throw(PermissionError('unknown')))
    elif fault == 'job_race':
        backend.properties = lambda service: dict(value, Job='1')
    else:
        responses = iter(['UnitPath=/etc/systemd/system /run/systemd/system /run/systemd/transient\n',
            'UnitPath=/etc/systemd/system /run/systemd/system /run/systemd/transient /new\n'])
        monkeypatch.setattr(module, 'systemctl', lambda *args: next(responses))
    with pytest.raises((RuntimeError, PermissionError)):
        backend.absent_idle(value)
