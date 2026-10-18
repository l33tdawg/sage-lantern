"""Versioned attempt identity contracts using synthetic Ed25519 identities."""

import base64
import hashlib
import json

from nacl.public import SealedBox
from nacl.signing import SigningKey
import pytest

from sage_lantern.mesh_envelope import (
    ATTEMPT_SCHEMA, ATTEMPT_SIGN_DOMAIN, ATTEMPT_WIRE_PREFIX, MAX_CONTENT_BYTES,
    MAX_INNER_BYTES, MAX_LIFETIME_SECONDS, MAX_WIRE_BYTES, MeshEnvelopeError, Peer,
    SCHEMA, SIGN_DOMAIN, WIRE_PREFIX, correlate_receipt, message_digest,
    open_envelope, seal_delivery_receipt, seal_message, seal_read_receipt,
)


KEY = SigningKey(b'a'*32)
OTHER_KEY = SigningKey(b'b'*32)
SENDER = Peer('synthetic-a', bytes(KEY.verify_key))
RECIPIENT = Peer('synthetic-b', bytes(OTHER_KEY.verify_key))
MESSAGE = '12345678-1234-4234-8234-123456789abc'
ATTEMPT = '22345678-1234-4234-8234-123456789abc'
SECOND_ATTEMPT = '32345678-1234-4234-8234-123456789abc'
RECEIPT = '42345678-1234-4234-8234-123456789abc'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def seal(**changes):
    fields = dict(sender=SENDER, recipient=RECIPIENT, message_id=MESSAGE,
        content='Synthetic private payload', issued_at=1000, expires_at=1600,
        attempt_id=ATTEMPT, logical_issued_at=100, logical_expires_at=1000)
    fields.update(changes)
    return seal_message(KEY, **fields)


def opened(wire, **changes):
    fields = dict(expected_sender=SENDER, expected_recipient=RECIPIENT, now=1100)
    fields.update(changes)
    return open_envelope(wire, OTHER_KEY, **fields)


def decrypted_inner(wire):
    cipher = base64.b64decode(wire[5:], validate=True)
    return SealedBox(OTHER_KEY.to_curve25519_private_key()).decrypt(cipher)


def signed_wire(body, *, domain=ATTEMPT_SIGN_DOMAIN, prefix=ATTEMPT_WIRE_PREFIX, raw=None):
    signature = base64.b64encode(KEY.sign(domain + canonical(body)).signature).decode('ascii')
    inner = canonical(dict(body=body, signature=signature)) if raw is None else raw
    cipher = SealedBox(OTHER_KEY.verify_key.to_curve25519_public_key()).encrypt(inner)
    return prefix + base64.b64encode(cipher).decode('ascii')


def test_original_and_distinct_attempts_keep_logical_identity_but_have_distinct_commitments():
    original = seal_message(KEY, sender=SENDER, recipient=RECIPIENT, message_id=MESSAGE,
        content='Synthetic private payload', issued_at=100, expires_at=1000)
    legacy = opened(original.wire, now=110)
    first, randomized = seal(), seal()
    second = seal(attempt_id=SECOND_ATTEMPT, issued_at=1600, expires_at=2200)
    first_opened = opened(first.wire)
    second_opened = opened(second.wire, now=1600)
    assert original.wire.startswith(WIRE_PREFIX) and first.wire.startswith(ATTEMPT_WIRE_PREFIX)
    assert first.wire != randomized.wire and first.digest == randomized.digest
    assert legacy.attempt_id is None and legacy.logical_issued_at is None and legacy.logical_expires_at is None
    assert first_opened.attempt_id == ATTEMPT and second_opened.attempt_id == SECOND_ATTEMPT
    assert (first_opened.logical_issued_at, first_opened.logical_expires_at) == (100, 1000)
    assert legacy.replay_identity == first_opened.replay_identity == second_opened.replay_identity
    assert legacy.logical_digest == first_opened.logical_digest == second_opened.logical_digest == original.digest
    assert len({original.digest, first.digest, second.digest}) == 3
    assert 'Synthetic' not in repr(first_opened) and first.wire not in repr(first)


