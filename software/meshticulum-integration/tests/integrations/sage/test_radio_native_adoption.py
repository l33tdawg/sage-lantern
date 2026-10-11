"""Real owned PTY/lock FDs and signed SDK REST; radio/native facts are modeled."""
import base64
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import fcntl
import json
import os
import uuid
from pathlib import Path

import pytest
from meshticulum.bench.authority import canonical,RunAuthority
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.wire import BenchGrant
from meshticulum.contracts.identity import PeerID,SessionKey,ServiceKind
from lantern_meshticulum.bench.profiles import profile_for,RADIO_ADMISSION
from lantern_meshticulum.sage.forward_budget import ChildPlan,Cost
from lantern_meshticulum.sage.forward_native_owner import SCOPES
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption,SCHEMA,DOMAIN,SCOPE
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_forward_native_owner import setup


class OpenedControl(Exception):pass


@contextmanager
def model(tmp_path,*,scopes=SCOPES+(SCOPE,)):
    with setup(scopes=scopes) as r:
        h=r.hardware;h._forward_radio=h._coordinator=h._radio_observer=None;h._initializing=False
        owner=r.create();op=owner.operation(r.noise[0],limits=Cost(2000,300000000),prior_debt=Cost(),sealed_record=r.packet)
        packet=op.packet();radio=profile_for('sf7-500');peer=lambda p:dict(chain_id=p.chain_id,agent_id=p.public_key.hex())
        plan=dict(schema='meshticulum.service-run.v1',unit='unit-a',host_boot_id=r.state.boot,local_peer=peer(owner.local),
            initiator=peer(owner.context.initiator),responder=peer(owner.context.responder),session_id=uuid.uuid4().hex,
            generation=uuid.uuid4().hex,issued_epoch_us=owner.context.issued_epoch_us,expires_epoch_us=owner.context.expires_epoch_us,
            profile=asdict(radio),object=dict(bytes=len(packet),sha256=hashlib.sha256(packet).hexdigest(),chunk_index=0,object_id=owner.context.digest.hex()),
            sender_unit='unit-a',country='MY',antenna_confirmed=True,max_frames=64,
            application_deadline_monotonic_us=owner.intention.application_deadline_monotonic_us-1000000,
            service=dict(kind=ServiceKind.MESSAGE.value,format='MPF2.record',application_expires_epoch_us=owner.context.expires_epoch_us))
        route={k:plan[k] for k in ('initiator','responder','session_id','generation','profile','object','sender_unit','country','service')}
        grant=BenchGrant(SessionKey(PeerID(**plan['initiator']),PeerID(**plan['responder']),bytes.fromhex(plan['session_id']),bytes.fromhex(plan['generation'])),
            'bench_transfer',hashlib.sha256(canonical(route)).digest(),plan['issued_epoch_us'],plan['expires_epoch_us'],208,64,b's'*32)
        child=ChildPlan(0,1,True,grant.digest.hex(),plan['object']['sha256'],len(packet),'lora',
            hashlib.sha256(canonical(asdict(radio))).hexdigest(),r.state.boot,owner.context.expires_epoch_us,
            plan['application_deadline_monotonic_us'],Cost(256,60000000),64)
        directory=tmp_path/'grant';directory.mkdir(mode=0o700)
        for name,raw in (('grant.json',_canonical(plan)),('secret.bin',b's'*32),('active',b'active\n')):
            fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as out:out.write(raw)
        master,slave=os.openpty();lock=os.open(tmp_path/'lock',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        fcntl.flock(slave,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        transport=ExclusiveSerial.__new__(ExclusiveSerial) # Explicit source PTY model, no physical constructor.
        transport.fd,transport.lock_fd=slave,lock
        r.owner,r.op,r.child,r.packet,r.directory,r.transport=owner,op,child,packet,directory,transport
        r.master=master
        original=Path.read_text
        def kernel_metadata(path,*a,**kw):
            if str(path)!='/proc/locks':return original(path,*a,**kw)
            # macOS source fixture models Linux's kernel text only; FDs/locks are real.
            rows=[]
            for i,fd in enumerate((slave,lock)):
                try:info=os.fstat(fd)
                except OSError:continue
                rows.append(f'{i}: FLOCK ADVISORY WRITE {os.getpid()} {os.major(info.st_dev):x}:{os.minor(info.st_dev):x}:{info.st_ino} 0 EOF')
            return '\n'.join(rows)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path,'read_text',kernel_metadata)
            try:yield r
            finally:
                if h._forward_radio is not None:h._forward_radio.release()
                transport.close();os.close(master)


def enroll_fixture(r,adoption,*,state='active'):
    body=dict(record_id=adoption.record_id,revision=1,binding=json.loads(adoption._binding),state=state)
    sig=r.hardware.identity._signing_key.sign(DOMAIN+_canonical(body)).signature
    value=dict(schema=SCHEMA,body=body,signature=base64.b64encode(sig).decode())
    adoption.store.put(adoption.record_id,'mesh_outbound',0,value,access_check=r.owner.access_check)


def enter(r,adoption,callback):
    def opened(child):
        assert child is r.child
        with r.owner.owner_lease():
            callback(r.hardware.adopt_forward_radio(adoption))
        raise OpenedControl()
    with pytest.raises(Exception):
        r.owner.prepare_stage(r.child,open_child=opened,native_check=lambda *_:False,
            retire_child=lambda _:False,contain_child=lambda *_:adoption.release(),terminal_check=lambda *_:False,payload=r.packet)


def test_real_fd_adoption_is_after_exact_prepaid_registered_child(tmp_path):
    with model(tmp_path) as r:
        a=NativeRadioAdoption(r.owner,r.child,r.directory,r.transport);enroll_fixture(r,a);seen=[]
        def check(token):
            assert token is a and token.current() and r.hardware._forward_radio is a
            assert r.owner._registry._children[0].opening
            body=r.state.rows[r.op.budget.record_id]['payload']['body']
            assert body['allocations']==[r.child.to_dict()]
            authority=RunAuthority(r.directory,expected_unit='unit-a',host_boot_id=r.state.boot,
                clock=r.owner._clock,owner_probe=token.current,radio_admission=RADIO_ADMISSION)
            assert authority.service_format=='MPF2.record' and authority.service_kind is ServiceKind.MESSAGE
            assert authority.grant.digest.hex()==r.child.grant_digest
            assert authority.contract.route.profile_digest.hex()==r.child.profile_digest
            assert authority.application_deadline_us==r.child.application_deadline_monotonic_us
            authority.close()
            seen.append(True)
            with pytest.raises(Exception):r.hardware.provider(lambda:True)
            assert token.release() is True and token.report()['physical_disarmed'] is None
            assert token.release() is True
        enter(r,a,check)
        assert seen==[True] and r.transport.fd is r.transport.lock_fd is None
        assert a.report()['native_qualified'] is False


@pytest.mark.parametrize('missing',['scope','record','coordinator','provider','tracked','revoked','kernel-lock'])
def test_no_default_admission_or_meshcore_parallel_ownership(tmp_path,missing):
    with model(tmp_path,scopes=SCOPES if missing=='scope' else SCOPES+(SCOPE,)) as r:
        if missing=='scope':
            with pytest.raises(Exception):NativeRadioAdoption(r.owner,r.child,r.directory,r.transport)
            return
        a=NativeRadioAdoption(r.owner,r.child,r.directory,r.transport)
        if missing!='record':enroll_fixture(r,a,state='revoked' if missing=='revoked' else 'active')
        if missing in ('coordinator','provider'):setattr(r.hardware,'_'+missing,object())
        if missing=='kernel-lock':
            original=Path.read_text
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(Path,'read_text',lambda p,*a,**kw:'' if str(p)=='/proc/locks' else original(p,*a,**kw))
                observed=[];enter(r,a,lambda token:observed.append(token));assert not observed and not a._adopted
            a.release();return
        if missing=='tracked':
            with r.owner.owner_lease():
                with pytest.raises(Exception):r.hardware.adopt_forward_radio(a)
        else:
            observed=[];enter(r,a,lambda token:observed.append(token))
            assert observed==[]
        assert r.hardware._forward_radio is None and not a._adopted
        a.release()


@pytest.mark.parametrize('change',['permission','expiry','deadline','boot','active','source','child','callback','reuse','cancel','reentry'])
def test_mutation_revocation_and_fd_reuse_refuse_without_refund(tmp_path,change,monkeypatch):
    with model(tmp_path) as r:
        a=NativeRadioAdoption(r.owner,r.child,r.directory,r.transport);enroll_fixture(r,a);seen=[];foreign=[]
        def mutate(token):
            assert token.current();seen.append(True)
            if change=='permission':r.state.owner_live=False
            elif change=='expiry':r.state.now=(r.owner.context.expires_epoch_us,r.state.now[1])
            elif change=='deadline':r.state.now=(r.state.now[0],r.child.application_deadline_monotonic_us)
            elif change=='boot':r.state.boot=str(uuid.uuid4())
            elif change=='active':(r.directory/'active').write_bytes(b'retired\n')
            elif change=='source':
                import lantern_meshticulum.sage.radio_native_adoption as module
                monkeypatch.setattr(module,'source_pins',lambda:{})
            elif change=='child':object.__setattr__(r.child.cost,'radio_attempts',1)
            elif change=='callback':r.transport.read=lambda _:b''
            elif change=='cancel':r.op.close()
            elif change=='reentry':
                original=r.hardware.configured
                def configured():
                    assert token.current() is False
                    return original()
                r.hardware.configured=configured
            else:
                fd=r.transport.fd;other=os.open(tmp_path/'foreign',os.O_RDWR|os.O_CREAT,0o600)
                os.dup2(other,fd);os.close(other);foreign.append(fd)
            assert not token.current()
            token.release()
        enter(r,a,mutate)
        assert seen==[True]
        body=r.state.rows[r.op.budget.record_id]['payload']['body']
        assert body['allocations'][0]['cost']['radio_attempts']==256
        for fd in foreign:os.fstat(fd);os.close(fd)


def test_named_grant_replacement_and_binding_tamper_fail_before_adoption(tmp_path):
    with model(tmp_path) as r:
        a=NativeRadioAdoption(r.owner,r.child,r.directory,r.transport);enroll_fixture(r,a)
        path=r.directory/'active';path.rename(r.directory/'old-active');path.write_bytes(b'active\n');path.chmod(0o600)
        seen=[];enter(r,a,lambda token:seen.append(token));assert not seen and not a._adopted
        a.release()
