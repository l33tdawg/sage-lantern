import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(name):
    path = Path(__file__).resolve().parents[1] / 'lantern' / name
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


module = load('transition-b-provisioning-sleep.py')
handoff = load('handoff-b-policy-guards.py')


def test_exact_handoff_dependency_binding():
    assert module.HANDOFF_SHA == handoff.ORIGINAL_HANDOFF_SHA


@pytest.mark.parametrize('fault', [None, 'missing_option', 'wrong_version', 'command_failure'])
def test_target_executable_contract_is_readonly_bounded(monkeypatch, fault):
    calls = []
    environment = SimpleNamespace(integrity=lambda: calls.append('integrity'),
        expected={'systemd_inhibit_sha256': module.INHIBIT_SHA})
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[0] == module.INHIBIT and argv[1] in ('--help', '--version')
        assert kwargs == dict(stdin=module.subprocess.DEVNULL, stdout=module.subprocess.PIPE,
            stderr=module.subprocess.DEVNULL, env=module.ENV, timeout=5, check=True)
        if fault == 'command_failure':
            raise module.subprocess.CalledProcessError(1, argv)
        help_raw = b'--what= --who= --why= --mode= --list --no-pager --no-legend --help --version'
        help_raw = help_raw.ljust(842, b' ')
        monkeypatch.setattr(module, 'INHIBIT_HELP_SHA', module.sha(help_raw))
        if fault == 'missing_option':
            help_raw = b'--help'
        return SimpleNamespace(stdout=help_raw if argv[1] == '--help' else
            (b'unknown' if fault == 'wrong_version' else b'systemd 252 (252.39-1~deb12u2)'))
    monkeypatch.setattr(module.subprocess, 'run', run)
    if fault:
        with pytest.raises((RuntimeError, module.subprocess.CalledProcessError)):
            module.Environment.executable_contract(environment)
    else:
        module.Environment.executable_contract(environment)
        assert calls[0] == calls[-1] == 'integrity'


def test_supported_wrapper_preserves_holder_contract():
    environment = SimpleNamespace(why='pinned', holder_argv=lambda: ['python', '--holder'])
    assert module.Environment.wrapper_argv(environment) == [module.INHIBIT,
        '--what=sleep', '--mode=block', '--who=' + module.WHO, '--why=pinned', 'python', '--holder']


def test_reconcile_refuses_unpinned_failed_files_before_observation():
    document = dict(schema='sage.lantern.b-sleep-failed-option.v1', boot_id='boot', files={})
    environment = SimpleNamespace(helper=SimpleNamespace(pinned=lambda pin: b'', decode=lambda raw: document),
        expected={'boot_id': 'boot'})
    with pytest.raises(RuntimeError):
        module.reconcile_failed(environment, {})


