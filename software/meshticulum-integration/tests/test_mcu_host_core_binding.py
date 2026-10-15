"""Host-only Core selection; firmware and historical paid owners stay exact."""
from copy import deepcopy
import hashlib
from types import MappingProxyType, SimpleNamespace

import pytest

from lantern_meshticulum.bench import mcu_artifact as mcu, fast_bundle as fast, node
from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
from tests.test_mcu_original_operation import original, runner
from tests.test_fast_bundle import fast_data
from tests.test_pilot_bundle import data


FIRMWARE_CORE = '2b9db9e71dfa7d115d4e465225893b828c760171'
HOST_CORE = 'd828aef5be0ed5aeff472f79bc3935fe50c17092'
HOST_LOCK = '07a39b44c0313e53839010fd96512b6c72732a5b9eeace00926db88d26ef13ff'
OLD_IRQ_SHA = '9e478abcb07234a2f42ed70966b3ac4d82c303bafd8e20a8c5b08eaf681a1f57'


@pytest.fixture
def modeled_current_bindings(monkeypatch):
    """Pure source context only: no consent, device facts, or native owner."""
    pins = dict(scope_sha256='9'*64, history_receipt_sha256='a'*64,
                preceding_parent=PAID_HISTORY[-1])
    monkeypatch.setattr(mcu, 'PENDING_NATIVE_BINDINGS', MappingProxyType(pins))
    return pins


def manifest(selected):
    return dict(mcu_artifact=deepcopy(selected), firmware=selected['firmware'],
        firmware_by_unit={'unit-a':selected['firmware']}, radio_profile_name='gfsk250',
        native_operation_kind=mcu.KIND,
        source_files={'provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json':selected['source_lock_sha256']})


def test_host_pairing_preserves_both_historical_artifacts_and_all_firmware_fields():
    old = mcu.artifact(mcu.IRQ020_SELECTOR)
    current = mcu.artifact()
    assert hashlib.sha256(mcu.canonical(old)).hexdigest() == mcu.IRQ020_ARTIFACT_SHA256 == OLD_IRQ_SHA
    assert hashlib.sha256(mcu.canonical(mcu.artifact(mcu.LEGACY_SELECTOR))).hexdigest() == (
        '72b1289b782f54940c0bced77e19118e5c8ae47e1fea51ecf112e597419c582b')
    assert {name for name in current if current[name] != old[name]} == {
        'selector', 'runtime_core_revision', 'source_lock_sha256'}
    assert current['core_revision'] == old['core_revision'] == mcu.FIRMWARE_CORE == FIRMWARE_CORE
    assert current['runtime_core_revision'] == mcu.CORE == HOST_CORE
    assert current['source_lock_sha256'] == HOST_LOCK
    assert current['selector'] == 'gfsk250-usb-rx2048-irq-hostfair-v1'
    assert current['schema'] == old['schema'] == 'meshticulum.explicit-mcu-artifact.v2'


@pytest.mark.parametrize('selector', [mcu.LEGACY_SELECTOR, mcu.IRQ020_SELECTOR, mcu.SELECTOR])
def test_each_original_raw_owner_keeps_its_original_runtime_core_and_descriptor(selector):
    selected = mcu.artifact(selector)
    original = dict(schema='meshticulum.finite-public-resume-owner.v1', qualification_kind=mcu.KIND,
        core_commit=selected.get('runtime_core_revision',selected['core_revision']),
        policy_digest='b'*64, mb1_bundle_scope_sha256='c'*64,
        history_receipt_sha256='d'*64, actual_cost=None, complete_global_debt=False)
    raw = mcu.canonical(mcu.owner_envelope(original,selector))
    import json
    decoded, artifact = mcu.decode_owner(json.loads(raw))
    assert decoded == original and artifact == selected
    assert mcu.canonical(mcu.owner_envelope(decoded,selector)) == raw


@pytest.mark.parametrize('field,value', [
    ('runtime_core_revision',FIRMWARE_CORE), ('source_lock_sha256','b504150554abdf068eeabe1d140db1a0c132ecb93c84c3b351049cf78ca4d904'),
    ('core_revision',HOST_CORE), ('selector',mcu.IRQ020_SELECTOR),
])
def test_current_artifact_cannot_mix_old_host_or_relabel_firmware_source(field,value):
    selected = mcu.artifact()
    selected[field] = value
    with pytest.raises(mcu.McuArtifactError,match='mcu_artifact_binding_changed'):
        mcu.validate_artifact(selected)


