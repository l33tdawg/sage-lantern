"""Original real policy/owner, genuine local zero ledger; native facts modeled only."""
from copy import deepcopy
from dataclasses import asdict
import hashlib,json,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from lantern_meshticulum.bench import fast_bundle_zero_history as history,pilot_resume_rpc as rpc,pilot_parent_snapshot as snapshot,fast_bundle
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost,canonical
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts
ACTUAL=Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-history39-inputs-20261014/ORIGINAL_INPUTS.json')

@pytest.fixture
def original():
 raw=ACTUAL.read_bytes();assert hashlib.sha256(raw).hexdigest()=='6e3cd6dc46a2fe8369782f646bcd4832300b0fc73bf7699472160597238b2e1b'
 v=json.loads(raw);return v,ParentPolicy.from_dict(v['policy'])

@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_originalfast_context_decodes_without_current_live_scope(original,unit,monkeypatch):
 v,p=original;monkeypatch.setattr(fast_bundle,'require_bindings',lambda:pytest.fail('historical decoding used live scope'))
 c=history.original_context(v['owners'][unit],p,unit=unit)
 assert c.value['whole_bytes']==100000 and len(c.value['stages'])==5
 assert history.declared_partition(p)[unit]['declared_new_reservation']==asdict(Cost() if unit=='unit-b' else Cost(2560,600000000))

@pytest.mark.parametrize('fault',['owner','scope','clock','kind','caps'])
def test_originalowner_clock_partition_cannot_be_relabelled(original,fault):
 v,p=original;o=deepcopy(v['owners']['unit-a'])
 if fault=='owner':o['runtime_sha256']='f'*64
 elif fault=='scope':o['mb1_bundle_scope_sha256']='f'*64
 elif fault=='clock':o['phase_end_epoch_us']+=1
 elif fault=='kind':o['qualification_kind']='c-gfsk250-mb1-bundle100k-v1'
 else:
  pd=deepcopy(v['policy']);pd['units'][0]['limits']['radio_attempts']-=1;p=ParentPolicy.from_dict(pd)
 with pytest.raises(Exception):history.original_context(o,p,unit='unit-a')

def test_unobserved_H39_cannot_supply_retention_or_new_RF_scope(monkeypatch):
 from types import MappingProxyType
 monkeypatch.setattr(history,'EXPIRY_COMPLETION_SHA256',None)
 monkeypatch.setattr(history,'NEXT_HISTORY_RECEIPT_SHA256',None)
 monkeypatch.setattr(fast_bundle,'PHYSICAL_BINDINGS',MappingProxyType(dict(fast_bundle.PHYSICAL_BINDINGS,scope_sha256=None)))
 with pytest.raises(Exception,match='pending_2756'):history.require_final_bindings()
 with pytest.raises(Exception):fast_bundle.require_bindings()

@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def zero_rows(original,tmp_path,monkeypatch,request):
 v,p=original;u=request.param;o=v['owners'][u];local=p.unit(u);tmp_path.chmod(0o700)
 phase=o['native_phase'][u];epoch=phase['epoch_us']+1000;mono=phase['monotonic_us']+1000
 caps=NativeCaps.enroll(tmp_path/'native.sqlite',p,unit=u,clock=lambda:(epoch,mono),
  facts=lambda:NativeFacts(u,local.host_boot_id,local.peer),approve=lambda _:True,prior_check=lambda _:True,
  history_binding_sha256=o['history_binding_sha256']);assert caps.close()
 raw=(tmp_path/'native.sqlite').read_bytes();pin=rpc.sha(raw);db=sqlite3.connect(':memory:');b=bytearray(raw);b[18]=b[19]=1;db.deserialize(bytes(b));db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
 class Reader:
  name='native.sqlite'
  def read(self,name,maximum):return (tmp_path/name).read_bytes()
 parsed,detail,_=snapshot._native(db,SimpleNamespace(parent_id=p.parent_id,policy_digest=p.digest,unit=u),Reader());db.close()
 observed=dict(status='scoped_rows_observed',parent_id=p.parent_id,policy_digest=p.digest,unit=u,detail=detail,expected_database_sha256=pin,
  legacy_runs=None,legacy_relation=None,policy_expired_at_observation=True,
  observation_original_epoch_us=history.ORIGINAL_PHASE_END+1,observation_original_monotonic_us=phase['deadline_monotonic_us']+1)
 value=dict(owner=o,database_sha256=pin,actual_native_paid_caps=asdict(local.prior_debt),actual_controller_paid_caps=asdict(local.prior_debt),
  conservative_unused_enrollment_caps=asdict(Cost() if u=='unit-b' else Cost(2560,600000000)),paid_caps=asdict(local.limits),
  runs=[],retired_unstarted=[],controller_endpoint_refs=[],preparation_sha256={},legacy_sha256=None,require_result_complete=False,failed_started_runs=[])
 # Only an explicit model of observed pins; never persisted as an actual receipt.
 monkeypatch.setattr(history,'EXPIRY_COMPLETION_SHA256','a'*64);monkeypatch.setattr(history,'NEXT_HISTORY_RECEIPT_SHA256','b'*64)
 for unit in history.NATIVE_DATABASE_SHA256:monkeypatch.setitem(history.NATIVE_DATABASE_SHA256,unit,'c'*64)
 monkeypatch.setitem(history.NATIVE_DATABASE_SHA256,u,pin)
 return u,p,observed,value

def test_real_empty_ledger_retains_full_declared_limits_without_inventing_spent_use(zero_rows):
 u,p,o,v=zero_rows;rpc.qualify_history_claims(p,u,o,[],[]);rpc.qualify_fast_zero_costs(u,p,o,v)
 assert o['detail']['claim_count']==o['detail']['prepared_count']==o['detail']['start_count']==0
 assert v['paid_caps']==asdict(p.unit(u).limits) and v['actual_native_paid_caps']==asdict(p.unit(u).prior_debt)

@pytest.mark.parametrize('fault',['claim','resource','count_bool','db','epoch','mono','limit','unused','fake_run','fake_ref','binding'])
def test_zero_retention_refuses_mutation_without_refund(zero_rows,fault):
 u,p,o,v=zero_rows;o=deepcopy(o);v=deepcopy(v)
 if fault=='claim':o['detail']['claim_count']=1
 elif fault=='resource':o['detail']['resource_count']=1
 elif fault=='count_bool':o['detail']['claim_count']=False
 elif fault=='db':v['database_sha256']='f'*64
 elif fault=='epoch':o['observation_original_epoch_us']=p.expires_epoch_us-1
 elif fault=='mono':o['observation_original_monotonic_us']=0
 elif fault=='limit':o['detail']['limit']['radio_attempts']-=1
 elif fault=='unused':v['conservative_unused_enrollment_caps']['radio_attempts']+=1
 elif fault=='fake_run':v['runs']=[{}]
 elif fault=='fake_ref':v['controller_endpoint_refs']=[{}]
 else:o['detail']['enrollment']['history_binding_sha256']='f'*64
 with pytest.raises(Exception):rpc.qualify_fast_zero_costs(u,p,o,v)
