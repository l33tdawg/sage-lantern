import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('maintenance', ROOT / 'scripts/lantern/prepare-runtime-maintenance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def receipt(**changes):
    value = dict(version=1, instance_id='a' * 32, closing=True, drained=True,
                 local_revoked=True, upstream_cleanup='confirmed')
    value.update(changes)
    return json.dumps(value).encode()


@pytest.mark.parametrize('changes', [{}, {'version': True}, {'instance_id': 'b' * 32},
    {'closing': False}, {'drained': False}, {'local_revoked': False},
    {'upstream_cleanup': 'unknown'}, {'drained': 1}, {'extra': True}])
def test_only_exact_current_positive_receipt(changes):
    if changes:
        with pytest.raises(ValueError):
            MODULE.current_auth_confirmed(receipt(**changes), 'a' * 32, json.loads)
    else:
        assert MODULE.current_auth_confirmed(receipt(), 'a' * 32, json.loads)['drained'] is True


@pytest.fixture
def scoped_receipt(tmp_path, monkeypatch):
    root = tmp_path / 'service'
    directory = root / 'device-lock' / 'drain'
    directory.mkdir(parents=True)
    for path in (root, root / 'device-lock', directory):
        path.chmod(0o700)
    path = directory / ('a' * 32 + '.json')
    path.write_bytes(receipt())
    path.chmod(0o600)
    monkeypatch.setattr(MODULE, 'RECEIPTS', directory)
    monkeypatch.setattr(MODULE, 'SERVICE_PARENTS', {root, root / 'device-lock', directory})
    real_fstat = os.fstat
    real_lstat = Path.lstat
    real_stat = os.stat
    def adjust(info):
        fields = {name: getattr(info, name) for name in MODULE.FILE_FIELDS}
        if stat.S_ISDIR(info.st_mode) and info.st_ino not in directory_inodes:
            fields.update(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        elif info.st_uid == os.getuid():
            fields['st_uid'] = 994
        return types.SimpleNamespace(**fields)
    directory_inodes = {real_lstat(parent).st_ino for parent in MODULE.SERVICE_PARENTS}
    monkeypatch.setattr(MODULE.os, 'fstat', lambda descriptor: adjust(real_fstat(descriptor)))
    monkeypatch.setattr(Path, 'lstat', lambda self: adjust(real_lstat(self)))
    monkeypatch.setattr(MODULE.os, 'stat', lambda *args, **kwargs: adjust(real_stat(*args, **kwargs)))
    pins = {str(parent): MODULE.directory_identity(parent.lstat()) for parent in MODULE.SERVICE_PARENTS}
    return path, pins


def test_service_owned_receipt_real_descriptor_read(scoped_receipt):
    path, pins = scoped_receipt
    assert MODULE.read_receipt(path, 994, pins) == receipt()


@pytest.mark.parametrize('fault', ['wrong_uid', 'wrong_parent', 'permissions', 'symlink', 'hardlink', 'empty'])
def test_scoped_receipt_rejects_mutations(scoped_receipt, fault):
    path, pins = scoped_receipt
    uid = 995 if fault == 'wrong_uid' else 994
    if fault == 'wrong_parent':
        pins[str(path.parent)]['st_ino'] += 1
    elif fault == 'permissions':
        os.chmod(path, 0o644)
    elif fault == 'symlink':
        original = path.with_suffix('.before')
        path.rename(original)
        path.symlink_to(original.name)
    elif fault == 'hardlink':
        os.link(path, path.with_suffix('.link'))
    elif fault == 'empty':
        path.write_bytes(b'')
    with pytest.raises((ValueError, OSError)):
        MODULE.read_receipt(path, uid, pins)


def test_receipt_read_does_not_accept_just_any_service_path(scoped_receipt):
    path, pins = scoped_receipt
    with pytest.raises(ValueError):
        MODULE.read_receipt(path.parent / 'token', 994, pins)


def test_atime_is_not_file_identity():
    before = types.SimpleNamespace(**{name: 1 for name in MODULE.FILE_FIELDS}, st_atime_ns=1)
    after = types.SimpleNamespace(**{name: 1 for name in MODULE.FILE_FIELDS}, st_atime_ns=2)
    assert MODULE.file_identity(before) == MODULE.file_identity(after)
    after.st_ctime_ns += 1
    assert MODULE.file_identity(before) != MODULE.file_identity(after)


class HandoffBackend:
    replacement_phase = 'runtime_replace'

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail
        self.auth_stopped = False

    def invoke(self, name, *arguments):
        self.calls.append((name, arguments))
        if name == self.fail:
            raise ValueError('injected')
        if name == 'stop_auth':
            self.auth_stopped = True
        if name in ('current', 'reconcile'):
            assert not self.auth_stopped
        if name == 'current_after_auth':
            assert self.auth_stopped
        if name == 'reconcile':
            return dict(storage_epoch='instance:9', workflow_sha256='a' * 64)

    def __getattr__(self, name):
        return lambda *args: self.invoke(name, *args)


def test_handoff_order_no_old_access_checks_after_auth_stop():
    backend = HandoffBackend()
    result = MODULE.handoff(backend)
    names = [name for name, arguments in backend.calls]
    assert names.index('freeze_handoff') < names.index('stop_auth')
    assert names.index('confirm_current_auth_receipt') < names.index('stop_importer')
    assert names.index('verify_all_importers_empty') < names.index('install_pinned_runtime')
    assert names.index('verify_installed_runtime') < names.index('restore_auth_locked')
    assert names.index('restore_auth_locked') < names.index('restore_basic_locked')
    assert result['audio_activated'] is False and result['historical_drain_verified'] is False
    assert not any(name in names for name in ('drain', 'restart_native', 'close_vault', 'bootstrap'))


@pytest.mark.parametrize('failure', ['reconcile', 'record', 'freeze_handoff', 'inhibit_auth',
    'stop_auth', 'current_after_auth', 'confirm_current_auth_receipt', 'stop_importer',
    'verify_all_importers_empty', 'install_pinned_runtime', 'verify_installed_runtime',
    'restore_auth_locked'])
def test_handoff_failure_never_advances_or_rolls_back(failure):
    backend = HandoffBackend(fail=failure)
    with pytest.raises(ValueError):
        MODULE.handoff(backend)
    names = [name for name, arguments in backend.calls]
    assert names[-1] == failure
    assert 'restore_basic_locked' not in names


@pytest.mark.parametrize('extra', [[], ['--approve-microphone-capture', '--wait-owner-seconds', '0'],
    ['--approve-microphone-capture', '--wait-owner-seconds', '301']])
def test_unapproved_or_invalid_apply_cannot_load_target(monkeypatch, extra):
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE, 'load', lambda *args: pytest.fail('must reject before loading'))
    with pytest.raises(ValueError):
        MODULE.main(['--expected', '/unused', '--expected-sha256', 'a' * 64,
            '--helper', '/unused-helper', '--helper-sha256', 'b' * 64, '--apply', *extra])


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('candidate', [None, MODULE.COMPACT_PROMPT_V1, MODULE.OWNER_LEDGERS_V2])
def test_cli_dispatch_preserves_readonly_default_and_approved_apply(monkeypatch, tmp_path, capsys, apply, candidate):
    import types
    calls = []
    pin = dict(path='/pinned-helper', sha256='b' * 64)
    expected = {'helper': pin}
    helper = types.SimpleNamespace(pinned=lambda supplied: b'expected', decode=lambda raw: expected)
    backend = types.SimpleNamespace(reconcile=lambda: calls.append('reconcile'),
                                    close=lambda: calls.append('close'))
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE, 'STATE', tmp_path / 'unused-attempt')
    monkeypatch.setattr(MODULE, 'load', lambda path, digest: helper)
    def construct(supplied, *, candidate_sha256):
        assert supplied is expected
        assert candidate_sha256 == (candidate or MODULE.RETRIEVAL_V1)
        return backend
    monkeypatch.setattr(MODULE, 'Maintenance', construct)
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    def prepare(supplied, *, approved_capture, wait_seconds):
        assert supplied is backend and approved_capture is True and wait_seconds == 120
        calls.append('apply')
        return {'fixture_apply': True}
    monkeypatch.setattr(MODULE, 'prepare', prepare)
    args = ['--expected', '/pinned-expected', '--expected-sha256', 'a' * 64,
            '--helper', pin['path'], '--helper-sha256', pin['sha256']]
    if apply:
        args += ['--apply', '--approve-microphone-capture']
    if candidate:
        args += ['--candidate-sha256', candidate]
    MODULE.main(args)
    output = json.loads(capsys.readouterr().out)
    assert calls == (['apply', 'close'] if apply else ['reconcile', 'close'])
    assert output == ({'fixture_apply': True} if apply else
        {'preflight': True, 'replacement_authorized': False, 'reconciliation': None})


