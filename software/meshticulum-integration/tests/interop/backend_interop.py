"""Canonical adapters against real temporary SAGE REST/vault fixtures.

Synthetic keys arrive on stdin. No production key, ledger, device, RF or
MessageSend API is used. Lifecycle callbacks are fixture policy, not proof of
the production owner/consent/sleep/revocation provider or private transport.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import time
import uuid

from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient
from meshticulum.privacy.mesh_envelope import Peer, seal_message, open_envelope
from meshticulum.privacy.mesh_binary_envelope import to_binary, to_text
from lantern_meshticulum.sage.message_contacts import RecipientID
from lantern_meshticulum.sage.message_storage import MessageStorageProbe
from lantern_meshticulum.sage.workflow_store import WorkflowStore, WorkflowNotFound, WorkflowOutcomeUnknown
from lantern_meshticulum.sage.mesh_inbox import MeshInbox, inbox_id
from lantern_meshticulum.sage.mesh_binary_capture import BinaryCapture, CaptureError

CANARY='MESHTICULUM-PRIVATE-BACKEND-CANARY'

def main():
    config=json.load(__import__('sys').stdin)
    first=AgentIdentity.from_seed(bytes.fromhex(config['first_seed']))
    second=AgentIdentity.from_seed(bytes.fromhex(config['second_seed']))
    sender=Peer('fixture-meshticulum-sender',bytes.fromhex(first.agent_id))
    recipient=Peer('fixture-meshticulum-recipient',bytes.fromhex(second.agent_id))
    checks={}
    with ExitStack() as clients:
        def store(endpoint,identity,peer):
            client=clients.enter_context(SageClient(endpoint,identity,timeout=3,trust_env=False))
            probe=MessageStorageProbe(client,actor=RecipientID(peer.chain_id,identity.agent_id),endpoint=endpoint)
            return WorkflowStore(client,storage=probe)
        outgoing=store(config['first_url'],first,sender)
        incoming=store(config['second_url'],second,recipient)
        checks['real_signed_storage_status']=bool(outgoing.storage.snapshot().epoch and incoming.storage.snapshot().epoch)
        control,record=str(uuid.uuid4()),str(uuid.uuid4())
        outgoing.put(control,'conversation_control',0,{'consent':True},access_check=lambda:True)
        outgoing.put(record,'conversation_session',0,{'private_canary':CANARY},
            guard={'record_id':control,'expected_revision':1},access_check=lambda:True)
        outgoing.put(control,'conversation_control',1,{'consent':False},access_check=lambda:True)
        try:
            outgoing.put(record,'conversation_session',1,{'private_canary':CANARY,'changed':True},
                guard={'record_id':control,'expected_revision':1},access_check=lambda:True)
        except WorkflowOutcomeUnknown:pass
        else:raise AssertionError('stale consent guard changed real backend state')
        assert outgoing.get(record,kind='conversation_session',access_check=lambda:True)['revision']==1
        checks['real_atomic_stale_guard_refused']=True
        try:incoming.get(record,kind='conversation_session',access_check=lambda:True)
        except WorkflowNotFound:pass
        else:raise AssertionError('cross-actor workflow record visible')
        checks['real_actor_isolation']=True

        # The backend is real; this lease/policy deliberately remains a fixture
        # and does not qualify production lifecycle authority.
        held=[False]; permitted=[True]
        @contextmanager
        def fixture_lease():
            assert not held[0];held[0]=True
            try:yield
            finally:held[0]=False
        access=lambda:held[0] and permitted[0]
        boot=str(uuid.uuid4())
        def capture():
            return BinaryCapture(incoming,recipient=recipient,recipient_signing_key=second._signing_key,
                owner_lease=fixture_lease,access_check=access,storage_check=access,
                peer_check=lambda p:access() and p==sender,boot_id=boot)
        def inbox():
            return MeshInbox(incoming,recipient=recipient,recipient_signing_key=second._signing_key,
                resolve_peer=lambda message:sender,access_check=access,storage_check=access)
        operation,message=str(uuid.uuid4()),str(uuid.uuid4())
        now=int(time.time())
        wire=seal_message(first._signing_key,sender=sender,recipient=recipient,message_id=message,
            content=CANARY,issued_at=now,expires_at=now+60).wire
        packet=to_binary(wire);assert to_text(packet)==wire
        digest=hashlib.sha256(packet).hexdigest();authority=hashlib.sha256(b'fixture-private-authority').hexdigest()
        owner=capture()
        assert owner.prepare(operation,sender=sender,packet_sha256=digest,packet_bytes=len(packet),
            application_expires_epoch_us=time.time_ns()//1000+30000000,
            application_deadline_monotonic_us=time.monotonic_ns()//1000+30000000,
            host_boot_id=boot,authority_digest=authority,permission_check=access)=='receiving'
        assert owner.complete(operation,packet,authority_digest=authority,permission_check=access)=='captured'
        restored=capture();result=restored.process_pending(inbox(),operation_id=operation)
        assert result.status=='admitted'
        local_id=inbox_id(sender,message)
        saved=incoming.get(local_id,kind='mesh_inbound',access_check=lambda:True)
        assert open_envelope(saved['payload']['wire'],second._signing_key,
            expected_sender=sender,expected_recipient=recipient,now=int(time.time())).content==CANARY
        replay=capture().process_pending(inbox(),operation_id=operation)
        assert replay.record_id==result.record_id
        assert capture().acknowledge(operation) is True
        checks['real_capture_restart_and_signed_admission']=True
        checks['real_durable_replay_same_record']=True
        checks['real_explicit_owner_acknowledgement']=True

        # Lose the client result after the real HTTP PUT commits. No fake HTTP
        # response or MockTransport is substituted; reconcile the exact UUID.
        operation2,message2=str(uuid.uuid4()),str(uuid.uuid4())
        wire2=seal_message(first._signing_key,sender=sender,recipient=recipient,message_id=message2,
            content=CANARY,issued_at=int(time.time()),expires_at=int(time.time())+60).wire
        packet2=to_binary(wire2);owner=capture()
        assert owner.prepare(operation2,sender=sender,packet_sha256=hashlib.sha256(packet2).hexdigest(),packet_bytes=len(packet2),
            application_expires_epoch_us=time.time_ns()//1000+30000000,
            application_deadline_monotonic_us=time.monotonic_ns()//1000+30000000,
            host_boot_id=boot,authority_digest=authority,permission_check=access)=='receiving'
        original=incoming.client.workflow_put;lost=[False]
        def committed_then_lost(*args,**kwargs):
            answer=original(*args,**kwargs)
            if answer.payload.get('schema')=='sage.lantern.mesh-binary-capture.v1' and answer.payload['body']['state']=='captured' and not lost[0]:
                lost[0]=True;raise RuntimeError('fixture client lost committed return')
            return answer
        incoming.client.workflow_put=committed_then_lost
        try:
            try:owner.complete(operation2,packet2,authority_digest=authority,permission_check=access)
            except CaptureError:pass
            else:raise AssertionError('unknown write was treated as confirmed')
        finally:incoming.client.workflow_put=original
        assert lost[0] and owner._uncertain
        restored=capture();assert restored.state()=='captured'
        recovered=restored.process_pending(inbox(),operation_id=operation2)
        assert recovered.status=='admitted' and capture().acknowledge(operation2) is True
        checks['real_committed_write_return_loss_reconciled']=True
        # Actual MPF2 receive-to-journal uses one encrypted workflow CAS. Native
        # permission/sender counters are fixture models; no projection/carrier.
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from meshticulum.privacy.forward_session import ForwardContext, ForwardSession, ForwardSessionError, public
        from lantern_meshticulum.sage.forward_receive_journal import (
            ForwardReceiveJournal, ForwardJournalError, ReceiveIntention, SCHEMA as JOURNAL_SCHEMA)
        class SenderCounters:
            def claim(self,binding):self.tx=0;return True
            def reserve_transmit(self):self.tx+=1;return self.tx
            def record_receive(self,n):return n==1
        def forward_case(*, lose_return=False):
            now_us=time.time_ns()//1000
            deadline=time.monotonic_ns()//1000+30000000
            expires=now_us+30000000
            authority=hashlib.sha256(b'fixture original forward-private authority').digest()
            noises=(X25519PrivateKey.generate(),X25519PrivateKey.generate())
            context=ForwardContext(sender,recipient,uuid.uuid4().bytes,authority,
                now_us,expires,64,public(noises[0]),public(noises[1]))
            packet=to_binary(seal_message(first._signing_key,sender=sender,recipient=recipient,
                message_id=str(uuid.uuid4()),content=CANARY,issued_at=int(time.time()),
                expires_at=int(time.time())+60).wire)
            intention=ReceiveIntention(str(uuid.uuid4()),hashlib.sha256(packet).hexdigest(),len(packet),
                authority.hex(),boot,expires,deadline)
            def make_journal(workflow):
                return ForwardReceiveJournal(workflow,context=context,local_initiator=False,
                    local_signing_key=second._signing_key,intention=intention,boot_id=boot,
                    owner_lease=fixture_lease,access_check=lambda:permitted[0],
                    storage_check=lambda:permitted[0],
                    peer_check=lambda peer:permitted[0] and peer==sender,
                    permission_check=lambda original:permitted[0] and original==intention,
                    clock=lambda:(time.time_ns()//1000,time.monotonic_ns()//1000))
            journal=make_journal(incoming)
            initiator=ForwardSession(context=context,initiator=True,signing_key=first._signing_key,
                noise_static_key=noises[0],counters=SenderCounters(),authorize=lambda:permitted[0],
                clock=lambda:(time.time_ns()//1000,time.monotonic_ns()//1000),
                application_deadline_monotonic_us=deadline)
            responder=ForwardSession(context=context,initiator=False,signing_key=second._signing_key,
                noise_static_key=noises[1],counters=journal,authorize=lambda:permitted[0],
                clock=lambda:(time.time_ns()//1000,time.monotonic_ns()//1000),
                application_deadline_monotonic_us=deadline,receive_mode='commit')
            initiator.finish(responder.respond(initiator.start()))
            initiator.verify_confirmation(responder.accept_confirmation(initiator.confirm()))
            outer=initiator.seal(packet)
            original_put=incoming.client.workflow_put;lost=[False]
            def committed_then_lost(*args,**kwargs):
                result=original_put(*args,**kwargs)
                if result.payload.get('schema')==JOURNAL_SCHEMA and result.payload['body']['capsule'] is not None:
                    lost[0]=True;raise RuntimeError('fixture lost joint-commit return')
                return result
            if lose_return:incoming.client.workflow_put=committed_then_lost
            try:
                if lose_return:
                    try:responder.receive_into(outer)
                    except ForwardSessionError:pass
                    else:raise AssertionError('unknown joint commit reported success')
                    assert lost[0] and journal._uncertain
                else:assert responder.receive_into(outer) is None
            finally:incoming.client.workflow_put=original_put
            initiator.close();responder.close();journal.close()
            # Fresh HTTP client/adapter; server stays live (no restart assertion).
            fresh_store=store(config['second_url'],second,recipient)
            recovered=make_journal(fresh_store).recover()
            assert recovered.sealed_record==packet and recovered.intention==intention
            assert recovered.outer_sha256==hashlib.sha256(outer).hexdigest()
            remote=fresh_store.get(journal.record_id,kind='mesh_inbound',access_check=lambda:True)
            assert remote['payload']['body']['capsule']['counter']==2
            try:make_journal(fresh_store).claim(journal.binding)
            except ForwardJournalError:pass
            else:raise AssertionError('retired claim reused')
            assert open_envelope(to_text(recovered.sealed_record),second._signing_key,
                expected_sender=sender,expected_recipient=recipient,now=int(time.time())).content==CANARY
            return True
        checks['real_forward_joint_replay_capsule_and_fresh_client_recovery']=forward_case()
        checks['real_forward_joint_commit_lost_return_recovery']=forward_case(lose_return=True)
        permitted[0]=False
        try:capture().state()
        except CaptureError:pass
        else:raise AssertionError('fixture revoked policy admitted work')
        checks['fixture_policy_revocation_refused']=True
        assert all(checks.values())
        print(json.dumps({'schema':'lantern.meshticulum.encrypted-backend-interop.v1','checks':checks,
            'backend_real_rest_sqlite_vault':True,'sdk_mock_transport':False,'fixture_only':True,
            'production_owner_consent_qualified':False,'private_transport_qualified':False,
            'private_production_enabled':False,'radio_opened':False,'original_operation_ids_preserved':True,
            'unknown_write_injection':'client return lost after real HTTP PUT; no network-disconnect claim'}),flush=True)

if __name__=='__main__':main()
