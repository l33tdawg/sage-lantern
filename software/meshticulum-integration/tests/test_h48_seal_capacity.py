"""Finite H48 metadata capacity; exact saved inputs and real local held FDs."""
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import types

import pytest
from lantern_meshticulum.bench import history_identity_seal as seals,pilot_resume_rpc as rpc
from lantern_meshticulum.bench import lora_endpoint_incomplete_history as history
from lantern_meshticulum.bench.history_metadata_fence import FenceError,_stamp

sha=lambda raw:hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope='module')
def actual_scopes():
    base=Path(os.environ['MESHTICULUM_H48_FIXTURE']);out={}
    for unit in ('unit-a','unit-b','unit-c'):
        raw=(base/'actual-intake'/('RAW_'+unit+'.json')).read_bytes()
        assert sha(raw)==history.RAW_UNIT_SHA256[unit]
        observed=json.loads(raw)['observation']
        relative='parents/'+history.PARENT+'/history-identity-seal.json'
        raw=(base/'resume-history-preparation-48'/(unit+'-public')/relative).read_bytes()
        assert sha(raw)==history.PUBLIC_FILES[unit][relative]['sha256']
        old=json.loads(raw);files={p:v['sha256']for p,v in old['files'].items()}
        dirs={p:v['entries']for p,v in old['directories'].items()}
        selected={p:v['sha256']for p,v in observed['files'].items()if v['present']}
        assert not set(files)&set(selected)
        files.update(selected)
        for record in observed['directories'].values():
            relative=Path(record['path']).relative_to('/var/lib/meshticulum-bench').as_posix()
            assert relative not in dirs
            dirs[relative]=record['names']
        dirs['parents']=observed['namespace']['parents']['names']
        dirs['runs']=observed['namespace']['runs']['names']
        out[unit]=(files,dirs)
    return out


def test_exact_saved_H48_union_identifies_only_the_file_ceiling(actual_scopes):
    assert seals.MAX_FILES==rpc.MAX_FILES==2304
    assert (seals.MAX_DIRS,seals.MAX_ENTRIES,seals.MAX_SEAL,seals.MAX_TOTAL_BYTES)==(512,4096,2*1024*1024,256*1024*1024)
    assert {u:(len(f),len(d),sum(map(len,d.values())))for u,(f,d)in actual_scopes.items()}=={
        'unit-a':(1888,239,2607),'unit-b':(794,135,1082),'unit-c':(2059,261,2854)}
    for files,dirs in actual_scopes.values():seals._bounded(files,dirs)


def test_exact_original_source_refuses_actual_C_union_before_any_filesystem_access(actual_scopes):
    repo=Path(__file__).resolve().parents[3]
    raw=subprocess.check_output(['git','show','2c647950e86877f2df5412477ce291578b5b612a:software/meshticulum-integration/src/lantern_meshticulum/bench/history_identity_seal.py'],cwd=repo,timeout=5)
    assert sha(raw)=='9ba33b9fed12064c0df42cb86d67d916cef1b9a8f58e1f65bb464ac48fdf4667'
    original=types.ModuleType('lantern_meshticulum.bench._original_H48_capacity')
    original.__package__='lantern_meshticulum.bench'
    exec(compile(raw,'original-history-identity-seal','exec'),original.__dict__)
    assert original.MAX_FILES==2048
    with pytest.raises(FenceError,match='too_many_paths'):original._bounded(*actual_scopes['unit-c'])
    seals._bounded(*actual_scopes['unit-c'])


def test_actual_C_descriptor_reservation_spends_only_process_soft_capacity(actual_scopes,monkeypatch):
    files,dirs=actual_scopes['unit-c'];limits=[256,16384];calls=[]
    monkeypatch.setattr(rpc.os,'listdir',lambda path:list(range(100)))
    monkeypatch.setattr(rpc.resource,'getrlimit',lambda _:tuple(limits))
    def reserve(_,value):calls.append(value);limits[:]=value
    monkeypatch.setattr(rpc.resource,'setrlimit',reserve)
    value=rpc.reserve_descriptor_capacity(files,dirs,[{}]*63)
    expected=100+2059+261+63*(3+len(rpc.OWNERS))+128+16+21
    assert calls==[(expected,16384)]and value['required_soft_limit']==expected
    assert value['hard_limit']==16384 and value['changed']is True


@pytest.mark.parametrize('dimension',('files','dirs','entries','path'))
def test_every_finite_path_bound_still_refuses_before_open(dimension,monkeypatch):
    files={};dirs={}
    if dimension=='files':files={str(i):'f'*64 for i in range(2305)}
    elif dimension=='dirs':dirs={str(i):[]for i in range(513)}
    elif dimension=='entries':dirs={'':list(map(str,range(4097)))}
    else:files={'x'*513:'f'*64}
    monkeypatch.setattr(seals,'_Ancestry',lambda *a,**k:pytest.fail('oversized scope reached filesystem'))
    with pytest.raises(FenceError):seals.capture(Path('/unused'),owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)


@pytest.fixture
def protected_root():
    # _Ancestry correctly refuses world-writable OS temp ancestors.
    root=Path(tempfile.mkdtemp(prefix='.h48-capacity-',dir=Path(__file__).parent))
    root.chmod(0o700);before=resource.getrlimit(resource.RLIMIT_NOFILE)
    target=max(before[0],6000)if before[0]!=resource.RLIM_INFINITY else before[0]
    assert before[1]==resource.RLIM_INFINITY or target<=before[1]
    resource.setrlimit(resource.RLIMIT_NOFILE,(target,before[1]))
    try:yield root
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE,before);shutil.rmtree(root)


def test_real_2059_file_capture_reopens_through_eight_endpoint_lifecycle_and_detects_drift(protected_root):
    root=protected_root
    for name in ('parents','runs'):(root/name).mkdir(mode=0o700)
    files={};dirs={'':['parents','runs'],'parents':[],'runs':[]}
    names=('grant.json','active','provision.json','job.json','result.json','started')
    for index in range(2059):
        run='%032x'%(index//len(names)+1);folder='runs/'+run
        if folder not in dirs:
            (root/folder).mkdir(mode=0o700);dirs[folder]=[];dirs['runs'].append(run)
        name=names[index%len(names)];raw=('public-metadata-'+str(index)).encode()
        path=root/folder/name;path.write_bytes(raw);path.chmod(0o600)
        dirs[folder].append(name);files[folder+'/'+name]=sha(raw)
    raw,pin=seals.capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
    data=json.loads(raw)
    assert len(data['files'])==2059 and len(raw)<seals.MAX_SEAL
    with seals.SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin)as held:held.check()
    own={};calls=[]
    for relative in ['parents/'+('e'*32)]+['runs/'+('%032x'%(10000+i))for i in range(8)]:
        path=root/relative;path.mkdir(mode=0o700);own[relative]=list(_stamp(path.stat())[:4])
    def actual_relation(paths):
        calls.append(paths)
        return paths==tuple(sorted(own))and all(list(_stamp((root/p).lstat())[:4])==own[p]for p in paths)
    with seals.SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin,
            own_directories=own,validate_actual_relation=actual_relation)as held:
        for relative in own:
            path=root/relative/'model-current-record.json';path.write_bytes(b'current-owned-model');path.chmod(0o600)
        held.check();assert len(held._data['files'])==2059 and len(held._own_fds)==9
        path=root/next(iter(files));path.write_bytes(b'changed historical bytes')
        with pytest.raises(FenceError):held.check()
    assert calls and all(v==tuple(sorted(own))for v in calls)
