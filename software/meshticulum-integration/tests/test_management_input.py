"""Actual private files/nonce writes with modeled Root receipt owner; no SSH or radio."""
from copy import deepcopy
import base64
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import shlex
import stat
import sys
import time
from types import SimpleNamespace

import pytest
from lantern_meshticulum.bench import management_input as inputs,pilot_ssh as ssh
from tests.test_public32_pilot import tmp_path

UNIT='unit-a';ACTION='capture';PARENT='a'*32;OP='b'*64;NONCE='c'*32


@pytest.fixture
def staged(tmp_path,monkeypatch,capsys):
    home=tmp_path/'home';home.mkdir(mode=0o700)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    value=dict(parent_id=PARENT,unit=UNIT,review_sha256=OP,metadata='synthetic private metadata canary')
    raw,csha,rsha=inputs.encode(value);end=time.time_ns()//1000+30000000
    monkeypatch.setattr(sys,'argv',['-c',UNIT,ACTION,PARENT,OP,NONCE,csha,rsha,str(end)])
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(raw)))
    exec(compile(inputs.STAGE_SOURCE,'reviewed-management-stage','exec'),{})
    printed=capsys.readouterr().out;assert value['metadata'] not in printed
    descriptor=json.loads(printed);assert descriptor['bytes']==len(raw)
    return value,descriptor,home


def open_input(descriptor):
    return inputs.PrivateInput(descriptor['path'],owner_uid=descriptor['owner_uid'],nonce=descriptor['nonce'],
        compressed_sha256=descriptor['compressed_sha256'],expected_identity=descriptor['file_identity'],
        expected_directory_identity=descriptor['directory_identity'])


def consume_kwargs(d):
    return dict(unit=d['unit'],action=d['action'],parent_id=d['parent_id'],operation_sha256=d['operation_sha256'],
        nonce=d['nonce'],compressed_sha256=d['compressed_sha256'],raw_sha256=d['raw_sha256'],
        original_end_epoch_us=d['original_end_epoch_us'],expected_stage_uid=d['owner_uid'],
        expected_identity=d['file_identity'],expected_directory_identity=d['directory_identity'])


def root_receipts(monkeypatch,folder,uid):
    folder.mkdir(mode=0o700)
    monkeypatch.setattr(inputs.os,'geteuid',lambda:0)
    monkeypatch.setenv('SUDO_UID',str(uid))
    records=[]
    def claim(path,record):
        assert path=='/run/meshticulum-bench/management-input-used'
        fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:inputs._write_nonce(fd,record)
        finally:os.close(fd)
        records.append(record)
    monkeypatch.setattr(inputs,'claim_nonce',claim)
    return records


def test_exact_binary_stage_decode_and_cleanup(staged):
    value,d,_=staged;held=open_input(d)
    try:
        assert inputs.decode(held.read(),d['compressed_sha256'],d['raw_sha256'])==value
        held.remove();assert not Path(d['path']).exists()
    finally:held.close()
    assert held.closed and held.fd is held.parent_fd is None


@pytest.mark.parametrize('fault',['symlink','hardlink','mode','replacement','directory_replacement','wrong_uid'])
def test_staged_file_or_ancestry_substitution_refuses(staged,fault):
    _,d,home=staged;path=Path(d['path']);raw=path.read_bytes();changed=deepcopy(d)
    if fault=='symlink':
        other=home/'other';other.write_bytes(raw);other.chmod(0o600);path.unlink();path.symlink_to(other)
    elif fault=='hardlink':os.link(path,path.with_name('alias'))
    elif fault=='mode':path.chmod(0o644)
    elif fault=='replacement':path.unlink();path.write_bytes(raw);path.chmod(0o600)
    elif fault=='directory_replacement':
        directory=path.parent;directory.rename(directory.with_name('previous'));directory.mkdir(mode=0o700)
        path.write_bytes(raw);path.chmod(0o600)
    else:changed['owner_uid']+=1
    with pytest.raises((RuntimeError,OSError)):open_input(changed)


def test_cleanup_keeps_foreign_replacement_and_closes_owned_FDs(staged):
    _,d,_=staged;path=Path(d['path']);held=open_input(d)
    path.unlink();path.write_bytes(b'replacement');path.chmod(0o600)
    with pytest.raises(RuntimeError,match='management_input_changed'):held.remove()
    held.close();assert path.read_bytes()==b'replacement' and held.closed