def test_no_fake_historical_waiver_in_source():
    source = Path(MODULE.__file__).read_text()
    assert 'verify_root_broker(' not in source
    assert 'root_control(' not in source
    assert 'supersession' not in source


@pytest.mark.parametrize('fault', [None, 'historical_changed', 'current_not_confirmed', 'new_unknown', 'final_changed'])
def test_post_auth_preserves_unknown_history_requires_only_current_confirmation(tmp_path, monkeypatch, fault):
    current = tmp_path / ('a' * 32 + '.json')
    historical = tmp_path / ('b' * 32 + '.json')
    initial = receipt(closing=False, drained=False, local_revoked=False, upstream_cleanup='unknown')
    historical_raw = receipt(instance_id='b' * 32, closing=False, drained=False,
                             local_revoked=False, upstream_cleanup='unknown')
    current.write_bytes(receipt())
    historical.write_bytes(historical_raw)
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    backend.expected = dict(receipts={str(current): sha(initial), str(historical): sha(historical_raw)},
        receipt_parents={}, services={MODULE.AUTH: dict(process=dict(uid=994), unit=dict(InvocationID='a' * 32))})
    backend.post_auth = True
    backend.auth_final_sha256 = sha(receipt())
    backend.new_auth_receipt = None
    backend.helper = types.SimpleNamespace(sha=sha, decode=json.loads)
    monkeypatch.setattr(MODULE, 'RECEIPTS', tmp_path)
    monkeypatch.setattr(MODULE, 'read_receipt', lambda name, uid, parents: Path(name).read_bytes())
    if fault == 'historical_changed':
        historical.write_bytes(receipt(instance_id='b' * 32))
    elif fault == 'current_not_confirmed':
        current.write_bytes(initial)
    elif fault == 'new_unknown':
        (tmp_path / ('c' * 32 + '.json')).write_bytes(initial)
    elif fault == 'final_changed':
        backend.auth_final_sha256 = 'e' * 64
    if fault:
        with pytest.raises(ValueError):
            backend.receipts_current()
    else:
        backend.receipts_current()
        assert historical.read_bytes() == historical_raw


