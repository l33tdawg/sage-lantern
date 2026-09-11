import importlib
import json
from pathlib import Path
import sys

from nacl.signing import SigningKey, VerifyKey
import pytest

from sage_lantern import mesh_contacts as contacts
from sage_lantern.message_contacts import ContactError, RecipientCache, RecipientID


@pytest.fixture
def identity(monkeypatch):
    source = Path(__file__).resolve().parents[4] / 'sage/sdk/python/src'
    monkeypatch.syspath_prepend(str(source))
    previous = {name: module for name, module in sys.modules.items()
                if name == 'sage_sdk' or name.startswith('sage_sdk.')}
    for name in previous:
        del sys.modules[name]
    try:
        sdk = importlib.import_module('sage_sdk')
        assert Path(sdk.__file__).resolve().is_relative_to(source.resolve())
        yield sdk.AgentIdentity.from_seed(bytes(range(32)))
    finally:
        for name in list(sys.modules):
            if name == 'sage_sdk' or name.startswith('sage_sdk.'):
                del sys.modules[name]
        sys.modules.update(previous)


@pytest.fixture
def fields():
    return dict(chain_id='test-home', radio_public_key=SigningKey(b'r' * 32).verify_key.encode().hex(),
                display_name='Test café', revision=1, issued_at=100, expires_at=160, now=110)


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def test_real_sdk_roundtrip_optout_and_exact_actor(identity, fields):
    card = contacts.sign_contact_card(identity, **fields)
    actor = RecipientID(fields['chain_id'], identity.agent_id)
    verified = contacts.verify_contact_card(card, now=110, known_actor=actor)
    assert verified.recipient == actor
    assert verified.radio_public_key == fields['radio_public_key']
    assert verified.findable is False and verified.messaging_allowed is False
    assert len(card) <= contacts.MAX_CARD_BYTES
    assert card == encode(json.loads(card))
    assert fields['display_name'] not in repr(verified)
    with pytest.raises(contacts.MeshContactError):
        contacts.verify_contact_card(card, now=110, known_actor=RecipientID('other-chain', identity.agent_id))
    with pytest.raises(contacts.MeshContactError):
        contacts.verify_contact_card(card, now=110, known_actor=RecipientID('test-home', 'b' * 64))


def test_domain_separation_and_independent_verification(identity, fields):
    import hashlib
    import struct

    card = contacts.sign_contact_card(identity, **fields, findable=True, messaging_allowed=True)
    envelope = json.loads(card)
    body = encode(envelope['payload'])
    canonical = b'LANTERN-CONTACT /sage-lantern/mesh-contact/v1\n' + body
    signed = (hashlib.sha256(canonical).digest() + struct.pack('>q', 100)
              + bytes.fromhex(envelope['nonce']))
    VerifyKey(bytes.fromhex(identity.agent_id)).verify(signed, bytes.fromhex(envelope['signature']))
    for method, path in [('POST', contacts.SIGN_PATH), (contacts.SIGN_METHOD, '/v1/messages')]:
        headers = identity.sign_request(method, path, body, timestamp=100)
        forged = encode(dict(payload=envelope['payload'], nonce=headers['X-Nonce'], signature=headers['X-Signature']))
        with pytest.raises(contacts.MeshContactError):
            contacts.verify_contact_card(forged, now=110)


@pytest.mark.parametrize('field,value', [
    ('chain_id', 'other-home'), ('agent_id', SigningKey(b'a' * 32).verify_key.encode().hex()),
    ('radio_public_key', SigningKey(b'z' * 32).verify_key.encode().hex()),
    ('display_name', 'Someone else'), ('revision', 2), ('issued_at', 101), ('expires_at', 159),
    ('findable', True), ('messaging_allowed', True), ('schema', 'sage.lantern.mesh-contact.v2'),
])
def test_every_payload_field_is_bound(identity, fields, field, value):
    envelope = json.loads(contacts.sign_contact_card(identity, **fields))
    envelope['payload'][field] = value
    with pytest.raises(contacts.MeshContactError, match='^mesh_contact_unavailable$'):
        contacts.verify_contact_card(encode(envelope), now=110)


