"""Finite compressed metadata decoder controls; no runtime or native dispatch."""
import ast
import base64
import hashlib
import lzma
import json
import random
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[3]
source=ROOT/'firmware/meshticulum-bench/run_public_sf5_c_bulk_8k.py'
entry=next(ast.literal_eval(n.value) for n in ast.parse(source.read_bytes()).body
           if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RPC_ENTRY' for t in n.targets))
# Execute the exact production input decoder without runtime or dispatch I/O.
decoder=entry[entry.index('need(len(blob)<=109336'):entry.index('sys.path.insert(0,str(root))')]

def decode(blob,raw,pin=None):
    def need(ok,reason):
        if not ok:raise RuntimeError(reason)
    env=dict(base64=base64,lzma=lzma,hashlib=hashlib,need=need,blob=base64.b64encode(blob).decode(),input_sha=pin or hashlib.sha256(raw).hexdigest())
    exec(compile(decoder,str(source),'exec'),env)
    return env['raw']


def test_82000_bound_retains_raw_and_decoder_limits():
    raw=b'{"metadata":true}'
    assert decode(lzma.compress(raw),raw)==raw
    with pytest.raises(RuntimeError,match='RPC_compressed_bound'):
        decode(bytes(82001),raw)
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
    # Public synthetic bytes exercise the range that actual H31 now needs.
    value=base64.b64encode(random.Random(7630).randbytes(75000)).decode()
    raw=json.dumps({'metadata':value},sort_keys=True,separators=(',',':')).encode()
    blob=lzma.compress(raw,format=lzma.FORMAT_XZ,preset=6|lzma.PRESET_EXTREME)
    assert 76000<len(blob)<=82000
    assert decode(blob,raw)==raw
    with pytest.raises(RuntimeError,match='RPC_input_bound_or_pin'):
        decode(blob,raw,'f'*64)


def test_batch_sender_keeps_final_argv_and_raw_caps():
    text=(ROOT/'firmware/meshticulum-bench/run_public_sf5_batch.py').read_text()
    assert "len(raw)<=2000000 and len(blob)<=82000,'batch_RPC_bound'" in text
    assert "need(len(argv[-1].encode())<125000,'batch_native_argv_bound')" in text
    assert "memlimit=33554432" in entry


def test_encoded_limit_matches_82000_compressed_bound():
    assert 4*((82000+2)//3)==109336
    with pytest.raises(RuntimeError,match='RPC_encoded_bound'):
        decode(bytes(82004),b'{}')
