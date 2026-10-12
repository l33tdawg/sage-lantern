"""Finite compressed metadata decoder controls; no runtime or native dispatch."""
import ast
import base64
import hashlib
import lzma
import json
import random
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[3]
source=ROOT/'firmware/meshticulum-bench/run_public_sf5_c_bulk_8k.py'
entry=next(ast.literal_eval(n.value) for n in ast.parse(source.read_bytes()).body
           if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RPC_ENTRY' for t in n.targets))
# Execute the exact production input decoder without runtime or dispatch I/O.
decoder=entry[entry.index('need(len(blob)<=112000'):entry.index('sys.path.insert(0,str(root))')]

def decode(blob,raw,pin=None):
    def need(ok,reason):
        if not ok:raise RuntimeError(reason)
    env=dict(base64=base64,lzma=lzma,hashlib=hashlib,need=need,blob=base64.b64encode(blob).decode(),input_sha=pin or hashlib.sha256(raw).hexdigest())
    exec(compile(decoder,str(source),'exec'),env)
    return env['raw']


def test_84000_bound_retains_raw_and_decoder_limits():
    raw=b'{"metadata":true}'
    assert decode(lzma.compress(raw),raw)==raw
    with pytest.raises(RuntimeError,match='RPC_encoded_bound'):
        decode(bytes(84001),raw)
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):
        decode(lzma.compress(b'x'*2000001),b'x'*2000001)


def test_sha_and_xz_end_remain_mandatory():
    raw=b'{"metadata":true}';blob=lzma.compress(raw)
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):
        decode(blob,raw,'f'*64)
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):
        decode(blob+bytes(1),raw)
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):
        decode(blob[:-1],raw)


def test_larger_complete_metadata_fits_new_bound_without_weakening_raw_pin():
    # Public synthetic bytes exercise the range that actual H33 now needs.
    value=base64.b64encode(random.Random(7630).randbytes(81000)).decode()
    raw=json.dumps({'metadata':value},sort_keys=True,separators=(',',':')).encode()
    blob=lzma.compress(raw,format=lzma.FORMAT_XZ,preset=6|lzma.PRESET_EXTREME)
    assert 82000<len(blob)<=84000
    assert decode(blob,raw)==raw
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):
        decode(blob,raw,'f'*64)


def test_batch_sender_keeps_final_argv_and_raw_caps():
    for name in ('run_public_sf5_batch.py','run_public_gfsk125_batch.py'):
        text=(ROOT/'firmware/meshticulum-bench'/name).read_text()
        assert "len(raw)<=2000000 and len(blob)<=84000,'batch_RPC_bound'" in text
        assert "need(len(argv[-1].encode())<125000,'batch_native_argv_bound')" in text
    assert "memlimit=33554432" in entry


def test_encoded_limit_matches_84000_compressed_bound():
    assert 4*((84000+2)//3)==112000
    with pytest.raises(RuntimeError,match='RPC_encoded_bound'):
        decode(bytes(84003),b'{}')


@pytest.fixture
def native_dispatch():
    """Run the production nested launcher with inert SSH/process adapters."""
    from lantern_meshticulum.bench.pilot_ssh import SSHOperator,one_record
    path=ROOT/'firmware/meshticulum-bench/run_public_gfsk125_batch.py'
    tree=ast.parse(path.read_bytes())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    native=next(n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='native')
    cfg={'units':{'unit-c':{'python':'/usr/bin/python3','sudo':True}}}
    calls=[]
    operator=SimpleNamespace(config=cfg,passwords={},clock=lambda:0,deadline=999,
        _ssh=lambda *a,**k:['inert-ssh'])
    operator._argv=lambda *a,**k:SSHOperator._argv(operator,*a,**k)
    def need(ok,reason):
        if not ok:raise RuntimeError(reason)
    class InertJob:
        def __init__(self,argv,**kwargs):
            calls.append(argv);self.process=SimpleNamespace(returncode=0,poll=lambda:0)
            self.output=json.dumps(dict(schema='meshticulum.private-resume-native-rpc.v1',
                unit='unit-c',action='capture',radio_opened=False,private_production=False)).encode()
        def wait(self):return 0
        def close(self):pass
    env=dict(canonical=lambda v:json.dumps(v,sort_keys=True,separators=(',',':')).encode(),
        sha=lambda raw:hashlib.sha256(raw).hexdigest(),lzma=lzma,need=need,
        native_callback_seconds=lambda *a:120,end=900000000,
        time=SimpleNamespace(time_ns=lambda:0),cfg=cfg,op={'runtime_sha256':'a'*64,
            'manifest_sha256':'b'*64,'native_adapter_sha256':'c'*64},RPC_ENTRY=entry,
        operator=operator,Job=InertJob,owned=[],result={'rpc_clients':[]},one_record=one_record)
    exec(compile(ast.Module(body=[native],type_ignores=[]),str(path),'exec'),env)
    return env['native'],operator,calls


@pytest.mark.parametrize('size,allowed',[(124999,True),(125000,False)])
def test_actual_quoted_command_boundary_refuses_before_process_start(native_dispatch,size,allowed):
    native,operator,calls=native_dispatch
    native('unit-c','capture',{})
    original=len(calls[-1][-1].encode());calls.clear()
    # A quote expands under shlex.join; adjust only the inert executable path.
    row=operator.config['units']['unit-c']
    row['python']="/usr/bin/python3'"
    native('unit-c','capture',{})
    quoted=len(calls[-1][-1].encode());calls.clear()
    assert quoted>original+1
    row['python']+='p'*(size-quoted)
    if allowed:
        native('unit-c','capture',{})
        assert len(calls[-1][-1].encode())==size
    else:
        with pytest.raises(RuntimeError,match='batch_native_argv_bound'):
            native('unit-c','capture',{})
        assert calls==[]
