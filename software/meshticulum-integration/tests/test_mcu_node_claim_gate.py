"""Actual node dispatch with modeled consumed claims; no native/RF approval.

Selection pins are explicitly local source fixtures. The real Core authority
holds private fixture files and consumes its started marker, then the runner
stops before any native job, store, USB, HAT or radio attempt can be created.
"""
import base64
from copy import deepcopy
from dataclasses import asdict
import hashlib
import os
from types import MappingProxyType, SimpleNamespace

import pytest

from meshticulum.bench import authority as core_authority
from meshticulum.contracts.identity import PeerID
from lantern_meshticulum.bench import mcu_artifact as mcu, node, pilot_job, pilot_native_caps
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import Cost, ParentPolicy, UnitCaps, canonical, child_caps
from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
from lantern_meshticulum.ip import node as ip_node

BOOT = '00000000-0000-4000-8000-000000000001'
NOW = (100000000, 50000000)
AUTO = object()
STOP = 'source_stop_before_native_job'


@pytest.fixture
def source_case(tmp_path, monkeypatch):
    real_authority = node.RunAuthority
    real_open = core_authority._open
    real_legacy = node.require_gfsk250_bindings
    real_selected = mcu.require_selector_scope

    def build(unit, *, selected=True, model_selection=True, selector=AUTO):
        units = tuple(UnitCaps(u, BOOT, PeerID('mcu-node-source-control', c*64),
            Cost(1024, 240000000), Cost(1, 1000), c*64)
            for u, c in [('unit-a', 'a'), ('unit-c', 'c')])
        policy = ParentPolicy('1'*32, BOOT, 90000000, 180000000, 40000000,
            130000000, units, Cost(2048, 480000000), 'b'*64, mcu.KIND, 'c'*64)
        sender = 'unit-c' if unit == 'unit-a' else 'unit-a'
        request = b'source-only selected receiver request' if unit == 'unit-c' else None
        descriptor = dict(bytes=len(request), sha256=hashlib.sha256(request).hexdigest()) \
            if request is not None else dict(bytes=128, sha256='4'*64)
        pair = pair_packages(campaign_id='2'*32, resource_sha256='3'*64,
            application_expires_epoch_us=160000000, sender=sender, receiver=unit,
            transport='lora', format='MR1.request' if request is not None else 'MR1.response',
            descriptor=descriptor, source=None if request is not None else '5'*32,
            request=request, peers={u.unit: asdict(u.peer) for u in units},
            boots={u.unit: BOOT for u in units}, now_epoch_us=NOW[0],
            profile='gfsk250', radio_permission=dict(country='MY', antenna_confirmed=True),
            public32=True, mb1_descriptor_sha256='6'*64,
            mcu_artifact_selector=mcu.SELECTOR if selected else None)
        package = pair['packages'][unit]
        child = child_caps(package, policy).to_dict()
        plan = deepcopy(package['grant'])
        plan['application_deadline_monotonic_us'] = 120000000
        secret = base64.b64decode(package['secret_base64'])
        parent = dict(schema='meshticulum.native-parent-claim.v1', prepared=True,
            unit=unit, host_boot_id=BOOT, run_id=plan['session_id'],
            native_plan_sha256=hashlib.sha256(canonical(plan)).hexdigest(),
            original_plan_sha256=child['metadata']['plan_sha256'],
            grant_context_sha256=child['metadata']['grant_digest'],
            secret_sha256=hashlib.sha256(secret).hexdigest(),
            application_expires_epoch_us=plan['service']['application_expires_epoch_us'],
            application_deadline_monotonic_us=plan['application_deadline_monotonic_us'],
            child_caps=child)
        directory = tmp_path/'state'/'runs'/plan['session_id']
        directory.mkdir(mode=0o700, parents=True)
        for name, raw in [('grant.json', canonical(plan)), ('secret.bin', secret), ('active', b'active\n')]:
            path = directory/name
            path.write_bytes(raw)
            path.chmod(0o600)
        debt = directory/'prior-attempt-debt.bin'
        debt.write_bytes(b'unchanged source fixture paid/unknown debt')
        debt.chmod(0o600)
        events, authorities, held_fds = [], [], []
        facts = dict(unit=unit, host_boot_id=BOOT, services={'source-model.service': 'inactive'},
            explicitly_source_modeled=True)

        class ModelCaps:
            """A prepared source DTO, never a NativeCaps or RF permit."""
            def __init__(self):
                self.policy = policy
                self._approve = SimpleNamespace(mcu_artifact=mcu.artifact() if selected else None)
                self.owner_current = True
                self.current_values = [True]
                self.current_calls = self.consumed = 0
                self.closed = False

            def consume_start(self, run_id, supplied_plan, supplied_secret):
                assert not self.closed and self.consumed == 0
                assert run_id == plan['session_id'] and supplied_plan == plan and supplied_secret == secret
                self.consumed += 1
                events.append('consume_start')
                return deepcopy(parent)

            def public32_owner_current(self):
                events.append('original_owner_current')
                return self.owner_current

            def current(self):
                index = min(self.current_calls, len(self.current_values)-1)
                self.current_calls += 1
                events.append('parent_current')
                return self.current_values[index] if not self.closed else False

            def snapshot(self):
                return dict(explicitly_source_modeled=True, starts_spent=self.consumed,
                    original_unknown_actual_cost=None, refunds=0, global_debt_complete=False,
                    aggregate_paid=asdict(Cost(514, 120002000)))

            def close(self):
                self.closed = True

        caps = ModelCaps()

        def construct(*args, **kwargs):
            events.append('core_constructor')
            instance = real_authority.__new__(real_authority)
            authorities.append(instance)
            real_authority.__init__(instance, *args, **kwargs)
            return instance

        def opened(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            held_fds.append(fd)
            return fd

        def legacy(kind):
            events.append(('legacy_scope', kind))
            return real_legacy(kind)

        def selection(kind):
            events.append(('selected_scope', kind))
            return real_selected(kind)

        def stop_before_native_job(*args, **kwargs):
            events.append('stop_before_native_job')
            assert authorities[-1].owner_probe() is False
            raise RuntimeError(STOP)

        def forbidden(*args, **kwargs):
            pytest.fail('source control reached a native job/store/driver/RF path')

        monkeypatch.setattr(node, 'clock', lambda: NOW)
        monkeypatch.setattr(ip_node, '_facts', lambda requested: deepcopy(facts) if requested == unit else pytest.fail('wrong unit'))
        monkeypatch.setattr(pilot_native_caps, 'open_for_run', lambda *a, **k: caps)
        monkeypatch.setattr(node, 'RunAuthority', construct)
        monkeypatch.setattr(core_authority, '_open', opened)
        monkeypatch.setattr(node, 'require_gfsk250_bindings', legacy)
        monkeypatch.setattr(mcu, 'require_selector_scope', selection)
        monkeypatch.setattr(pilot_job, 'record', stop_before_native_job)
        for name in ('SessionStore', 'AttemptJournal', '_serial', 'LinuxHAT', 'BenchFlow'):
            monkeypatch.setattr(node, name, forbidden)
        if model_selection:
            # Selection structure only: this local table supplies no native owner,
            # Linux device ownership, broker lease, ARM or radio permission.
            monkeypatch.setattr(mcu, 'PENDING_NATIVE_BINDINGS', MappingProxyType(dict(
                scope_sha256='e'*64, history_receipt_sha256='f'*64,
                preceding_parent=PAID_HISTORY[-1])))
        else:
            # This explicit refusal model must stay pending after activation.
            monkeypatch.setattr(mcu, 'PENDING_NATIVE_BINDINGS', MappingProxyType(dict(
                scope_sha256=None, history_receipt_sha256=None, preceding_parent=None)))
        selected_argument = mcu.SELECTOR if selected and unit == 'unit-a' else None
        if selector is not AUTO:
            selected_argument = selector

        def run():
            result = node._run(unit, directory, tmp_path/'objects', public_service=True,
                mcu_artifact_selector=selected_argument)
            assert result['complete'] is False and result['facts_after'] == facts
            assert caps.consumed == 1 and caps.closed
            assert result['parent_caps']['starts_spent'] == 1
            assert result['parent_caps']['refunds'] == 0
            assert result['parent_caps']['original_unknown_actual_cost'] is None
            assert result['parent_caps']['global_debt_complete'] is False
            assert result['parent_caps']['aggregate_paid'] == asdict(Cost(514, 120002000))
            assert debt.read_bytes() == b'unchanged source fixture paid/unknown debt'
            assert all(a.closed and not a.fds for a in authorities)
            for fd in held_fds:
                with pytest.raises(OSError):
                    os.fstat(fd)
            assert not (directory/'wire.sqlite').exists()
            assert not (directory/'attempts.sqlite').exists()
            assert not any(name.startswith('native-job') for name in os.listdir(directory))
            return result

        return SimpleNamespace(run=run, caps=caps, parent=parent, plan=plan, secret=secret,
            directory=directory, events=events, authorities=authorities)

    return build


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
def test_selected_claim_reaches_real_constructor_without_legacy_scope(source_case, unit):
    case = source_case(unit)
    result = case.run()
    assert result['error'] == 'RuntimeError:'+STOP
    assert ('selected_scope', mcu.KIND) in case.events
    assert not any(type(e) is tuple and e[0] == 'legacy_scope' for e in case.events)
    authority, = case.authorities
    assert authority.application_expires_epoch_us == 160000000
    assert authority.application_deadline_us == 110000000
    assert authority.deadline_us == 110000000 and authority.grant.max_frames == 512
    assert authority.grant.digest.hex() == case.parent['grant_context_sha256']
    assert authority.profile.max_boot_attempts == 256
    assert authority.profile.max_boot_estimated_airtime_us == 60000000
    assert (case.directory/'started').read_bytes() == authority.grant.digest.hex().encode()+b'\n'
    assert case.events.index('consume_start') < case.events.index('original_owner_current') \
        < case.events.index(('selected_scope', mcu.KIND)) < case.events.index('core_constructor')


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
def test_pending_real_selector_scope_refuses_after_spent_claim_before_constructor(source_case, unit):
    case = source_case(unit, model_selection=False)
    result = case.run()
    assert 'pending_mcu_artifact_native_scope_and_history' in result['error']
    assert ('selected_scope', mcu.KIND) in case.events and not case.authorities
    assert not (case.directory/'started').exists()


@pytest.mark.parametrize('unit,selector', [('unit-a', None), ('unit-a', 'unknown'),
    ('unit-a', False), ('unit-c', mcu.SELECTOR), ('unit-c', 'unknown'), ('unit-c', False)])
def test_selector_direction_is_bound_to_consumed_claim(source_case, unit, selector):
    case = source_case(unit, selector=selector)
    assert 'mcu_artifact_original_claim_mismatch' in case.run()['error']
    assert not case.authorities and not any(type(e) is tuple for e in case.events)


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
@pytest.mark.parametrize('damage', ['image', 'digest', 'modem', 'board'])
def test_selected_metadata_substitution_never_reaches_scope(source_case, unit, damage):
    case = source_case(unit)
    metadata = case.parent['child_caps']['metadata']
    if damage == 'image':
        metadata['mcu_artifact']['image_sha256'] = '0'*64
    elif damage == 'digest':
        metadata['mcu_artifact_sha256'] = '0'*64
    elif damage == 'modem':
        metadata['radio_modem'] = 'gfsk125'
    else:
        metadata['mcu_artifact']['board_id'] = 2
    assert 'McuArtifactError:' in case.run()['error']
    assert not case.authorities and not any(type(e) is tuple for e in case.events)


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
@pytest.mark.parametrize('damage', ['false', 'truthy', 'artifact'])
def test_original_owner_refuses_before_scope(source_case, unit, damage):
    case = source_case(unit)
    if damage == 'artifact':
        case.caps._approve.mcu_artifact['image_sha256'] = '0'*64
    else:
        case.caps.owner_current = False if damage == 'false' else 1
    assert 'mcu_artifact_original_owner_changed' in case.run()['error']
    assert not case.authorities and not any(type(e) is tuple for e in case.events)


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
@pytest.mark.parametrize('damage', ['scope', 'history', 'preceding', 'kind'])
def test_selection_scope_keeps_exact_kind_history_and_preceding_guards(source_case, monkeypatch, unit, damage):
    case = source_case(unit)
    pins = dict(mcu.PENDING_NATIVE_BINDINGS)
    if damage == 'scope':
        pins['scope_sha256'] = True
    elif damage == 'history':
        pins['history_receipt_sha256'] = 'x'*64
    elif damage == 'preceding':
        pins['preceding_parent'] = PAID_HISTORY[-2]
    else:
        case.caps.policy = SimpleNamespace(qualification_kind='another-kind')
    monkeypatch.setattr(mcu, 'PENDING_NATIVE_BINDINGS', MappingProxyType(pins))
    assert 'pending_mcu_artifact_native_scope_and_history' in case.run()['error']
    assert not case.authorities


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
@pytest.mark.parametrize('field', ['native_plan_sha256', 'grant_context_sha256', 'secret_sha256',
    'application_expires_epoch_us', 'application_deadline_monotonic_us'])
def test_original_authority_binding_still_refuses_before_native_job(source_case, unit, field):
    case = source_case(unit)
    case.parent[field] = '0'*64 if field.endswith('sha256') else 90000000
    assert 'pilot_parent_authority_binding_changed' in case.run()['error']
    assert len(case.authorities) == 1 and (case.directory/'started').exists()
    assert 'stop_before_native_job' not in case.events


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
@pytest.mark.parametrize('after_gate', [False, True])
def test_parent_is_fresh_before_gate_and_after_constructor(source_case, unit, after_gate):
    case = source_case(unit)
    case.caps.current_values = [True, False] if after_gate else [False]
    result = case.run()
    assert ('pilot_parent_authority_binding_changed' if after_gate else 'pilot_parent_not_current') in result['error']
    assert len(case.authorities) == int(after_gate)
    assert 'stop_before_native_job' not in case.events


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
def test_nonselected_claim_keeps_original_legacy_refusal(source_case, unit):
    case = source_case(unit, selected=False)
    assert 'pending_fast_bundle_scope' in case.run()['error']
    assert ('legacy_scope', mcu.KIND) in case.events and not case.authorities
    assert not any(type(e) is tuple and e[0] == 'selected_scope' for e in case.events)
    assert not (case.directory/'started').exists()


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
def test_cli_selector_never_promotes_a_legacy_claim(source_case, unit):
    case = source_case(unit, selected=False, selector=mcu.SELECTOR)
    assert 'mcu_artifact_original_claim_mismatch' in case.run()['error']
    assert not case.authorities and not any(type(e) is tuple for e in case.events)


@pytest.mark.parametrize('unit', ['unit-a', 'unit-c'])
def test_previous_started_marker_is_never_replaced_or_refunded(source_case, unit):
    case = source_case(unit)
    path = case.directory/'started'
    path.write_bytes(b'previous spent original start\n')
    path.chmod(0o600)
    assert 'FileExistsError:' in case.run()['error']
    assert path.read_bytes() == b'previous spent original start\n'
    assert 'stop_before_native_job' not in case.events
