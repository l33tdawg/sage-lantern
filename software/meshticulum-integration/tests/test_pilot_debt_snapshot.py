# SPDX-License-Identifier: Apache-2.0
"""Real read-only file/SQLite controls with explicitly modeled root identity.

Source files and journals are real local fixtures; UID observations are patched
to root without changing the production root guard. No device/network/native
execution, approval or enrollment is represented by these controls.
"""
from dataclasses import asdict,replace
import hashlib,json,os,sqlite3
from pathlib import Path

import pytest

from meshticulum.contracts.identity import PeerID,SessionKey
from meshticulum.bench.wire import BenchGrant
from meshticulum.ip.authority import grant_from_plan
from meshticulum.ip.wire import IpLimits
from lantern_meshticulum.bench.profiles import profile_for
from lantern_meshticulum.bench import pilot_debt_snapshot as module

BOOT='00000000-0000-4000-8000-000000000001'
OTHER='00000000-0000-4000-8000-000000000002'
RUN='1'*32
PEERS=[PeerID('readonly-public-fixture',c*64)for c in 'ab']


class RootStat:
    def __init__(self,value):self.value=value;self.st_uid=0
    def __getattr__(self,key):return getattr(self.value,key)


@pytest.fixture
def root_observation(monkeypatch):
    actual_stat,actual_fstat=os.stat,os.fstat
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(os,'stat',lambda *a,**k:RootStat(actual_stat(*a,**k)))
    monkeypatch.setattr(os,'fstat',lambda *a,**k:RootStat(actual_fstat(*a,**k)))
    monkeypatch.setattr(os,'lstat',lambda *a,**k:RootStat(actual_stat(*a,follow_symlinks=False,**k)))


def write(path,value):
    path.write_bytes(value if type(value)is bytes else module.canonical(value));path.chmod(0o600)


