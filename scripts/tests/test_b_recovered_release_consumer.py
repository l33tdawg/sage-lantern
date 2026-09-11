import importlib.util
from pathlib import Path
import stat
from types import SimpleNamespace
import uuid

import pytest


SPEC = importlib.util.spec_from_file_location('receipt_consumer',
    Path(__file__).resolve().parents[1] / 'lantern/handoff-b-policy-guards.py')
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def fixture(monkeypatch):
    attempt = '11111111-1111-4111-8111-111111111111'
    monkeypatch.setattr(helper, 'SUCCESS_ATTEMPT', attempt)
    boot = '22222222-2222-4222-8222-222222222222'
    native = '33333333-3333-4333-8333-333333333333'
    root = Path('/var/lib/lantern-b-release-recovery-' + attempt)
    original = Path('/var/lib/original-policy-attempt')
    reports = Path('/var/lib/auth-reports')
    auth, ui, sage = 'auth.service', 'ui.service', 'native.service'
    old = {'boot_id': boot, 'services': {auth: {'invocation': 'a' * 32, 'image_sha256': 'b' * 64},
        ui: {'invocation': 'c' * 32, 'image_sha256': 'd' * 64}}}
    files = {'/old/expected.json': helper.encoded(old)}
    prior = {name: ('retained-v2-failure-' + name).encode() for name in helper.PRIOR_FILES}
    files.update({str(helper.PRIOR_RECOVERY / name): raw for name, raw in prior.items()})
    monkeypatch.setattr(helper, 'PRIOR_FILES', {name: helper.sha(raw) for name, raw in prior.items()})
    diagnostic = {name: ('retained-v3-failure-' + name).encode() for name in helper.DIAGNOSTIC_FILES}
    files.update({str(helper.DIAGNOSTIC_RECOVERY / name): raw for name, raw in diagnostic.items()})
    monkeypatch.setattr(helper, 'DIAGNOSTIC_FILES', {name: helper.sha(raw) for name, raw in diagnostic.items()})
    oldpin = {'path': '/old/expected.json', 'sha256': helper.sha(files['/old/expected.json'])}
    monkeypatch.setattr(helper, 'RECOVERY_POLICY_SHA', oldpin['sha256'])
    expected = dict(schema='sage.lantern.b-release-recovery.v1', attempt_id=attempt,
        self_sha256=helper.RECOVERY_SHA, coordinator={'path': '/recovery/coordinator.py', 'sha256': helper.RECOVERY_COORDINATOR_SHA},
        policy_expected=oldpin, journal_sha256=helper.FAILED_JOURNAL_SHA,
        complete_sha256=helper.NATIVE_COMPLETE_SHA, auth_reports=helper.HISTORICAL_AUTH)
    evidence = dict(schema='sage.lantern.b-recovered-release-evidence.v1',
        recovery={'path': '/recovery/helper.py', 'sha256': helper.RECOVERY_SHA},
        expected={'path': str(root / 'expected.json'), 'sha256': 'fresh-expected'},
        complete={'path': str(root / 'complete.json'), 'sha256': 'fresh-completion'})
    fresh = {auth: dict(pid=20, start_ticks=100, invocation='e' * 32, image_sha256='b' * 64),
        ui: dict(pid=21, start_ticks=101, invocation='f' * 32, image_sha256='d' * 64)}
    receipt = dict(schema='sage.lantern.b-release-recovery-complete.v1', expected_sha256='later',
        boot_id=boot, native_boot=native, owner_unlock_required=True, audio_started=False,
        historical_cleanup='unclaimed', fresh_services=fresh, retained_guards=sorted([sage, 'cold.service']))
    historic = {'a' * 32 + '.json': b'old-drain-is-unknown'}
    historical = {name: helper.sha(raw) for name, raw in historic.items()}
    monkeypatch.setattr(helper, 'HISTORICAL_AUTH', historical)
    expected['auth_reports'] = historical
    failed_raw = helper.encoded({'phase': 'release_failed_processes_stopped_auth_cleanup_unknown'})
    native_raw = helper.encoded(dict(boot_id=boot, native_boot=native))
    monkeypatch.setattr(helper, 'FAILED_JOURNAL_SHA', helper.sha(failed_raw))
    monkeypatch.setattr(helper, 'NATIVE_COMPLETE_SHA', helper.sha(native_raw))
    expected['journal_sha256'], expected['complete_sha256'] = helper.FAILED_JOURNAL_SHA, helper.NATIVE_COMPLETE_SHA
    files.update({str(original / 'expected.json'): files['/old/expected.json'],
        str(original / 'transition.jsonl'): failed_raw, str(original / 'complete.json'): native_raw,
        str(root / ('guard-' + auth)): b'guard',
        str(root / ('guard-' + ui)): b'guard'})
    phases = ('intent', 'remove-' + auth, 'remove-' + ui, 'start_auth', 'start_ui', 'complete')
    files[str(root / 'journal.jsonl')] = b'\n'.join(helper.encoded({'phase': phase}) for phase in phases)
    auth_report = helper.encoded({'instance_id': 'e' * 32, 'closing': False})
    files[str(root / 'auth-new-locked.json')] = auth_report
    files[str(reports / ('e' * 32 + '.json'))] = auth_report
    for name, raw in historic.items():
        files[str(reports / name)] = raw
    def refresh():
        files[str(root / 'expected.json')] = helper.encoded(expected)
        receipt['expected_sha256'] = helper.sha(files[str(root / 'expected.json')])
        files[str(root / 'complete.json')] = helper.encoded(receipt)
        evidence['expected']['sha256'] = helper.sha(files[str(root / 'expected.json')])
        evidence['complete']['sha256'] = helper.sha(files[str(root / 'complete.json')])
        monkeypatch.setattr(helper, 'SUCCESS_COMPLETE_SHA', evidence['complete']['sha256'])
    refresh()
    def pinned(pin):
        if pin['path'] in ('/recovery/helper.py', '/recovery/coordinator.py'):
            return b'fixed-code-pin-checked'
        raw = files[pin['path']]
        helper.require(helper.sha(raw) == pin['sha256'])
        return raw
    monkeypatch.setattr(helper, 'pinned', pinned)
    monkeypatch.setattr(helper, 'read', lambda path: files[str(path)])
    monkeypatch.setattr(helper.os.path, 'lexists', lambda path: str(path) in files)
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0))
    monkeypatch.setattr(Path, 'iterdir', lambda path: iter(Path(name) for name in files if Path(name).parent == path))
    module = SimpleNamespace(AUTH=auth, UI=ui, SAGE=sage, COLD=('cold.service',), REPORTS=reports,
        valid_boot=lambda value: type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int > 0,
        read=lambda path, *args: files[str(path)], require=helper.require)
    def report(value, invocation, **kwargs):
        helper.require(value == {'instance_id': invocation, 'closing': False} and kwargs == {'current': True})
    policy = SimpleNamespace(expected=old, state=original, guard=b'guard', uid=994,
        guards={auth: Path('/guards/auth'), ui: Path('/guards/ui')},
        preflight=SimpleNamespace(report=report), capture_process=lambda service: dict(fresh[service]))
    return SimpleNamespace(evidence=evidence, policy=policy, module=module, pin=oldpin,
        expected=expected, receipt=receipt, files=files, root=root, original=original,
        reports=reports, refresh=refresh)


