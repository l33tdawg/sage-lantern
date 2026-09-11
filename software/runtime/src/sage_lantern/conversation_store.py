"""Private local conversation outbox; suppression is not ledger or byte erasure.

One process owns a store until close(). No network, legacy import, or expiry runs
here. Unknown submissions require external evidence before they can be retried.
"""

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5


MAX_ENTRIES = 10000
MAX_PAYLOAD_BYTES = 12000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_DATABASE_BYTES = 128 * 1024 * 1024
_NAMESPACE = UUID("85fc645f-757a-4386-aaf5-f82076c47036")


class ConversationStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class JournalEntry:
    fingerprint: str
    receipt: object = None


def _session(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise ConversationStoreError("invalid_session_id") from None
    return value


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ConversationStore:
    """Create with an absolute private directory; existing invalid DBs fail closed.

    enqueue(ticket, item) accepts session_id, created_at (timezone required),
    question, answer, has_photo, and optional reflection text. It returns the
    interaction and optional reflection operation IDs, or [] without consent.
    pending() returns immutable JSON payload strings and their SHA-256 hashes.
    record_receipt() records evidence, not a claim of SAGE ledger erasure.
    """

    def __init__(self, directory: Path, *, approved=False):
        if type(approved) is not bool:
            raise ConversationStoreError("invalid_consent")
        self._approved = approved
        self._binding = threading.local()
        self.directory = Path(directory)
        self._mutex = threading.RLock()
        self._db = None
        self._directory_fd = None
        self._lock_fd = None
        self._database_identity = None
        self._closed = False
        try:
            if not self.directory.is_absolute():
                raise ConversationStoreError("unsafe_storage")
            for parent in reversed((self.directory, *self.directory.parents)):
                if parent == self.directory and not parent.exists() and not parent.is_symlink():
                    parent.mkdir(mode=0o700)
                    descriptor = os.open(parent.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                if not stat.S_ISDIR(parent.lstat().st_mode):
                    raise ConversationStoreError("unsafe_storage")
            self._directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._directory_identity = os.fstat(self._directory_fd)
            self._check_directory()
            initialized = self._exists("owner.lock")
            self._lock_fd = os.open("owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                    0o600, dir_fd=self._directory_fd)
            self._check_file(os.fstat(self._lock_fd))
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConversationStoreError("store_in_use") from None
            exists = self._exists("conversations.sqlite3")
            if not exists and initialized:
                raise ConversationStoreError("missing_database")
            if not exists:
                descriptor = os.open("conversations.sqlite3", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=self._directory_fd)
                os.fsync(descriptor)
                os.close(descriptor)
                os.fsync(self._directory_fd)
            self._check_storage()
            self._database_identity = os.stat("conversations.sqlite3", dir_fd=self._directory_fd, follow_symlinks=False)
            if exists and os.stat("conversations.sqlite3", dir_fd=self._directory_fd).st_size == 0:
                raise ConversationStoreError("invalid_database")
            self._db = sqlite3.connect(self.directory / "conversations.sqlite3", timeout=1,
                                       isolation_level=None, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            if exists:
                if self._db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ConversationStoreError("invalid_database")
                self._validate_schema()
            if self._db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise ConversationStoreError("durability_unavailable")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA temp_store=MEMORY")
            page_size = self._db.execute("PRAGMA page_size").fetchone()[0]
            self._db.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}")
            self._db.execute("PRAGMA journal_size_limit=0")
            with self._transaction():
                if not exists:
                    self._create_schema()
                self._db.execute("UPDATE operations SET state='unknown' WHERE state='dispatching'")
            os.fsync(self._directory_fd)
        except (OSError, sqlite3.Error, ConversationStoreError):
            self.close()
            raise ConversationStoreError("store_unavailable") from None

    def _exists(self, name):
        try:
            os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def _check_file(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ConversationStoreError("unsafe_storage")

    def _check_directory(self):
        for parent in self.directory.parents:
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise ConversationStoreError("unsafe_storage")
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700
                or (info.st_dev, info.st_ino) != (self._directory_identity.st_dev, self._directory_identity.st_ino)):
            raise ConversationStoreError("unsafe_storage")

    def _check_storage(self):
        self._check_directory()
        for name in ("owner.lock", "conversations.sqlite3", "conversations.sqlite3-wal",
                     "conversations.sqlite3-shm", "conversations.sqlite3-journal"):
            if self._exists(name):
                info = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
                self._check_file(info)
                if info.st_size > MAX_DATABASE_BYTES:
                    raise ConversationStoreError("capacity_exceeded")
        if self._database_identity is not None:
            info = os.stat("conversations.sqlite3", dir_fd=self._directory_fd, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (self._database_identity.st_dev, self._database_identity.st_ino):
                raise ConversationStoreError("unsafe_storage")
        if self._lock_fd is not None:
            info = os.stat("owner.lock", dir_fd=self._directory_fd, follow_symlinks=False)
            opened = os.fstat(self._lock_fd)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise ConversationStoreError("unsafe_storage")

    def _create_schema(self):
        statements = (
            "CREATE TABLE settings (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL CHECK(version=1), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), generation TEXT NOT NULL, history_generation TEXT)",
            "CREATE TABLE interactions (session_id TEXT PRIMARY KEY, item TEXT NOT NULL, item_hash TEXT NOT NULL, consent_generation TEXT NOT NULL, history_generation TEXT)",
            "CREATE TABLE suppressed (session_id TEXT PRIMARY KEY)",
            "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES interactions(session_id), kind TEXT NOT NULL CHECK(kind IN ('interaction','reflection')), payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, consent_generation TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','dispatching','unknown','accepted','recall_ready','cancelled')), history_generation TEXT, memory_id TEXT, sage_status TEXT, deprecation_status TEXT, consent_revoked INTEGER NOT NULL DEFAULT 0 CHECK(consent_revoked IN (0,1)), UNIQUE(session_id,kind))",
            "CREATE INDEX operations_state ON operations(state)",
            "CREATE TABLE journal (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id), receipt TEXT)",
        )
        for statement in statements:
            self._db.execute(statement)
        self._db.execute("INSERT INTO settings VALUES (1,1,?,?,NULL)", (int(self._approved), str(uuid4())))

    def _validate_schema(self):
        settings = self._db.execute("SELECT singleton,version,enabled,generation FROM settings").fetchall()
        if (len(settings) != 1 or tuple(settings[0])[:3] not in ((1, 1, 0), (1, 1, 1))):
            raise ConversationStoreError("invalid_database")
        _session(settings[0]["generation"])
        self._db.execute("SELECT session_id,item,item_hash,consent_generation,history_generation FROM interactions LIMIT 0")
        self._db.execute("SELECT session_id FROM suppressed LIMIT 0")
        self._db.execute("SELECT operation_id,session_id,kind,payload,payload_sha256,consent_generation,state,memory_id,sage_status,deprecation_status,history_generation,consent_revoked FROM operations LIMIT 0")
        self._db.execute("SELECT history_generation FROM settings")
        self._db.execute("SELECT key,fingerprint,operation_id,receipt FROM journal LIMIT 0")

    @contextmanager
    def _transaction(self):
        with self._mutex:
            if self._closed:
                raise ConversationStoreError("store_closed")
            try:
                self._check_storage()
                self._db.execute("BEGIN IMMEDIATE")
                yield
                self._check_storage()
                self._db.execute("COMMIT")
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except (OSError, sqlite3.Error):
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise ConversationStoreError("store_unavailable") from None
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def close(self):
        with self._mutex:
            self._closed = True
            try:
                if self._db is not None:
                    self._db.close()
            finally:
                self._db = None
                for field in ("_lock_fd", "_directory_fd"):
                    descriptor = getattr(self, field)
                    if descriptor is not None:
                        os.close(descriptor)
                        setattr(self, field, None)

    def approve(self, enabled, *, history_generation=None):
        if type(enabled) is not bool:
            raise ConversationStoreError("invalid_consent")
        if history_generation is not None and (not isinstance(history_generation, str) or not 1 <= len(history_generation) <= 128):
            raise ConversationStoreError("invalid_consent")
        with self._transaction():
            settings = self._settings()
            if bool(settings["enabled"]) != enabled or settings["history_generation"] != history_generation:
                self._db.execute("UPDATE settings SET enabled=?,generation=?,history_generation=?", (int(enabled), str(uuid4()), history_generation))
                if settings["enabled"] and not enabled:
                    self._db.execute("UPDATE operations SET consent_revoked=1 WHERE state IN ('queued','dispatching','unknown')")
                    self._db.execute("UPDATE operations SET state='cancelled' WHERE state='queued'")
                    self._db.execute("UPDATE operations SET state='unknown' WHERE state='dispatching'")
            return self._ticket()

    def _settings(self):
        self._validate_schema()
        return self._db.execute("SELECT * FROM settings").fetchone()

    def _ticket(self):
        settings = self._settings()
        if not settings["enabled"]:
            return None
        if settings["history_generation"] is None:
            return settings["generation"]
        return {"store_generation": settings["generation"], "history_generation": settings["history_generation"]}

    @staticmethod
    def _generation(ticket):
        return ticket["store_generation"] if isinstance(ticket, dict) else ticket

    def consent(self):
        with self._transaction():
            return self._ticket()

    def enqueue(self, ticket, item):
        required = {"session_id", "created_at", "question", "answer", "has_photo"}
        if (not isinstance(item, dict) or not required <= item.keys()
                or item.keys() - required - {"reflection"}
                or type(item["has_photo"]) is not bool
                or any(not isinstance(item[field], str) for field in required - {"has_photo"})
                or ("reflection" in item and not isinstance(item["reflection"], str))):
            raise ConversationStoreError("invalid_item")
        session_id = _session(item["session_id"])
        try:
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            if created.utcoffset() is None:
                raise ValueError
            encoded = _encode(item)
            if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise ValueError
        except (ValueError, UnicodeError):
            raise ConversationStoreError("invalid_item") from None
        with self._transaction():
            if ticket is None or ticket != self._ticket() or self._is_suppressed(session_id):
                return []
            existing = self._db.execute("SELECT item FROM interactions WHERE session_id=?", (session_id,)).fetchone()
            if existing:
                if existing["item"] != encoded:
                    raise ConversationStoreError("payload_conflict")
                return [row[0] for row in self._db.execute("SELECT operation_id FROM operations WHERE session_id=? ORDER BY kind", (session_id,))]
            operations = []
            for kind in ("interaction", "reflection"):
                if kind == "reflection" and "reflection" not in item:
                    continue
                content = ({key: item[key] for key in required} if kind == "interaction" else
                           {"session_id": session_id, "created_at": item["created_at"], "reflection": item["reflection"]})
                payload = _encode({"version": 1, "kind": kind, **content})
                if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                    raise ConversationStoreError("capacity_exceeded")
                operations.append((str(uuid5(_NAMESPACE, session_id + ":" + kind)), session_id, kind, payload,
                                   hashlib.sha256(payload.encode("utf-8")).hexdigest(), self._generation(ticket), "queued", self._settings()["history_generation"]))
            size = self._db.execute("SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM operations").fetchone()[0]
            size += self._db.execute("SELECT COALESCE(SUM(length(CAST(item AS BLOB))),0) FROM interactions").fetchone()[0]
            count = self._db.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
            if count >= MAX_ENTRIES or size + len(encoded.encode("utf-8")) + sum(len(row[3].encode("utf-8")) for row in operations) > MAX_TOTAL_BYTES:
                raise ConversationStoreError("capacity_exceeded")
            self._db.execute("INSERT INTO interactions VALUES (?,?,?,?,?)", (session_id, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest(), self._generation(ticket), self._settings()["history_generation"]))
            self._db.executemany("INSERT INTO operations (operation_id,session_id,kind,payload,payload_sha256,consent_generation,state,history_generation) VALUES (?,?,?,?,?,?,?,?)", operations)
            return [row[0] for row in operations]

    def _is_suppressed(self, session_id):
        return self._db.execute("SELECT 1 FROM suppressed WHERE session_id=?", (session_id,)).fetchone() is not None

    def _operation(self, operation_id):
        row = self._db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise ConversationStoreError("operation_not_found")
        if hashlib.sha256(row["payload"].encode("utf-8")).hexdigest() != row["payload_sha256"]:
            raise ConversationStoreError("payload_corrupt")
        result = dict(row)
        result["suppressed"] = self._is_suppressed(row["session_id"])
        return result

    def pending(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ConversationStoreError("invalid_limit")
        with self._transaction():
            if not self._settings()["enabled"]:
                return []
            rows = self._db.execute("SELECT operation_id FROM operations WHERE state='queued' AND consent_revoked=0 AND session_id NOT IN (SELECT session_id FROM suppressed) ORDER BY rowid LIMIT ?", (limit,)).fetchall()
            return [self._operation(row[0]) for row in rows]

    def operation(self, operation_id):
        with self._transaction():
            return self._operation(operation_id)

    def unresolved(self):
        with self._transaction():
            return [self._operation(row[0]) for row in self._db.execute("SELECT operation_id FROM operations WHERE state='unknown' ORDER BY rowid").fetchall()]

    def operations(self, state=None, include_suppressed=False):
        if state is not None and state not in {"queued", "dispatching", "unknown", "accepted", "recall_ready", "cancelled"}:
            raise ConversationStoreError("invalid_state")
        if type(include_suppressed) is not bool:
            raise ConversationStoreError("invalid_filter")
        with self._transaction():
            rows = self._db.execute("SELECT operation_id FROM operations WHERE (? IS NULL OR state=?) ORDER BY rowid", (state, state)).fetchall()
            operations = [self._operation(row[0]) for row in rows]
            return [operation for operation in operations if include_suppressed or not operation["suppressed"]]

    def mark_deprecated(self, operation_id, status):
        """Record withdrawal evidence separately; this never means byte erasure."""
        if status not in {"pending", "challenged", "deprecated", "blocked_permission", "unknown"}:
            raise ConversationStoreError("invalid_deprecation_status")
        with self._transaction():
            operation = self._operation(operation_id)
            if not operation["suppressed"] or operation["memory_id"] is None:
                raise ConversationStoreError("deprecation_denied")
            if operation["deprecation_status"] == "deprecated" and status != "deprecated":
                raise ConversationStoreError("invalid_transition")
            self._db.execute("UPDATE operations SET deprecation_status=? WHERE operation_id=?", (status, operation_id))
            if status in {"challenged", "deprecated"}:
                self._db.execute("UPDATE operations SET sage_status=?,state='accepted' WHERE operation_id=?", (status, operation_id))
            return self._operation(operation_id)

    def mark_dispatching(self, operation_id):
        with self._transaction():
            operation = self._operation(operation_id)
            if (operation["state"] != "queued" or operation["suppressed"]
                    or operation["consent_revoked"] or not self._settings()["enabled"]):
                raise ConversationStoreError("dispatch_denied")
            self._db.execute("UPDATE operations SET state='dispatching' WHERE operation_id=?", (operation_id,))
            return self._operation(operation_id)

    def mark_unknown(self, operation_id):
        with self._transaction():
            operation = self._operation(operation_id)
            if operation["state"] not in {"dispatching", "unknown"}:
                raise ConversationStoreError("invalid_transition")
            self._db.execute("UPDATE operations SET state='unknown' WHERE operation_id=?", (operation_id,))
            return self._operation(operation_id)

    def _receipt(self, operation_id, memory_id, recall_ready):
        if (not isinstance(memory_id, str) or not memory_id.isascii() or not 1 <= len(memory_id) <= 256
                or any(ord(character) < 33 or ord(character) > 126 for character in memory_id)
                or type(recall_ready) is not bool):
            raise ConversationStoreError("invalid_receipt")
        operation = self._operation(operation_id)
        if operation["state"] not in {"dispatching", "unknown", "accepted", "recall_ready"}:
            raise ConversationStoreError("invalid_transition")
        if operation["memory_id"] not in (None, memory_id):
            raise ConversationStoreError("receipt_conflict")
        state = "recall_ready" if recall_ready or operation["state"] == "recall_ready" else "accepted"
        self._db.execute("UPDATE operations SET state=?,memory_id=? WHERE operation_id=?", (state, memory_id, operation_id))
        return self._operation(operation_id)

    def record_receipt(self, operation_id, memory_id, *, recall_ready=False):
        with self._transaction():
            self._receipt(operation_id, memory_id, recall_ready)
            if recall_ready:
                self._db.execute("UPDATE operations SET sage_status='committed' WHERE operation_id=?", (operation_id,))
            return self._operation(operation_id)

    def reconcile_unknown(self, operation_id, *, memory_id=None, proven_not_submitted=False, recall_ready=False):
        """Requeue only on external proof of non-submission, never search absence."""
        if type(proven_not_submitted) is not bool or type(recall_ready) is not bool:
            raise ConversationStoreError("invalid_reconciliation")
        if (memory_id is None) == (not proven_not_submitted) or (memory_id is None and recall_ready):
            raise ConversationStoreError("invalid_reconciliation")
        with self._transaction():
            operation = self._operation(operation_id)
            if operation["state"] != "unknown":
                raise ConversationStoreError("invalid_transition")
            if memory_id is not None:
                return self._receipt(operation_id, memory_id, recall_ready)
            if self._db.execute("SELECT 1 FROM journal WHERE operation_id=?", (operation_id,)).fetchone():
                raise ConversationStoreError("reserved_operation_requires_reconciliation")
            state = "queued" if not operation["suppressed"] and not operation["consent_revoked"] and self._settings()["enabled"] else "cancelled"
            self._db.execute("UPDATE operations SET state=? WHERE operation_id=?", (state, operation_id))
            return self._operation(operation_id)

    def suppress(self, session_ids):
        if not isinstance(session_ids, (list, tuple, set, frozenset)) or len(session_ids) > MAX_ENTRIES:
            raise ConversationStoreError("invalid_sessions")
        session_ids = sorted({_session(value) for value in session_ids})
        with self._transaction():
            existing = {row[0] for row in self._db.execute("SELECT session_id FROM suppressed")}
            if len(existing | set(session_ids)) > MAX_ENTRIES:
                raise ConversationStoreError("capacity_exceeded")
            self._db.executemany("INSERT OR IGNORE INTO suppressed VALUES (?)", [(value,) for value in session_ids])
            for session_id in session_ids:
                self._db.execute("UPDATE operations SET state=CASE state WHEN 'queued' THEN 'cancelled' WHEN 'dispatching' THEN 'unknown' ELSE state END WHERE session_id=?", (session_id,))
            return session_ids

    def list(self, include_suppressed=False):
        if type(include_suppressed) is not bool:
            raise ConversationStoreError("invalid_filter")
        with self._transaction():
            result = []
            for row in self._db.execute("SELECT * FROM interactions ORDER BY rowid DESC"):
                suppressed = self._is_suppressed(row["session_id"])
                if suppressed and not include_suppressed:
                    continue
                if hashlib.sha256(row["item"].encode("utf-8")).hexdigest() != row["item_hash"]:
                    raise ConversationStoreError("payload_corrupt")
                result.append({**json.loads(row["item"]), "consent_generation": row["consent_generation"], "history_generation": row["history_generation"], "suppressed": suppressed})
            return result

    def suppressed_ids(self):
        with self._transaction():
            return {row[0] for row in self._db.execute("SELECT session_id FROM suppressed")}

    def is_suppressed(self, session_id):
        with self._transaction():
            return self._is_suppressed(_session(session_id))

    @contextmanager
    def bind_operation(self, operation_id):
        """Bind opaque adapter reserve keys to one work row on this thread.

        Hold no SQLite transaction over the adapter/network call. reserve() is
        the final atomic consent/suppression gate and may claim a queued row.
        """
        if getattr(self._binding, "operation_id", None) is not None:
            raise ConversationStoreError("already_bound")
        self.operation(operation_id)
        self._binding.operation_id = operation_id
        try:
            yield self
        finally:
            del self._binding.operation_id

    def work(self, session_id, kind):
        if kind not in {"interaction", "reflection"}:
            raise ConversationStoreError("invalid_kind")
        return self.operation(str(uuid5(_NAMESPACE, _session(session_id) + ":" + kind)))

    @staticmethod
    def _journal_token(value):
        if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ConversationStoreError("invalid_journal_token")
        return value

    def reserve(self, key, fingerprint):
        self._journal_token(key)
        self._journal_token(fingerprint)
        with self._transaction():
            previous = self._db.execute("SELECT * FROM journal WHERE key=?", (key,)).fetchone()
            operation_id = getattr(self._binding, "operation_id", None)
            if previous:
                if previous["fingerprint"] != fingerprint or (operation_id is not None and previous["operation_id"] != operation_id):
                    raise ConversationStoreError("journal_conflict")
                return False
            if operation_id is None:
                raise ConversationStoreError("operation_binding_required")
            operation = self._operation(operation_id)
            if (operation["state"] not in {"queued", "dispatching"} or operation["suppressed"]
                    or operation["consent_revoked"] or not self._settings()["enabled"]):
                raise ConversationStoreError("dispatch_denied")
            self._db.execute("INSERT INTO journal VALUES (?,?,?,NULL)", (key, fingerprint, operation_id))
            self._db.execute("UPDATE operations SET state='dispatching' WHERE operation_id=?", (operation_id,))
            return True

    def get(self, key):
        self._journal_token(key)
        with self._transaction():
            row = self._db.execute("SELECT * FROM journal WHERE key=?", (key,)).fetchone()
            if row is None:
                raise ConversationStoreError("journal_not_found")
            receipt = None if row["receipt"] is None else SimpleNamespace(**json.loads(row["receipt"]))
            return JournalEntry(row["fingerprint"], receipt)

    def complete(self, key, receipt):
        self._journal_token(key)
        fields = {"operation_key", "memory_id", "tx_hash", "status", "committed", "committed_height", "projection_confirmed", "retryable", "outcome"}
        try:
            value = asdict(receipt) if is_dataclass(receipt) else dict(vars(receipt))
            if (set(value) != fields or value["operation_key"] != key
                    or value["outcome"] not in {"acknowledged", "projection_observed"}
                    or value["status"] not in {"proposed", "validated", "committed", "challenged", "deprecated", "committed_unconfirmed"}
                    or any(value[field] is not None and type(value[field]) is not bool for field in ("committed", "projection_confirmed", "retryable"))
                    or (value["committed_height"] is not None and (type(value["committed_height"]) is not int or value["committed_height"] < 1))
                    or not isinstance(value["tx_hash"], str) or len(value["tx_hash"]) > 256):
                raise ValueError
            encoded = _encode(value)
            if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise ValueError
        except (TypeError, ValueError, UnicodeError):
            raise ConversationStoreError("invalid_receipt") from None
        with self._transaction():
            row = self._db.execute("SELECT * FROM journal WHERE key=?", (key,)).fetchone()
            if row is None:
                raise ConversationStoreError("journal_not_found")
            if row["receipt"] is not None:
                previous = json.loads(row["receipt"])
                if previous["memory_id"] != value["memory_id"]:
                    raise ConversationStoreError("receipt_conflict")
                for field in ("tx_hash", "committed_height", "committed"):
                    if previous[field] is not None and previous[field] != "":
                        if value[field] is not None and value[field] != "" and value[field] != previous[field]:
                            raise ConversationStoreError("receipt_conflict")
                        value[field] = previous[field]
                encoded = _encode(value)
            ready = value["status"] == "committed" and value["projection_confirmed"] is True
            self._receipt(row["operation_id"], value["memory_id"], ready)
            self._db.execute("UPDATE operations SET sage_status=?,state=? WHERE operation_id=?", (value["status"], "recall_ready" if ready else "accepted", row["operation_id"]))
            self._db.execute("UPDATE journal SET receipt=? WHERE key=?", (encoded, key))
