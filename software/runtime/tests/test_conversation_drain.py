import threading
from types import SimpleNamespace

import pytest

from sage_lantern.conversation_records import ConversationDrainSnapshot
from sage_lantern.conversation_service import ConversationService
from sage_lantern.protected_conversation_owner import ProtectedConversationOwner, ConversationOwnerError


def service_with_snapshot(tmp_path, **counts):
    owner = ProtectedConversationOwner(tmp_path.resolve())
    closed = []
    store = SimpleNamespace(close=lambda: closed.append(True))
    service = ConversationService(store, None)
    service.protected_storage = True
    service._owner = owner
    service._epoch_reader = lambda: "a" * 32
    values = dict(queued=3, dispatching=0, unknown=0, pending_media=0)
    values.update(counts)

    def snapshot():
        assert service._drain_admitted() is True
        return ConversationDrainSnapshot("synthetic-storage-epoch", **values)

    store.drain_snapshot = snapshot
    return service, closed


def test_queued_only_release_closes_under_exclusive_ownership(tmp_path):
    service, closed = service_with_snapshot(tmp_path)
    with pytest.raises(ConversationOwnerError):
        ProtectedConversationOwner(tmp_path.resolve())
    assert service.release(timeout=1) is True
    assert closed == [True]
    assert service._owner.valid() is False
    assert service.release(timeout=1) is False
    replacement = ProtectedConversationOwner(tmp_path.resolve())
    replacement.close()


@pytest.mark.parametrize("field", ["dispatching", "unknown", "pending_media"])
def test_unresolved_state_retains_owner_and_client(tmp_path, field):
    service, closed = service_with_snapshot(tmp_path, **{field: 1})
    try:
        assert service.release(timeout=1) is False
        assert not closed and service._owner.valid() is True
        with pytest.raises(ConversationOwnerError):
            ProtectedConversationOwner(tmp_path.resolve())
    finally:
        service.close(timeout=1)


def test_active_producer_prevents_snapshot(tmp_path):
    service, closed = service_with_snapshot(tmp_path)
    entered, leave = threading.Event(), threading.Event()

    def producer():
        with service._activity():
            entered.set()
            assert leave.wait(2)

    worker = threading.Thread(target=producer)
    worker.start()
    try:
        assert entered.wait(1)
        assert service.release(timeout=0.01) is False
        assert not closed and service._owner.valid() is True
    finally:
        leave.set()
        worker.join(2)
        service.close(timeout=1)


def test_unavailable_snapshot_is_not_zero(tmp_path):
    service, closed = service_with_snapshot(tmp_path)
    service.store.drain_snapshot = lambda: None
    try:
        assert service.release(timeout=1) is False
        assert not closed and service._owner.valid() is True
    finally:
        service.close(timeout=1)


def test_closure_failure_retains_owner(tmp_path):
    service, _ = service_with_snapshot(tmp_path)

    def fail():
        raise RuntimeError("synthetic closure failure")

    service.store.close = fail
    try:
        assert service.release(timeout=1) is False
        assert service._owner.valid() is True
        assert service._drain_admitted() is False
    finally:
        service._owner.close()


def test_drain_lease_expires_after_snapshot(tmp_path):
    service, _ = service_with_snapshot(tmp_path)
    assert service._drain_admitted() is False
    snapshot = service.drain_snapshot(timeout=1)
    assert snapshot.queued == 3
    assert service._drain_admitted() is False
    assert service._owner.valid() is True
    service.close(timeout=1)


def test_other_thread_cannot_borrow_drain_lease(tmp_path):
    service, _ = service_with_snapshot(tmp_path)
    results = []

    def snapshot():
        assert service._drain_admitted() is True
        worker = threading.Thread(target=lambda: results.append(service._drain_admitted()))
        worker.start()
        worker.join(1)
        assert not worker.is_alive()
        return ConversationDrainSnapshot("synthetic-epoch", 0, 0, 0, 0)

    service.store.drain_snapshot = snapshot
    assert service.release(timeout=1) is True
    assert results == [False]
