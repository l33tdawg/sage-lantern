"""Real SDK/WorkflowStore, synthetic HTTP SQLite CAS; no private files or RF."""

import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk import AgentIdentity, SageClient

from sage_lantern.mesh_contacts import HistoricalMeshContact, sign_contact_card
from sage_lantern.mesh_contact_registry import (
    CONTACT_SCHEMA, INDEX_SCHEMA, KIND, METHOD, PATH, MeshContactRegistry,
    RegistryError, RegistryOutcomeUnknown, contact_record_id, registry_index_id,
)
from sage_lantern.meshcore_contacts import ContactInventory, RadioContact
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.workflow_store import WorkflowStore


BASE = 'http://127.0.0.1:19100'


@pytest.fixture
def setup(tmp_path):
    state = SimpleNamespace(now=110, allowed=True, fault=None, barrier=None, writes=[], policy_calls=[])
    state.identity = AgentIdentity.from_seed(b'o' * 32)
    state.peer = AgentIdentity.from_seed(b'p' * 32)
    state.owner = RecipientID('Owner:CHAIN', state.identity.agent_id)
    state.actor = RecipientID('Peer:CHAIN', state.peer.agent_id)
    state.radio = SigningKey(b'r' * 32).verify_key.encode()
    database = tmp_path / 'synthetic-journal.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE rows (id TEXT PRIMARY KEY, revision INTEGER, payload TEXT)')
    def row(identifier, revision, payload):
        return dict(schema='sage.workflow-journal.v1', agent_id=state.owner.agent_id, record_id=identifier,
                    revision=revision, payload=payload, kind=KIND, trust='untrusted_auxiliary')
    def handler(request):
        if request.url.path == '/v1/messages/storage':
            return httpx.Response(200, json=dict(schema='sage.message-storage.v1', instance_id='a' * 64,
                agent_id=state.owner.agent_id, encryption_expected=True, vault_active=True,
                vault_generation='1', stable=True, canonical_send_idempotency=True, encrypted_send_admission=True))
        if request.url.path == '/v1/workflows':
            return httpx.Response(200, json=dict(schema='sage.workflow-journal.v1', items=[],
                                                next_after=None, has_more=False))
        identifier = request.url.path.rsplit('/', 1)[-1]
        with sqlite3.connect(database, timeout=2) as connection:
            if request.method == 'PUT':
                body = json.loads(request.content)
                schema = body['payload']['body']['schema']
                if state.barrier and schema == INDEX_SCHEMA:
                    state.barrier.wait(timeout=5)
                connection.execute('BEGIN IMMEDIATE')
                previous = connection.execute('SELECT revision FROM rows WHERE id=?', (identifier,)).fetchone()
                if (previous[0] if previous else 0) != body['expected_revision']:
                    connection.rollback()
                    return httpx.Response(409, json={'detail': 'conflict'})
                if state.fault == (schema, 'before'):
                    connection.rollback()
                    raise httpx.ReadTimeout('private diagnostic')
                revision = body['expected_revision'] + 1
                connection.execute('INSERT OR REPLACE INTO rows VALUES (?, ?, ?)',
                                   (identifier, revision, json.dumps(body['payload'])))
                connection.commit()
                state.writes.append((identifier, revision))
                if state.fault == (schema, 'after'):
                    raise httpx.ReadTimeout('private diagnostic')
                return httpx.Response(200, json=row(identifier, revision, body['payload']))
            found = connection.execute('SELECT revision,payload FROM rows WHERE id=?', (identifier,)).fetchone()
            return httpx.Response(200, json=row(identifier, found[0], json.loads(found[1]))) if found else httpx.Response(404, json={})
    transports = []
    def make(**options):
        client = SageClient(BASE, state.identity, timeout=2, trust_env=False)
        client._client.close()
        client._client = httpx.Client(base_url=BASE, timeout=2, trust_env=False,
                                      follow_redirects=False, transport=httpx.MockTransport(handler))
        transports.append(client._client)
        store = WorkflowStore(client, storage=MessageStorageProbe(client, actor=state.owner, endpoint=BASE))
        return MeshContactRegistry(store, state.identity, owner=state.owner, clock=lambda: state.now, **options)
    state.make = make
    state.registry = make()
    state.access = lambda: state.allowed
    def card(identity=None, actor=None, **changes):
        identity = identity or state.peer
        actor = actor or state.actor
        fields = dict(chain_id=actor.chain_id, radio_public_key=state.radio.hex(), display_name='Synthetic peer',
                      revision=1, issued_at=100, expires_at=160, now=110, findable=True, messaging_allowed=True)
        return sign_contact_card(identity, **(fields | changes))
    state.card = card
    def read(identifier):
        with sqlite3.connect(database) as connection:
            found = connection.execute('SELECT revision,payload FROM rows WHERE id=?', (identifier,)).fetchone()
            return row(identifier, found[0], json.loads(found[1])) if found else None
    state.read = read
    def tamper(identifier, change):
        value = read(identifier)
        change(value)
        with sqlite3.connect(database) as connection:
            connection.execute('UPDATE rows SET revision=?,payload=? WHERE id=?',
                               (value['revision'], json.dumps(value['payload']), identifier))
    state.tamper = tamper
    yield state
    for transport in transports:
        transport.close()