def database(path,sql,rows):
    db=sqlite3.connect(path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        for statement in sql:db.execute(statement)
        for query,values in rows:db.executemany(query,values)
        db.commit()
    finally:db.close()
    path.chmod(0o600)


def fixture(tmp_path,transport='tcp',parent=None):
    state=tmp_path/'state';state.mkdir(mode=0o700);runs=state/'runs';runs.mkdir(mode=0o700)
    directory=runs/RUN;directory.mkdir(mode=0o700)
    shared=dict(host_boot_id=BOOT,local_peer=asdict(PEERS[0]),issued_epoch_us=100000000,expires_epoch_us=180000000,
                application_deadline_monotonic_us=320000000)
    obj=dict(bytes=64,sha256='e'*64)
    if transport=='tcp':
        p=dict(shared,schema='meshticulum.ip-service-run.v1',session=dict(initiator=asdict(PEERS[0]),responder=asdict(PEERS[1]),
            session_id=RUN,generation='2'*32),format='MR1.request',object=obj,route_binding_digest='c'*64,
            application_expires_epoch_us=180000000,initiator_address=['127.0.0.1',35001],
            responder_address=['127.0.0.1',35002],limits=asdict(IpLimits()))
        grant=grant_from_plan(p,b'x'*32,local_peer=PEERS[0],host_boot_id=BOOT)
        binding=grant.digest.hex();name='bytes.sqlite'
        database(directory/name,[
            'CREATE TABLE owner(id INTEGER PRIMARY KEY,binding BLOB,rx INTEGER,rx_pending INTEGER,rx_unknown INTEGER,fenced INTEGER)',
            'CREATE TABLE attempts(id INTEGER PRIMARY KEY,digest BLOB,bytes INTEGER,lane TEXT,written INTEGER,state TEXT)'],[
            ('INSERT INTO owner VALUES(?,?,?,?,?,?)',[(1,grant.digest,100,50,60,1)]),
            ('INSERT INTO attempts VALUES(?,?,?,?,?,?)',[(1,b'd'*32,130,'DATA',130,'KERNEL_ACCEPTED'),
                (2,b'd'*32,200,'DATA',10,'UNKNOWN'),(3,b'd'*32,150,'ACK',0,'NOT_SENT'),(4,b'd'*32,100,'ACK',20,'RESERVED')])])
    else:
        p=dict(shared,schema='meshticulum.service-run.v1',unit='unit-a',initiator=asdict(PEERS[0]),responder=asdict(PEERS[1]),
            session_id=RUN,generation='2'*32,profile=asdict(profile_for('sf7-500')),object=dict(obj,object_id=obj['sha256'],chunk_index=0),
            sender_unit='unit-a',country='MY',antenna_confirmed=True,max_frames=512,
            service=dict(kind='knowledge',format='MR1.request',application_expires_epoch_us=180000000))
        route={k:p[k]for k in('initiator','responder','session_id','generation','profile','object','sender_unit','country','service')}
        grant=BenchGrant(SessionKey(*PEERS,bytes.fromhex(RUN),bytes.fromhex('2'*32)),'bench_transfer',
            hashlib.sha256(module.canonical(route)).digest(),100000000,180000000,208,512,b'x'*32)
        binding='b'*64;name='attempts.sqlite'
        database(directory/name,[
            'CREATE TABLE owner(id INTEGER PRIMARY KEY,binding BLOB,max_attempts INTEGER,max_airtime INTEGER,request INTEGER,fenced INTEGER)',
            'CREATE TABLE attempts(request INTEGER PRIMARY KEY,packet_sha BLOB,bytes INTEGER,estimated_us INTEGER,state TEXT,evidence TEXT)'],[
            ('INSERT INTO owner VALUES(?,?,?,?,?,?)',[(1,bytes.fromhex(binding),256,60000000,999,1)]),
            ('INSERT INTO attempts VALUES(?,?,?,?,?,?)',[(i+1,b'd'*32,32,10+i,s,'driver source fixture')
                for i,s in enumerate(('RF_DONE','REFUSED','NOT_SENT','UNKNOWN','RESERVED'))])])
    write(directory/(name+'.owner'),b'')
    fields='only_row INTEGER PRIMARY KEY,binding BLOB,direction INTEGER,boot TEXT,'
    if transport=='tcp':fields+='original_deadline INTEGER,'
    fields+='deadline INTEGER,epoch INTEGER,mono INTEGER,tx INTEGER,high INTEGER,bitmap BLOB,retired INTEGER'
    values=(1,grant.digest,0,BOOT)+((320000000,)if transport=='tcp'else())+(319000000,150000000,280000000,4,7,bytes(32),1)
    database(directory/'wire.sqlite',['CREATE TABLE state('+fields+')'],[
        ('INSERT INTO state VALUES('+','.join('?'for _ in values)+')',[values])])
    write(directory/'grant.json',p);write(directory/'active',b'retired\n')
    provision=dict(schema='meshticulum.provisioned-public-pilot.v1',unit='unit-a',run_id=RUN,transport=transport,
        original_application_expires_epoch_us=180000000,original_application_deadline_us=320000000)
    if parent is not None:provision['parent']=parent
    write(directory/'provision.json',provision)
    job=dict(schema='meshticulum.native-public-job.v1',unit='unit-a',host_boot_id=BOOT,run_id=RUN,
        grant_context_sha256=grant.digest.hex(),transport=transport,pid=1234,start_ticks=22,command_sha256='f'*64)
    write(directory/'job.json',job)
    result=dict(schema='meshticulum.physical-service-run.v1'if transport=='lora'else'meshticulum.ip-public-node-run.v1',
        unit='unit-a',host_boot_id=BOOT,session_id=RUN,grant_context_sha256=grant.digest.hex(),service_format='MR1.request',
        original_application_expires_epoch_us=180000000,original_application_deadline_us=320000000,native_job=job)
    write(directory/'result.json',result)
    run=module.RunScope(RUN,BOOT,transport,module.sha((directory/'grant.json').read_bytes()),grant.digest.hex(),binding)
    scope=module.Scope('meshticulum.public-debt-scope.v1','unit-a',BOOT,'a'*64,'b'*64,250000000,(run,))
    return state,directory,p,scope


def collect(state,scope):return module.collect(state,scope,clock=lambda:(200000000,400000000),boot_reader=lambda:BOOT)


def before_after(directory):
    return {p.name:(p.read_bytes(),p.stat().st_mode,p.stat().st_size,p.stat().st_mtime_ns)for p in directory.iterdir()}


@pytest.mark.parametrize('transport,expected',[
    ('tcp',dict(radio_attempts=0,estimated_airtime_us=0,ip_frames=4,tx_stream_bytes=580,rx_stream_bytes=210)),
    ('lora',dict(radio_attempts=5,estimated_airtime_us=60,ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0)),
])
def test_exact_all_state_costs_match_core_public_digest_without_native_mutation(tmp_path,root_observation,transport,expected):
    state,directory,p,scope=fixture(tmp_path,transport);before=before_after(directory)
    result=collect(state,scope);row=result['runs'][0]
    assert row['status']=='scoped_rows_observed',row
    assert row['actual_cost']==expected and result['known_scoped_actual_cost']==expected
    assert row['journal']['has_unknown_or_pending'] is True
    assert len({r['packet_sha256']for r in row['journal']['attempts']})==1 # Retries still separately charged.
    assert row['grant']['context_sha256']==scope.runs[0].grant_context_sha256
    assert before_after(directory)==before and not any(directory.glob('*-wal'))
    assert not result['complete_global_debt']and not result['eligible_for_enrollment']and not result['parent_prepayment_overlap_resolved']
    assert result['MCU_observations']is None and result['current_terminal_ownership']is None


def test_collector_uses_only_readonly_allowed_paths_and_memory_sqlite(tmp_path,root_observation,monkeypatch):
    state,directory,p,scope=fixture(tmp_path)
    for name in('secret.bin','sender.bin','receiver.bin'):write(directory/name,b'PRIVATE-DO-NOT-READ')
    original_open,original_connect=os.open,sqlite3.connect;opened=[];connections=[]
    def guarded_open(path,flags,*args,**kwargs):
        name=os.fspath(path);opened.append(name)
        assert flags&os.O_ACCMODE==os.O_RDONLY and not flags&os.O_CREAT
        assert Path(name).name not in('secret.bin','sender.bin','receiver.bin')
        return original_open(path,flags,*args,**kwargs)
    def guarded_connect(path,*args,**kwargs):
        connections.append(path);assert path==':memory:';return original_connect(path,*args,**kwargs)
    monkeypatch.setattr(os,'open',guarded_open);monkeypatch.setattr(sqlite3,'connect',guarded_connect)
    report=collect(state,scope)
    assert report['scoped_rows_complete']and len(connections)==2
    assert 'PRIVATE-DO-NOT-READ'not in json.dumps(report)


@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
def test_any_sqlite_sidecar_is_unavailable_never_recovered_or_erased(tmp_path,root_observation,suffix):
    state,directory,p,scope=fixture(tmp_path);write(directory/('bytes.sqlite'+suffix),b'held or uncertain')
    before=before_after(directory);report=collect(state,scope);row=report['runs'][0]
    assert row['actual_cost']is None and row['reason']=='sqlite_sidecar_present'
    assert row['admitted_caps']['ip_frames']==2048 and report['known_scoped_actual_cost']is None
    assert before_after(directory)==before


def test_existing_owner_activity_is_unavailable_without_mutating_lock_or_rows(tmp_path,root_observation):
    import fcntl
    state,directory,p,scope=fixture(tmp_path);fd=os.open(directory/'bytes.sqlite.owner',os.O_RDONLY)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);before=before_after(directory)
        row=collect(state,scope)['runs'][0]
        assert row['actual_cost']is None and row['admitted_caps']['ip_frames']==2048
        assert before_after(directory)==before
    finally:os.close(fd)