@pytest.mark.parametrize('fault',['compressed_hash','raw_hash','truncated','trailing','decoded_bound','compressed_bound','memory_bound','non_dict'])
def test_decoder_retains_exact_limits_and_pins(fault):
    raw=inputs.canonical(dict(public='fixture'));packed=lzma.compress(raw)
    csha,rsha=inputs.sha(packed),inputs.sha(raw)
    if fault=='compressed_hash':csha='f'*64
    elif fault=='raw_hash':rsha='f'*64
    elif fault=='truncated':packed=packed[:-1];csha=inputs.sha(packed)
    elif fault=='trailing':packed+=b'\0';csha=inputs.sha(packed)
    elif fault=='decoded_bound':raw=b'x'*(inputs.RAW_MAX+1);packed=lzma.compress(raw);csha,rsha=inputs.sha(packed),inputs.sha(raw)
    elif fault=='compressed_bound':packed=b'x'*262145;csha=inputs.sha(packed)
    elif fault=='memory_bound':packed=lzma.compress(raw,preset=9);csha=inputs.sha(packed)
    else:raw=b'[]';packed=lzma.compress(raw);csha,rsha=inputs.sha(packed),inputs.sha(raw)
    with pytest.raises((RuntimeError,lzma.LZMAError)):inputs.decode(packed,csha,rsha)


def test_single_use_nonce_is_retained_before_cleanup_and_cannot_replay(staged,monkeypatch,tmp_path):
    value,d,_=staged;path=Path(d['path']);raw=path.read_bytes()
    records=root_receipts(monkeypatch,tmp_path/'receipts',d['owner_uid'])
    assert inputs.consume(d['path'],**consume_kwargs(d))==value
    assert not path.exists() and len(records)==1
    assert records[0]['native_authority'] is False and records[0]['native_state_writes'] is False
    path.write_bytes(raw);path.chmod(0o600);replay=deepcopy(d)
    replay['file_identity']=list(inputs._stamp(path.lstat()))
    with pytest.raises(FileExistsError):inputs.consume(replay['path'],**consume_kwargs(replay))
    assert not path.exists() and len(records)==1


@pytest.mark.parametrize('fault',['uid','expired','scope','hash'])
def test_root_input_refusal_cannot_claim_nonce(staged,monkeypatch,tmp_path,fault):
    _,d,_=staged;records=root_receipts(monkeypatch,tmp_path/'receipts',d['owner_uid']);kwargs=consume_kwargs(d)
    if fault=='uid':kwargs['expected_stage_uid']+=1
    elif fault=='expired':kwargs['original_end_epoch_us']=time.time_ns()//1000-1
    elif fault=='scope':kwargs['parent_id']='e'*32
    else:kwargs['raw_sha256']='f'*64
    with pytest.raises(RuntimeError):inputs.consume(d['path'],**kwargs)
    assert not records
    assert Path(d['path']).exists() is (fault in ('uid','expired'))


def test_nonce_stays_used_after_late_original_window_expiry(staged,monkeypatch,tmp_path):
    _,d,_=staged;records=root_receipts(monkeypatch,tmp_path/'receipts',d['owner_uid']);original=inputs.claim_nonce
    def expire(path,record):
        original(path,record)
        monkeypatch.setattr(inputs.time,'time_ns',lambda:d['original_end_epoch_us']*1000)
    monkeypatch.setattr(inputs,'claim_nonce',expire)
    with pytest.raises(RuntimeError,match='management_input_window_expired'):inputs.consume(d['path'],**consume_kwargs(d))
    assert len(records)==1 and not Path(d['path']).exists()


def test_staging_replay_does_not_overwrite_first_exact_private_file(staged,monkeypatch):
    _,d,_=staged;path=Path(d['path']);before=path.read_bytes();monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(before)))
    with pytest.raises(FileExistsError):exec(compile(inputs.STAGE_SOURCE,'reviewed-management-stage','exec'),{})
    assert path.read_bytes()==before


def test_short_file_command_keeps_scope_and_hashes_but_no_payload(staged):
    value,d,_=staged;bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    spec=importlib.util.spec_from_file_location('file_rpc_controls',bench/'management_file_rpc.py')
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    command=adapter.command('/opt/meshticulum-bench/runtime-'+'1'*16,'2'*64,'3'*64,d,'/usr/bin/python3',45)
    argv=shlex.join(command);assert len(argv.encode())<8000 and value['metadata'] not in argv
    assert PARENT in command and OP in command and NONCE in command
    assert d['compressed_sha256'] in command and d['raw_sha256'] in command
    assert 'module.dispatch(action,unit,value,runtime_root=root,manifest_sha256=pin)' in adapter.RPC_FILE_ENTRY
    assert 'original_end_epoch_us=int(end)' in adapter.RPC_FILE_ENTRY
    compile(adapter.RPC_FILE_ENTRY,'file-rpc-controls','exec')
    with pytest.raises(RuntimeError):adapter.command('/opt/meshticulum-bench/runtime-'+'1'*16,'2'*64,'3'*64,d,'/usr/bin/python3',121)