def put(setup, card=None, actor=None):
    return setup.registry.put(card or setup.card(), known_actor=actor or setup.actor, access_check=setup.access)


def test_roundtrip_owner_bound_deterministic_ids_restart_idempotency(setup):
    raw = setup.card()
    value = put(setup, raw)
    index = registry_index_id(setup.owner)
    identifier = contact_record_id(setup.owner, setup.actor)
    assert [item[0] for item in setup.writes] == [index, identifier]
    assert setup.read(identifier)['payload']['body']['card'].encode() == raw
    setup.registry = setup.make()
    assert put(setup, raw) == value and len(setup.writes) == 2
    assert setup.registry.list_current(access_check=setup.access) == (value,)
    assert type(setup.registry.historical(setup.actor, access_check=setup.access)) is HistoricalMeshContact
    assert contact_record_id(setup.owner, RecipientID('peer:chain', setup.actor.agent_id)) != identifier
    assert contact_record_id(RecipientID('another', setup.owner.agent_id), setup.actor) != identifier


def test_expired_previous_signature_allows_only_new_revision_and_keeps_revocation(setup):
    put(setup)
    setup.now = 200
    assert setup.registry.list_current(access_check=setup.access) == ()
    assert setup.registry.historical(setup.actor, access_check=setup.access).revision == 1
    revoked = setup.card(revision=2, issued_at=200, expires_at=260, now=200, findable=False, messaging_allowed=False)
    put(setup, revoked)
    setup.now = 300
    setup.registry = setup.make()
    assert setup.registry.historical(setup.actor, access_check=setup.access).messaging_allowed is False
    for revision in (1, 2):
        with pytest.raises(RegistryError):
            put(setup, setup.card(revision=revision, issued_at=300, expires_at=360, now=300))
    assert setup.registry.list_current(access_check=setup.access) == ()
    assert len(setup.read(setup.registry.index_id)['payload']['body']['members']) == 1


@pytest.mark.parametrize('schema', [INDEX_SCHEMA, CONTACT_SCHEMA])
@pytest.mark.parametrize('fault', ['before', 'after'])
def test_unknown_creation_reconciles_original_card_and_uuid(setup, schema, fault):
    raw = setup.card()
    setup.fault = (schema, fault)
    with pytest.raises(RegistryOutcomeUnknown, match='^mesh_contact_registry_outcome_unknown$'):
        put(setup, raw)
    setup.fault = None
    setup.registry = setup.make()
    if setup.read(setup.registry.index_id) and not setup.read(contact_record_id(setup.owner, setup.actor)):
        with pytest.raises(RegistryError):
            setup.registry.list_current(access_check=setup.access)
    put(setup, raw)
    assert len(setup.read(setup.registry.index_id)['payload']['body']['members']) == 1
    assert setup.read(contact_record_id(setup.owner, setup.actor))['revision'] == 1


def reserve_slots(setup, count):
    members = sorted(str(uuid.uuid5(uuid.NAMESPACE_OID, str(index))) for index in range(count))
    setup.registry._write(setup.registry.index_id, INDEX_SCHEMA, 0, dict(members=members), setup.access)
    return members


def test_all_256_reserved_slots_charge_capacity_without_eviction(setup):
    members = reserve_slots(setup, 256)
    record = setup.read(setup.registry.index_id)
    assert len(json.dumps(record['payload'])) < 16384
    with pytest.raises(RegistryError):
        put(setup)
    assert setup.read(setup.registry.index_id)['payload']['body']['members'] == members
    assert setup.read(contact_record_id(setup.owner, setup.actor)) is None


def test_last_capacity_slot_cas_cross_connections_exactly_one_winner(setup):
    reserve_slots(setup, 255)
    registries = [setup.make(), setup.make()]
    actors = [setup.actor, RecipientID('other:chain', setup.actor.agent_id)]
    cards = [setup.card(actor=actor) for actor in actors]
    setup.barrier = threading.Barrier(2)
    results = []
    def worker(index):
        try:
            results.append(registries[index].put(cards[index], known_actor=actors[index], access_check=setup.access))
        except RegistryError:
            results.append(None)
    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sum(result is not None for result in results) == 1
    assert len(setup.read(setup.registry.index_id)['payload']['body']['members']) == 256
    assert sum(setup.read(contact_record_id(setup.owner, actor)) is not None for actor in actors) == 1


