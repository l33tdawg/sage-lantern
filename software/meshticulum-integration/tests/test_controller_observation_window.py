"""Real protected controller SQLite, signed v3 data; modeled clocks/boot only."""
import hashlib
import os
import json
from pathlib import Path
import pytest
from meshticulum.resources import resource
from lantern_meshticulum.bench import bundle_checkpoint_root as root,pilot_parent_snapshot as snapshot
from lantern_meshticulum.bench.pilot_bundle import BundleContext
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps
from lantern_meshticulum.bench.pilot_controller import pair_packages
from tests.test_bundle_v3_native import v3
from tests.test_pilot_bundle import data,packet,PEERS,BOOT
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_public32_pilot import tmp_path

TIMING=Path(__file__).parent/'fixtures/controller_checkpoint_observation_timing.json'
TIMING_SHA='e12825f8923cac128bbb68a332182f415ec3bb8b80b76586e259008d09eee630'


def recorded_remaining():
 raw=TIMING.read_bytes();assert root.sha(raw)==TIMING_SHA
 value=json.loads(raw);first=value['callbacks'];assert len(first)==2
 assert value['source_result_sha256']=='806b3c3f77cd6f3add9b0933bec48e7f5f063053e7cef03d06581f3bdc75ee40'
 assert all(v['returncode']==0 and v['callback_completed'] is v['remote_terminal_proven'] is v['local_closed'] is True for v in first)
 end=value['first_handoff_end_epoch_us'];assert end-value['original_epoch_us']==345000000
 remaining=end-max(v['finished_epoch_us'] for v in first)
 assert remaining==73270088
 return remaining


def test_optional_primary_result_crosscheck():
 path=os.environ.get('MESHTICULUM_PRIMARY_TIMING_RESULT')
 if path is None:pytest.skip('optional primary record is not part of the portable control')
 raw=Path(path).read_bytes();excerpt=json.loads(TIMING.read_bytes())
 assert root.sha(raw)==excerpt['source_result_sha256'];value=json.loads(raw)
 assert value['original_whole']['epoch_us']==excerpt['original_epoch_us']
 assert value['original_whole']['epoch_us']+345000000==excerpt['first_handoff_end_epoch_us']
 assert [{k:row[k] for k in ('finished_epoch_us','returncode','local_closed','callback_completed','remote_terminal_proven')}
  for row in value['checkpoint_callbacks']]==excerpt['callbacks']


@pytest.fixture
def early_closed_ledger(v3,tmp_path,root_observation):
 _,chunks,value,policy=v3;context=BundleContext(value,policy)
 now=[(value['stages'][0]['issued_epoch_us']+1000,policy.original_monotonic_us+105000000+1000)]
 clock=lambda:now[0];boot=lambda:BOOT
 directory=tmp_path/'controller';directory.mkdir(mode=0o700)
 parent=ParentCaps.enroll(directory/'caps.sqlite',policy,clock=clock,boot_reader=boot)
 request=packet(value,policy,0,now[0][0]);raw,entry=context.exchange(0);part=chunks[entry['chunk_sha256']]
 response=resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(raw).digest()[:16],0,len(part),0)+part
 reply=pair_packages(campaign_id=value['stages'][0]['campaign_id'],resource_sha256=value['manifest_sha256'],
  application_expires_epoch_us=value['stages'][0]['expires_epoch_us'],sender='unit-c',receiver='unit-a',transport='lora',format='MR1.response',
  descriptor=dict(bytes=len(response),sha256=root.sha(response)),source=dict(kind='served_public_chunk',request_run_id=request['run_id']),
  request=None,peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=now[0][0],radio_permission={'country':'MY','antenna_confirmed':True},public32=True)
 for pair in (request,reply):parent.consume(parent.prepay_pair(tuple(pair['packages'].values())))
 handoff=value['stages'][0]['handoff_end_epoch_us'];epoch=handoff-recorded_remaining()
 now[0]=(epoch,policy.original_monotonic_us+epoch-policy.issued_epoch_us)
 try:yield parent,context,now
 finally:
  if not parent._closed:parent.close()


def test_installed_unclipped_handoff_scope_refuses_before_reader_or_SQL_even_with_genuine_two_rows(early_closed_ledger,monkeypatch):
 parent,context,now=early_closed_ledger;policy=parent.policy
 assert parent.close()
 raw=parent.path.read_bytes();entered=[];original=snapshot._Reader
 def reader(*a,**k):entered.append(True);return original(*a,**k)
 monkeypatch.setattr(snapshot,'_Reader',reader)
 scope=snapshot.Scope('meshticulum.public-parent-snapshot-scope.v1','controller',os.geteuid(),policy.parent_id,
  policy.digest,root.sha(raw),None,BOOT,context.value['stages'][0]['handoff_end_epoch_us'])
 observed=snapshot.collect(parent.path.parent,scope,clock=parent._clock,boot_reader=parent._boot).to_dict()
 assert observed['status']=='unavailable' and observed['reason']=='observation_expired_or_unbounded' and entered==[]
 assert observed['sqlite_parser']['native_path_opened'] is False


def test_bounded_original_controller_observation_reopens_same_real_ledger_without_new_caps(early_closed_ledger,monkeypatch):
 parent,context,now=early_closed_ledger;before=parent.snapshot();identity=tuple(parent._ids['db']);scopes=[];original=root.collect
 def capture_scope(state,scope,**kw):scopes.append(scope);return original(state,scope,**kw)
 monkeypatch.setattr(root,'collect',capture_scope)
 reopened,observed=root.controller_observation(parent,context,1)
 try:
  assert observed['status']=='scoped_rows_observed' and observed['scoped_rows_complete'] is True
  assert observed['pending_unknown'] is False and observed['detail']['allocation_count']==2
  assert scopes[0].observation_expires_epoch_us==min(context.value['stages'][0]['handoff_end_epoch_us'],now[0][0]+55000000)
  assert reopened.policy is parent.policy and tuple(reopened._ids['db'])==identity and reopened.snapshot()==before
  assert reopened.current() is True and observed['database_writes'] is False
 finally:assert reopened.close()


@pytest.mark.parametrize('fault',['original_handoff_expired','original_whole_expired','mono_regressed','clock_advanced_during_observation'])
def test_original_owner_windows_and_clock_checks_still_refuse(early_closed_ledger,monkeypatch,fault):
 parent,context,now=early_closed_ledger
 if fault=='original_handoff_expired':
  delta=context.value['stages'][0]['handoff_end_epoch_us']-now[0][0];now[0]=(now[0][0]+delta,now[0][1]+delta)
 elif fault=='original_whole_expired':now[0]=(parent.policy.expires_epoch_us,parent.policy.deadline_monotonic_us)
 elif fault=='mono_regressed':now[0]=(now[0][0],parent.policy.original_monotonic_us-1)
 else:
  actual=root.collect
  def expired(*a,**k):
   observed=actual(*a,**k);now[0]=(now[0][0]+80000000,now[0][1]+80000000);return observed
  monkeypatch.setattr(root,'collect',expired)
 with pytest.raises(Exception):root.controller_observation(parent,context,1)
