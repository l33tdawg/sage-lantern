"""Signed public bytes and real controller ledger; transport/process facts are models."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import pytest

from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench.lora_comparison import ComparisonContext,SSH_SCHEMA
from lantern_meshticulum.bench import mcu_artifact as mcu
from lantern_meshticulum.bench.pilot_controller import Controller
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps,canonical
from tests.test_lora_comparison import BOOT,PEERS,value,source_scope
from tests.test_pilot_controller import ModelOperator

sha=lambda raw:hashlib.sha256(raw).hexdigest()


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.fixture
def runner(monkeypatch):
    folder=Path(__file__).absolute().parents[3]/'firmware/meshticulum-bench'
    monkeypatch.syspath_prepend(str(folder))
    return load('exact_lora_comparison_runner',folder/'run_public_lora_comparison.py')


@pytest.fixture
def public(runner):
    directory=Path(tempfile.mkdtemp(prefix='.comparison-source-',dir=Path(__file__).parent))
    try:
        builder=load('comparison_signed_source_fixture',Path(runner.__file__).parent/'prepare_public_sf5_fixture.py')
        context,_,now=value();descriptor=deepcopy(context.descriptor);fixtures=[]
        for index,name in enumerate(('sf7-500','sf5-500')):
            path=directory/name;f=builder.build(path);row=f['chunks'][0]
            fixtures.append(dict(path=str(path),sha256=sha((path/'FIXTURE.json').read_bytes())))
            descriptor['samples'][index].update(resource_sha256=f['manifest_sha256'],chunk_sha256=row['content_address_sha256'],
                request=dict(bytes=74,sha256=row['request_sha256']),response=dict(bytes=8192,sha256=row['response_sha256']))
        context=ComparisonContext(descriptor,context.policy,phase_end_epoch_us=context.phase_end_epoch_us)
        op=dict(comparison_descriptor=descriptor,fixtures=fixtures)
        yield directory,context,now,op,runner.fixtures(op)
    finally:shutil.rmtree(directory)


def test_two_genuine_signed_resources_drive_actual_controller_all_eight_slots(public,runner):
    directory,context,now,op,pieces=public
    clock=lambda:now[1]/1000000
    class ModelComparisonOperator(ModelOperator):
        # Native process/crypto/physical receipts are explicitly modeled; the
        # signed cache, object hashes, Controller and SQLite ledger are genuine.
        def __init__(self):
            super().__init__(None);self.config=dict(schema=SSH_SCHEMA,lora_mcu_artifact=mcu.lora_artifact(),
                comparison_clock=dict(phase_start_epoch_us=context.policy.issued_epoch_us,
                    phase_end_epoch_us=context.phase_end_epoch_us,original_monotonic_us=context.policy.original_monotonic_us,
                    deadline_monotonic_us=context.policy.deadline_monotonic_us+5000000))
        def prepare(self,package):
            from lantern_meshticulum.bench.pilot_ssh import preparation_schema
            assert preparation_schema(self.config,package)=='meshticulum.provisioned-public-pilot.v2'
            receipt=super().prepare(package);claim=receipt['parent']
            claim.update(host_boot_id=BOOT,application_deadline_monotonic_us=now[1]+package['application_expires_epoch_us']-now[0])
            return receipt
        def start(self,unit,pair):
            handle=super().start(unit,pair)
            for (peer,run),result in self.results.items():
                package=self.packages[(peer,run)]
                result.update(host_boot_id=BOOT,original_application_expires_epoch_us=package['application_expires_epoch_us'],
                    start_monotonic_us=now[1])
            return handle
        def status(self,unit,run):
            return dict(super().status(unit,run),host_boot_id=BOOT,radio_receive_checked=True)
        def verify(self,unit,campaign,request,response,**options):
            from meshticulum.resources import knowledge_pack as kp
            piece=next(p for p in pieces if p['sample']['campaign_id']==campaign)
            raw=piece['cache'].piece(piece['sample']['chunk_sha256'])
            assert len(raw)==8164
            assert sha(raw)==piece['piece']['stored_chunk_sha256']
            assert kp.content_address(json.loads(raw))==piece['sample']['chunk_sha256']
            package=self.packages[(unit,response)];end=package['application_expires_epoch_us']
            return dict(schema='meshticulum.pilot-verified-piece.v1',publisher_verified=True,private_admission=False,
                sage_imported=False,unit=unit,campaign_id=campaign,manifest_sha256=piece['sample']['resource_sha256'],
                response_run_id=response,chunk_sha256=piece['sample']['chunk_sha256'],
                original_application_expires_epoch_us=end,original_application_deadline_us=self.claims[(unit,response)]['application_deadline_monotonic_us'],
                verified_monotonic_us=now[1]+1,useful_verified_bytes=8164,reused=False)
    parent=ParentCaps.enroll(directory/'controller.sqlite',context.policy,clock=lambda:tuple(now),boot_reader=lambda:BOOT)
    operator=ModelComparisonOperator();result=dict(samples=[],links=[])
    try:
        for index,piece in enumerate(pieces):
            if index:now[0]+=200000000;now[1]+=200000000
            expires=now[0]+180000000
            controller=Controller(operator,campaign_id=piece['sample']['campaign_id'],resource_sha256=piece['sample']['resource_sha256'],
                expires_epoch_us=expires,handoff_deadline_monotonic=context.policy.deadline_monotonic_us/1000000,
                peers=PEERS,boots={u:BOOT for u in PEERS},parent=parent,clock=clock,epoch=lambda:now[0],
                radio_permission=dict(country='MY',antenna_confirmed=True),radio_profile=piece['sample']['radio_profile_name'],
                lora_sender_first=True,comparison_context=context)
            verified=controller.direct_piece(piece['request'],piece['response'],'lora',publisher='unit-c',requester='unit-a')
            runner.finish_sample(operator,controller,result,index=index,original_application=dict(expires_epoch_us=expires),verified=verified,metrics={})
        snapshot=parent.snapshot()
        assert snapshot['allocations']==4
        assert snapshot['per_unit']['unit-a']['radio_attempts']==snapshot['per_unit']['unit-c']['radio_attempts']==1024
        assert snapshot['per_unit']['unit-b']['radio_attempts']==0
        assert len(result['samples'])==2 and len(result['links'])==4
        assert len([v for v in operator.events if v[0]=='prepare'])==8
        assert len([v for v in operator.events if v[0]=='revoke'])==8
        assert all(v['launch_order']==[v['sender'],v['receiver']] for v in result['links'])
    finally:parent.close()


def test_signed_fixture_corruption_refuses_before_controller_or_hardware(public,runner):
    directory,context,now,op,pieces=public
    path=Path(op['fixtures'][0]['path'])/'pack/manifest.sig.json';raw=bytearray(path.read_bytes());raw[-3]^=1;path.write_bytes(raw)
    with pytest.raises(Exception):runner.fixtures(op)


def test_unissued_template_refuses_before_source_paths_credentials_or_clock(public,runner,monkeypatch):
    directory,*_=public
    op=dict(schema=runner.SCHEMA,runner_sha256=sha(Path(runner.__file__).read_bytes()),preparation_only=True,
        not_for_issuance=True,window_issued=False)
    path=directory/'OPERATION.json';path.write_bytes(canonical(op));path.chmod(0o600)
    monkeypatch.setattr(runner.getpass,'getpass',lambda *_:pytest.fail('credential prompt reached'))
    monkeypatch.setattr(runner.time,'time_ns',lambda:pytest.fail('clock reached'))
    with pytest.raises(RuntimeError,match='comparison_operation_still_unissued'):runner.review(path,sha(path.read_bytes()))


@pytest.mark.parametrize('fail_directory_sync',[False,True])
def test_scope_marker_is_durable_before_use_and_cannot_rotate_operation(public,runner,monkeypatch,fail_directory_sync):
    directory,*_=public
    scope=dict(operation_directory=str(directory));path=directory/'SCOPE.json';path.write_bytes(canonical(scope));path.chmod(0o600)
    op=dict(scope_path=str(path),comparison_scope_sha256=sha(path.read_bytes()),output_directory=str(directory),retained_unissued_scopes=[])
    monkeypatch.setattr(runner,'check_unissued',lambda _:None)
    calls=[];real=os.fsync
    def synced(fd):
        kind='directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file';calls.append(kind)
        if kind=='directory' and fail_directory_sync:raise OSError('modeled directory sync failure')
        return real(fd)
    monkeypatch.setattr(os,'fsync',synced)
    if fail_directory_sync:
        with pytest.raises(OSError):runner.consume_scope(op,'a'*64,'b'*32,100,600000100)
    else:runner.consume_scope(op,'a'*64,'b'*32,100,600000100)
    assert calls==['file','directory']
    marker=directory/('COMPARISON_USED-'+op['comparison_scope_sha256']+'.json');assert marker.is_file()
    with pytest.raises(FileExistsError):runner.consume_scope(op,'c'*64,'d'*32,200,600000200)


def test_current_A_status_preserves_same_boot_and_cumulative_counters(runner):
    op=dict(radio_status_expected=dict(mcu_boot=runner.MCU_BOOT,boot_attempts=12,boot_reserved_estimated_airtime_us=30000))
    before=dict(schema='meshticulum.native-bench-inspection.v1',unit='unit-a',arm_commands=0,tx_commands=0,
        services_before={},services_after={},radio=dict(disarmed=True,firmware='mtb1-020e287255',usb_serial='F8:5B:1B:A5:F6:08',
        mcu_boot=runner.MCU_BOOT,boot_attempts=12,boot_reserved_estimated_airtime_us=30000,dropped_receive=0))
    assert runner.validate_a_status(before,op,None,min_remaining=160) is before
    later=deepcopy(before);later['radio'].update(boot_attempts=80,boot_reserved_estimated_airtime_us=50000)
    assert runner.validate_a_status(later,op,before,min_remaining=80) is later
    for damage,value in [('mcu_boot','0'*32),('boot_attempts',11),('boot_reserved_estimated_airtime_us',29999),('dropped_receive',1)]:
        bad=deepcopy(later);bad['radio'][damage]=value
        with pytest.raises(RuntimeError):runner.validate_a_status(bad,op,before,min_remaining=80)
