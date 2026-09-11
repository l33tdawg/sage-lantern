import threading
from types import SimpleNamespace

import pytest

from sage_lantern.conversation_service import ConversationService
from sage_lantern.private_media_store import ProtectedMediaOutcomeUnknown


class MediaDouble:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.after = lambda: None

    def get(self, identifier, *, access_check, expected_digest=None):
        self.calls.append(("get", identifier, expected_digest))
        assert access_check() is True
        self.entered.set()
        assert self.release.wait(2)
        self.after()
        return b"synthetic private media"

    def put(self, identifier, image, *, access_check):
        self.calls.append(("put", identifier, image))
        assert access_check() is True
        self.entered.set()
        assert self.release.wait(2)
        self.after()
        return "synthetic digest"


def fixture():
    provider = MediaDouble()
    closed = []
    service = ConversationService(SimpleNamespace(close=lambda: closed.append(True)), None, media_store=provider)
    service._admitted = lambda: not service.stopped.is_set()
    return service, provider, closed


@pytest.mark.parametrize("operation", ["get", "put"])
def test_media_calls_participate_in_stop_and_close(operation):
    service, provider, closed = fixture()
    results = []

    def run():
        try:
            if operation == "get":
                results.append(service.media_store.get("id", access_check=lambda: True, expected_digest="digest"))
            else:
                results.append(service.media_store.put("id", b"image", access_check=lambda: True))
        except Exception as error:
            results.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert provider.entered.wait(1)
        assert service.close(timeout=0) is False
        assert not closed
        with pytest.raises(RuntimeError):
            service.media_store.get("other", access_check=lambda: True)
    finally:
        provider.release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert len(provider.calls) == 1
    assert len(results) == 1 and isinstance(results[0], Exception)
    if operation == "put":
        assert isinstance(results[0], ProtectedMediaOutcomeUnknown)
    assert service.close(timeout=1) is True
    assert closed == [True]


@pytest.mark.parametrize("operation", ["get", "put"])
def test_revoked_session_denies_before_provider(operation):
    service, provider, _ = fixture()
    with pytest.raises(RuntimeError):
        if operation == "get":
            service.media_store.get("id", access_check=lambda: False)
        else:
            service.media_store.put("id", b"image", access_check=lambda: False)
    assert not provider.calls
    assert service.close(timeout=0) is True


def test_success_preserves_digest_and_bytes():
    service, provider, _ = fixture()
    provider.release.set()
    assert service.media_store.put("id", b"image", access_check=lambda: True) == "synthetic digest"
    assert service.media_store.get("id", expected_digest="digest", access_check=lambda: True) == b"synthetic private media"
    assert provider.calls[-1] == ("get", "id", "digest")
    assert service.close(timeout=0) is True


def test_intent_forwarding_uses_same_reflection_as_conversation():
    service, _, _ = fixture()
    calls = []
    service.store.enqueue = lambda ticket, item: calls.append((ticket, item))

    def reserve(session_id, digest, *, ticket, item, access_check):
        assert access_check() is True
        calls.append((ticket, item))
        return (session_id, digest)

    service.store.reserve_media = reserve
    service.store.commit_media = lambda intent, *, access_check: access_check() is True
    item = dict(question="Synthetic question", answer="Synthetic answer", has_photo=True)
    service.record("ticket", item)
    intent = service.reserve_media("session", "digest", ticket="ticket", item=item, access_check=lambda: True)
    assert calls[0] == calls[1]
    assert "reflection" not in item
    assert intent == ("session", "digest")
    assert service.commit_media(intent, access_check=lambda: True) is True
    assert service.close(timeout=0) is True


def test_intent_commit_requires_exact_success_and_live_guard():
    service, _, _ = fixture()
    calls = []
    service.store.commit_media = lambda *args, **kwargs: calls.append(True)
    with pytest.raises(RuntimeError):
        service.commit_media("intent", access_check=lambda: False)
    assert not calls
    with pytest.raises(RuntimeError):
        service.commit_media("intent", access_check=lambda: True)
    assert calls == [True]
    assert service.close(timeout=0) is True


@pytest.mark.parametrize("operation", ["get", "put"])
def test_lock_unlock_between_checks_does_not_revive_old_operation(operation):
    service, provider, _ = fixture()
    del service._admitted
    service.protected_storage = True
    service._owner = SimpleNamespace(valid=lambda: True, close=lambda: None)
    state = {"epoch": "a" * 32}
    service._epoch_reader = lambda: state["epoch"]
    provider.release.set()
    provider.after = lambda: state.update(epoch="b" * 32)
    with pytest.raises(RuntimeError):
        if operation == "get":
            service.media_store.get("id", access_check=lambda: True)
        else:
            service.media_store.put("id", b"image", access_check=lambda: True)
    provider.after = lambda: None
    assert service.media_store.get("new", access_check=lambda: True) == b"synthetic private media"
    assert service.close(timeout=0) is True


def test_nested_activity_cannot_refresh_changed_epoch():
    service, _, _ = fixture()
    del service._admitted
    service.protected_storage = True
    service._owner = SimpleNamespace(valid=lambda: True, close=lambda: None)
    state = {"epoch": "a" * 32}
    service._epoch_reader = lambda: state["epoch"]
    with service._activity():
        assert service._admitted() is True
        state["epoch"] = "b" * 32
        assert service._admitted() is False
        with pytest.raises(RuntimeError):
            with service._activity():
                raise AssertionError("old activity adopted new epoch")
    with service._activity():
        assert service._admitted() is True
    assert service.close(timeout=0) is True
