"""Actual full H49 payload and finite upper bound; no native permission."""
import hashlib
import importlib.util
import lzma
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest
from lantern_meshticulum.bench import capture_storage, management_input, pilot_resume_rpc


@pytest.fixture(scope='module')
def actual_c():
    base=Path(os.environ['MESHTICULUM_H49_FIXTURE'])
    sys.path.insert(0,str(base))
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module);return module
    old=load('H49_bound_reader',base/'original_assemble_history44.py')
    prepared=load('H49_actual_bound_inputs',base/'prepare_history49_inputs_actual.py')
    values=prepared.inputs(old.read,old.load,old.need,old.sha,old.canonical)
    history=values['histories']['unit-c']
    payload=dict(history,previous=history['previous']+[values['latest']['unit-c']])
    raw=management_input.canonical(payload)
    assert len(raw)==2521284 and old.canonical(payload['previous'][:-1])==old.canonical(history['previous'])
    assert old.canonical(payload['legacy_inventory'])==old.canonical(history['legacy_inventory'])
    return payload,raw


def sized(size):
    value={'padding':''};value['padding']='x'*(size-len(management_input.canonical(value)))
    assert len(management_input.canonical(value))==size
    return value


def test_actual_full_H49_roundtrips_without_changing_compression_or_memory_bounds(actual_c):
    value,raw=actual_c
    assert management_input.RAW_MAX==capture_storage.RAW_MAX==pilot_resume_rpc.RAW_MAX==2750000
    assert management_input.COMPRESSED_MAX==262144 and management_input.DECODER_MEMORY_MAX==33554432
    packed,packed_pin,raw_pin=management_input.encode(value)
    assert len(packed)<=262144 and raw_pin==hashlib.sha256(raw).hexdigest()
    assert management_input.canonical(management_input.decode(packed,packed_pin,raw_pin))==raw
    assert capture_storage.encode(raw)==raw
    assert capture_storage.decode(raw,expected_sha256=raw_pin)==raw


def test_exact_old_raw_bound_refuses_the_same_actual_full_H49(actual_c):
    root=Path(__file__).resolve().parents[3]
    relative='software/meshticulum-integration/src/lantern_meshticulum/bench/management_input.py'
    raw=subprocess.check_output(['git','show','b696a21c2e6367b53d8d12349b2f94aaaae364de:'+relative],cwd=root)
    original=types.ModuleType('original_H48_management_input')
    exec(compile(raw,relative,'exec'),original.__dict__)
    assert original.RAW_MAX==2500000
    with pytest.raises(RuntimeError,match='management_input_raw_bound'):original.encode(actual_c[0])


def test_exact_finite_upper_bound_roundtrips():
    value=sized(2750000);packed,pin,raw_pin=management_input.encode(value)
    assert management_input.decode(packed,pin,raw_pin)==value


def test_larger_raw_is_refused_before_compression(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('oversize raw reached compressor')
    monkeypatch.setattr(management_input.lzma,'compress',forbidden)
    with pytest.raises(RuntimeError,match='management_input_raw_bound'):
        management_input.encode(sized(2750001))


def test_compressed_oversize_raw_still_refuses():
    raw=management_input.canonical(sized(2750001));packed=lzma.compress(raw)
    with pytest.raises(RuntimeError,match='management_input_raw_pin'):
        management_input.decode(packed,hashlib.sha256(packed).hexdigest(),hashlib.sha256(raw).hexdigest())
    with pytest.raises(RuntimeError,match='capture_storage_size_bound'):
        capture_storage.decode(raw)
