"""Exact actual pins with genuine protected model files; no devices or grants."""
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

import pytest

from lantern_meshticulum.bench import capture_8120 as pinned, capture_a658, retained_capture as retained
from lantern_meshticulum.bench.history_identity_seal import capture, SealedHistory
from lantern_meshticulum.bench.history_metadata_fence import FenceError, HistoricalMetadataFence
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from meshticulum.ip.wire import IpError

sha=lambda raw:hashlib.sha256(raw).hexdigest()


@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def sample(request,synthetic_a658_capture):
    base=Path(tempfile.mkdtemp(prefix='.8120-capture-',dir=Path(__file__).parent))
    state=base/'state';state.mkdir(mode=0o700)
    for name in ('parents','runs'):(state/name).mkdir(mode=0o700)
    for parent in retained.PAID_HISTORY:(state/'parents'/parent).mkdir(mode=0o700)
    unit=request.param;now=(pinned.ABSOLUTE_END+1,pinned.UNITS[unit]['phase']['deadline_monotonic_us']+pinned.ABSOLUTE_END-pinned.END+1)
    clock=lambda:now
    synthetic_a658_capture(state,unit,clock)
    try:yield state,unit,pinned.row(unit),clock
    finally:shutil.rmtree(base)


def test_actual_evidence_keeps_exact_paid_prefix_and_empty_enrollment_classification():
    assert len(retained.PAID_HISTORY)==42 and pinned.PARENT not in retained.PAID_HISTORY
    assert sha(canonical(retained.PAID_HISTORY))=='ebf29572ee1b76fd9e14b536a2ad4b1cd0eca4016a195df108df84f7fb4a04b2'
    assert pinned.EXPIRY_COMPLETION_SHA256=='a996e21a8d632d4b26c2baf623a1ec8f981ebc66344de678352b0f3371de7aa2'
    assert pinned.H42_RECEIPT_SHA256=='68f7a680452b111ba6f826ff4566dd7a25e8299d7682e7979bd095730e87f489'
    assert pinned.OBSERVATION_SHA256['unit-c']=='48122f0c112bc8366a895e51e81c25ca294535dfe794d246816d8cb16c488493'
    assert pinned.START<pinned.WHOLE_END<pinned.END<pinned.ABSOLUTE_END
    assert [len(pinned.row(u)['files']) for u in ('unit-a','unit-b','unit-c')]==[4,4,10]
    for unit in ('unit-a','unit-b','unit-c'):
        assert retained.expected_capture_only_parents(unit)[-1]==pinned.PARENT
        row=pinned.row(unit)
        assert row['phase']['deadline_monotonic_us']==row['phase']['monotonic_us']+pinned.END-row['phase']['epoch_us']
    with pytest.raises(RuntimeError,match='invalid_own_parent'):
        rpc.capture_history('unit-c',dict(parent_id=pinned.PARENT),None)


