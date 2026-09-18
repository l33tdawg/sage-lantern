"""Real SDK/WorkflowStore, synthetic HTTP SQLite CAS; no private files or RF."""

import hashlib
import json
import os
import sqlite3
from types import SimpleNamespace

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk import AgentIdentity, SageClient

from sage_lantern.mesh_contact_registry import CONTACT_SCHEMA, KIND, MeshContactRegistry, contact_record_id
from sage_lantern.mesh_contact_registry_recovery import (
    EVIDENCE_SCHEMA, ContactRecordRecovery, ContactRecoveryError)
from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.workflow_store import WorkflowStore


BASE = 'http://127.0.0.1:19200'
ARTIFACT_REVISION = 1788911957


@pytest.fixture
def setup(tmp_path):
    state = SimpleNamespace(now=110, allowed=True, fault=None, writes=[], database=tmp_path / 'journal.sqlite')
    state.identity = AgentIdentity.from_seed(b'o' * 32)
    state.peer = AgentIdentity.from_seed(b'p' * 32)
    state.owner = RecipientID('Owner:CHAIN', state.identity.agent_id)
    state.actor = RecipientID('Peer:CHAIN', state.peer.agent_id)
    state.radio = SigningKey(b'r' * 32).verify_key.encode()
    state.other_radio = SigningKey(b'q' * 32).verify_key.encode()
    with sqlite3.connect(state.database) as connection:
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
        with sqlite3.connect(state.database, timeout=2) as connection:
            if request.method == 'PUT':
                body = json.loads(request.content)
                schema = body['payload']['body']['schema']
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
            return (httpx.Response(200, json=row(identifier, found[0], json.loads(found[1])))
                    if found else httpx.Response(404, json={}))

    state.client = SageClient(BASE, state.identity, timeout=2, trust_env=False)
    state.client._client.close()
    state.client._client = httpx.Client(base_url=BASE, timeout=2, trust_env=False,
                                        follow_redirects=False, transport=httpx.MockTransport(handler))
    state.store = WorkflowStore(state.client, storage=MessageStorageProbe(state.client, actor=state.owner, endpoint=BASE))
    state.peers = [dict(chain_id=state.actor.chain_id, agent_id=state.actor.agent_id,
                        radio_public_key=state.radio.hex())]

    def registry():
        return MeshContactRegistry(state.store, state.identity, owner=state.owner, clock=lambda: state.now)

    def recovery(**changes):
        return ContactRecordRecovery(state.store, state.identity, owner=state.owner,
                                     peers=changes.pop('peers', state.peers), clock=lambda: state.now, **changes)

    def card(identity=None, actor=None, radio=None, **changes):
        identity = identity or state.peer
        actor = actor or state.actor
        fields = dict(chain_id=actor.chain_id, radio_public_key=(radio or state.radio).hex(),
                      display_name='Synthetic peer', revision=1, issued_at=190, expires_at=250, now=190,
                      findable=True, messaging_allowed=True)
        return sign_contact_card(identity, **(fields | changes))

    state.registry, state.card, state.recovery = registry, card, recovery

    # Signed once: cards carry a random nonce, so every call would return different bytes.
    state.artifact_card = card(revision=ARTIFACT_REVISION, issued_at=100, expires_at=160, now=100,
                               display_name='Synthetic link test', findable=False)
    state.artifact = lambda: state.artifact_card
    yield state
    state.client._client.close()


def seed(setup):
    """Store the expired-artifact-style card as if a peer import had committed it."""
    raw = setup.artifact()
    setup.registry().put(raw, known_actor=setup.actor, access_check=lambda: True)
    return raw


def apply(setup, card, evidence_root, **changes):
    return setup.recovery(**changes).apply(card, acknowledged_sha256=hashlib.sha256(card).hexdigest(),
                                           reason='test repair', evidence_root=str(evidence_root))


