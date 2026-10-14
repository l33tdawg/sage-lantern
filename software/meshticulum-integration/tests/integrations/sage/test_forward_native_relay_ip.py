# SPDX-License-Identifier: Apache-2.0
"""Real localhost TCP/crypto/journals; native identity/CAS and radio are models.

No external connection, physical radio qualification, endpoint key custody at A,
private inbox admission or native production enablement is established here.
"""
import base64
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import threading
import time
from types import SimpleNamespace
import uuid

import pytest
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.state import SessionStore as RadioStore
from meshticulum.bench.wire import SessionCipher, FrameKind
from meshticulum.contracts.identity import PeerID
from meshticulum.ip.authority import IpAuthority, revoke
from meshticulum.ip.flow import IpFlow
from meshticulum.ip.journal import IpJournal
from meshticulum.ip.state import SessionStore
from meshticulum.ip.worker import TcpWorker
from meshticulum.ip.wire import READY_MODE
from meshticulum.privacy.forward_relay import RelayStage
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_native_relay import RELAY_SCOPES
from lantern_meshticulum.sage.forward_native_relay_ip import (
    RelayIpAdoption, RelayIpError, NativeRelayIpFactory, plan_ip_hop, SCHEMA, DOMAIN, SCOPE)
from lantern_meshticulum.sage.forward_tcp import ForwardTcpStage
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_forward_native_relay import relay_rig, ModeledFactory, DEBT, LIMITS
from tests.integrations.sage.test_forward_radio_composition import shared_process_peer_scheduling
from tests.test_meshticulum_bench_runtime import Clock, ModelWorker, BOOT
from tests.test_meshticulum_private_carrier import CONTENT, open_private
from tests.radio_profiles import SOURCE_ADMISSION


def unused_address():
    with socket.socket() as value:
        value.bind(('127.0.0.1',0)); return value.getsockname()


@contextmanager
def timed_rig(path, monkeypatch, *, scopes=RELAY_SCOPES+(SCOPE,), limit=LIMITS):
    # Core TCP dispatch uses the OS monotonic clock. Keep the existing public
    # crypto fixture epoch and its original 100s lifetime, on a coherent real
    # monotonic base; never renew a clock after native approval.
    start = time.monotonic_ns()//1000; original = Clock.__call__
    monkeypatch.setattr(Clock,'mono',start)
    def now(clock):
        epoch,mono = original(clock); elapsed = time.monotonic_ns()//1000-start
        return epoch+elapsed,mono+elapsed
    def setup(r):
        r.hardware._initializing = False
        r.hardware._forward_radio = r.hardware._radio_observer = r.hardware._forward_relay_ip = None
    with relay_rig(path,scopes=scopes,limit=limit,before_create=setup) as r:
        # The original fixture samples both clocks separately during approval.
        # Keep those initial samples identical, then advance from the original
        # start (including setup time); no parent deadline is changed.
        monkeypatch.setattr(Clock,'__call__',now)
        r.ip_factories = []; r.remotes = []; r.threads = []
        try: yield r
        finally:
            r.owner.fence(); r.hardware._forward_children.close(4)
            for t in r.threads: t.join(2)
            for remote in r.remotes: close_remote(remote)
            # Explicit fixture-owner emergency teardown is separate from the
            # native terminal proof when a negative control stays uncertain.
            for f in r.ip_factories:
                if f.adoption is not None:
                    try: f.adoption.release()
                    except Exception: pass
                for sock,_ in f._sockets:
                    if sock.fileno()>=0: sock.close()
                for h in (f.flow,f.store,f.journal,f.authority):
                    if h is not None:
                        try: h.close()
                        except Exception: pass


def native_plan(r, packet, kind=1, addresses=None, *, startup_mode=None):
    sends = kind%2 == 1; b = PeerID(r.context.responder.chain_id,r.context.responder.public_key.hex())
    first,last = (r.local,b) if sends else (b,r.local)
    if addresses is None: addresses = ('127.0.0.1',10001),('127.0.0.1',10002)
    plan=dict(schema='meshticulum.ip-service-run.v1',host_boot_id=BOOT,local_peer=asdict(r.local),
        session=dict(initiator=asdict(first),responder=asdict(last),session_id=uuid.uuid4().hex,generation=uuid.uuid4().hex),
        format='MPF2.record',object=dict(bytes=len(packet),sha256=hashlib.sha256(packet).hexdigest()),
        route_binding_digest=r.context.digest.hex(),issued_epoch_us=r.context.issued_epoch_us,
        expires_epoch_us=r.context.expires_epoch_us,application_expires_epoch_us=r.context.expires_epoch_us,
        application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us-2000000,
        initiator_address=list(addresses[0]),responder_address=list(addresses[1]),
        limits=dict(max_plaintext=256,max_frames=64,max_tx_stream_bytes=65536,max_rx_stream_bytes=65536,
                    max_object_bytes=4096,window_frames=4,max_retries=3))
    if startup_mode is not None:
        plan.update(schema='meshticulum.ip-service-run.v2',startup_mode=startup_mode)
    return plan