@pytest.mark.parametrize('fault', [None, 'owned_row', 'pid_row', 'mask_changed', 'extra_receipt', 'live_pid'])
def test_exact_failed_attempt_readonly_reconciliation(monkeypatch, fault):
    expected = {'self_sha256': 'new', 'boot_id': 'boot'}
    old = dict(expected, self_sha256=module.FAILED_HELPER_SHA)
    files = {'expected.json': json.dumps(old).encode(),
        'intent.json': json.dumps(dict(inhibitor_acquisition_authorized=True, mask_retirement_authorized=True)).encode(),
        'spawned.json': b'{"pid":81168}'}
    checksums = {name: module.sha(raw) for name, raw in files.items()}
    monkeypatch.setattr(module, 'FAILED_FILES', checksums)
    document = dict(schema='sage.lantern.b-sleep-failed-option.v1', boot_id='boot', files=checksums)
    helper = SimpleNamespace(pinned=lambda pin: json.dumps(document).encode(), decode=json.loads)
    masks = {unit: [24, 2965 + index, module.stat.S_IFLNK | 0o777, 0, 0, 1, 9,
        1788787466285620339, 1788787466285620339] for index, unit in enumerate(module.TARGETS)}
    if fault == 'mask_changed':
        masks[module.TARGETS[0]][1] += 10
    rows = []
    if fault in ('owned_row', 'pid_row'):
        rows = [['sleep', module.WHO if fault == 'owned_row' else 'other', 'why', 'block', 0, 81168]]
    environment = SimpleNamespace(helper=helper, expected=expected, inspect=lambda: None,
        rows=lambda: rows, mask_state=lambda: masks)
    monkeypatch.setattr(module, 'trusted', lambda path: files[path.name])
    monkeypatch.setattr(module.os.path, 'lexists', lambda path: fault == 'live_pid' and str(path) == '/proc/81168')
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=module.stat.S_IFDIR | 0o700, st_uid=0))
    monkeypatch.setattr(Path, 'iterdir', lambda path: iter([] if str(path) == '/proc' else
        [path / name for name in list(files) + (['owner.json'] if fault == 'extra_receipt' else [])]))
    if fault:
        with pytest.raises(RuntimeError):
            module.reconcile_failed(environment, {})
    else:
        assert module.reconcile_failed(environment, {}) == dict(
            reconciliation_verified=True, archived=False, automatic_resume=False)


@pytest.mark.parametrize('fault', [None, 'empty', 'delay', 'wrong_pid', 'wrong_uid',
    'wrong_scope', 'wrong_why', 'duplicate', 'malformed'])
def test_real_inhibitor_rows_require_exact_owned_sleep_block(fault):
    row = ['sleep', module.WHO, 'reviewed', 'block', 0, 100]
    if fault in ('delay', 'wrong_pid', 'wrong_uid', 'wrong_scope', 'wrong_why'):
        index, value = {'delay': (3, 'delay'), 'wrong_pid': (5, 101), 'wrong_uid': (4, 994),
            'wrong_scope': (0, 'idle'), 'wrong_why': (2, 'other')}[fault]
        row[index] = value
    rows = [] if fault == 'empty' else [row]
    if fault == 'duplicate':
        rows.append(row)
    text = 'a(ssssuu) ' + str(len(rows)) + ''.join(' ' + ' '.join(map(str, item)) for item in rows)
    if fault == 'malformed':
        text = 'a(ssssuu) 1 sleep short'
    if fault:
        with pytest.raises(RuntimeError):
            module.owned(module.inhibitors(text), {'wrapper': {'pid': 100}}, 'reviewed')
    else:
        assert module.owned(module.inhibitors(text), {'wrapper': {'pid': 100}}, 'reviewed') == []


def test_other_inhibitors_preserved_not_confused_with_owner():
    rows = [['idle', 'desktop', 'reason with spaces', 'block', 1000, 80],
        ['sleep', module.WHO, 'reviewed', 'block', 0, 100]]
    assert module.owned(rows, {'wrapper': {'pid': 100}}, 'reviewed') == rows[:1]


@pytest.mark.parametrize('fault', [None, 'wrapper_reused', 'holder_parent', 'holder_image', 'boot'])
def test_live_process_ownership_allows_wrapper_reparent_only(fault):
    backend = object.__new__(module.Environment)
    backend.raw = b'pinned'
    backend.expected = {'boot_id': 'boot'}
    wrapper = dict(pid=100, start_ticks=10, argv=['wrapper'], image_sha256='image')
    child = dict(pid=101, start_ticks=11, parent=100, argv=['child'], image_sha256='python')
    backend.owner = dict(wrapper=wrapper, holder=child, boot_id='boot', expected_sha256=module.sha(backend.raw))
    backend.why = 'reviewed'
    backend.rows = lambda: [['sleep', module.WHO, 'reviewed', 'block', 0, 100]]
    def process(pid):
        value = dict(wrapper, parent=1) if pid == 100 else dict(child)
        if fault == 'wrapper_reused' and pid == 100:
            value['start_ticks'] += 1
        if fault == 'holder_parent' and pid == 101:
            value['parent'] = 999
        if fault == 'holder_image' and pid == 101:
            value['image_sha256'] = 'other'
        return value
    backend.process = process
    if fault == 'boot':
        backend.owner['boot_id'] = 'other'
    if fault:
        with pytest.raises(RuntimeError):
            backend.owner_check()
    else:
        assert backend.owner_check() == []


