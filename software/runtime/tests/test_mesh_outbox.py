"""Real WorkflowStore/SDK/driver; SQLite CAS HTTP double, synthetic identities only."""

import hashlib
import json
import sqlite3
import struct
import threading
import uuid
from types import SimpleNamespace

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk import AgentIdentity, SageClient

from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.mesh_envelope import Peer, open_envelope, seal_delivery_receipt, seal_read_receipt
from sage_lantern.mesh_fragments import split_envelope
from sage_lantern.mesh_outbox import MeshOutbox, MeshOutboxError
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.workflow_store import WorkflowStore
from test_meshcore_contacts import contact, start, end
from test_meshcore_transport import client as radio_client, frame, handshake_bytes, KEY as RADIO_SELF


MESSAGE = '12345678-1234-4234-8234-123456789abc'
BASE = 'http://127.0.0.1:19100'


@pytest.fixture
def setup(tmp_path):
    state = SimpleNamespace(now=110, allowed=True, contact_allowed=True, writes=[], radios=[],
                            fault=None, barrier=None, guard_actions=[], after_commit=None, promotion_barrier=None,
                            receipt_slot_barrier=None)
    state.identity = AgentIdentity.from_seed(b'a' * 32)
    state.peer_identity = AgentIdentity.from_seed(b'b' * 32)
    state.sender = Peer('test-home', bytes.fromhex(state.identity.agent_id))
    state.peer = Peer('test-home', bytes.fromhex(state.peer_identity.agent_id))
    state.radio_key = SigningKey(b'r' * 32).verify_key.encode()
    database = tmp_path / 'synthetic-http-journal.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE rows (id TEXT PRIMARY KEY, revision INTEGER, payload TEXT)')

    def row(identifier, revision, payload):
        return dict(schema='sage.workflow-journal.v1', agent_id=state.identity.agent_id,
                    record_id=identifier, revision=revision, kind='mesh_outbound',
                    payload=payload, trust='untrusted_auxiliary')

    def handler(request):
        path = request.url.path
        if path == '/v1/messages/storage':
            return httpx.Response(200, json=dict(schema='sage.message-storage.v1', instance_id='d' * 64,
                agent_id=state.identity.agent_id, encryption_expected=True, vault_active=True,
                vault_generation='1', stable=True, canonical_send_idempotency=True, encrypted_send_admission=True))
        if path == '/v1/workflows':
            return httpx.Response(200, json=dict(schema='sage.workflow-journal.v1', items=[],
                                                next_after=None, has_more=False))
        identifier = path.rsplit('/', 1)[-1]
        with sqlite3.connect(database, timeout=2) as connection:
            if request.method == 'PUT':
                body = json.loads(request.content)
                if state.barrier and body['payload']['body'].get('state') == 'dispatching':
                    state.barrier.wait(timeout=5)
                promotion = body['payload']['body'].get('state') in ('delivered', 'read')
                if state.promotion_barrier and promotion:
                    state.promotion_barrier.wait(timeout=5)
                if (state.receipt_slot_barrier
                        and body['payload']['body']['schema'] == 'sage.lantern.mesh-receipt-slot.v1'):
                    state.receipt_slot_barrier.wait(timeout=5)
                connection.execute('BEGIN IMMEDIATE')
                previous = connection.execute('SELECT revision FROM rows WHERE id=?', (identifier,)).fetchone()
                if (previous[0] if previous else 0) != body['expected_revision']:
                    connection.rollback()
                    return httpx.Response(409, json={'detail': 'revision conflict'})
                revision = body['expected_revision'] + 1
                if state.fault == 'before' or state.fault == 'promotion_before' and promotion:
                    connection.rollback()
                    raise httpx.ReadTimeout('private diagnostic')
                connection.execute('INSERT OR REPLACE INTO rows VALUES (?, ?, ?)',
                                   (identifier, revision, json.dumps(body['payload'])))
                connection.commit()
                state.writes.append(body['payload']['body'].get('state',
                    'receipt_slot' if body['payload']['body']['schema'] == 'sage.lantern.mesh-receipt-slot.v1'
                    else 'receipt_replay'))
                if state.after_commit:
                    state.after_commit(body['payload']['body'].get('state', 'receipt_replay'))
                if state.fault == 'after' or state.fault == 'promotion_after' and promotion:
                    raise httpx.ReadTimeout('private diagnostic')
                return httpx.Response(200, json=row(identifier, revision, body['payload']))
            found = connection.execute('SELECT revision,payload FROM rows WHERE id=?', (identifier,)).fetchone()
            if found is None:
                return httpx.Response(404, json={'detail': 'not found'})
            return httpx.Response(200, json=row(identifier, found[0], json.loads(found[1])))

    transports = []
    def make_outbox():
        sdk = SageClient(BASE, state.identity, timeout=2, trust_env=False)
        sdk._client.close()
        sdk._client = httpx.Client(base_url=BASE, timeout=2, trust_env=False,
                                  follow_redirects=False, transport=httpx.MockTransport(handler))
        transports.append(sdk._client)
        probe = MessageStorageProbe(sdk, actor=RecipientID('test-home', state.identity.agent_id), endpoint=BASE)
        store = WorkflowStore(sdk, storage=probe)
        def guard(action):
            state.guard_actions.append(action)
            return state.allowed
        def verify(action):
            card = sign_contact_card(state.peer_identity, chain_id='test-home', radio_public_key=state.radio_key.hex(),
                                     display_name='Synthetic peer', revision=1, issued_at=100, expires_at=1000,
                                     now=state.now, findable=state.contact_allowed, messaging_allowed=state.contact_allowed)
            return verify_contact_card(card, now=state.now)
        return MeshOutbox(store, state.identity, sender=state.sender, guard=guard,
                          verify_contact=verify, clock=lambda: state.now)
    state.make = make_outbox
    state.outbox = make_outbox()
    state.access = lambda: state.allowed
    state.queue = lambda: state.outbox.queue(message_id=MESSAGE, recipient=state.peer, radio_public_key=state.radio_key,
        content='Synthetic private content', approval_binding='explicit-open-1', consent_binding='consent-1',
        issued_at=100, expires_at=1000, access_check=state.access)
    def stored():
        with sqlite3.connect(database) as connection:
            found = connection.execute('SELECT payload FROM rows WHERE id=?', (MESSAGE,)).fetchone()
            return json.loads(found[0]) if found else None
    state.stored = stored
    def row_count():
        with sqlite3.connect(database) as connection:
            return connection.execute('SELECT COUNT(*) FROM rows').fetchone()[0]
    state.row_count = row_count
    def tamper(change):
        payload = stored()
        change(payload)
        with sqlite3.connect(database) as connection:
            connection.execute('UPDATE rows SET payload=? WHERE id=?', (json.dumps(payload), MESSAGE))
    state.tamper = tamper
    def radio(*, reply=True, fail_progress=False):
        body = stored()['body']
        fragment = split_envelope(body['approved']['wire'].encode())[body['next_fragment']]
        stamp = body['dispatch_timestamp'] or 500
        ack = hashlib.sha256(struct.pack('<I', stamp) + b'\0' + fragment.encode() + RADIO_SELF).digest()[:4]
        incoming = handshake_bytes() + frame(start(1)) + frame(contact(state.radio_key)) + frame(end(123))
        if reply:
            incoming += frame(b'\x06\x00' + ack + struct.pack('<I', 900))
        device, stream = radio_client(incoming, chunk=179)
        device.handshake()
        base_write = stream.write
        def write(data, timeout):
            if data.startswith(b'<') and len(data) > 3 and data[3] == 2:
                assert stored()['body']['state'] == 'dispatching'
                assert stored()['body']['dispatch_timestamp'] == stamp
                state.radios.append(data)
                if fail_progress:
                    state.fault = 'before'
            return base_write(data, timeout)
        stream.write = write
        return device
    state.radio = radio
    yield state
    for transport in transports:
        transport.close()


