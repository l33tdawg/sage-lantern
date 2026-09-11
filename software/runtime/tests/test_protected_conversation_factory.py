from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nacl.signing import SigningKey
import pytest
from sage_sdk.auth import AgentIdentity

from sage_lantern.config import RuntimeConfig
from sage_lantern.conversation_service import ConversationService, _DeferredConversationStore


def configuration(directory):
    return SimpleNamespace(protected_storage=True, sage_chain_id="synthetic-private-chain",
        sage_base_url="http://127.0.0.1:19100", llama_base_url="http://127.0.0.1:19101",
        sage_identity_path=Path("synthetic-key-not-read"), sage_domain="synthetic.home",
        conversation_dir=directory.resolve())


def test_protected_factory_defers_vault_access_and_owns_client(tmp_path):
    identity = AgentIdentity(SigningKey(b"p" * 32))
    closed = []
    store = SimpleNamespace(consent=lambda: "ticket", close=lambda: closed.append(True))
    with patch("sage_sdk.AgentIdentity.from_file", return_value=identity), \
            patch("sage_lantern.conversation_service.ConversationStore", side_effect=AssertionError("plaintext fallback")), \
            patch("sage_lantern.conversation_workflow_store.ConversationWorkflowStore", return_value=store) as factory:
        service = ConversationService.from_config(configuration(tmp_path))
        service._epoch_reader = lambda: "a" * 32
        service._admitted = lambda: not service.stopped.is_set()
        assert service.protected_storage is True and service.media_store is not None
        assert not factory.called
        assert service._owned_client._client.trust_env is False
        assert service._owned_client._client.timeout.read == 5
        assert service.store.consent() == "ticket"
        assert service.store.consent() == "ticket"
        assert factory.call_count == 1
        assert service.close(timeout=0) is True
        assert service._owned_client._client.is_closed
        assert closed == [True]


def test_close_before_unlock_does_not_initialize_storage(tmp_path):
    identity = AgentIdentity(SigningKey(b"p" * 32))
    with patch("sage_sdk.AgentIdentity.from_file", return_value=identity), \
            patch("sage_lantern.conversation_workflow_store.ConversationWorkflowStore") as factory:
        service = ConversationService.from_config(configuration(tmp_path))
        assert service.close(timeout=0) is True
        factory.assert_not_called()
        with pytest.raises(RuntimeError):
            service.store.consent()


def test_deferred_initialization_failure_never_falls_back():
    calls = []

    def create():
        calls.append(True)
        raise RuntimeError("synthetic locked vault")

    store = _DeferredConversationStore(create)
    with pytest.raises(RuntimeError):
        store.consent()
    assert calls == [True]
    store.close()
    with pytest.raises(RuntimeError):
        store.consent()
    assert calls == [True]


@pytest.mark.parametrize("settings,valid", [
    ('protected_storage = false', True),
    ('protected_storage = true\nchain_id = "private-chain"', False),
    ('protected_storage = true\nchain_id = "private-chain"\n'
     'conversation_dir="/var/lib/sage-lantern/history/conversations-v1"\n'
     'identity_path="/var/lib/sage-lantern/identity/lantern-agent.key"', True),
    ('protected_storage = true', False),
    ('protected_storage = "yes"', False),
    ('protected_storage = true\nchain_id = 42', False),
])
def test_protected_config_requires_explicit_binding(tmp_path, settings, valid):
    config = tmp_path / "runtime.toml"
    config.write_text('[runtime]\nstate_log="state"\noutput_dir="output"\n'
                      '[stt]\nbinary="whisper"\nmodel="model"\n'
                      '[llm]\nbase_url="http://127.0.0.1:19101"\nmodel="model"\n'
                      '[tts]\nenabled=false\n[sage]\nbase_url="http://127.0.0.1:19100"\n' + settings)
    if valid:
        loaded = RuntimeConfig.load(config)
        assert type(loaded.protected_storage) is bool
    else:
        with pytest.raises(ValueError):
            RuntimeConfig.load(config)
