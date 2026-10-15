"""Actual pre-expiry rows with explicit local-only expired-clock models."""
from copy import deepcopy
from dataclasses import asdict
import hashlib,json,os
from pathlib import Path
import pytest
from lantern_meshticulum.bench import fast_bundle_unstarted_history as history,pilot_resume_rpc as rpc,fast_bundle
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost,canonical
BASE=Path('/Users/l33tdawg/nodejs-projects/sage-commons/work')
ATTEMPT=BASE/'meshticulum-gfsk250-physical-20261013/operations/v5/gfsk250-mb1-fast-bundle100k-9f90bdec3df97edbbe1e77cfc8ab836e'
DIAG=BASE/'meshticulum-guard9f90-diagnostic-20261013/readonly-diagnostic-1791866799858939'

def pinned(path,pin):
 raw=path.read_bytes();assert rpc.sha(raw)==pin;return json.loads(raw)

@pytest.fixture
def original():
 policy=pinned(ATTEMPT/'POLICY.json',history.POLICY_SHA256)
 owners=pinned(ATTEMPT/'OWNER_APPROVAL.json','c3982aec408085feeaa35de7dc2349594028fea86e2b4811f1c5802d5d7b3481')
 result=pinned(ATTEMPT/'RESULT.json',history.FINAL_RESULT_SHA256)
 return policy,owners,result

@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_original_context_and_full_reservation(original,unit,monkeypatch):
 pd,owners,_=original;p=ParentPolicy.from_dict(pd)
 monkeypatch.setattr(fast_bundle,'require_bindings',lambda:pytest.fail('historical decoder used live scope'))
 c=history.original_context(owners[unit],p,unit=unit)
 assert c.value['whole_bytes']==100000 and len(c.value['stages'])==5
 assert history.declared_partition(p)[unit]['declared_new_reservation']==asdict(Cost() if unit=='unit-b' else Cost(2560,600000000))
 assert rpc.HISTORY[:39]==history.H39_PREFIX and rpc.HISTORY[39]==history.PARENT
 assert len(rpc.OLD_DIGESTS)==len(rpc.HISTORY)==49 and rpc.OLD_DIGESTS[39]==history.POLICY_DIGEST

@pytest.mark.parametrize('field',['EXPIRY_COMPLETION_SHA256','NEXT_HISTORY_RECEIPT_SHA256','NATIVE_DATABASE_SHA256'])
def test_pending_actual_evidence_blocks_retention(field,monkeypatch):
 monkeypatch.setattr(history,field,dict.fromkeys(history.NATIVE_DATABASE_SHA256) if field=='NATIVE_DATABASE_SHA256' else None)
 with pytest.raises(Exception,match='pending_9f90'):history.require_final_bindings()

def test_no_new_rf_scope_is_inferred(monkeypatch):
 from types import MappingProxyType
 monkeypatch.setattr(fast_bundle,'PHYSICAL_BINDINGS',MappingProxyType(dict(fast_bundle.PHYSICAL_BINDINGS,scope_sha256=None,history_receipt_sha256=None)))
 assert fast_bundle.PHYSICAL_BINDINGS['scope_sha256'] is None
 assert fast_bundle.PHYSICAL_BINDINGS['history_receipt_sha256'] is None
 with pytest.raises(Exception,match='pending_fast_bundle_scope'):fast_bundle.require_bindings()

