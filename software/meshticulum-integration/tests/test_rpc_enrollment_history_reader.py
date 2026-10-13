"""Genuine held files and SQLite enrollment; modeled old typed collection/facts.

These local controls exercise enrollment boundaries, not native qualification.
"""
import fcntl
import os
import sqlite3
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench import pilot_native_caps as native
from tests.test_pilot_resume_rpc_integration import local_scope, PARENT


def history_model(monkeypatch, state, count=8):
    directory = state/'parents'/rpc.HISTORY[0]
    paths = [directory/('observed-'+str(i)+'.json') for i in range(count)]
    for path in paths:
        rpc.write_new(directory,path.name,b'{"local_public_metadata":"'+b'x'*1000+b'"}')
    active = directory/'active'; rpc.write_new(directory,'active',b'retired\n')
    probe = SimpleNamespace(reader=None,collecting=False,history_reads=0,
        collection_checks=0,authority_checks=0,gates=0,returned=False,
        before_return=lambda:None,inside_gate=lambda:None,paths=paths,active=active)
    original_check, original_pread = rpc.Reader.check, os.pread
    def checked(reader):
        if reader is probe.reader:
            if probe.collecting:probe.collection_checks += 1
            else:probe.authority_checks += 1
        return original_check(reader)
    def pread(fd,*args):
        if probe.reader is not None and fd in {row[1] for row in probe.reader.held}:
            probe.history_reads += 1
        return original_pread(fd,*args)
    def modeled(reader,unit,value,guard,**scope):
        assert scope == {'own_parent':PARENT} and unit == 'unit-a'
        probe.reader = reader; probe.collecting = True
        reader.read(active,128,lock=True)
        for path in paths:
            guard(); probe.gates += 1
            reader.read(path,4096)
            probe.inside_gate()
            guard(); probe.gates += 1
        reader.check()  # The real full_history has this mandatory final sweep.
        probe.collecting = False
        probe.before_return(); probe.returned = True
        return {},{},[],[]
    monkeypatch.setattr(rpc.Reader,'check',checked)
    monkeypatch.setattr(os,'pread',pread)
    monkeypatch.setattr(rpc,'full_history',modeled)
    return probe


def enrollment_rows(state):
    path = state/'parents'/PARENT/'native.sqlite'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return db.execute('SELECT COUNT(*) FROM enrollment').fetchone()[0] if 'enrollment' in tables else 0