def test_plan_read_only_and_monotonic_refusals(setup, tmp_path):
    seed(setup)
    setup.now = 220
    writes = list(setup.writes)
    fresh = setup.card()
    plan = setup.recovery().plan(fresh)
    assert plan['verdict'] == 'ready'
    assert plan['stored']['revision'] == ARTIFACT_REVISION
    assert plan['incoming']['revision'] == 1
    assert setup.writes == writes, 'plan must not write'

    assert setup.recovery().plan(setup.card(issued_at=100, expires_at=160, now=100))['verdict'] == 'refused_invalid_card'
    assert setup.recovery().plan(setup.card(issued_at=100, expires_at=250, now=100))['verdict'] == 'refused_not_monotonic'
    assert setup.recovery().plan(setup.card(radio=setup.other_radio))['verdict'] == 'refused_binding_changed'
    assert setup.recovery().plan(setup.card(messaging_allowed=False))['verdict'] == 'refused_messaging_not_allowed'
    assert setup.recovery(peers=[]).plan(fresh)['verdict'] == 'refused_not_in_peer_policy'
    other_actor = setup.card(identity=AgentIdentity.from_seed(b'x' * 32))
    assert setup.recovery().plan(other_actor)['verdict'].startswith('refused_')
    setup.now = 150
    assert setup.recovery().plan(setup.card(issued_at=140, expires_at=200, now=140))['verdict'] == 'refused_stored_still_valid'
    assert setup.writes == writes


def test_apply_writes_evidence_then_cas_and_is_idempotent(setup, tmp_path):
    seed(setup)
    setup.now = 220
    fresh = setup.card()
    before = list(setup.writes)
    evidence_root = tmp_path / 'evidence'
    result = apply(setup, fresh, evidence_root)
    assert result['applied'] is True and result['already_applied'] is False
    assert len(setup.writes) == len(before) + 1
    assert setup.writes[-1] == (contact_record_id(setup.owner, setup.actor), 2)

    evidence = json.loads(open(result['evidence']).read())
    assert evidence['schema'] == EVIDENCE_SCHEMA
    assert evidence['decision'] == 'expired_artifact_superseded'
    assert evidence['stored']['sha256'] == hashlib.sha256(setup.artifact()).hexdigest()
    assert evidence['incoming']['sha256'] == hashlib.sha256(fresh).hexdigest()
    assert evidence['stored']['revision'] == ARTIFACT_REVISION
    assert evidence['record_revision'] == 1

    assert setup.registry().list_current(access_check=lambda: True) == (verify_contact_card(fresh, now=setup.now),)
    assert setup.registry().put(fresh, known_actor=setup.actor, access_check=lambda: True) is not None

    again = apply(setup, fresh, evidence_root)
    assert again['already_applied'] is True and again['applied'] is False
    assert len(setup.writes) == len(before) + 1, 'idempotent re-run must not write again'


def test_apply_requires_exact_acknowledgement_and_never_rewrites_evidence(setup, tmp_path):
    seed(setup)
    setup.now = 220
    fresh = setup.card()
    evidence_root = tmp_path / 'evidence'
    before = list(setup.writes)
    with pytest.raises(ContactRecoveryError) as error:
        setup.recovery().apply(fresh, acknowledged_sha256='0' * 64, reason='test', evidence_root=str(evidence_root))
    assert error.value.code == 'acknowledgement_mismatch'
    assert setup.writes == before, 'a mismatched acknowledgement must not write'

    recovery = setup.recovery()
    recovery_id = recovery.recovery_id(recovery.plan(fresh))
    directory = evidence_root / recovery_id
    directory.mkdir(parents=True)
    (directory / 'evidence.json').write_text(json.dumps({'schema': EVIDENCE_SCHEMA, 'recovery_id': 'f' * 64}))
    with pytest.raises(ContactRecoveryError) as error:
        apply(setup, fresh, evidence_root)
    assert error.value.code == 'evidence_conflict'
    assert setup.writes == before, 'conflicting evidence must not write'

    __import__('shutil').rmtree(directory)
    result = apply(setup, fresh, evidence_root)
    assert result['applied'] is True
    assert setup.writes[-1] == (contact_record_id(setup.owner, setup.actor), 2)


def test_unknown_write_outcome_leaves_evidence(setup, tmp_path):
    seed(setup)
    setup.now = 220
    fresh = setup.card()
    evidence_root = tmp_path / 'evidence'
    setup.fault = (CONTACT_SCHEMA, 'after')
    with pytest.raises(ContactRecoveryError) as error:
        apply(setup, fresh, evidence_root)
    assert error.value.code == 'record_outcome_unknown'
    files = os.listdir(evidence_root)
    assert len(files) == 1 and os.listdir(evidence_root / files[0]) == ['evidence.json']
