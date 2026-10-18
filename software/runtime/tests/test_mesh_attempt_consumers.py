"""Attempt-aware consumers against real signed rows and synthetic transports."""

import base64
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import time
import uuid

import pytest

from sage_lantern.maintenance_owner_ledgers import OwnerLedgerInspection
from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.mesh_custody import (
    CustodyError, HEADER, MAX_ENVELOPE_BYTES, PREFIX, message_digest, parse_custody, wrap_custody,
)
from sage_lantern.mesh_envelope import Peer, seal_message
from sage_lantern.mesh_outbox import ATTEMPT_SCHEMA, MeshOutbox, REPLAY_NAMESPACE, _canonical
from sage_lantern.workflow_store import WorkflowUnavailable
from test_mesh_daemon import runtime as daemon_runtime
from test_mesh_mailbox import setup as mailbox_setup
from test_mesh_outbox import MESSAGE, ingest, lapsed, receipt_wire, setup as outbox_setup
from test_mesh_runtime import factory


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('attempt_cancel_outbox', ROOT / 'scripts/lantern/cancel-outbox-rows.py')
CANCEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANCEL)


def renew(setup):
    setup.queue()
    original = copy.deepcopy(setup.stored()['body']['approved'])
    lapsed(setup)
    setup.outbox.reapprove(MESSAGE, expected_revision=1, window_seconds=600,
        approval_binding='owner-renewal', consent_binding='renewed-consent', access_check=setup.access)
    assert setup.stored()['body']['schema'] == ATTEMPT_SCHEMA
    return original


def terminal_rows(setup):
    store = setup.outbox.store
    get = lambda key: store.get(key, kind='mesh_outbound', access_check=setup.access)
    original = get(MESSAGE)
    rows = [original]
    for item in original['payload']['body']['receipts']:
        receipt = setup.outbox._receipt(original['payload']['body'], item['wire'], item['verified_at'])
        scopes = [dict(sender=receipt.sender.to_dict(), receipt_id=receipt.message_id),
            dict(schema='sage.lantern.mesh-receipt-slot.v1', receiver=setup.sender.to_dict(),
                 original_id=MESSAGE, kind=receipt.kind)]
        rows.extend(get(str(uuid.uuid5(REPLAY_NAMESPACE, _canonical(scope).decode('ascii')))) for scope in scopes)
    return rows


def inspection(setup, rows):
    actor = dict(chain_id=setup.sender.chain_id, agent_id=setup.identity.agent_id)
    return OwnerLedgerInspection(rows, actor, setup.outbox.store, setup.identity, containment=setup.access)


@pytest.mark.parametrize('kind', ['delivery', 'read'])
def test_maintenance_retains_delayed_original_receipt_after_renewal(outbox_setup, kind):
    setup = outbox_setup
    original = renew(setup)
    ingest(setup, receipt_wire(setup, kind, original_digest=original['content_digest'],
                              issued_at=110, expires_at=2500))
    rows = terminal_rows(setup)
    writes, radios = list(setup.writes), list(setup.radios)
    checker = inspection(setup, rows)
    assert all(checker.inspect(row) == 'retained_terminal' for row in rows)
    assert checker.finish()
    assert setup.writes == writes and setup.radios == radios
    for row in rows[1:]:
        assert row['payload']['body']['original_digest'] == original['content_digest']
        assert row['payload']['body']['original_digest'] != rows[0]['payload']['body']['approved']['content_digest']


@pytest.mark.parametrize('index', [1, 2])
def test_maintenance_refuses_validly_signed_reservation_with_wrong_attempt_digest(outbox_setup, index):
    setup = outbox_setup
    original = renew(setup)
    ingest(setup, receipt_wire(setup, original_digest=original['content_digest'], expires_at=2500))
    rows = terminal_rows(setup)
    target = rows[index]
    body = target['payload']['body']
    method = ('LANTERN-OUTBOX-RECEIPT-REPLAY' if body['schema'].endswith('replay.v1')
              else 'LANTERN-OUTBOX-RECEIPT-SLOT')
    # This is a retained, signed digest belonging to this logical message, but
    # the authenticated receipt names the other attempt. It must not match.
    changed = dict(body, original_digest=rows[0]['payload']['body']['approved']['content_digest'])
    target['payload'] = setup.outbox._proof(changed, method=method)
    checker = inspection(setup, rows)
    with pytest.raises(Exception):
        checker.inspect(rows[0])
    with pytest.raises(Exception):
        checker.inspect(target)