def files(path, plan, secret=b'i'*32):
    path.mkdir(mode=0o700)
    for name,raw in (('grant.json',_canonical(plan)),('secret.bin',secret),('active',b'active\n')):
        fd=os.open(path/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(raw)


def enrollment(r, f, *, revision=1, state='active', change=None):
    a = f.adoption or RelayIpAdoption(r.owner,f.profile,f.directory)
    try:
        body=dict(record_id=a.record_id,revision=revision,binding=json.loads(a._binding),state=state)
        if change: change(body)
        sig=r.hardware.identity._signing_key.sign(DOMAIN+_canonical(body)).signature
        a.store.put(a.record_id,'mesh_outbound',revision-1,
            dict(schema=SCHEMA,body=body,signature=base64.b64encode(sig).decode()),access_check=r.owner.access_check)
        return a.record_id
    finally:
        if a is not f.adoption: a.release()


def factory(r, packet, kind=1, *, enroll=True, addresses=None, ready=None, startup_mode=None):
    if addresses is None:
        addresses=unused_address(),unused_address()
        while addresses[0] == addresses[1]: addresses=unused_address(),unused_address()
    p=native_plan(r,packet,kind,addresses,startup_mode=startup_mode)
    profile=plan_ip_hop(r.owner,p,b'i'*32,slot=(kind-1)*2+int(kind%2==1),kind=kind,sends=kind%2==1)
    d=r.path/('ip-'+str(kind)); files(d,p)
    f=NativeRelayIpFactory(r.owner,profile,d,ready=ready); r.ip_factories.append(f)
    if enroll: enrollment(r,f)
    return f


class MixedRadioFactory(ModeledFactory):
    """Existing real carrier/crypto with modeled RF, paired with TCP costs."""
    def open(self, owner, child):
        assert not self.opened and owner._held and child.plan is self.plan
        row=self.r.state.rows[owner._budget.record_id]['payload']['body']
        assert row['allocations'][-2:] == [c.plan.to_dict() for c in owner._active[1:]]
        if self.before_open: self.before_open(owner,child)
        assert owner.access_check() is True; self.opened=True
        for index,(directory,plan) in enumerate(zip(self.directories,self.plans)):
            j=AttemptJournal(directory/'attempts.sqlite',binding=bytes([self.plan.slot+1])*32,
                max_attempts=256,max_airtime_us=60000000,create=True,request_clock=lambda:self.r.clock()[0])
            w=ModelWorker(j,self.r.clock)
            a=RunAuthority(directory,expected_unit=plan['unit'],host_boot_id=BOOT,
                clock=self.r.clock,owner_probe=lambda w=w:w.owned,radio_admission=SOURCE_ADMISSION)
            s=RadioStore(directory/'wire.sqlite',a.grant,a.local_peer,boot_id=BOOT,clock=self.r.clock,create=True)
            flow=BenchFlow(authority=a,worker=w,cipher=SessionCipher(a.grant,a.local_peer,s),journal=j,
                flow_key=a.flow_key,contract=a.contract,profile=a.carrier_profile,
                ledger=Ledger(BudgetPolicy(180000000,60000000,120000000,1000000,quantization_us=1),a.ledger_epoch,self.r.clock()[1]),
                scheduler_epoch=a.scheduler_epoch,broker_epoch=a.broker_epoch,sends=index==0,clock=self.r.clock)
            if index==self.local_index:
                transmit=w.transmit
                def held(packet,transmit=transmit):
                    assert owner._held and owner.resource._mutex.locked(); assert owner.access_check()
                    return transmit(packet)
                w.transmit=held
            self.handles.append(SimpleNamespace(journal=j,worker=w,authority=a,store=s,flow=flow,transfer=ServiceTransfer(flow)))
        self.handles[0].worker.peer,self.handles[1].worker.peer=self.handles[1].worker,self.handles[0].worker
        self.local,self.remote=self.handles[self.local_index],self.handles[1-self.local_index]
        if self.after_open:self.after_open(owner,child)
        return self.local.transfer


def remote_connector(r,f, *, listener_ready=None, wrong_source=False):
    """A real independent B socket owner; only literal 127.0.0.1 is used."""
    result=queue.Queue(); listening=threading.Event()
    def actor():
        listener=connected=None; a=j=s=w=None
        try:
            p=json.loads(f.profile.plan_bytes); p['local_peer']=p['session']['responder' if f.profile.child.sends else 'initiator']
            assert all(p[name][0]=='127.0.0.1' for name in ('initiator_address','responder_address'))
            d=r.path/('remote-ip-'+str(f.profile.child.kind)); files(d,p)
            a=IpAuthority(d,local_peer=PeerID(**p['local_peer']),host_boot_id=BOOT,clock=r.clock,owner_probe=lambda:True)
            j=IpJournal(d/'bytes.sqlite',a.grant,create=True)
            if f.profile.child.sends:
                listener=socket.socket();listener.settimeout(8);listener.bind(tuple(p['responder_address']));listener.listen(1)
                listening.set();connected,peer=listener.accept();assert peer==tuple(p['initiator_address'])
                listener.close();listener=None
            else:
                assert listener_ready is not None and listener_ready.wait(8)
                connected=socket.socket();connected.settimeout(8)
                connected.bind(('127.0.0.1',0) if wrong_source else tuple(p['initiator_address']))
                connected.connect(tuple(p['responder_address']))
            if wrong_source:
                connected.close();connected=None;a.close();j.close();result.put(None);return
            w=TcpWorker(connected,a.grant,a.local_peer,j);connected=None
            s=SessionStore(d/'wire.sqlite',a.grant,a.local_peer,boot_id=BOOT,clock=r.clock,
                original_deadline_us=a.application_deadline_us,create=True)
            flow=IpFlow(authority=a,worker=w,store=s)
            q=SimpleNamespace(authority=a,journal=j,store=s,worker=w,flow=flow,transfer=ServiceTransfer(flow),directory=d)
            r.remotes.append(q);result.put(q)
        except BaseException as error:
            for h in (connected,listener,w,s,j,a):
                if h is not None:
                    try:h.close()
                    except Exception:pass
            result.put(error)
    t=threading.Thread(target=actor,daemon=True);r.threads.append(t);t.start()
    if f.profile.child.sends:assert listening.wait(8)
    return result,t


def close_remote(q):
    q.flow.close();q.worker.close()
    try:q.store.retire()
    except Exception:pass
    q.store.close();q.journal.close();q.authority.close()


def paid(r):
    return r.state.rows[r.owner._budget.record_id]['payload']['body']['allocations']


def prepare(r,packet,kind, *, ip=None, radio=None, startup_mode=None):
    listening=threading.Event()
    if ip is None:ip=factory(r,packet,kind,ready=lambda p:listening.set() or True,startup_mode=startup_mode)
    if radio is None:radio=MixedRadioFactory(r,packet,kind,not ip.profile.child.sends)
    result,t=remote_connector(r,ip,listener_ready=listening)
    incoming,outgoing=(radio,ip) if ip.profile.child.sends else (ip,radio)
    plan=lambda f:f.profile.child if f is ip else f.plan
    hop=lambda f:f.profile.hop if f is ip else f.hop
    stage=RelayStage(kind,hop(incoming),hop(outgoing),len(packet),hashlib.sha256(packet).digest())
    children=r.owner.prepare(stage,plan(incoming),plan(outgoing),incoming_factory=incoming,outgoing_factory=outgoing)
    q=result.get(timeout=8);t.join(2);assert not isinstance(q,BaseException),repr(q);assert not t.is_alive()
    assert paid(r)[-2:]==[plan(incoming).to_dict(),plan(outgoing).to_dict()]
    return ip,radio,q,children


def carry(r,packet,kind, *, drop=False, startup_mode=None):
    ip,radio,q,children=prepare(r,packet,kind,startup_mode=startup_mode)
    source,target=(radio.remote,q) if ip.profile.child.sends else (q,radio.remote)
    if drop:radio.handles[0].worker.drop_kind=FrameKind.DATA
    source.transfer.send(packet);end=time.monotonic()+15
    done=False
    while not done:
        assert time.monotonic()<end
        if not source.flow.flow.complete:source.flow.step()
        done=r.owner.step()
        if not target.flow.flow.complete:target.flow.step()
        elif type(target.flow) is IpFlow:target.flow.finish_receive_ack()
        if type(source.flow) is BenchFlow and source.flow.flow.complete:
            pass
        time.sleep(.001)
    assert target.transfer.received()==packet
    assert r.owner.finish_stage()['outgoing_final_ack_observed'] is True
    assert all(c.retired for c in children) and not ip._unknown
    terminal=ip.terminal(r.owner,next(c for c in children if c.factory is ip))
    assert terminal.radio_disarmed is None and terminal.resources_closed and not terminal.pending_unknown
    assert terminal.journal_snapshot['attempts']>0 and not terminal.journal_snapshot['fenced']
    assert terminal.journal_snapshot['peer_receipt_established'] is False
    assert CONTENT.encode() not in packet and not (ip.directory/'sender.bin').exists()
    return packet


@pytest.mark.parametrize('kind',range(1,6))
def test_five_ip_plans_bind_only_b_adjacent_direction(tmp_path,kind):
    with relay_rig(tmp_path) as r:
        p=native_plan(r,r.sessions[0].start(),kind);sends=kind%2==1
        v=plan_ip_hop(r.owner,p,b'i'*32,slot=(kind-1)*2+int(sends),kind=kind,sends=sends)
        assert v.child.carrier=='tcp' and v.child.cost==Cost(ip_frames=64,tx_stream_bytes=65536,rx_stream_bytes=65536)
        assert (v.hop.session.initiator if sends else v.hop.session.responder)==r.local
        assert v.grant.route_binding_digest==r.context.digest
        with pytest.raises(Exception):ForwardTcpStage(r.owner,v,tmp_path/'wrong',native_admission=lambda p:True)
        with pytest.raises(RelayIpError):plan_ip_hop(r.owner,p,b'i'*32,slot=(kind-1)*2+int(not sends),kind=kind,sends=not sends)
        assert not hasattr(r.owner,'_operation') and not hasattr(r.owner,'local_initiator')


@pytest.mark.parametrize('kind',range(1,6))
def test_explicit_ready_relay_profile_binds_original_direction_mode_and_caps(tmp_path,kind):
    with relay_rig(tmp_path) as r:
        p=native_plan(r,r.sessions[0].start(),kind);sends=kind%2==1
        legacy=plan_ip_hop(r.owner,p,b'i'*32,slot=(kind-1)*2+int(sends),kind=kind,sends=sends)
        p.update(schema='meshticulum.ip-service-run.v2',startup_mode=READY_MODE)
        ready=plan_ip_hop(r.owner,p,b'i'*32,slot=(kind-1)*2+int(sends),kind=kind,sends=sends)
        assert ready.grant.startup_mode==READY_MODE and legacy.grant.startup_mode is None
        assert ready.grant.digest!=legacy.grant.digest and ready.hop.grant_digest!=legacy.hop.grant_digest
        assert ready.child.cost==legacy.child.cost and ready.child.cipher_frame_limit==legacy.child.cipher_frame_limit
        assert ready.child.application_deadline_monotonic_us==legacy.child.application_deadline_monotonic_us
        assert (ready.grant.direction(r.local)==0) is sends


def test_signed_legacy_hop_binding_cannot_authorize_ready_plan(tmp_path,monkeypatch):
    with timed_rig(tmp_path,monkeypatch) as r:
        packet=r.sessions[0].start();ip=factory(r,packet,startup_mode=READY_MODE)
        p=json.loads(ip.profile.plan_bytes);p.pop('startup_mode');p['schema']='meshticulum.ip-service-run.v1'
        legacy=plan_ip_hop(r.owner,p,b'i'*32,slot=1,kind=1,sends=True)
        def change(body):body['binding']['hop']['grant_digest']=legacy.grant.digest.hex()
        enrollment(r,ip,revision=2,change=change)
        with pytest.raises(RelayIpError):
            adoption=RelayIpAdoption(r.owner,ip.profile,ip.directory)
            try:adoption.observe()
            finally:adoption.release()
        assert not ip._sockets and not (ip.directory/'bytes.sqlite').exists()
        assert r.owner._budget.record_id not in r.state.rows
        assert not hasattr(r.owner,'_operation') and not hasattr(r.owner,'local_initiator')


@pytest.mark.parametrize('damage',['endpoint-owner','c-peer','dns','route','expiry','deadline','caps','format','boot','local'])
def test_ip_plan_refuses_substitution_without_socket(tmp_path,monkeypatch,damage):
    with relay_rig(tmp_path) as r:
        p=native_plan(r,r.sessions[0].start())
        if damage=='endpoint-owner':p['session']['initiator']=asdict(PeerID(r.context.initiator.chain_id,r.context.initiator.public_key.hex()))
        elif damage=='c-peer':p['session']['responder']=asdict(PeerID(r.context.initiator.chain_id,r.context.initiator.public_key.hex()))
        elif damage=='dns':p['responder_address'][0]='localhost'
        elif damage=='route':p['route_binding_digest']='c'*64
        elif damage=='expiry':p['application_expires_epoch_us']+=1
        elif damage=='deadline':p['application_deadline_monotonic_us']+=3000000
        elif damage=='caps':p['limits']['max_frames']=0
        elif damage=='format':p['format']='MR1.request'
        elif damage=='boot':p['host_boot_id']=str(uuid.uuid4())
        else:p['local_peer']=p['session']['responder']
        monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('pure planner opened a socket'))
        with pytest.raises(RelayIpError):plan_ip_hop(r.owner,p,b'i'*32,slot=1,kind=1,sends=True)
        assert not r.state.rows