@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def modeled_expiry(original,request,monkeypatch):
 pd,owners,result=original;p=ParentPolicy.from_dict(pd);u=request.param;owner=owners[u]
 completion=pinned(DIAG/'COMPLETION.json','ae2d768079391da109362eb617dd6e59ce78eebb9778f9991c574f08390ac019')
 # This immutable diagnostic is explicitly pre-expiry. Only this test's copy models a later clock.
 raw=pinned(DIAG/(u+'.json'),completion['observations'][u]);a=deepcopy(raw['value']['observation'])
 assert not a['policy_expired_at_observation']
 count=0 if u=='unit-b' else 1;claimed=Cost() if not count else Cost(256,60000000)
 a.update(policy_expired_at_observation=True,observation_original_epoch_us=history.ORIGINAL_ABSOLUTE_END+1,
   observation_original_monotonic_us=owner['native_phase'][u]['deadline_monotonic_us']+110000000)
 rows=[] if not count else [dict(run_id=history.RUN,transport='lora',host_boot_id=p.unit(u).host_boot_id,
   grant_sha256='d'*64,prepared_identity_sha256=history.PREPARATION_SHA256[u])]
 refs=[dict(controller_allocation_id=c['controller_allocation_id'],child=c['child']) for c in a['detail']['claims']]
 v=dict(owner=owner,database_sha256=a['expected_database_sha256'],actual_native_paid_caps=asdict(p.unit(u).prior_debt.plus(claimed)),
  actual_controller_paid_caps=asdict(p.unit(u).prior_debt.plus(claimed)),paid_caps=asdict(p.unit(u).limits),
  conservative_unused_enrollment_caps=asdict(Cost() if not count else Cost(2304,540000000)),runs=[],retired_unstarted=rows,
  controller_endpoint_refs=refs,preparation_sha256={} if not count else {history.RUN:history.PREPARATION_SHA256[u]},
  fast_stage_sha256={} if not count else {'fast-stage-0.json':history.FAST_STAGE_SHA256[u]},legacy_sha256=None,
  require_result_complete=False,failed_started_runs=[])
 monkeypatch.setattr(history,'EXPIRY_COMPLETION_SHA256','a'*64);monkeypatch.setattr(history,'NEXT_HISTORY_RECEIPT_SHA256','b'*64)
 for unit in history.NATIVE_DATABASE_SHA256:monkeypatch.setitem(history.NATIVE_DATABASE_SHA256,unit,'c'*64)
 monkeypatch.setitem(history.NATIVE_DATABASE_SHA256,u,a['expected_database_sha256'])
 return u,p,a,v

def test_observed_prepared_rows_retain_whole_enrollment(modeled_expiry):
 u,p,a,v=modeled_expiry;rpc.qualify_history_claims(p,u,a,[],v['retired_unstarted']);history.qualify_costs(u,p,a,v)
 assert a['detail']['start_count']==0 and v['paid_caps']==asdict(p.unit(u).limits)
 assert v['actual_native_paid_caps']==a['detail']['prior_plus_prepaid_caps']

def test_captured_unstarted_rows_through_real_sqlite_parser(modeled_expiry,original,tmp_path):
 import sqlite3
 from types import SimpleNamespace
 from lantern_meshticulum.bench import pilot_parent_snapshot as snapshot
 from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,hashed
 u,p,a,v=modeled_expiry;pd,owners,result=original;local=p.unit(u);tmp_path.chmod(0o700)
 caps=NativeCaps.enroll(tmp_path/'native.sqlite',p,unit=u,
   clock=lambda:(p.issued_epoch_us+1000,1000000000),facts=lambda:NativeFacts(u,local.host_boot_id,local.peer),
   approve=lambda _:True,prior_check=lambda _:True,history_binding_sha256='e'*64)
 assert caps.close()
 image=bytearray((tmp_path/'native.sqlite').read_bytes());image[18]=image[19]=1
 db=sqlite3.connect(':memory:');db.deserialize(bytes(image));detail=a['detail'];e=detail['enrollment']
 enrollment=dict(schema='meshticulum.native-parent-enrollment.v2',policy=pd,
   **{k:e[k] for k in ('unit','host_boot_id','original_epoch_us','original_monotonic_us','deadline_monotonic_us','history_binding_sha256')})
 db.execute('UPDATE enrollment SET body=? WHERE id=1',(canonical(enrollment),))
 sc=detail['stored_clock'];db.execute('UPDATE clock SET epoch=?,mono=? WHERE id=1',(sc['epoch_us'],sc['monotonic_us']))
 resources={}
 for row in detail['resources']:
  body={k:x for k,x in row.items() if k not in ('resource_id','row_sha256')};resources[row['resource_id']]=body
  assert rpc.sha(canonical(body))==row['row_sha256']
  db.execute('INSERT INTO resources VALUES(?,?)',(row['resource_id'],canonical(body)))
 for row in detail['claims']:
  receipt=result['links'][0]['prepared_parent_claims'][u]
  body=dict(child=receipt['child_caps'],controller_allocation_id=receipt['controller_allocation_id'],resource_id=row['resource_id'],resource=resources[row['resource_id']])
  assert hashed(body)==row['native_claim_id'] and rpc.sha(canonical(body))==row['row_sha256']
  db.execute('INSERT INTO claims VALUES(?,?,?)',(row['native_claim_id'],canonical(body),history.RUN))
  db.execute('INSERT INTO prepared VALUES(?,?)',(row['native_claim_id'],canonical(receipt)))
 db.commit();db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
 class Reader:
  name='native.sqlite'
  def read(self,name,maximum):
   assert name=='native.sqlite.approval';return (hashed(enrollment)+'\n').encode()
 scope=SimpleNamespace(parent_id=p.parent_id,policy_digest=p.digest,unit=u)
 snapshot._schema(db,'native');parsed,actual,_=snapshot._native(db,scope,Reader());db.close()
 assert actual==detail and parsed==p and actual['start_count']==0
 history.qualify_costs(u,p,dict(a,detail=actual),v)