def test_maintenance_requires_both_delayed_receipt_reservations(outbox_setup):
    setup = outbox_setup
    original = renew(setup)
    ingest(setup, receipt_wire(setup, original_digest=original['content_digest'], expires_at=2500))
    rows = terminal_rows(setup)
    for missing in (1, 2):
        retained = rows[:missing] + rows[missing + 1:]
        with pytest.raises(WorkflowUnavailable):
            inspection(setup, retained).inspect(rows[0])


def test_renewed_outbox_remains_visible_with_original_creation_time(mailbox_setup):
    setup = mailbox_setup
    identifier = setup.add_outbox()
    before = setup.mailbox.list_messages('outbox', access_check=setup.access).items[0]
    setup.now = 2000
    radio = bytes.fromhex(setup.rows[identifier]['payload']['body']['approved']['radio_public_key'])
    card = sign_contact_card(setup.peer_identity, chain_id=setup.peer.chain_id,
        radio_public_key=radio.hex(), display_name='Synthetic peer', revision=1,
        issued_at=100, expires_at=10000, now=setup.now, findable=True, messaging_allowed=True)
    setup.outbox.verify_contact = lambda action: verify_contact_card(card, now=setup.now)
    setup.outbox.reapprove(identifier, expected_revision=1, window_seconds=600,
        approval_binding='renewed-owner', consent_binding='renewed-consent', access_check=setup.access)
    rows = copy.deepcopy(setup.rows)
    page = setup.mailbox.list_messages('outbox', access_check=setup.access)
    assert len(page.items) == 1
    after = page.items[0]
    assert (after.id, after.message_id, after.text, after.created_at) == (
        before.id, before.message_id, before.text, before.created_at)
    assert setup.mailbox.open_message(after.id, access_check=setup.access) == after
    assert setup.rows == rows


def test_v2_first_inbox_display_uses_logical_creation_time(mailbox_setup):
    setup = mailbox_setup
    setup.now = 2000
    wire = seal_message(setup.peer_identity._signing_key, sender=setup.peer, recipient=setup.sender,
        message_id=str(uuid.uuid4()), content='Synthetic renewed message',
        issued_at=2000, expires_at=2600, attempt_id=str(uuid.uuid4()),
        logical_issued_at=100, logical_expires_at=1000).wire
    identifier = setup.ingest(wire)
    rows = copy.deepcopy(setup.rows)
    item = setup.mailbox.list_messages('inbox', access_check=setup.access).items[0]
    assert item.local_id == identifier and item.created_at == '1970-01-01T00:01:40Z'
    assert setup.rows[identifier]['payload']['attestation']['body']['admitted_at'] == 2000
    assert setup.mailbox.open_message(item.id, access_check=setup.access) == item
    assert setup.rows == rows


def renewed_in_runtime_store(state):
    now = int(time.time())
    clock = {'now': now - 200}
    card = sign_contact_card(state.remote, chain_id='remote', radio_public_key=state.radio.hex(),
        display_name='Synthetic peer', revision=1, issued_at=now - 1000, expires_at=now + 1000,
        now=clock['now'], findable=True, messaging_allowed=True)
    outbox = MeshOutbox(state.runtime.store, state.identity,
        sender=Peer('local', bytes.fromhex(state.identity.agent_id)), guard=lambda action: True,
        verify_contact=lambda action: verify_contact_card(card, now=clock['now']), clock=lambda: clock['now'])
    identifier = str(uuid.uuid4())
    outbox.queue(message_id=identifier, recipient=Peer('remote', bytes.fromhex(state.remote.agent_id)),
        radio_public_key=state.radio, content='Synthetic renewed runtime message',
        approval_binding=state.context.approval_binding, consent_binding='synthetic-consent',
        issued_at=now - 200, expires_at=now - 100, access_check=lambda: True)
    clock['now'] = now
    outbox.reapprove(identifier, expected_revision=1, window_seconds=600,
        approval_binding=state.context.approval_binding, consent_binding='synthetic-renewed-consent',
        access_check=lambda: True)
    return outbox, identifier, now


def test_runtime_scan_dispatches_signed_v2_and_cleanly_drains(factory):
    state = factory
    state.adopt()
    outbox, identifier, now = renewed_in_runtime_store(state)
    assert identifier not in state.runtime._queued_rows()
    state.runtime._run(state.context, 'background_dispatch', state.runtime._dispatch_pending)
    body = outbox._load(identifier, lambda: True)
    assert body['next_fragment'] > 0 and state.transmits
    item = state.runtime.list_messages(state.context, 'outbox').items[0]
    expected = datetime.fromtimestamp(now - 200, timezone.utc).isoformat().replace('+00:00', 'Z')
    assert item.created_at == expected
    assert state.runtime.close(timeout=5) is True