@pytest.mark.parametrize('fault', [None, 'cleanup_claim', 'audio_claim', 'unlock_flag', 'wrong_boot',
    'wrong_native', 'wrong_attempt', 'wrong_observer', 'wrong_code', 'wrong_policy', 'old_success',
    'old_journal_changed', 'incomplete_recovery', 'error_artifact', 'stale_auth', 'missing_current_receipt',
    'changed_current_receipt', 'historical_changed', 'retained_guard_missing', 'pid_bool', 'same_process'])
def test_exact_recovery_receipt_preserves_failed_v5_trace(monkeypatch, fault):
    case = fixture(monkeypatch)
    original_bytes = {name: raw for name, raw in case.files.items() if str(case.original) in name}
    if fault == 'cleanup_claim':
        case.receipt['historical_cleanup'] = 'confirmed'
    elif fault == 'audio_claim':
        case.receipt['audio_started'] = True
    elif fault == 'unlock_flag':
        case.receipt['owner_unlock_required'] = 1
    elif fault == 'wrong_boot':
        case.receipt['boot_id'] = 'different'
    elif fault == 'wrong_native':
        case.receipt['native_boot'] = '44444444-4444-4444-8444-444444444444'
    elif fault == 'wrong_attempt':
        case.expected['attempt_id'] = '55555555-5555-4555-8555-555555555555'
    elif fault == 'wrong_observer':
        case.expected['coordinator']['sha256'] = helper.POLICY_SHA
    elif fault == 'wrong_code':
        case.evidence['recovery']['sha256'] = '0' * 64
    elif fault == 'wrong_policy':
        case.expected['policy_expected'] = {'path': '/not-original', 'sha256': '0' * 64}
    elif fault == 'old_success':
        case.files[str(case.original / 'released.json')] = b'forged-success'
    elif fault == 'old_journal_changed':
        case.files[str(case.original / 'transition.jsonl')] = b'{"phase":"released_owner_unlock_required"}'
    elif fault == 'incomplete_recovery':
        case.files[str(case.root / 'journal.jsonl')] = b'{"phase":"intent"}'
    elif fault == 'error_artifact':
        case.files[str(case.root / 'failed.json')] = b'{}'
    elif fault == 'stale_auth':
        case.policy.capture_process = lambda service: {'pid': 999}
    elif fault == 'missing_current_receipt':
        del case.files[str(case.reports / ('e' * 32 + '.json'))]
    elif fault == 'changed_current_receipt':
        case.files[str(case.reports / ('e' * 32 + '.json'))] = b'changed'
    elif fault == 'historical_changed':
        case.files[str(case.reports / ('a' * 32 + '.json'))] = b'cleanup-invented'
    elif fault == 'retained_guard_missing':
        case.receipt['retained_guards'] = []
    elif fault == 'pid_bool':
        case.receipt['fresh_services'][case.module.AUTH]['pid'] = True
    elif fault == 'same_process':
        case.receipt['fresh_services'][case.module.UI]['pid'] = 20
    case.refresh()
    if fault:
        with pytest.raises((RuntimeError, KeyError)):
            helper.recovered_release(case.evidence, case.policy, case.module, case.pin, current_auth=True)
    else:
        assert helper.recovered_release(case.evidence, case.policy, case.module, case.pin, current_auth=True) == case.receipt
        assert {name: case.files[name] for name in original_bytes} == original_bytes
        assert str(case.original / 'released.json') not in case.files