@pytest.mark.parametrize('fault', [None, 'wrong_user', 'not_dynamic', 'root_uid', 'same_invocation', 'argv'])
def test_camera_restore_uses_bound_dynamic_unit_not_nss_or_old_uid(fault):
    service = 'sage-lantern-camera.service'
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    original = dict(uid=63957, executable='/usr/bin/python3.11', argv_sha256='b' * 64)
    backend.expected = dict(services={service: dict(process=original, unit=dict(InvocationID='a' * 32))})
    unit = dict(User='sage-lantern-camera', DynamicUser='yes')
    process = dict(original, uid=64001)
    value = dict(InvocationID='c' * 32)
    if fault == 'wrong_user':
        unit['User'] = 'foreign'
    elif fault == 'not_dynamic':
        unit['DynamicUser'] = 'no'
    elif fault == 'root_uid':
        process['uid'] = 0
    elif fault == 'same_invocation':
        value['InvocationID'] = 'a' * 32
    elif fault == 'argv':
        process['argv_sha256'] = 'e' * 64
    backend.system = types.SimpleNamespace(clock=lambda: 0, _show=lambda *args: unit)
    if fault:
        with pytest.raises(ValueError):
            backend.restored_identity(service, value, process)
    else:
        backend.restored_identity(service, value, process)


def test_non_camera_uid_change_is_not_allowed():
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    process = dict(uid=994, executable='/usr/bin/python3.11', argv_sha256='a' * 64)
    backend.expected = dict(services={MODULE.AUTH: dict(process=process, unit=dict(InvocationID='b' * 32))})
    with pytest.raises(ValueError):
        backend.restored_identity(MODULE.AUTH, dict(InvocationID='c' * 32), dict(process, uid=995))


def test_post_auth_checks_do_not_invoke_old_access_or_signed_work_scan():
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    backend.post_auth = True
    backend.stopped = {MODULE.AUTH}
    backend.running = {}
    calls = []
    backend.current = lambda: calls.append('bounded_current')
    backend.reconcile = lambda: pytest.fail('old unlocked workflow scan must not run')
    backend.current_after_auth()
    assert calls == ['bounded_current']


