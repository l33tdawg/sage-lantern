"""Actual unissued H46 source binding; no newly constructed native grant."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType

import pytest
from lantern_meshticulum.bench import mcu_artifact as mcu,fast_bundle_360_history as history
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy


@pytest.fixture(scope='module')
def actual():
    value=os.environ.get('MESHTICULUM_H46_FIXTURE')
    if not value:pytest.skip('MESHTICULUM_H46_FIXTURE must name the actual reviewed H46 lane')
    base=Path(value);raw=(base/'SCOPE.json').read_bytes();scope=json.loads(raw)
    receipt_raw=(base/'resume-history-preparation-46/RECEIPT.json').read_bytes()
    assert hashlib.sha256(receipt_raw).hexdigest()==history.NEXT_HISTORY_RECEIPT_SHA256
    receipt=json.loads(receipt_raw)
    owners=json.loads((base/'completed-parent-supplement-v2/INPUT-unit-a.json').read_bytes())
    return scope,raw,receipt,owners


def test_exact_reviewed_unissued_scope_and_history_source_binding(actual):
    scope,raw,receipt,p=actual
    expected=dict(scope_sha256=hashlib.sha256(raw).hexdigest(),history_receipt_sha256=history.NEXT_HISTORY_RECEIPT_SHA256,preceding_parent=history.PARENT)
    assert dict(mcu.PENDING_NATIVE_BINDINGS)==mcu.require_selector_scope(mcu.KIND)==expected
    assert scope['reviewed_implementation_commit']=='8015bca2bfacb1623c60738773923d890ac9211f'
    assert scope['core_commit']==mcu.CORE=='b9ce259a915e1336ee2eda5c2cbe413d370e6b9d'
    assert scope['mcu_artifact']==mcu.artifact()
    assert scope['source_lock_sha256']==mcu.artifact()['source_lock_sha256']
    assert scope['mcu_artifact']['core_revision']==mcu.FIRMWARE_CORE
    assert scope['paid_history_count']==receipt['local_controllers_reobserved']==46
    assert scope['history_parent_ids']==list(history.pending_next_roster())
    assert scope['window_issued'] is scope['native_permission'] is scope['qualification_complete'] is False
    assert scope['maximum_whole_transfers']==1 and scope['parts']==5
    assert (scope['whole_seconds'],scope['phase_seconds'],scope['part_application_seconds'],scope['absolute_window_seconds'])==(1350,1355,180,1500)
    assert scope['refunds']==0 and scope['global_debt_complete']is False and scope['original_unknown_actual_cost']is None
    assert scope['retained_unissued_host_scopes'][0]==scope['retained_unissued_host_scope']
    assert [p['sha256']for p in scope['retained_unissued_host_scopes']]==[
        'c922e922b97590a69fc331a83134c6d94f0ee700a2524b803c7e2dc0928c5f1f',
        'ba84af81f913d73013fdefa1b550725795f91d0bea11ee0e7367169b72999e50',
        '3d88fb98b68c02c865fae66faa935744687227bf6cf9a07f595890ec774f0152',
        'c4ce1ce81748084ede8b8731c1479fc70f36a196ad5601a7651c570e66014c9e']
    assert all(p['window_issued']is False and p['original_scope_not_consumed']is True
        and p['no_paid_history_entry_added']is True and p['refunds']==0
        for p in scope['retained_unissued_host_scopes'])


def test_actual_new_boot_does_not_replace_paid_original_boot_or_caps(actual):
    scope,raw,receipt,p=actual
    fpath=Path(os.environ['MESHTICULUM_H46_FIXTURE']).parent/'meshticulum-next100k-h46-intake-20261015/freshboot/FRESH_BOOT.json'
    fresh_raw=fpath.read_bytes();fresh=json.loads(fresh_raw)
    assert hashlib.sha256(fresh_raw).hexdigest()=='9b780798e492b2a4db87fd08f89090563d67b3cba42a86366ff5fe639f76db3c'
    assert fresh['complete']is True and fresh['closure']['owned_group_gone']is True
    assert fresh['observation']['old_radio']['mcu_boot'].endswith('0037')
    assert fresh['observation']['old_radio']['boot_attempts']==150
    assert fresh['observation']['old_radio']['boot_reserved_estimated_airtime_us']==418560
    requirement=scope['fresh_radio_requirement'];new=fresh['observation']['new_inspection']['radio']
    assert requirement['expected_mcu_boot']==new['mcu_boot']=='000008f6a51b5bf80000000000000038'
    assert requirement['actual_current_status_sha256']==requirement['actual_reset_and_new_boot_receipt_sha256']==hashlib.sha256(fresh_raw).hexdigest()
    assert requirement['current_same_boot_inspection_required']is requirement['zero_counters_are_new_boot_fact_not_refund']is True
    assert scope['full_H46_prior_caps']['unit-a']==p['whole_parent_paid_caps']
    assert p['owner_record']['original_owner']['mb1_bundle_scope_sha256']==history.ORIGINAL_SCOPE_SHA256
    assert receipt['prior_H45_original_run_complete']is receipt['prior_H44_original_run_complete']is False


def test_original_paid_owner_does_not_become_a_new_scope_grant(actual):
    scope,raw,receipt,p=actual
    manifest_path=Path(os.environ['MESHTICULUM_H46_FIXTURE']).parent/'meshticulum-e244-repaired-host-final-20261015/MANIFEST.json'
    manifest=json.loads(manifest_path.read_text())
    owner=deepcopy(p['owner_record']);before=json.dumps(owner,sort_keys=True)
    with pytest.raises(mcu.McuArtifactError,match='historical_mcu_artifact_not_current'):
        mcu.require_native_scope(owner,ParentPolicy.from_dict(p['policy']),manifest)
    assert json.dumps(owner,sort_keys=True)==before


@pytest.mark.parametrize('field',['scope_sha256','history_receipt_sha256','preceding_parent'])
def test_missing_current_source_binding_refuses_before_admission(actual,monkeypatch,field):
    values=dict(mcu.PENDING_NATIVE_BINDINGS);values[field]=None
    monkeypatch.setattr(mcu,'PENDING_NATIVE_BINDINGS',MappingProxyType(values))
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu_artifact_native_scope_and_history'):
        mcu.require_selector_scope(mcu.KIND)


def test_original_scope45_remains_closed_against_active46(actual,monkeypatch):
    monkeypatch.setattr(mcu,'PENDING_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256=history.ORIGINAL_SCOPE_SHA256,history_receipt_sha256=history.ORIGINAL_HISTORY_RECEIPT_SHA256,
        preceding_parent='e2449e5bcd4af15d45f7a756dff7d997')))
    with pytest.raises(mcu.McuArtifactError,match='pending_mcu_artifact_native_scope_and_history'):
        mcu.require_selector_scope(mcu.KIND)