@pytest.mark.parametrize('fault',['claim','resource','boolean_count','start','db','phase','mono','owner','unused','limit','paid','controller','fake_run','fake_ref','preparation','stage','unstarted','binding'])
def test_actual_retention_mutations_refused(modeled_expiry,fault):
 u,p,a,v=modeled_expiry;a=deepcopy(a);v=deepcopy(v)
 if fault=='claim':a['detail']['claims'].append({})
 elif fault=='resource':a['detail']['resources'].append({})
 elif fault=='boolean_count':a['detail']['claim_count']=bool(a['detail']['claim_count'])
 elif fault=='start':a['detail']['start_count']=1
 elif fault=='db':v['database_sha256']='f'*64
 elif fault=='phase':a['observation_original_epoch_us']=history.ORIGINAL_ABSOLUTE_END-1
 elif fault=='mono':a['observation_original_monotonic_us']=0
 elif fault=='owner':v['owner']['phase_end_epoch_us']+=1
 elif fault=='unused':v['conservative_unused_enrollment_caps']['radio_attempts']+=1
 elif fault=='limit':a['detail']['limit']['radio_attempts']-=1
 elif fault=='paid':v['paid_caps']['radio_attempts']-=1
 elif fault=='controller':v['actual_controller_paid_caps']['radio_attempts']+=1
 elif fault=='fake_run':v['runs']=[{}]
 elif fault=='fake_ref':v['controller_endpoint_refs'].append({})
 elif fault=='preparation':v['preparation_sha256']['f'*32]='f'*64
 elif fault=='stage':v['fast_stage_sha256']['fast-stage-1.json']='f'*64
 elif fault=='unstarted':v['retired_unstarted'].append({})
 else:a['detail']['enrollment']['history_binding_sha256']='f'*64
 with pytest.raises(Exception):history.qualify_costs(u,p,a,v)

@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_stage_namespace_is_exact_and_bytes_are_pinned(tmp_path,monkeypatch,unit):
 parent=tmp_path/'parents'/history.PARENT;parent.mkdir(parents=True)
 class Reader:
  def directory(self,path):assert path.is_dir()
  def read(self,path,maximum):return path.read_bytes()
  def check(self):return True
 raw=b'local public stage model';expected={} if unit=='unit-b' else {'fast-stage-0.json':rpc.sha(raw)}
 monkeypatch.setitem(history.FAST_STAGE_SHA256,unit,None if unit=='unit-b' else rpc.sha(raw))
 if unit!='unit-b':(parent/'fast-stage-0.json').write_bytes(raw)
 assert history.stage_files(Reader(),tmp_path,unit,{'fast_stage_sha256':expected})=={
   'parents/'+history.PARENT+'/'+k:v for k,v in expected.items()}
 (parent/'fast-stage-1.json').write_bytes(b'{}')
 with pytest.raises(Exception,match='stage_namespace'):history.stage_files(Reader(),tmp_path,unit,{'fast_stage_sha256':expected})