def mask_fixture(tmp_path, monkeypatch):
    root, state = tmp_path / 'run', tmp_path / 'state'
    root.mkdir()
    state.mkdir()
    for unit in module.TARGETS:
        (root / unit).symlink_to('/dev/null')
    other = root / 'unrelated.target'
    other.write_bytes(b'original untouched')
    monkeypatch.setattr(module, 'STATE', state)
    monkeypatch.setattr(module, 'MASK_ROOT', root)
    monkeypatch.setattr(handoff, 'parents', lambda path: None)
    backend = object.__new__(module.Environment)
    backend.helper = handoff
    backend.removed = set()
    backend.masks = None
    backend.mask_state = lambda: {unit: list(module.signature((root / unit).lstat()))
        for unit in module.TARGETS if unit not in backend.removed}
    events = []
    backend.owner_check = lambda: events.append('inhibitor')
    def guarded():
        events.append('guarded')
        backend.owner_check()
        assert other.read_bytes() == b'original untouched'
    backend.before_removal = guarded
    backend.reload = lambda: events.append('reload')
    def ordinary():
        assert backend.removed == set(module.TARGETS)
        assert all(not os.path.lexists(root / unit) for unit in module.TARGETS)
        events.append('ordinary-r7')
    backend.ordinary_sleep_idle = ordinary
    backend.idle = lambda: None
    return backend, root, state, events


@pytest.mark.parametrize('fault', [None, 'inhibitor', 'archive', 'unlink', 'reload', 'post_remove', 'ordinary'])
def test_exact_four_mask_retirement_has_real_archives_and_no_retry(tmp_path, monkeypatch, fault):
    backend, root, state, events = mask_fixture(tmp_path, monkeypatch)
    original = {unit: os.readlink(root / unit) for unit in module.TARGETS}
    before = backend.before_removal
    def guarded():
        before()
        if fault == 'inhibitor' or fault == 'post_remove' and backend.removed:
            raise RuntimeError('unknown')
    backend.before_removal = guarded
    if fault == 'archive':
        backend.archive_masks = lambda: (_ for _ in ()).throw(OSError('archive unknown'))
    unlink = backend.unlink
    def remove(unit):
        assert (state / ('unlink-' + unit + '.json')).exists()
        assert all(os.readlink(state / 'masks' / name) == original[name] for name in module.TARGETS)
        unlink(unit)
        if fault == 'unlink':
            raise OSError('unlink unknown')
    backend.unlink = remove
    if fault == 'reload':
        backend.reload = lambda: (_ for _ in ()).throw(TimeoutError('reload unknown'))
    if fault == 'ordinary':
        backend.ordinary_sleep_idle = lambda: (_ for _ in ()).throw(RuntimeError('ordinary R7 refused'))
    if fault:
        with pytest.raises((OSError, RuntimeError)):
            module.remove_masks(backend)
        assert not (state / 'ready.json').exists()
    else:
        module.remove_masks(backend)
        assert json.loads((state / 'ready.json').read_bytes())['inhibitor_held'] is True
        assert events.count('reload') == 4 and events[-1] == 'ordinary-r7'
    if fault in ('unlink', 'reload', 'post_remove'):
        assert len(backend.removed) == 1
    assert (root / 'unrelated.target').read_bytes() == b'original untouched'
    if (state / 'mask-archive.json').exists():
        with pytest.raises((FileExistsError, RuntimeError)):
            backend.archive_masks()


