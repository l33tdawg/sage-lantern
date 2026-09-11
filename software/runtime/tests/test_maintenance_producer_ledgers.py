import copy
import hashlib
import uuid

import pytest
from sage_sdk import AgentIdentity
from nacl.signing import SigningKey

from sage_lantern.maintenance_owner_ledgers import OwnerLedgerInspection
from sage_lantern.maintenance_producer_ledgers import fragments
from sage_lantern.mesh_inbox import ASSEMBLY_SCHEMA, INBOX_SCHEMA
from sage_lantern.mesh_fragments import split_envelope
from sage_lantern.workflow_store import WorkflowUnavailable
from test_mesh_inbox import rig, feed, sealed, KEY, RECIPIENT, frame, OPERATION
from test_mesh_receipt_sender import sender, device_for
from test_mesh_read_receipts import consent, approve
from test_public_mesh_provider import lantern
from test_public_mesh_protocol import IDENTITY, GATEWAY, GATEWAY_KEY, request
from test_gateway_contact import contact, response, receive
from test_public_gateway_bridge import fixture, exchange
from test_public_gateway_runtime import gateway


def inspector(state):
    return OwnerLedgerInspection(list(state.rows.values()),
        dict(chain_id=RECIPIENT.chain_id, agent_id=RECIPIENT.public_key.hex()), state.store,
        AgentIdentity(KEY), containment=lambda: state.live and state.storage)


def test_completed_inbox_assembly_requires_exact_admission_attestation(rig):
    feed(rig.inbox, sealed())
    before = copy.deepcopy(rig.rows)
    checker = inspector(rig)
    assert all(checker.inspect(row) in {'retained_unknown', 'retained_terminal'} for row in rig.rows.values())
    assert checker.finish() and rig.rows == before
    assembly = next(row for row in rig.rows.values() if row['payload']['schema'] == ASSEMBLY_SCHEMA)
    inbox_id = next(key for key, row in rig.rows.items() if row['payload']['schema'] == INBOX_SCHEMA)
    removed = rig.rows.pop(inbox_id)
    with pytest.raises(WorkflowUnavailable):
        inspector(rig).inspect(assembly)
    rig.rows[inbox_id] = removed
    removed['payload']['attestation']['signature'] = 'A' * 88
    with pytest.raises(Exception):
        inspector(rig).inspect(removed)


def test_partial_assembly_retained_not_completed_or_replayed(rig):
    packet = split_envelope(sealed())[0]
    rig.inbox.ingest(OPERATION, frame(packet))
    row = next(iter(rig.rows.values()))
    assert row['payload']['state'] == 'collecting'
    before, puts = copy.deepcopy(rig.rows), rig.puts
    assert inspector(rig).inspect(row) == 'retained_unknown'
    assert rig.rows == before and rig.puts == puts
    row['record_id'] = str(uuid.uuid4())
    with pytest.raises(WorkflowUnavailable):
        inspector(rig).inspect(row)


def test_receipt_sender_queued_and_dispatch_unknown_block_serial_ack_is_not_delivery(sender):
    progress = sender.queue()
    row = sender.rig.rows[progress.receipt_id]
    with pytest.raises(WorkflowUnavailable):
        inspector(sender.rig).inspect(row)
    while progress.state != 'serial_accepted':
        device = device_for(sender, progress.receipt_id)
        progress = sender.sender.dispatch_one(progress.receipt_id, device=device, radio_timestamp=500, access_check=sender.access)
    row = sender.rig.rows[progress.receipt_id]
    before = copy.deepcopy(sender.rig.rows)
    assert inspector(sender.rig).inspect(row) == 'retained_terminal'
    assert sender.rig.rows == before
    changed = copy.deepcopy(row)
    changed['payload']['body']['next_fragment'] = 0
    sender.rig.rows[progress.receipt_id] = changed
    with pytest.raises(Exception):
        inspector(sender.rig).inspect(changed)


@pytest.mark.parametrize('parts', [{'00': 'YQ=='}, {'0': 'Yg=='}, {'0': 'YQ'}, {'1': 'YQ=='}, {'0': True}])
def test_fragment_metadata_and_digest_fail_closed(parts):
    with pytest.raises(Exception):
        fragments(hashlib.sha256(b'a').hexdigest(), 1, 1, parts)


def test_historical_render_consent_does_not_reauthorize_new_session(consent):
    receipt_owner, _, state, _, _ = consent
    approve(consent)
    row = next(row for row in receipt_owner.rig.rows.values()
        if row['payload'].get('body', {}).get('schema') == 'sage.lantern.mesh-read-consent.v1')
    state['epoch'] = 'new-epoch'
    before = copy.deepcopy(receipt_owner.rig.rows)
    assert inspector(receipt_owner.rig).inspect(row) == 'retained_unknown'
    assert receipt_owner.rig.rows == before
    row['payload']['body']['session']['epoch'] = 'forged-new-epoch'
    with pytest.raises(Exception):
        inspector(receipt_owner.rig).inspect(row)


