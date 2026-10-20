"""Exact actual H17 exported writer metadata; no native execution/authority."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc,retained_capture
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy

BASE=Path(__file__).resolve().parents[5]/'meshticulum-native-window-20261011'
D=BASE/'c-bulk32-h16-public-20261011T155052Z-8dfcfdfe'
RESULT_RAW=(D/'RESULT.json').read_bytes()
assert hashlib.sha256(RESULT_RAW).hexdigest()=='b89329b5ecaf35aafa7d86fbd6d839f229b1b54b5fdadf344dddd09d3d19af37'
RESULT=json.loads(RESULT_RAW)


@pytest.mark.parametrize('unit',('unit-a','unit-b','unit-c'))
def test_genuine_writer_claim_set_and_known_complete_incomplete_results(unit):
    policy=ParentPolicy.from_dict(json.loads((D/'POLICY.json').read_bytes()))
    observation=RESULT['native_final'][unit]['terminal_observation']
    runs=[{'run_id':r['run_id']} for r in observation['legacy_runs'] or []]
    rpc.qualify_history_claims(policy,unit,observation,runs,[])
    history=json.loads((BASE/'resume-history-preparation-16'/(unit+'.json')).read_bytes())
    latest=history['previous'][-1];rpc.qualify_public32_failure_scope(unit,policy.parent_id,latest)
    for link in RESULT['links']:
        if unit in link['outcomes']:rpc.qualify_public32_result(unit,link['run_id'],link['outcomes'][unit],parent_id=policy.parent_id)
    old=json.loads((BASE/'resume-history-preparation-15'/(unit+'.json')).read_bytes())
    assert history['previous'][:-1]==old['previous'] and history['legacy_inventory']==old['legacy_inventory']
    assert {k:v for k,v in history.items() if k!='previous'}=={k:v for k,v in old.items() if k!='previous'}
    assert latest['paid_caps']==RESULT['native_final'][unit]['snapshot']['debt']
    assert all(r['actual_cost'] is None for r in history['legacy_inventory']['rows'])
    assert retained_capture.UNADMITTED_PAID_HISTORY==retained_capture.PAID_HISTORY[:12]


@pytest.mark.parametrize('field,value',[('complete',True),('disarmed_on_exit',False),('session_id','0'*32)])
def test_mutated_actual_failed_outcome_never_reclassified_as_success(field,value):
    link=next(v for v in RESULT['links'] if v['run_id']==rpc.PUBLIC32_RETRY_RESPONSE)
    result=deepcopy(link['outcomes']['unit-a']);result[field]=value
    with pytest.raises(RuntimeError):rpc.qualify_public32_result('unit-a',link['run_id'],result,parent_id=rpc.PUBLIC32_RETRY_PAID)


@pytest.mark.parametrize('failed,complete',[([],False),([rpc.PUBLIC32_RETRY_REQUEST],False),([rpc.PUBLIC32_RETRY_RESPONSE],True),
    ([rpc.PUBLIC32_RETRY_REQUEST,rpc.PUBLIC32_RETRY_RESPONSE],False)])
def test_caller_supplied_failure_boolean_or_extra_scope_cannot_change_known_outcomes(failed,complete):
    with pytest.raises(RuntimeError):rpc.qualify_public32_failure_scope('unit-a',rpc.PUBLIC32_RETRY_PAID,
        {'failed_started_runs':failed,'require_result_complete':complete})


def test_real_builder_requires_every_original_callback_and_job_terminal():
    spec=importlib.util.spec_from_file_location('h17_actual_builder',BASE/'rollover_inputs_h17.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    assert builder.checked_result(RESULT)
    for field in ('local_client_terminal','remote_RPC_terminal_proven','returncode'):
        result=deepcopy(RESULT);result['rpc_clients'][0][field]=False
        with pytest.raises(RuntimeError):builder.checked_result(result)
    result=deepcopy(RESULT);result['final_native_statuses'][0]['live']=None
    with pytest.raises(RuntimeError):builder.checked_result(result)