@pytest.mark.parametrize('core',[FIRMWARE_CORE, '0'*40])
def test_current_owner_refuses_firmware_core_as_host_core(core):
    original = dict(schema='meshticulum.finite-public-resume-owner.v1',
                    qualification_kind=mcu.KIND,core_commit=core)
    with pytest.raises(mcu.McuArtifactError,match='mcu_original_owner_mismatch'):
        mcu.owner_envelope(original,mcu.SELECTOR)


def test_current_runtime_requires_current_host_lock_while_historical_manifest_stays_readable():
    current = mcu.artifact()
    assert mcu.select_runtime(manifest(current)) == current
    old = mcu.artifact(mcu.IRQ020_SELECTOR)
    assert mcu.select_runtime(manifest(old)) == old
    value = manifest(current)
    value['source_files']['provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json'] = old['source_lock_sha256']
    with pytest.raises(mcu.McuArtifactError,match='mcu_artifact_runtime_source_changed'):
        mcu.select_runtime(value)


def test_historical_irq_runtime_retains_its_original_strict_lock_check():
    value = manifest(mcu.artifact(mcu.IRQ020_SELECTOR))
    value['source_files']['provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json'] = HOST_LOCK
    with pytest.raises(mcu.McuArtifactError,match='mcu_artifact_runtime_source_changed'):
        mcu.select_runtime(value)


@pytest.mark.parametrize('selector',[mcu.LEGACY_SELECTOR,mcu.IRQ020_SELECTOR])
def test_historical_selectors_refuse_before_serial_or_current_native_binding(monkeypatch,selector):
    monkeypatch.setattr(node,'ExclusiveSerial',lambda *a,**k:pytest.fail('serial must remain closed'))
    monkeypatch.setattr(mcu,'require_selector_scope',lambda *a:pytest.fail('historical selector must refuse first'))
    with pytest.raises(mcu.McuArtifactError,match='historical_mcu_artifact_not_current'):
        node._serial('unit-a',radio_modem='gfsk250',qualification_kind=mcu.KIND,mcu_artifact_selector=selector)
    selected = mcu.artifact(selector)
    original = dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=mcu.KIND,
        core_commit=selected.get('runtime_core_revision',selected['core_revision']),policy_digest='a'*64)
    wrapped = mcu.owner_envelope(original,selector)
    with pytest.raises(mcu.McuArtifactError,match='historical_mcu_artifact_not_current'):
        mcu.require_native_scope(wrapped,SimpleNamespace(digest='a'*64,qualification_kind=mcu.KIND),manifest(selected))


def test_operation_binding_uses_host_core_before_any_native_work(original,runner,modeled_current_bindings):
    op = original['op']
    assert runner.operation_bindings(op) == dict(modeled_current_bindings,core_commit=HOST_CORE)
    old = deepcopy(op)
    old.update(mcu_artifact_selector=mcu.IRQ020_SELECTOR,mcu_artifact=mcu.artifact(mcu.IRQ020_SELECTOR),
               core_commit=FIRMWARE_CORE)
    with pytest.raises(Exception,match='wrong_original_mcu_operation'):
        runner.operation_bindings(old)


def test_fast_owner_uses_host_core_and_genuine_signed_bundle_context(original,modeled_current_bindings):
    context = fast.require_owner(original['common'],original['policy'])
    assert context.value == original['value'] and context.transport is not None
    assert context.policy is original['policy']
    assert context.value['deadline_monotonic_us'] == original['value']['deadline_monotonic_us']


def test_current_source_pins_still_refuse_without_a_reviewed_new_scope(original,monkeypatch):
    pending = dict(mcu.PENDING_NATIVE_BINDINGS)
    pending['scope_sha256'] = None
    monkeypatch.setattr(mcu,'PENDING_NATIVE_BINDINGS',MappingProxyType(pending))
    assert mcu.PENDING_NATIVE_BINDINGS['scope_sha256'] is None
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu'):
        fast.require_owner(original['common'],original['policy'])
