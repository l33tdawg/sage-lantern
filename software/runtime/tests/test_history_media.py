import fcntl
import hashlib
import os
import stat
import tempfile
import threading
import time
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sage_lantern.history_media import (
    DEFAULT_FREE_BYTES,
    MAX_JPEG_BYTES,
    HistoryMediaError,
    HistoryMediaStore,
)


def jpeg_headers(width=1920, height=1080, padding=b"\x00"):
    frame = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    return b"\xff\xd8\xff\xc0\x00\x0b" + frame + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + padding + b"\xff\xd9"


class HistoryMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.item_id = str(uuid4())
        self.path = self.directory / (self.item_id + ".media")
        self.store = HistoryMediaStore(directory=self.directory, reserve_free_bytes=0)

    def assert_error(self, code, operation):
        with self.assertRaises(HistoryMediaError) as raised:
            operation()
        self.assertEqual(str(raised.exception), code)

    def test_original_bytes_digest_restart_and_modes(self):
        image = jpeg_headers(padding=b"original-metadata-canary")
        digest = self.store.put(self.item_id, image)
        self.assertEqual(digest, hashlib.sha256(image).hexdigest())
        self.assertEqual(self.store.get(self.item_id), image)
        reopened = HistoryMediaStore(directory=self.directory, reserve_free_bytes=0)
        self.assertEqual(reopened.get(self.item_id), image)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.path.stat().st_uid, os.geteuid())
        self.assertEqual(self.path.stat().st_nlink, 1)
        self.assertEqual(set(self.directory.iterdir()), {self.path})

    def test_symlink_loop_returns_only_bounded_error(self):
        loop = self.directory / "private-path-canary"
        loop.symlink_to(loop.name)
        with self.assertRaises(HistoryMediaError) as raised:
            HistoryMediaStore(directory=loop)
        self.assertIn(str(raised.exception), {"unsafe_storage", "media_unavailable"})
        self.assertNotIn("private-path-canary", str(raised.exception))

    def test_idempotency_and_conflict_preserve_original(self):
        original = jpeg_headers()
        digest = self.store.put(self.item_id, original)
        before = self.path.read_bytes()
        self.assertEqual(self.store.put(self.item_id, original), digest)
        self.assert_error("item_conflict", lambda: self.store.put(self.item_id, jpeg_headers(1280, 720)))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.store.delete(self.item_id))
        self.assertFalse(self.store.delete(self.item_id))
        self.assert_error("media_not_found", lambda: self.store.get(self.item_id))

    def test_uuid_only_all_operations(self):
        for item_id in ("../private", "/private/key", "", "file.jpg", self.item_id.upper(),
                        self.item_id.replace("-", ""), uuid4(), None, 1):
            for operation in (lambda: self.store.put(item_id, jpeg_headers()),
                              lambda: self.store.get(item_id), lambda: self.store.delete(item_id)):
                with self.subTest(item_id=item_id):
                    self.assert_error("invalid_item_id", operation)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_exact_byte_limit_and_static_dimensions(self):
        base = jpeg_headers()
        image = base[:-2] + b"\x00" * (MAX_JPEG_BYTES - len(base)) + base[-2:]
        self.store.put(self.item_id, image)
        self.assertEqual(self.store.get(self.item_id), image)
        self.assert_error("invalid_jpeg", lambda: self.store.put(str(uuid4()), image[:-2] + b"x" + image[-2:]))
        smaller = HistoryMediaStore(directory=self.directory, dimensions=(1280, 720), reserve_free_bytes=0)
        smaller.put(str(uuid4()), jpeg_headers(1280, 720))
        for width, height in ((1281, 720), (1280, 721), (1920, 1080)):
            self.assert_error("invalid_jpeg", lambda: smaller.put(str(uuid4()), jpeg_headers(width, height)))
        for width, height in ((1921, 1080), (1920, 1081), (0, 1), (1, 0)):
            self.assert_error("invalid_jpeg", lambda: self.store.put(str(uuid4()), jpeg_headers(width, height)))

    def test_malformed_headers_rejected_before_storage(self):
        image = jpeg_headers()
        for raw in (None, bytearray(image), b"", b"PNG", b"\xff\xd8\xff\xd9", image[:-2],
                    image.replace(b"\xff\xc0", b"\xff\xc3"),
                    image.replace(b"\x00\x0b", b"\x00\x02"),
                    image.replace(b"\xff\xda\x00\x08\x01", b"\xff\xda\x00\x08\x00"),
                    b"\xff\xd8\xff\xe0\xff\xff\xff\xd9"):
            with self.subTest(raw=raw):
                self.assert_error("invalid_jpeg", lambda: self.store.put(self.item_id, raw))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_corruption_never_returned_or_overwritten(self):
        self.store.put(self.item_id, jpeg_headers())
        original = self.path.read_bytes()
        for corrupted in (b"", original[:-1], b"x" + original[1:],
                          original[:12] + bytes([original[12] ^ 1]) + original[13:],
                          original[:-3] + b"x" + original[-2:]):
            self.path.write_bytes(corrupted)
            self.assert_error("corrupt_media", lambda: self.store.get(self.item_id))
            self.assert_error("corrupt_media", lambda: self.store.put(self.item_id, jpeg_headers()))
            self.assertEqual(self.path.read_bytes(), corrupted)
        self.assertTrue(self.store.delete(self.item_id))

    def test_unsafe_files_never_followed_or_deleted(self):
        target = self.directory / "sentinel"
        target.write_bytes(b"synthetic-private-canary")
        target.chmod(0o600)
        for kind in ("symlink", "hardlink", "fifo", "directory", "mode"):
            if kind == "symlink":
                self.path.symlink_to(target)
            elif kind == "hardlink":
                os.link(target, self.path)
            elif kind == "fifo":
                os.mkfifo(self.path, 0o600)
            elif kind == "directory":
                self.path.mkdir(mode=0o700)
            else:
                self.path.write_bytes(b"unsafe")
                self.path.chmod(0o644)
            for operation in (lambda: self.store.put(self.item_id, jpeg_headers()),
                              lambda: self.store.get(self.item_id), lambda: self.store.delete(self.item_id)):
                self.assert_error("unsafe_storage", operation)
            self.assertEqual(target.read_bytes(), b"synthetic-private-canary")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            if kind == "directory":
                self.path.rmdir()
            else:
                self.path.unlink()

    def test_unsafe_directories_and_configuration(self):
        self.directory.chmod(0o755)
        self.assert_error("unsafe_storage", lambda: self.store.put(self.item_id, jpeg_headers()))
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o755)
        self.directory.chmod(0o700)
        alias = self.directory / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        for directory in (alias, Path("relative")):
            self.assert_error("unsafe_storage", lambda: HistoryMediaStore(directory=directory))
        with patch("sage_lantern.history_media.os.geteuid", return_value=os.geteuid() + 1):
            self.assert_error("unsafe_storage", lambda: self.store.get(self.item_id))
        for options in ({"dimensions": (640, 480)}, {"dimensions": [1920, 1080]},
                        {"reserve_free_bytes": -1}, {"reserve_free_bytes": True}):
            self.assert_error("invalid_configuration", lambda: HistoryMediaStore(directory=self.directory, **options))

    def test_default_floor_counts_incoming_allocation(self):
        store = HistoryMediaStore(directory=self.directory)
        block = 4096
        space = SimpleNamespace(f_frsize=block, f_bsize=block, f_bavail=DEFAULT_FREE_BYTES // block)
        with patch("sage_lantern.history_media.os.fstatvfs", return_value=space):
            self.assert_error("insufficient_space", lambda: store.put(self.item_id, jpeg_headers()))
        self.assertEqual(list(self.directory.iterdir()), [])
        space.f_bavail += 2
        with patch("sage_lantern.history_media.os.fstatvfs", return_value=space):
            store.put(self.item_id, jpeg_headers())
        space.f_bavail = 0
        with patch("sage_lantern.history_media.os.fstatvfs", return_value=space):
            store.put(self.item_id, jpeg_headers())
            self.assertEqual(store.get(self.item_id), jpeg_headers())
            self.assertTrue(store.delete(self.item_id))

    def test_wrong_file_owner_and_oversize_are_refused(self):
        self.store.put(self.item_id, jpeg_headers())
        original_stat = os.stat

        def wrong_owner(path, *args, **kwargs):
            metadata = original_stat(path, *args, **kwargs)
            if path == self.item_id + ".media":
                fields = list(metadata)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return metadata

        before = self.path.read_bytes()
        with patch("sage_lantern.history_media.os.stat", side_effect=wrong_owner):
            self.assert_error("unsafe_storage", lambda: self.store.get(self.item_id))
            self.assert_error("unsafe_storage", lambda: self.store.delete(self.item_id))
        self.assertEqual(self.path.read_bytes(), before)
        with self.path.open("wb") as stream:
            stream.truncate(MAX_JPEG_BYTES + 1024)
        self.assert_error("unsafe_storage", lambda: self.store.get(self.item_id))

    def test_replaced_directory_is_not_used(self):
        child = self.directory / "media"
        child.mkdir(mode=0o700)
        store = HistoryMediaStore(directory=child, reserve_free_bytes=0)
        child.rename(self.directory / "old-media")
        child.mkdir(mode=0o700)
        self.assert_error("unsafe_storage", lambda: store.put(self.item_id, jpeg_headers()))
        self.assertEqual(list(child.iterdir()), [])

    def test_floor_rechecked_after_write(self):
        store = HistoryMediaStore(directory=self.directory)
        enough = SimpleNamespace(f_frsize=4096, f_bsize=4096, f_bavail=1024 * 1024)
        empty = SimpleNamespace(f_frsize=4096, f_bsize=4096, f_bavail=0)
        with patch("sage_lantern.history_media.os.fstatvfs", side_effect=[enough, empty]):
            self.assert_error("insufficient_space", lambda: store.put(self.item_id, jpeg_headers()))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_failures_before_publish_are_atomic_and_sanitized(self):
        for function in ("fsync", "rename"):
            with patch("sage_lantern.history_media.os." + function,
                       side_effect=OSError("private-path-canary")):
                with self.assertRaises(HistoryMediaError) as raised:
                    self.store.put(self.item_id, jpeg_headers())
                self.assertEqual(str(raised.exception), "media_unavailable")
                self.assertNotIn("private-path-canary", "".join(traceback.format_exception(raised.exception)))
            self.assertEqual(list(self.directory.iterdir()), [])
        self.store.put(self.item_id, jpeg_headers())

    def test_post_rename_fsync_failure_leaves_complete_retryable_record(self):
        original = os.fsync
        calls = 0

        def fail_directory(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected")
            original(descriptor)

        with patch("sage_lantern.history_media.os.fsync", side_effect=fail_directory):
            self.assert_error("media_unavailable", lambda: self.store.put(self.item_id, jpeg_headers()))
        self.assertEqual(self.store.get(self.item_id), jpeg_headers())
        self.store.put(self.item_id, jpeg_headers())
        self.assertEqual(set(self.directory.iterdir()), {self.path})

    def test_partial_write_failure_cleans_staging_and_preserves_other_items(self):
        self.store.put(self.item_id, jpeg_headers())
        other_id = str(uuid4())
        original = os.fdopen

        class FailedWriter:
            def __init__(self, descriptor, mode):
                self.stream = original(descriptor, mode)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def write(self, content):
                self.stream.write(content[:len(content) // 2])
                self.stream.flush()
                raise OSError("synthetic disk-full")

        with patch("sage_lantern.history_media.os.fdopen", side_effect=FailedWriter):
            self.assert_error("media_unavailable", lambda: self.store.put(other_id, jpeg_headers()))
        self.assertEqual(set(self.directory.iterdir()), {self.path})
        self.assertEqual(self.store.get(self.item_id), jpeg_headers())
        self.assert_error("media_not_found", lambda: self.store.get(other_id))

    def test_concurrent_conflicting_puts_never_overwrite(self):
        barrier = threading.Barrier(2)
        images = (jpeg_headers(), jpeg_headers(1280, 720))

        def write(image):
            store = HistoryMediaStore(directory=self.directory, reserve_free_bytes=0)
            barrier.wait(timeout=5)
            try:
                return store.put(self.item_id, image)
            except HistoryMediaError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, images))
        self.assertEqual(results.count("item_conflict"), 1)
        image = self.store.get(self.item_id)
        self.assertIn(hashlib.sha256(image).hexdigest(), results)
        self.assertEqual(set(self.directory.iterdir()), {self.path})

    def test_lock_contention_times_out_without_mutation(self):
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = time.monotonic()
            self.assert_error("media_busy", lambda: self.store.put(self.item_id, jpeg_headers()))
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.95)
            self.assertLess(elapsed, 2.0)
            self.assertEqual(list(self.directory.iterdir()), [])
        finally:
            os.close(descriptor)
        self.store.put(self.item_id, jpeg_headers())
        self.assertEqual(self.store.get(self.item_id), jpeg_headers())

    def test_prune_preserves_linked_removes_orphans_and_pending(self):
        self.store.put(self.item_id, jpeg_headers())
        orphan_id = str(uuid4())
        self.store.put(orphan_id, jpeg_headers())
        pending = self.directory / (".pending-" + str(uuid4()))
        pending.write_bytes(b"partial record")
        pending.chmod(0o600)
        unknown = self.directory / "unrelated.media"
        unknown.write_bytes(b"untouched")
        self.assertEqual(self.store.prune([self.item_id, self.item_id]), 2)
        self.assertEqual(self.store.get(self.item_id), jpeg_headers())
        self.assertEqual(set(self.directory.iterdir()), {self.path, unknown})
        with patch("sage_lantern.history_media.os.fsync", wraps=os.fsync) as sync:
            self.assertEqual(self.store.prune({self.item_id}), 0)
        sync.assert_called_once()

    def test_prune_validates_entire_allowlist_before_side_effects(self):
        self.store.put(self.item_id, jpeg_headers())
        for retained in ([self.item_id, "invalid"], [self.item_id.upper()],
                         [uuid4()], None, self.item_id, {"unexpected": self.item_id}):
            with patch("sage_lantern.history_media.os.unlink") as unlink:
                with patch("sage_lantern.history_media.os.fsync") as sync:
                    self.assert_error("invalid_item_id", lambda: self.store.prune(retained))
            unlink.assert_not_called()
            sync.assert_not_called()
        self.assertEqual(self.store.get(self.item_id), jpeg_headers())

    def test_prune_unknown_names_and_missing_retained_are_untouched(self):
        for name in (".pending-invalid", "UPPER.media", "photo.jpg", str(uuid4()),
                     ".pending-" + str(uuid4()).upper()):
            (self.directory / name).write_bytes(b"unrelated")
        before = set(self.directory.iterdir())
        self.assertEqual(self.store.prune(frozenset({self.item_id})), 0)
        self.assertEqual(set(self.directory.iterdir()), before)

    def test_prune_refuses_symlinks_hardlinks_modes_and_special_files(self):
        target = self.directory / "sentinel"
        target.write_bytes(b"private-canary")
        target.chmod(0o600)
        for prefix, suffix in (("", ".media"), (".pending-", "")):
            candidate = self.directory / (prefix + self.item_id + suffix)
            for kind in ("symlink", "hardlink", "mode", "fifo", "directory"):
                if kind == "symlink":
                    candidate.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, candidate)
                elif kind == "fifo":
                    os.mkfifo(candidate, 0o600)
                elif kind == "directory":
                    candidate.mkdir(mode=0o700)
                else:
                    candidate.write_bytes(b"unsafe")
                    candidate.chmod(0o644)
                self.assert_error("unsafe_storage", lambda: self.store.prune([]))
                self.assertEqual(target.read_bytes(), b"private-canary")
                self.assertTrue(candidate.exists())
                candidate.rmdir() if kind == "directory" else candidate.unlink()

    def test_prune_fsync_failure_retry_syncs_even_when_empty(self):
        self.store.put(self.item_id, jpeg_headers())
        with patch("sage_lantern.history_media.os.fsync", side_effect=OSError("private-canary")):
            self.assert_error("media_unavailable", lambda: self.store.prune([]))
        self.assertFalse(self.path.exists())
        with patch("sage_lantern.history_media.os.fsync", wraps=os.fsync) as sync:
            self.assertEqual(self.store.prune([]), 0)
        sync.assert_called_once()

    def test_prune_unlink_failure_syncs_and_preserves_record(self):
        self.store.put(self.item_id, jpeg_headers())
        with patch("sage_lantern.history_media.os.unlink", side_effect=OSError("private-canary")):
            with patch("sage_lantern.history_media.os.fsync", wraps=os.fsync) as sync:
                self.assert_error("media_unavailable", lambda: self.store.prune([]))
        sync.assert_called_once()
        self.assertEqual(self.store.get(self.item_id), jpeg_headers())
        self.assertEqual(self.store.prune([]), 1)

    def test_prune_lock_contention_does_not_delete(self):
        self.store.put(self.item_id, jpeg_headers())
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_error("media_busy", lambda: self.store.prune([]))
        finally:
            os.close(descriptor)
        self.assertEqual(self.store.get(self.item_id), jpeg_headers())

    def test_prune_wrong_owner_preflight_prevents_any_deletion(self):
        self.store.put(self.item_id, jpeg_headers())
        other_id = str(uuid4())
        self.store.put(other_id, jpeg_headers())
        original_stat = os.stat

        def wrong_owner(path, *args, **kwargs):
            metadata = original_stat(path, *args, **kwargs)
            if path == other_id + ".media":
                fields = list(metadata)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return metadata

        with patch("sage_lantern.history_media.os.stat", side_effect=wrong_owner):
            with patch("sage_lantern.history_media.os.unlink") as unlink:
                self.assert_error("unsafe_storage", lambda: self.store.prune([]))
        unlink.assert_not_called()
        self.assertEqual(self.store.get(self.item_id), jpeg_headers())
        self.assertEqual(self.store.get(other_id), jpeg_headers())


if __name__ == "__main__":
    unittest.main()
