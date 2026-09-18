"""The owner's 24-hour approval window, through every cap that gates it.

Owner decision 2026-09-13: an explicitly approved message carries a 24-hour
window and is retryable inside it. Four separate ceilings have to agree for that
to be true, because each is enforced by a different party:

* the owner-issued contact card (``mesh_contacts.MAX_TTL_SECONDS``),
* the owner-supplied card TTL on the root route (``mesh_contact_forwarding``),
* the signed message approval (``mesh_outbox._validate``),
* the sealed wire envelope, re-checked with the *peer's* clock on receipt
  (``mesh_envelope.MAX_LIFETIME_SECONDS``).

Raising any one alone fails closed somewhere else, which is what this module
exists to catch.
"""

import pytest
from nacl.signing import SigningKey

from sage_lantern import mesh_contact_forwarding, mesh_contacts, mesh_envelope, mesh_outbox
from sage_lantern.mesh_contact_issuance import ContactIssuanceUnavailable
from sage_lantern.mesh_contacts import (
    MAX_WINDOW_SECONDS, MeshContactError, sign_contact_card, verify_contact_card,
)
from sage_lantern.mesh_envelope import MeshEnvelopeError, Peer, open_envelope, seal_message
from sage_lantern.message_contacts import RecipientID
from test_community_mesh_service import setup as community_setup
from test_mesh_contacts import identity as sdk_identity
from test_mesh_mailbox import setup as mailbox_setup  # noqa: F401  (community_setup depends on it)
from test_mesh_outbox import MESSAGE, setup as outbox_setup


SENDER_KEY = SigningKey(b's' * 32)
RECIPIENT_KEY = SigningKey(b'p' * 32)
RADIO_KEY = SigningKey(b'r' * 32).verify_key.encode()


def test_every_mesh_artifact_cap_is_one_shared_day():
    assert MAX_WINDOW_SECONDS == 24 * 60 * 60
    assert mesh_contacts.MAX_TTL_SECONDS is MAX_WINDOW_SECONDS
    assert mesh_envelope.MAX_LIFETIME_SECONDS is MAX_WINDOW_SECONDS
    # the outbox imports that same object rather than keeping its own copy
    assert mesh_outbox.MAX_WINDOW_SECONDS is MAX_WINDOW_SECONDS


def test_owner_supplied_card_ttl_accepts_a_day_and_refuses_a_second_more():
    forwarded = dict(token='a' * 43, findable=True, messaging_allowed=True)
    mesh_contact_forwarding.validate_payload(
        '/contacts/preview', {**forwarded, 'ttl_seconds': MAX_WINDOW_SECONDS})
    with pytest.raises(ContactIssuanceUnavailable):
        mesh_contact_forwarding.validate_payload(
            '/contacts/preview', {**forwarded, 'ttl_seconds': MAX_WINDOW_SECONDS + 1})


def test_contact_card_accepts_a_day_and_refuses_a_second_more(sdk_identity):
    def card(seconds):
        return sign_contact_card(sdk_identity, chain_id='test-home', radio_public_key=RADIO_KEY.hex(),
                                 display_name='Test peer', revision=1, issued_at=1000,
                                 expires_at=1000 + seconds, now=1000, findable=True, messaging_allowed=True)

    verified = verify_contact_card(card(MAX_WINDOW_SECONDS), now=1000 + MAX_WINDOW_SECONDS - 1)
    assert verified.expires_at == 1000 + MAX_WINDOW_SECONDS
    with pytest.raises(MeshContactError):
        card(MAX_WINDOW_SECONDS + 1)


def test_a_day_long_envelope_opens_on_the_peer_clock_at_the_edge():
    sender = Peer('chain-a', bytes(SENDER_KEY.verify_key))
    recipient = Peer('chain-b', bytes(RECIPIENT_KEY.verify_key))
    sealed = seal_message(SENDER_KEY, sender=sender, recipient=recipient,
                          message_id='00000000-0000-4000-8000-000000000042',
                          content='synthetic pending message', issued_at=1000,
                          expires_at=1000 + MAX_WINDOW_SECONDS)
    opened = open_envelope(sealed.wire, RECIPIENT_KEY, expected_sender=sender,
                           expected_recipient=recipient, now=1000 + MAX_WINDOW_SECONDS - 1)
    assert opened.content == 'synthetic pending message'
    # one second past the window the peer refuses it, as it must
    with pytest.raises(MeshEnvelopeError):
        open_envelope(sealed.wire, RECIPIENT_KEY, expected_sender=sender,
                      expected_recipient=recipient, now=1000 + MAX_WINDOW_SECONDS)
    # and an envelope longer than the window can never be sealed in the first place
    with pytest.raises(MeshEnvelopeError):
        seal_message(SENDER_KEY, sender=sender, recipient=recipient,
                     message_id='00000000-0000-4000-8000-000000000043',
                     content='synthetic pending message', issued_at=1000,
                     expires_at=1000 + MAX_WINDOW_SECONDS + 1)


def test_signed_approval_accepts_a_full_day_and_refuses_a_second_more(outbox_setup):
    state = outbox_setup

    def queue(seconds):
        return state.outbox.queue(message_id=MESSAGE, recipient=state.peer,
            radio_public_key=state.radio_key, content='Synthetic private content',
            approval_binding='explicit-open-1', consent_binding='consent-1',
            issued_at=100, expires_at=100 + seconds, access_check=state.access)

    # a full-day approval is accepted, and still validates re-read from the journal
    assert queue(MAX_WINDOW_SECONDS).state == 'queued'
    state.outbox = state.make()
    assert state.outbox.inspect(MESSAGE, access_check=state.access).state == 'queued'
    # one second more is refused before any write
    state.outbox = state.make()
    with pytest.raises(mesh_outbox.MeshOutboxError):
        queue(MAX_WINDOW_SECONDS + 1)


def test_owner_send_window_spans_a_day_when_the_card_allows(community_setup):
    state = community_setup
    raw = sign_contact_card(state.peer_identity, chain_id=state.peer.chain_id,
        radio_public_key=state.card.radio_public_key, display_name='Synthetic peer', revision=2,
        issued_at=100, expires_at=100 + MAX_WINDOW_SECONDS, now=state.now,
        findable=True, messaging_allowed=True)
    state.registry.put(raw, known_actor=RecipientID(state.peer.chain_id, state.peer_identity.agent_id),
                       access_check=state.access)
    body = state.send_body()
    assert state.call('messages/send', body)['state'] == 'queued'
    approved = state.rows[body['request_id']]['payload']['body']['approved']
    # min(now + 24 h, card.expires_at) -- the card is the shorter of the two here
    assert approved['expires_at'] == 100 + MAX_WINDOW_SECONDS
    assert approved['expires_at'] - approved['issued_at'] == MAX_WINDOW_SECONDS - 50
