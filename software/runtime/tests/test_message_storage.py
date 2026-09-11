from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from sage_lantern.community_messaging import MessagingUnavailable, Prerequisites
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe


@pytest.fixture
def fixture():
    actor = RecipientID('home', 'a' * 64)
    transport = httpx.Client(base_url='http://127.0.0.1:19100', trust_env=False, timeout=2)
    payload = dict(schema='sage.message-storage.v1', instance_id='b' * 64, agent_id=actor.agent_id,
                   encryption_expected=True, vault_active=True, vault_generation='2', stable=True,
                   canonical_send_idempotency=True, encrypted_send_admission=True)
    client = SimpleNamespace(_client=transport, _identity=SimpleNamespace(agent_id=actor.agent_id),
                             _base_url='http://127.0.0.1:19100', message_storage_status=Mock(return_value=payload))
    probe = MessageStorageProbe(client, actor=actor, endpoint=client._base_url)
    yield actor, client, probe, payload
    transport.close()


def test_probe_observes_actual_generation_without_granting_other_rights(fixture):
    actor, client, probe, payload = fixture
    first = probe.prerequisites(Prerequisites(actor))
    assert first.encrypted_storage_verified is True
    assert first.authenticated_node_and_signer is False
    assert first.recipient_policy_verified is False
    assert first.local_send_supported is False
    payload['vault_generation'] = '3'
    assert probe.prerequisites(Prerequisites(actor)) != first
    payload['instance_id'] = 'c' * 64
    assert probe.snapshot().epoch == 'c' * 64 + ':3'
    assert client.message_storage_status.call_count == 3


@pytest.mark.parametrize('change', [
    {'vault_active': False}, {'encryption_expected': False}, {'stable': False},
    {'canonical_send_idempotency': False}, {'encrypted_send_admission': False}, {'vault_active': 1}, {'vault_generation': 2},
    {'vault_generation': '02'}, {'vault_generation': '0'}, {'vault_generation': str(1 << 64)},
    {'instance_id': 'unknown'}, {'agent_id': 'd' * 64}, {'extra': True}, {'schema': 'old'},
])
def test_invalid_or_locked_observation_denied(fixture, change):
    _, _, probe, payload = fixture
    payload.update(change)
    with pytest.raises(MessagingUnavailable):
        probe.snapshot()


def test_no_cached_success_after_backend_failure(fixture):
    _, client, probe, _ = fixture
    probe.snapshot()
    client.message_storage_status.side_effect = RuntimeError('private details')
    with pytest.raises(MessagingUnavailable, match='^messaging_unavailable$'):
        probe.snapshot()


def test_transport_replacement_during_probe_denied(fixture):
    _, client, probe, payload = fixture
    def replace_transport():
        client._client = object()
        return payload
    client.message_storage_status.side_effect = replace_transport
    with pytest.raises(MessagingUnavailable):
        probe.snapshot()


@pytest.mark.parametrize('endpoint', ['https://example.org', 'http://localhost:19100',
                                   'http://127.0.0.1:19100/path', 'http://127.0.0.1:99999'])
def test_endpoint_is_exact_local_configuration(fixture, endpoint):
    actor, client, _, _ = fixture
    with pytest.raises(MessagingUnavailable):
        MessageStorageProbe(client, actor=actor, endpoint=endpoint)


@pytest.mark.parametrize('options', [{'trust_env': True}, {'follow_redirects': True}, {'timeout': None}])
def test_proxy_redirect_and_unbounded_transport_denied(fixture, options):
    actor, client, _, _ = fixture
    settings = dict(base_url=client._base_url, trust_env=False, timeout=2, follow_redirects=False)
    settings.update(options)
    with httpx.Client(**settings) as transport:
        client._client = transport
        with pytest.raises(MessagingUnavailable):
            MessageStorageProbe(client, actor=actor, endpoint=client._base_url)
