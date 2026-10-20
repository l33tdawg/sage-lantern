# SPDX-License-Identifier: Apache-2.0
"""Actual signed-journal import/inbox crypto; backend/native lifecycle modeled."""
from contextlib import contextmanager
from dataclasses import replace
import base64
import copy
import json
import uuid

import pytest
pytest.importorskip('sage_sdk',reason='compatible optional Lantern SDK required')
from lantern_meshticulum.sage.mesh_binary_capture import BinaryCapture, CaptureError, SCHEMA
from lantern_meshticulum.sage.forward_receive_journal import SIGNATURE_DOMAIN
from lantern_meshticulum.sage.mesh_inbox import _canonical
from tests.integrations.sage.test_mesh_inbox import rig, RECIPIENT, KEY, OPERATION, records
from tests.integrations.sage.test_forward_receive_journal import journal_rig, ready, accepted, BOOT


@pytest.fixture
def import_rig(journal_rig):
    rig=journal_rig
    rig.new_capture=lambda boot=BOOT:BinaryCapture(rig.store,recipient=RECIPIENT,
        recipient_signing_key=KEY,owner_lease=rig.journal._lease,access_check=lambda:rig.live,
        storage_check=lambda:rig.storage,peer_check=lambda peer:peer==rig.peer,
        clock=lambda:rig.now,monotonic_clock=lambda:rig.monotonic/1000000,boot_id=boot)
    rig.capture=rig.new_capture()
    return rig


def imported(rig, *, owner=None, reader=None):
    return (owner or rig.capture).import_forward(reader or rig.new_journal(),
        read_permission_check=lambda intended:rig.live and intended==rig.intention)


def capture_body(rig):return rig.rows[rig.capture.slot_id]['payload']['body']


def test_original_signed_journal_import_admission_restart_and_ack(import_rig):
    rig=import_rig;i,r=ready(rig)
    original=copy.deepcopy(rig.rows[rig.journal.record_id]['payload']['body'])
    rig.now=151;rig.monotonic=101000000;r.receive_into(i.seal(rig.packet))
    journal=copy.deepcopy(rig.rows[rig.journal.record_id]);i.close();r.close();rig.journal.close()
    rig.now=152;rig.monotonic=102000000
    assert imported(rig)=='captured'
    c=capture_body(rig)['capture']
    assert c['operation_id']==OPERATION and c['prepared_at_us']==original['prepared_epoch_us']
    assert c['captured_at_us']==151000000 and c['application_expires_epoch_us']==180000000
    assert c['application_deadline_monotonic_us']==130000000 and c['host_boot_id']==BOOT
    assert c['forward_journal']['prepared_monotonic_us']==100000000
    assert c['forward_journal']['observed_monotonic_us']==101000000
    before=copy.deepcopy(rig.rows)
    assert imported(rig,owner=rig.new_capture())=='captured' and rig.rows==before
    result=rig.new_capture().process_pending(rig.new(),operation_id=OPERATION)
    assert result.status=='admitted' and len(records(rig))==1
    assert capture_body(rig)['last_completed']['forward_journal']==c['forward_journal']
    before=copy.deepcopy(rig.rows)
    assert imported(rig,owner=rig.new_capture())=='idle' and rig.rows==before
    assert rig.new_capture().process_pending(rig.new(),operation_id=OPERATION).record_id==result.record_id
    assert rig.new_capture().acknowledge(OPERATION)
    assert imported(rig,owner=rig.new_capture())=='idle'
    assert rig.rows[rig.journal.record_id]==journal and 'private synthetic' not in json.dumps(rig.rows)


