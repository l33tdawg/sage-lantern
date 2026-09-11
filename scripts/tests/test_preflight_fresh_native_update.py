import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('cold_preflight',
    Path(__file__).resolve().parents[1] / 'lantern/preflight-fresh-native-update.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


INFO = {'result': {'response': {'app_version': '27'}}}


def receipt(current=True):
    return dict(version=1, instance_id='a' * 32, closing=not current,
                drained=not current, local_revoked=not current,
                upstream_cleanup='unknown' if current else 'confirmed')


@pytest.mark.parametrize('current', [True, False])
def test_report_exact_current_and_prior(current):
    MODULE.report(receipt(current), 'a' * 32, current=current)


@pytest.mark.parametrize('field,value', [('version', True), ('instance_id', 'b' * 32),
    ('closing', True), ('drained', True), ('local_revoked', True), ('upstream_cleanup', 'confirmed'),
    ('extra', False)])
def test_current_report_never_claims_drained(field, value):
    report = receipt()
    report[field] = value
    with pytest.raises(ValueError):
        MODULE.report(report, 'a' * 32, current=True)


@pytest.mark.parametrize('field,value', [('closing', False), ('drained', 1), ('local_revoked', False),
                                       ('upstream_cleanup', 'unknown')])
def test_prior_report_requires_confirmed_cleanup(field, value):
    report = receipt(False)
    report[field] = value
    with pytest.raises(ValueError):
        MODULE.report(report, 'a' * 32, current=False)


@pytest.mark.parametrize('fault', [None, 'pending_plan', 'active_proposal', 'version', 'chain', 'sync', 'error', 'code'])
def test_governance_denies_pending_and_wrong_chain(fault):
    value = dict(schema='sage-upgrade-governance-status/v1', current_app_version=27,
                 pending_plan=None, active_proposal=None)
    status = dict(result=dict(node_info=dict(network='sage-personal-a', protocol_version=dict(app='27')),
                              sync_info=dict(catching_up=False)))
    if fault in ('pending_plan', 'active_proposal'):
        value[fault] = {}
    if fault == 'version':
        value['current_app_version'] = 28
    if fault == 'chain':
        status['result']['node_info']['network'] = 'other'
    if fault == 'sync':
        status['result']['sync_info']['catching_up'] = True
    envelope = dict(result=dict(response=dict(code=0, value=base64.b64encode(json.dumps(value).encode()).decode())))
    if fault == 'error':
        envelope['error'] = {}
    if fault == 'code':
        envelope['result']['response']['code'] = False
    if fault:
        with pytest.raises(ValueError):
            MODULE.governance(envelope, status, 'sage-personal-a', INFO)
    else:
        MODULE.governance(envelope, status, 'sage-personal-a', INFO)


@pytest.mark.parametrize('advertised,governed,chain,catching_up,accepted', [
    ('23', 27, 'sage-personal-a', False, True),
    ('27', 23, 'sage-personal-a', False, False),
    ('23', 23, 'sage-personal-a', False, False),
    ('23', 28, 'sage-personal-a', False, False),
    ('23', 27, 'sage-personal-other', False, False),
    ('23', 27, 'sage-personal-a', True, False),
])
def test_comet_advertisement_is_not_governed_application_version(advertised, governed, chain, catching_up, accepted):
    value = dict(schema='sage-upgrade-governance-status/v1', current_app_version=governed,
                 pending_plan=None, active_proposal=None)
    envelope = dict(result=dict(response=dict(code=0, value=base64.b64encode(json.dumps(value).encode()).decode())))
    status = dict(result=dict(node_info=dict(network=chain, protocol_version=dict(app=advertised)),
                              sync_info=dict(catching_up=catching_up)))
    if accepted:
        MODULE.governance(envelope, status, 'sage-personal-a', INFO)
    else:
        with pytest.raises(ValueError):
            MODULE.governance(envelope, status, 'sage-personal-a', INFO)


@pytest.mark.parametrize('info', [
    {'result': {'response': {'app_version': '23'}}},
    {'result': {'response': {'app_version': '28'}}},
    {'result': {'response': {'app_version': 27}}},
    {'result': {'response': {'app_version': True}}},
    {'result': {'response': {'app_version': '027'}}},
    {'result': {'response': {}}},
    {'error': {}, 'result': {'response': {'app_version': '27'}}},
    None,
])
def test_info_must_independently_report_exact_string_v27(info):
    value = dict(schema='sage-upgrade-governance-status/v1', current_app_version=27,
                 pending_plan=None, active_proposal=None)
    envelope = dict(result=dict(response=dict(code=0, value=base64.b64encode(json.dumps(value).encode()).decode())))
    status = dict(result=dict(node_info=dict(network='sage-personal-a', protocol_version=dict(app='23')),
                              sync_info=dict(catching_up=False)))
    with pytest.raises((ValueError, KeyError)):
        MODULE.governance(envelope, status, 'sage-personal-a', info)


@pytest.mark.parametrize('fault', [None, 'active', 'pid', 'job', 'cgroup', 'occupied', 'changed', 'override', 'reload'])
def test_cold_service_empty_tree_and_two_observations(fault):
    name = MODULE.COLD[1]
    assert name == 'sage-lantern-mesh-runtime.service'
    fragment = '/etc/systemd/system/' + name
    value = dict(LoadState='loaded', ActiveState='inactive', MainPID='0', InvocationID='', Job='',
                 ControlGroup='', Slice='system.slice', KillMode='control-group', User='root',
                 FragmentPath=fragment, DropInPaths='', NeedDaemonReload='no')
    changes = dict(active=('ActiveState', 'active'), pid=('MainPID', '99'), job=('Job', '10'),
        cgroup=('ControlGroup', '/other'), override=('DropInPaths', '/unknown'), reload=('NeedDaemonReload', 'yes'))
    if fault in changes:
        field, replacement = changes[fault]
        value[field] = replacement
    calls = []
    def show(service, deadline, fields):
        assert service == name and fields == MODULE.FIELDS
        calls.append('show')
        result = value.copy()
        if fault == 'changed' and calls.count('show') == 2:
            result['MainPID'] = '99'
        return result
    def empty(path, deadline):
        calls.append('empty')
        assert str(path) == '/system.slice/' + name
        if fault == 'occupied':
            raise ValueError
    observer = MODULE.Observation.__new__(MODULE.Observation)
    observer.deadline = 45
    observer.system = SimpleNamespace(_show=show, _empty_tree=empty, _cgroup=lambda path: path)
    if fault:
        with pytest.raises(ValueError):
            observer.service(name, None, {fragment: 'a' * 64})
    else:
        observer.service(name, None, {fragment: 'a' * 64})
        assert calls == ['show', 'empty', 'show']


@pytest.mark.parametrize('fault', [None, 'changed', 'timeout'])
def test_aggregate_observation_not_mutation_admission(fault):
    calls = []
    def observe(expected):
        calls.append('observe')
        if fault == 'changed' and len(calls) == 2:
            return 'changed'
        return 'same'
    def remaining():
        if fault == 'timeout':
            raise TimeoutError
    observer = SimpleNamespace(observe=observe, remaining=remaining)
    if fault:
        with pytest.raises((ValueError, TimeoutError)):
            MODULE.verify({}, observer)
    else:
        result = MODULE.verify({}, observer)
        assert result == dict(preflight_verified=True, unit_id='unit-a', mutation_authorized=False,
                             auth_drained=False, completion_verified=False, recovery='manual-only')
        assert calls == ['observe', 'observe']


@pytest.mark.parametrize('fault', [None, 'redirect', 'oversize', 'timeout'])
def test_http_only_bounded_fixed_loopback_get(monkeypatch, fault):
    events = []
    class Connection:
        def __init__(self, host, port, timeout):
            assert host == '127.0.0.1' and port == 3196 and timeout == 3
        def request(self, method, route):
            assert method == 'GET' and route == '/state'
            if fault == 'timeout':
                raise TimeoutError
        def getresponse(self):
            return SimpleNamespace(status=302 if fault == 'redirect' else 200,
                read=lambda limit: b'x' * limit if fault == 'oversize' else b'{"locked":true}')
        def close(self):
            events.append('closed')
    monkeypatch.setattr(MODULE.http.client, 'HTTPConnection', Connection)
    observer = MODULE.Observation.__new__(MODULE.Observation)
    observer.remaining = lambda: 3
    if fault:
        with pytest.raises((ValueError, TimeoutError)):
            observer.get(3196, '/state')
    else:
        assert observer.get(3196, '/state') == {'locked': True}
    assert events == ['closed']


def test_duplicate_review_fields_rejected():
    with pytest.raises(ValueError):
        MODULE.decode('{"unit_id":"unit-a","unit_id":"unit-b"}')


@pytest.mark.parametrize('fault', [None, 'occupied', 'changed', 'job', 'pid', 'fragment', 'dropin', 'other_unit'])
def test_only_absent_idle_allowed_with_independent_empty_cgroup(fault):
    name = 'sage-lantern-idle-suspend.service' if fault != 'other_unit' else MODULE.COLD[0]
    value = dict(LoadState='not-found', ActiveState='inactive', MainPID='0', InvocationID='', Job='',
                 ControlGroup='', Slice='', KillMode='control-group', User='', FragmentPath='',
                 DropInPaths='', NeedDaemonReload='no')
    for reason, field, replacement in [('job', 'Job', '42'), ('pid', 'MainPID', '123'),
        ('fragment', 'FragmentPath', '/unexpected'), ('dropin', 'DropInPaths', '/unexpected')]:
        if fault == reason:
            value[field] = replacement
    events = []
    def show(service, deadline, fields):
        events.append('show')
        result = value.copy()
        if fault == 'changed' and events.count('show') == 2:
            result['LoadState'] = 'loaded'
        return result
    def empty(path, deadline):
        assert path == '/system.slice/' + name
        events.append('empty')
        if fault == 'occupied':
            raise ValueError
    observer = MODULE.Observation.__new__(MODULE.Observation)
    observer.deadline = 45
    observer.system = SimpleNamespace(_show=show, _empty_tree=empty, _cgroup=lambda path: path)
    if fault:
        with pytest.raises(ValueError):
            observer.service(name, None, {})
    else:
        observer.service(name, None, {})
        assert events == ['show', 'empty', 'show']


@pytest.mark.parametrize('fault', [None, 'yaml_changed', 'commission_changed', 'missing_report',
    'numeric_locked', 'numeric_ready', 'backup', 'helper', 'boot', 'pending', 'deadline',
    'strict_unknown', 'recovery_unknown', 'recovery_partial'])
def test_complete_cold_observation(monkeypatch, tmp_path, fault):
    reports = tmp_path / 'drain'
    reports.mkdir(mode=0o700)
    current = reports / ('a' * 32 + '.json')
    current.write_text(json.dumps(receipt()))
    current.chmod(0o600)
    monkeypatch.setattr(MODULE, 'REPORTS', reports)
    boot = '12345678-1234-4234-8234-123456789abc'
    initialized = dict(schema='sage.lantern.initialized.v1', unit_id='unit-a', root_id='b' * 64,
                       chain_id='sage-personal-a')
    raw = {path: b'reviewed' for path in MODULE.REQUIRED}
    raw[MODULE.CONFIG + 'initialized-public.json'] = json.dumps(initialized).encode()
    commission = dict(schema='sage.lantern.commission-complete.v1', unit_id='unit-a', output_sha256={})
    for name, filename in (('lantern.toml', 'lantern.toml'), ('bindings.json', 'commissioned-bindings.json'),
                           ('initialized-public.json', 'initialized-public.json')):
        commission['output_sha256'][name] = MODULE.sha(raw[MODULE.CONFIG + filename])
    if fault == 'commission_changed':
        commission['output_sha256']['bindings.json'] = 'f' * 64
    raw[MODULE.CONFIG + 'commissioning-complete.json'] = json.dumps(commission).encode()
    raw.update({MODULE.BINARY: b'old', '/reviewed/new': b'new', '/reviewed/backup': b'old',
                '/reviewed/helper': b'helper'})
    for name, binary in (('before', MODULE.BINARY), ('after', '/reviewed/new')):
        raw['/reviewed/' + name + '.json'] = json.dumps({'binary_sha256': MODULE.sha(raw[binary])}).encode()
    expected = dict(schema='sage.lantern.cold-native-preflight.v1', unit_id='unit-a', boot_id=boot,
        chain_id='sage-personal-a', root_id='b' * 64, files={path: MODULE.sha(data) for path, data in raw.items()},
        native_before=dict(binary=MODULE.BINARY, manifest='/reviewed/before.json'),
        native_after=dict(binary='/reviewed/new', manifest='/reviewed/after.json'),
        backup='/reviewed/backup', restart_helper='/reviewed/helper',
        services={MODULE.AUTH: dict(invocation='a' * 32), MODULE.SAGE: {}})
    monkeypatch.setattr(MODULE, 'RESTART_SHA', MODULE.sha(b'helper'))
    if fault == 'helper':
        monkeypatch.setattr(MODULE, 'RESTART_SHA', 'f' * 64)
    if fault == 'backup':
        expected['backup'] = MODULE.BINARY
    if fault == 'yaml_changed':
        raw[MODULE.YAML] = b'changed after review'
    if fault == 'missing_report':
        current.unlink()
    raw[current] = json.dumps(receipt()).encode()
    if fault in ('strict_unknown', 'recovery_unknown', 'recovery_partial'):
        prior = reports / ('b' * 32 + '.json')
        value = dict(receipt(), instance_id='b' * 32)
        if fault == 'recovery_partial':
            value['closing'] = True
        prior.write_text(json.dumps(value))
        prior.chmod(0o600)
        raw[prior] = prior.read_bytes()
    monkeypatch.setattr(MODULE, 'trusted', lambda path, *args: raw[path])
    original = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda path, *args, **kwargs:
        ('wrong' if fault == 'boot' else boot) if str(path) == '/proc/sys/kernel/random/boot_id'
        else original(path, *args, **kwargs))
    monkeypatch.setattr(Path, 'is_file', lambda path: True)
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: fault == 'pending')
    monkeypatch.setattr(MODULE, 'load_kiosk_binding', lambda:
        SimpleNamespace(unit_id='unit-a', account='radxa', uid=1000))
    observer = MODULE.Observation.__new__(MODULE.Observation)
    observer.recovery_entry = fault in ('recovery_unknown', 'recovery_partial')
    observer.uid = MODULE.os.getuid()
    observer.deadline = 45
    observer.service = lambda name, expected, files: {'observed': name}
    observer.system = SimpleNamespace(_sleep_idle=lambda deadline: None)
    def remaining():
        if fault == 'deadline':
            raise TimeoutError
        return 10
    observer.remaining = remaining
    value = dict(schema='sage-upgrade-governance-status/v1', current_app_version=27,
                 pending_plan=None, active_proposal=None)
    replies = {'/state': {'locked': 1 if fault == 'numeric_locked' else True},
        '/abci_info': INFO,
        '/sleep-ready': {'ready': 1 if fault == 'numeric_ready' else True},
        '/abci_query?path=%22/upgrade/governance-status%22': {'result': {'response': {'code': 0,
            'value': base64.b64encode(json.dumps(value).encode()).decode()}}},
        '/status': {'result': {'node_info': {'network': 'sage-personal-a', 'protocol_version': {'app': '27'}},
                              'sync_info': {'catching_up': False}}}}
    observer.get = lambda port, route: replies[route]
    if fault == 'recovery_unknown':
        result = MODULE.verify(expected, observer)
        assert result['recovery_entry_verified'] is True
        assert result['preflight_verified'] is False and result['mutation_authorized'] is False
        assert result['current_auth_drain_required'] is True and result['capability_invalidated'] is False
        assert result['unknown_receipts'] == {'b' * 32: MODULE.sha(raw[prior])}
    elif fault:
        with pytest.raises((ValueError, TimeoutError)):
            MODULE.verify(expected, observer)
    else:
        assert MODULE.verify(expected, observer)['preflight_verified'] is True
