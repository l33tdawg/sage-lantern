"""Actual Core nonce/attempt SQLite and AEAD source controls, not RF evidence."""
from copy import deepcopy
from dataclasses import asdict,replace
import hashlib
import json
import sqlite3

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import BenchGrant,SessionCipher,FrameKind
from meshticulum.contracts.identity import SessionKey
from lantern_meshticulum.bench.profiles import profile_for
from lantern_meshticulum.bench import pilot_debt_snapshot as module
from tests.test_pilot_debt_snapshot import root_observation,write,before_after,BOOT,RUN,PEERS


def genuine32(tmp_path,fmt='MR1.response'):
    state=tmp_path/'state';state.mkdir(mode=0o700);runs=state/'runs';runs.mkdir(mode=0o700);directory=runs/RUN;directory.mkdir(mode=0o700)
    unit='unit-c' if fmt=='MR1.response' else 'unit-a';clock=lambda:(110000000,250000000)
    plan=dict(schema='meshticulum.service-run.v1',unit=unit,host_boot_id=BOOT,local_peer=asdict(PEERS[0]),
        initiator=asdict(PEERS[0]),responder=asdict(PEERS[1]),session_id=RUN,generation='2'*32,
        issued_epoch_us=100000000,expires_epoch_us=180000000,profile=asdict(profile_for('sf7-500')),
        object=dict(bytes=32768 if fmt=='MR1.response' else 74,sha256='e'*64,object_id='e'*64,chunk_index=0),
        sender_unit=unit,country='MY',antenna_confirmed=True,max_frames=512,
        service=dict(kind='knowledge',format=fmt,application_expires_epoch_us=180000000,public_response_max_bytes=32768),
        application_deadline_monotonic_us=320000000)
    route={k:plan[k] for k in ('initiator','responder','session_id','generation','profile','object','sender_unit','country','service')}
    grant=BenchGrant(SessionKey(*PEERS,bytes.fromhex(RUN),bytes.fromhex('2'*32)),'bench_transfer',
        hashlib.sha256(module.canonical(route)).digest(),100000000,180000000,208,512,b'x'*32)
    binding=hashlib.sha256(b'actual-source-attempt-binding'+grant.digest).digest()
    store=SessionStore(directory/'wire.sqlite',grant,PEERS[0],boot_id=BOOT,clock=clock,create=True)
    journal=AttemptJournal(directory/'attempts.sqlite',binding=binding,max_attempts=256,max_airtime_us=60000000,create=True,request_clock=lambda:250000000)
    cipher=SessionCipher(grant,PEERS[0],store)
    def source_scope_current():return grant.issued_epoch_us<=clock()[0]<grant.expires_epoch_us
    count=170 if fmt=='MR1.response' else 1
    try:
        for i in range(count):
            packet=cipher.seal(bytes([i%256])*193,FrameKind.DATA,authorize=source_scope_current)
            token=journal.reserve(packet,1000);journal.settle(token,packet,'RF_DONE','source-only synthetic driver completion')
        store.retire()
    finally:journal.close();store.close()
    write(directory/'grant.json',plan);write(directory/'active',b'retired\n')
    write(directory/'provision.json',dict(schema='meshticulum.provisioned-public-pilot.v2',unit=unit,run_id=RUN,transport='lora',
        original_application_expires_epoch_us=180000000,original_application_deadline_us=320000000))
    job=dict(schema='meshticulum.native-public-job.v1',unit=unit,host_boot_id=BOOT,run_id=RUN,grant_context_sha256=grant.digest.hex(),
        transport='lora',pid=1234,start_ticks=22,command_sha256='f'*64)
    write(directory/'job.json',job)
    write(directory/'result.json',dict(schema='meshticulum.physical-service-run.v1',unit=unit,host_boot_id=BOOT,session_id=RUN,
        grant_context_sha256=grant.digest.hex(),service_format=fmt,original_application_expires_epoch_us=180000000,
        original_application_deadline_us=320000000,native_job=job,complete=False))
    run=module.RunScope(RUN,BOOT,'lora',module.sha((directory/'grant.json').read_bytes()),grant.digest.hex(),binding.hex())
    scope=module.Scope('meshticulum.public-debt-scope.v1',unit,BOOT,'a'*64,'b'*64,250000000,(run,))
    return state,directory,plan,scope,count


def observe(state,scope):return module.collect(state,scope,clock=lambda:(200000000,400000000),boot_reader=lambda:BOOT)


@pytest.mark.parametrize('fmt',['MR1.request','MR1.response'])
def test_actual_32_marker_real_journals_readonly_costs_and_cipher_flow_distinction(tmp_path,root_observation,fmt):
    state,directory,plan,scope,count=genuine32(tmp_path,fmt);before=before_after(directory)
    row=observe(state,scope)['runs'][0]
    assert row['status']=='scoped_rows_observed' and row['actual_cost']==dict(radio_attempts=count,estimated_airtime_us=count*1000,ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0),row
    assert row['grant']['max_frames']==512 and row['grant']['flow_max_frames']==(256 if fmt=='MR1.response' else 128)
    assert row['wire']['tx']==count and row['wire']['retired']==1 and not row['journal']['has_unknown_or_pending']
    assert before_after(directory)==before
    assert not row['parent_prepayment_verified'] and not row['physical_quiescence_observed']
    assert row['grant']['context_sha256']==scope.runs[0].grant_context_sha256


@pytest.mark.parametrize('fault',['no_marker','false_marker','wrong_marker','private','short','larger','wrong_peer','wrong_sender','deadline','cost','source_binding'])
def test_public32_retrospective_mutations_refuse_without_rewriting_files(tmp_path,root_observation,fault):
    state,directory,plan,scope,_=genuine32(tmp_path);p=deepcopy(plan)
    if fault=='no_marker':p['service'].pop('public_response_max_bytes')
    elif fault=='false_marker':p['service']['public_response_max_bytes']=True
    elif fault=='wrong_marker':p['service']['public_response_max_bytes']=32769
    elif fault=='private':p['service'].update(kind='protected_message',format='MPF2.record')
    elif fault=='short':p['object']['bytes']=32767
    elif fault=='larger':p['object']['bytes']=32769
    elif fault=='wrong_peer':p['local_peer']['agent_id']='c'*64
    elif fault=='wrong_sender':p['sender_unit']='unit-b'
    elif fault=='deadline':p['application_deadline_monotonic_us']=False
    elif fault=='cost':p['profile']['max_boot_attempts']=255
    elif fault=='source_binding':p['service']['application_expires_epoch_us']-=1
    write(directory/'grant.json',p)
    scope=replace(scope,runs=(replace(scope.runs[0],grant_sha256=module.sha((directory/'grant.json').read_bytes())),))
    before=before_after(directory);row=observe(state,scope)['runs'][0]
    assert row['actual_cost'] is None and row['status']=='unavailable'
    assert before_after(directory)==before


def test_public32_wire_source_binding_mutation_and_full_cap_retention(tmp_path,root_observation):
    state,directory,plan,scope,_=genuine32(tmp_path)
    db=sqlite3.connect(directory/'wire.sqlite')
    try:db.execute('UPDATE state SET binding=?',(bytes(32),));db.commit()
    finally:db.close()
    before=before_after(directory);row=observe(state,scope)['runs'][0]
    assert row['actual_cost'] is None and row['conservative_unknown_caps']['radio_attempts']==256
    assert row['conservative_unknown_caps']['estimated_airtime_us']==60000000 and before_after(directory)==before