@pytest.mark.parametrize('boundary',['before','after','conflict'])
def test_import_cas_uncertainty_keeps_only_authoritative_source_and_recovers(import_rig,boundary):
    rig=import_rig;accepted(rig);journal=copy.deepcopy(rig.rows[rig.journal.record_id])
    assert rig.capture.state()=='idle'
    rig.failure=(SCHEMA,boundary)
    with pytest.raises(CaptureError):imported(rig)
    assert rig.capture._uncertain and rig.rows[rig.journal.record_id]==journal
    assert (capture_body(rig)['state']=='captured')==(boundary=='after')
    rig.failure=None
    assert imported(rig,owner=rig.new_capture())=='captured'
    assert rig.new_capture().process_pending(rig.new(),operation_id=OPERATION).status=='admitted'
    assert len(records(rig))==1 and rig.rows[rig.journal.record_id]==journal


@pytest.mark.parametrize('mode',['epoch','mono','boot'])
def test_historical_import_preserves_expired_original_and_never_fresh_admits(import_rig,mode):
    rig=import_rig;accepted(rig);journal=copy.deepcopy(rig.rows[rig.journal.record_id])
    boot=BOOT
    if mode=='epoch':rig.now=180
    elif mode=='mono':rig.monotonic=130000000
    else:boot=str(uuid.uuid4());rig.monotonic=1
    owner=rig.new_capture(boot=boot);reader=rig.new_journal(boot=boot)
    assert imported(rig,owner=owner,reader=reader)=='captured'
    result=rig.new_capture(boot=boot).process_pending(rig.new(),operation_id=OPERATION)
    assert result.status=='rejected'
    assert result.reason==('original_boot_changed' if mode=='boot' else 'application_expired')
    assert not records(rig) and rig.rows[rig.journal.record_id]==journal


@pytest.mark.parametrize('bad',['dto','claimed','foreign-store','context','operation','unsigned','boolean-original'])
def test_import_requires_exact_actual_signed_record_and_original_validation_binding(import_rig,bad):
    rig=import_rig;accepted(rig);reader=rig.new_journal()
    if bad=='dto':reader=reader.recover()
    elif bad=='claimed':reader=rig.journal
    elif bad=='foreign-store':reader.store=object()
    elif bad=='context':reader=rig.new_journal(context=replace(rig.context,max_records=32))
    elif bad=='operation':reader=rig.new_journal(intended=replace(rig.intention,operation_id=str(uuid.uuid4())))
    else:
        row=rig.rows[rig.journal.record_id];body=row['payload']['body']
        if bad=='unsigned':body['capsule']['outer_sha256']='d'*64
        else:
            body['intention']['packet_bytes']=True
            row['payload']['signature']=base64.b64encode(KEY.sign(SIGNATURE_DOMAIN+_canonical(body)).signature).decode()
    before=copy.deepcopy(rig.rows)
    with pytest.raises(CaptureError):imported(rig,reader=reader)
    assert rig.rows==before and not records(rig)


def test_other_original_receiving_intention_is_never_overwritten(import_rig):
    rig=import_rig;accepted(rig)
    other=str(uuid.uuid4());intent=rig.intention
    rig.capture.prepare(other,sender=rig.peer,packet_sha256=intent.packet_sha256,
        packet_bytes=intent.packet_bytes,application_expires_epoch_us=intent.application_expires_epoch_us,
        application_deadline_monotonic_us=intent.application_deadline_monotonic_us,
        host_boot_id=BOOT,authority_digest=intent.authority_digest,permission_check=lambda:True)
    before=copy.deepcopy(rig.rows)
    with pytest.raises(CaptureError):imported(rig)
    assert rig.rows==before and capture_body(rig)['capture']['operation_id']==other


