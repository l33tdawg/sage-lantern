"""Captured original grant codec plus actual schema/rows; no native authority."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
from lantern_meshticulum.bench import pilot_debt_snapshot as m
from lantern_meshticulum.bench.profiles import RADIO_ADMISSION
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.wire import WireError
from tests.test_pilot_debt_snapshot import root_observation, write, database, before_after

FIXTURES = Path(__file__).with_name('fixtures')
DATA = json.loads((FIXTURES/'current5733-public-debt-decoder.json').read_bytes())
NATIVE = json.loads((FIXTURES/'current5733-sf5-startup-metadata.json').read_bytes())
RUN = '3ef93b13ec6966964c2f73d1dbd0a176'


def original(unit='unit-c'):
    plan = deepcopy(DATA['public_metadata']['grant'])
    parent = deepcopy(NATIVE['prepared'][unit][RUN])
    local = next(u for u in NATIVE['policy']['units'] if u['unit'] == unit)
    plan.update(unit=unit,host_boot_id=local['host_boot_id'],local_peer=local['peer'],
                application_deadline_monotonic_us=parent['application_deadline_monotonic_us'])
    assert m.sha(m.canonical(plan)) == parent['native_plan_sha256']
    observed = NATIVE['native'][unit]['legacy_runs'][0]
    run = m.RunScope(RUN,plan['host_boot_id'],'lora',m.sha(m.canonical(plan)+b'\n'),
        parent['grant_context_sha256'],observed['journal']['owner']['binding'])
    now = NATIVE['owners'][unit]['phase_end_epoch_us']+1
    scope = m.Scope('meshticulum.public-debt-scope.v1',unit,plan['host_boot_id'],
        NATIVE['owners'][unit]['runtime_sha256'],NATIVE['owners'][unit]['manifest_sha256'],
        now+30000000,(run,))
    return plan,parent,scope,run,now


@pytest.mark.parametrize('unit',['unit-a','unit-c'])
def test_original_captured_grants_decode_without_repricing_or_live_admission(unit):
    plan,parent,scope,run,now = original(unit)
    expected = NATIVE['native'][unit]['legacy_runs'][0]['grant']
    assert m._public_grant(plan,scope,run,historical_parent=parent,observed_epoch_us=now) == expected
    with pytest.raises(WireError):
        RADIO_ADMISSION.check('MY',True,RadioProfile(**plan['profile']))
    with pytest.raises(WireError):
        m._public_grant(plan,scope,run)


@pytest.mark.parametrize('fault',['parent','policy','unit','run','grant','runtime','manifest','context','binding','plan','expiry'])
def test_historical_exception_is_bound_to_original_immutable_records(fault):
    plan,parent,scope,run,now = original()
    if fault == 'parent':parent['parent_id']='f'*32
    elif fault == 'policy':parent['policy_digest']='f'*64
    elif fault == 'unit':scope=replace(scope,unit='unit-a')
    elif fault == 'run':run=replace(run,run_id='f'*32)
    elif fault == 'grant':run=replace(run,grant_sha256='f'*64)
    elif fault == 'runtime':scope=replace(scope,runtime_sha256='f'*64)
    elif fault == 'manifest':scope=replace(scope,manifest_sha256='f'*64)
    elif fault == 'context':run=replace(run,grant_context_sha256='f'*64)
    elif fault == 'binding':run=replace(run,attempt_binding_sha256='f'*64)
    elif fault == 'plan':plan['issued_epoch_us']-=1
    else:now=plan['expires_epoch_us']
    with pytest.raises((WireError,m.SnapshotError)):
        m._public_grant(plan,scope,run,historical_parent=parent,observed_epoch_us=now)


def test_complete_readonly_collector_preserves_stored_attempts_and_wire(tmp_path,root_observation):
    plan,parent,scope,run,now=original()
    state=tmp_path/'state';state.mkdir(mode=0o700);(state/'runs').mkdir(mode=0o700)
    path=state/'runs'/RUN;path.mkdir(mode=0o700)
    write(path/'grant.json',m.canonical(plan)+b'\n');write(path/'active',b'retired\n')
    for name,value in DATA['public_metadata'].items():
        if name!='grant':write(path/(name+'.json'),value)
    write(path/'result.json',NATIVE['outcomes']['unit-c'][RUN])
    journal=DATA['original_legacy_row']['journal'];owner=journal['owner']
    database(path/'attempts.sqlite',[
        'CREATE TABLE owner(id INTEGER PRIMARY KEY,binding BLOB,max_attempts INTEGER,max_airtime INTEGER,request INTEGER,fenced INTEGER)',
        'CREATE TABLE attempts(request INTEGER PRIMARY KEY,packet_sha BLOB,bytes INTEGER,estimated_us INTEGER,state TEXT,evidence TEXT)'],[
        ('INSERT INTO owner VALUES(?,?,?,?,?,?)',[(1,bytes.fromhex(owner['binding']),owner['max_attempts'],owner['max_airtime_us'],owner['request'],int(owner['fenced']))]),
        ('INSERT INTO attempts VALUES(?,?,?,?,?,?)',[(a['request'],bytes.fromhex(a['packet_sha256']),a['bytes'],a['estimated_us'],a['state'],'local schema evidence') for a in journal['attempts']])])
    write(path/'attempts.sqlite.owner',b'')
    wire=DATA['original_legacy_row']['wire'];fields=('only_row','binding','direction','boot','deadline','epoch','mono','tx','high','bitmap','retired')
    values=tuple(bytes.fromhex(wire[k]) if k in ('binding','bitmap') else wire[k] for k in fields)
    database(path/'wire.sqlite',['CREATE TABLE state(only_row INTEGER PRIMARY KEY,binding BLOB,direction INTEGER,boot TEXT,deadline INTEGER,epoch INTEGER,mono INTEGER,tx INTEGER,high INTEGER,bitmap BLOB,retired INTEGER)'],
             [('INSERT INTO state VALUES('+','.join('?' for _ in values)+')',[values])])
    before=before_after(path)
    report=m.collect(state,scope,clock=lambda:(now,now),boot_reader=lambda:plan['host_boot_id'])
    row,=report['runs']
    assert row['status']=='scoped_rows_observed',row
    assert row['actual_cost']==DATA['original_legacy_row']['actual_cost']
    assert row['grant']==DATA['original_legacy_row']['grant'] and row['wire']==wire
    assert row['journal']['owner']==owner and len(row['journal']['attempts'])==8
    assert all(a['estimated_us']==12986 for a in row['journal']['attempts'])
    assert row['parent_reference']==DATA['original_legacy_row']['parent_reference']
    assert before_after(path)==before and report['eligible_for_enrollment'] is False