@pytest.mark.parametrize('missing',['grant.json','job.json','result.json','bytes.sqlite','bytes.sqlite.owner','wire.sqlite'])
def test_missing_inputs_are_unknown_and_never_created(tmp_path,root_observation,missing):
    state,directory,p,scope=fixture(tmp_path);(directory/missing).unlink();before=before_after(directory)
    report=collect(state,scope)
    assert report['runs'][0]['actual_cost']is None and report['known_scoped_actual_cost']is None
    assert not report['scoped_rows_complete']and before_after(directory)==before


@pytest.mark.parametrize('change',['original_deadline','owner_binding','row_amount','wire_binding','schema'])
def test_tampered_rows_fail_provenance_without_repair(tmp_path,root_observation,change):
    state,directory,p,scope=fixture(tmp_path)
    name='wire.sqlite'if change in('original_deadline','wire_binding')else'bytes.sqlite'
    db=sqlite3.connect(directory/name)
    try:
        if change=='original_deadline':db.execute('UPDATE state SET original_deadline=321000000')
        elif change=='wire_binding':db.execute('UPDATE state SET binding=?',(b'z'*32,))
        elif change=='owner_binding':db.execute('UPDATE owner SET binding=?',(b'z'*32,))
        elif change=='row_amount':db.execute('UPDATE attempts SET bytes=-1 WHERE id=1')
        else:db.execute('ALTER TABLE attempts ADD COLUMN extra TEXT')
        db.commit()
    finally:db.close()
    before=before_after(directory);report=collect(state,scope)
    assert report['runs'][0]['actual_cost']is None and before_after(directory)==before