def test_actual_localhost_pair_is_prepaid_before_open_and_foreign_drain(tmp_path,monkeypatch):
    with timed_rig(tmp_path,monkeypatch) as r:
        packet=r.sessions[0].start();ip,radio,q,children=prepare(r,packet,1)
        assert r.hardware._forward_relay_ip is ip.adoption and ip.worker.owned()
        assert paid(r)==[c.plan.to_dict() for c in children] and ip.journal.snapshot()['attempts']==0
        before=paid(r)[:];values=[]
        t=threading.Thread(target=lambda:values.append(r.hardware._forward_children.close(4)))
        t.start();t.join(6)
        assert not t.is_alive() and values==[True]
        child=next(c for c in children if c.factory is ip)
        assert ip.terminal(r.owner,child).resources_closed and ip._directory_fd is None
        assert ip.journal.lock_fd is None and all(sock.fileno()==-1 for sock,_ in ip._sockets)
        assert r.hardware._forward_relay_ip is None and paid(r)==before
        assert not r.owner.snapshot()['native_qualified']


def test_five_stages_radio_model_and_real_tcp_keep_end_to_end_crypto(tmp_path,monkeypatch,shared_process_peer_scheduling):
    with timed_rig(tmp_path,monkeypatch) as r:
        i,b=r.sessions
        second=b.respond(carry(r,i.start(),1))
        i.finish(carry(r,second,2))
        fourth=b.accept_confirmation(carry(r,i.confirm(),3))
        i.verify_confirmation(carry(r,fourth,4))
        sealed=i.seal(r.inner);assert b.open(carry(r,sealed,5))==r.inner
        assert open_private(r.inner,r.clock).content==CONTENT
        assert len(paid(r))==10 and r.owner.snapshot()['completed_stages']==5
        assert not r.owner.snapshot()['native_qualified']
        assert all(f._retired and not f._unknown for f in r.ip_factories)
        assert len(list(tmp_path.glob('ip-*/bytes.sqlite')))==5
        assert not list(tmp_path.glob('**/sender.bin'))