@pytest.mark.parametrize('change', [
    {'revision': True}, {'revision': -1}, {'revision': 1.0}, {'revision': 1 << 53},
    {'issued_at': False}, {'issued_at': 0}, {'issued_at': 111}, {'expires_at': 110},
    {'expires_at': 3701}, {'expires_at': '160'}, {'findable': 1}, {'messaging_allowed': None},
    {'chain_id': '../home'}, {'chain_id': ''}, {'chain_id': 'a' * 129},
    {'display_name': ''}, {'display_name': ' padded'}, {'display_name': 'bad\nname'},
    {'display_name': 'bad\u202ename'}, {'display_name': 'cafe\u0301'}, {'display_name': 'a' * 129},
    {'radio_public_key': '00' * 32}, {'radio_public_key': 'aa' * 6},
    {'now': True}, {'now': float('nan')}, {'now': float('inf')},
])
def test_invalid_values_rejected_before_signing(identity, fields, change, monkeypatch):
    called = []
    monkeypatch.setattr(identity, 'sign_request', lambda *args, **kwargs: called.append(True))
    with pytest.raises(contacts.MeshContactError, match='^mesh_contact_unavailable$'):
        contacts.sign_contact_card(identity, **(fields | change))
    assert called == []


def test_same_key_for_radio_refused(identity, fields):
    with pytest.raises(contacts.MeshContactError):
        contacts.sign_contact_card(identity, **(fields | {'radio_public_key': identity.agent_id}))


@pytest.mark.parametrize('chain_id', ['HOME', 'SAGE:Unit_A-1.test', 'A' * 128, '.', ':'])
def test_chain_matches_recipient_id_without_normalization(identity, fields, chain_id):
    actor = RecipientID(chain_id, identity.agent_id)
    card = contacts.sign_contact_card(identity, **(fields | {'chain_id': chain_id}))
    verified = contacts.verify_contact_card(card, now=110, known_actor=actor)
    assert verified.recipient == actor
    assert json.loads(card)['payload']['chain_id'] == chain_id
    if chain_id.lower() != chain_id:
        with pytest.raises(contacts.MeshContactError):
            contacts.verify_contact_card(card, now=110,
                                        known_actor=RecipientID(chain_id.lower(), identity.agent_id))


def test_limits_and_expiry(identity, fields):
    card = contacts.sign_contact_card(identity, **(fields | {'revision': contacts.MAX_INTEGER, 'expires_at': 3700}))
    assert contacts.verify_contact_card(card, now=3699).revision == contacts.MAX_INTEGER
    for now in (99, 3700, True, float('nan'), float('inf'), -1):
        with pytest.raises(contacts.MeshContactError):
            contacts.verify_contact_card(card, now=now)


def test_strict_envelope_duplicate_fields_and_wire_bounds(identity, fields):
    card = contacts.sign_contact_card(identity, **fields)
    envelope = json.loads(card)
    malformed = [
        card.decode(), b'', b'\xff', b' ' * (contacts.MAX_CARD_BYTES + 1), card + b'\n',
        card.replace(b'"revision":1', b'"revision":1,"revision":1'),
        card.replace(b'"nonce":', b'"nonce":"0000000000000000","nonce":', 1),
        card.replace(b'"revision":1', b'"revision":NaN'),
        card.replace(b'"revision":1', b'"revision":true'),
        encode(envelope | {'extra': True}), encode({'payload': envelope['payload']}),
        encode(envelope | {'signature': '0' * 128}), encode(envelope | {'nonce': '0' * 14}),
        encode(envelope | {'nonce': envelope['nonce'].upper() + 'A'}), b'[]',
        encode(envelope | {'payload': envelope['payload'] | {'extra': False}}),
    ]
    for raw in malformed:
        with pytest.raises(contacts.MeshContactError, match='^mesh_contact_unavailable$'):
            contacts.verify_contact_card(raw, now=110)


def test_signed_invalid_payload_still_rejected(identity, fields):
    envelope = json.loads(contacts.sign_contact_card(identity, **fields))
    for change in ({'revision': True}, {'expires_at': 3701}, {'extra': 1}, {'findable': 1}):
        payload = envelope['payload'] | change
        headers = identity.sign_request(contacts.SIGN_METHOD, contacts.SIGN_PATH, encode(payload), timestamp=100)
        card = encode(dict(payload=payload, nonce=headers['X-Nonce'], signature=headers['X-Signature']))
        with pytest.raises(contacts.MeshContactError):
            contacts.verify_contact_card(card, now=110)


