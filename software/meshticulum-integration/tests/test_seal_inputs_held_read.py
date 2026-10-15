"""Local held-file call counts and drift controls; no native/H43 attestation."""
from collections import Counter
import os
import sys

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from tests.test_rpc_reader_ancestry import tmp_path, sample


def counts(callback):
    observed = Counter()
    codes = {rpc.Reader._check_nodes.__code__:'ancestry_sweeps',
             rpc.Reader.directory.__wrapped__.__code__:'directory_calls',
             rpc.Reader.read.__wrapped__.__code__:'read_calls',
             rpc.Reader.read_held.__wrapped__.__code__:'read_held_calls',
             rpc.Reader.seal_candidate_bytes.__wrapped__.__code__:'seal_candidate_calls'}
    previous = sys.getprofile()
    def profile(frame,event,arg):
        if event=='call' and frame.f_code in codes:observed[codes[frame.f_code]] += 1
        if event=='c_call' and arg is os.pread:observed['fresh_preads'] += 1
    try:
        sys.setprofile(profile)
        value = callback()
    finally:sys.setprofile(previous)
    return value,dict(observed)


def test_exact_held_read_removes_one_setup_sweep_and_keeps_fresh_bytes(tmp_path):
    reader,_,_,paths = sample(tmp_path)
    try:
        old,old_counts = counts(lambda:reader.read(paths[0],1024))
        new,new_counts = counts(lambda:reader.read_held(paths[0],1024))
        assert old == new == b'{"public":"fixture"}'
        assert old_counts == dict(ancestry_sweeps=3,directory_calls=1,read_calls=1,fresh_preads=1)
        assert new_counts == dict(ancestry_sweeps=2,read_held_calls=1,fresh_preads=1)
    finally:reader.close()


@pytest.fixture
def seal_fixture(tmp_path,monkeypatch):
    """Only state location is modeled; the complete active source roster remains."""
    state = tmp_path/'state';state.mkdir(mode=0o700)
    for name in ('parents','runs'):(state/name).mkdir(mode=0o700)
    for parent in rpc.HISTORY:(state/'parents'/parent).mkdir(mode=0o700)
    for parent in rpc.GUARDED_PAID:
        (state/'parents'/parent/'prepared-identities').mkdir(mode=0o700)
    if rpc.incomplete_lora_history.PARENT in rpc.HISTORY:
        (state/'parents'/rpc.incomplete_lora_history.PARENT/'prepared-identities').mkdir(mode=0o700)
    if rpc.endpoint_lora_history.PARENT in rpc.HISTORY:
        (state/'parents'/rpc.endpoint_lora_history.PARENT/'prepared-identities').mkdir(mode=0o700)
    if rpc.unstarted_history.UNMATERIALIZED_PARENT in rpc.HISTORY:
        (state/'parents'/rpc.unstarted_history.UNMATERIALIZED_PARENT/'prepared-identities').mkdir(mode=0o700)
    for relative in rpc.capture_paths(state,'unit-b')['directories']:
        (state/relative).mkdir(mode=0o700,parents=True,exist_ok=True)
    path = state/'parents'/rpc.HISTORY[0]/'policy.json'
    raw = b'{"local_source_fixture":true}'
    path.write_bytes(raw);path.chmod(0o600)
    monkeypatch.setattr(rpc,'STATE',state)
    reader=rpc.Reader()
    try:
        assert reader.read(path,1024)==raw
        yield state,reader,path,raw
    finally:
        fds=tuple(reader._tokens);reader.close()
        for fd in fds:
            with pytest.raises(OSError):os.fstat(fd)


def test_seal_inputs_uses_held_reader_and_unchanged_final_full_check(seal_fixture):
    state,reader,path,raw = seal_fixture
    known = {str(path.relative_to(state)):rpc.sha(raw)}
    (files,dirs),observed = counts(lambda:rpc.seal_inputs(reader,'unit-b',known,{},{}))
    assert files == known
    assert observed.get('seal_candidate_calls',0)==2 and observed.get('read_held_calls',0)==0
    assert observed.get('read_calls',0)==0
    # Full fresh Reader.check bookends reread every held byte.
    assert observed['fresh_preads']==2
    assert set(rpc.HISTORY) <= set(dirs['parents']) and len(rpc.HISTORY)==49


@pytest.mark.parametrize('fault',['bytes','replacement','hardlink','mode','parent','offset'])
def test_seal_inputs_rejects_drift_in_original_held_input(seal_fixture,fault):
    state,reader,path,raw = seal_fixture
    known = {str(path.relative_to(state)):rpc.sha(raw)}
    if fault=='bytes':path.write_bytes(b'x'*len(raw))
    elif fault=='replacement':
        replacement=path.parent/'replacement';replacement.write_bytes(raw);replacement.chmod(0o600);replacement.replace(path)
    elif fault=='hardlink':os.link(path,state/'extra-link')
    elif fault=='mode':path.chmod(0o644)
    elif fault=='parent':
        path.parent.rename(state/'held-parent');path.parent.mkdir(mode=0o700)
    else:os.lseek(reader._file_index[path][1],0,os.SEEK_SET)
    try:
        with pytest.raises((RuntimeError,OSError)):
            rpc.seal_inputs(reader,'unit-b',known,{}, {})
    finally:
        if fault=='offset':
            fd=reader._file_index[path][1]
            os.lseek(fd,reader._tokens[fd],os.SEEK_SET)


def test_seal_inputs_rejects_during_pread_mutation(seal_fixture,monkeypatch):
    state,reader,path,raw = seal_fixture
    known={str(path.relative_to(state)):rpc.sha(raw)};pread=os.pread;armed=True
    def mutate(*args):
        nonlocal armed
        value=pread(*args)
        if armed:
            armed=False;path.write_bytes(b'x'*len(raw))
        return value
    monkeypatch.setattr(os,'pread',mutate)
    with pytest.raises(RuntimeError):rpc.seal_inputs(reader,'unit-b',known,{}, {})