def test_attempt_commitment_is_signed_over_attempt_and_original_times():
    sealed = seal()
    value = json.loads(decrypted_inner(sealed.wire))
    assert value['body']['schema'] == ATTEMPT_SCHEMA
    signed = base64.b64decode(value['signature'])
    KEY.verify_key.verify(ATTEMPT_SIGN_DOMAIN + canonical(value['body']), signed)
    assert hashlib.sha256(canonical(value['body'])).hexdigest() == sealed.digest
    assert message_digest(sender=SENDER, recipient=RECIPIENT, message_id=MESSAGE,
        content='Synthetic private payload', issued_at=100, expires_at=1000) == opened(sealed.wire).logical_digest


@pytest.mark.parametrize('change', [dict(content='Changed signed payload'), dict(logical_issued_at=99),
    dict(logical_expires_at=999), dict(recipient=Peer('different-chain', RECIPIENT.public_key))])
def test_content_or_original_anchor_change_changes_logical_commitment(change):
    original = opened(seal().wire)
    envelope = seal(**change)
    result = opened(envelope.wire, expected_recipient=change.get('recipient', RECIPIENT))
    assert result.replay_identity == original.replay_identity
    assert result.logical_digest != original.logical_digest and result.digest != original.digest


@pytest.mark.parametrize('change', [dict(attempt_id=None), dict(logical_issued_at=None),
    dict(logical_expires_at=None), dict(attempt_id=MESSAGE), dict(attempt_id='not-uuid'),
    dict(attempt_id='00000000-0000-0000-0000-000000000000'), dict(attempt_id=ATTEMPT.upper()),
    dict(attempt_id=ATTEMPT.replace('-', '')), dict(attempt_id=True), dict(logical_issued_at=True),
    dict(logical_expires_at=True), dict(logical_issued_at=-1), dict(logical_issued_at=1000),
    dict(logical_expires_at=100), dict(logical_expires_at=1001),
    dict(logical_issued_at=0, logical_expires_at=MAX_LIFETIME_SECONDS+1,
         issued_at=MAX_LIFETIME_SECONDS+1, expires_at=MAX_LIFETIME_SECONDS+2),
    dict(issued_at=True), dict(expires_at=True), dict(expires_at=1000),
    dict(expires_at=1000+MAX_LIFETIME_SECONDS+1),
    dict(issued_at=2**53-1, expires_at=2**53), dict(message_id=MESSAGE.upper()),
    dict(content=''), dict(content='\n\t'), dict(content='x'*(MAX_CONTENT_BYTES+1)),
    dict(content='é'*257), dict(content='x\r'), dict(content='\ud800')])
def test_invalid_attempt_parameters_are_refused_at_sealing(change):
    with pytest.raises(MeshEnvelopeError, match='^invalid_mesh_envelope$'):
        seal(**change)


@pytest.mark.parametrize('now,accepted', [(999, False), (1000, True), (1599, True), (1600, False),
    (True, False), (-1, False), (2**53, False), ('1100', False)])
def test_attempt_uses_current_window_with_exact_clock_boundaries(now, accepted):
    wire = seal().wire
    if accepted:
        assert opened(wire, now=now).issued_at == 1000
    else:
        with pytest.raises(MeshEnvelopeError):
            opened(wire, now=now)


def test_original_expiry_can_equal_attempt_issue_and_maximum_timestamp_is_supported():
    ceiling = 2**53-1
    envelope = seal(issued_at=ceiling-1, expires_at=ceiling,
        logical_issued_at=ceiling-2, logical_expires_at=ceiling-1)
    result = opened(envelope.wire, now=ceiling-1)
    assert result.expires_at == ceiling and result.logical_expires_at == result.issued_at
    with pytest.raises(MeshEnvelopeError):
        opened(envelope.wire, now=ceiling)


@pytest.mark.parametrize('mutation', ['missing_attempt', 'missing_logical', 'extra', 'v1_schema',
    'future_anchor', 'bad_uuid', 'receipt_kind', 'wrong_sender', 'wrong_recipient'])
def test_freshly_signed_malformed_attempt_body_is_refused(mutation):
    body = json.loads(decrypted_inner(seal().wire))['body']
    if mutation == 'missing_attempt':
        del body['attempt_id']
    elif mutation == 'missing_logical':
        del body['logical_expires_at']
    elif mutation == 'extra':
        body['priority'] = 'interactive'
    elif mutation == 'v1_schema':
        body['schema'] = SCHEMA
    elif mutation == 'future_anchor':
        body['logical_expires_at'] = body['issued_at']+1
    elif mutation == 'bad_uuid':
        body['attempt_id'] = MESSAGE
    elif mutation == 'receipt_kind':
        body.update(kind='delivery', content=None, original_id=RECEIPT, original_digest='f'*64)
    else:
        body['sender' if mutation == 'wrong_sender' else 'recipient']['chain_id'] = 'wrong'
    with pytest.raises(MeshEnvelopeError):
        opened(signed_wire(body))


