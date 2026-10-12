"""Genuine local enrollment/typed parser; modeled approval, facts and clock, no RF."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc, pilot_parent_snapshot as snapshot
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps, NativeFacts
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy, Cost
from lantern_meshticulum.bench import retained_capture, resume_parent_guard

ROOT=Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-native-window-20261011')
ACTUAL=ROOT/'bundle100k-public-20261011T192151Z-141c892d'
POLICY_FIXTURE=Path(__file__).parent/'fixtures/h20-zero-enrollment-policy.json'


@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def zero_rows(tmp_path,monkeypatch,request):
    tmp_path.chmod(0o700);unit=request.param
    policy=ParentPolicy.from_dict(json.loads(POLICY_FIXTURE.read_bytes()));local=policy.unit(unit)
    caps=NativeCaps.enroll(tmp_path/'native.sqlite',policy,unit=unit,
        clock=lambda:(policy.issued_epoch_us+1000,1000000000),
        facts=lambda:NativeFacts(unit,local.host_boot_id,local.peer),approve=lambda _:True,prior_check=lambda _:True,
        history_binding_sha256='e'*64)
    assert caps.close()
    raw=(tmp_path/'native.sqlite').read_bytes();pin=rpc.sha(raw)
    db=sqlite3.connect(':memory:');image=bytearray(raw);image[18]=image[19]=1;db.deserialize(bytes(image))
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
    class Reader:
        name='native.sqlite'
        def read(self,name,maximum):
            assert name=='native.sqlite.approval';return (tmp_path/name).read_bytes()
    scope=SimpleNamespace(parent_id=policy.parent_id,policy_digest=policy.digest,unit=unit)
    snapshot._schema(db,'native');parsed,detail,_=snapshot._native(db,scope,Reader());db.close()
    observed=dict(detail=detail,expected_database_sha256=pin,legacy_runs=None,legacy_relation=None,
        policy_expired_at_observation=True,observation_original_epoch_us=policy.expires_epoch_us+1,
        observation_original_monotonic_us=detail['enrollment']['deadline_monotonic_us']+1)
    extra=Cost(2560,600000000) if unit!='unit-b' else Cost()
    value=dict(owner={'history_binding_sha256':'e'*64},database_sha256=pin,
        actual_native_paid_caps=asdict(local.prior_debt),actual_controller_paid_caps=asdict(local.prior_debt),
        conservative_unused_enrollment_caps=asdict(extra),paid_caps=asdict(local.limits),runs=[],retired_unstarted=[],
        controller_endpoint_refs=[],preparation_sha256={},legacy_sha256=None,require_result_complete=False,
        failed_started_runs=[])
    # Production exact DB pins refer to the real device artifact. This explicit
    # model-only override lets the genuine new local ledger exercise the predicate.
    monkeypatch.setitem(rpc.ENROLLED_ZERO_DATABASES,unit,pin)
    return unit,parsed,observed,value


def test_genuine_enrollment_keeps_old_spent_debt_and_conservatively_retains_full_limits(zero_rows):
    unit,policy,observed,value=zero_rows;detail=observed['detail']
    assert detail['claim_count']==detail['prepared_count']==detail['start_count']==detail['resource_count']==0
    assert detail['full_prepaid_caps']==asdict(Cost())
    assert detail['prior_plus_prepaid_caps']==asdict(policy.unit(unit).prior_debt)
    rpc.qualify_history_claims(policy,unit,observed,[],[])
    rpc.qualify_enrolled_zero_costs(unit,policy,observed,value)
    assert value['paid_caps']==detail['limit']
    assert observed['detail']['prior_plus_prepaid_caps']==value['actual_native_paid_caps']


@pytest.mark.parametrize('fault',['claim','prepared','start','resource','boolean_count','binding','db','prior','limit',
    'epoch_live','mono_live','expiry_flag','clock_bool','refund','double_charge','fake_run','fake_ref','identity','complete','failure','legacy'])
def test_zero_child_conservative_bound_refuses_changed_actual_or_scope(zero_rows,fault):
    unit,policy,observed,value=zero_rows;observed=deepcopy(observed);value=deepcopy(value);d=observed['detail']
    if fault in ('claim','prepared','start','resource'):d[fault+'_count']=1
    elif fault=='boolean_count':d['claim_count']=False
    elif fault=='binding':d['enrollment']['history_binding_sha256']='f'*64
    elif fault=='db':value['database_sha256']='f'*64
    elif fault=='prior':value['actual_native_paid_caps']['radio_attempts']+=1
    elif fault=='limit':d['limit']['radio_attempts']+=1
    elif fault=='epoch_live':observed['observation_original_epoch_us']=policy.expires_epoch_us-1
    elif fault=='mono_live':observed['observation_original_monotonic_us']=d['enrollment']['deadline_monotonic_us']-1
    elif fault=='expiry_flag':observed['policy_expired_at_observation']=False
    elif fault=='clock_bool':observed['observation_original_monotonic_us']=True
    elif fault=='refund':value['paid_caps']=deepcopy(value['actual_native_paid_caps']);value['paid_caps']['ip_frames']-=1
    elif fault=='double_charge':value['paid_caps']['radio_attempts']+=2560
    elif fault=='fake_run':value['runs']=[dict(run_id='a'*32)]
    elif fault=='fake_ref':value['controller_endpoint_refs']=[{}]
    elif fault=='identity':value['preparation_sha256']={'a'*32:'f'*64}
    elif fault=='complete':value['require_result_complete']=True
    elif fault=='failure':value['failed_started_runs']=None
    elif fault=='legacy':observed['legacy_runs']=[]
    with pytest.raises(RuntimeError):rpc.qualify_enrolled_zero_costs(unit,policy,observed,value)


def test_exact_twenty_parent_roster_keeps_nineteen_and_capture_prefix_twelve():
    assert len(retained_capture.PAID_HISTORY)==len(rpc.OLD_DIGESTS)==26
    assert retained_capture.PAID_HISTORY[19]==rpc.ENROLLED_ZERO_PAID
    assert resume_parent_guard.HISTORY is retained_capture.PAID_HISTORY
    assert len(retained_capture.UNADMITTED_PAID_HISTORY)==12
    assert retained_capture.PAID_HISTORY[18]=='62bcef4f1c74532fd7ab3ada1b8b94e2'
    if (ROOT/'resume-history-preparation-18/unit-a.json').is_file():
        old=json.loads((ROOT/'resume-history-preparation-18/unit-a.json').read_bytes())
        assert list(retained_capture.PAID_HISTORY[:19])==[r['policy']['parent_id'] for r in old['previous']]


def test_actual_live_terminal_metadata_does_not_prove_expiry_or_create_rollover():
    path=ROOT/'rollover_inputs_h20.py'
    if not path.is_file() or not ACTUAL.is_dir():pytest.skip('local source preparation artifact not packaged')
    spec=importlib.util.spec_from_file_location('h20_builder',path)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    raw=(ACTUAL/'RESULT.json').read_bytes();result=json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==helper.RESULT_SHA
    assert helper.checked_result(result) is True
    assert all(v['terminal_observation']['policy_expired_at_observation'] is False
               for v in result['native_final'].values())
    with pytest.raises(RuntimeError,match='actual_expired_native_observations_required'):helper.assemble(None)