def test_five_stages_explicit_sender_ready_radio_model_and_real_tcp(tmp_path,monkeypatch,shared_process_peer_scheduling):
    # Same helpers, original 100s parent, 15s carry and 8s connector bounds.
    with timed_rig(tmp_path,monkeypatch) as r:
        i,b=r.sessions
        def ready(packet,kind):return carry(r,packet,kind,startup_mode=READY_MODE)
        second=b.respond(ready(i.start(),1))
        i.finish(ready(second,2))
        fourth=b.accept_confirmation(ready(i.confirm(),3))
        i.verify_confirmation(ready(fourth,4))
        sealed=i.seal(r.inner);assert b.open(ready(sealed,5))==r.inner
        assert open_private(r.inner,r.clock).content==CONTENT
        assert len(paid(r))==10 and r.owner.snapshot()['completed_stages']==5
        assert not r.owner.snapshot()['native_qualified']
        assert all(f._retired and not f._unknown and f.profile.grant.startup_mode==READY_MODE for f in r.ip_factories)
        assert len(list(tmp_path.glob('ip-*/bytes.sqlite')))==5 and not list(tmp_path.glob('**/sender.bin'))


@pytest.mark.parametrize('damage',['scope','missing','signature','binding','active'])
def test_signed_ip_admission_refuses_before_socket_or_child_journal(tmp_path,monkeypatch,damage):
    with timed_rig(tmp_path,monkeypatch,scopes=RELAY_SCOPES if damage=='scope' else RELAY_SCOPES+(SCOPE,)) as r:
        packet=r.sessions[0].start()
        ip=factory(r,packet,enroll=damage not in ('scope','missing'))
        if damage in ('signature','binding'):
            record=next(row for row in r.state.rows.values() if row['payload'].get('schema')==SCHEMA)
            if damage=='signature':record['payload']['signature']=base64.b64encode(bytes(64)).decode()
            else:record['payload']['body']['binding']['exchange_approval']='f'*64
        if damage=='active':(ip.directory/'active').write_bytes(b'retired\n')
        radio=MixedRadioFactory(r,packet,1,False)
        stage=RelayStage(1,radio.hop,ip.profile.hop,len(packet),hashlib.sha256(packet).digest())
        monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('refused before socket'))
        with pytest.raises(Exception):r.owner.prepare(stage,radio.plan,ip.profile.child,incoming_factory=radio,outgoing_factory=ip)
        assert ip._sockets==[] and not (ip.directory/'bytes.sqlite').exists()