@pytest.mark.parametrize('source',['permission','journal-peer','journal-clock','capture-peer','nested-capture','nested-journal'])
def test_import_callback_close_or_caught_reentry_cannot_dispatch_projection(import_rig,source):
    rig=import_rig;accepted(rig);reader=rig.new_journal();assert rig.capture.state()=='idle'
    before=copy.deepcopy(rig.rows)
    def permission(intention):
        if source=='permission':rig.capture.close(0)
        elif source=='nested-capture':
            with pytest.raises(CaptureError):rig.capture.state()
        elif source=='nested-journal':
            from lantern_meshticulum.sage.forward_receive_journal import ForwardJournalError
            with pytest.raises(ForwardJournalError):reader.recover()
        return True
    def close_peer(peer):rig.capture.close(0);return True
    if source=='journal-peer':reader._peer=close_peer
    elif source=='capture-peer':rig.capture._peer=close_peer
    elif source=='journal-clock':
        def clock():rig.capture.close(0);return rig.clock()
        reader._clock=clock
    with pytest.raises(CaptureError):rig.capture.import_forward(reader,read_permission_check=permission)
    assert rig.rows==before and rig.capture._uncertain and reader._uncertain


@pytest.mark.parametrize('mode',['access','storage-epoch','clock-access'])
def test_post_exit_observation_revocation_refuses_success_with_capsule_retained(import_rig,mode):
    rig=import_rig;accepted(rig);reader=rig.new_journal()
    original=rig.store.storage.snapshot
    def snapshot():
        result=original()
        if not rig.lease_active:
            if mode=='access':rig.live=False
            elif mode=='storage-epoch':rig.instance='c'*64
        return result
    rig.store.storage.snapshot=snapshot
    if mode=='clock-access':
        def clock():
            result=rig.clock()
            if not rig.lease_active:rig.live=False
            return result
        reader._clock=clock
    with pytest.raises(CaptureError):imported(rig,reader=reader)
    assert capture_body(rig)['state']=='captured' and reader._uncertain
    assert rig.rows[rig.journal.record_id]['payload']['body']['capsule'] is not None


def test_import_without_joint_data_capsule_cannot_create_capture_intention(import_rig):
    rig=import_rig;ready(rig);before=copy.deepcopy(rig.rows)
    with pytest.raises(CaptureError):imported(rig)
    assert rig.rows==before


@pytest.mark.parametrize('callback',['snapshot','clock','permission'])
def test_caught_post_exit_capture_reentry_refuses_without_second_lease_or_mutation(import_rig,callback):
    rig=import_rig;accepted(rig);reader=rig.new_journal();events=[]
    original=rig.capture._lease
    @contextmanager
    def lease():
        events.append('enter')
        with original():yield
        events.append('exit')
    rig.capture._lease=reader._lease=lease
    def reenter():
        if not rig.lease_active:
            before=copy.deepcopy(rig.rows)
            with pytest.raises(CaptureError):rig.capture.process_pending(rig.new(),operation_id=OPERATION)
            assert rig.rows==before
    if callback=='snapshot':
        original_snapshot=rig.store.storage.snapshot
        def snapshot():reenter();return original_snapshot()
        rig.store.storage.snapshot=snapshot
    elif callback=='clock':
        def clock():reenter();return rig.clock()
        reader._clock=clock
    def permission(intention):
        if callback=='permission':reenter()
        return True
    with pytest.raises(CaptureError):rig.capture.import_forward(reader,read_permission_check=permission)
    assert events==['enter','exit'] and not records(rig)
    assert rig.capture._uncertain and reader._uncertain
    assert capture_body(rig)['state']=='captured'


@pytest.mark.parametrize('settled',[False,True])
def test_legitimate_source_counter_advance_preserves_import_proof_and_fences_later_rollback(import_rig,settled):
    rig=import_rig;accepted(rig);assert imported(rig)=='captured'
    source_before=copy.deepcopy(rig.rows[rig.journal.record_id])
    proof=copy.deepcopy(capture_body(rig)['capture']['forward_journal'])
    if settled:
        assert rig.new_capture().process_pending(rig.new(),operation_id=OPERATION).status=='admitted'
        assert rig.new_capture().acknowledge(OPERATION)
    rig.journal.reserve_transmit()  # Source signature/immutable capsule stay valid.
    expected='idle' if settled else 'captured'
    assert imported(rig,owner=rig.new_capture())==expected
    b=capture_body(rig);new=(b['last_completed'] if settled else b['capture'])['forward_journal']
    assert new['revision']==proof['revision'] and new['body_sha256']==proof['body_sha256']
    assert new['latest_revision']>proof['revision']
    assert new['intention']==proof['intention']
    rig.rows[rig.journal.record_id]=source_before
    before=copy.deepcopy(rig.rows)
    with pytest.raises(CaptureError):imported(rig,owner=rig.new_capture())
    assert rig.rows==before


