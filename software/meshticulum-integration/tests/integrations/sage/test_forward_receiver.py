# SPDX-License-Identifier: Apache-2.0
"""Canonical borrowed-client composition; owner/key purpose are fixture policy."""
from dataclasses import replace
import copy
import pytest
pytest.importorskip('sage_sdk',reason='compatible optional Lantern SDK required')
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from meshticulum.privacy.forward_session import public
from lantern_meshticulum.sage.forward_receiver import ForwardReceiver,ForwardReceiverError
from lantern_meshticulum.sage.workflow_store import WorkflowStore
from tests.integrations.sage.test_mesh_inbox import rig,KEY,records
from tests.integrations.sage.test_forward_receive_journal import journal_rig,BOOT


@pytest.fixture
def receiver_rig(journal_rig):
    rig=journal_rig;rig.read_ok=True;rig.purpose=True
    def new():
        return ForwardReceiver(rig.store.client,context=rig.context,local_initiator=False,
            intention=rig.intention,boot_id=BOOT,endpoint=rig.store.client._base_url,signing_key=KEY,
            owner_lease=rig.journal._lease,access_check=lambda:rig.live,storage_check=lambda:rig.storage,
            peer_check=lambda peer:peer==rig.peer,dispatch_check=lambda ctx,intention:rig.permission,
            read_check=lambda ctx,intention:rig.read_ok,key_check=lambda key,digest:rig.purpose,
            clock=rig.clock)
    rig.new_receiver=new;rig.receiver=new()
    # Same dedicated public fixture key as the existing journal_rig.
    import hashlib
    rig.noise=X25519PrivateKey.from_private_bytes(hashlib.sha256(b'fixture journal responder Noise').digest())
    return rig


def complete(rig):
    i=rig.sender();r=rig.receiver.create_session(rig.noise)
    i.finish(r.respond(i.start()));i.verify_confirmation(r.accept_confirmation(i.confirm()))
    r.receive_into(i.seal(rig.packet))
    return i,r


def test_canonical_composition_uses_borrowed_client_and_original_protected_admission(receiver_rig):
    rig=receiver_rig;owner=rig.receiver
    assert owner.client is rig.store.client and owner.store.client is rig.store.client
    assert type(owner.store) is WorkflowStore and owner.store is not rig.store
    i,r=complete(rig);before=copy.deepcopy(rig.rows[owner.journal.record_id])
    assert owner.import_pending()=='captured'
    result=owner.settle();assert result.status=='admitted' and len(records(rig))==1
    assert owner.acknowledge() and owner.close()
    assert not rig.store.client._client.is_closed
    assert rig.rows[owner.journal.record_id]==before
    fresh=rig.new_receiver();assert fresh.import_pending()=='idle'
    assert fresh.settle().record_id==result.record_id
    with pytest.raises(ForwardReceiverError):fresh.create_session(rig.noise)
    assert len(records(rig))==1 and rig.rows[owner.journal.record_id]==before


@pytest.mark.parametrize('mode',['purpose','dispatch','access','storage','peer','key'])
def test_native_input_refusal_happens_before_any_claim(receiver_rig,mode):
    rig=receiver_rig;key=rig.noise
    if mode=='purpose':rig.purpose=False
    elif mode=='dispatch':rig.permission=False
    elif mode=='access':rig.live=False
    elif mode=='storage':rig.storage=False
    elif mode=='peer':rig.peer=rig.receiver.local
    else:key=X25519PrivateKey.generate()
    with pytest.raises(ForwardReceiverError):rig.receiver.create_session(key)
    assert not rig.rows and rig.receiver._uncertain
    assert not rig.store.client._client.is_closed


@pytest.mark.parametrize('mode',['purpose','dispatch','storage','peer'])
def test_between_stage_revocation_closes_crypto_without_new_nonce_or_capsule(receiver_rig,mode):
    rig=receiver_rig;i,r=complete(rig);before=copy.deepcopy(rig.rows)
    if mode=='purpose':rig.purpose=False
    elif mode=='dispatch':rig.permission=False
    elif mode=='storage':rig.storage=False
    else:rig.peer=rig.receiver.local
    from meshticulum.privacy.forward_session import ForwardSessionError
    with pytest.raises(ForwardSessionError):r.seal(rig.packet)
    assert r.state=='closed' and rig.rows==before


def test_recovery_does_not_require_retired_noise_key_or_fresh_dispatch_authority(receiver_rig):
    rig=receiver_rig;complete(rig);original=copy.deepcopy(rig.rows[rig.receiver.journal.record_id])
    rig.receiver.close();rig.purpose=False;rig.permission=False;rig.now=180
    fresh=rig.new_receiver();assert fresh.import_pending()=='captured'
    result=fresh.settle();assert result.status=='rejected' and result.reason=='application_expired'
    assert not records(rig) and rig.rows[fresh.journal.record_id]==original


def test_closed_composition_never_closes_borrowed_backend_or_dispatches(receiver_rig):
    rig=receiver_rig;rig.receiver.close();before=copy.deepcopy(rig.rows)
    with pytest.raises(ForwardReceiverError):rig.receiver.import_pending()
    assert rig.rows==before and not rig.store.client._client.is_closed