@pytest.mark.parametrize('occupied',['_provider','_coordinator','_initializing','_radio_observer','_forward_relay_ip','_forward_radio'])
def test_runtime_seam_refuses_other_owners(tmp_path,monkeypatch,occupied):
    with timed_rig(tmp_path,monkeypatch) as r:
        ip=factory(r,r.sessions[0].start());a=RelayIpAdoption(r.owner,ip.profile,ip.directory)
        try:
            setattr(r.hardware,occupied,True if occupied=='_initializing' else object())
            with pytest.raises(Exception):r.hardware.adopt_forward_relay_ip(a)
            assert not a._adopted and ip._sockets==[]
        finally:
            setattr(r.hardware,occupied,False if occupied=='_initializing' else None);a.release()


def test_direct_transfer_dispatch_has_no_unheld_native_permission(tmp_path,monkeypatch):
    with timed_rig(tmp_path,monkeypatch) as r:
        packet=r.sessions[0].start();ip,radio,q,children=prepare(r,packet,1)
        before=ip.journal.snapshot()
        with pytest.raises(Exception):ip.transfer.send(packet)
        assert ip.journal.snapshot()==before and not ip.flow.started
        assert r.hardware._forward_children.close(4)


@pytest.mark.parametrize('damage',['owner','boot','active','enrollment','source'])
def test_current_admission_is_fresh_before_any_ip_frame(tmp_path,monkeypatch,damage):
    with timed_rig(tmp_path,monkeypatch) as r:
        packet=r.sessions[0].start();ip,radio,q,children=prepare(r,packet,1)
        if damage=='owner':r.state.owner_live=False
        elif damage=='boot':r.state.boot=str(uuid.uuid4())
        elif damage=='active':(ip.directory/'active').write_bytes(b'retired\n')
        elif damage=='enrollment':enrollment(r,ip,revision=2,state='revoked')
        else:
            from lantern_meshticulum.sage import forward_native_relay_ip as module
            current=module.source_pins;monkeypatch.setattr(module,'source_pins',lambda:{**current(),'source':'changed'})
        with pytest.raises(Exception):r.owner.step()
        assert ip.journal.snapshot()['attempts']==0 and len(paid(r))==2
        # The source owner can close its current connection despite revocation;
        # no cleanup result is promoted to a completed exchange.
        r.owner.fence();closed=r.hardware._forward_children.close(4)
        if damage=='boot':
            # The enclosing native observer refuses a changed host boot before
            # entering a carrier callback. It cannot claim terminal resources;
            # fixture teardown owns the leftover test sockets explicitly.
            assert closed is False and any(sock.fileno()>=0 for sock,_ in ip._sockets)
            return
        assert all(sock.fileno()==-1 for sock,_ in ip._sockets)