def test_runtime_drain_refuses_v2_unknown_dispatch(factory):
    state = factory
    state.adopt()
    outbox, identifier, now = renewed_in_runtime_store(state)
    body = outbox._load(identifier, lambda: True)
    outbox._put({**body, 'state': 'dispatching', 'dispatch_timestamp': now, 'attempts': 1},
                body['revision'], lambda: True)
    assert state.runtime.close(timeout=5) is False
    assert state.transmits == []


def test_daemon_reconcile_v2_preserves_window_and_cancel_rules(daemon_runtime, monkeypatch):
    state, _, resource, _, opened = daemon_runtime
    outbox, identifier, now = renewed_in_runtime_store(state)
    body = outbox._load(identifier, lambda: True)
    outbox._put({**body, 'state': 'dispatching', 'dispatch_timestamp': now, 'attempts': 1},
                body['revision'], lambda: True)
    resource._initializing = True
    before = copy.deepcopy(state.rows)
    resource.reconcile(lambda: True)
    assert state.rows == before and opened == [] and state.transmits == []
    monkeypatch.setattr('sage_lantern.mesh_daemon.time.time', lambda: now + 600)
    with pytest.raises(Exception):
        resource.reconcile(lambda: True)
    outbox.cancel(identifier, expected_revision=3, decision_binding='owner-cancel-v2', access_check=lambda: True)
    before = copy.deepcopy(state.rows)
    resource.reconcile(lambda: True)
    assert state.rows == before and opened == [] and state.transmits == []
    resource._initializing = False


@pytest.mark.parametrize('apply', [False, True])
def test_cancel_operator_recognizes_signed_v2_and_preserves_current_window(factory, apply):
    state = factory
    outbox, identifier, now = renewed_in_runtime_store(state)
    rows = CANCEL.cancel_rows(outbox.store, outbox, lambda: True, now=now,
        binding='owner-cancel-v2', only=identifier, apply=apply)
    assert len(rows) == 1 and rows[0]['refused'] == 'window_still_open'
    assert outbox._load(identifier, lambda: True)['state'] == 'queued'
    rows = CANCEL.cancel_rows(outbox.store, outbox, lambda: True, now=now + 600,
        binding='owner-cancel-v2', only=identifier, apply=apply)
    assert rows[0]['eligible_by'] == 'window_lapsed'
    assert rows[0]['outcome'] == ('confirmed' if apply else 'would_cancel')
    assert outbox._load(identifier, lambda: True)['state'] == ('cancelled' if apply else 'queued')
    assert state.transmits == []


@pytest.mark.parametrize('prefix', ['LME1.', 'LME2.'])
def test_custody_roundtrip_accepts_only_bounded_opaque_supported_attempts(prefix):
    envelope = prefix + 'A' * (MAX_ENVELOPE_BYTES - len(prefix))
    wire = wrap_custody(recipient_prefix=b'123456', message_id=MESSAGE,
        envelope_wire=envelope, expires_at=4600, envelope_expires_at=4600, now=1000)
    deposit = parse_custody(wire)
    assert deposit.envelope_wire == envelope and deposit.message_digest == message_digest(MESSAGE)
    with pytest.raises(CustodyError):
        wrap_custody(recipient_prefix=b'123456', message_id=MESSAGE,
            envelope_wire=envelope + 'A', expires_at=4600, envelope_expires_at=4600, now=1000)
    with pytest.raises(CustodyError):
        wrap_custody(recipient_prefix=b'123456', message_id=MESSAGE,
            envelope_wire=envelope, expires_at=4601, envelope_expires_at=4600, now=1000)


@pytest.mark.parametrize('prefix', ['LME0.', 'LME3.', 'lme2.'])
def test_custody_refuses_unknown_envelope_versions_in_wrap_and_parse(prefix):
    envelope = prefix + 'A' * 64
    with pytest.raises(CustodyError):
        wrap_custody(recipient_prefix=b'123456', message_id=MESSAGE,
            envelope_wire=envelope, expires_at=4600, envelope_expires_at=4600, now=1000)
    raw = HEADER.pack(1, b'123456', message_digest(MESSAGE), 4600, len(envelope)) + envelope.encode()
    with pytest.raises(CustodyError):
        parse_custody(PREFIX + base64.b64encode(raw).decode())
