from types import SimpleNamespace
import uuid

import httpx
import pytest

import test_community_service as base
from sage_lantern.canonical_messaging import CanonicalMessageSender
from sage_lantern.community_service import CommunityError, CommunityService
from sage_lantern.message_storage import MessageStorageProbe


@pytest.fixture
def fixture(tmp_path):
    harness = base.CommunityServiceTests()
    harness.setUp()
    endpoint = 'http://127.0.0.1:19100'
    transport = httpx.Client(base_url=endpoint, trust_env=False, timeout=2)
    harness.sdk._client = transport
    harness.sdk._base_url = endpoint
    harness.sdk._identity = SimpleNamespace(agent_id=harness.actor.agent_id)
    harness.sdk.message_storage_status.return_value = dict(
        schema='sage.message-storage.v1', instance_id='d' * 64, agent_id=harness.actor.agent_id,
        encryption_expected=True, vault_active=True, vault_generation='2', stable=True,
        canonical_send_idempotency=True, encrypted_send_admission=True)
    directory = tmp_path / 'attempts'
    directory.mkdir(mode=0o700)
    probe = MessageStorageProbe(harness.sdk, actor=harness.actor, endpoint=endpoint)
    sender = CanonicalMessageSender(harness.messaging, probe, directory)
    def service():
        return CommunityService(community_messaging=harness.messaging, canonical_messages=sender,
                                contact_names=('Peer',), clock=lambda: harness.now, wall_clock=lambda: 1700000000)
    harness.service = service()
    harness.call('contacts/list')
    yield harness, directory, service
    transport.close()


def test_canonical_send_uses_encrypted_server_without_local_content_store(fixture):
    harness, directory, _ = fixture
    assert harness.call('status')['messaging']['available'] is True
    response = harness.call('messages/send', harness.send_body())
    assert response['state'] == 'sent'
    assert harness.sdk.message_send.call_args.kwargs['require_encrypted_storage'] is True
    assert harness.sdk.message_send.call_args.kwargs['idempotency_key'] == base.REQUEST
    assert list(directory.iterdir()) == []
    assert harness.service.close(1) is True


def test_unknown_send_persists_only_uuid_and_blocks_restart_resend(fixture):
    harness, directory, new_service = fixture
    harness.sdk.message_send.side_effect = TimeoutError('unknown private text')
    with pytest.raises(CommunityError, match='outcome_unknown'):
        harness.call('messages/send', harness.send_body())
    markers = list(directory.iterdir())
    assert len(markers) == 1
    assert markers[0].read_text() == uuid.UUID(base.REQUEST).hex + '\n'
    assert harness.service.close(1) is False
    harness.service = new_service()
    assert harness.call('status')['messaging']['available'] is False
    harness.call('contacts/list')
    with pytest.raises(CommunityError):
        harness.call('messages/send', {**harness.send_body(), 'request_id': str(uuid.uuid4())})
    assert harness.sdk.message_send.call_count == 1
    assert harness.service.close(1) is False


@pytest.mark.parametrize('change', ['lock', 'generation', 'instance'])
def test_post_dispatch_revocation_or_epoch_change_retains_marker(fixture, change):
    harness, directory, _ = fixture
    reply = harness.sdk.message_send.return_value
    def changed(**kwargs):
        if change == 'lock':
            harness.allowed = False
        elif change == 'generation':
            harness.sdk.message_storage_status.return_value['vault_generation'] = '3'
        else:
            harness.sdk.message_storage_status.return_value['instance_id'] = 'e' * 64
        return reply
    harness.sdk.message_send.side_effect = changed
    with pytest.raises(CommunityError, match='outcome_unknown'):
        harness.call('messages/send', harness.send_body())
    assert len(list(directory.iterdir())) == 1
    assert harness.service.close(1) is False


def test_missing_attempt_directory_does_not_enable_or_dispatch(fixture):
    harness, directory, _ = fixture
    directory.rmdir()
    assert harness.call('status')['messaging']['available'] is False
    with pytest.raises(CommunityError):
        harness.call('messages/send', harness.send_body())
    harness.sdk.message_send.assert_not_called()


def test_public_sharing_still_requires_protected_public_archive(fixture):
    harness, _, _ = fixture
    assert harness.call('status')['sharing']['available'] is False


def test_new_session_does_not_inherit_recipient_consent(fixture):
    harness, directory, _ = fixture
    harness.service.lock()
    harness.session_id = 'new-auth-session'
    with pytest.raises(CommunityError):
        harness.call('messages/send', harness.send_body())
    harness.sdk.message_send.assert_not_called()
    assert list(directory.iterdir()) == []