@pytest.mark.parametrize('fault', [None, 'replacement', 'reload', 'verification'])
def test_emergency_remask_is_once_only_and_never_resume(tmp_path, monkeypatch, fault):
    backend, root, state, events = mask_fixture(tmp_path, monkeypatch)
    for unit in module.TARGETS:
        (root / unit).unlink()
    if fault == 'replacement':
        (root / module.TARGETS[0]).write_bytes(b'foreign replacement')
    if fault == 'reload':
        backend.reload = lambda: (_ for _ in ()).throw(TimeoutError('unknown'))
    if fault == 'verification':
        backend.idle = lambda: (_ for _ in ()).throw(RuntimeError('unknown'))
    assert backend.remask_once() is (fault is None)
    outcome = json.loads((state / 'remask-outcome.json').read_bytes())
    assert outcome['resume_authorized'] is False
    if fault == 'replacement':
        assert (root / module.TARGETS[0]).read_bytes() == b'foreign replacement'
        assert all((root / unit).is_symlink() for unit in module.TARGETS[1:])
    with pytest.raises(FileExistsError):
        backend.remask_once()


@pytest.mark.parametrize('fault', [None, 'commissioning', 'lost_owner', 'journal'])
def test_release_authorization_does_not_silently_close_inhibitor(tmp_path, monkeypatch, fault):
    backend, root, state, events = mask_fixture(tmp_path, monkeypatch)
    pin = {'path': '/reviewed/release.json', 'sha256': 'a' * 64}
    def check(value):
        assert value == pin and (state / 'release-request.json').exists()
        events.append('actual-completion')
        if fault == 'commissioning':
            raise RuntimeError('no current commissioned lifetime')
    backend.release_check = check
    if fault == 'lost_owner':
        backend.owner_check = lambda: (_ for _ in ()).throw(RuntimeError('inhibitor lost'))
    if fault == 'journal':
        backend.record = lambda *args: (_ for _ in ()).throw(OSError('unknown'))
    if fault:
        with pytest.raises((RuntimeError, OSError)):
            module.release_holder(backend, pin)
        assert not (state / 'release-authorized.json').exists()
    else:
        module.release_holder(backend, pin)
        assert json.loads((state / 'release-authorized.json').read_bytes())['released'] is False
    if fault != 'journal':
        with pytest.raises(FileExistsError):
            module.release_holder(backend, pin)


@pytest.mark.parametrize('fault', [None, 'snapshot', 'wrong_boot', 'wrong_policy', 'wrong_transition', 'no_native_release'])
def test_release_checks_actual_completed_handoff_bound_to_this_transition(monkeypatch, fault):
    backend = object.__new__(module.Environment)
    backend.raw = b'transition'
    backend.expected = {'boot_id': 'boot', 'policy_expected': {'path': '/policy', 'sha256': 'a' * 64}}
    document = dict(schema='sage.lantern.b-provisioning-sleep-release.v1',
        transition_sha256=module.sha(backend.raw), native_expected={'path': '/native', 'sha256': 'b' * 64},
        native_complete_sha256='c' * 64)
    result = dict(verified=True, boot_id='boot', policy_expected=backend.expected['policy_expected'], native_guard_removed=True)
    if fault == 'wrong_boot':
        result['boot_id'] = 'other'
    if fault == 'wrong_policy':
        result['policy_expected'] = {}
    if fault == 'wrong_transition':
        document['transition_sha256'] = '0' * 64
    if fault == 'no_native_release':
        result['native_guard_removed'] = False
    calls = []
    def verify(raw, sha):
        calls.append('live-completion')
        if fault == 'snapshot':
            raise RuntimeError('current lifetime invalid')
        return result
    backend.helper = SimpleNamespace(decode=json.loads, pinned=lambda pin: json.dumps(document).encode(),
        verify_completed_native=verify)
    backend.status = lambda: calls.append('live-inhibitor')
    if fault:
        with pytest.raises(RuntimeError):
            backend.release_check({})
    else:
        backend.release_check({})
        assert calls == ['live-inhibitor', 'live-completion', 'live-inhibitor']


