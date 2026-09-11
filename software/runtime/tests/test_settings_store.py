import os
import sqlite3
import tempfile
import threading
import time
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sage_lantern.settings import ObservationState, SettingId, project_settings
from sage_lantern.settings_store import (
    DATABASE_NAME,
    MAX_FILE_BYTES,
    MAX_REVISION,
    DraftErrorCode,
    DraftSettingsStore,
    DraftStoreError,
)


class DraftSettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.database = self.directory / DATABASE_NAME

    def store(self):
        store = DraftSettingsStore(directory=self.directory)
        self.addCleanup(store.close)
        return store

    def assert_code(self, code, operation):
        with self.assertRaises(DraftStoreError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code.value)

    def test_initial_state_and_secure_creation(self):
        store = self.store()
        snapshot = store.read()
        self.assertEqual((snapshot.revision, snapshot.state, dict(snapshot.intent)), (0, "draft_only", {}))
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.database.stat().st_uid, os.geteuid())
        self.assertEqual(set(self.directory.iterdir()), {self.database})

    def test_replace_restart_clear_and_immutable_snapshot(self):
        store = self.store()
        intent = {SettingId.VOICE: True, SettingId.PHOTO: False}
        snapshot = store.replace(expected_revision=0, intent=intent)
        intent.clear()
        self.assertEqual(dict(snapshot.intent), {SettingId.VOICE: True, SettingId.PHOTO: False})
        with self.assertRaises(TypeError):
            snapshot.intent[SettingId.TTS] = True
        with self.assertRaises(FrozenInstanceError):
            snapshot.revision = 10
        store.close()
        reopened = self.store()
        self.assertEqual(reopened.read(), snapshot)
        changed = reopened.replace(expected_revision=1, intent={SettingId.TTS: True})
        self.assertEqual((changed.revision, dict(changed.intent)), (2, {SettingId.TTS: True}))
        cleared = reopened.replace(expected_revision=2, intent={})
        self.assertEqual((cleared.revision, dict(cleared.intent)), (3, {}))
        reopened.close()
        self.assertEqual(self.store().read(), cleared)

    def test_draft_projection_never_invents_observed_or_applied_state(self):
        snapshot = self.store().replace(expected_revision=0, intent={SettingId.VOICE: True})
        rows = project_settings(now=datetime(2026, 9, 5, tzinfo=UTC), desired=snapshot.intent)
        self.assertEqual(snapshot.state, "draft_only")
        self.assertTrue(next(row for row in rows if row.definition.setting_id is SettingId.VOICE).desired)
        for row in rows:
            self.assertIsNone(row.effective)
            self.assertEqual(row.observation_state, ObservationState.UNKNOWN)

    def test_cas_across_connections_and_noop_advances_revision(self):
        first, second = self.store(), self.store()
        snapshot = first.replace(expected_revision=0, intent={SettingId.VOICE: True})
        self.assert_code(DraftErrorCode.REVISION_CONFLICT, lambda: second.replace(
            expected_revision=0, intent={SettingId.PHOTO: True},
        ))
        self.assertEqual(second.read(), snapshot)
        self.assertEqual(second.replace(expected_revision=1, intent=snapshot.intent).revision, 2)
        self.assert_code(DraftErrorCode.REVISION_CONFLICT, lambda: first.replace(expected_revision=1, intent={}))

    def test_simultaneous_writers_have_one_winner(self):
        self.store().close()
        barrier = threading.Barrier(2)

        def save(setting_id):
            with DraftSettingsStore(directory=self.directory) as store:
                revision = store.read().revision
                barrier.wait(timeout=5)
                try:
                    return store.replace(expected_revision=revision, intent={setting_id: True})
                except DraftStoreError as error:
                    return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, (SettingId.VOICE, SettingId.PHOTO)))
        self.assertEqual(results.count(DraftErrorCode.REVISION_CONFLICT), 1)
        snapshot = self.store().read()
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(len(snapshot.intent), 1)
        self.assertIn(snapshot, results)

    def test_fault_after_update_rolls_back_without_leaking_error(self):
        store = self.store()
        before = store.replace(expected_revision=0, intent={SettingId.VOICE: False})
        original = store._snapshot
        calls = 0

        def fail_after_write():
            nonlocal calls
            calls += 1
            if calls == 2:
                journal = self.directory / (DATABASE_NAME + "-journal")
                self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
                self.assertEqual(journal.stat().st_uid, os.geteuid())
                raise sqlite3.OperationalError("sensitive injected database detail")
            return original()

        with patch.object(store, "_snapshot", side_effect=fail_after_write):
            try:
                store.replace(expected_revision=1, intent={SettingId.PHOTO: True})
            except DraftStoreError as error:
                self.assertEqual(error.code, DraftErrorCode.UNAVAILABLE)
                self.assertNotIn("sensitive injected database detail", "".join(traceback.format_exception(error)))
            else:
                self.fail("fault did not fail transaction")
        self.assertEqual(store.read(), before)
        store.close()
        self.assertEqual(self.store().read(), before)

    def test_commit_failure_rolls_back_values_and_revision(self):
        store = self.store()
        before = store.replace(expected_revision=0, intent={SettingId.VOICE: False})
        connection = store._connection

        class FailedCommit:
            execute = connection.execute
            rollback = connection.rollback
            close = connection.close

            def commit(self):
                raise sqlite3.OperationalError("private-commit-canary")

        with patch.object(store, "_connection", FailedCommit()):
            self.assert_code(DraftErrorCode.UNAVAILABLE, lambda: store.replace(
                expected_revision=1, intent={SettingId.TTS: True},
            ))
        self.assertFalse(connection.in_transaction)
        self.assertEqual(store.read(), before)
        store.close()
        self.assertEqual(self.store().read(), before)

    def test_write_lock_has_bounded_timeout_without_mutation(self):
        store = self.store()
        before = store.read()
        blocker = sqlite3.connect(self.database, isolation_level=None)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            self.assert_code(DraftErrorCode.UNAVAILABLE, lambda: store.replace(
                expected_revision=0, intent={SettingId.VOICE: True},
            ))
            self.assertLess(time.monotonic() - started, 5)
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(store.read(), before)

    def test_invalid_inputs_cannot_store_or_echo_values(self):
        store = self.store()
        invalid = [
            {"https://private.invalid": True}, {"/private/key": True}, {"private.service": True},
            {"voice_capture": True}, {SettingId.MESSAGING: True}, {SettingId.RECOVERY: False},
            {SettingId.HISTORY: True}, {SettingId.VOICE: "private-content-canary"},
            {SettingId.VOICE: 1}, {SettingId.VOICE: 0}, {SettingId.VOICE: None},
            {SettingId.VOICE: {}}, {SettingId.VOICE: []}, [], "private-content-canary",
        ]
        before = self.database.read_bytes()
        for intent in invalid:
            with self.subTest(intent=intent):
                self.assert_code(DraftErrorCode.INVALID_DRAFT, lambda: store.replace(expected_revision=0, intent=intent))
        for revision in (True, False, -1, 1.0, "private-content-canary", MAX_REVISION + 1, None):
            self.assert_code(DraftErrorCode.INVALID_REVISION, lambda: store.replace(expected_revision=revision, intent={}))
        self.assertEqual(self.database.read_bytes(), before)
        self.assertNotIn(b"private-content-canary", self.database.read_bytes())

    def test_unsupported_version_is_not_repaired(self):
        self.store().close()
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA user_version = 99")
        before = self.database.read_bytes()
        self.assert_code(DraftErrorCode.UNSUPPORTED_SCHEMA, lambda: self.store())
        self.assertEqual(self.database.read_bytes(), before)

    def test_version_changes_are_checked_on_open_connections(self):
        store = self.store()
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA user_version = 2")
        self.assert_code(DraftErrorCode.UNSUPPORTED_SCHEMA, store.read)
        self.assert_code(DraftErrorCode.UNSUPPORTED_SCHEMA, lambda: store.replace(expected_revision=0, intent={}))

    def test_extra_schema_and_invalid_stored_values_fail_closed(self):
        store = self.store()
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE unexpected (content TEXT)")
        self.assert_code(DraftErrorCode.UNSUPPORTED_SCHEMA, store.read)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE unexpected")
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE draft_state SET voice_capture = 'private-content-canary'")
        self.assert_code(DraftErrorCode.INVALID_STATE, store.read)
        self.assert_code(DraftErrorCode.INVALID_STATE, lambda: store.replace(expected_revision=0, intent={}))

    def test_revision_exhaustion(self):
        store = self.store()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE draft_state SET revision = ?", (MAX_REVISION,))
        self.assert_code(DraftErrorCode.REVISION_EXHAUSTED, lambda: store.replace(expected_revision=MAX_REVISION, intent={}))
        self.assertEqual(store.read().revision, MAX_REVISION)

    def test_rejects_unsafe_directory_without_chmod_or_creation(self):
        self.directory.chmod(0o755)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o755)
        self.assertFalse(self.database.exists())
        self.directory.chmod(0o700)
        link = self.directory / "alias"
        link.symlink_to(self.directory, target_is_directory=True)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: DraftSettingsStore(directory=link))
        child = self.directory / "managed"
        child.mkdir(mode=0o700)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: DraftSettingsStore(directory=link / "managed"))
        self.assertFalse((child / DATABASE_NAME).exists())
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: DraftSettingsStore(directory=Path("relative")))
        with patch("sage_lantern.settings_store.os.geteuid", return_value=os.geteuid() + 1):
            self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())

    def test_rejects_unsafe_database_types_and_permissions(self):
        target = self.directory / "untouched"
        target.write_bytes(b"private-content-canary")
        self.database.symlink_to(target)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())
        self.assertEqual(target.read_bytes(), b"private-content-canary")
        self.database.unlink()
        os.link(target, self.database)
        self.database.chmod(0o600)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())
        self.database.unlink()
        os.mkfifo(self.database, 0o600)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())
        self.database.unlink()
        self.store().close()
        self.database.chmod(0o644)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o644)

    def test_wrong_database_owner_is_refused_without_changes(self):
        self.store().close()
        original_stat = os.stat
        before = self.database.read_bytes()

        def wrong_owner(path, *args, **kwargs):
            metadata = original_stat(path, *args, **kwargs)
            if path == DATABASE_NAME:
                fields = list(metadata)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return metadata

        with patch("sage_lantern.settings_store.os.stat", side_effect=wrong_owner):
            self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)

    def test_unsafe_sidecar_is_rejected_before_sqlite_access(self):
        self.store().close()
        target = self.directory / "untouched"
        target.write_bytes(b"private-content-canary")
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.directory / (DATABASE_NAME + suffix)
            sidecar.symlink_to(target)
            self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())
            self.assertTrue(sidecar.is_symlink())
            sidecar.unlink()
        self.assertEqual(target.read_bytes(), b"private-content-canary")

    def test_storage_changes_are_rejected_on_existing_connection(self):
        store = self.store()
        self.database.chmod(0o644)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, store.read)
        self.database.chmod(0o600)
        moved = self.directory / "old-database"
        self.database.rename(moved)
        self.database.write_bytes(moved.read_bytes())
        self.database.chmod(0o600)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: store.replace(expected_revision=0, intent={}))

    def test_corrupt_empty_and_oversize_files_are_not_reset(self):
        for content in (b"", b"private-content-canary"):
            self.database.write_bytes(content)
            self.database.chmod(0o600)
            with self.assertRaises(DraftStoreError) as raised:
                self.store()
            self.assertNotIn("private-content-canary", str(raised.exception))
            self.assertEqual(self.database.read_bytes(), content)
        with self.database.open("wb") as stream:
            stream.truncate(MAX_FILE_BYTES + 1)
        self.assert_code(DraftErrorCode.UNSAFE_STORAGE, lambda: self.store())

    def test_closed_store_fails_with_sanitized_error(self):
        store = self.store()
        store.close()
        store.close()
        self.assert_code(DraftErrorCode.UNAVAILABLE, store.read)


if __name__ == "__main__":
    unittest.main()