@pytest.mark.parametrize('domain,prefix', [(SIGN_DOMAIN, ATTEMPT_WIRE_PREFIX),
    (ATTEMPT_SIGN_DOMAIN, WIRE_PREFIX), (SIGN_DOMAIN, WIRE_PREFIX),
    (b'other-domain\0', ATTEMPT_WIRE_PREFIX)])
def test_attempt_rejects_version_prefix_or_signature_domain_confusion(domain, prefix):
    body = json.loads(decrypted_inner(seal().wire))['body']
    with pytest.raises(MeshEnvelopeError):
        opened(signed_wire(body, domain=domain, prefix=prefix))


@pytest.mark.parametrize('fault', ['duplicate_body', 'noncanonical', 'oversize_inner', 'extra_wrapper',
    'bad_signature', 'truncated', 'base64_newline', 'bad_prefix'])
def test_malformed_attempt_wire_is_refused(fault):
    wire = seal().wire
    raw = decrypted_inner(wire)
    value = json.loads(raw)
    if fault == 'duplicate_body':
        wire = signed_wire(value['body'], raw=b'{"body":{},'+raw[1:])
    elif fault == 'noncanonical':
        wire = signed_wire(value['body'], raw=json.dumps(value, indent=2).encode())
    elif fault == 'oversize_inner':
        wire = signed_wire(value['body'], raw=b' '*(MAX_INNER_BYTES+1))
    elif fault == 'extra_wrapper':
        value['extra'] = False
        wire = signed_wire(value['body'], raw=canonical(value))
    elif fault == 'bad_signature':
        value['signature'] = base64.b64encode(bytes(64)).decode()
        wire = signed_wire(value['body'], raw=canonical(value))
    elif fault == 'truncated':
        wire = wire[:-8]
    elif fault == 'base64_newline':
        wire += '\n'
    else:
        wire = 'LME3.'+wire[5:]
    with pytest.raises(MeshEnvelopeError):
        opened(wire)


@pytest.mark.parametrize('kind', ['delivery', 'read'])
def test_legacy_receipt_can_assert_specific_attempt_digest_without_changing_logical_identity(kind):
    attempt = opened(seal().wire)
    fields = dict(sender=RECIPIENT, recipient=SENDER, message_id=RECEIPT, original_id=MESSAGE,
        original_digest=attempt.digest, issued_at=1100, expires_at=2000)
    receipt = (seal_delivery_receipt(OTHER_KEY, **fields) if kind == 'delivery' else
        seal_read_receipt(OTHER_KEY, **fields, explicit_user_read=True))
    assert receipt.wire.startswith(WIRE_PREFIX)
    result = open_envelope(receipt.wire, KEY, expected_sender=RECIPIENT, expected_recipient=SENDER, now=1200)
    history = dict(original_sender=SENDER, original_recipient=RECIPIENT, original_id=MESSAGE,
        original_digest=attempt.digest, original_issued_at=attempt.issued_at)
    assert correlate_receipt(result, **history) == ('peer_delivery_asserted' if kind == 'delivery' else 'peer_read_asserted')
    assert result.attempt_id is None
    with pytest.raises(MeshEnvelopeError):
        correlate_receipt(result, **dict(history, original_digest=attempt.logical_digest))


@pytest.mark.parametrize('content', ['é'*256, 'x'+'\t'*511, 'x'+'\\'*511, 'x'+'"'*511])
def test_maximum_peer_names_and_content_fit_v2_without_truncation(content):
    sender = Peer('A'*128, SENDER.public_key)
    recipient = Peer('B'*128, RECIPIENT.public_key)
    ceiling = 2**53-1
    envelope = seal(sender=sender, recipient=recipient, content=content,
        issued_at=ceiling-1, expires_at=ceiling,
        logical_issued_at=ceiling-2, logical_expires_at=ceiling-1)
    result = opened(envelope.wire, expected_sender=sender, expected_recipient=recipient, now=ceiling-1)
    assert result.content == content and len(content.encode('utf-8')) == MAX_CONTENT_BYTES
    assert len(decrypted_inner(envelope.wire)) <= MAX_INNER_BYTES == 2048
    assert envelope.encoded_bytes <= MAX_WIRE_BYTES
