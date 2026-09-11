"""Real sibling SDK contract, with synthetic trust and no network or activation."""

import hashlib
import importlib
import json
from pathlib import Path
import struct
import sys
import uuid

import httpx
from nacl.signing import VerifyKey
import pytest

from sage_lantern.canonical_messaging import CanonicalMessageSender
from sage_lantern.community_messaging import CommunityMessaging, MessagingUnavailable, Prerequisites
from sage_lantern.message_contacts import ContactClaim, RecipientID
from sage_lantern.message_storage import MessageStorageProbe


@pytest.fixture
def sdk(monkeypatch):
    source = Path(__file__).resolve().parents[4] / 'sage/sdk/python/src'
    monkeypatch.syspath_prepend(str(source))
    previous = {name: module for name, module in sys.modules.items()
                if name == 'sage_sdk' or name.startswith('sage_sdk.')}
    for name in previous:
        del sys.modules[name]
    try:
        module = importlib.import_module('sage_sdk')
        assert Path(module.__file__).resolve().is_relative_to(source.resolve())
        yield module
    finally:
        for name in list(sys.modules):
            if name == 'sage_sdk' or name.startswith('sage_sdk.'):
                del sys.modules[name]
        sys.modules.update(previous)


@pytest.mark.parametrize('result', ['success', 'generation', 'instance', '503'])
def test_real_sdk_protected_send_contract(sdk, tmp_path, result):
    endpoint = 'http://127.0.0.1:19100'
    identity = sdk.AgentIdentity.from_seed(bytes(range(32)))
    actor = RecipientID('test-home', identity.agent_id)
    recipient = RecipientID('test-home', 'b' * 64)
    request_id = str(uuid.UUID('12345678-1234-4234-8234-123456789abc'))
    payload = 'Synthetic approved text: café'
    requests = []
    dispatched = False

    def respond(request):
        nonlocal dispatched
        requests.append(request)
        if request.method == 'GET' and request.url.path == '/v1/messages/storage':
            return httpx.Response(200, json={
                'schema': 'sage.message-storage.v1',
                'instance_id': ('e' if dispatched and result == 'instance' else 'd') * 64,
                'agent_id': actor.agent_id, 'encryption_expected': True,
                'vault_active': True,
                'vault_generation': '3' if dispatched and result == 'generation' else '2',
                'stable': True, 'canonical_send_idempotency': True,
                'encrypted_send_admission': True,
            })
        if request.method == 'POST' and request.url.path == '/v1/pipe/resolve':
            return httpx.Response(200, json={'to_agent': recipient.agent_id})
        if request.method == 'POST' and request.url.path == '/v1/messages':
            dispatched = True
            if result == '503':
                return httpx.Response(503, json={
                    'title': 'Encrypted message storage required', 'status': 503,
                    'detail': 'synthetic diagnostic must not escape',
                })
            return httpx.Response(201, json={
                'message_id': 'synthetic-message', 'status': 'pending',
                'idempotent_replay': False, 'expires_at': '2026-09-07T12:00:00Z',
            })
        return httpx.Response(500)

    client = sdk.SageClient(base_url=endpoint, identity=identity, timeout=2, trust_env=False)
    client._client.close()
    with httpx.Client(base_url=endpoint, timeout=2, trust_env=False,
                      follow_redirects=False, transport=httpx.MockTransport(respond)) as transport:
        client._client = transport
        state = Prerequisites(actor, True, False, True, True, True, 'test-operator', 'test-policy')
        claim = ContactClaim(actor, recipient, 'Test peer', 'test-operator', 'test-policy',
                             1, 100, 160, True, True)

        def verifier(caller, target, record):
            assert caller == actor and target == recipient
            assert record['to_agent'] == recipient.agent_id
            return claim

        adapter = CommunityMessaging(client, actor=actor, prerequisites=lambda: state,
                                     guard=lambda request: True, verify_recipient=verifier,
                                     clock=lambda: 110)
        probe = MessageStorageProbe(client, actor=actor, endpoint=endpoint)
        directory = tmp_path / 'attempts'
        directory.mkdir(mode=0o700)
        sender = CanonicalMessageSender(adapter, probe, directory).bind(adapter)
        outcome = sender.send('test-session', request_id=request_id, recipient=recipient,
                              payload=payload, access_check=lambda: True)

        assert outcome.outcome == ('accepted_by_node' if result == 'success' else 'unknown')
        assert 'synthetic diagnostic' not in repr(outcome)
        markers = list(directory.iterdir())
        if result == 'success':
            assert outcome.message_id == 'synthetic-message'
            assert markers == []
            assert sender.drained() is True
        else:
            assert len(markers) == 1
            assert markers[0].read_bytes() == (uuid.UUID(request_id).hex + '\n').encode()
            assert markers[0].stat().st_mode & 0o777 == 0o600
            assert sender.drained() is False
            restarted = CanonicalMessageSender(adapter, probe, directory)
            count = len(requests)
            assert restarted.ready() is False
            with pytest.raises(MessagingUnavailable, match='^messaging_unavailable$'):
                restarted.send('new-session', request_id=str(uuid.uuid4()), recipient=recipient,
                               payload=payload, access_check=lambda: True)
            assert len(requests) == count

    expected_bodies = {
        '/v1/messages/storage': b'',
        '/v1/pipe/resolve': json.dumps({'to': recipient.agent_id}, separators=(',', ':')).encode(),
        '/v1/messages': json.dumps({
            'to_agent': recipient.agent_id, 'payload': payload, 'idempotency_key': request_id,
            'ttl_minutes': 1440, 'require_encrypted_storage': True,
        }, separators=(',', ':')).encode(),
    }
    assert requests[0].url.path == '/v1/messages/storage'
    assert sum(request.url.path == '/v1/messages' for request in requests) == 1
    assert sum(request.url.path == '/v1/pipe/resolve' for request in requests) == 1
    nonces = set()
    for request in requests:
        path = request.url.path
        assert path in expected_bodies
        assert str(request.url) == endpoint + path
        assert request.method == ('GET' if path == '/v1/messages/storage' else 'POST')
        assert request.content == expected_bodies[path]
        if request.method == 'POST':
            assert request.headers['content-type'] == 'application/json'
        assert request.headers['x-agent-id'] == actor.agent_id
        nonce = bytes.fromhex(request.headers['x-nonce'])
        assert len(nonce) == 8 and nonce not in nonces
        nonces.add(nonce)
        canonical = request.method.encode() + b' ' + path.encode() + b'\n' + request.content
        signed = (hashlib.sha256(canonical).digest()
                  + struct.pack('>q', int(request.headers['x-timestamp'])) + nonce)
        VerifyKey(bytes.fromhex(actor.agent_id)).verify(
            signed, bytes.fromhex(request.headers['x-signature']))
