"""Real mesh/store adapters over synthetic SDK HTTP; no RF or live keys."""

import copy
import uuid
from dataclasses import replace

import pytest
from nacl.signing import SigningKey

from sage_lantern.community_service import CommunityError, CommunityMeshProvider, CommunityService
from sage_lantern.mesh_contact_registry import MeshContactRegistry
from sage_lantern.mesh_contacts import sign_contact_card
from sage_lantern.mesh_envelope import seal_delivery_receipt
from sage_lantern.meshcore_contacts import ContactInventory, RadioContact
from test_mesh_mailbox import setup as mailbox_setup


@pytest.fixture
def setup(mailbox_setup):
    state = mailbox_setup
    state.ready = True
    state.closed = []
    radio = SigningKey(b'r' * 32).verify_key.encode()
    state.registry = MeshContactRegistry(state.store, state.identity, owner=state.owner,
        inventory=lambda: ContactInventory((RadioContact(radio, 1, 0, 100),), 100),
        verify_binding=lambda owner, card, key: key == radio, clock=lambda: state.now)
    def put_card(revision=1, allowed=True):
        raw = sign_contact_card(state.peer_identity, chain_id=state.peer.chain_id,
            radio_public_key=radio.hex(), display_name='Synthetic peer', revision=revision,
            issued_at=100, expires_at=1000, now=state.now, findable=allowed,
            messaging_allowed=allowed)
        from sage_lantern.message_contacts import RecipientID
        return state.registry.put(raw, known_actor=RecipientID(state.peer.chain_id,
            state.peer_identity.agent_id), access_check=state.access)
    state.put_card = put_card
    state.card = put_card()
    def close(*, timeout):
        state.closed.append(timeout)
        return True
    state.provider = CommunityMeshProvider(state.mailbox, state.registry,
        runtime_ready=lambda **kwargs: state.ready, runtime_close=close, clock=lambda: state.now)
    state.service = CommunityService(mesh_provider=state.provider)
    state.session = 'synthetic-session'
    state.call = lambda route, body=None: state.service.dispatch('/community/' + route,
        {} if body is None else body, state.access, session_id=state.session)
    def send(text='Synthetic sent'):
        contact = state.call('contacts/list')['items'][0]['id']
        return dict(recipient_id=contact, paragraphs=[text], request_id=str(uuid.uuid4()), consent=True)
    state.send_body = send
    return state


def test_radio_directory_preserves_full_verified_binding(setup):
    before = copy.deepcopy(setup.rows)
    contact = setup.call('contacts/radio')['items'][0]
    assert contact['radio_public_key'] == setup.card.radio_public_key
    assert contact['agent_id'] == setup.card.agent_id
    assert contact['chain_id'] == setup.card.chain_id
    assert contact['expires_at'] == setup.card.expires_at
    assert set(setup.call('contacts/list')['items'][0]) == {'id', 'name'}
    assert setup.rows == before


def test_status_directory_and_durable_queue_contract(setup):
    assert setup.call('status')['messaging'] == dict(available=True, reason='ready')
    body = setup.send_body()
    assert body['recipient_id'].startswith('mesh-contact:')
    assert setup.peer_identity.agent_id not in body['recipient_id']
    result = setup.call('messages/send', body)
    assert set(result) == {'id', 'state', 'created_at'} and result['state'] == 'queued'
    assert result['created_at'] == '1970-01-01T00:02:30Z'
    assert body['request_id'] in setup.rows
    listing = setup.call('messages/list', {'box': 'outbox'})
    assert listing['items'][0]['text'] == 'Synthetic sent'
    assert listing['items'][0]['peer_name'] == setup.peer.chain_id + ':' + setup.peer_identity.agent_id
    assert all(path.startswith('/v1/workflows') or path == '/v1/messages/storage'
               for _, path in setup.calls)


@pytest.mark.parametrize('text,accepted', [('a' * 512, True), ('é' * 256, True),
                                         ('a' * 513, False), ('é' * 257, False)])
def test_utf8_bound_before_mutation(setup, text, accepted):
    body = setup.send_body(text)
    before = copy.deepcopy(setup.rows)
    if accepted:
        assert setup.call('messages/send', body)['state'] == 'queued'
    else:
        with pytest.raises(CommunityError):
            setup.call('messages/send', body)
        assert setup.rows == before