def test_initial_importer_exclusion_only_adopts_pinned_python_services(monkeypatch):
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    backend.self_identity = dict(start_ticks=1, exe_dev=2, exe_ino=3)
    backend.expected = {'services': {service: {'process': dict(
        pid=100 + index, executable='/usr/bin/python3' if index == 0 else '/usr/bin/native',
        start_ticks=20 + index, exe_dev=2, exe_ino=30 + index)}
        for index, service in enumerate(MODULE.SERVICES)}}
    calls = []
    def inventory(allowed):
        calls.append(dict(allowed))
        return allowed
    monkeypatch.setattr(MODULE, 'importer_inventory', inventory)
    backend.exclude_importers(initial=True)
    assert calls == [{os.getpid(): backend.self_identity,
                      100: dict(start_ticks=20, exe_dev=2, exe_ino=30)}] * 2
    calls.clear()
    backend.exclude_importers()
    assert calls == [{os.getpid(): backend.self_identity}] * 2


def test_install_cannot_begin_with_any_importer_missing():
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    backend.stopped = set(MODULE.STOP + (MODULE.AUTH,))
    backend.auth_final_sha256 = 'a' * 64
    backend.current_after_auth = lambda: pytest.fail('must reject missing importers first')
    with pytest.raises(ValueError):
        backend.verify_all_importers_empty()


def fake_proc(root, pid, executable, ticks=123, arguments=b'python\0-c\0pass\0'):
    directory = root / str(pid)
    directory.mkdir()
    fields = ['S'] + ['0'] * 18 + [str(ticks)] + ['0'] * 8
    (directory / 'stat').write_text(str(pid) + ' (worker) ' + ' '.join(fields))
    (directory / 'cmdline').write_bytes(arguments)
    (directory / 'exe').symlink_to(executable)
    info = executable.stat()
    return dict(start_ticks=ticks, exe_dev=info.st_dev, exe_ino=info.st_ino)


@pytest.mark.parametrize('fault', [None, 'unknown_python', 'venv_script', 'reused_pid', 'missing_allowed'])
def test_importer_exclusion_is_not_just_known_units(tmp_path, fault):
    executable = tmp_path / 'python3.11'
    executable.write_bytes(b'fixture-image')
    proc = tmp_path / 'proc'
    proc.mkdir()
    pin = fake_proc(proc, 10, executable)
    allowed = {10: pin}
    if fault == 'unknown_python':
        fake_proc(proc, 11, executable)
    elif fault == 'venv_script':
        shell = tmp_path / 'sh'
        shell.write_bytes(b'shell')
        fake_proc(proc, 11, shell, arguments=b'/bin/sh\0/opt/sage-lantern/venv/bin/worker\0')
    elif fault == 'reused_pid':
        allowed[10] = dict(pin, start_ticks=124)
    elif fault == 'missing_allowed':
        allowed[11] = pin
    if fault:
        with pytest.raises(ValueError):
            MODULE.importer_inventory(allowed, proc=proc)
    else:
        assert MODULE.importer_inventory(allowed, proc=proc) == allowed


@pytest.mark.parametrize('populated', ['0', '1'])
def test_absent_extra_unit_is_not_created_but_cgroup_is_checked(tmp_path, monkeypatch, populated):
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    service = MODULE.EXTRA[0]
    unit = dict(LoadState='not-found', ActiveState='inactive', Job='')
    group = tmp_path / 'cgroup'
    group.mkdir()
    (group / 'cgroup.events').write_text('populated ' + populated + '\n')
    backend.expected = dict(extra_services={service: unit})
    backend.guarded = set()
    backend.guard_path = lambda name: tmp_path / 'never-created'
    backend.system = types.SimpleNamespace(clock=lambda: 0, _show=lambda *args: unit,
        _no_job=lambda job: not job, _cgroup=lambda path: group, _empty_tree=lambda *args: None)
    if populated == '1':
        with pytest.raises(ValueError):
            backend.extra_current(service)
    else:
        backend.extra_current(service)
    assert not backend.guard_path(service).exists()