def dispatch(setup, **kwargs):
    return setup.outbox.dispatch_one(MESSAGE, device=setup.radio(**kwargs), radio_timestamp=500,
                                     access_check=setup.access)


def test_queue_proof_original_wire_restarts_and_one_fragment_progress(setup):
    progress = setup.queue()
    assert progress.state == 'queued' and progress.revision == 1
    assert setup.radios == []
    initial = setup.stored()['body']['approved']
    assert initial['content'] == 'Synthetic private content'
    assert setup.outbox.read_sent(MESSAGE, access_check=setup.access).content == initial['content']
    assert initial['content'] not in repr(setup.outbox.read_sent(MESSAGE, access_check=setup.access))
    opened = open_envelope(initial['wire'], setup.peer_identity._signing_key,
                           expected_sender=setup.sender, expected_recipient=setup.peer, now=110)
    assert opened.content == 'Synthetic private content' and opened.message_id == MESSAGE
    assert opened.digest == initial['content_digest']
    with pytest.raises(MeshOutboxError):
        setup.queue()
    setup.outbox = setup.make()
    assert dispatch(setup).next_fragment == 1
    assert setup.stored()['body']['approved'] == initial
    assert setup.writes == ['queued', 'dispatching', 'queued']
    assert len(setup.radios) == 1