def test_historical_receipt_is_not_claimed_as_current_auth(monkeypatch):
    case = fixture(monkeypatch)
    case.policy.capture_process = lambda service: (_ for _ in ()).throw(AssertionError('no live-auth claim requested'))
    del case.files[str(case.reports / ('e' * 32 + '.json'))]
    assert helper.recovered_release(case.evidence, case.policy, case.module, case.pin)['historical_cleanup'] == 'unclaimed'


@pytest.mark.parametrize('fault', ['missing', 'wrong_hash', 'wrong_path'])
def test_actual_completion_pin_is_mandatory(monkeypatch, fault):
    case = fixture(monkeypatch)
    if fault == 'missing':
        del case.evidence['complete']
    elif fault == 'wrong_hash':
        case.evidence['complete']['sha256'] = '0' * 64
    else:
        case.evidence['complete']['path'] = '/another-attempt/complete.json'
    with pytest.raises((RuntimeError, KeyError)):
        helper.recovered_release(case.evidence, case.policy, case.module, case.pin)


@pytest.mark.parametrize('fault', [None, 'cold_guard', 'unknown_producer', 'native_boot', 'governance', 'numeric_lock'])
def test_recovered_mask_admission_still_requires_current_physical_containment(monkeypatch, fault):
    events = []
    receipt = {'fresh_services': {'auth': {'pid': 123}}, 'native_boot': 'native-boot'}
    monkeypatch.setattr(helper, 'recovered_release', lambda *args, **kwargs: events.append(('anchors', kwargs)) or receipt)
    def gate(name):
        events.append(name)
        if name == fault:
            raise RuntimeError('current state refused')
    def services(stopped, **kwargs):
        assert stopped == ('ui', 'idle')
        assert kwargs == dict(guarded={'native', 'cold'}, completed=True, fresh={'auth': {'pid': 123}})
        gate('cold_guard')
    policy = SimpleNamespace(files=lambda **kwargs: gate('files'), services=services,
        mesh=SimpleNamespace(b_no_prior_lifetime=lambda: gate('fresh'), b_other_producers=lambda: gate('unknown_producer')),
        system=SimpleNamespace(_sleep_idle=lambda deadline: gate('sleep')),
        expected={'services': {'native': {'pid': 42, 'start_ticks': 10}}, 'boot_id': 'boot', 'after': {'version': 'new'}},
        listener=SimpleNamespace(listener_owner=lambda *args: gate('listener')),
        preflight=SimpleNamespace(governance=lambda *args: gate('governance')), policy=SimpleNamespace(CHAIN='B-chain'))
    def get(port, path):
        if port == 8080:
            return {'vault_locked': False}
        if port == 3196:
            return {'locked': 0 if fault == 'numeric_lock' else False}
        return {'result': {'n_txs': '0'}}
    module = SimpleNamespace(AUTH='auth', UI='ui', IDLE='idle', SAGE='native', RETAINED={'native', 'cold'},
        get=get, validate_health=lambda *args: gate('native_boot'))
    if fault:
        with pytest.raises(RuntimeError):
            helper.recovered_policy_contained({}, policy, module, {})
    else:
        assert helper.recovered_policy_contained({}, policy, module, {}) == receipt
        assert events[0] == events[-1] == ('anchors', {'current_auth': True})