@pytest.fixture(params=['unit-a','unit-c'])
def actual_expired_metadata(request,original,tmp_path):
 import base64
 unit=request.param;pd,owners,result=original;p=ParentPolicy.from_dict(pd)
 expiry=BASE/'meshticulum-history40-inputs-20261013/expired-parent-observation-1791868093633419'
 completion=pinned(expiry/'COMPLETION.json','ecf611c2d1fc1fb01909d17a560b3a323e79ca81a4a054c5fe27e4cd5f898b2d')
 native=pinned(expiry/(unit+'.json'),completion['observations'][unit])['value'];a=native['observation'];meta=native['prepared_metadata']
 assert native['original_clocks_expired'] is True and a['policy_expired_at_observation'] is True
 raw={key:base64.b64decode(record['base64'],validate=True) for key,record in meta['public_metadata'].items()}
 assert all(rpc.sha(raw[key])==record['sha256'] for key,record in meta['public_metadata'].items())
 parent=tmp_path/'parents'/history.PARENT;target=tmp_path/'runs'/history.RUN
 (parent/'prepared-identities').mkdir(parents=True);target.mkdir(parents=True)
 for name in meta['run_namespace']:(target/name).touch()
 identity=json.loads(raw['parents/'+history.PARENT+'/prepared-identities/'+history.RUN+'.json'])
 row=dict(run_id=history.RUN,transport='lora',host_boot_id=p.unit(unit).host_boot_id,
  grant_sha256=rpc.sha(raw['runs/'+history.RUN+'/grant.json']),prepared_identity_sha256=meta['prepared_identity_sha256'])
 raw['runs/'+history.RUN+'/active']=b'retired\n'
 class Reader:
  directories={target:identity['directory_identity']}
  opened=[]
  def read(self,path,maximum,lock=False):
   name=str(path.relative_to(tmp_path));self.opened.append(name);value=raw[name];assert len(value)<=maximum;return value
  def directory(self,path):assert path.is_dir()
  def check(self):return True
 reader=Reader();now=lambda:(a['observation_original_epoch_us'],a['observation_original_monotonic_us'])
 return unit,p,a,row,raw,reader,tmp_path,now

def test_actual_retired_unstarted_public_bytes_and_fast_stage_are_all_bound(actual_expired_metadata):
 u,p,a,row,raw,reader,state,now=actual_expired_metadata
 result=history.retired_metadata(reader,state,u,p,a,row,now,lambda:p.unit(u).host_boot_id)
 assert result['prepared'] is True and result['started'] is False and result['actual_cost'] is None
 assert result['no_refund'] is True and result['physical_quiescence_observed'] is False
 assert result['files']['parents/'+history.PARENT+'/fast-stage-0.json']==history.FAST_STAGE_SHA256[u]
 assert set(result['files'])==set(reader.opened)==set(raw)
 assert all(not name.endswith(('secret.bin','sender.bin','receiver.bin')) for name in reader.opened)

@pytest.mark.parametrize('fault',['grant','provision','identity','stage','extra_stage_start','started','result','active','live_clock'])
def test_actual_retired_reader_refuses_rebinding_or_runtime_records(actual_expired_metadata,fault):
 u,p,a,row,raw,reader,state,now=actual_expired_metadata
 if fault in ('grant','provision'):
  key='runs/'+history.RUN+'/'+fault+'.json';raw[key]=raw[key].replace(b'1140ada832368459a91a5d390d7c3e52',b'1140ada832368459a91a5d390d7c3e53')
 elif fault=='identity':raw['parents/'+history.PARENT+'/prepared-identities/'+history.RUN+'.json']+=b' '
 elif fault=='stage':raw['parents/'+history.PARENT+'/fast-stage-0.json']+=b' '
 elif fault=='active':raw['runs/'+history.RUN+'/active']=b'active\n'
 elif fault=='live_clock':now=lambda:(p.issued_epoch_us,p.original_monotonic_us)
 else:(state/'runs'/history.RUN/({'result':'result.json','started':'started','extra_stage_start':'attempts.sqlite'}[fault])).touch()
 with pytest.raises(Exception):history.retired_metadata(reader,state,u,p,a,row,now,lambda:p.unit(u).host_boot_id)


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_actual_h40_receipt_and_expiry_qualify_without_modeling_final_pins(unit):
 expiry=BASE/'meshticulum-history40-inputs-20261013/expired-parent-observation-1791868093633419'
 output=BASE/'meshticulum-history40-inputs-20261013/resume-history-preparation-40'
 completion=pinned(expiry/'COMPLETION.json',history.EXPIRY_COMPLETION_SHA256)
 receipt=pinned(output/'RECEIPT.json',history.NEXT_HISTORY_RECEIPT_SHA256)
 payload=pinned(output/(unit+'.json'),receipt['payload_sha256'][unit]);value=payload['previous'][-1]
 observed=pinned(expiry/(unit+'.json'),completion['observations'][unit])['value']['observation']
 policy=ParentPolicy.from_dict(value['policy'])
 rpc.qualify_history_claims(policy,unit,observed,value['runs'],value['retired_unstarted'])
 history.qualify_costs(unit,policy,observed,value)
 assert history.pending_next_roster()==rpc.HISTORY[:40]
