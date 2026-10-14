"""Real signed records/root grants/PTY ownership; backend and native facts modeled."""
import base64
from collections import Counter
from contextlib import contextmanager
import copy
from dataclasses import asdict, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

import pytest

from meshticulum.bench.authority import canonical
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.wire import BenchGrant
from meshticulum.contracts.identity import PeerID, SessionKey, ServiceKind
from lantern_meshticulum.bench.profiles import profile_for
from lantern_meshticulum.sage.forward_budget import ChildPlan, Cost
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner, ForwardNativeOwnerError
from lantern_meshticulum.sage.forward_native_provider import ForwardNativeProvider, SETUP_SCOPES
from lantern_meshticulum.sage import radio_native_adoption as radio
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption, SCOPE
from lantern_meshticulum.sage.mesh_inbox import _canonical
from lantern_meshticulum.sage.noise_purpose import SIGNATURE_DOMAIN as PURPOSE_DOMAIN
from lantern_meshticulum.sage.noise_native_enrollment import SIGNATURE_DOMAIN as ENROLL_DOMAIN
from tests.integrations.sage.test_forward_native_provider import fixture, DEBT
from tests.integrations.sage.test_radio_native_adoption import enroll_fixture


@contextmanager
def prepared(tmp_path):
    """An actually prepaid, registered opening child; no worker/ARM/transport."""
    with fixture(scopes=SETUP_SCOPES+(SCOPE,)) as (r,p,source,bind):
        bind(scopes=SETUP_SCOPES+(SCOPE,));op=p.start(r.packet);o=p.owner
        h=r.hardware;h._forward_radio=h._coordinator=h._radio_observer=None;h._initializing=False
        packet=op.packet();profile=profile_for('sf7-500')
        peer=lambda p:dict(chain_id=p.chain_id,agent_id=p.public_key.hex())
        plan=dict(schema='meshticulum.service-run.v1',unit='unit-a',host_boot_id=r.state.boot,
            local_peer=peer(o.local),initiator=peer(o.context.initiator),responder=peer(o.context.responder),
            session_id=uuid.uuid4().hex,generation=uuid.uuid4().hex,issued_epoch_us=o.context.issued_epoch_us,
            expires_epoch_us=o.context.expires_epoch_us,profile=asdict(profile),
            object=dict(bytes=len(packet),sha256=hashlib.sha256(packet).hexdigest(),chunk_index=0,object_id=o.context.digest.hex()),
            sender_unit='unit-a',country='MY',antenna_confirmed=True,max_frames=64,
            application_deadline_monotonic_us=o.intention.application_deadline_monotonic_us-1000000,
            service=dict(kind=ServiceKind.MESSAGE.value,format='MPF2.record',application_expires_epoch_us=o.context.expires_epoch_us))
        route={k:plan[k] for k in ('initiator','responder','session_id','generation','profile','object','sender_unit','country','service')}
        grant=BenchGrant(SessionKey(PeerID(**plan['initiator']),PeerID(**plan['responder']),
            bytes.fromhex(plan['session_id']),bytes.fromhex(plan['generation'])),'bench_transfer',
            hashlib.sha256(canonical(route)).digest(),plan['issued_epoch_us'],plan['expires_epoch_us'],208,64,b's'*32)
        child=ChildPlan(0,1,True,grant.digest.hex(),plan['object']['sha256'],len(packet),'lora',
            hashlib.sha256(canonical(asdict(profile))).hexdigest(),r.state.boot,o.context.expires_epoch_us,
            plan['application_deadline_monotonic_us'],Cost(256,60000000),64)
        directory=tmp_path/'grant';directory.mkdir(mode=0o700)
        for name,raw in (('grant.json',_canonical(plan)),('secret.bin',b's'*32),('active',b'active\n')):
            fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as out:out.write(raw)
        master,slave=os.openpty();lock=os.open(tmp_path/'lock',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        fcntl.flock(slave,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        wire=ExclusiveSerial.__new__(ExclusiveSerial);wire.fd,wire.lock_fd=slave,lock
        original=Path.read_text
        def kernel(path,*args,**kwargs):
            if str(path)!='/proc/locks':return original(path,*args,**kwargs)
            rows=[]
            for index,fd in enumerate((slave,lock)):
                try:info=os.fstat(fd)
                except OSError:continue
                rows.append(f'{index}: FLOCK ADVISORY WRITE {os.getpid()} {os.major(info.st_dev):x}:{os.minor(info.st_dev):x}:{info.st_ino} 0 EOF')
            return '\n'.join(rows)
        a=None
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path,'read_text',kernel)
            try:
                r.owner,r.op,r.child,r.packet,r.directory,r.transport=o,op,child,packet,directory,wire
                r.provider,r.source=p,source
                a=NativeRadioAdoption(o,child,directory,wire);enroll_fixture(r,a)
                payment=op.budget.pre_admit(child);op.budget.check_pending(payment)
                def opened():
                    with o.owner_lease():
                        o._registry.begin(o,child,contain=lambda *_:False,terminal=lambda *_:False)
                        assert h.adopt_forward_radio(a) is a
                op._run(opened)
                r.adoption=a;r.payment=payment;r.rows=copy.deepcopy(r.state.rows)
                r.fds=tuple((fd,os.fstat(fd).st_ino) for fd in (slave,lock))
                yield r
            finally:
                if a is not None:a.release()
                wire.close();os.close(master)


def retained(r):
    row=r.state.rows[r.op.budget.record_id]
    assert row==r.rows[r.op.budget.record_id]
    assert row['payload']['body']['allocations']==[r.child.to_dict()]
    assert r.op.budget.prior==DEBT and r.provider._retained_debt==DEBT and not r.payment.bound
    assert json.loads(r.provider._accounting_binding)['actual_usage'] is None
    assert r.child.cost==Cost(256,60000000) and r.op.position==0 and r.op.active is None
    assert r.adoption._read_run is None and r.owner._radio_read is None and not r.owner._busy
    assert all(os.fstat(fd).st_ino==inode for fd,inode in r.fds)


def test_two_full_key_observations_and_six_fresh_accounting_fences_per_read(tmp_path,record_property):
    with prepared(tmp_path) as r:
        requests=[];r.client._client.event_hooks['response'].append(lambda response:requests.append(response.request.url.path))
        metadata=[]
        r.source.hook=lambda:metadata.append(True) if not r.provider._purpose._observing else None
        before=r.source.observations
        assert not r.owner._held and not r.hardware._mutex.locked()
        assert r.adoption.current() is True
        first=Counter(requests);first_observations=r.source.observations-before
        record_property('requests_per_observation',sum(first.values()))
        record_property('storage_statuses_per_observation',first['/v1/messages/storage'])
        record_property('accounting_observations_per_observation',first_observations)
        assert r.adoption.current() is True
        assert Counter(requests)==first+first and r.source.observations-before==2*first_observations
        assert first['/v1/workflows/'+r.adoption.record_id]==1
        assert first['/v1/workflows/'+r.provider._purpose.record_id]==2
        assert first['/v1/workflows/'+r.provider._enrollment.record_id]==2
        assert first_observations==16 and len(metadata)==12
        assert r.state.rows==r.rows and not r.hardware._mutex.locked()
        retained(r)


@pytest.mark.parametrize('position',range(1,7))
def test_each_original_workflow_callback_freshly_checks_held_native_accounting(tmp_path,position):
    with prepared(tmp_path) as r:
        seen=[];highest=r.adoption._highest;record=r.adoption._record
        def changed():
            if not r.provider._purpose._observing:
                seen.append(True)
                if len(seen)==position:r.source.evidence='f'*64
        r.source.hook=changed
        assert r.adoption.current() is False and len(seen)==position
        assert r.adoption._highest==highest and r.adoption._record==record
        assert r.adoption._uncertain and r.owner._uncertain
        retained(r)


@pytest.mark.parametrize('change',['purpose-revoke','enrollment-revoke','adoption-revoke','scope','epoch',
    'deadline','boot','provider-close','source','unknown-to-zero','debt','owner-context','store','method'])
def test_callback_drift_is_refused_before_read_can_return_admitted(tmp_path,monkeypatch,change):
    with prepared(tmp_path) as r:
        seen=[]
        def change_at_row(response):
            if response.request.url.path!='/v1/workflows/'+r.adoption.record_id or seen:return
            seen.append(True)
            if change in ('purpose-revoke','enrollment-revoke','adoption-revoke'):
                target={'purpose-revoke':r.provider._purpose,'enrollment-revoke':r.provider._enrollment,
                    'adoption-revoke':r.adoption}[change]
                row=r.state.rows[target.record_id];body=row['payload']['body']
                row['revision']+=1;body['revision']=row['revision'];body['state']='revoked'
                domain={'purpose-revoke':PURPOSE_DOMAIN,'enrollment-revoke':ENROLL_DOMAIN,'adoption-revoke':radio.DOMAIN}[change]
                row['payload']['signature']=base64.b64encode(r.hardware.identity._signing_key.sign(domain+_canonical(body)).signature).decode()
                if change=='adoption-revoke':response._content=json.dumps(row).encode()
            elif change=='scope':
                with r.authority._mutex:
                    grant=r.authority._grants[id(r.owner.native_context)]
                    grant['operations']=grant['operations']-{SCOPE}
            elif change=='epoch':r.state.instance_id='c'*64
            elif change=='deadline':r.state.now=(r.state.now[0],r.child.application_deadline_monotonic_us)
            elif change=='boot':r.state.boot=str(uuid.uuid4())
            elif change=='provider-close':assert r.provider.close() is False
            elif change=='source':r.source.evidence='f'*64
            elif change=='unknown-to-zero':r.source.actual=Cost()
            elif change=='debt':r.source.debt=Cost(9999,60000000)
            elif change=='owner-context':r.owner.context=replace(r.owner.context)
            elif change=='store':r.adoption.store.client=object()
            else:monkeypatch.setattr(ForwardNativeProvider,'_radio_native_check',lambda *_:True)
        r.client._client.event_hooks['response'].append(change_at_row)
        assert r.adoption.current() is False and seen==[True] and r.adoption._uncertain
        assert r.adoption.current() is False
        retained(r)


@pytest.mark.parametrize('nested',['read','access','crypto','dispatch','adopt','provider','release','close','drain'])
def test_caught_reentry_or_close_cannot_admit_or_release_held_fds(tmp_path,nested):
    with prepared(tmp_path) as r:
        seen=[]
        def inside(response):
            if response.request.url.path!='/v1/workflows/'+r.adoption.record_id or seen:return
            seen.append(True);o=r.owner
            assert o._busy and o._radio_read is r.adoption._read_run
            if nested=='read':assert r.adoption.current() is False
            elif nested=='release':assert r.adoption.release() is False
            elif nested=='close':assert o.close() is False
            elif nested=='drain':assert o._registry.close(timeout=0) is False
            else:
                call={'access':o.access_check,'dispatch':lambda:o.dispatch_check(o.context,o.intention),
                    'crypto':lambda:o.crypto_action_check(o.context,o.intention,o.remote,
                        o.context.initiator_noise_public_key,o.context.digest),
                    'adopt':lambda:r.adoption.adopt(r.hardware),
                    'provider':r.provider.generate}[nested]
                with pytest.raises(Exception):call()
            assert all(os.fstat(fd).st_ino==inode for fd,inode in r.fds)
        r.client._client.event_hooks['response'].append(inside)
        assert r.adoption.current() is False and seen==[True] and r.adoption._uncertain
        retained(r)


def test_arbitrary_native_callback_is_not_an_opt_in_permission(tmp_path):
    from tests.integrations.sage.test_forward_native_owner import setup
    with setup() as r:
        with pytest.raises(ForwardNativeOwnerError):
            ForwardNativeOwner(r.hardware,r.authority,r.native_context,context=r.context,intention=r.intention,
                local_initiator=True,key_check=lambda *_:True,peer_key_check=lambda *_:True,
                boot_reader=lambda:r.state.boot,clock_reader=lambda:r.state.now,radio_native_check=lambda *_:True)
        assert not r.state.rows