def test_all_fragments_stop_at_awaiting_receipt_not_delivered(setup):
    progress = setup.queue()
    for index in range(progress.fragment_count):
        progress = dispatch(setup)
        assert progress.next_fragment == index + 1
    assert progress.state == 'awaiting_receipt'
    device, stream = radio_client(handshake_bytes())
    device.handshake()
    before = bytes(stream.output)
    with pytest.raises(MeshOutboxError):
        setup.outbox.dispatch_one(MESSAGE, device=device, radio_timestamp=500, access_check=setup.access)
    assert bytes(stream.output) == before


def test_uncertain_rf_restart_explicit_retry_same_original_fragment(setup):
    setup.queue()
    approved = setup.stored()['body']['approved']
    with pytest.raises(MeshOutboxError):
        dispatch(setup, reply=False)
    setup.outbox = setup.make()
    progress = setup.outbox.inspect(MESSAGE, access_check=setup.access)
    assert progress.state == 'dispatching' and progress.next_fragment == 0
    with pytest.raises(MeshOutboxError):
        dispatch(setup)
    assert len(setup.radios) == 1
    setup.outbox.retry_decision(MESSAGE, expected_revision=progress.revision,
                               decision_binding='explicit-fenced-retry', access_check=setup.access)
    assert setup.stored()['body']['dispatch_timestamp'] == 500
    dispatch(setup)
    assert setup.radios[0] == setup.radios[1]
    assert setup.stored()['body']['approved'] == approved
    assert any(action.operation == 'retry' and action.decision_binding == 'explicit-fenced-retry'
               for action in setup.guard_actions)


@pytest.mark.parametrize('fault', ['before', 'after'])
def test_dispatch_cas_uncertainty_never_rf(setup, fault):
    setup.queue()
    device = setup.radio()
    setup.fault = fault
    with pytest.raises(MeshOutboxError, match='^mesh_outbox_unavailable$'):
        setup.outbox.dispatch_one(MESSAGE, device=device, radio_timestamp=500, access_check=setup.access)
    assert setup.radios == []
    assert setup.stored()['body']['state'] == ('queued' if fault == 'before' else 'dispatching')


def test_failed_progress_commit_retains_dispatching(setup):
    setup.queue()
    with pytest.raises(MeshOutboxError):
        dispatch(setup, fail_progress=True)
    assert len(setup.radios) == 1
    assert setup.stored()['body']['state'] == 'dispatching'


@pytest.mark.parametrize('change', ['guard', 'consent', 'expiry'])
def test_revocation_after_dispatch_commit_stops_rf(setup, change):
    setup.queue()
    device = setup.radio()
    def revoke(state):
        if state == 'dispatching':
            if change == 'guard':
                setup.allowed = False
            elif change == 'consent':
                setup.contact_allowed = False
            else:
                setup.now = 1000
    setup.after_commit = revoke
    with pytest.raises(MeshOutboxError):
        setup.outbox.dispatch_one(MESSAGE, device=device, radio_timestamp=500, access_check=setup.access)
    assert setup.radios == []
    assert setup.stored()['body']['state'] == 'dispatching'


def test_queue_commit_timeout_reconciles_without_resealing(setup):
    setup.fault = 'after'
    with pytest.raises(MeshOutboxError):
        setup.queue()
    original = setup.stored()
    setup.fault = None
    setup.outbox = setup.make()
    assert setup.outbox.inspect(MESSAGE, access_check=setup.access).state == 'queued'
    with pytest.raises(MeshOutboxError):
        setup.queue()
    assert setup.stored() == original
    assert setup.radios == []


@pytest.mark.parametrize('revision', [True, 0, 1, 3])
def test_retry_requires_exact_dispatching_revision(setup, revision):
    setup.queue()
    with pytest.raises(MeshOutboxError):
        dispatch(setup, reply=False)
    original = setup.stored()
    with pytest.raises(MeshOutboxError):
        setup.outbox.retry_decision(MESSAGE, expected_revision=revision,
                                   decision_binding='explicit-retry', access_check=setup.access)
    assert setup.stored() == original


