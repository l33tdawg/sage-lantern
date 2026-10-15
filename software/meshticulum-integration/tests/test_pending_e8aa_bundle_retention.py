"""Actual declared metadata only; expiry observations below are inert vectors."""
from copy import deepcopy
from dataclasses import asdict,replace
import json
from pathlib import Path
import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import gfsk_bundle_history as history,gfsk_bundle,retained_capture,profiles
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost

DATA=json.loads(Path(__file__).with_name('fixtures').joinpath('current-e8aa-declared-gfsk-bundle.json').read_bytes())


@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def original(request):
    unit=request.param;return unit,ParentPolicy.from_dict(DATA['policy']),DATA['owners'][unit]


def expired_vector(unit,owner):
    return dict(status='scoped_rows_observed',parent_id=history.PARENT,policy_digest=history.POLICY_DIGEST,
        unit=unit,policy_expired_at_observation=True,observation_original_epoch_us=history.ORIGINAL_PHASE_END,
        observation_original_monotonic_us=owner['native_phase'][unit]['deadline_monotonic_us'])


def test_declared_reservations_preserve_each_old_cap_without_consumption_claim(original):
    unit,policy,owner=original;rows=history.declared_partition(policy);row=rows[unit]
    extra=Cost() if unit=='unit-b' else Cost(2560,600000000)
    assert row['declared_new_reservation']==asdict(extra) and row['actual_consumption'] is None and row['refunds']==0
    assert row['full_original_caps']==asdict(policy.unit(unit).prior_debt.plus(extra))
    context=history.original_context(owner,policy,unit=unit)
    assert context.value['whole_bytes']==100000 and len(context.value['stages'])==5
    assert context.value['expires_epoch_us']==1791831164309719
    assert not DATA['native_observations_collected'] and not DATA['result_or_completion_proved']


def test_bound_actual_evidence_appends_only_e8aa_without_live_authority(original,monkeypatch):
    assert len(retained_capture.PAID_HISTORY)==48 and retained_capture.PAID_HISTORY[33]==history.PARENT
    assert history.FINAL_RESULT_SHA256=='c71b030dd357aa1e109b716f0e7ae1f9f6fb4ba1a64a0fad01e657aa4f79bcc4'
    assert history.EXPIRY_COMPLETION_SHA256=='b8d4b312298ec0296a6240b52aeda5ee064edc1da309ac931fa985b5134a2bd6'
    assert history.NEXT_HISTORY_RECEIPT_SHA256=='ddebe2592ab011cad4a7c4e0057764e2cddab2350b4e48e37eb125a0f0e5a648'
    assert {u:len(v) for u,v in history.NATIVE_OUTCOME_SHA256.items()}=={'unit-a':10,'unit-b':0,'unit-c':10}
    assert history.pending_next_roster()==retained_capture.PAID_HISTORY
    monkeypatch.setattr(history,'EXPIRY_COMPLETION_SHA256',None)
    with pytest.raises(WireError,match='pending_e8aa_final_result_expiry_and_history'):history.pending_next_roster()


def test_historical_metadata_decode_is_separate_from_later_live_readiness(original,monkeypatch):
    unit,policy,owner=original
    monkeypatch.setattr(profiles,'GFSK_HISTORY_RECEIPT_SHA256',None)
    monkeypatch.setattr(gfsk_bundle,'GFSK_BUNDLE_SCOPE_SHA256',None)
    context=history.retained_context(owner,policy,unit=unit,observation=expired_vector(unit,owner))
    assert context.policy is policy
    with pytest.raises(WireError):gfsk_bundle.require_owner(owner,policy)


@pytest.mark.parametrize('fault',['live','epoch','mono','parent','policy','unit','owner','modem','scope','runtime','whole','refund'])
def test_exact_original_context_cannot_renew_clocks_rebind_or_refund(original,fault):
    unit,policy,owner=original;owner=deepcopy(owner);observed=expired_vector(unit,owner)
    if fault=='live':observed['policy_expired_at_observation']=False
    elif fault=='epoch':observed['observation_original_epoch_us']-=1
    elif fault=='mono':observed['observation_original_monotonic_us']-=1
    elif fault=='parent':observed['parent_id']='f'*32
    elif fault=='policy':observed['policy_digest']='f'*64
    elif fault=='unit':observed['unit']='unit-z'
    elif fault=='owner':owner['policy_digest']='f'*64
    elif fault=='modem':owner['radio_modem']='lora'
    elif fault=='scope':owner['gfsk_bundle_scope_sha256']='f'*64
    elif fault=='runtime':owner['runtime_sha256']='f'*64
    elif fault=='whole':owner['bundle_context']['whole_sha256']='f'*64
    else:
        units=tuple(replace(u,limits=u.limits.plus(Cost(1,1))) if u.unit==unit else u for u in policy.units)
        policy=replace(policy,units=units,aggregate_limits=policy.aggregate_limits.plus(Cost(1,1)))
    with pytest.raises((WireError,RuntimeError,ValueError)):
        history.retained_context(owner,policy,unit=unit,observation=observed)