def test_unknown_ip_reservation_cannot_be_retired_or_refunded(tmp_path,monkeypatch):
    with timed_rig(tmp_path,monkeypatch) as r:
        ip,radio,q,children=prepare(r,r.sessions[0].start(),1)
        token=ip.journal.reserve(b'unknown-test-reservation','DATA')
        ip.journal.settle(token,b'unknown-test-reservation','UNKNOWN')
        r.owner.fence();assert r.hardware._forward_children.close(4) is False
        assert ip._unknown and len(paid(r))==2
        with pytest.raises(Exception):ip.terminal(r.owner,next(c for c in children if c.factory is ip))
        assert all(sock.fileno()==-1 for sock,_ in ip._sockets) and ip.journal.closed
        assert r.owner.snapshot()['refunds']==0


def test_insufficient_ip_caps_precede_any_socket_or_journal_and_keep_prior_charge(tmp_path,monkeypatch):
    limit=Cost(LIMITS.radio_attempts,LIMITS.estimated_airtime_us,DEBT.ip_frames,DEBT.tx_stream_bytes,DEBT.rx_stream_bytes)
    with timed_rig(tmp_path,monkeypatch,limit=limit) as r:
        packet=r.sessions[0].start();ip=factory(r,packet);radio=MixedRadioFactory(r,packet,1,False)
        stage=RelayStage(1,radio.hop,ip.profile.hop,len(packet),hashlib.sha256(packet).digest())
        monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('budget refused before socket'))
        with pytest.raises(Exception):r.owner.prepare(stage,radio.plan,ip.profile.child,incoming_factory=radio,outgoing_factory=ip)
        assert paid(r)==[radio.plan.to_dict()] and not radio.opened
        assert not (ip.directory/'bytes.sqlite').exists() and ip._sockets==[]
        assert r.owner.snapshot()['refunds']==0


