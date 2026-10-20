"""Finite CLI source replay with invented management; no device evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import io

import pytest
from lantern_meshticulum.ip.node import _write
from tests.test_meshticulum_resources import pack
from tests.test_pilot_ssh import config
from meshticulum.contracts.identity import PeerID
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps, ParentPolicy, UnitCaps, Cost


def load():
    path=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/run_public_pilot.py'
    spec=importlib.util.spec_from_file_location('public_pilot_entry',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def enroll_entry_parent(module, tmp_path, cfg, monkeypatch):
    """Explicit synthetic controller enrollment; no native approval implied."""
    boot='00000000-0000-4000-8000-000000000001'
    monkeypatch.setattr(module,'controller_boot',lambda:boot)
    epoch,mono=module.parent_clock();limits=Cost(4096,960000000,65536,67108864,67108864)
    units=tuple(UnitCaps(unit,row['host_boot_id'],PeerID('meshticulum-public-pilot-v1',hashlib.sha256(
        ('meshticulum-public-pilot-v1/'+unit+'/'+row['host_boot_id']).encode()).hexdigest()),
        limits,Cost(1,1000,1,128,128),str(index)*64) for index,(unit,row) in enumerate(cfg['units'].items(),1))
    total=Cost()
    for unit in units:total=total.plus(unit.limits)
    policy=ParentPolicy('1'*32,boot,epoch-1000000,cfg['handoff_end_epoch_us'],mono-1000000,
        mono+cfg['handoff_end_epoch_us']-epoch,units,total,'d'*64)
    directory=tmp_path/'parent';directory.mkdir(mode=0o700)
    ledger=directory/'caps.sqlite';parent=ParentCaps.enroll(ledger,policy,
        clock=module.parent_clock,boot_reader=module.controller_boot);assert parent.close()
    path=directory/'policy.json';_write(path,json.dumps(policy.to_dict()).encode())
    return ['--parent-policy',str(path),'--parent-ledger',str(ledger)]


@pytest.mark.parametrize('fault,reuse',[(fault,reuse)
    for reuse in (False,True)
    for fault in (None,'lifecycle','retirement','transfer','late_cleanup','management_cleanup')
    if reuse or fault!='management_cleanup'])
def test_complete_entry_preserves_manifest_only_recipient_and_failure_evidence(pack,tmp_path,monkeypatch,fault,reuse):
    module=load();cfg,_=config();cfg['handoff_end_epoch_us']=module.time.time_ns()//1000+600000000
    manifest=b'{"public_parent_claims_required":true}';cfg['manifest_sha256']=hashlib.sha256(manifest).hexdigest()
    archive=tmp_path/'runtime.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        item=tarfile.TarInfo('manifest.json');item.size=len(manifest);tar.addfile(item,io.BytesIO(manifest))
    cfg['runtime_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
    path=tmp_path/'config.json';_write(path,json.dumps(cfg).encode())
    trust=tmp_path/'trust.json';_write(trust,json.dumps(pack[1]).encode())
    output=tmp_path/'results';seeds=[];calls=[]
    parent_args=enroll_entry_parent(module,tmp_path,cfg,monkeypatch)
    class Operator:
        def __init__(self,*args,**kwargs):self.deadline=module.time.monotonic()+600;self.passwords={};self.jobs={};self.events=[dict(outside_handoff=True)] if fault=='late_cleanup' else [];self.round=0
        def facts(self,unit):
            self.round+=1
            return dict(unit=unit,host_boot_id=cfg['units'][unit]['host_boot_id'],epoch_us=self.round,
                monotonic_us=self.round,services={'source_only':self.round if fault=='lifecycle' else 'stable'})
        def seed(self,packet):seeds.append(packet)
        def reap(self,job):calls.append('reap')
        def open_connections(self):calls.append('management-open')
        def close_connections(self):
            calls.append('management-close')
            if fault=='management_cleanup':raise RuntimeError('source cleanup refusal')
    class Controller:
        def __init__(self,*args,**kwargs):self.links=[];self.expiry=kwargs['expires_epoch_us']
        def mixed_piece(self,request,response):
            if fault=='transfer':raise RuntimeError('source transfer failed')
            return dict(publisher_verified=True)
        def retire(self):
            calls.append('retire')
            return [dict(retirement_error='source-only')] if fault=='retirement' else []
    monkeypatch.setattr(module,'SSHOperator',Operator);monkeypatch.setattr(module,'Controller',Controller)
    monkeypatch.setattr(sys,'argv',['pilot','--config',str(path),'--runtime-archive',str(archive),
        '--pack',str(pack[0]),'--trust',str(trust),'--output',str(output),'--country','MY',
        '--antennas-confirmed','--current-device-handoff-confirmed',
        *parent_args,
        *(['--reuse-management-connections'] if reuse else [])])
    if fault:
        with pytest.raises(SystemExit) as caught:module.main()
        assert caught.value.code==1
    else:module.main()
    record=json.loads((output/'RESULT.json').read_bytes())
    assert record['complete'] is (fault is None)
    assert calls==(['management-open','retire','management-close'] if reuse else ['retire'])
    if fault=='management_cleanup':assert record['management_connection_cleanup_error']=='RuntimeError'
    assert seeds[0]['unit']=='unit-b' and len(seeds[0]['files'])==10
    assert seeds[1]['unit']=='unit-c' and set(seeds[1]['files'])=={'manifest.json','manifest.sig.json'}
    assert record['measured_airtime_us'] is None and not record['matched_benchmark'] and not record['private_admission']


def test_help_opens_no_management_or_hardware():
    import subprocess
    path=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/run_public_pilot.py'
    result=subprocess.run([sys.executable,str(path),'--help'],capture_output=True,text=True,timeout=5)
    assert result.returncode==0 and '--current-device-handoff-confirmed' in result.stdout


@pytest.mark.parametrize('faster',[True,False])
def test_resume_mode_reports_success_only_after_measured_faster_ip_return(pack,tmp_path,monkeypatch,faster):
    module=load();cfg,_=config();cfg['handoff_end_epoch_us']=module.time.time_ns()//1000+600000000
    raw=b'{"public_parent_claims_required":true}';cfg['manifest_sha256']=hashlib.sha256(raw).hexdigest();archive=tmp_path/'runtime.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        item=tarfile.TarInfo('manifest.json');item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
    cfg['runtime_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
    path=tmp_path/'config.json';_write(path,json.dumps(cfg).encode())
    trust=tmp_path/'trust.json';_write(trust,json.dumps(pack[1]).encode())
    parent_args=enroll_entry_parent(module,tmp_path,cfg,monkeypatch)
    class Operator:
        def __init__(self,*a,**k):self.deadline=module.time.monotonic()+600;self.passwords={};self.jobs={};self.events=[]
        def facts(self,unit):return dict(unit=unit,source_only=True)
        def seed(self,packet):pass
    class Controller:
        def __init__(self,*a,**k):self.links=[]
        def failure_resume(self,exchanges,cutoff):
            assert len(exchanges)==4 and cutoff==32
            return dict(faster_ip_observed=faster,returned_to_ip=faster,return_piece=dict(publisher_verified=True) if faster else None)
        def retire(self):return []
    monkeypatch.setattr(module,'SSHOperator',Operator);monkeypatch.setattr(module,'Controller',Controller)
    output=tmp_path/'out'
    monkeypatch.setattr(sys,'argv',['pilot','--config',str(path),'--runtime-archive',str(archive),'--pack',str(pack[0]),
        '--trust',str(trust),'--output',str(output),'--country','MY','--antennas-confirmed','--current-device-handoff-confirmed',
        *parent_args,
        '--mode','resume','--loss-rx-bytes','32'])
    if faster:module.main()
    else:
        with pytest.raises(SystemExit):module.main()
    result=json.loads((output/'RESULT.json').read_bytes())
    assert result['complete'] is faster and result['resume_sequence']['returned_to_ip'] is faster


def test_verified_runtime_buffer_is_parsed_even_if_path_changes_after_read(pack,tmp_path,monkeypatch):
    original=load;target=tmp_path/'runtime.tar.gz';changed=[]
    replacement=io.BytesIO()
    with tarfile.open(fileobj=replacement,mode='w:gz') as tar:
        raw=b'{"different":true}';entry=tarfile.TarInfo('manifest.json');entry.size=len(raw)
        tar.addfile(entry,io.BytesIO(raw))
    def immutable_load():
        module=original();read=module.runtime_bytes
        def replace_after_read(path):
            verified=read(path);target.write_bytes(replacement.getvalue());changed.append(True);return verified
        monkeypatch.setattr(module,'runtime_bytes',replace_after_read);return module
    monkeypatch.setattr(sys.modules[__name__],'load',immutable_load)
    test_complete_entry_preserves_manifest_only_recipient_and_failure_evidence(pack,tmp_path,monkeypatch,None,False)
    assert changed==[True] and target.read_bytes()==replacement.getvalue()


def test_runtime_read_refuses_oversize_and_symlink_before_any_management(tmp_path):
    module=load();path=tmp_path/'large.tar.gz'
    with path.open('wb') as f:f.truncate(2000001)
    with pytest.raises(ValueError):module.runtime_bytes(path)
    alias=tmp_path/'alias';alias.symlink_to(path)
    with pytest.raises(OSError):module.runtime_bytes(alias)