def public_inspector(state, *, config=None):
    actor = state.store.storage.actor
    settings = dict(author=IDENTITY.agent_id, gateway_key=state.pins.gateway_key,
        radio_key=state.pins.radio_key.hex(), chain_id=state.pins.chain_id, domain=state.pins.domain)
    return OwnerLedgerInspection(list(state.rows.values()), dict(chain_id=actor.chain_id, agent_id=actor.agent_id),
        state.store, AgentIdentity.from_seed(b'n' * 32), containment=state.check,
        producer_config={'public_mesh': settings} if config is None else config,
        producer_identities={'public_mesh': IDENTITY})


def test_public_submit_and_restart_unknown_request_no_live_authority(lantern):
    lantern.begin()
    before, puts, sends = copy.deepcopy(lantern.rows), lantern.puts, list(lantern.transmits)
    checker = public_inspector(lantern)
    assert all(checker.inspect(row) == 'retained_unknown' for row in lantern.rows.values())
    assert checker.finish() and lantern.rows == before and lantern.puts == puts and lantern.transmits == sends
    row = next(row for row in lantern.rows.values() if row['payload']['body']['kind'] == 'request')
    with pytest.raises(WorkflowUnavailable):
        public_inspector(lantern, config={}).inspect(row)
    body = row['payload']['body']
    lantern.provider._put(row['record_id'], 'request', row['revision'], dict(body['data'], state='dispatching'), lantern.check)
    with pytest.raises(WorkflowUnavailable):
        public_inspector(lantern).inspect(lantern.rows[row['record_id']])


def test_gateway_contact_verified_signature_is_history_not_current_freshness(contact):
    state, probe = contact
    wire = response(contact)
    receive(contact, wire)
    row = state.rows[probe._slot]
    before, puts = copy.deepcopy(state.rows), state.puts
    assert public_inspector(state).inspect(row) == 'retained_unknown'
    assert state.rows == before and state.puts == puts
    changed = copy.deepcopy(row['payload']['body']['data'])
    changed['parts']['0'] = 'AAAA'
    probe._put(row['revision'], changed, state.check)
    with pytest.raises(Exception):
        public_inspector(state).inspect(state.rows[probe._slot])


def test_public_response_assembly_validates_fragment_shape_and_slot(lantern):
    import base64
    data = dict(digest=hashlib.sha256(b'a').hexdigest(), count=1, size=1,
        expires_at=lantern.now + 120, parts={'0': base64.b64encode(b'a').decode()})
    slot = lantern.provider._id('assembly', 0)
    lantern.provider._put(slot, 'assembly', 0, data, lantern.check)
    assert public_inspector(lantern).inspect(lantern.rows[slot]) == 'retained_unknown'
    lantern.provider._put(slot, 'assembly', 1, dict(data, count=True), lantern.check)
    with pytest.raises(WorkflowUnavailable):
        public_inspector(lantern).inspect(lantern.rows[slot])


def gateway_inspector(state):
    actor = state.workflow.storage.actor
    settings = dict(role=dict(gateway_key=GATEWAY_KEY, chain_id='commons', domain='public-domain',
        endpoint='http://127.0.0.1:19101', storage_actor=actor.agent_id, storage_chain=actor.chain_id,
        pins=[dict(radio_key=bytes(SigningKey(b'p' * 32).verify_key).hex(),
                   author=IDENTITY.agent_id)]))
    return OwnerLedgerInspection(list(state.records.values()), dict(chain_id=actor.chain_id, agent_id=actor.agent_id),
        state.workflow, AgentIdentity.from_seed(b'\x03' * 32), containment=lambda: state.enabled and state.lease,
        producer_config={'public_gateway': settings}, producer_identities={'public_gateway': AgentIdentity(GATEWAY)})


def test_gateway_receipt_validated_against_supplied_scan_not_different_scan(fixture):
    exchange(fixture, request())
    row = next(iter(fixture.records.values()))
    before, calls = copy.deepcopy(fixture.records), list(fixture.calls)
    assert gateway_inspector(fixture).inspect(row) == 'retained_unknown'
    assert fixture.records == before and fixture.calls == calls
    row['payload']['receipt']['content_hash'] = 'f' * 64
    with pytest.raises(Exception):
        gateway_inspector(fixture).inspect(row)


def test_gateway_receiving_retained_queued_and_unknown_states_block(gateway):
    from sage_lantern.public_mesh_provider import public_fragments
    gateway.ingest(public_fragments(request().wire)[0])
    row = next(row for row in gateway.records.values() if row['payload'].get('body', {}).get('schema') ==
        'sage.lantern.public-gateway-runtime.v2')
    before = copy.deepcopy(gateway.records)
    assert gateway_inspector(gateway).inspect(row) == 'retained_unknown'
    assert gateway.records == before
    for state in ('queued', 'dispatching', 'forwarding'):
        row['payload']['body']['data']['state'] = state
        with pytest.raises(WorkflowUnavailable):
            gateway_inspector(gateway).inspect(row)
