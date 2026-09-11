import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sage_lantern.conversation_store import ConversationStore, ConversationStoreError
from sage_lantern.protected_conversation_owner import ConversationOwnerError, ProtectedConversationOwner


@pytest.fixture
def directory(tmp_path):
    path = tmp_path.resolve() / "conversation"
    path.mkdir(mode=0o700)
    return path


def test_independent_descriptors_contend_and_close_reacquires(directory):
    owner = ProtectedConversationOwner(directory)
    try:
        assert owner.valid() is True
        assert set(path.name for path in directory.iterdir()) == {"owner.lock"}
        lock = directory / "owner.lock"
        assert lock.stat().st_size == 0 and lock.stat().st_mode & 0o777 == 0o600
        with pytest.raises(ConversationOwnerError):
            ProtectedConversationOwner(directory)
        assert owner.valid() is True
    finally:
        owner.close()
    assert owner.valid() is False and lock.exists()
    owner.close()
    reopened = ProtectedConversationOwner(directory)
    assert reopened.valid() is True
    reopened.close()


def test_legacy_contention_both_directions(directory):
    legacy = ConversationStore(directory)
    try:
        with pytest.raises(ConversationOwnerError):
            ProtectedConversationOwner(directory)
    finally:
        legacy.close()
    owner = ProtectedConversationOwner(directory)
    try:
        with pytest.raises(ConversationStoreError):
            ConversationStore(directory)
        assert owner.valid() is True
    finally:
        owner.close()


def test_fresh_protected_marker_forbids_legacy_database_creation(directory):
    owner = ProtectedConversationOwner(directory)
    owner.close()
    with pytest.raises(ConversationStoreError):
        ConversationStore(directory)
    assert set(path.name for path in directory.iterdir()) == {"owner.lock"}


def test_replaced_lock_is_terminal_even_if_restored(directory):
    owner = ProtectedConversationOwner(directory)
    lock = directory / "owner.lock"
    moved = directory / "old"
    try:
        lock.rename(moved)
        lock.touch(mode=0o600)
        assert owner.valid() is False
        lock.unlink()
        moved.rename(lock)
        assert owner.valid() is False
    finally:
        owner.close()


def test_directory_replacement_detected(directory):
    owner = ProtectedConversationOwner(directory)
    try:
        directory.rename(directory.with_name("old"))
        directory.mkdir(mode=0o700)
        assert owner.valid() is False
    finally:
        owner.close()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "nonempty", "mode", "fifo"])
def test_unsafe_lock_rejected_without_repair(directory, kind):
    lock = directory / "owner.lock"
    target = directory / "target"
    target.touch(mode=0o600)
    if kind == "symlink":
        lock.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, lock)
    elif kind == "fifo":
        os.mkfifo(lock, 0o600)
    else:
        lock.touch(mode=0o600)
        if kind == "mode":
            lock.chmod(0o644)
        else:
            lock.write_bytes(b"not empty")
    with pytest.raises(ConversationOwnerError):
        ProtectedConversationOwner(directory)
    assert lock.lstat()


@pytest.mark.parametrize("kind", ["mode", "symlink", "missing", "relative", "traversal", "ancestor"])
def test_directory_policy(directory, kind):
    path = directory
    if kind == "mode":
        path.chmod(0o750)
    elif kind == "symlink":
        path = directory.with_name("alias")
        path.symlink_to(directory, target_is_directory=True)
    elif kind == "missing":
        path = directory / "missing" / "child"
    elif kind == "relative":
        path = Path("relative")
    elif kind == "traversal":
        path = directory / ".." / directory.name
    else:
        directory.parent.chmod(0o777)
    try:
        with pytest.raises(ConversationOwnerError):
            ProtectedConversationOwner(path)
        assert not (directory / "owner.lock").exists()
    finally:
        directory.parent.chmod(0o700)


@pytest.mark.parametrize("kind", ["file_mode", "directory_mode", "hardlink", "contents"])
def test_valid_checks_later_tampering(directory, kind):
    owner = ProtectedConversationOwner(directory)
    try:
        lock = directory / "owner.lock"
        if kind == "file_mode":
            lock.chmod(0o644)
        elif kind == "directory_mode":
            directory.chmod(0o755)
        elif kind == "hardlink":
            os.link(lock, directory / "alias")
        else:
            lock.write_bytes(b"x")
        assert owner.valid() is False
    finally:
        owner.close()


def test_wrong_lock_owner_rejected(directory):
    original = os.fstat
    def inspect(descriptor):
        info = original(descriptor)
        if info.st_mode & 0o170000 == 0o100000:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1,
                                   st_nlink=info.st_nlink, st_size=info.st_size)
        return info
    with patch("sage_lantern.protected_conversation_owner.os.fstat", side_effect=inspect):
        with pytest.raises(ConversationOwnerError):
            ProtectedConversationOwner(directory)


def test_ancestor_replacement_detected(directory):
    owner = ProtectedConversationOwner(directory)
    ancestor = directory.parent
    moved = ancestor.with_name(ancestor.name + "-moved")
    try:
        ancestor.rename(moved)
        ancestor.mkdir(mode=0o700)
        (moved / directory.name).rename(directory)
        assert owner.valid() is False
    finally:
        owner.close()
        moved.rmdir()