@pytest.mark.parametrize('field,value', [('state', 'awaiting_receipt'), ('next_fragment', 1),
                                         ('dispatch_timestamp', 123), ('revision', 9)])
def test_untrusted_progress_tampering_no_rf(setup, field, value):
    setup.queue()
    device = setup.radio()
    setup.tamper(lambda payload: payload['body'].update({field: value}))
    with pytest.raises(MeshOutboxError):
        setup.outbox.dispatch_one(MESSAGE, device=device, radio_timestamp=500, access_check=setup.access)
    assert setup.radios == []


@pytest.mark.parametrize('field,value', [('wire', 'LME1.forged'), ('wire_hash', '0' * 64),
    ('approval_binding', 'forged'), ('consent_binding', 'forged'), ('content_digest', '0' * 64),
    ('content', 'forged text')])
def test_untrusted_approval_tampering_no_rf(setup, field, value):
    setup.queue()
    device = setup.radio()
    setup.tamper(lambda payload: payload['body']['approved'].update({field: value}))
    with pytest.raises(MeshOutboxError):
        setup.outbox.dispatch_one(MESSAGE, device=device, radio_timestamp=500, access_check=setup.access)
    assert setup.radios == []


@pytest.mark.parametrize('change', ['guard', 'consent', 'expired'])
def test_live_admission_failure_before_rf(setup, change):
    setup.queue()
    device = setup.radio()
    if change == 'guard':
        setup.allowed = False
    elif change == 'consent':
        setup.contact_allowed = False
    else:
        setup.now = 1000
    with pytest.raises(MeshOutboxError):
        setup.outbox.dispatch_one(MESSAGE, device=device, radio_timestamp=500, access_check=setup.access)
    assert setup.radios == []


def test_cross_connection_cas_exactly_one_fragment_winner(setup):
    setup.queue()
    outboxes = [setup.make(), setup.make()]
    devices = [setup.radio(), setup.radio()]
    setup.barrier = threading.Barrier(2)
    results = []
    def worker(index):
        try:
            results.append(outboxes[index].dispatch_one(MESSAGE, device=devices[index], radio_timestamp=500,
                                                        access_check=setup.access))
        except MeshOutboxError:
            results.append(None)
    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sum(result is not None for result in results) == 1
    assert len(setup.radios) == 1
    assert setup.stored()['body']['next_fragment'] == 1


RECEIPT = '22345678-1234-4234-8234-123456789abc'
READ_RECEIPT = '32345678-1234-4234-8234-123456789abc'


def receipt_wire(setup, kind='delivery', **changes):
    approved = setup.stored()['body']['approved']
    values = dict(sender=setup.peer, recipient=setup.sender,
                  message_id=RECEIPT if kind == 'delivery' else READ_RECEIPT,
                  original_id=MESSAGE, original_digest=approved['content_digest'],
                  issued_at=110, expires_at=500)
    values.update(changes)
    if kind == 'read':
        return seal_read_receipt(setup.peer_identity._signing_key, **values, explicit_user_read=True).wire
    return seal_delivery_receipt(setup.peer_identity._signing_key, **values).wire


def ingest(setup, wire):
    return setup.outbox.ingest_receipt(MESSAGE, wire=wire, access_check=setup.access)


def test_genuine_receipts_persist_monotonically_and_keep_original_approval(setup):
    setup.queue()
    approval = setup.stored()['body']['approval_proof']
    delivery = receipt_wire(setup)
    read = receipt_wire(setup, 'read')
    delivered = ingest(setup, delivery)
    assert delivered.state == 'delivered' and delivered.revision == 2
    assert setup.writes == ['queued', 'receipt_slot', 'receipt_replay', 'delivered']
    assert ingest(setup, delivery) == delivered
    assert ingest(setup, receipt_wire(setup)) == delivered
    promoted = ingest(setup, read)
    assert promoted.state == 'read' and promoted.revision == 3
    assert ingest(setup, delivery) == promoted
    setup.outbox = setup.make()
    assert ingest(setup, read) == promoted
    assert setup.outbox.read_sent(MESSAGE, access_check=setup.access).content == 'Synthetic private content'
    assert setup.stored()['body']['approval_proof'] == approval
    assert setup.radios == []


def test_read_before_delivery_never_downgrades(setup):
    setup.queue()
    progress = ingest(setup, receipt_wire(setup, 'read'))
    assert ingest(setup, receipt_wire(setup)) == progress
    assert len(setup.stored()['body']['receipts']) == 1