@pytest.mark.parametrize('journal_failure', [False, True])
def test_actual_restore_failure_reinhibits_and_stops_started_host_process(tmp_path, monkeypatch, journal_failure):
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    service = MODULE.AUTH
    guard = tmp_path / 'maintenance.conf'
    group = tmp_path / 'group'
    group.mkdir()
    boot = '2701621b-86ee-4302-8a90-98f70cff9046'
    real_read_text = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda path, *args, **kwargs:
        boot if str(path) == '/proc/sys/kernel/random/boot_id' else real_read_text(path, *args, **kwargs))
    old = dict(FragmentPath='/etc/systemd/system/' + service, DropInPaths='', InvocationID='a' * 32)
    expected_process = dict(argv_sha256='a' * 64, executable=sys.executable, uid=os.getuid())
    backend.expected = dict(boot_id=boot, services={service: dict(unit=old, process=expected_process)})
    backend.installed = True
    backend.guarded = {service}
    backend.stopped = {service}
    backend.running = {}
    backend.restore_attempted = set()
    backend.file_metadata = {}
    backend.guard_path = lambda name: guard
    backend.current_after_auth = lambda: None
    records = []
    def record(phase, details=None):
        if journal_failure and phase.startswith('restore_failure'):
            raise OSError('journal-disk-fault')
        records.append((phase, details))
    backend.record = record
    backend.helper = types.SimpleNamespace(public=lambda path: path.read_bytes(),
        write=lambda path, raw: path.write_bytes(raw), sync=lambda path: None)
    guard.write_bytes(backend.guard())
    child = None
    def command(arguments, deadline):
        nonlocal child
        if arguments[0] == 'start':
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(30)'])
            (group / 'cgroup.events').write_text('populated 1\n')
        elif arguments[0] == 'stop':
            assert guard.read_bytes() == backend.guard()
            child.terminate()
            child.wait(timeout=5)


            (group / 'cgroup.events').write_text('populated 0\n')
    def show(name):
        active = child is not None and child.poll() is None
        return dict(old, ActiveState='active' if active else 'inactive', MainPID=str(child.pid) if active else '0',
            InvocationID='b' * 32, Job='', ControlGroup='/system.slice/' + service if active else '',
            LoadState='loaded', NeedDaemonReload='no', Restart='no' if guard.exists() else 'always',
            DropInPaths=str(guard) if guard.exists() else '', KillMode='control-group', Slice='system.slice',
            RuntimeDirectoryPreserve='yes')
    def empty(*args):
        assert child.poll() is not None
    backend.system = types.SimpleNamespace(clock=time.monotonic, _command=command, _no_job=lambda job: not job,
        _guard=lambda *args: MODULE.require(guard.exists()), _cgroup=lambda name: group, _empty_tree=empty)
    backend.show = show
    backend.process = lambda value: dict(expected_process, pid=child.pid)
    backend.starting_process = lambda service, value, observed, deadline: (backend.process(value), (child.pid, 1, 'b' * 32, True))
    backend.restored_identity = lambda *args: None
    def locked():
        assert child.poll() is None
        raise ValueError('auth-is-not-locked')
    backend.locked = locked
    try:
        with pytest.raises(ValueError, match='auth-is-not-locked'):
            backend.restore(service)
        assert child is not None and child.poll() is not None
        assert guard.read_bytes() == backend.guard() and service in backend.guarded
        assert service not in backend.running
        if not journal_failure:
            assert records[-1][1] == dict(guards_verified=True, containment_verified=True, shutdown_outcome='unknown')
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize('fault', [None, 'history_unknown', 'http_unknown', 'stale_transition', 'stale_instance'])
def test_voice_local_uncertainty_is_never_reset(fault):
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    instance = dict(instance_id='3701621b-86ee-4302-8a90-98f70cff9046', pid=100, start_ticks='300',
                    boot_id='2701621b-86ee-4302-8a90-98f70cff9046', invocation_id='a' * 32)
    backend.expected = dict(voice_instance=instance)
    backend.stopped = {MODULE.VOICE}
    backend.voice_transition = '4701621b-86ee-4302-8a90-98f70cff9046'
    receipt = dict(instance, schema='sage.lantern.voice-drain.v1', transition_id=backend.voice_transition,
        checks=dict(history_outcome_known=True, http_outcome_known=True, memory_drained=False), drained=False)
    if fault == 'history_unknown':
        receipt['checks']['history_outcome_known'] = False
    elif fault == 'http_unknown':
        receipt['checks']['http_outcome_known'] = False
    elif fault == 'stale_transition':
        receipt['transition_id'] = 'other'
    elif fault == 'stale_instance':
        receipt['pid'] += 1
    backend.voice_document = lambda name: receipt
    backend.helper = types.SimpleNamespace(sha=lambda raw: hashlib.sha256(raw).hexdigest(),
                                          canonical=lambda value: json.dumps(value).encode())
    output = []
    backend.record = lambda *args: output.append(args)
    if fault:
        with pytest.raises(ValueError):
            backend.voice_outcomes()
        assert not output
    else:
        backend.voice_outcomes()
        assert output[0][1]['historical_drain_verified'] is False
        assert output[0][1]['queued_writes'] == 'not_asserted'


def test_guard_retains_runtime_records_until_explicit_archive():
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    assert b'RuntimeDirectoryPreserve=yes\n' in backend.guard()
