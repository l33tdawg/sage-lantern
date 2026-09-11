"""Child of the isolated Go fixture harness; stdin contains synthetic keys only."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json
import struct
import sys
import time
import uuid

from nacl.signing import SigningKey
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient

from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.community_public import CommunityPublic
from sage_lantern.conversation_service import ConversationService
from sage_lantern.conversation_records import ConversationDrainSnapshot
from sage_lantern.protected_conversation_owner import ProtectedConversationOwner, ConversationOwnerError
from sage_lantern.mesh_envelope import Peer, open_envelope
from sage_lantern.mesh_fragments import split_envelope
from sage_lantern.mesh_inbox import MeshInbox, inbox_id
from sage_lantern.mesh_outbox import MeshOutbox
from sage_lantern.mesh_receiver import MeshReceiver
from sage_lantern.mesh_receipt_sender import MeshReceiptSender, ReceiptAction, ReceiptPermit, ReceiptSenderError
from sage_lantern.meshcore_transport import FIRMWARE, MeshCoreClient
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.private_media_store import ProtectedMediaStore, ProtectedMediaUnavailable
from sage_lantern.public_workflow_store import PublicWorkflowStore
from sage_lantern.workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


class SyntheticSerial:
    def __init__(self, data):
        self.incoming = bytearray(data)
        self.outgoing = bytearray()

    def read(self, size, timeout):
        assert timeout > 0
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def write(self, data, timeout):
        assert timeout > 0
        self.outgoing.extend(data)
        return len(data)


def frame(payload):
    return b">" + struct.pack("<H", len(payload)) + payload


def handshake(key):
    info = bytearray(82)
    info[:2] = b"\x0d\x0d"
    info[60:60 + len(FIRMWARE)] = FIRMWARE.encode("ascii")
    own = bytearray(58)
    own[0] = 5
    own[4:36] = key
    return frame(bytes(info)) + frame(bytes(own))


def inventory(key):
    row = bytearray(148)
    row[0] = 3
    row[1:33] = key
    row[144:148] = struct.pack("<I", 1)
    return frame(b"\x02" + struct.pack("<I", 1)) + frame(bytes(row)) + frame(b"\x04" + struct.pack("<I", 1))


def main():
    configuration = json.load(sys.stdin)
    first = AgentIdentity.from_seed(bytes.fromhex(configuration["first_seed"]))
    second = AgentIdentity.from_seed(bytes.fromhex(configuration["second_seed"]))
    sender = Peer("interop-a", bytes.fromhex(first.agent_id))
    recipient = Peer("interop-b", bytes.fromhex(second.agent_id))
    sender_radio = bytes(SigningKey(b"r" * 32).verify_key)
    recipient_radio = bytes(SigningKey(b"s" * 32).verify_key)
    clients = ExitStack()

    def journal(endpoint, identity, peer):
        client = clients.enter_context(SageClient(base_url=endpoint, identity=identity, timeout=3, trust_env=False))
        probe = MessageStorageProbe(client, actor=RecipientID(peer.chain_id, identity.agent_id), endpoint=endpoint)
        return WorkflowStore(client, storage=probe)

    try:
        sender_store = journal(configuration["first_url"], first, sender)
        recipient_store = journal(configuration["second_url"], second, recipient)
        control_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
        sender_store.put(control_id, "conversation_control", 0, {"consent": True}, access_check=lambda: True)
        sender_store.put(session_id, "conversation_session", 0, {"state": "queued"},
                         guard=dict(record_id=control_id, expected_revision=1), access_check=lambda: True)
        sender_store.put(control_id, "conversation_control", 1, {"consent": False}, access_check=lambda: True)
        try:
            sender_store.put(session_id, "conversation_session", 1, {"state": "reserved"},
                             guard=dict(record_id=control_id, expected_revision=1), access_check=lambda: True)
        except WorkflowOutcomeUnknown:
            pass
        else:
            raise AssertionError("stale consent guard admitted mutation")
        unchanged = sender_store.get(session_id, kind="conversation_session", access_check=lambda: True)
        assert unchanged["revision"] == 1 and unchanged["payload"] == {"state": "queued"}
        media = ProtectedMediaStore(sender_store.client, storage=sender_store.storage)
        other_media = ProtectedMediaStore(recipient_store.client, storage=recipient_store.storage)
        photo_id = str(uuid.uuid4())
        synthetic_jpeg = bytes((255, 216, 255, 192, 0, 11, 8, 2, 208, 5, 0, 1, 1, 17, 0,
                                255, 218, 0, 8, 1, 1, 0, 0, 63, 0)) + b"LANTERN-INTEROP-PRIVATE-CANARY\xff\xd9"
        photo_digest = media.put(photo_id, synthetic_jpeg, access_check=lambda: True)
        assert media.put(photo_id, synthetic_jpeg, access_check=lambda: True) == photo_digest
        assert media.get(photo_id, expected_digest=photo_digest, access_check=lambda: True) == synthetic_jpeg
        try:
            other_media.get(photo_id, access_check=lambda: True)
        except ProtectedMediaUnavailable:
            pass
        else:
            raise AssertionError("cross-store photo visible")
        public_archive = PublicWorkflowStore(sender_store, chain_id="synthetic-public", domain="village",
                                              author=first.agent_id, access_check=lambda: True,
                                              drain_check=lambda: True, identity=first)
        public_preview = CommunityPublic(None, None, chain_id="synthetic-public", domain="village",
                                         author=first.agent_id).prepare(operation_id=str(uuid.uuid4()),
                                         question="LANTERN-INTEROP-PRIVATE-CANARY", answer="Synthetic preview only")
        prepared_at = "2026-09-06T00:00:00Z"
        history_source = str(uuid.uuid4())
        assert public_archive.prepare_public_source(public_preview, prepared_at, history_source) is True
        restored_archive = PublicWorkflowStore(sender_store, chain_id="synthetic-public", domain="village",
                                               author=first.agent_id, access_check=lambda: True,
                                               drain_check=lambda: True, identity=first)
        restored_source = restored_archive.public_sources([history_source])[history_source]
        assert restored_source["proposal"] == public_preview and restored_source["receipt"] is None
        assert public_archive.pending_count() == 1
        assert public_archive.cancel_prepared_public(public_preview, prepared_at) is True
        assert public_archive.pending_count() == 0
        assert public_archive.list_public() == []
        assert restored_archive.public_sources([history_source])[history_source]["receipt"] is None
        owner_directory = Path(clients.enter_context(TemporaryDirectory(prefix="synthetic-conversation-owner-"))).resolve(strict=True)
        assert owner_directory.stat().st_mode & 0o777 == 0o700
        history_config = SimpleNamespace(protected_storage=True, sage_base_url=configuration["second_url"],
                                        sage_chain_id=recipient.chain_id, sage_domain="second.home",
                                        sage_identity_path=Path("synthetic-stdin-identity-only"),
                                        conversation_dir=owner_directory, llama_base_url="http://127.0.0.1:1")
        def synthetic_identity(path):
            assert path == "synthetic-stdin-identity-only"
            return second
        with patch.object(AgentIdentity, "from_file", side_effect=synthetic_identity) as identity_loader:
            memory = ConversationService.from_config(history_config)
        identity_loader.assert_called_once_with("synthetic-stdin-identity-only")
        clients.callback(memory.close, timeout=10)
        memory._epoch_reader = lambda: "a" * 32
        assert memory.protected_storage is True and memory._owner.valid() is True
        assert memory._owned_client._client.trust_env is False
        assert memory.store._store is None
        try:
            competing_owner = ProtectedConversationOwner(owner_directory)
        except ConversationOwnerError:
            pass
        else:
            competing_owner.close()
            raise AssertionError("protected conversation owner did not exclude a competitor")
        memory.store.approve(True)
        assert memory.store._store is not None
        conversation_id = str(uuid.uuid4())
        item = dict(session_id=conversation_id, created_at=prepared_at,
                    question="LANTERN-INTEROP-PRIVATE-CANARY", answer="Synthetic photo answer", has_photo=True)
        intent = memory.reserve_media(conversation_id, photo_digest, ticket=memory.store.consent(),
                                      item=item, access_check=lambda: True)
        assert memory.media_store.put(conversation_id, synthetic_jpeg, access_check=lambda: True) == photo_digest
        assert memory.commit_media(intent, access_check=lambda: True) is True
        history = memory.items()
        assert len(history) == 1 and history[0]["has_photo"] is True
        assert history[0]["photo_reference"] == dict(object_id=conversation_id, digest=photo_digest,
                                                     backend="sage.private-media.v1")
        memory.store.suppress([conversation_id])
        assert memory.items() == []
        assert memory.media_store.get(conversation_id, expected_digest=photo_digest,
                                      access_check=lambda: True) == synthetic_jpeg
        plain_id = str(uuid.uuid4())
        memory.record(memory.store.consent(), dict(session_id=plain_id, created_at=prepared_at,
                      question="LANTERN-INTEROP-PRIVATE-CANARY", answer="Synthetic queued history", has_photo=False))
        assert any(entry["session_id"] == plain_id for entry in memory.items())
        drain = memory.drain_snapshot(timeout=10)
        assert type(drain) is ConversationDrainSnapshot and drain.queued > 0
        assert drain.storage_epoch and drain.dispatching == drain.unknown == drain.pending_media == 0
        assert memory._owner.valid() is True
        assert memory.release(timeout=10) is True
        assert memory._owned_client._client.is_closed is True
        assert memory._owner.valid() is False
        released_owner = ProtectedConversationOwner(owner_directory)
        assert released_owner.valid() is True
        released_owner.close()
        now = int(time.time())
        card = sign_contact_card(second, chain_id=recipient.chain_id, radio_public_key=recipient_radio.hex(),
                                 display_name="Synthetic B", revision=1, issued_at=now - 1,
                                 expires_at=now + 3599, now=now, findable=True, messaging_allowed=True)
        verified_contact = verify_contact_card(card, now=now)
        outbox = MeshOutbox(sender_store, first, sender=sender, guard=lambda action: True,
                            verify_contact=lambda action: verified_contact)
        message_id = str(uuid.uuid4())
        outbox.queue(message_id=message_id, recipient=recipient, radio_public_key=recipient_radio,
                     content="LANTERN-INTEROP-PRIVATE-CANARY", approval_binding="synthetic-review-1",
                     consent_binding="synthetic-contact-1", issued_at=now, expires_at=now + 3500,
                     access_check=lambda: True)
        record = sender_store.get(message_id, kind="mesh_outbound", access_check=lambda: True)
        wire = record["payload"]["body"]["approved"]["wire"]
        pieces = split_envelope(wire.encode("ascii"))
        outgoing_replies = handshake(sender_radio)
        incoming_frames = handshake(recipient_radio)
        for index, piece in enumerate(pieces):
            stamp = struct.pack("<I", now + index)
            acknowledgement = hashlib.sha256(stamp + b"\0" + piece.encode("ascii") + sender_radio).digest()[:4]
            outgoing_replies += inventory(recipient_radio) + frame(b"\x06\x01" + acknowledgement + struct.pack("<I", 1000))
            incoming_frames += frame(b"\x07" + sender_radio[:6] + b"\0\0" + stamp + piece.encode("ascii"))
        outgoing = MeshCoreClient(SyntheticSerial(outgoing_replies), expected_public_key=sender_radio)
        incoming = MeshCoreClient(SyntheticSerial(incoming_frames), expected_public_key=recipient_radio)
        outgoing.handshake()
        incoming.handshake()
        slot = str(uuid.uuid4())
        receiver = MeshReceiver(recipient_store, incoming, slot_id=slot,
                                access_check=lambda: True, storage_check=lambda: True)
        inbox = MeshInbox(recipient_store, recipient=recipient, recipient_signing_key=second._signing_key,
                          resolve_peer=lambda message: sender if message.sender_prefix == sender_radio[:6] else None,
                          access_check=lambda: True, storage_check=lambda: True)
        for index, _ in enumerate(pieces):
            progress = outbox.dispatch_one(message_id, device=outgoing, radio_timestamp=now + index,
                                           access_check=lambda: True)
            assert progress.state in ("queued", "awaiting_receipt")
            assert receiver.receive_next() == "captured"
            if index == len(pieces) // 2:
                assert receiver.close(timeout=1) is True
                receiver = MeshReceiver(recipient_store, incoming, slot_id=slot,
                                        access_check=lambda: True, storage_check=lambda: True)
                inbox = MeshInbox(recipient_store, recipient=recipient, recipient_signing_key=second._signing_key,
                                  resolve_peer=lambda message: sender, access_check=lambda: True,
                                  storage_check=lambda: True)
            assert receiver.process_pending(inbox) is True
        local_id = inbox_id(sender, message_id)
        saved = recipient_store.get(local_id, kind="mesh_inbound", access_check=lambda: True)
        opened = open_envelope(saved["payload"]["wire"], second._signing_key,
                               expected_sender=sender, expected_recipient=recipient, now=int(time.time()))
        assert opened.content == "LANTERN-INTEROP-PRIVATE-CANARY"
        assert inbox.historical_read(local_id).content == opened.content
        assert saved["payload"]["remote_id"] == message_id
        assert outbox.inspect(message_id, access_check=lambda: True).state == "awaiting_receipt"
        receipt_time = int(time.time())
        return_card = sign_contact_card(first, chain_id=sender.chain_id, radio_public_key=sender_radio.hex(),
                                       display_name="Synthetic A", revision=1, issued_at=receipt_time - 1,
                                       expires_at=receipt_time + 3599, now=receipt_time,
                                       findable=True, messaging_allowed=True)
        producer_session = object()
        current_session = producer_session
        render_ack = (recipient, local_id, message_id, opened.digest,
                      hashlib.sha256(opened.content.encode()).hexdigest(), producer_session)
        read_privacy_approval = (recipient, sender, local_id, producer_session)
        recovery_fenced = False

        def receipt_access():
            return current_session is producer_session

        def receipt_guard(action):
            return (type(action) is ReceiptAction and receipt_access()
                    and action.local_id == local_id and action.kind == "read"
                    and action.original_sender == sender and action.original_id == message_id
                    and action.original_digest == opened.digest and action.radio_public_key == sender_radio)

        def receipt_policy(action):
            assert receipt_guard(action)
            assert read_privacy_approval == (recipient, sender, local_id, current_session)
            retained = inbox.historical_read(local_id)
            assert render_ack == (recipient, local_id, retained.message_id, retained.digest,
                                  hashlib.sha256(retained.content.encode()).hexdigest(), current_session)
            if action.operation == "retry":
                assert recovery_fenced and action.decision_binding == "synthetic-old-receipt-worker-fenced"
            return ReceiptPermit(action, "synthetic-read-privacy-approval", "synthetic-render-ack")

        def receipt_contact(action):
            assert receipt_guard(action)
            return verify_contact_card(return_card, now=int(time.time()))

        def new_receipt_sender():
            return MeshReceiptSender(recipient_store, second, inbox=inbox, sender=recipient,
                                     guard=receipt_guard, policy=receipt_policy, verify_contact=receipt_contact)

        producer = new_receipt_sender()
        queued_receipt = producer.queue(local_id=local_id, kind="read", radio_public_key=sender_radio,
                                        issued_at=receipt_time, expires_at=receipt_time + 3500,
                                        access_check=receipt_access)
        receipt_id = queued_receipt.receipt_id

        def receipt_record():
            return recipient_store.get(receipt_id, kind="mesh_outbound", access_check=receipt_access)["payload"]["body"]

        durable_receipt = receipt_record()
        receipt_wire = durable_receipt["approval"]["body"]["wire"]
        receipt_pieces = split_envelope(receipt_wire.encode("ascii"))
        serial_sends = []

        class ReceiptSerial(SyntheticSerial):
            def __init__(self, data, lose_ack):
                super().__init__(data)
                self.lose_ack = lose_ack

            def read(self, size, timeout):
                if self.lose_ack and not self.incoming:
                    raise OSError("synthetic receipt acknowledgement lost")
                return super().read(size, timeout)

            def write(self, data, timeout):
                if len(data) > 3 and data[0:1] == b"<" and data[3] == 2:
                    pending = receipt_record()
                    assert pending["state"] == "dispatching"
                    assert pending["approval"]["body"]["wire"] == receipt_wire
                    assert pending["approval"]["body"]["receipt_id"] == receipt_id
                    serial_sends.append(bytes(data))
                return super().write(data, timeout)

        def receipt_outgoing(lose_ack=False):
            pending = receipt_record()
            stamp = pending["timestamp"] or receipt_time + pending["next_fragment"]
            piece = receipt_pieces[pending["next_fragment"]]
            acknowledgement = hashlib.sha256(struct.pack("<I", stamp) + b"\0" + piece.encode("ascii") + recipient_radio).digest()[:4]
            replies = handshake(recipient_radio) + inventory(sender_radio)
            if not lose_ack:
                replies += frame(b"\x06\x01" + acknowledgement + struct.pack("<I", 1000))
            device = MeshCoreClient(ReceiptSerial(replies, lose_ack), expected_public_key=recipient_radio)
            device.handshake()
            return device

        try:
            producer.dispatch_one(receipt_id, device=receipt_outgoing(lose_ack=True),
                                  radio_timestamp=receipt_time, access_check=receipt_access)
        except ReceiptSenderError:
            pass
        else:
            raise AssertionError("lost receipt acknowledgement did not remain uncertain")
        assert len(serial_sends) == 1
        uncertain = receipt_record()
        assert uncertain["state"] == "dispatching" and uncertain["next_fragment"] == 0
        assert uncertain["timestamp"] == receipt_time
        assert producer.close(timeout=1) is False
        recovery_fenced = True
        producer = new_receipt_sender()
        inspected = producer.inspect(receipt_id, access_check=receipt_access)
        assert inspected.state == "dispatching" and inspected.receipt_id == receipt_id
        assert receipt_record()["approval"]["body"]["wire"] == receipt_wire
        producer.retry_decision(receipt_id, expected_revision=inspected.revision,
                                decision_binding="synthetic-old-receipt-worker-fenced", access_check=receipt_access)
        assert receipt_record()["timestamp"] == receipt_time
        accepted_sends = []
        for index, _ in enumerate(receipt_pieces):
            progress = producer.dispatch_one(receipt_id, device=receipt_outgoing(),
                                             radio_timestamp=receipt_time + index, access_check=receipt_access)
            assert progress.next_fragment == index + 1
            accepted_sends.append(serial_sends[-1])
        assert progress.state == "serial_accepted"
        assert accepted_sends[0] == serial_sends[0]
        assert receipt_record()["approval"]["body"]["wire"] == receipt_wire
        receipt_frames = handshake(sender_radio)
        for sent in accepted_sends:
            payload = sent[3:]
            assert payload[:3] == b"\x02\x00\x00" and payload[7:13] == sender_radio[:6]
            stamp, piece = payload[3:7], payload[13:].decode("ascii")
            receipt_frames += frame(b"\x07" + recipient_radio[:6] + b"\0\0" + stamp + piece.encode("ascii"))
        receipt_device = MeshCoreClient(SyntheticSerial(receipt_frames), expected_public_key=sender_radio)
        receipt_device.handshake()
        receipt_receiver = MeshReceiver(sender_store, receipt_device, slot_id=str(uuid.uuid4()),
                                        access_check=lambda: True, storage_check=lambda: True)
        receipt_inbox = MeshInbox(sender_store, recipient=sender, recipient_signing_key=first._signing_key,
                                 resolve_peer=lambda message: recipient if message.sender_prefix == recipient_radio[:6] else None,
                                 access_check=lambda: True, storage_check=lambda: True)
        for _ in receipt_pieces:
            assert receipt_receiver.receive_next() == "captured"
            assert receipt_receiver.process_pending(receipt_inbox) is True
        received_receipt = sender_store.get(inbox_id(recipient, receipt_id), kind="mesh_inbound",
                                            access_check=lambda: True)["payload"]["wire"]
        assert received_receipt == receipt_wire
        assert outbox.inspect(message_id, access_check=lambda: True).state == "awaiting_receipt"
        assert outbox.ingest_receipt(message_id, wire=received_receipt, access_check=lambda: True).state == "read"
        assert outbox.ingest_receipt(message_id, wire=received_receipt, access_check=lambda: True).state == "read"
        assert outbox.read_sent(message_id, access_check=lambda: True).content == opened.content
        try:
            recipient_store.get(message_id, kind="mesh_outbound", access_check=lambda: True)
        except WorkflowNotFound:
            pass
        else:
            raise AssertionError("cross-store message visible")
        assert receiver.close(timeout=1) is True
        assert receipt_receiver.close(timeout=1) is True
        assert producer.close(timeout=1) is False
        assert outbox.close(timeout=1) is True
        print(json.dumps(dict(real_sage_api=True, encrypted_stores=2, simulated_radio=True,
                              fragments=len(pieces), receipt_fragments=len(receipt_pieces),
                              reconstructed_handler=True, correlated_read_receipt=True, receipt_sender=True,
                              receipt_sender_recovery=True,
                              private_media_roundtrip=True, stale_control_guard_denied=True,
                              unsubmitted_proposal_cancelled=True, protected_photo_history=True,
                              protected_history_factory=True, protected_history_queued_release=True,
                              fixture_auth_epoch_synthetic=True)))
    finally:
        clients.close()


if __name__ == "__main__":
    main()