@pytest.mark.parametrize('count',[1,16,42])
def test_loading_has_one_history_sweep_and_authority_keeps_fresh_checks(local_scope,monkeypatch,count,record_property):
    state,runtime,p,_,_ = local_scope; probe = history_model(monkeypatch,state,count)
    closes = []; original_close = native.NativeCaps.close
    def close(caps):
        assert not probe.reader._closed
        held_fd = os.open(probe.active,os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(held_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(held_fd)
        closes.append(True); return original_close(caps)
    monkeypatch.setattr(native.NativeCaps,'close',close)
    result = rpc.enrolled_action('enroll','unit-a',p,runtime)
    assert result['snapshot']['claims'] == result['snapshot']['prepared'] == result['snapshot']['starts'] == 0
    assert enrollment_rows(state) == 1 and not result['snapshot']['uncertain']
    assert probe.gates == 2*count and probe.collection_checks == 1
    # Twelve facts-bound sweeps after prior collection, six approval-record
    # sweeps, one prior-return sweep and one after-close sweep.
    assert probe.authority_checks == 20 and closes == [True]
    assert probe.history_reads == 21*(count+1)
    assert probe.reader._closed
    with probe.active.open('rb') as stream:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    record_property('modeled_history_files',count+1)
    record_property('per_parent_history_sweeps',0)
    record_property('complete_history_sweeps',1+probe.authority_checks)
    record_property('history_full_byte_rereads',probe.history_reads)


@pytest.mark.parametrize('fault',['bytes','replacement','mode','ancestry','descriptor'])
def test_changed_history_after_collection_cannot_commit(local_scope,monkeypatch,fault):
    state,runtime,p,_,_ = local_scope; probe = history_model(monkeypatch,state)
    changed_description = []
    def mutate():
        path = probe.paths[0]
        if fault == 'bytes':path.write_bytes(b'changed')
        elif fault == 'replacement':
            raw=path.read_bytes(); path.unlink(); rpc.write_new(path.parent,path.name,raw)
        elif fault == 'mode':path.chmod(0o644)
        elif fault == 'ancestry':path.parent.rename(path.parent.with_name('e'*32))
        else:
            fd=probe.reader.held[0][1]; changed_description.append(fd)
            os.lseek(fd,0,os.SEEK_SET)
    probe.before_return = mutate
    try:
        with pytest.raises(native.NativeCapsError):rpc.enrolled_action('enroll','unit-a',p,runtime)
    finally:
        # Reader deliberately will not close an open description whose token
        # changed. The local fault injector still owns that descriptor.
        for fd in changed_description:os.close(fd)
    assert probe.returned and enrollment_rows(state) == 0
    assert probe.reader._closed


@pytest.mark.parametrize('when',['before_precommit_observe','inside_precommit_approval',
    'after_enrollment_commit','after_constructor','inside_current_approval','after_snapshot','after_close'])
def test_history_changes_at_each_authority_boundary_refuse(local_scope,monkeypatch,when):
    state,runtime,p,_,_ = local_scope; probe = history_model(monkeypatch,state)
    fired = []
    def mutate():
        if not fired:probe.paths[0].write_bytes(b'changed'); fired.append(when)
    if when == 'before_precommit_observe':
        original = native.NativeCaps._observe
        def observe(caps,phase,*args):
            mutate();return original(caps,phase,*args)
        monkeypatch.setattr(native.NativeCaps,'_observe',observe)
    elif when in ('inside_precommit_approval','inside_current_approval'):
        original = rpc.parse
        def parse(raw):
            value = original(raw)
            if isinstance(value,dict) and value.get('phase') == ('current' if when=='inside_current_approval' else 'enroll'):
                mutate()
            return value
        monkeypatch.setattr(rpc,'parse',parse)
    elif when == 'after_enrollment_commit':
        original = native.NativeCaps._commit
        def commit(caps):
            result=original(caps);mutate();return result
        monkeypatch.setattr(native.NativeCaps,'_commit',commit)
    elif when == 'after_constructor':
        original = native.NativeCaps.enroll.__func__
        def enroll(cls,*args,**kwargs):
            caps=original(cls,*args,**kwargs);mutate();return caps
        monkeypatch.setattr(native.NativeCaps,'enroll',classmethod(enroll))
    elif when == 'after_snapshot':
        original = native.NativeCaps.snapshot
        def snapshot(caps):
            result=original(caps);mutate();return result
        monkeypatch.setattr(native.NativeCaps,'snapshot',snapshot)
    else:
        original = native.NativeCaps.close
        def close(caps):
            result=original(caps);mutate();return result
        monkeypatch.setattr(native.NativeCaps,'close',close)
    with pytest.raises((native.NativeCapsError,RuntimeError)):
        rpc.enrolled_action('enroll','unit-a',p,runtime)
    assert fired == [when] and probe.reader._closed
    committed = when not in ('before_precommit_observe','inside_precommit_approval')
    assert enrollment_rows(state) == int(committed)
    if when == 'after_enrollment_commit':
        # The enrollment/debt already committed must stay present and pending.
        assert (state/'parents'/PARENT/'native.sqlite.pending').read_text() == p['owner']['policy_digest']


@pytest.mark.parametrize('fault',['capture','clock','boot','services'])
def test_each_loading_gate_keeps_live_context_checks(local_scope,monkeypatch,fault):
    state,runtime,p,_,_ = local_scope; probe = history_model(monkeypatch,state)
    def mutate():
        if fault == 'capture':(state/'parents'/PARENT/'capture.json').write_bytes(b'changed')
        elif fault == 'clock':
            first=rpc.clock();monkeypatch.setattr(rpc,'clock',lambda:(p['owner']['phase_end_epoch_us'],first[1]))
        elif fault == 'boot':monkeypatch.setattr(rpc,'boot',lambda:'22222222-2222-4222-8222-222222222222')
        else:
            facts=dict(p['owner']['facts']['unit-a'],services={'changed':True})
            monkeypatch.setattr(rpc,'native_facts',lambda unit:facts)
    probe.inside_gate = mutate
    with pytest.raises(native.NativeCapsError):rpc.enrolled_action('enroll','unit-a',p,runtime)
    assert enrollment_rows(state) == 0 and probe.reader._closed
