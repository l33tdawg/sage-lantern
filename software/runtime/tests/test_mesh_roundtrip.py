import hashlib
import struct
import uuid

from nacl.signing import SigningKey
import pytest

from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.mesh_envelope import (
    MeshEnvelopeError, Peer, correlate_receipt, open_envelope, seal_message, seal_read_receipt,
)
from sage_lantern.mesh_fragments import FragmentAssembler, split_envelope
from sage_lantern.meshcore_transport import MeshCoreClient
from test_mesh_contacts import identity
from test_meshcore_transport import SerialDouble, frame, handshake_bytes


NOW = 1700000000


def serial_roundtrip(wire, *, sender_radio, receiver_radio, started_at=NOW):
    pieces = split_envelope(wire.encode("ascii"))
    replies = handshake_bytes(key=sender_radio)
    incoming = handshake_bytes(key=receiver_radio)
    for index, piece in enumerate(pieces):
        stamp = struct.pack("<I", started_at + index)
        acknowledgement = hashlib.sha256(stamp + b"\0" + piece.encode("ascii") + sender_radio).digest()[:4]
        replies += frame(b"\x06\x01" + acknowledgement + struct.pack("<I", 1000))
        incoming += frame(b"\x07" + sender_radio[:6] + b"\0\0" + stamp + piece.encode("ascii"))
    sender = MeshCoreClient(SerialDouble(replies), expected_public_key=sender_radio)
    receiver = MeshCoreClient(SerialDouble(incoming), expected_public_key=receiver_radio)
    sender.handshake()
    receiver.handshake()
    assembler = FragmentAssembler()
    persisted_frames = []
    result = None
    for index, piece in enumerate(pieces):
        receipt = sender.send_text(recipient_prefix=receiver_radio[:6], text=piece,
                                   timestamp=started_at + index, authorize=lambda: True)
        assert receipt.serial_accepted and not receipt.delivered and not receipt.read
        message = receiver.receive_next(authorize=lambda: True,
                                        persist=lambda value: persisted_frames.append(value.raw_frame) or True)
        assert message.raw_frame == persisted_frames[-1]
        assert message.sender_prefix == sender_radio[:6]
        assert not message.application_authenticated and not message.delivered and not message.read
        result = assembler.accept(sender_radio, message.payload.decode("ascii"))
    assert len(persisted_frames) == len(pieces)
    assert result == wire.encode("ascii")
    return result.decode("ascii")


def test_two_private_chains_contact_message_and_correlated_receipt(identity):
    other = type(identity).from_seed(b"b" * 32)
    sender = Peer("Village:A", bytes(identity._signing_key.verify_key))
    recipient = Peer("Village:B", bytes(other._signing_key.verify_key))
    sender_radio = bytes(SigningKey(b"r" * 32).verify_key)
    receiver_radio = bytes(SigningKey(b"s" * 32).verify_key)
    card = sign_contact_card(other, chain_id=recipient.chain_id, radio_public_key=receiver_radio.hex(),
                             display_name="Lantern B", revision=1, issued_at=NOW,
                             expires_at=NOW + 3600, now=NOW, findable=True, messaging_allowed=True)
    contact = verify_contact_card(card, now=NOW)
    assert contact.messaging_allowed and contact.findable
    assert contact.agent_id == recipient.public_key.hex()
    sealed = seal_message(identity._signing_key, sender=sender, recipient=recipient,
                          message_id=str(uuid.uuid4()), content="Can we meet tomorrow?",
                          issued_at=NOW, expires_at=NOW + 3600)
    received = serial_roundtrip(sealed.wire, sender_radio=sender_radio, receiver_radio=receiver_radio)
    opened = open_envelope(received, other._signing_key, expected_sender=sender,
                           expected_recipient=recipient, now=NOW + 60)
    assert opened.content == "Can we meet tomorrow?"
    receipt = seal_read_receipt(other._signing_key, sender=recipient, recipient=sender,
                                message_id=str(uuid.uuid4()), original_id=opened.message_id,
                                original_digest=opened.digest, issued_at=NOW + 61,
                                expires_at=NOW + 3600, explicit_user_read=True)
    returned = serial_roundtrip(receipt.wire, sender_radio=receiver_radio, receiver_radio=sender_radio,
                                started_at=NOW + 61)
    assertion = open_envelope(returned, identity._signing_key, expected_sender=recipient,
                              expected_recipient=sender, now=NOW + 120)
    assert correlate_receipt(assertion, original_sender=sender, original_recipient=recipient,
                             original_id=sealed.message_id, original_digest=sealed.digest,
                             original_issued_at=NOW) == "peer_read_asserted"


def test_radio_acceptance_does_not_grant_wrong_recipient_decryption(identity):
    recipient_key = SigningKey(b"b" * 32)
    wrong_key = SigningKey(b"c" * 32)
    sender = Peer("A", bytes(identity._signing_key.verify_key))
    recipient = Peer("B", bytes(recipient_key.verify_key))
    sealed = seal_message(identity._signing_key, sender=sender, recipient=recipient,
                          message_id=str(uuid.uuid4()), content="Private", issued_at=NOW, expires_at=NOW + 3600)
    wire = serial_roundtrip(sealed.wire, sender_radio=bytes(SigningKey(b"r" * 32).verify_key),
                            receiver_radio=bytes(SigningKey(b"s" * 32).verify_key))
    with pytest.raises(MeshEnvelopeError):
        open_envelope(wire, wrong_key, expected_sender=sender,
                      expected_recipient=Peer("B", bytes(wrong_key.verify_key)), now=NOW + 60)