def test_context_and_attempt_binding_semantics_are_not_double_hashed(tmp_path,root_observation):
    state,directory,p,scope=fixture(tmp_path)
    bad=replace(scope,runs=(replace(scope.runs[0],attempt_binding_sha256=module.sha(bytes.fromhex(scope.runs[0].attempt_binding_sha256))),))
    assert collect(state,bad)['runs'][0]['actual_cost']is None
    missing=replace(scope,runs=(replace(scope.runs[0],grant_context_sha256=None,attempt_binding_sha256=None),))
    row=collect(state,missing)['runs'][0]
    assert row['actual_cost']is None and row['admitted_caps']['ip_frames']==2048


def test_unretired_or_symlinked_files_are_unknown_without_following(tmp_path,root_observation):
    state,directory,p,scope=fixture(tmp_path);write(directory/'active',b'active\n')
    assert collect(state,scope)['runs'][0]['actual_cost']is None
    write(directory/'active',b'retired\n');(directory/'grant.json').unlink()
    foreign=tmp_path/'foreign';write(foreign,b'private');(directory/'grant.json').symlink_to(foreign)
    row=collect(state,scope)['runs'][0]
    assert row['actual_cost']is None and 'private'not in json.dumps(row)


def test_parent_metadata_cannot_export_arbitrary_body_fields(tmp_path,root_observation):
    state,directory,p,scope=fixture(tmp_path,parent=dict(secret='SECRET-FROM-MALFORMED-PARENT',body='PRIVATE-BODY'))
    report=collect(state,scope)
    assert report['runs'][0]['actual_cost']is None
    assert 'SECRET-FROM-MALFORMED-PARENT'not in json.dumps(report)and 'PRIVATE-BODY'not in json.dumps(report)


def test_last_file_change_cannot_leave_a_scoped_cost_result(tmp_path,root_observation,monkeypatch):
    state,directory,p,scope=fixture(tmp_path);original=module._Reader.check;changed=[]
    def check(self):
        if 'wire.sqlite'in self.files and not changed:
            changed.append(True);write(directory/'active',b'active\n')
        return original(self)
    monkeypatch.setattr(module._Reader,'check',check)
    report=collect(state,scope)
    assert changed and report['runs'][0]['actual_cost']is None


