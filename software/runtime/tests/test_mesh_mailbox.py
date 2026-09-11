"""Real SDK/store/inbox/outbox behind synthetic HTTP; all mailbox operations read-only."""

from contextlib import contextmanager
import copy
import json
from types import SimpleNamespace
import uuid

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk import AgentIdentity, SageClient

from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.mesh_envelope import Peer, seal_delivery_receipt, seal_message
from sage_lantern.mesh_fragments import split_envelope
from sage_lantern.mesh_inbox import MeshInbox, INBOX_SCHEMA
from sage_lantern.mesh_mailbox import MailboxError, MeshMailbox
from sage_lantern.mesh_outbox import MeshOutbox
from sage_lantern.meshcore_transport import decode_incoming
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.workflow_store import WorkflowStore


BASE = 'http://127.0.0.1:19100'


@pytest.fixture
def setup():
    state = SimpleNamespace(rows={}, now=150, allowed=True, held=False, lease=True, calls=[],
                            instance='d' * 64, after_get=None, after_page=None, after_release=None)
    state.identity = AgentIdentity.from_seed(b'o' * 32)
    state.peer_identity = AgentIdentity.from_seed(b'p' * 32)
    state.owner = RecipientID('Owner:CHAIN', state.identity.agent_id)
    state.sender = Peer(state.owner.chain_id, bytes.fromhex(state.owner.agent_id))
    state.peer = Peer('Peer:CHAIN', bytes.fromhex(state.peer_identity.agent_id))
    radio = SigningKey(b'r' * 32).verify_key.encode()
    def handler(request):
        state.calls.append((request.method, request.url.path))
        path = request.url.path
        if path == '/v1/messages/storage':
            return httpx.Response(200, json=dict(schema='sage.message-storage.v1', instance_id=state.instance,
                agent_id=state.owner.agent_id, encryption_expected=True, vault_active=True,
                vault_generation='1', stable=True, canonical_send_idempotency=True, encrypted_send_admission=True))
        if path == '/v1/workflows':
            after = request.url.params.get('after', '')
            limit = int(request.url.params['limit'])
            rows = [copy.deepcopy(state.rows[key]) for key in sorted(state.rows) if key > after]
            more = len(rows) > limit
            rows = rows[:limit]
            if state.after_page:
                state.after_page()
            return httpx.Response(200, json=dict(schema='sage.workflow-journal.v1', items=rows,
                has_more=more, next_after=rows[-1]['record_id'] if more else None))
        identifier = path.rsplit('/', 1)[-1]
        if request.method == 'GET':
            row = copy.deepcopy(state.rows.get(identifier))
            if state.after_get:
                state.after_get(identifier)
            return httpx.Response(200, json=row) if row else httpx.Response(404, json={})
        body = json.loads(request.content)
        revision = state.rows.get(identifier, {}).get('revision', 0)
        if body['expected_revision'] != revision:
            return httpx.Response(409, json={})
        row = dict(schema='sage.workflow-journal.v1', agent_id=state.owner.agent_id, record_id=identifier,
                   revision=revision + 1, kind=body['kind'], payload=body['payload'], trust='untrusted_auxiliary')
        state.rows[identifier] = row
        return httpx.Response(200, json=row)
    client = SageClient(BASE, state.identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=BASE, timeout=2, trust_env=False, follow_redirects=False,
                                  transport=httpx.MockTransport(handler))
    state.store = WorkflowStore(client, storage=MessageStorageProbe(client, actor=state.owner, endpoint=BASE))
    state.access = lambda: state.allowed
    state.inbox = MeshInbox(state.store, recipient=state.sender, recipient_signing_key=state.identity._signing_key,
                           resolve_peer=lambda message: state.peer, access_check=state.access,
                           storage_check=state.access, clock=lambda: state.now)
    def contact(action):
        card = sign_contact_card(state.peer_identity, chain_id=state.peer.chain_id, radio_public_key=radio.hex(),
                                 display_name='Synthetic peer', revision=1, issued_at=100, expires_at=1000,
                                 now=state.now, findable=True, messaging_allowed=True)
        return verify_contact_card(card, now=state.now)
    state.outbox = MeshOutbox(state.store, state.identity, sender=state.sender, guard=lambda action: state.allowed,
                             verify_contact=contact, clock=lambda: state.now)
    @contextmanager
    def fence():
        assert not state.held
        state.held = True
        try:
            yield lambda: state.held and state.lease
        finally:
            state.held = False
            if state.after_release:
                state.after_release()
    state.fence = fence
    state.mailbox = MeshMailbox(state.store, state.inbox, state.outbox, owner=state.owner, producer_fence=fence)
    def ingest(wire):
        result = None
        for part in split_envelope(wire.encode()):
            incoming = decode_incoming(b'\x07' + radio[:6] + bytes(6) + part.encode())
            result = state.inbox.ingest(str(uuid.uuid4()), incoming)
        return result.record_id
    state.ingest = ingest
    def add_inbox(*, issued_at=100, content='Synthetic incoming', peer=None):
        peer = peer or state.peer
        original = state.peer
        state.peer = peer
        try:
            wire = seal_message(state.peer_identity._signing_key, sender=peer, recipient=state.sender,
                                message_id=str(uuid.uuid4()), content=content, issued_at=issued_at, expires_at=1000).wire
            return ingest(wire)
        finally:
            state.peer = original
    state.add_inbox = add_inbox
    def add_outbox():
        identifier = str(uuid.uuid4())
        state.outbox.queue(message_id=identifier, recipient=state.peer, radio_public_key=radio,
                           content='Synthetic sent', approval_binding='approved', consent_binding='consent',
                           issued_at=110, expires_at=1000, access_check=state.access)
        return identifier
    state.add_outbox = add_outbox
    yield state
    client._client.close()