@pytest.mark.parametrize('name', tuple(helper.PRIOR_FILES))
@pytest.mark.parametrize('change', ['missing', 'changed'])
@pytest.mark.parametrize('directory', ['PRIOR_RECOVERY', 'DIAGNOSTIC_RECOVERY'])
def test_every_failed_recovery_file_is_required_unchanged(monkeypatch, name, change, directory):
    case = fixture(monkeypatch)
    path = str(getattr(helper, directory) / name)
    if change == 'missing':
        del case.files[path]
    else:
        case.files[path] = b'not the actual retained failure'
    with pytest.raises(RuntimeError):
        helper.recovered_release(case.evidence, case.policy, case.module, case.pin)


@pytest.mark.parametrize('directory', ['PRIOR_RECOVERY', 'DIAGNOSTIC_RECOVERY'])
def test_failed_recovery_cannot_be_retroactively_completed(monkeypatch, directory):
    case = fixture(monkeypatch)
    case.files[str(getattr(helper, directory) / 'complete.json')] = b'fabricated-success'
    with pytest.raises(RuntimeError):
        helper.recovered_release(case.evidence, case.policy, case.module, case.pin)


def test_old_v2_code_is_not_accepted_as_successor(monkeypatch):
    case = fixture(monkeypatch)
    case.evidence['recovery']['sha256'] = '27f3e293e235e3e77703de22a930a382e75ad1cfe01d8a1cc7f5996585616b7e'
    with pytest.raises(RuntimeError):
        helper.recovered_release(case.evidence, case.policy, case.module, case.pin)


@pytest.mark.parametrize('fault', ['completion', 'attempt'])
def test_only_reported_actual_success_is_admitted(monkeypatch, fault):
    case = fixture(monkeypatch)
    monkeypatch.setattr(helper, 'SUCCESS_COMPLETE_SHA' if fault == 'completion' else 'SUCCESS_ATTEMPT', 'substituted')
    with pytest.raises(RuntimeError):
        helper.recovered_release(case.evidence, case.policy, case.module, case.pin)


def test_prior_failure_is_rechecked_after_current_receipt(monkeypatch):
    case = fixture(monkeypatch)
    original = case.policy.capture_process
    def capture(service):
        case.files[str(helper.PRIOR_RECOVERY / 'failed.json')] = b'changed-during-read'
        return original(service)
    case.policy.capture_process = capture
    with pytest.raises(RuntimeError):
        helper.recovered_release(case.evidence, case.policy, case.module, case.pin, current_auth=True)