@pytest.mark.parametrize('field,value',[
    ('authority_digest','d'*64),('application_expires_epoch_us',179000000),
    ('application_deadline_monotonic_us',129000000),('host_boot_id','31f9c178-6090-40d9-9359-ecf1c7b950b1')])
def test_signed_idle_tombstone_refuses_changed_original_facts(import_rig,field,value):
    rig=import_rig;accepted(rig);imported(rig)
    rig.new_capture().process_pending(rig.new(),operation_id=OPERATION)
    from lantern_meshticulum.sage.mesh_binary_capture import DOMAIN
    row=rig.rows[rig.capture.slot_id];b=row['payload']['body']
    b['last_completed']['forward_journal']['intention'][field]=value
    row['payload']['signature']=base64.b64encode(KEY.sign(DOMAIN+_canonical(b)).signature).decode()
    before=copy.deepcopy(rig.rows)
    with pytest.raises(CaptureError):imported(rig,owner=rig.new_capture())
    assert rig.rows==before and len(records(rig))==1


def test_unrelated_native_lease_function_cannot_import_even_with_matching_store_and_keys(import_rig):
    rig=import_rig;accepted(rig);reader=rig.new_journal();before=copy.deepcopy(rig.rows)
    @contextmanager
    def wrong_lease():yield
    reader._lease=wrong_lease
    with pytest.raises(CaptureError):imported(rig,reader=reader)
    assert rig.rows==before


def test_forward_import_inbox_commit_lost_response_reconciles_original_admission(import_rig):
    rig=import_rig;accepted(rig);imported(rig)
    journal=copy.deepcopy(rig.rows[rig.journal.record_id])
    from lantern_meshticulum.sage.mesh_inbox import INBOX_SCHEMA
    rig.failure=(INBOX_SCHEMA,'after')
    with pytest.raises(CaptureError):rig.new_capture().process_pending(rig.new(),operation_id=OPERATION)
    assert len(records(rig))==1
    rig.failure=None
    assert imported(rig,owner=rig.new_capture())=='captured'
    decision=rig.new_capture().process_pending(rig.new(),operation_id=OPERATION)
    assert decision.status=='duplicate' and len(records(rig))==1
    assert records(rig)[0]['payload']['operation_id']==OPERATION
    assert rig.rows[rig.journal.record_id]==journal


def test_forward_clear_response_loss_retains_signed_tombstone_and_ack_does_not_recreate(import_rig):
    rig=import_rig;accepted(rig);imported(rig)
    journal=copy.deepcopy(rig.rows[rig.journal.record_id])
    from tests.integrations.sage.test_mesh_binary_capture import fail_phase
    original=fail_phase(rig,'clear','after')
    with pytest.raises(CaptureError):rig.new_capture().process_pending(rig.new(),operation_id=OPERATION)
    rig.store.put=original;rig.failure=None
    assert capture_body(rig)['state']=='idle' and len(records(rig))==1
    assert imported(rig,owner=rig.new_capture())=='idle'
    assert rig.new_capture().process_pending(rig.new(),operation_id=OPERATION).status=='admitted'
    assert rig.new_capture().acknowledge(OPERATION)
    assert imported(rig,owner=rig.new_capture())=='idle' and len(records(rig))==1
    assert rig.rows[rig.journal.record_id]==journal