def test_real_verified_inbox_outbox_listing_and_open_are_read_only(setup):
    setup.add_inbox()
    setup.add_outbox()
    before = copy.deepcopy(setup.rows)
    setup.calls.clear()
    for box, text in [('inbox', 'Synthetic incoming'), ('outbox', 'Synthetic sent')]:
        page = setup.mailbox.list_messages(box, access_check=setup.access)
        assert len(page.items) == 1 and page.more is False
        item = page.items[0]
        assert item.text == text and item.box == box and len(item.id) <= 256
        assert item.sender.chain_id == ('Peer:CHAIN' if box == 'inbox' else 'Owner:CHAIN')
        assert item.recipient.chain_id == ('Owner:CHAIN' if box == 'inbox' else 'Peer:CHAIN')
        assert item.created_at == ('1970-01-01T00:01:40Z' if box == 'inbox' else '1970-01-01T00:01:50Z')
        assert item.state == ('unknown' if box == 'inbox' else 'queued')
        assert setup.mailbox.open_message(item.id, access_check=setup.access) == item
        assert text not in repr(item) and text not in repr(page)
    assert setup.rows == before and all(method == 'GET' for method, _ in setup.calls)
    assert not setup.held


def test_direct_open_beyond_page_does_not_scan(setup):
    setup.add_inbox(issued_at=100)
    setup.add_inbox(issued_at=120)
    both = setup.mailbox.list_messages('inbox', access_check=setup.access)
    first = setup.mailbox.list_messages('inbox', limit=1, access_check=setup.access)
    assert first.more and first.items == both.items[:1]
    setup.calls.clear()
    assert setup.mailbox.open_message(both.items[1].id, access_check=setup.access) == both.items[1]
    assert not any(path == '/v1/workflows' for _, path in setup.calls)


def test_attested_expired_inbox_remains_readable(setup):
    setup.add_inbox()
    identifier = setup.mailbox.list_messages('inbox', access_check=setup.access).items[0].id
    setup.now = 2000
    assert setup.mailbox.open_message(identifier, access_check=setup.access).text == 'Synthetic incoming'


def test_schema_controls_and_genuine_receipt_envelopes_are_not_ui_messages(setup):
    setup.add_inbox()
    outgoing = setup.add_outbox()
    digest = setup.rows[outgoing]['payload']['body']['approved']['content_digest']
    receipt = seal_delivery_receipt(setup.peer_identity._signing_key, sender=setup.peer, recipient=setup.sender,
                                   message_id=str(uuid.uuid4()), original_id=outgoing, original_digest=digest,
                                   issued_at=120, expires_at=1000).wire
    setup.ingest(receipt)
    setup.outbox.ingest_receipt(outgoing, wire=receipt, access_check=setup.access)
    for schema in ('sage.lantern.mesh-contact-record.v1', 'sage.lantern.mesh-receiver.v1',
                   'sage.lantern.mesh-rejection.v1'):
        setup.store.put(str(uuid.uuid4()), 'mesh_inbound', 0, {'schema': schema}, access_check=setup.access)
    before = copy.deepcopy(setup.rows)
    assert len(setup.mailbox.list_messages('inbox', access_check=setup.access).items) == 1
    outbox = setup.mailbox.list_messages('outbox', access_check=setup.access)
    assert len(outbox.items) == 1 and outbox.items[0].state == 'delivered'
    assert setup.rows == before