def test_existing_child_seed_and_argv_contracts_remain_unchanged():
    assert 'read(100001)' in ssh.STAGE and 'len(data)<=100000' in ssh.STAGE
    assert 'read(700001)' in ssh.PUBLIC_SEED_STAGE and 'len(data)<=700000' in ssh.PUBLIC_SEED_STAGE
    bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    tree=__import__('ast').parse((bench/'run_public_sf5_c_bulk_8k.py').read_bytes())
    entry=__import__('ast').literal_eval(next(n.value for n in tree.body if isinstance(n,__import__('ast').Assign)
        and any(isinstance(t,__import__('ast').Name) and t.id=='RPC_ENTRY' for t in n.targets)))
    assert "need(len(blob)<=112000,'RPC_encoded_bound')" in entry
    assert "need(len(compressed)<=84000,'RPC_compressed_bound')" in entry


def test_optional_stager_uses_non_pty_stdin_and_original_deadline(staged,monkeypatch):
    value,d,_=staged;operator=object.__new__(ssh.SSHOperator)
    operator.config=dict(handoff_end_epoch_us=d['original_end_epoch_us']);operator.deadline=100.0
    operator.clock=lambda:90.0;operator.epoch=lambda:d['original_end_epoch_us']-3000000;operator.events=[];calls=[]
    def argv(unit,command,**kw):
        calls.append((unit,command,kw));assert kw==dict(root=False);return ['owned-inert-SSH',*command]
    operator._argv=argv
    def run(command,**kwargs):
        assert kwargs['input']==inputs.encode(value)[0] and kwargs['timeout']==3
        assert 'password' not in kwargs and 'sudo' not in command
        return SimpleNamespace(returncode=0,stdout=inputs.canonical(d),stderr=b'')
    monkeypatch.setattr(ssh.subprocess,'run',run)
    assert operator.stage_management_input(UNIT,ACTION,value,parent_id=PARENT,operation_sha256=OP,nonce=NONCE,
        original_end_epoch_us=d['original_end_epoch_us'])==d
    assert calls[0][1][2]=='2.0s' and operator.deadline==100.0
    assert operator.events[0]['stage_source_sha256']==inputs.STAGE_SHA256


def test_explicit_cleanup_after_expiry_never_dispatches_or_claims_nonce(staged,monkeypatch,tmp_path):
    _,d,_=staged;records=root_receipts(monkeypatch,tmp_path/'receipts',d['owner_uid'])
    monkeypatch.setattr(inputs.time,'time_ns',lambda:(d['original_end_epoch_us']+1)*1000)
    inputs.cleanup(d['path'],nonce=d['nonce'],compressed_sha256=d['compressed_sha256'],expected_stage_uid=d['owner_uid'],
        expected_identity=d['file_identity'],expected_directory_identity=d['directory_identity'])
    assert not Path(d['path']).exists() and not records


def test_already_consumed_input_cleanup_requires_exact_original_directory(staged,monkeypatch,tmp_path):
    _,d,_=staged;records=root_receipts(monkeypatch,tmp_path/'receipts',d['owner_uid'])
    held=open_input(d);held.remove();held.close()
    kwargs=dict(nonce=d['nonce'],compressed_sha256=d['compressed_sha256'],expected_stage_uid=d['owner_uid'],
        expected_identity=d['file_identity'],expected_directory_identity=d['directory_identity'])
    assert inputs.cleanup(d['path'],**kwargs) is True and not records
    parent=Path(d['path']).parent;parent.rename(parent.with_name('old'));parent.mkdir(mode=0o700)
    with pytest.raises(RuntimeError,match='management_input_staged_directory_changed'):inputs.cleanup(d['path'],**kwargs)


def test_file_callback_budget_uses_remaining_original_time_and_cleanup_is_separate(staged,monkeypatch):
    _,d,_=staged;bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    spec=importlib.util.spec_from_file_location('file_deadline_controls',bench/'management_file_rpc.py')
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    monkeypatch.setattr(adapter.time,'time_ns',lambda:(d['original_end_epoch_us']-3100000)*1000)
    command=adapter.command('/opt/meshticulum-bench/runtime-'+'1'*16,'2'*64,'3'*64,d,'/usr/bin/python3',120)
    assert command[2]=='3s' and str(d['original_end_epoch_us']) in command
    monkeypatch.setattr(adapter.time,'time_ns',lambda:d['original_end_epoch_us']*1000)
    with pytest.raises(RuntimeError):adapter.command('/opt/meshticulum-bench/runtime-'+'1'*16,'2'*64,'3'*64,d,'/usr/bin/python3',45)
    command=adapter.command('/opt/meshticulum-bench/runtime-'+'1'*16,'2'*64,'3'*64,d,'/usr/bin/python3',5,cleanup=True)
    assert command[2]=='5s' and 'cleanup-management-input' in command
    with pytest.raises(RuntimeError):adapter.command('/opt/meshticulum-bench/runtime-'+'1'*16,'2'*64,'3'*64,d,'/usr/bin/python3',6,cleanup=True)