def test_many_distinct_redundant_receipts_allocate_nothing(setup):
    setup.queue()
    delivered = ingest(setup, receipt_wire(setup))
    assert setup.row_count() == 3
    writes = list(setup.writes)
    for _ in range(100):
        assert ingest(setup, receipt_wire(setup, message_id=str(uuid.uuid4()))) == delivered
    assert setup.row_count() == 3 and setup.writes == writes
    read = ingest(setup, receipt_wire(setup, 'read'))
    assert setup.row_count() == 5
    writes = list(setup.writes)
    for _ in range(100):
        for kind in ('delivery', 'read'):
            assert ingest(setup, receipt_wire(setup, kind, message_id=str(uuid.uuid4()))) == read
    assert setup.row_count() == 5 and setup.writes == writes


@pytest.mark.parametrize('fault', ['after', 'promotion_before'])
def test_many_replacement_receipts_cannot_expand_uncertain_reservation(setup, fault):
    setup.queue()
    original = receipt_wire(setup)
    setup.fault = fault
    with pytest.raises(MeshOutboxError):
        ingest(setup, original)
    setup.fault = None
    setup.outbox = setup.make()
    count = setup.row_count()
    assert count in (2, 3)
    for _ in range(50):
        with pytest.raises(MeshOutboxError):
            ingest(setup, receipt_wire(setup, message_id=str(uuid.uuid4())))
    assert setup.row_count() == count
    assert ingest(setup, original).state == 'delivered'
    assert setup.row_count() == 3


def test_concurrent_distinct_same_rank_receipts_share_one_fixed_slot(setup):
    setup.queue()
    wires = [receipt_wire(setup, message_id=str(uuid.uuid4())) for _ in range(2)]
    outboxes = [setup.make(), setup.make()]
    setup.receipt_slot_barrier = threading.Barrier(2)
    results = []
    def worker(index):
        try:
            results.append(outboxes[index].ingest_receipt(MESSAGE, wire=wires[index], access_check=setup.access))
        except MeshOutboxError:
            results.append(None)
    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sum(result is not None for result in results) == 1
    assert setup.row_count() == 3


def test_altered_content_under_same_receipt_uuid_rejected(setup):
    setup.queue()
    progress = ingest(setup, receipt_wire(setup))
    for wire in (receipt_wire(setup, expires_at=501), receipt_wire(setup, 'read', message_id=RECEIPT)):
        with pytest.raises(MeshOutboxError):
            ingest(setup, wire)
    assert setup.outbox.inspect(MESSAGE, access_check=setup.access) == progress


def test_receipt_uuid_cannot_be_rebound_to_another_outgoing_message(setup):
    setup.queue()
    ingest(setup, receipt_wire(setup))
    second = '42345678-1234-4234-8234-123456789abc'
    setup.outbox.queue(message_id=second, recipient=setup.peer, radio_public_key=setup.radio_key,
                       content='Another approved message', approval_binding='explicit-open-2',
                       consent_binding='consent-1', issued_at=100, expires_at=1000, access_check=setup.access)
    row = setup.outbox.store.get(second, kind='mesh_outbound', access_check=setup.access)
    digest = row['payload']['body']['approved']['content_digest']
    wire = receipt_wire(setup, original_id=second, original_digest=digest)
    with pytest.raises(MeshOutboxError):
        setup.outbox.ingest_receipt(second, wire=wire, access_check=setup.access)
    assert setup.outbox.inspect(second, access_check=setup.access).state == 'queued'


@pytest.mark.parametrize('change', [
    {'original_id': '42345678-1234-4234-8234-123456789abc'},
    {'original_digest': '0' * 64}, {'issued_at': 99}, {'issued_at': 111}, {'expires_at': 110},
])
def test_wrong_message_digest_or_time_never_promotes(setup, change):
    setup.queue()
    if change == {'expires_at': 110}:
        change = dict(issued_at=100, expires_at=110)
    wire = receipt_wire(setup, **change)
    with pytest.raises(MeshOutboxError):
        ingest(setup, wire)
    assert setup.writes == ['queued']


def test_wrong_qualified_sender_or_recipient_rejected(setup):
    setup.queue()
    for change in ({'sender': Peer('other-chain', setup.peer.public_key)},
                   {'recipient': Peer('other-chain', setup.sender.public_key)},
                   {'recipient': setup.peer}):
        with pytest.raises(MeshOutboxError):
            ingest(setup, receipt_wire(setup, **change))
    assert setup.writes == ['queued']


