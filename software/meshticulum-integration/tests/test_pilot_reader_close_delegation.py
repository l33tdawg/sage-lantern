# SPDX-License-Identifier: Apache-2.0
"""Actual held OFDs and threads; cleanup is not physical retirement evidence."""
import os
import threading

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from tests.test_pilot_resume_rpc import anchor, file


def threaded(call):
    result = []
    thread = threading.Thread(target=lambda: result.append(call()))
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    return result


def test_exact_delegation_closes_held_descriptions_and_shared_lock(anchor):
    path = file(anchor / 'owner', b'')
    reader = rpc.Reader()
    reader.read(path, 128, lock=True)
    token = reader.prepare_close_delegation()
    descriptors = tuple(reader._tokens)
    contender = os.open(path, os.O_RDONLY)
    try:
        import fcntl
        with pytest.raises(BlockingIOError):
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert threaded(lambda: reader.close_delegated(token)) == [True]
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)
        assert reader.close_delegated(token) is False
        with pytest.raises(RuntimeError):
            reader.read(path, 128)
    finally:
        os.close(contender)
        reader.close()


def test_delegation_neither_grants_live_reader_access_nor_changes_creator(anchor):
    path = file(anchor / 'metadata')
    with rpc.Reader() as reader:
        reader.read(path, 128)
        original = reader._owner
        reader.prepare_close_delegation()
        def foreign():
            with pytest.raises(RuntimeError):
                reader.read(path, 128)
            with pytest.raises(RuntimeError):
                reader.check()
            with pytest.raises(RuntimeError):
                reader.prepare_close_delegation()
            reader.close()
            return reader._closed
        assert threaded(foreign) == [False]
        assert reader._owner is original and reader.check() is True


def test_only_registered_exact_token_can_close_its_reader(anchor):
    path = file(anchor / 'metadata')
    with rpc.Reader() as reader, rpc.Reader() as other:
        reader.read(path, 128)
        token = reader.prepare_close_delegation()
        forged = rpc.ReaderCloseDelegation(reader)
        assert reader.close_delegated(forged) is False
        assert other.close_delegated(token) is False
        assert reader.check() is True
        with pytest.raises(RuntimeError):
            reader.prepare_close_delegation()
        assert threaded(lambda: reader.close_delegated(token)) == [True]


@pytest.mark.parametrize('identity', ['pid', 'euid'])
def test_delegation_cannot_cross_process_or_user_identity(anchor, monkeypatch, identity):
    path = file(anchor / 'metadata')
    with rpc.Reader() as reader:
        reader.read(path, 128)
        token = reader.prepare_close_delegation()
        method = 'getpid' if identity == 'pid' else 'geteuid'
        original = getattr(os, method)
        with monkeypatch.context() as change:
            change.setattr(os, method, lambda: original() + 1)
            assert reader.close_delegated(token) is False
        assert reader.check() is True
        assert threaded(lambda: reader.close_delegated(token)) == [True]


def test_active_read_prevents_cleanup_and_preserves_unused_token(anchor, monkeypatch):
    path = file(anchor / 'metadata')
    with rpc.Reader() as reader:
        reader.read(path, 128)
        token = reader.prepare_close_delegation()
        original = os.pread
        results = []
        def during_read(fd, count, offset):
            if not results:
                results.extend(threaded(lambda: reader.close_delegated(token)))
                # Same-thread reentry also cannot close an active operation.
                results.append(reader.close_delegated(token))
                reader.close()
                assert reader._closed is False
            return original(fd, count, offset)
        monkeypatch.setattr(os, 'pread', during_read)
        assert reader.read_held(path, 128) == b'public metadata'
        assert results == [False, False] and token._used is False
        assert threaded(lambda: reader.close_delegated(token)) == [True]


def test_reused_descriptor_is_not_closed_or_reported_as_success(anchor):
    path = file(anchor / 'metadata')
    with rpc.Reader() as reader:
        reader.read(path, 128)
        token = reader.prepare_close_delegation()
        owned = reader.held[0][1]
        os.close(owned)
        replacement = os.open(path, os.O_RDONLY)
        try:
            if replacement != owned:
                os.dup2(replacement, owned)
            assert threaded(lambda: reader.close_delegated(token)) == [False]
            os.fstat(owned)
            assert reader.close_delegated(token) is False
        finally:
            os.close(owned)
            if replacement != owned:
                os.close(replacement)


def test_changed_reader_state_contains_original_stack_and_stays_failed(anchor):
    path = file(anchor / 'metadata')
    with rpc.Reader() as reader:
        reader.read(path, 128)
        token = reader.prepare_close_delegation()
        descriptors = tuple(reader._tokens)
        reader.held = list(reader.held)
        assert threaded(lambda: reader.close_delegated(token)) == [False]
        assert reader.close_delegated(token) is False
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)