@pytest.mark.parametrize('parts,valid', [([b'{', b'}\n'], True), ([b''], False),
    ([b'{}\nextra'], False), ([b'{}\n{}\n'], False), ([b'a' * 8193], False)])
def test_bounded_socket_framing(parts, valid):
    iterator = iter(parts)
    connection = SimpleNamespace(recv=lambda maximum: next(iterator))
    if valid:
        assert module.receive(connection) == b'{}'
    else:
        with pytest.raises(RuntimeError):
            module.receive(connection)


def test_completed_native_verifier_actually_runs_live_check(monkeypatch):
    calls = []
    class Verified:
        def __init__(self, expected, raw, *, completed_sha256):
            calls.append(('reconstruct', completed_sha256))
            self.policy = SimpleNamespace(expected={'boot_id': 'boot'})
            self.expected = {'policy_expected': 'exact-pin'}

        def check(self):
            calls.append('live-check')
    monkeypatch.setattr(handoff, 'Backend', Verified)
    assert handoff.verify_completed_native(b'{}', 'completed-pin') == dict(verified=True,
        boot_id='boot', policy_expected='exact-pin', native_guard_removed=True)
    assert calls == [('reconstruct', 'completed-pin'), 'live-check']


@pytest.mark.parametrize('fault', [None, 'job', 'preparing_sleep', 'preparing_shutdown', 'idle_unknown',
    'sleep_unknown', 'control_pid', 'cgroup', 'unknown_process', 'permission'])
def test_no_jobs_idle_or_unknown_sleep_producers_gate(tmp_path, monkeypatch, fault):
    backend = object.__new__(module.Environment)
    backend.code = SimpleNamespace(IDLE='idle.service', SLEEP_UNITS=('systemd-suspend.service',))
    def refuse(which):
        if fault == which:
            raise RuntimeError('unknown')
    backend.policy = SimpleNamespace(properties=lambda unit: {}, absent_idle=lambda value: refuse('idle_unknown'),
        system=SimpleNamespace(_sleep_idle=lambda deadline: refuse('sleep_unknown'),
            _show=lambda *args: {'MainPID': '0', 'ControlPID': '123' if fault == 'control_pid' else '0'},
            _cgroup=lambda path: path, _empty_tree=lambda *args: refuse('cgroup')))
    def bus(service, member, **kwargs):
        if member == 'ListJobs':
            return 'a(usssoo) 1 pending' if fault == 'job' else 'a(usssoo) 0'
        if member == 'PreparingForSleep' and fault == 'preparing_sleep':
            return 'b true'
        if member == 'PreparingForShutdown' and fault == 'preparing_shutdown':
            return 'b true'
        return 'b false'
    backend.bus = bus
    process = tmp_path / '123'
    process.mkdir()
    (process / 'cmdline').write_bytes(b'systemd-sleep\0suspend\0' if fault == 'unknown_process' else b'ordinary\0')
    monkeypatch.setattr(Path, 'iterdir', lambda path: iter((process,)))
    if fault == 'permission':
        monkeypatch.setattr(Path, 'read_bytes', lambda path: (_ for _ in ()).throw(PermissionError('unknown')))
    if fault:
        with pytest.raises((RuntimeError, PermissionError)):
            backend.idle()
    else:
        backend.idle()