def test_delayed_receipt_original_expired_and_stored_receipt_expiry(setup):
    setup.queue()
    setup.now = 1200
    wire = receipt_wire(setup, issued_at=1200, expires_at=1300)
    progress = ingest(setup, wire)
    assert progress.state == 'delivered'
    setup.now = 1400
    assert setup.outbox.inspect(MESSAGE, access_check=setup.access) == progress
    assert ingest(setup, wire) == progress


@pytest.mark.parametrize('fault', ['after', 'promotion_before', 'promotion_after'])
def test_receipt_commit_unknown_reconciles_same_uuid(setup, fault):
    setup.queue()
    wire = receipt_wire(setup)
    setup.fault = fault
    with pytest.raises(MeshOutboxError):
        ingest(setup, wire)
    assert setup.outbox.close(0) is False
    setup.fault = None
    setup.outbox = setup.make()
    progress = ingest(setup, wire)
    assert progress.state == 'delivered' and progress.revision == 2
    assert setup.writes.count('receipt_replay') == 1
    assert setup.writes.count('delivered') == 1
    assert setup.radios == []


def test_original_approval_cannot_be_replaced_by_fresh_progress_signature(setup):
    setup.queue()
    def change(payload):
        payload['body']['approved']['content'] = 'forged'
        replacement = setup.outbox._proof(payload['body'])
        payload.clear()
        payload.update(replacement)
    setup.tamper(change)
    with pytest.raises(MeshOutboxError):
        setup.outbox.read_sent(MESSAGE, access_check=setup.access)


def test_persisted_receipt_signature_reverified_on_each_read(setup):
    setup.queue()
    ingest(setup, receipt_wire(setup))
    def change(payload):
        payload['body']['receipts'][0]['wire'] = 'LME1.invalid'
        replacement = setup.outbox._proof(payload['body'])
        payload.clear()
        payload.update(replacement)
    setup.tamper(change)
    with pytest.raises(MeshOutboxError):
        setup.outbox.inspect(MESSAGE, access_check=setup.access)


def test_concurrent_receipt_promotions_cas_then_reconcile_monotonic(setup):
    setup.queue()
    wires = [receipt_wire(setup), receipt_wire(setup, 'read')]
    outboxes = [setup.make(), setup.make()]
    setup.promotion_barrier = threading.Barrier(2)
    results = []
    def worker(index):
        try:
            results.append(outboxes[index].ingest_receipt(MESSAGE, wire=wires[index], access_check=setup.access))
        except MeshOutboxError:
            results.append(None)
    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sum(result is not None for result in results) == 1
    setup.promotion_barrier = None
    ingest(setup, wires[0])
    assert ingest(setup, wires[1]).state == 'read'


def test_close_fences_all_new_admission_and_joins_existing_worker(setup):
    setup.queue()
    device = setup.radio()
    entered, release = threading.Event(), threading.Event()
    original = setup.outbox.verify_contact
    def blocked(action):
        entered.set()
        assert release.wait(5)
        return original(action)
    setup.outbox.verify_contact = blocked
    errors = []
    def worker():
        try:
            setup.outbox.dispatch_one(MESSAGE, device=device, radio_timestamp=500, access_check=setup.access)
        except MeshOutboxError:
            errors.append(True)
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(5)
    with pytest.raises(MeshOutboxError):
        setup.outbox.inspect(MESSAGE, access_check=setup.access)
    assert setup.outbox.close(0) is False
    for operation in (lambda: setup.queue(),
                      lambda: setup.outbox.read_sent(MESSAGE, access_check=setup.access),
                      lambda: ingest(setup, 'invalid')):
        with pytest.raises(MeshOutboxError):
            operation()
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive() and errors == [True]
    assert setup.outbox.close(1) is True
    assert setup.radios == []


def test_clean_close_and_uncertain_rf_close(setup):
    setup.queue()
    with pytest.raises(MeshOutboxError):
        dispatch(setup, reply=False)
    assert setup.outbox.close(1) is False


def test_interrupted_admission_releases_join_but_never_claims_clean(setup):
    setup.queue()
    def interrupted(action):
        raise KeyboardInterrupt()
    setup.outbox.guard = interrupted
    with pytest.raises(KeyboardInterrupt):
        setup.outbox.read_sent(MESSAGE, access_check=setup.access)
    assert setup.outbox.close(0) is False
    assert setup.outbox._active is False