def test_revision_revocation_rotation_and_claim_cache(identity, fields):
    enabled = contacts.sign_contact_card(identity, **fields, findable=True, messaging_allowed=True)
    first = contacts.verify_contact_card(enabled, now=110)
    disabled = contacts.sign_contact_card(identity, **(fields | {'revision': 2, 'issued_at': 111, 'now': 112}))
    revoked = contacts.verify_contact_card(disabled, now=112, previous=first)
    assert revoked.findable is False and revoked.messaging_allowed is False
    with pytest.raises(contacts.MeshContactError):
        contacts.verify_contact_card(enabled, now=112, previous=revoked)
    conflict = contacts.sign_contact_card(identity, **(fields | {'display_name': 'Changed'}),
                                          findable=True, messaging_allowed=True)
    with pytest.raises(contacts.MeshContactError):
        contacts.verify_contact_card(conflict, now=112, previous=first)
    rotated = contacts.sign_contact_card(identity, **(fields | {
        'radio_public_key': SigningKey(b'n' * 32).verify_key.encode().hex(),
        'revision': 3, 'issued_at': 112, 'now': 112,
    }), findable=True, messaging_allowed=True)
    current = contacts.verify_contact_card(rotated, now=112, previous=revoked)
    assert current.recipient == first.recipient
    assert current.radio_public_key != first.radio_public_key
    assert contacts.verify_contact_card(rotated, now=112, previous=current) == current
    caller = RecipientID('test-home', SigningKey(b'c' * 32).verify_key.encode().hex())
    cache = RecipientCache(caller=caller, issuer='self-consent', policy_binding='test-policy',
                           clock=lambda: 112, verifier=lambda raw: contacts.verify_contact_card(
                               raw, now=112, known_actor=first.recipient).contact_claim(
                                   caller=caller, issuer='self-consent', policy_binding='test-policy'))
    assert cache.put(enabled).recipient == first.recipient
    with pytest.raises(ContactError):
        cache.put(disabled)
    with pytest.raises(ContactError):
        cache.put(enabled)
    with pytest.raises(ContactError):
        cache.get(first.recipient)


def test_signer_failure_is_sanitized(identity, fields, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('secret diagnostic')
    monkeypatch.setattr(identity, 'sign_request', fail)
    with pytest.raises(contacts.MeshContactError, match='^mesh_contact_unavailable$') as failure:
        contacts.sign_contact_card(identity, **fields)
    assert failure.value.__suppress_context__ is True


def test_historical_signature_is_distinct_and_never_current_authority(identity, fields):
    card = contacts.sign_contact_card(identity, **fields, findable=True, messaging_allowed=True)
    historical = contacts.verify_contact_card_signature(card)
    assert type(historical) is contacts.HistoricalMeshContact
    assert not isinstance(historical, contacts.VerifiedMeshContact)
    assert not hasattr(historical, 'contact_claim')
    with pytest.raises(contacts.MeshContactError):
        contacts.verify_contact_card(card, now=200)
    assert contacts.verify_contact_card_signature(card) == historical
    next_card = contacts.sign_contact_card(identity, **(fields | {
        'revision': 2, 'issued_at': 200, 'expires_at': 260, 'now': 200}))
    assert contacts.verify_contact_card(next_card, now=200, previous=historical).revision == 2


def test_historical_never_skips_signature_shape_or_actor_checks(identity, fields):
    card = contacts.sign_contact_card(identity, **fields)
    for raw in (card.replace(b'"revision":1', b'"revision":2'),
                card.replace(b'"revision":1', b'"revision":1,"revision":1'), card + b'\n'):
        with pytest.raises(contacts.MeshContactError):
            contacts.verify_contact_card_signature(raw)
    with pytest.raises(contacts.MeshContactError):
        contacts.verify_contact_card_signature(card, known_actor=RecipientID('other', identity.agent_id))
