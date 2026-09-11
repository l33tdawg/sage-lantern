import copy
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid
from unittest.mock import Mock, patch

import pytest

from sage_lantern import canonical_messaging
from sage_lantern.canonical_messaging import CanonicalMessageSender
from sage_lantern.community_messaging import MessagingUnavailable
from sage_lantern.inference_guard import InferenceGuardError, PendingInference, begin, pending_count
from test_canonical_messaging import fixture


def test_atomic_empty_admission_preserves_normal_inference_concurrency(tmp_path):
    directory = tmp_path.resolve() / "pending"
    directory.mkdir(mode=0o700)
    first = begin(directory, require_empty=True)
    with pytest.raises(InferenceGuardError):
        begin(directory, request_id=uuid.uuid4().hex, require_empty=True)
    second = begin(directory)
    assert pending_count(directory, owner_uid=os.geteuid()) == 2
    assert first.complete() is True
    assert second.complete() is True
    assert begin(directory, require_empty=True).complete() is True


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_empty_requirement_is_strict_boolean(tmp_path, value):
    directory = tmp_path.resolve() / "pending"
    directory.mkdir(mode=0o700)
    with pytest.raises(InferenceGuardError):
        begin(directory, require_empty=value)
    assert list(directory.iterdir()) == []


def test_independent_senders_cannot_pass_stale_empty_observations(fixture):
    harness, directory, _ = fixture
    probe = harness.service._canonical.storage
    senders = [CanonicalMessageSender(harness.messaging, probe, directory).bind(copy.copy(harness.messaging))
               for _ in range(2)]
    for sender in senders:
        sender.ready = Mock(return_value=True)
    harness.sdk.message_send.side_effect = TimeoutError("unknown")
    barrier = threading.Barrier(2)

    def simultaneous_begin(*args, **kwargs):
        assert kwargs["require_empty"] is True
        barrier.wait(timeout=3)
        return begin(*args, **kwargs)

    def send(sender):
        try:
            return sender.send("session", request_id=str(uuid.uuid4()), recipient=harness.peer,
                               payload="Hello", access_check=lambda: True).outcome
        except MessagingUnavailable:
            return "denied"

    with patch.object(canonical_messaging, "begin", side_effect=simultaneous_begin):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(send, senders))
    assert sorted(results) == ["denied", "unknown"]
    assert harness.sdk.message_send.call_count == 1
    assert pending_count(directory, owner_uid=os.geteuid()) == 1
    assert all(sender.drained() is False for sender in senders)


def test_orphan_created_after_readiness_blocks_dispatch(fixture):
    harness, directory, _ = fixture

    def orphan_then_admit(*args, **kwargs):
        begin(directory)
        return begin(*args, **kwargs)

    with patch.object(canonical_messaging, "begin", side_effect=orphan_then_admit):
        with pytest.raises(MessagingUnavailable):
            harness.service._canonical.send("session", request_id=str(uuid.uuid4()),
                                            recipient=harness.peer, payload="Hello", access_check=lambda: True)
    harness.sdk.message_send.assert_not_called()
    assert pending_count(directory, owner_uid=os.geteuid()) == 1


@pytest.mark.parametrize("completion", [False, None])
@pytest.mark.parametrize("cancel", [False, True])
def test_unconfirmed_completion_is_unknown_and_denies_drain(fixture, completion, cancel):
    harness, directory, _ = fixture
    sender = CanonicalMessageSender(harness.messaging, harness.service._canonical.storage, directory).bind(copy.copy(harness.messaging))
    checks = iter([True, False] if cancel else [True, True, True])
    with patch.object(PendingInference, "complete", return_value=completion):
        outcome = sender.send("session", request_id=str(uuid.uuid4()), recipient=harness.peer,
                              payload="Hello", access_check=lambda: next(checks))
    assert outcome.outcome == "unknown"
    assert sender.drained() is False
    assert pending_count(directory, owner_uid=os.geteuid()) == 1
    assert harness.sdk.message_send.call_count == (0 if cancel else 1)


def test_known_predispatch_cancellation_removes_marker(fixture):
    harness, directory, _ = fixture
    checks = iter([True, False])
    with pytest.raises(MessagingUnavailable):
        harness.service._canonical.send("session", request_id=str(uuid.uuid4()), recipient=harness.peer,
                                        payload="Hello", access_check=lambda: next(checks))
    harness.sdk.message_send.assert_not_called()
    assert pending_count(directory, owner_uid=os.geteuid()) == 0
    assert harness.service._canonical.drained() is True