def test_callback_close_or_caught_reentry_cannot_finish_construction(receiver_rig):
    rig=receiver_rig
    def purpose(key,digest):
        with pytest.raises(ForwardReceiverError):rig.receiver.import_pending()
        return True
    rig.receiver._purpose=purpose
    with pytest.raises(ForwardReceiverError):rig.receiver.create_session(rig.noise)
    assert not rig.rows and rig.receiver._uncertain


@pytest.mark.parametrize('operation',['settle','acknowledge'])
def test_current_read_revocation_blocks_settlement_or_clear_without_backend_change(receiver_rig,operation):
    rig=receiver_rig;complete(rig);rig.receiver.import_pending()
    if operation=='acknowledge':assert rig.receiver.settle().status=='admitted'
    before=copy.deepcopy(rig.rows);rig.read_ok=False
    with pytest.raises(ForwardReceiverError):getattr(rig.receiver,operation)()
    assert rig.rows==before


def test_current_read_revoked_after_inbox_commit_cannot_publish_success(receiver_rig):
    rig=receiver_rig;complete(rig);rig.receiver.import_pending()
    rig.after_put=lambda:setattr(rig,'read_ok',False)
    with pytest.raises(ForwardReceiverError):rig.receiver.settle()
    assert len(records(rig))==1
    rig.after_put=None;rig.read_ok=True
    fresh=rig.new_receiver();assert fresh.import_pending()=='captured'
    assert fresh.settle().status=='duplicate' and len(records(rig))==1


@pytest.mark.parametrize('boundary',['crypto','journal'])
def test_close_during_direct_child_operation_cannot_certify_join(receiver_rig,boundary):
    rig=receiver_rig;complete(rig);rig.receiver.import_pending();seen=[]
    if boundary=='crypto':
        original=rig.receiver._dispatch
        def dispatch(ctx,intention):seen.append(rig.receiver.close());return True
        rig.receiver._dispatch=dispatch
        from meshticulum.privacy.forward_session import ForwardSessionError
        with pytest.raises(ForwardSessionError):rig.receiver.session.seal(rig.packet)
    else:
        def storage():seen.append(rig.receiver.close());return True
        rig.receiver._storage=storage
        from lantern_meshticulum.sage.forward_receive_journal import ForwardJournalError
        with pytest.raises(ForwardJournalError):rig.receiver.journal.reserve_transmit()
    assert seen==[False] and not rig.store.client._client.is_closed


@pytest.mark.parametrize('operation',['import_pending','settle','acknowledge'])
def test_direct_crypto_callback_cannot_reenter_composition_and_publish_success(receiver_rig,operation):
    rig=receiver_rig;complete(rig);rig.receiver.import_pending();before=copy.deepcopy(rig.rows)
    def dispatch(ctx,intention):
        with pytest.raises(ForwardReceiverError):getattr(rig.receiver,operation)()
        return True
    rig.receiver._dispatch=dispatch
    from meshticulum.privacy.forward_session import ForwardSessionError
    with pytest.raises(ForwardSessionError):rig.receiver.session.seal(rig.packet)
    assert rig.rows==before and rig.receiver._uncertain


def test_composition_callback_cannot_enter_returned_crypto_session(receiver_rig):
    rig=receiver_rig;complete(rig);before=copy.deepcopy(rig.rows)
    def read(ctx,intention):
        from meshticulum.privacy.forward_session import ForwardSessionError
        with pytest.raises(ForwardSessionError):rig.receiver.session.seal(rig.packet)
        return True
    rig.receiver._read=read
    with pytest.raises(ForwardReceiverError):rig.receiver.import_pending()
    assert rig.rows==before and rig.receiver._uncertain


def test_wrong_process_copy_cannot_certify_original_owner_join(receiver_rig):
    import os,threading
    rig=receiver_rig;complete(rig);rig.receiver.import_pending();before=copy.deepcopy(rig.rows)
    rig.receiver._owner=(os.getpid()+1,threading.get_ident())
    assert rig.receiver.close() is False
    assert rig.rows==before and not rig.store.client._client.is_closed


@pytest.mark.parametrize('change',['identity','endpoint','transport','signer','closed'])
def test_borrowed_client_drift_refuses_handshake_before_new_crypto_or_backend_work(receiver_rig,change):
    rig=receiver_rig;r=rig.receiver.create_session(rig.noise);i=rig.sender();h1=i.start()
    client=rig.store.client;original=(client._identity,client._base_url,client._client,client._identity._signing_key)
    before=copy.deepcopy(rig.rows)
    from sage_sdk.auth import AgentIdentity
    from nacl.signing import SigningKey
    if change=='identity':client._identity=AgentIdentity(SigningKey.generate())
    elif change=='endpoint':client._base_url='http://127.0.0.1:19101'
    elif change=='transport':client._client=object()
    elif change=='signer':client._identity._signing_key=SigningKey(bytes(KEY))
    else:client._client.close()
    from meshticulum.privacy.forward_session import ForwardSessionError
    try:
        with pytest.raises(ForwardSessionError):r.respond(h1)
        assert r.state=='closed' and rig.rows==before
    finally:
        client._identity,client._base_url,client._client=original[:3]
        client._identity._signing_key=original[3]