def test_scan_multiple_pages_and_capacity_fail_closed(setup):
    setup.add_inbox()
    for _ in range(51):
        setup.store.put(str(uuid.uuid4()), 'mesh_inbound', 0, {'schema': 'control'}, access_check=setup.access)
    setup.calls.clear()
    assert len(setup.mailbox.list_messages('inbox', access_check=setup.access).items) == 1
    assert sum(path == '/v1/workflows' for _, path in setup.calls) >= 2
    mailbox = MeshMailbox(setup.store, setup.inbox, setup.outbox, owner=setup.owner,
                          producer_fence=setup.fence, max_records=50)
    with pytest.raises(MailboxError):
        mailbox.list_messages('inbox', access_check=setup.access)


@pytest.mark.parametrize('when', ['before', 'page', 'get', 'release', 'lease', 'epoch'])
def test_auth_fence_and_epoch_revocation_discard_results(setup, when):
    setup.add_inbox()
    if when == 'before': setup.allowed = False
    if when == 'page': setup.after_page = lambda: setattr(setup, 'allowed', False)
    if when == 'get': setup.after_get = lambda identifier: setattr(setup, 'allowed', False)
    if when == 'release': setup.after_release = lambda: setattr(setup, 'allowed', False)
    if when == 'lease': setup.after_page = lambda: setattr(setup, 'lease', False)
    if when == 'epoch': setup.after_page = lambda: setattr(setup, 'instance', 'e' * 64)
    with pytest.raises(MailboxError, match='^mesh_mailbox_unavailable$'):
        setup.mailbox.list_messages('inbox', access_check=setup.access)
    assert not setup.held


@pytest.mark.parametrize('target', ['inbox', 'outbox'])
def test_display_always_reverifies_actual_stored_proof(setup, target):
    identifier = setup.add_inbox() if target == 'inbox' else setup.add_outbox()
    first = setup.mailbox.list_messages(target, access_check=setup.access).items[0]
    if target == 'inbox':
        setup.rows[identifier]['payload']['wire'] = 'LME1.tampered'
    else:
        setup.rows[identifier]['payload']['body']['approved']['content'] = 'tampered'
    with pytest.raises(MailboxError):
        setup.mailbox.open_message(first.id, access_check=setup.access)
    with pytest.raises(MailboxError):
        setup.mailbox.list_messages(target, access_check=setup.access)


def test_qualified_ids_distinguish_same_key_on_other_chain(setup):
    setup.add_inbox()
    setup.add_inbox(peer=Peer('Other:CHAIN', setup.peer.public_key))
    page = setup.mailbox.list_messages('inbox', access_check=setup.access)
    assert len({item.id for item in page.items}) == 2
    assert {item.sender.chain_id for item in page.items} == {'Peer:CHAIN', 'Other:CHAIN'}
    forged = page.items[0].id[:-1] + ('0' if page.items[0].id[-1] != '0' else '1')
    with pytest.raises(MailboxError):
        setup.mailbox.open_message(forged, access_check=setup.access)


@pytest.mark.parametrize('limit', [True, 0, 21, 1.5])
def test_strict_bounds_before_io(setup, limit):
    setup.calls.clear()
    with pytest.raises(MailboxError):
        setup.mailbox.list_messages('inbox', limit=limit, access_check=setup.access)
    assert setup.calls == []


def test_requires_fence_and_borrowed_actor_store_binding(setup):
    with pytest.raises(MailboxError):
        MeshMailbox(setup.store, setup.inbox, setup.outbox, owner=setup.owner, producer_fence=None)
    with pytest.raises(MailboxError):
        MeshMailbox(setup.store, setup.inbox, setup.outbox, owner=RecipientID('wrong', setup.owner.agent_id),
                    producer_fence=setup.fence)
    @contextmanager
    def fake_fence():
        yield True
    mailbox = MeshMailbox(setup.store, setup.inbox, setup.outbox, owner=setup.owner, producer_fence=fake_fence)
    with pytest.raises(MailboxError):
        mailbox.list_messages('inbox', access_check=setup.access)


def test_fence_cannot_suppress_validation_failure(setup):
    @contextmanager
    def suppressing_fence():
        try:
            yield lambda: False
        except Exception:
            pass
    mailbox = MeshMailbox(setup.store, setup.inbox, setup.outbox, owner=setup.owner,
                          producer_fence=suppressing_fence)
    with pytest.raises(MailboxError):
        mailbox.list_messages('inbox', access_check=setup.access)