def test_refused_real_connection_contains_partial_handles_and_preserves_both_caps(tmp_path,monkeypatch):
    with timed_rig(tmp_path,monkeypatch) as r:
        packet=r.sessions[0].start();ip=factory(r,packet);radio=MixedRadioFactory(r,packet,1,False)
        stage=RelayStage(1,radio.hop,ip.profile.hop,len(packet),hashlib.sha256(packet).digest())
        # No peer owns this selected temporary port. Actual connect_ex/SO_ERROR
        # fails after prepayment and after the create-once started tombstone.
        with pytest.raises(Exception):r.owner.prepare(stage,radio.plan,ip.profile.child,incoming_factory=radio,outgoing_factory=ip)
        assert paid(r)==[radio.plan.to_dict(),ip.profile.child.to_dict()]
        assert (ip.directory/'started').is_file() and ip.journal.snapshot()['attempts']==0
        assert r.hardware._forward_children.close(4) is False
        assert ip._unknown and ip.journal.closed and ip.authority.closed
        assert all(sock.fileno()==-1 for sock,_ in ip._sockets)
        assert (ip.directory/'active').read_bytes()==b'retired\n' and r.owner.snapshot()['refunds']==0


@pytest.mark.parametrize('expired',[False,True])
def test_cleanup_detaches_stale_socket_and_preserves_reused_foreign_fd(tmp_path,monkeypatch,expired):
    with timed_rig(tmp_path,monkeypatch) as r:
        ip,radio,q,children=prepare(r,r.sessions[0].start(),1)
        old=ip.worker.sock.fileno();os.close(old)
        foreign=os.open(r.path/'foreign',os.O_CREAT|os.O_RDWR,0o600)
        if foreign!=old:os.dup2(foreign,old)
        expected=os.fstat(old)
        try:
            r.owner.fence()
            if expired:
                assert ip._cleanup(time.monotonic()-1) is False
            else:
                assert r.hardware._forward_children.close(4) is False
            actual=os.fstat(old)
            assert (actual.st_dev,actual.st_ino)==(expected.st_dev,expected.st_ino)
            assert ip.worker.sock.fileno()==-1 and ip._unknown and len(paid(r))==2
        finally:
            os.close(old)
            if foreign!=old:os.close(foreign)