@pytest.mark.parametrize('target', ['index', 'card', 'revision', 'owner', 'kind', 'uuid'])
def test_unsigned_ledger_tampering_rejected(setup, target):
    put(setup)
    identifier = setup.registry.index_id if target == 'index' else contact_record_id(setup.owner, setup.actor)
    def change(row):
        body = row['payload']['body']
        if target == 'index': body['members'] = []
        elif target == 'card': body['card'] = body['card'].replace('Synthetic peer', 'Another person')
        elif target == 'revision': row['revision'] += 1
        elif target == 'owner': body['owner']['chain_id'] = 'wrong'
        elif target == 'kind': body['kind'] = 'mesh_outbound'
        else: body['record_id'] = str(uuid.uuid4())
    setup.tamper(identifier, change)
    with pytest.raises(RegistryError):
        setup.registry.list_current(access_check=setup.access)


def test_owner_signature_cannot_bless_invalid_remote_card(setup):
    put(setup)
    def change(row):
        body = row['payload']['body']
        body['card'] = body['card'].replace('Synthetic peer', 'Another person')
        encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
        headers = setup.identity.sign_request(METHOD, PATH, encoded, timestamp=110)
        row['payload'].update(timestamp=headers['X-Timestamp'], nonce=headers['X-Nonce'], signature=headers['X-Signature'])
    setup.tamper(contact_record_id(setup.owner, setup.actor), change)
    with pytest.raises(RegistryError):
        setup.registry.historical(setup.actor, access_check=setup.access)


def test_resolution_requires_policy_inventory_and_full_key_uniqueness(setup):
    put(setup)
    with pytest.raises(RegistryError):
        setup.registry.resolve(setup.radio, access_check=setup.access)
    inventory = ContactInventory((RadioContact(setup.radio, 1, 0, 1),), 1)
    calls = []
    def policy(owner, card, radio):
        calls.append((owner, card.recipient, radio))
        return True
    setup.registry = setup.make(inventory=lambda: inventory, verify_binding=policy)
    assert setup.registry.resolve(setup.radio, access_check=setup.access).recipient == setup.actor
    assert calls == [(setup.owner, setup.actor, setup.radio)]
    with pytest.raises(RegistryError):
        setup.registry.resolve(setup.radio[:6], access_check=setup.access)
    other = RecipientID('second-chain', setup.actor.agent_id)
    put(setup, setup.card(actor=other), other)
    with pytest.raises(RegistryError):
        setup.registry.resolve(setup.radio, access_check=setup.access)


@pytest.mark.parametrize('failure', ['policy', 'inventory', 'expired', 'optout', 'access'])
def test_resolve_failclosed_boundaries(setup, failure):
    put(setup, setup.card(findable=failure != 'optout', messaging_allowed=failure != 'optout'))
    inventory = ContactInventory(() if failure == 'inventory' else (RadioContact(setup.radio, 1, 0, 1),), 1)
    setup.registry = setup.make(inventory=lambda: inventory, verify_binding=lambda *args: failure != 'policy')
    if failure == 'expired': setup.now = 200
    if failure == 'access': setup.allowed = False
    with pytest.raises(RegistryError):
        setup.registry.resolve(setup.radio, access_check=setup.access)


def test_clock_rollback_sticky_and_no_writes_after_denial(setup):
    put(setup)
    writes = list(setup.writes)
    setup.now = 109
    with pytest.raises(RegistryError):
        setup.registry.list_current(access_check=setup.access)
    setup.now = 110
    with pytest.raises(RegistryError):
        setup.registry.list_current(access_check=setup.access)
    assert setup.writes == writes


def test_full_registry_still_updates_existing_tombstone(setup):
    reserve_slots(setup, 255)
    put(setup)
    members = setup.read(setup.registry.index_id)['payload']['body']['members']
    setup.now = 200
    revoked = setup.card(revision=2, issued_at=200, expires_at=260, now=200, findable=False, messaging_allowed=False)
    put(setup, revoked)
    assert setup.read(setup.registry.index_id)['payload']['body']['members'] == members
    assert setup.registry.historical(setup.actor, access_check=setup.access).revision == 2


def test_binding_callback_revocation_never_returns_stale_contact(setup):
    put(setup)
    updater = setup.make()
    inventory = ContactInventory((RadioContact(setup.radio, 1, 0, 1),), 1)
    def policy(*args):
        updater.put(setup.card(revision=2, findable=False, messaging_allowed=False),
                    known_actor=setup.actor, access_check=setup.access)
        return True
    registry = setup.make(inventory=lambda: inventory, verify_binding=policy)
    with pytest.raises(RegistryError):
        registry.resolve(setup.radio, access_check=setup.access)


def test_six_byte_inventory_collision_rejected_even_with_policy(setup):
    from nacl.bindings import crypto_core_ed25519_is_valid_point

    put(setup)
    candidates = (setup.radio[:6] + hashlib.sha256(str(index).encode()).digest()[:26] for index in range(256))
    other = next(key for key in candidates if crypto_core_ed25519_is_valid_point(key))
    inventory = ContactInventory((RadioContact(setup.radio, 1, 0, 1), RadioContact(other, 1, 0, 1)), 1)
    registry = setup.make(inventory=lambda: inventory, verify_binding=lambda *args: True)
    with pytest.raises(RegistryError):
        registry.resolve(setup.radio, access_check=setup.access)
