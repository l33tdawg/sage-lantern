"""Rendered consent through service, Root IPC and protected synthetic storage."""

import copy
import struct
import time
import uuid

import pytest

from sage_lantern.community_service import CommunityError
from sage_lantern.community_service import CommunityService
from test_community_mesh_service import setup, mailbox_setup
from test_mesh_daemon import runtime, socket_connector
from test_mesh_runtime import factory
from sage_lantern.mesh_daemon import MeshDaemon
from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
from sage_lantern.mesh_envelope import Peer, seal_message
from sage_lantern.mesh_fragments import split_envelope


INBOX_ID = 'mesh:inbox:10000000-0000-4000-8000-000000000001:' + 'a' * 64
CHALLENGE_ID = '20000000-0000-4000-8000-000000000001'
RECEIPT_ID = '30000000-0000-4000-8000-000000000001'


@pytest.fixture
def wired(runtime):
    state, supervisor, resource, config, opened = runtime
    now = int(time.time())
    envelope = seal_message(state.remote._signing_key,
        sender=Peer('remote', bytes.fromhex(state.remote.agent_id)),
        recipient=Peer('local', bytes.fromhex(state.identity.agent_id)),
        message_id=str(uuid.uuid4()), content='Synthetic rendered text', issued_at=now, expires_at=now + 900)
    for fragment in split_envelope(envelope.wire.encode()):
        state.pending.append(b'\x07' + state.radio[:6] + bytes(2) + struct.pack('<I', now) + fragment.encode())
        assert state.runtime.poll_once(state.context) == 'captured'
    daemon = MeshDaemon(supervisor, resource, epoch_reader=lambda: 'e' * 32)
    connector, workers = socket_connector(daemon)
    state.facade = MeshVoiceFacade(connector=connector, epoch_reader=lambda: 'e' * 32)
    state.service = CommunityService(mesh_provider=state.facade)
    state.session = 'current-ui-session'
    state.allowed = True
    state.call = lambda action, body: state.service.dispatch('/community/messages/' + action, body,
        lambda: state.allowed, session_id=state.session)
    state.identifier = state.call('list', dict(box='inbox'))['items'][0]['id']
    yield state
    supervisor.phase = 'draining'
    state.service.close(3)
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()


def challenge(state):
    return state.call('render-challenge', dict(id=state.identifier, rendered=True))


def acknowledge(state, value):
    return state.call('rendered', dict(id=value['id'], challenge_id=value['challenge_id'],
                                      rendered=True, share_read_receipt=True))


def test_actual_service_root_sdk_roundtrip_requires_render_and_consent(wired):
    before = copy.deepcopy(wired.rows)
    wired.call('open', dict(id=wired.identifier, share_read_receipt=False))
    value = challenge(wired)
    assert wired.rows == before and not wired.transmits
    result = acknowledge(wired, value)
    assert result['id'] == wired.identifier and result['state'] == 'queued'
    assert uuid.UUID(result['receipt_id']).int and wired.rows != before and not wired.transmits
    after = copy.deepcopy(wired.rows)
    with pytest.raises(CommunityError):
        acknowledge(wired, value)
    assert wired.rows == after


@pytest.mark.parametrize('failure', ['session', 'message', 'lock', 'revoke', 'expiry'])
def test_challenge_cannot_cross_ui_binding_or_expiry(wired, failure):
    value = challenge(wired)
    before = copy.deepcopy(wired.rows)
    if failure == 'session':
        wired.session = 'different-ui-session'
    elif failure == 'message':
        value['id'] = INBOX_ID
    elif failure == 'lock':
        wired.service.lock()
    elif failure == 'revoke':
        wired.allowed = False
    else:
        wired.service._wall_clock = lambda: value['expires_at']
    with pytest.raises(CommunityError):
        acknowledge(wired, value)
    assert wired.rows == before and not wired.transmits


@pytest.mark.parametrize('failure', ['lost', 'lock', 'malformed'])
def test_after_real_ack_dispatch_unknown_never_replayed(wired, monkeypatch, failure):
    value = challenge(wired)
    original = wired.facade.rendered
    calls = []
    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if failure == 'lost':
            raise OSError('synthetic lost response')
        if failure == 'lock':
            wired.service.lock()
        else:
            result['id'] = INBOX_ID
        return result
    monkeypatch.setattr(wired.facade, 'rendered', interrupted)
    with pytest.raises(CommunityError) as caught:
        acknowledge(wired, value)
    assert caught.value.code == 'outcome_unknown' and len(calls) == 1
    after = copy.deepcopy(wired.rows)
    with pytest.raises(CommunityError):
        acknowledge(wired, value)
    assert len(calls) == 1 and wired.rows == after and not wired.transmits


def test_revocation_before_ipc_submission_creates_no_receipt(wired, monkeypatch):
    value = challenge(wired)
    before = copy.deepcopy(wired.rows)
    original = wired.facade.rendered
    def revoked(*args, **kwargs):
        wired.allowed = False
        return original(*args, **kwargs)
    monkeypatch.setattr(wired.facade, 'rendered', revoked)
    with pytest.raises(CommunityError):
        acknowledge(wired, value)
    assert wired.rows == before and not wired.transmits and not wired.service._uncertain