def test_original_ip_expiry_cannot_be_renewed_and_cleanup_creates_no_new_work(tmp_path,monkeypatch):
    with timed_rig(tmp_path,monkeypatch) as r:
        ip,radio,q,children=prepare(r,r.sessions[0].start(),1)
        count=ip.journal.snapshot()['attempts'];before=paid(r)[:]
        r.clock.advance(101000000)
        with pytest.raises(Exception):r.owner.step()
        # Parent failure remains uncertain even after its actual handles close.
        r.owner.fence();assert r.hardware._forward_children.close(4) is False
        assert ip._retired and ip.journal.closed and paid(r)==before
        value=json.loads(ip._snapshot);assert value['attempts']==count==0
        assert ip.authority.application_deadline_us==ip.profile.child.application_deadline_monotonic_us


def test_unregistered_foreign_thread_cannot_contain_a_live_carrier(tmp_path,monkeypatch):
    with timed_rig(tmp_path,monkeypatch) as r:
        ip,radio,q,children=prepare(r,r.sessions[0].start(),1)
        child=next(c for c in children if c.factory is ip);errors=[]
        def foreign():
            try:ip.contain(r.owner,child,time.monotonic()+3)
            except Exception as e:errors.append(type(e))
        t=threading.Thread(target=foreign);t.start();t.join(3)
        assert not t.is_alive() and errors and ip.worker.owned() and not ip._retired
        assert r.hardware._forward_children.close(4)


def test_real_gfsk250_pty_and_tcp_compose_a_without_endpoint_keys(
        tmp_path,monkeypatch,shared_process_peer_scheduling):
    from lantern_meshticulum.sage.forward_native_relay_radio import SCOPE as RADIO_SCOPE
    from meshticulum.bench.serial_port import ExclusiveSerial
    from tests.integrations.sage.test_forward_native_relay_radio import prepared_rig, remote
    # Real OS monotonic base, selected before the original fixed-clock native
    # approval; the PTY fixture then advances from that same original base.
    monkeypatch.setattr(Clock,'mono',time.monotonic_ns()//1000)
    with prepared_rig(tmp_path,monkeypatch,scopes=RELAY_SCOPES+(RADIO_SCOPE,SCOPE),lease_ms=45000) as r:
        r.ip_factories=[];r.remotes=[];r.threads=[]
        ip=factory(r,r.packet);result,t=remote_connector(r,ip);sender=remote(r)
        stage=RelayStage(1,r.profile.hop,ip.profile.hop,len(r.packet),hashlib.sha256(r.packet).digest())
        r.wire.paid_at_arm=lambda:paid(r)==[r.profile.child.to_dict(),ip.profile.child.to_dict()] or pytest.fail('ARM before pair payment')
        try:
            children=r.owner.prepare(stage,r.profile.child,ip.profile.child,
                incoming_factory=r.factory,outgoing_factory=ip)
            target=result.get(timeout=8);t.join(2);assert not isinstance(target,BaseException),repr(target)
            assert r.hardware._forward_radio.owner is r.hardware._forward_relay_ip.owner is r.owner
            sender.transfer.send(r.packet);end=time.monotonic()+15;done=False
            while not done:
                assert time.monotonic()<end
                if not sender.flow.flow.complete:sender.flow.step()
                done=r.owner.step()
                if not target.flow.flow.complete:target.flow.step()
                else:target.flow.finish_receive_ack()
                time.sleep(.001)
            assert target.transfer.received()==r.packet
            assert r.sessions[1].respond(r.packet).startswith(b'MPF2')
            receipt=r.owner.finish_stage()
            assert receipt['outgoing_final_ack_observed'] and not receipt['native_qualified']
            assert all(c.retired for c in children) and len(paid(r))==2
            assert r.factory.observer.physical_disarmed is True and r.factory.transport.fd is None
            assert ip.journal.closed and all(sock.fileno()==-1 for sock,_ in ip._sockets)
            assert not hasattr(r.owner,'local_initiator') and CONTENT.encode() not in r.packet
        finally:
            r.owner.fence();r.hardware._forward_children.close(6)
            for thread in r.threads:thread.join(2)
            for q in r.remotes:close_remote(q)
            if ip.adoption is not None:ip.adoption.release()
            for sock,_ in ip._socket_refs:
                if sock.fileno()>=0:sock.close()
            for h in (ip.flow,ip.store,ip.journal,ip.authority):
                if h is not None:
                    try:h.close()
                    except Exception:pass
