"""Exact successful32 exported native writer metadata; comparison only."""
from copy import deepcopy
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc,retained_capture
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy
from lantern_meshticulum.bench.history_metadata_fence import FenceError,HistoricalMetadataFence

BASE=Path(__file__).resolve().parents[5]/'meshticulum-native-window-20261011'
D=BASE/'c-bulk32-h18-public-20261011T173620Z-62bcef4f'
RAW=(D/'RESULT.json').read_bytes()
assert hashlib.sha256(RAW).hexdigest()=='e5a11599548d086c2f9d8024d7ff9434f79e831852a700368c01f1ba09ac9ef6'
RESULT=json.loads(RAW)
_spec=importlib.util.spec_from_file_location('h19_seal_fixture',Path(__file__).with_name('test_history_paid_parent_seal.py'))
_helpers=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_helpers)


@pytest.mark.parametrize('unit',('unit-a','unit-b','unit-c'))
def test_actual_success_claims_results_and_fullcaps_preserve_eighteen_prefix(unit):
    policy=ParentPolicy.from_dict(json.loads((D/'POLICY.json').read_bytes()))
    observation=RESULT['native_final'][unit]['terminal_observation']
    rpc.qualify_history_claims(policy,unit,observation,[{'run_id':r['run_id']} for r in observation['legacy_runs'] or []],[])
    history=json.loads((BASE/'resume-history-preparation-18'/(unit+'.json')).read_bytes())
    old=json.loads((BASE/'resume-history-preparation-17'/(unit+'.json')).read_bytes())
    latest=history['previous'][-1]
    rpc.qualify_public32_failure_scope(unit,policy.parent_id,latest)
    assert latest['require_result_complete'] is True and latest['failed_started_runs']==[]
    for link in RESULT['links']:
        if unit in link['outcomes']:
            rpc.qualify_public32_result(unit,link['run_id'],link['outcomes'][unit],parent_id=policy.parent_id)
    assert history['previous'][:-1]==old['previous']
    assert {k:v for k,v in history.items() if k!='previous'}=={k:v for k,v in old.items() if k!='previous'}
    assert latest['paid_caps']==RESULT['native_final'][unit]['snapshot']['debt']
    delta={k:v-old['previous'][-1]['paid_caps'][k] for k,v in latest['paid_caps'].items()}
    assert delta=={k:({'radio_attempts':512,'estimated_airtime_us':120000000}.get(k,0) if unit!='unit-b' else 0) for k in delta}
    assert all(r['actual_cost'] is None for r in history['legacy_inventory']['rows'])


@pytest.mark.parametrize('link',(0,1))
@pytest.mark.parametrize('field,value',[('complete',False),('complete',1),('disarmed_on_exit',False),('session_id','0'*32)])
def test_actual_success_canonical_hash_and_typed_complete_cannot_be_rebound(link,field,value):
    row=RESULT['links'][link];result=deepcopy(row['outcomes']['unit-a']);result[field]=value
    with pytest.raises(RuntimeError):rpc.qualify_public32_result('unit-a',row['run_id'],result,parent_id=rpc.PUBLIC32_COMPLETE_PAID)


@pytest.mark.parametrize('failed,complete',[([rpc.PUBLIC32_COMPLETE_RESPONSE],True),([],False),([],1),([rpc.PUBLIC32_COMPLETE_REQUEST],False)])
def test_known_success_scope_refuses_caller_failed_set_or_complete_substitution(failed,complete):
    with pytest.raises(RuntimeError):rpc.qualify_public32_failure_scope('unit-a',rpc.PUBLIC32_COMPLETE_PAID,
        dict(failed_started_runs=failed,require_result_complete=complete))


def test_old_eighteen_gate_refuses_before_native_reads(monkeypatch):
    old=json.loads((BASE/'resume-history-preparation-17/unit-a.json').read_bytes())
    class NoReader:
        def directory(self,*args):pytest.fail('native_read_before_nineteen_parent_gate')
    monkeypatch.setattr(rpc,'reserve_history_descriptor_capacity',lambda:None)
    with pytest.raises(RuntimeError,match='wrong_retained_parents'):
        rpc.full_history(NoReader(),'unit-a',old,lambda:pytest.fail('guard_before_nineteen_parent_gate'))


def test_old_source_and_captured_prefixes_remain_exact():
    base=BASE.parent/'meshticulum-h18-performance-pair-20261012/sage-lantern/software/meshticulum-integration/src/lantern_meshticulum/bench'
    def assignments(path,names):
        return {node.targets[0].id:ast.dump(node.value) for node in ast.parse(path.read_bytes()).body
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in names}
    old=assignments(base/'retained_capture.py',{'CAPTURE_FILES','UNADMITTED'})
    assert old==assignments(Path(retained_capture.__file__),set(old))
    old_history=next(ast.literal_eval(node.value) for node in ast.parse((base/'retained_capture.py').read_bytes()).body
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='PAID_HISTORY')
    assert retained_capture.PAID_HISTORY[:18]==old_history
    assert retained_capture.UNADMITTED_PAID_HISTORY==old_history[:12]
    assert len(rpc.HISTORY)==20 and rpc.HISTORY[18]==rpc.PUBLIC32_COMPLETE_PAID
    assert rpc.OLD_DIGESTS[18]==ParentPolicy.from_dict(json.loads((D/'POLICY.json').read_bytes())).digest


@pytest.mark.parametrize('count',(0,1,2,3))
def test_exact_success_prepared_identity_namespace_requires_zero_or_two(tmp_path,count):
    root,files,dirs=_helpers.fixture(tmp_path,count,'parents/'+rpc.PUBLIC32_COMPLETE_PAID)
    if count in (0,2):
        with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs) as fence:fence.check()
    else:
        with pytest.raises(FenceError):HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