def test_all_observed_metadata_enters_real_seal_without_new_debt(sample):
    state,unit,row,clock=sample;target=state/'parents'/pinned.PARENT
    original={name:(target/name).read_bytes() for name in row['files']}
    expected=retained.PAID_HISTORY+((capture_a658.PARENT,) if unit!='unit-a' else ())+(pinned.PARENT,)
    assert retained.historical_parent_ids(state,unit,clock)==expected
    files={name:pin for name,pin in retained.capture_paths(state,unit)['files'].items() if (state/name).exists()}
    dirs={'':['parents','runs'],'parents':list(expected),'runs':[]}
    dirs.update({'parents/'+p:[] for p in retained.PAID_HISTORY})
    for parent in expected[42:]:
        prefix='parents/'+parent;dirs[prefix]=os.listdir(state/prefix);dirs[prefix+'/prepared-identities']=[]
    raw,pin=capture(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
    with SealedHistory(state,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin) as held:
        held.check()
        changed=target/('native.sqlite' if unit=='unit-c' else 'capture.json')
        changed.write_bytes(b'changed')
        with pytest.raises(FenceError):held.check()
    changed.write_bytes(original[changed.name])
    assert original=={name:(target/name).read_bytes() for name in row['files']}
    record=json.loads(original['capture.json'])
    assert record['refunds']==0 and record['original_unknown_actual_cost'] is None and record['global_debt_complete'] is False
    assert tuple(v['policy']['parent_id'] for v in record['history']['previous'])==retained.PAID_HISTORY
    if unit=='unit-c':
        with sqlite3.connect((target/'native.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            assert db.execute('SELECT name FROM sqlite_master').fetchall()==[]
        assert original['native.sqlite.owner']==original['native.sqlite.approval']==b''
    else:assert not (target/'native.sqlite').exists()


@pytest.mark.parametrize('fault',['missing','pending','sidecar','prepared','extra','symlink','hardlink',
    'mode','boot','absolute_epoch','absolute_monotonic','expiry_pin','native_database','owner_lock'])
def test_namespace_bytes_lock_and_all_original_expiry_gates_refuse(sample,monkeypatch,fault):
    state,unit,row,clock=sample;target=state/'parents'/pinned.PARENT;path=target/'capture.json';fd=None
    if fault=='missing':path.unlink()
    elif fault in ('pending','sidecar','extra'):
        name={'pending':'native.sqlite.pending','sidecar':'native.sqlite-wal','extra':'unknown.json'}[fault]
        (target/name).write_bytes(b'changed');(target/name).chmod(0o600)
    elif fault=='prepared':(target/'prepared-identities'/('a'*32+'.json')).write_bytes(b'changed')
    elif fault=='symlink':path.rename(target/'saved');path.symlink_to(target/'saved')
    elif fault=='hardlink':os.link(path,state/'extra-link')
    elif fault=='mode':path.chmod(0o644)
    elif fault=='boot':monkeypatch.setattr(retained,'current_boot',lambda:'changed')
    elif fault=='absolute_epoch':
        observed=clock();clock=lambda:(pinned.ABSOLUTE_END-1,observed[1])
    elif fault=='absolute_monotonic':
        observed=clock();clock=lambda:(observed[0],row['phase']['deadline_monotonic_us']+pinned.ABSOLUTE_END-pinned.END-1)
    elif fault=='expiry_pin':monkeypatch.setattr(pinned,'EXPIRY_COMPLETION_SHA256',None)
    elif fault=='native_database':(target/'native.sqlite').write_bytes(b'changed')
    else:
        lock=target/('native.sqlite.owner' if unit=='unit-c' else 'capture.json')
        fd=os.open(lock,os.O_RDONLY);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        with pytest.raises((FenceError,OSError,IpError)):retained.historical_parent_ids(state,unit,clock)
    finally:
        if fd is not None:os.close(fd)


@pytest.mark.parametrize('fault',['history','refund','phase','schema','binding','approval','database'])
def test_repinning_one_changed_file_cannot_reclassify_capture_or_empty_db(sample,monkeypatch,fault):
    state,unit,row,clock=sample;target=state/'parents'/pinned.PARENT;changed=deepcopy(row)
    if fault in ('approval','database'):
        if unit!='unit-c':
            (target/'native.sqlite').write_bytes(b'not a capture-only namespace')
        else:
            name='native.sqlite.approval' if fault=='approval' else 'native.sqlite'
            if fault=='approval':(target/name).write_bytes(b'approval')
            else:
                with sqlite3.connect(target/name) as db:db.execute('CREATE TABLE enrollment(id INTEGER)')
            changed['files'][name]=sha((target/name).read_bytes())
    else:
        path=target/'capture.json';value=json.loads(path.read_bytes())
        if fault=='history':value['history']['previous'].reverse()
        elif fault=='refund':value['refunds']=1
        elif fault=='phase':value['native_phase']['epoch_us']+=1
        elif fault=='schema':value['schema']='meshticulum.private-resume-actual-capture.v1'
        else:value['history_binding_sha256']='f'*64
        raw=canonical(value);path.write_bytes(raw);changed['files']['capture.json']=sha(raw)
    monkeypatch.setattr(pinned,'UNITS',dict(pinned.UNITS,**{unit:changed}))
    with pytest.raises(FenceError):retained.historical_parent_ids(state,unit,clock)


def test_adjacent_parent_cannot_import_capture_allowlist(sample):
    state,unit,row,clock=sample;wrong='8120145bab3fd1e6ac0115edbfe754ae';target=state/'parents'/wrong
    target.mkdir(mode=0o700);(target/'capture.json').write_bytes(b'{}');(target/'capture.json').chmod(0o600)
    with pytest.raises(FenceError,match='outside_retained_capture_metadata'):
        HistoricalMetadataFence(state,owner_uid=os.geteuid(),expected_files={'parents/'+wrong+'/capture.json':sha(b'{}')},
            expected_directories={'':['parents','runs'],'parents':os.listdir(state/'parents'),'parents/'+wrong:['capture.json']})