@pytest.mark.parametrize('fault', [None, 'preflight', 'spawn', 'exit'])
def test_exclusive_intent_precedes_real_inhibitor_launch(tmp_path, monkeypatch, fault):
    state = tmp_path / 'attempt'
    monkeypatch.setattr(module, 'STATE', state)
    monkeypatch.setattr(handoff, 'parents', lambda path: None)
    backend = object.__new__(module.Environment)
    backend.helper = handoff
    backend.raw = b'exact-expected'
    def inspect():
        if fault == 'preflight':
            raise RuntimeError('not safe')
    backend.inspect = inspect
    backend.wrapper_argv = lambda: ['reviewed-systemd-inhibit-command']
    backend.status = lambda: {'inhibitor_verified': True}
    calls = []
    def spawn(argv, **kwargs):
        assert (state / 'intent.json').exists() and (state / 'expected.json').read_bytes() == backend.raw
        calls.append('spawn')
        if fault == 'spawn':
            raise OSError('unknown')
        (state / 'ready.json').write_bytes(b'fixture')
        (state / 'control.sock').write_bytes(b'fixture')
        return SimpleNamespace(pid=100, poll=lambda: 1 if fault == 'exit' else None)
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    if fault:
        with pytest.raises((OSError, RuntimeError)):
            module.start(backend)
    else:
        assert module.start(backend) == {'inhibitor_verified': True}
    assert len(calls) == (0 if fault == 'preflight' else 1)
    if fault != 'preflight':
        with pytest.raises(RuntimeError):
            module.start(backend)


def test_holder_uncaught_error_restores_masks_before_wrapper_can_exit(monkeypatch):
    events = []
    monkeypatch.setattr(module, 'serve_holder', lambda backend: (_ for _ in ()).throw(OSError('socket creation failed')))
    monkeypatch.setattr(module.os.path, 'lexists', lambda path: False)
    backend = SimpleNamespace(remask_once=lambda: events.append('verified-remask') or True)
    module.holder(backend)
    assert events == ['verified-remask']


def test_holder_unknown_restoration_does_not_exit_or_retry(monkeypatch):
    class Held(BaseException):
        pass
    events = []
    monkeypatch.setattr(module, 'serve_holder', lambda backend: (_ for _ in ()).throw(OSError('unknown')))
    monkeypatch.setattr(module.os.path, 'lexists', lambda path: False)
    monkeypatch.setattr(module.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: (_ for _ in ()).throw(Held()))
    backend = SimpleNamespace(remask_once=lambda: events.append('remask-once') or False)
    with pytest.raises(Held):
        module.holder(backend)
    assert events == ['remask-once']


def test_mask_admission_uses_explicit_recovery_authority_without_replaying_original():
    backend = object.__new__(module.Environment)
    evidence = {'actual': 'separately-pinned-recovery'}
    backend.expected = {'recovery_evidence': evidence, 'policy_expected': 'unchanged-v5'}
    backend.policy, backend.code = object(), object()
    calls = []
    backend.helper = SimpleNamespace(recovered_policy_contained=lambda *args: calls.append(args))
    backend.guarded_policy()
    assert calls == [(evidence, backend.policy, backend.code, 'unchanged-v5')]


@pytest.mark.parametrize('fault', [None, 'missing', 'substituted'])
def test_final_release_must_carry_same_recovery_evidence(fault):
    backend = object.__new__(module.Environment)
    backend.raw = b'transition'
    evidence = {'schema': 'recovered', 'complete': 'fresh-actual-pin'}
    backend.expected = {'boot_id': 'boot', 'policy_expected': 'original-v5', 'recovery_evidence': evidence}
    document = dict(schema='sage.lantern.b-provisioning-sleep-release.v1', transition_sha256=module.sha(backend.raw),
        native_expected={}, native_complete_sha256='actual')
    result = dict(verified=True, boot_id='boot', policy_expected='original-v5', native_guard_removed=True)
    if fault != 'missing':
        result['recovery_evidence'] = {} if fault == 'substituted' else evidence
    backend.helper = SimpleNamespace(decode=json.loads, pinned=lambda pin: json.dumps(document).encode(),
        verify_completed_native=lambda *args: result)
    backend.status = lambda: None
    if fault:
        with pytest.raises(RuntimeError):
            backend.release_check({})
    else:
        backend.release_check({})
