# SPDX-License-Identifier: Apache-2.0
"""Controller-only durable full-cap accounting; no native claim or devices.

Real private SQLite and lifetime locks exercise atomic pair payment, retained
unknown commits, reopening and callback fences. Packages use the existing pure
public-pilot constructor; no controller, provisioner, socket or RF is started.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
import sqlite3
import threading

import pytest

from meshticulum.contracts.identity import PeerID
from meshticulum.resources import resource
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import (
    Cost, UnitCaps, ParentPolicy, ParentCaps, ParentCapsError, PairPermit, child_caps, canonical,
)

BOOT = '00000000-0000-4000-8000-000000000001'
OTHER_BOOT = '00000000-0000-4000-8000-000000000002'
PEERS = {u: dict(chain_id='public-parent-source-controls', agent_id=c*64)
         for u, c in zip(('unit-a','unit-b'), 'ab')}
REQUEST = resource.encode_request('/packs/'+'e'*64+'/chunks/'+'f'*64+'.json', 0, 16356)
RESPONSE = resource.BlockServer(lambda _: b'public source fixture').handle(REQUEST)
LIMIT = Cost(1024, 240000000, 8192, 4194304, 4194304)


class Clock:
    def __init__(self):
        self.epoch, self.mono, self.calls, self.hook = 100000000, 50000000, 0, None
        self.boot = BOOT
    def __call__(self):
        self.calls += 1
        if self.hook is not None: self.hook(self.calls)
        return self.epoch, self.mono


def policy(*, limits=LIMIT, aggregate=None, prior=None):
    prior = prior if prior is not None else (Cost(1,1000,1,32,32), Cost(2,2000,2,64,64))
    units = tuple(UnitCaps(u, BOOT, PeerID(**PEERS[u]), limits, debt, c*64)
                  for u,debt,c in zip(PEERS, prior, 'ab'))
    return ParentPolicy('1'*32, BOOT, 90000000, 200000000, 40000000, 150000000,
                        units, aggregate or limits.plus(limits), 'c'*64)


def packages(p, transport='tcp', *, source=None, fmt='MR1.request', expiry=180000000):
    body = REQUEST if fmt == 'MR1.request' else RESPONSE
    pair = pair_packages(campaign_id='d'*32, resource_sha256='e'*64,
        application_expires_epoch_us=expiry, sender='unit-a', receiver='unit-b', transport=transport,
        format=fmt, descriptor=dict(bytes=len(body),sha256=hashlib.sha256(body).hexdigest()),
        source=source, request=body if source is None else None,
        peers={u.unit:asdict(u.peer) for u in p.units}, boots={u.unit:u.host_boot_id for u in p.units},
        now_epoch_us=100000000, endpoints={'unit-a':('127.0.0.1',35001),'unit-b':('127.0.0.1',35002)},
        radio_permission=dict(country='MY',antenna_confirmed=True))
    return list(pair['packages'].values())


@pytest.fixture
def state(tmp_path):
    directory = tmp_path/'private-parent'; directory.mkdir(mode=0o700)
    clock = Clock(); p = policy(); path = directory/'caps.sqlite'
    ledger = ParentCaps.enroll(path,p,clock=clock,boot_reader=lambda:clock.boot)
    yield ledger, clock, p, path
    clock.hook = None
    ledger.close()


@pytest.mark.parametrize('transport,expected',[
    ('lora',Cost(256,60000000)), ('tcp',Cost(0,0,2048,1048576,1048576)),
])
def test_pure_extraction_charges_actual_full_enforced_maxima_without_io(monkeypatch,transport,expected):
    p = policy(); pair = packages(p,transport)
    def forbid(*args,**kwargs): raise AssertionError('extraction attempted I/O')
    monkeypatch.setattr(os,'open',forbid); monkeypatch.setattr(sqlite3,'connect',forbid)
    children = tuple(child_caps(x,p) for x in pair)
    assert [x.cost for x in children] == [expected,expected]
    assert children[0].sends and not children[1].sends
    assert children[0].run_id == children[1].run_id
    meta = json.loads(children[0].metadata)
    assert meta['cipher_frame_limit'] == (512 if transport=='lora' else 2048)
    assert meta['policy_digest']==p.digest and meta['secret_sha256']==hashlib.sha256(__import__('base64').b64decode(pair[0]['secret_base64'])).hexdigest()
    assert pair[0]['secret_base64'].encode() not in children[0].metadata
    assert pair[0]['payload_base64'].encode() not in children[0].metadata


@pytest.mark.parametrize('transport',['lora','tcp'])
def test_native_served_public_response_source_is_exactly_supported(transport):
    p=policy(); source=dict(kind='served_public_chunk',request_run_id='4'*32)
    pair=packages(p,transport,source=source,fmt='MR1.response')
    assert json.loads(child_caps(pair[0],p).metadata)['source_run_id']==source
    pair[0]['source_run_id']['unexpected']=True
    with pytest.raises(ParentCapsError): child_caps(pair[0],p)


def test_atomic_pair_payment_preserves_prior_full_caps_and_one_use_after_reopen(state):
    ledger,clock,p,path=state; pair=packages(p)
    permit=ledger.prepay_pair(pair)
    before=ledger.snapshot()
    assert before['allocations']==1 and before['native_enforcement'] is False
    cost=Cost(0,0,2048,1048576,1048576)
    assert before['per_unit']=={u.unit:asdict(u.prior_debt.plus(cost)) for u in p.units}
    assert ledger.db.execute('PRAGMA journal_mode').fetchone()==('wal',)
    assert ledger.db.execute('PRAGMA synchronous').fetchone()==(2,)
    assert ledger.db.execute('SELECT COUNT(*) FROM allocations').fetchone()==(1,)
    children=ledger.consume(permit)
    assert len(children)==2 and ledger.snapshot()==before
    permit.used=False
    with pytest.raises(ParentCapsError):ledger.consume(permit)
    assert ledger.close() is False
    reopened=ParentCaps.open(path,p,clock=clock,boot_reader=lambda:BOOT)
    try:
        assert reopened.snapshot()==before
        fake=PairPermit(reopened,permit.key,children)
        with pytest.raises(ParentCapsError):reopened.consume(fake)
        assert reopened.snapshot()['aggregate']==before['aggregate']
    finally:reopened.close()


def test_open_never_creates_and_enroll_never_replaces_existing_policy(tmp_path):
    directory=tmp_path/'private';directory.mkdir(mode=0o700);path=directory/'caps.sqlite';p=policy();clock=Clock()
    with pytest.raises(ParentCapsError):ParentCaps.open(path,p,clock=clock,boot_reader=lambda:BOOT)
    assert not path.exists()
    ledger=ParentCaps.enroll(path,p,clock=clock,boot_reader=lambda:BOOT);assert ledger.close()
    raw=path.read_bytes()
    with pytest.raises(ParentCapsError):ParentCaps.enroll(path,p,clock=clock,boot_reader=lambda:BOOT)
    assert path.read_bytes()==raw
    for changed in (replace(p,expires_epoch_us=201000000),replace(p,prior_snapshot_sha256='f'*64),
                    replace(p,controller_boot_id=OTHER_BOOT),replace(p,aggregate_limits=p.aggregate_limits.plus(Cost(1)))):
        with pytest.raises(ParentCapsError):ParentCaps.open(path,changed,clock=clock,boot_reader=lambda:BOOT)
    ledger=ParentCaps.open(path,p,clock=clock,boot_reader=lambda:BOOT);assert ledger.close()


@pytest.mark.parametrize('axis,transport',[
    ('radio_attempts','lora'),('estimated_airtime_us','lora'),('ip_frames','tcp'),
    ('tx_stream_bytes','tcp'),('rx_stream_bytes','tcp'),
])
def test_one_endpoint_limit_refuses_whole_pair_without_partial_payment(tmp_path,axis,transport):
    directory=tmp_path/'private';directory.mkdir(mode=0o700);clock=Clock()
    p=policy(prior=(Cost(),Cost()))
    full=child_caps(packages(p,transport)[0],p).cost
    limits=asdict(LIMIT);limits[axis]=asdict(full)[axis]-1
    p=replace(p,units=(replace(p.units[0],limits=Cost(**limits)),p.units[1]))
    ledger=ParentCaps.enroll(directory/'caps.sqlite',p,clock=clock,boot_reader=lambda:BOOT)
    try:
        with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p,transport))
        assert ledger.snapshot()['allocations']==0
        assert not ledger.pending.exists()
    finally:ledger.close()


def test_aggregate_limit_refuses_even_when_each_endpoint_fits(tmp_path):
    directory=tmp_path/'private';directory.mkdir(mode=0o700);clock=Clock()
    p=policy(prior=(Cost(),Cost()),aggregate=Cost(1024,240000000,4095,4194304,4194304))
    ledger=ParentCaps.enroll(directory/'caps.sqlite',p,clock=clock,boot_reader=lambda:BOOT)
    try:
        with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
        assert ledger.snapshot()['allocations']==0
    finally:ledger.close()


@pytest.mark.parametrize('committed',[False,True])
def test_unknown_commit_retains_fence_and_exact_joint_debt_on_reopen(state,monkeypatch,committed):
    ledger,clock,p,path=state; commit=ledger._commit
    def lost_return():
        if committed:commit()
        raise OSError('injected lost commit return')
    monkeypatch.setattr(ledger,'_commit',lost_return)
    with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
    assert ledger.pending.exists()
    snapshot=ledger.snapshot();assert snapshot['allocations']==int(committed) and snapshot['uncertain']
    assert ledger.close() is False
    reopened=ParentCaps.open(path,p,clock=clock,boot_reader=lambda:BOOT)
    try:
        assert reopened.snapshot()==snapshot
        with pytest.raises(ParentCapsError):reopened.prepay_pair(packages(p))
        assert reopened.snapshot()==snapshot
    finally:reopened.close()


@pytest.mark.parametrize('failure',['close','exception','reentry'])
def test_last_clock_callback_after_payment_keeps_durable_unknown_fence(state,failure):
    ledger,clock,p,path=state; target=clock.calls+3
    def final_callback(count):
        if count!=target:return
        if failure=='close':assert ledger.close() is False
        elif failure=='exception':raise RuntimeError('lost final observation')
        else:
            with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
    clock.hook=final_callback
    with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
    clock.hook=None;assert ledger.pending.exists()
    ledger.close()
    reopened=ParentCaps.open(path,p,clock=clock,boot_reader=lambda:BOOT)
    try:
        snap=reopened.snapshot();assert snap['allocations']==1 and snap['uncertain']
        assert snap['per_unit']['unit-a']['ip_frames']==p.units[0].prior_debt.ip_frames+2048
        with pytest.raises(ParentCapsError):reopened.prepay_pair(packages(p))
    finally:reopened.close()


def test_child_expiry_refuses_handoff_while_original_parent_still_live(state):
    ledger,clock,p,path=state;permit=ledger.prepay_pair(packages(p));before=ledger.snapshot()
    clock.epoch=180000000;clock.mono=130000000
    assert clock.epoch<p.expires_epoch_us and clock.mono<p.deadline_monotonic_us
    with pytest.raises(ParentCapsError):ledger.consume(permit)
    assert ledger.snapshot()['aggregate']==before['aggregate']


def test_final_consume_clock_expiry_refuses_handoff_and_retains_payment_and_used_permit(state):
    ledger,clock,p,path=state;permit=ledger.prepay_pair(packages(p));before=ledger.snapshot()
    target=clock.calls+2
    def expire_on_final_observation(count):
        if count==target:clock.epoch,clock.mono=180000000,130000000
    clock.hook=expire_on_final_observation
    with pytest.raises(ParentCapsError):ledger.consume(permit)
    clock.hook=None
    assert clock.epoch<p.expires_epoch_us and clock.mono<p.deadline_monotonic_us
    assert permit.used and ledger._permits[id(permit)][3] is True
    assert ledger.snapshot()['aggregate']==before['aggregate']
    permit.used=False
    with pytest.raises(ParentCapsError):ledger.consume(permit)


def test_changed_or_reused_child_identity_cannot_buy_back_an_allocation(state):
    ledger,clock,p,path=state;pair=packages(p);permit=ledger.prepay_pair(pair);before=ledger.snapshot()
    changed=deepcopy(pair)
    for package in changed:
        package['grant']['limits']['max_frames']=2049
    with pytest.raises(ParentCapsError):ledger.prepay_pair(changed)
    assert ledger.snapshot()['aggregate']==before['aggregate']
    ledger.close()
    reopened=ParentCaps.open(path,p,clock=clock,boot_reader=lambda:BOOT)
    try:
        with pytest.raises(ParentCapsError):reopened.prepay_pair(pair)
        assert reopened.snapshot()['aggregate']==before['aggregate']
    finally:reopened.close()


@pytest.mark.parametrize('change',['epoch_expiry','mono_expiry','epoch_backwards','mono_backwards','boot'])
def test_original_time_boot_and_persisted_observations_cannot_be_reset(state,change):
    ledger,clock,p,path=state;ledger.prepay_pair(packages(p));before=ledger.snapshot()
    if change=='epoch_expiry':clock.epoch=p.expires_epoch_us
    elif change=='mono_expiry':clock.mono=p.deadline_monotonic_us
    elif change=='epoch_backwards':clock.epoch-=1
    elif change=='mono_backwards':clock.mono-=1
    else:clock.boot=OTHER_BOOT
    with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
    assert ledger.snapshot()['aggregate']==before['aggregate']


def test_lifetime_lock_replacement_and_foreign_descriptor_are_refused_without_foreign_close(state):
    ledger,clock,p,path=state
    with pytest.raises(ParentCapsError):ParentCaps.open(path,p,clock=clock,boot_reader=lambda:BOOT)
    fd=ledger._fds['lock'];os.close(fd)
    foreign=path.parent/'foreign';foreign.write_bytes(b'foreign');foreign.chmod(0o600)
    replacement=os.open(foreign,os.O_RDWR)
    if replacement!=fd:os.dup2(replacement,fd);os.close(replacement)
    try:
        with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
        assert ledger.close() is False
        assert os.pread(fd,7,0)==b'foreign'
    finally:os.close(fd)


def test_database_name_replacement_refuses_before_payment(state):
    ledger,clock,p,path=state
    original=path.with_name('original.sqlite');path.rename(original)
    path.write_bytes(b'foreign');path.chmod(0o600)
    with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
    assert path.read_bytes()==b'foreign'


def test_wrong_thread_refuses_without_creating_allocation(state):
    ledger,clock,p,path=state;errors=[]
    def worker():
        try:ledger.prepay_pair(packages(p))
        except ParentCapsError:errors.append(True)
        assert ledger.close() is False
    thread=threading.Thread(target=worker);thread.start();thread.join(2)
    assert not thread.is_alive() and errors==[True]
    assert not ledger._closed
    assert ledger.snapshot()['allocations']==0


def test_caught_reentry_cannot_rollback_outer_committed_pair(state,monkeypatch):
    ledger,clock,p,path=state;commit=ledger._commit;pair=packages(p);observed=[]
    def reenter_before_commit():
        assert ledger.db.in_transaction
        with pytest.raises(ParentCapsError):ledger.prepay_pair(pair)
        observed.append(ledger.db.in_transaction)
        commit()
    monkeypatch.setattr(ledger,'_commit',reenter_before_commit)
    with pytest.raises(ParentCapsError):ledger.prepay_pair(pair)
    assert observed==[True]
    assert ledger.snapshot()['allocations']==1 and ledger.pending.exists()


@pytest.mark.parametrize('field',['path','_clock','_boot','_fds','db'])
def test_replacing_owner_refs_refuses_before_payment(state,field):
    ledger,clock,p,path=state;original=getattr(ledger,field);replacement=None
    if field=='path':replacement=path.with_name('different.sqlite')
    elif field=='_clock':replacement=lambda:(clock.epoch,clock.mono)
    elif field=='_boot':replacement=lambda:BOOT
    elif field=='_fds':replacement=dict(ledger._fds)
    else:replacement=sqlite3.connect(':memory:')
    setattr(ledger,field,replacement)
    try:
        with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
    finally:
        setattr(ledger,field,original)
        if field=='db':replacement.close()
    assert ledger.snapshot()['allocations']==0


def test_prior_cost_and_digest_are_mandatory_explicit_inputs():
    p=policy()
    with pytest.raises(ParentCapsError):replace(p.units[0],prior_snapshot_sha256=None)
    with pytest.raises(ParentCapsError):replace(p.units[0],prior_debt=None)
    with pytest.raises(ParentCapsError):replace(p,prior_snapshot_sha256=None)
    with pytest.raises(ParentCapsError):Cost(ip_frames=True)
    with pytest.raises(ParentCapsError):Cost(ip_frames=2**63-1).plus(Cost(ip_frames=1))


def test_policy_json_decode_preserves_exact_binding_and_refuses_missing_prior():
    p=policy();value=json.loads(canonical(p.to_dict()))
    assert ParentPolicy.from_dict(value)==p
    assert ParentPolicy.from_dict(value).digest==p.digest
    del value['units'][0]['prior_snapshot_sha256']
    with pytest.raises(ParentCapsError):ParentPolicy.from_dict(value)


def test_relabelled_request_resource_is_refused_before_any_pair_payment(state):
    ledger,clock,p,path=state;pair=packages(p)
    for package in pair:package['resource_sha256']='f'*64
    with pytest.raises(ParentCapsError):ledger.prepay_pair(pair)
    assert ledger.snapshot()['allocations']==0 and not ledger.pending.exists()


def test_current_observation_writes_no_allocation_and_fences_regression_before_prepay(state):
    ledger,clock,p,path=state;changes=ledger.db.total_changes
    clock.epoch+=1000000;clock.mono+=1000000
    assert ledger.current() is True and ledger.db.total_changes==changes
    clock.epoch-=1
    with pytest.raises(ParentCapsError):ledger.prepay_pair(packages(p))
    assert ledger.snapshot()['allocations']==0


def test_current_callback_caught_reentry_cannot_publish_current_parent(state):
    ledger,clock,p,path=state;target=clock.calls+1
    def nested(count):
        if count==target:
            with pytest.raises(ParentCapsError):ledger.current()
    clock.hook=nested
    with pytest.raises(ParentCapsError):ledger.current()
    assert ledger.snapshot()['allocations']==0