def test_reopened_service_cannot_reuse_previous_render_challenge(wired):
    value = challenge(wired)
    before = copy.deepcopy(wired.rows)
    wired.service = CommunityService(mesh_provider=wired.facade)
    with pytest.raises(CommunityError):
        acknowledge(wired, value)
    assert wired.rows == before and not wired.transmits


@pytest.mark.parametrize('change', [dict(id=INBOX_ID), dict(expires_at=True),
                                   dict(expires_at=2**53), dict(challenge_id='invalid')])
def test_malformed_root_challenge_is_not_admitted(wired, monkeypatch, change):
    before = copy.deepcopy(wired.rows)
    original = wired.facade.render_challenge
    def malformed(*args, **kwargs):
        return dict(original(*args, **kwargs), **change)
    monkeypatch.setattr(wired.facade, 'render_challenge', malformed)
    with pytest.raises(CommunityError):
        challenge(wired)
    assert not wired.service._render_challenges and wired.rows == before


def test_challenge_capacity_is_bounded_before_root_call(wired, monkeypatch):
    calls = []
    def fresh(identifier, *, rendered, access_check):
        assert rendered and access_check()
        calls.append(identifier)
        return dict(id=identifier, challenge_id=str(uuid.uuid4()), expires_at=int(time.time()) + 60)
    monkeypatch.setattr(wired.facade, 'render_challenge', fresh)
    for unused in range(32):
        challenge(wired)
    with pytest.raises(CommunityError) as caught:
        challenge(wired)
    assert caught.value.code == 'busy' and len(calls) == 32


@pytest.mark.parametrize('action,body', [
    ('render-challenge', dict(id=INBOX_ID, rendered=True)),
    ('rendered', dict(id=INBOX_ID, challenge_id=CHALLENGE_ID, rendered=True, share_read_receipt=True)),
])
def test_exact_read_route_request_schema(action, body):
    path = '/community/messages/' + action
    CommunityService._validate(path, body)
    changes = [dict(body, extra=True), dict(body, rendered=False), dict(body, rendered=1),
               dict(body, id=INBOX_ID.replace('inbox', 'outbox')),
               dict(body, id='mesh:inbox:00000000-0000-0000-0000-000000000000')]
    if action == 'rendered':
        changes += [dict(body, share_read_receipt=False), dict(body, share_read_receipt=1),
                    dict(body, challenge_id='00000000-0000-0000-0000-000000000000')]
    for invalid in changes:
        with pytest.raises(CommunityError) as caught:
            CommunityService._validate(path, invalid)
        assert caught.value.status == 400


def test_read_response_does_not_claim_delivery_or_read():
    path = '/community/messages/rendered'
    for state in ('queued', 'serial_accepted', 'unknown'):
        CommunityService._validate_response(path, dict(id=INBOX_ID, receipt_id=RECEIPT_ID, state=state))
    for state in ('read', 'delivered', 'sent', 'accepted', True, None):
        with pytest.raises(CommunityError):
            CommunityService._validate_response(path, dict(id=INBOX_ID, receipt_id=RECEIPT_ID, state=state))


@pytest.mark.parametrize('change', [dict(expires_at=True), dict(expires_at=0), dict(expires_at=1.5),
                                   dict(challenge_id='invalid'), dict(extra='private')])
def test_render_challenge_response_rejects_unsafe_shapes(change):
    response = dict(id=INBOX_ID, challenge_id=CHALLENGE_ID, expires_at=1234567890)
    CommunityService._validate_response('/community/messages/render-challenge', response)
    with pytest.raises(CommunityError):
        CommunityService._validate_response('/community/messages/render-challenge', dict(response, **change))


def test_actual_open_never_creates_consent_or_receipt(setup):
    setup.add_inbox()
    before = copy.deepcopy(setup.rows)
    item = setup.call('messages/list', {'box': 'inbox'})['items'][0]
    result = setup.call('messages/open', {'id': item['id'], 'share_read_receipt': False})
    assert result['id'] == item['id'] and setup.rows == before
    assert setup.call('status')['sharing']['available'] is False


@pytest.mark.parametrize('value', [True, 1, 'true', None])
def test_open_cannot_smuggle_render_consent(setup, value):
    setup.add_inbox()
    identifier = setup.call('messages/list', {'box': 'inbox'})['items'][0]['id']
    before = copy.deepcopy(setup.rows)
    with pytest.raises(CommunityError):
        setup.call('messages/open', {'id': identifier, 'share_read_receipt': value})
    assert setup.rows == before


@pytest.mark.parametrize('route,body', [
    ('messages/render-challenge', {'id': 'message-1', 'rendered': True}),
    ('messages/rendered', {'id': 'message-1', 'challenge_id': '00000000-0000-4000-8000-000000000001',
                           'rendered': True, 'share_read_receipt': True}),
])
def test_unwired_provider_and_revoked_session_fail_closed(setup, route, body):
    before = copy.deepcopy(setup.rows)
    with pytest.raises(CommunityError):
        setup.call(route, body)
    assert setup.rows == before
    setup.allowed = False
    with pytest.raises(CommunityError) as caught:
        setup.call(route, body)
    assert caught.value.status == 403 and setup.rows == before
