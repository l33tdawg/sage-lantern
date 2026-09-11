import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from sage_lantern.device_profile import DeviceProfileStore, ProfileError, ProfileRevisionConflict, normalize_display_name


@pytest.fixture
def store(tmp_path):
    return DeviceProfileStore(tmp_path / "profile")


def test_default_and_explicit_setup_reopen(store):
    profile = store.load()
    assert profile.setup_required and not profile.configured
    assert profile.display_name == "" and profile.revision == 0
    assert not profile.public_discoverable
    assert not (store.directory / "profile.json").exists()
    result = store.complete_setup("  Unit   A  ", expected_revision=0)
    assert result.display_name == "Unit A" and result.revision == 1
    assert result.configured and not result.setup_required
    assert DeviceProfileStore(store.directory).load() == result
    assert set(json.loads((store.directory / "profile.json").read_text())) == {
        "version", "configured", "display_name", "public_discoverable", "revision",
    }
    assert result.to_dict()["setup_required"] is False


def test_permissions_and_rename_does_not_touch_identity(store, tmp_path):
    identity = tmp_path / "identity.key"
    identity.write_bytes(b"untouched")
    store.complete_setup("A")
    renamed = store.rename("B", expected_revision=1)
    assert renamed.display_name == "B" and renamed.revision == 2
    assert not renamed.public_discoverable
    assert identity.read_bytes() == b"untouched"
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    for path in store.directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("name", ["", "   ", "a" * 41, None, 1, "a\nb", "a\tb", "a\x00b", "a\x7fb", "a\u202eb", "a\u2066b", "a\u200db", "a\u2028b", "\ud800"])
def test_invalid_names_never_configure(store, name):
    with pytest.raises(ProfileError):
        store.complete_setup(name)
    assert store.load().setup_required


@pytest.mark.parametrize("name, expected", [(" مصباح  البيت ", "مصباح البيت"), ("Cafe\u0301", "Café"), ("Unit\u00a0 A", "Unit A"), ("😀" * 40, "😀" * 40)])
def test_unicode_normalization(name, expected):
    assert normalize_display_name(name) == expected


def test_setup_and_idempotency_rules(store):
    with pytest.raises(ProfileError, match="Complete setup"):
        store.rename("A")
    profile = store.complete_setup("A")
    assert store.complete_setup(" A ") == profile
    assert store.rename("A", expected_revision=1) == profile
    with pytest.raises(ProfileError, match="already configured"):
        store.complete_setup("B")
    with pytest.raises(ProfileRevisionConflict):
        store.rename("A", expected_revision=0)
    assert store.load() == profile


@pytest.mark.parametrize("revision", [True, -1, 1.0, "1", 1 << 63])
def test_invalid_expected_revision(store, revision):
    with pytest.raises(ProfileError):
        store.complete_setup("A", expected_revision=revision)


def test_concurrent_revision_fence(store):
    store.complete_setup("A")

    def rename(name):
        try:
            return DeviceProfileStore(store.directory).rename(name, expected_revision=1).display_name
        except ProfileRevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(rename, ["B", "C"]))
    assert results.count("conflict") == 1
    assert store.load().revision == 2


@pytest.mark.parametrize("raw", [b"{", b"\xff", b"[]", b"{}", b"x" * 4097, b'{"version":1,"version":1}'])
def test_corruption_is_not_reset(store, raw):
    store.complete_setup("A")
    path = store.directory / "profile.json"
    path.write_bytes(raw)
    for action in (store.load, lambda: store.complete_setup("B"), lambda: store.rename("B")):
        with pytest.raises(ProfileError):
            action()
    assert path.read_bytes() == raw


@pytest.mark.parametrize("change", [{"version": True}, {"version": 2}, {"configured": 1}, {"public_discoverable": "true"}, {"revision": True}, {"revision": -1}, {"display_name": " A "}, {"configured": False}, {"pin": "1234"}])
def test_strict_schema(store, change):
    store.complete_setup("A")
    path = store.directory / "profile.json"
    value = json.loads(path.read_text())
    value.update(change)
    path.write_text(json.dumps(value))
    with pytest.raises(ProfileError):
        store.load()


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_unsafe_file_permissions_fail_closed(store, mode):
    store.complete_setup("A")
    (store.directory / "profile.json").chmod(mode)
    with pytest.raises(ProfileError, match="Unsafe"):
        store.load()


def test_unsafe_directory_and_symlinks(store, tmp_path):
    store.load()
    store.directory.chmod(0o755)
    with pytest.raises(ProfileError):
        store.load()
    store.directory.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(store.directory, target_is_directory=True)
    with pytest.raises(OSError):
        DeviceProfileStore(alias).load()
    (store.directory / "profile.json").symlink_to(tmp_path / "missing")
    with pytest.raises(OSError):
        store.load()


def test_hardlinked_file_rejected(store, tmp_path):
    store.complete_setup("A")
    os.link(store.directory / "profile.json", tmp_path / "other")
    with pytest.raises(ProfileError, match="hard linked"):
        store.load()


def test_failed_replace_preserves_previous_profile(store):
    previous = store.complete_setup("A")
    with patch("sage_lantern.device_profile.os.replace", side_effect=OSError("disk failure")), pytest.raises(OSError):
        store.rename("B")
    assert store.load() == previous
    assert not list(store.directory.glob(".profile-*"))


def test_failed_initial_save_does_not_complete_setup(store):
    with patch("sage_lantern.device_profile.os.replace", side_effect=OSError("disk failure")), pytest.raises(OSError):
        store.complete_setup("A")
    assert store.load().setup_required


def test_directory_fsync_failure_requires_reconciliation(store):
    store.complete_setup("A")
    original = os.fsync

    def fail_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory sync failed")
        original(descriptor)

    with patch("sage_lantern.device_profile.os.fsync", side_effect=fail_directory), pytest.raises(OSError):
        store.rename("B", expected_revision=1)
    assert store.load().display_name == "B"
    with pytest.raises(ProfileRevisionConflict):
        store.rename("C", expected_revision=1)


def test_revision_exhaustion_preserves_profile(store):
    store.complete_setup("A")
    path = store.directory / "profile.json"
    value = json.loads(path.read_text())
    value["revision"] = (1 << 63) - 1
    path.write_text(json.dumps(value))
    with pytest.raises(ProfileError, match="exhausted"):
        store.rename("B")
    assert store.load().display_name == "A"


def test_lock_symlink_is_rejected(store, tmp_path):
    store.directory.mkdir(mode=0o700)
    (store.directory / ".profile.lock").symlink_to(tmp_path / "missing")
    with pytest.raises(OSError):
        store.load()
    assert not (tmp_path / "missing").exists()


def test_failed_file_sync_keeps_previous_profile(store):
    previous = store.complete_setup("A")
    with patch("sage_lantern.device_profile.os.fsync", side_effect=OSError("sync failed")), pytest.raises(OSError):
        store.rename("B")
    assert store.load() == previous
    assert not list(store.directory.glob(".profile-*"))
