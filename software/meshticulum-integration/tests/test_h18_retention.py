"""Exact actual H18 exported writer metadata; no native execution/authority."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc,retained_capture
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy

BASE=Path(__file__).resolve().parents[5]/'meshticulum-native-window-20261011'
D=BASE/'c-bulk32-h17-public-20261011T164425Z-6f89e2a2'
RESULT_RAW=(D/'RESULT.json').read_bytes()
assert hashlib.sha256(RESULT_RAW).hexdigest()=='c80e7d9f20f03c8b444c92a335df3d0dfc47a6f79c01b8c2b30b590da3d5ef43'
RESULT=json.loads(RESULT_RAW)


@pytest.mark.parametrize('unit',('unit-a','unit-b','unit-c'))
def test_genuine_writer_claim_set_and_known_complete_incomplete_results(unit):
    policy=ParentPolicy.from_dict(json.loads((D/'POLICY.json').read_bytes()))
    observation=RESULT['native_final'][unit]['terminal_observation']
    runs=[{'run_id':r['run_id']} for r in observation['legacy_runs'] or []]
    rpc.qualify_history_claims(policy,unit,observation,runs,[])
    history=json.loads((BASE/'resume-history-preparation-17'/(unit+'.json')).read_bytes())
    latest=history['previous'][-1];rpc.qualify_public32_failure_scope(unit,policy.parent_id,latest)
    for link in RESULT['links']:
        if unit in link['outcomes']:rpc.qualify_public32_result(unit,link['run_id'],link['outcomes'][unit],parent_id=policy.parent_id)
    old=json.loads((BASE/'resume-history-preparation-16'/(unit+'.json')).read_bytes())
    assert history['previous'][:-1]==old['previous'] and history['legacy_inventory']==old['legacy_inventory']
    assert {k:v for k,v in history.items() if k!='previous'}=={k:v for k,v in old.items() if k!='previous'}
    assert latest['paid_caps']==RESULT['native_final'][unit]['snapshot']['debt']
    assert all(r['actual_cost'] is None for r in history['legacy_inventory']['rows'])
    assert retained_capture.UNADMITTED_PAID_HISTORY==retained_capture.PAID_HISTORY[:12]


@pytest.mark.parametrize('field,value',[('complete',True),('disarmed_on_exit',False),('session_id','0'*32)])
def test_mutated_actual_failed_outcome_never_reclassified_as_success(field,value):
    link=next(v for v in RESULT['links'] if v['run_id']==rpc.PUBLIC32_SENDER_READY_RESPONSE)
    result=deepcopy(link['outcomes']['unit-a']);result[field]=value
    with pytest.raises(RuntimeError):rpc.qualify_public32_result('unit-a',link['run_id'],result,parent_id=rpc.PUBLIC32_SENDER_READY_PAID)


@pytest.mark.parametrize('failed,complete',[([],False),([rpc.PUBLIC32_SENDER_READY_REQUEST],False),([rpc.PUBLIC32_SENDER_READY_RESPONSE],True),
    ([rpc.PUBLIC32_SENDER_READY_REQUEST,rpc.PUBLIC32_SENDER_READY_RESPONSE],False)])
def test_caller_supplied_failure_boolean_or_extra_scope_cannot_change_known_outcomes(failed,complete):
    with pytest.raises(RuntimeError):rpc.qualify_public32_failure_scope('unit-a',rpc.PUBLIC32_SENDER_READY_PAID,
        {'failed_started_runs':failed,'require_result_complete':complete})


def test_real_builder_requires_every_original_callback_and_job_terminal():
    spec=importlib.util.spec_from_file_location('h18_actual_builder',BASE/'rollover_inputs_h18.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    assert builder.checked_result(RESULT)
    for field in ('local_client_terminal','remote_RPC_terminal_proven','returncode'):
        result=deepcopy(RESULT);result['rpc_clients'][0][field]=False
        with pytest.raises(RuntimeError):builder.checked_result(result)
    result=deepcopy(RESULT);result['final_native_statuses'][0]['live']=None
    with pytest.raises(RuntimeError):builder.checked_result(result)


def test_h17_prefix_and_both_opaque_capture_bodies_stay_source_exact():
    base=Path(__file__).resolve().parents[5]
    original=base/'meshticulum-h17-bundle-merge-20261012/sage-lantern/software/meshticulum-integration/src/lantern_meshticulum/bench'
    # Read only the actual constant assignments; imported native source is not
    # rebound, and these exported constants provide no enrollment authority.
    import ast
    def constants(path,names):
        value={}
        for node in ast.parse(path.read_bytes()).body:
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in names:
                value[node.targets[0].id]=ast.literal_eval(node.value) if node.targets[0].id=='PAID_HISTORY' else ast.dump(node.value)
        return value
    old=constants(original/'retained_capture.py',{'PAID_HISTORY','CAPTURE_FILES','UNADMITTED'})
    assert retained_capture.PAID_HISTORY[:17]==old['PAID_HISTORY']
    current=constants(Path(retained_capture.__file__),{'CAPTURE_FILES','UNADMITTED'})
    assert current['CAPTURE_FILES']==old['CAPTURE_FILES'] and current['UNADMITTED']==old['UNADMITTED']
    policy=ParentPolicy.from_dict(json.loads((D/'POLICY.json').read_bytes()))
    assert tuple(rpc.HISTORY)==retained_capture.PAID_HISTORY and rpc.OLD_DIGESTS[17]==policy.digest
    assert len(rpc.HISTORY)==46 and rpc.HISTORY[17]==policy.parent_id
    assert rpc.HISTORY[22]==rpc.V2_FAILED_BUNDLE_PAID
    assert rpc.HISTORY[23]==rpc.V3_PARTIAL_BUNDLE_PAID


def test_actual_old17_input_cannot_enter_full_native_history_before_any_read(monkeypatch):
    old=json.loads((BASE/'resume-history-preparation-16/unit-a.json').read_bytes())
    class NoReader:
        def directory(self,*args):pytest.fail('native_reader_reached_before_18_parent_gate')
    monkeypatch.setattr(rpc,'reserve_history_descriptor_capacity',lambda:None)
    with pytest.raises(RuntimeError,match='wrong_retained_parents'):
        rpc.full_history(NoReader(),'unit-a',old,lambda:pytest.fail('guard_reached_before_18_parent_gate'))