def test_final_clock_mutation_cannot_reuse_released_earlier_evidence(tmp_path,root_observation,monkeypatch):
    state,directory,p,scope=fixture(tmp_path);returned=[];original=module._one
    def one(*args):
        result=original(*args)
        assert result['status']=='scoped_rows_observed'
        returned.append(True);return result
    monkeypatch.setattr(module,'_one',one);changed=[]
    def clock():
        if returned and not changed:
            changed.append(True);write(directory/'active',b'active\n')
        return(200000000,400000000)
    report=module.collect(state,scope,clock=clock,boot_reader=lambda:BOOT)
    assert returned==[True]and changed and report['runs'][0]['reason']=='final_input_changed'
    assert report['runs'][0]['actual_cost']is None and not report['scoped_rows_complete']


def test_same_named_real_columns_are_not_native_integer_state(tmp_path,root_observation):
    state,directory,p,scope=fixture(tmp_path);path=directory/'wire.sqlite';path.unlink()
    fields='only_row INTEGER PRIMARY KEY,binding BLOB,direction REAL,boot TEXT,original_deadline INTEGER,'
    fields+='deadline INTEGER,epoch INTEGER,mono INTEGER,tx INTEGER,high INTEGER,bitmap BLOB,retired REAL'
    values=(1,bytes.fromhex(scope.runs[0].grant_context_sha256),0.0,BOOT,320000000,319000000,150000000,280000000,4,7,bytes(32),1.0)
    database(path,['CREATE TABLE state('+fields+')'],[('INSERT INTO state VALUES('+','.join('?'for _ in values)+')',[values])])
    report=collect(state,scope)
    assert report['runs'][0]['actual_cost']is None and report['runs'][0]['reason']=='invalid_journal_schema'


@pytest.mark.parametrize('name',['grant.json','bytes.sqlite.owner'])
def test_fifo_metadata_is_refused_without_a_blocking_open(tmp_path,root_observation,name,monkeypatch):
    state,directory,p,scope=fixture(tmp_path);(directory/name).unlink();os.mkfifo(directory/name,0o600)
    original_open=os.open;calls=[]
    def observed_open(path,flags,*args,**kwargs):
        calls.append((os.fspath(path),flags))
        if os.fspath(path)==name:assert flags&os.O_NONBLOCK
        return original_open(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',observed_open)
    report=collect(state,scope)
    assert report['runs'][0]['actual_cost']is None and not report['scoped_rows_complete']
    assert report['runs'][0]['reason']=='unprotected_metadata'


def test_readonly_guard_refuses_nonroot_before_any_open(tmp_path,monkeypatch):
    state,directory,p,scope=fixture(tmp_path)
    monkeypatch.setattr(os,'geteuid',lambda:1000)
    monkeypatch.setattr(os,'open',lambda *a,**k:(_ for _ in()).throw(AssertionError('opened before root refusal')))
    with pytest.raises(module.SnapshotError,match='requires_root'):collect(state,scope)


@pytest.mark.parametrize('bad',['expired','regressed','boot'])
def test_original_observation_window_boot_and_monotonic_progress_cannot_be_renewed(tmp_path,root_observation,bad):
    state,directory,p,scope=fixture(tmp_path);calls=[]
    def clock():
        calls.append(1)
        if len(calls)==1:return(200000000,400000000)
        if bad=='expired':return(250000000,450000000)
        if bad=='regressed':return(199999999,399999999)
        return(200000000,400000000)
    reads=[]
    def boot_reader():
        reads.append(1);return BOOT if len(reads)==1 else OTHER
    before=before_after(directory)
    with pytest.raises(module.SnapshotError):module.collect(state,scope,clock=clock,boot_reader=boot_reader if bad=='boot'else lambda:BOOT)
    assert before_after(directory)==before


def test_scope_decoder_rejects_extra_fields_unknown_units_duplicate_runs_and_missing_pins(tmp_path):
    state,directory,p,scope=fixture(tmp_path);raw=json.loads(module.canonical(asdict(scope)))
    assert module.Scope.from_dict(raw)==scope
    for changed in(dict(raw,extra='no'),dict(raw,unit='other'),dict(raw,runs=raw['runs']*2),dict(raw,runtime_sha256=None)):
        with pytest.raises(module.SnapshotError):module.Scope.from_dict(changed)
