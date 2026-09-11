"""Encrypted private draft/review store; no transport or public delivery.

Provision an absolute owner-only 0700 directory on a local SQLite/flock/fsync
filesystem with trusted ancestors and no ACL grants. Root/same-UID writers are
trusted; mode checks are not encryption or a race-proof path sandbox. One store
owns the directory until close(); its methods serialize threads. Never use the
history directory. Source IDs and source text are immutable copies supplied by
an authorized caller; this module never opens or modifies source history.

All mutation request IDs and source/draft IDs are canonical UUID strings.
Reusing a request ID with identical arguments returns the original response,
not necessarily the latest revision; changed arguments fail. Review must name
the exact revision shown to the human. Editing invalidates review. Queueing
requires explicit consent but remains DRAFT, unsigned and not submitted.
The caller owns authentication, CSRF protection and the actual human consent UI.
Photo metadata is preview-only; no image bytes, paths or public attachment exist.

crypto is REQUIRED and supplied by the private-plane integration: encrypt(bytes)
and decrypt(bytes) must implement authenticated encryption with unique nonces
and a separately managed key. No key or passcode is generated/stored here. All
source text/IDs, draft payloads and idempotency arguments/results are encrypted;
draft/request UUIDs, revisions, flags and timestamps remain SQLite metadata.
This is not a Commons adapter/envelope queue or proof of dual-home compliance.
Unknown/locked crypto fails closed. The adapter must enforce its vault policy
on every call; no plaintext fallback, crypto caching or migration is provided.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4


MAX_DRAFTS = 100
MAX_REQUESTS = 256
MAX_DATABASE_BYTES = 16 * 1024 * 1024
DATABASE = "knowledge-sharing.sqlite3"


class KnowledgeSharingError(RuntimeError):
    """Content-free, stable error code."""


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise KnowledgeSharingError("invalid_id") from None
    return value


def _text(value, maximum):
    try:
        valid = type(value) is str and bool(value.strip()) and len(value.encode("utf-8")) <= maximum
    except UnicodeError:
        valid = False
    if not valid:
        raise KnowledgeSharingError("invalid_text")
    return value


def _photo(value):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"width", "height", "byte_count"}:
        raise KnowledgeSharingError("invalid_photo_metadata")
    for key, maximum in (("width", 8192), ("height", 8192), ("byte_count", 2 * 1024 * 1024)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise KnowledgeSharingError("invalid_photo_metadata")
    return dict(value)


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class KnowledgeSharingStore:
    def __init__(self, *, directory: Path, crypto=None):
        if crypto is None or not callable(getattr(crypto, "encrypt", None)) or not callable(getattr(crypto, "decrypt", None)):
            raise KnowledgeSharingError("crypto_required")
        self._crypto = crypto
        self._open(self._seal("probe", "probe"), "probe")
        self._db = None
        self._directory_fd = None
        self._guard = threading.RLock()
        self.directory = directory
        try:
            if not isinstance(directory, Path) or not directory.is_absolute() or directory.resolve(strict=True) != directory:
                raise KnowledgeSharingError("unsafe_storage")
            self._directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(self._directory_fd)
            self._identity = (info.st_dev, info.st_ino)
            self._check_storage()
            try:
                fcntl.flock(self._directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise KnowledgeSharingError("store_in_use") from None
            created = False
            try:
                descriptor = os.open(DATABASE, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                     0o600, dir_fd=self._directory_fd)
            except FileExistsError:
                pass
            else:
                created = True
                try:
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(self._directory_fd)
            self._check_storage()
            info = os.stat(DATABASE, dir_fd=self._directory_fd, follow_symlinks=False)
            self._database_identity = (info.st_dev, info.st_ino)
            if not created and info.st_size == 0:
                raise KnowledgeSharingError("invalid_database")
            self._db = sqlite3.connect(directory / DATABASE, timeout=1, isolation_level=None, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA trusted_schema=OFF")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA temp_store=MEMORY")
            page_size = self._db.execute("PRAGMA page_size").fetchone()[0]
            self._db.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}")
            if created:
                self._db.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE drafts (
                        draft_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                        original_json BLOB NOT NULL, payload BLOB NOT NULL,
                        revision INTEGER NOT NULL CHECK(revision >= 1),
                        reviewed_revision INTEGER, consented INTEGER NOT NULL CHECK(consented IN (0,1)),
                        state TEXT NOT NULL CHECK(state='DRAFT'),
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    CREATE TABLE requests (
                        request_id TEXT PRIMARY KEY, fingerprint BLOB NOT NULL, result BLOB NOT NULL);
                    CREATE TABLE crypto_check (singleton INTEGER PRIMARY KEY CHECK(singleton=1), value BLOB NOT NULL);
                    PRAGMA user_version=1;
                    COMMIT;
                """)
                self._db.execute("INSERT INTO crypto_check VALUES (1,?)", (self._seal("knowledge-sharing-v1", "store-key-check"),))
            elif (self._db.execute("PRAGMA user_version").fetchone()[0] != 1
                  or self._db.execute("PRAGMA quick_check").fetchone()[0] != "ok"):
                raise KnowledgeSharingError("invalid_database")
            check = self._db.execute("SELECT value FROM crypto_check WHERE singleton=1").fetchone()
            if check is None or self._open(check[0], "store-key-check") != "knowledge-sharing-v1":
                raise KnowledgeSharingError("crypto_unavailable")
            os.fsync(self._directory_fd)
        except (OSError, sqlite3.Error, KnowledgeSharingError):
            self.close()
            raise KnowledgeSharingError("store_unavailable") from None

    def _check_storage(self):
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700 or (info.st_dev, info.st_ino) != self._identity
                or self.directory.resolve(strict=True) != self.directory):
            raise KnowledgeSharingError("unsafe_storage")
        for name in (DATABASE, DATABASE + "-journal", DATABASE + "-wal", DATABASE + "-shm"):
            try:
                info = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                if name == DATABASE and hasattr(self, "_database_identity"):
                    raise KnowledgeSharingError("unsafe_storage") from None
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or info.st_size > MAX_DATABASE_BYTES):
                raise KnowledgeSharingError("unsafe_storage")
            if name == DATABASE and hasattr(self, "_database_identity") and (info.st_dev, info.st_ino) != self._database_identity:
                raise KnowledgeSharingError("unsafe_storage")

    def close(self):
        with self._guard:
            try:
                if self._db is not None:
                    self._db.close()
            finally:
                self._db = None
                if self._directory_fd is not None:
                    os.close(self._directory_fd)
                    self._directory_fd = None

    def _seal(self, value, binding):
        try:
            plain = _json(dict(binding=binding, value=value)).encode()
            if len(plain) > 262144:
                raise ValueError
            encrypted = self._crypto.encrypt(plain)
            if type(encrypted) is not bytes or not len(plain) + 16 <= len(encrypted) <= 524288:
                raise ValueError
            return encrypted
        except Exception:
            raise KnowledgeSharingError("crypto_unavailable") from None

    def _open(self, encrypted, binding):
        try:
            if type(encrypted) is not bytes or not 16 <= len(encrypted) <= 524288:
                raise ValueError
            plain = self._crypto.decrypt(encrypted)
            if type(plain) is not bytes or len(plain) > 262144:
                raise ValueError
            envelope = json.loads(plain)
            if type(envelope) is not dict or set(envelope) != {"binding", "value"} or envelope["binding"] != binding:
                raise ValueError
            return envelope["value"]
        except Exception:
            raise KnowledgeSharingError("crypto_unavailable") from None

    @staticmethod
    def _binding(row):
        return _json([row[key] for key in ("draft_id", "revision", "reviewed_revision", "consented", "state", "created_at", "updated_at")])

    def _snapshot(self, row):
        if row is None:
            raise KnowledgeSharingError("draft_not_found")
        payload = self._open(row["payload"], self._binding(row))
        return dict(draft_id=row["draft_id"], source_id=self._open(row["source_id"], row["draft_id"] + ":source"),
                    revision=row["revision"], state=row["state"],
                    reviewed=row["reviewed_revision"] == row["revision"],
                    consented=bool(row["consented"]),
                    created_at=row["created_at"], updated_at=row["updated_at"],
                    **payload, photo_status="preview_only" if payload["photo_metadata"] else "none",
                    public_photo_supported=False, transport="unavailable", publication="not_submitted", signed=False)

    def get(self, draft_id):
        _uuid(draft_id)
        with self._guard:
            try:
                if self._db is None:
                    raise KnowledgeSharingError("store_closed")
                self._check_storage()
                return self._snapshot(self._db.execute("SELECT * FROM drafts WHERE draft_id=?", (draft_id,)).fetchone())
            except (OSError, sqlite3.Error, ValueError):
                raise KnowledgeSharingError("store_unavailable") from None

    def _mutate(self, request_id, arguments, operation):
        _uuid(request_id)
        with self._guard:
            try:
                if self._db is None:
                    raise KnowledgeSharingError("store_closed")
                self._check_storage()
                self._db.execute("BEGIN IMMEDIATE")
                previous = self._db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
                if previous is not None:
                    if self._open(previous["fingerprint"], request_id + ":arguments") != arguments:
                        raise KnowledgeSharingError("idempotency_conflict")
                    result = self._open(previous["result"], request_id + ":result")
                else:
                    if self._db.execute("SELECT count(*) FROM requests").fetchone()[0] >= MAX_REQUESTS:
                        raise KnowledgeSharingError("capacity_exceeded")
                    result = operation()
                    self._db.execute("INSERT INTO requests VALUES (?,?,?)", (request_id,
                        self._seal(arguments, request_id + ":arguments"), self._seal(result, request_id + ":result")))
                self._db.execute("COMMIT")
                os.fsync(self._directory_fd)
                return result
            except Exception as error:
                if self._db is not None and self._db.in_transaction:
                    try:
                        self._db.execute("ROLLBACK")
                    except sqlite3.Error:
                        raise KnowledgeSharingError("store_unavailable") from None
                if isinstance(error, KnowledgeSharingError):
                    raise
                raise KnowledgeSharingError("store_unavailable") from None

    def create_draft(self, *, source_id, question, answer, request_id, photo_metadata=None):
        source_id = _uuid(source_id)
        payload = dict(question=_text(question, 4096), answer=_text(answer, 16384), photo_metadata=_photo(photo_metadata))

        def create():
            if self._db.execute("SELECT count(*) FROM drafts").fetchone()[0] >= MAX_DRAFTS:
                raise KnowledgeSharingError("capacity_exceeded")
            draft_id = str(uuid4())
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            row = dict(draft_id=draft_id, revision=1, reviewed_revision=None, consented=0,
                       state="DRAFT", created_at=now, updated_at=now)
            self._db.execute("INSERT INTO drafts VALUES (?,?,?,?,1,NULL,0,'DRAFT',?,?)",
                             (draft_id, self._seal(source_id, draft_id + ":source"),
                              self._seal(payload, draft_id + ":original"),
                              self._seal(payload, self._binding(row)), now, now))
            return self.get(draft_id)
        return self._mutate(request_id, ["create", source_id, payload], create)

    def _change(self, draft_id, expected_revision, request_id, action, payload=None):
        _uuid(draft_id)
        if type(expected_revision) is not int or not 1 <= expected_revision < 9007199254740991:
            raise KnowledgeSharingError("invalid_revision")

        def change():
            current = self.get(draft_id)
            if current["revision"] != expected_revision:
                raise KnowledgeSharingError("revision_conflict")
            if current["state"] != "DRAFT":
                raise KnowledgeSharingError("draft_frozen")
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            row = dict(self._db.execute("SELECT * FROM drafts WHERE draft_id=?", (draft_id,)).fetchone())
            content = self._open(row["payload"], self._binding(row))
            row.update(revision=expected_revision + 1, updated_at=now)
            if action == "update":
                content = payload
                row.update(reviewed_revision=None, consented=0)
            elif action == "review":
                row.update(reviewed_revision=row["revision"], consented=0)
            elif action == "save":
                if not current["reviewed"]:
                    raise KnowledgeSharingError("review_required")
                row.update(reviewed_revision=row["revision"], consented=1)
            self._db.execute("UPDATE drafts SET payload=?, revision=?, reviewed_revision=?, consented=?, updated_at=? WHERE draft_id=?",
                             (self._seal(content, self._binding(row)), row["revision"], row["reviewed_revision"], row["consented"], now, draft_id))
            return self.get(draft_id)
        return self._mutate(request_id, [action, draft_id, expected_revision, payload], change)

    def update_draft(self, draft_id, *, expected_revision, question, answer, request_id, photo_metadata=None):
        payload = dict(question=_text(question, 4096), answer=_text(answer, 16384), photo_metadata=_photo(photo_metadata))
        return self._change(draft_id, expected_revision, request_id, "update", payload)

    def review_draft(self, draft_id, *, expected_revision, request_id):
        return self._change(draft_id, expected_revision, request_id, "review")

    def save_reviewed(self, draft_id, *, expected_revision, consent, request_id, attach_photo=False):
        if consent is not True:
            raise KnowledgeSharingError("consent_required")
        if attach_photo is not False:
            raise KnowledgeSharingError("photo_attachment_unsupported")
        return self._change(draft_id, expected_revision, request_id, "save")

    def queue_reviewed(self, *args, **kwargs):
        raise KnowledgeSharingError("transport_unavailable")