@pytest.mark.parametrize('change', ['revoke', 'session', 'lock', 'not_ready'])
def test_send_rechecks_current_consent_session_and_runtime(setup, change):
    body = setup.send_body()
    if change == 'revoke': setup.put_card(2, False)
    if change == 'session': setup.session = 'another-session'
    if change == 'lock': setup.service.lock()
    if change == 'not_ready': setup.ready = False
    before = copy.deepcopy(setup.rows)
    with pytest.raises(CommunityError):
        setup.call('messages/send', body)
    assert setup.rows == before


def test_listing_open_no_claim_or_read_receipt(setup):
    setup.add_inbox()
    before = copy.deepcopy(setup.rows)
    item = setup.call('messages/list', {'box': 'inbox'})['items'][0]
    assert item['state'] == 'unknown'
    setup.calls.clear()
    result = setup.call('messages/open', {'id': item['id'], 'share_read_receipt': False})
    assert result['state'] == 'unknown'
    assert not any(path == '/v1/workflows' for _, path in setup.calls)
    with pytest.raises(CommunityError):
        setup.call('messages/open', {'id': item['id'], 'share_read_receipt': True})
    assert setup.rows == before


@pytest.mark.parametrize('change', ['auth', 'lease', 'epoch', 'tamper'])
def test_unverified_results_never_escape(setup, change):
    identifier = setup.add_outbox()
    if change == 'auth': setup.after_page = lambda: setattr(setup, 'allowed', False)
    if change == 'lease': setup.lease = False
    if change == 'epoch': setup.after_page = lambda: setattr(setup, 'instance', 'e' * 64)
    if change == 'tamper': setup.rows[identifier]['payload']['body']['approved']['content'] = 'forged'
    with pytest.raises(CommunityError):
        setup.call('messages/list', {'box': 'outbox'})


def test_opaque_identity_binds_chain_and_close_joins(setup):
    assert setup.provider.contact_id(setup.card) != setup.provider.contact_id(
        replace(setup.card, chain_id='Other:CHAIN'))
    assert setup.service.close(timeout=1) is True
    assert len(setup.closed) == 1
    with pytest.raises(CommunityError):
        setup.call('status')


def test_provider_absent_or_runtime_unready_disabled(setup):
    service = CommunityService()
    assert service.dispatch('/community/status', {}, lambda: True,
                            session_id='session')['messaging']['available'] is False
    setup.ready = False
    assert setup.call('status')['messaging']['available'] is False
    with pytest.raises(ValueError):
        CommunityService(mesh_provider=object())
    with pytest.raises(ValueError):
        CommunityService(mesh_provider=setup.provider, canonical_messages=object())


def test_genuine_receipt_projects_delivered_not_radio_acceptance(setup):
    identifier = setup.add_outbox()
    assert setup.call('messages/list', {'box': 'outbox'})['items'][0]['state'] == 'queued'
    digest = setup.rows[identifier]['payload']['body']['approved']['content_digest']
    wire = seal_delivery_receipt(setup.peer_identity._signing_key, sender=setup.peer,
        recipient=setup.sender, message_id=str(uuid.uuid4()), original_id=identifier,
        original_digest=digest, issued_at=120, expires_at=1000).wire
    setup.outbox.ingest_receipt(identifier, wire=wire, access_check=setup.access)
    assert setup.call('messages/list', {'box': 'outbox'})['items'][0]['state'] == 'delivered'


def test_direct_open_survives_list_page_bound(setup):
    setup.add_inbox()
    first = setup.call('messages/list', {'box': 'inbox'})['items'][0]['id']
    for issued in range(101, 122):
        setup.add_inbox(issued_at=issued)
    listing = setup.call('messages/list', {'box': 'inbox'})
    assert listing['more'] and len(listing['items']) == 20
    assert first not in {item['id'] for item in listing['items']}
    assert setup.call('messages/open', {'id': first, 'share_read_receipt': False})['id'] == first


def test_close_stops_external_producers_even_if_outbox_not_drained(setup, monkeypatch):
    monkeypatch.setattr(setup.outbox, 'close', lambda timeout: False)
    assert setup.service.close(timeout=1) is False
    assert len(setup.closed) == 1


def test_lost_post_queue_access_is_unknown_without_new_uuid(setup):
    body = setup.send_body()
    setup.after_release = lambda: setattr(setup, 'allowed', False)
    with pytest.raises(CommunityError) as caught:
        setup.call('messages/send', body)
    assert caught.value.code == 'outcome_unknown'
    assert body['request_id'] in setup.rows
    before = copy.deepcopy(setup.rows)
    setup.allowed = True
    setup.after_release = None
    with pytest.raises(CommunityError):
        setup.call('messages/send', body)
    assert setup.rows == before
    assert setup.service.close(timeout=1) is False
