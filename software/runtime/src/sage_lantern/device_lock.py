"""Staged device-lock foundation, not a complete device security boundary.

Callers must protect every sensitive surface separately. PINs must already be
SAGE vault passphrases; this module never provisions credentials. Dashboard login
can unlock SAGE's serving vault; revoking its cookie does not relock that vault.
Root, same-UID processes, inherited ACLs and the configured clocks are trusted.
Only rate-limit timestamps persist. Device sessions belong to one controller
instance and vanish on restart; separate instances share only the rate limiter.
Deploy one broker process with one DeviceLock instance for all device requests.
Its verification mutex does not serialize other SAGE clients or other instances.
Unlock returns an inactive 30-second ticket. Activation acknowledges possession;
the sole active session needs both its idle deadline and six-second presence
lease. Presence renews only presence; activity renews only idle. Reads renew none.
No PIN/cookie logging, proxy configuration, redirects, or caller-supplied URLs.
"""

from __future__ import annotations

import fcntl
import hashlib
import http.client
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path


DATABASE_NAME = "device-lock.sqlite3"
_GUARD_NAME = "device-lock.guard"
_MAX_FILE_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 8192
_AUTH_PATH = "/v1/dashboard/auth/"
_SCHEMA = "CREATE TABLE rate_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_seen REAL NOT NULL, attempts TEXT NOT NULL)"


class DeviceLockError(RuntimeError):
    """Only bounded generic codes, never backend response bodies or secrets."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _valid_pin(pin):
    return isinstance(pin, str) and re.fullmatch(r"[0-9]{8,12}", pin) is not None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class _LoginDenied(Exception):
    pass


class SagePINVerifier:
    """Verify using only fixed loopback auth endpoints and revoke the cookie.

    Revocation is attempted even after malformed login/check responses when a
    usable cookie was received. Network loss can make revocation unconfirmable;
    verification then fails and no device session is issued. A lost login
    response may leave a server-side session whose cookie was never received.
    Exact cookie-free login denials preserve prior cleanup evidence: SAGE's
    handleLogin returns 401/wrong passphrase or 429/too many login attempts before
    issueDashboardSession (web/handler.go). Other failures remain uncertain;
    later verification cannot reconcile a previously unknown session.
    """

    def __init__(self, *, timeout=3.0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise DeviceLockError("invalid_configuration")
        self.timeout = timeout
        self.cleanup_confirmed = True

    def _request(self, method, action, *, pin=None, cookie=None, capture=None):
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=self.timeout)
        response = None
        try:
            headers = {"Accept": "application/json", "Connection": "close"}
            body = None
            if pin is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps({"passphrase": pin}, separators=(",", ":")).encode("ascii")
            if cookie is not None:
                headers["Cookie"] = "sage_session=" + cookie
            connection.request(method, _AUTH_PATH + action, body=body, headers=headers)
            response = connection.getresponse()
            response_headers = response.getheaders()
            denial = (method == "POST" and action == "login" and response.status in (401, 429)
                      and not any(name.lower() == "set-cookie" for name, value in response_headers))
            if capture is not None and not denial:
                capture(response_headers)
            if ((response.status != 200 and not denial) or sum(len(name) + len(value) for name, value in response_headers) > 16384
                    or any(name.lower() == "location" for name, value in response_headers)):
                raise ValueError
            content_types = [value.split(";", 1)[0].strip().lower() for name, value in response_headers if name.lower() == "content-type"]
            if content_types != ["application/json"]:
                raise ValueError
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError
            result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict):
                raise ValueError
            if denial:
                expected = "wrong passphrase" if response.status == 401 else "too many login attempts, try again later"
                if set(result) != {"ok", "error"} or result["ok"] is not False or result["error"] != expected:
                    raise ValueError
                raise _LoginDenied()
            return result
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                connection.close()

    @staticmethod
    def _check(result, authenticated):
        if (set(result) != {"auth_required", "authenticated"}
                or result["auth_required"] is not True or result["authenticated"] is not authenticated):
            raise ValueError

    def __call__(self, pin):
        if not _valid_pin(pin):
            raise DeviceLockError("invalid_pin")
        cookie = None
        verified = False
        revoked = False
        previous_cleanup = self.cleanup_confirmed
        self.cleanup_confirmed = False

        def capture(headers):
            nonlocal cookie
            values = [value for name, value in headers if name.lower() == "set-cookie"]
            if len(values) != 1 or len(values[0]) > 1024:
                raise ValueError
            parsed = SimpleCookie()
            parsed.load(values[0])
            if set(parsed) != {"sage_session"}:
                raise ValueError
            candidate = parsed["sage_session"]
            if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", candidate.value) is None:
                raise ValueError
            cookie = candidate.value
            if candidate["domain"] or candidate["path"] != "/" or not candidate["httponly"]:
                raise ValueError

        try:
            self._check(self._request("GET", "check"), False)
            result = self._request("POST", "login", pin=pin, capture=capture)
            if result != {"ok": True} or result.get("ok") is not True or cookie is None:
                raise ValueError
            self._check(self._request("GET", "check", cookie=cookie), True)
            verified = True
        except _LoginDenied:
            self.cleanup_confirmed = previous_cleanup
            verified = False
        except Exception:
            verified = False
        finally:
            if cookie is not None:
                try:
                    result = self._request("POST", "lock", cookie=cookie)
                    if (set(result) != {"ok", "locked"} or result["ok"] is not True or result["locked"] is not True):
                        raise ValueError
                    self._check(self._request("GET", "check", cookie=cookie), False)
                    revoked = True
                except Exception:
                    revoked = False
            cookie = None
        if not verified or not revoked:
            raise DeviceLockError("verification_failed")
        self.cleanup_confirmed = previous_cleanup
        return True


class DeviceLock:
    """Locked on construction; 5 admitted attempts per rolling 60 seconds.

    verifier(pin) must return exactly True. clock is monotonic; wall_clock is
    persisted for restart-safe limiting. Wall-clock rollback freezes the limit
    conservatively until time catches up. Generation changes cancel in-flight
    unlocks. Only explicit authenticated touch() renews the idle expiry; status
    and authorized never renew it. The private directory is trusted configuration,
    not request input.
    """

    def __init__(self, directory: Path, *, verifier=None, clock=time.monotonic,
                 wall_clock=time.time, session_seconds=300):
        if (type(session_seconds) not in (int, float) or not math.isfinite(session_seconds)
                or not 1 <= session_seconds <= 3600 or not callable(clock) or not callable(wall_clock)
                or (verifier is not None and not callable(verifier))):
            raise DeviceLockError("invalid_configuration")
        self._mutex = threading.RLock()
        self._verification_mutex = threading.Lock()
        self._verifier = SagePINVerifier() if verifier is None else verifier
        self._clock = clock
        self._wall_clock = wall_clock
        self._session_seconds = session_seconds
        self._sessions = {}
        self._pending = {}
        self._presence = {}
        self._generation = 0
        self._access_epoch = secrets.token_hex(16)
        self._last_monotonic = None
        self._closed = False
        self._db = None
        self._directory_fd = None
        self._guard_fd = None
        try:
            self._directory = Path(directory)
            if not self._directory.is_absolute():
                raise ValueError
            if not self._directory.exists():
                self._directory.mkdir(mode=0o700)
                descriptor = os.open(self._directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            if self._directory.resolve(strict=True) != self._directory:
                raise ValueError
            self._directory_fd = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._directory_identity = os.fstat(self._directory_fd)
            self._check_directory()
            try:
                self._guard_fd = os.open(_GUARD_NAME, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=self._directory_fd)
                first = True
            except FileExistsError:
                self._guard_fd = os.open(_GUARD_NAME, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                         dir_fd=self._directory_fd)
                first = False
            self._check_file(_GUARD_NAME)
            fcntl.flock(self._guard_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                self._initialize(first)
            finally:
                fcntl.flock(self._guard_fd, fcntl.LOCK_UN)
        except Exception:
            self.close()
            raise DeviceLockError("unavailable") from None

    def _check_directory(self):
        info = self._directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700
                or self._directory.resolve(strict=True) != self._directory
                or (info.st_dev, info.st_ino) != (self._directory_identity.st_dev, self._directory_identity.st_ino)):
            raise ValueError

    def _check_file(self, name):
        info = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > _MAX_FILE_BYTES):
            raise ValueError
        return info

    def _check_files(self):
        self._check_directory()
        for name, original in ((DATABASE_NAME, self._file_identity), (_GUARD_NAME, os.fstat(self._guard_fd))):
            current = self._check_file(name)
            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                raise ValueError
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                self._check_file(DATABASE_NAME + suffix)
            except FileNotFoundError:
                continue
            if suffix != "-journal":
                raise ValueError

    def _initialize(self, first):
        try:
            self._file_identity = self._check_file(DATABASE_NAME)
            created = False
        except FileNotFoundError:
            if not first:
                raise ValueError
            descriptor = os.open(DATABASE_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=self._directory_fd)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._file_identity = self._check_file(DATABASE_NAME)
            created = True
        self._check_files()
        if not created and self._file_identity.st_size == 0:
            raise ValueError
        os.fsync(self._directory_fd)
        self._db = sqlite3.connect((self._directory / DATABASE_NAME).as_uri() + "?mode=rw", uri=True,
                                   timeout=1, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA trusted_schema=OFF")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA temp_store=MEMORY")
        page_size = self._db.execute("PRAGMA page_size").fetchone()[0]
        self._db.execute(f"PRAGMA max_page_count={_MAX_FILE_BYTES // page_size}")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if created:
                self._db.execute(_SCHEMA)
                self._db.execute("INSERT INTO rate_state VALUES (1,0,'[]')")
                self._db.execute("PRAGMA user_version=1")
            self._state()
            if self._db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError
            self._check_files()
            self._db.execute("COMMIT")
            os.fsync(self._directory_fd)
        except Exception:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    def _state(self):
        if (self._db.execute("PRAGMA user_version").fetchone()[0] != 1
                or self._db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
                or self._db.execute("SELECT type,name,sql FROM sqlite_master").fetchall() != [("table", "rate_state", _SCHEMA)]):
            raise ValueError
        rows = self._db.execute("SELECT singleton,last_seen,attempts FROM rate_state").fetchall()
        if len(rows) != 1 or rows[0][0] != 1 or not isinstance(rows[0][2], str) or len(rows[0][2]) > 512:
            raise ValueError
        last_seen = self._timestamp(rows[0][1])
        attempts = json.loads(rows[0][2])
        if not isinstance(attempts, list) or len(attempts) > 5:
            raise ValueError
        if any(self._timestamp(value) > last_seen for value in attempts) or attempts != sorted(attempts):
            raise ValueError
        return last_seen, attempts

    @staticmethod
    def _timestamp(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError
        return value

    @contextmanager
    def _transaction(self):
        try:
            if self._closed:
                raise ValueError
            self._check_files()
            self._db.execute("BEGIN IMMEDIATE")
            yield
            self._check_files()
            self._db.execute("COMMIT")
        except Exception:
            self._sessions.clear()
            self._pending.clear()
            self._presence.clear()
            self._generation += 1
            self._access_epoch = secrets.token_hex(16)
            if self._db is not None and self._db.in_transaction:
                try:
                    self._db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise DeviceLockError("unavailable") from None

    def _consume_attempt(self):
        with self._transaction():
            last_seen, attempts = self._state()
            now = max(self._timestamp(self._wall_clock()), last_seen)
            attempts = [attempt for attempt in attempts if attempt > now - 60]
            admitted = len(attempts) < 5
            if admitted:
                attempts.append(now)
            self._db.execute("UPDATE rate_state SET last_seen=?,attempts=? WHERE singleton=1",
                             (now, json.dumps(attempts, separators=(",", ":"))))
        if not admitted:
            raise DeviceLockError("rate_limited")

    def _now(self):
        try:
            now = self._timestamp(self._clock())
            if self._last_monotonic is not None and now < self._last_monotonic:
                raise ValueError
            self._last_monotonic = now
            previous = (len(self._pending), len(self._sessions))
            self._pending = {digest: expiry for digest, expiry in self._pending.items() if expiry > now}
            self._sessions = {digest: expiry for digest, expiry in self._sessions.items()
                              if expiry > now and self._presence.get(digest, 0) > now}
            self._presence = {digest: expiry for digest, expiry in self._presence.items() if digest in self._sessions}
            if previous != (len(self._pending), len(self._sessions)):
                self._access_epoch = secrets.token_hex(16)
            return now
        except Exception:
            self._sessions.clear()
            self._pending.clear()
            self._presence.clear()
            self._generation += 1
            self._access_epoch = secrets.token_hex(16)
            raise DeviceLockError("unavailable") from None

    def status(self):
        with self._mutex:
            with self._transaction():
                self._state()
            self._now()
            return {"locked": not bool(self._sessions)}

    def reserve_attempt(self):
        """Durably charge an external PIN check to this broker's shared budget.

        This does not verify a PIN or issue a token. The single broker must also
        serialize external setup checks with ordinary unlock verification.
        """
        with self._mutex:
            self._consume_attempt()
            self._now()

    def access_epoch(self):
        """Atomic noncredential snapshot; compare across work, never authorize UI."""
        with self._mutex:
            state = self.status()
            return {"schema": "sage.lantern.access-epoch.v1", "locked": state["locked"],
                    "epoch": self._access_epoch}

    def unlock(self, pin):
        """Verify a PIN and issue a hashed, inactive 30-second activation ticket."""
        return self._verify_pin(pin, issue_ticket=True)

    def verify_pin(self, pin):
        """Verify for a separate trusted surface without issuing browser access.

        Uses the same durable attempt budget, verifier mutex and lock-generation
        fence as unlock(). The single AuthBroker must serialize upstream vault
        operations and enforce setup/cleanup policy before granting that surface.
        """
        return self._verify_pin(pin, issue_ticket=False)

    def session_seconds(self):
        with self._mutex:
            if self._closed:
                raise DeviceLockError("unavailable")
            return self._session_seconds

    def _verify_pin(self, pin, *, issue_ticket):
        with self._mutex:
            generation = self._generation
            self._consume_attempt()
            self._now()
        if not _valid_pin(pin):
            raise DeviceLockError("invalid_pin")
        if not self._verification_mutex.acquire(blocking=False):
            raise DeviceLockError("verification_busy")
        try:
            with self._mutex:
                if generation != self._generation or self._closed:
                    raise DeviceLockError("unlock_cancelled")
            try:
                verified = self._verifier(pin) is True
            except Exception:
                verified = False
            if not verified:
                raise DeviceLockError("verification_failed")
            with self._mutex:
                with self._transaction():
                    self._state()
                if generation != self._generation:
                    raise DeviceLockError("unlock_cancelled")
                now = self._now()
                if not issue_ticket:
                    return True
                if len(self._pending) >= 256:
                    raise DeviceLockError("unavailable")
                token = secrets.token_urlsafe(32)
                self._pending[hashlib.sha256(token.encode("ascii")).digest()] = now + 30
                return token
        finally:
            self._verification_mutex.release()

    def lock(self):
        with self._mutex:
            self._generation += 1
            self._access_epoch = secrets.token_hex(16)
            self._sessions.clear()
            self._pending.clear()
            self._presence.clear()
            return {"locked": True}

    def authorized(self, token):
        return self._token_access(token, action="authorize")

    def pending(self, token):
        return self._token_access(token, action="pending")

    def activate(self, token):
        """Acknowledge possession, replacing all other tickets and sessions.

        Repeated activation is idempotent and renews neither deadline.
        """
        return self._token_access(token, action="activate")

    def presence(self, token):
        """Renew only the six-second presence lease of a still-live session."""
        return self._token_access(token, action="presence")

    def revoke(self, token):
        """Cancel an own pending ticket, or lock when the token is active."""
        return self._token_access(token, action="revoke")

    def touch(self, token):
        """Renew a live token's idle expiry, never revive an expired session."""
        return self._token_access(token, action="touch")

    def set_session_seconds(self, seconds):
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise DeviceLockError("invalid_configuration")
        with self._mutex:
            self._now()
            adjustment = seconds - self._session_seconds
            self._sessions = {digest: expiry + adjustment for digest, expiry in self._sessions.items()}
            self._session_seconds = seconds
            self._now()

    def _token_access(self, token, *, action):
        with self._mutex:
            try:
                with self._transaction():
                    self._state()
                now = self._now()
                if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
                    return False
                digest = hashlib.sha256(token.encode("ascii")).digest()
                if action in ("pending", "activate", "revoke") and digest in self._pending:
                    if action == "activate":
                        self.lock()
                        self._sessions[digest] = now + self._session_seconds
                        self._presence[digest] = now + 6
                    elif action == "revoke":
                        del self._pending[digest]
                        self._access_epoch = secrets.token_hex(16)
                    return True
                if action == "pending":
                    return False
                for stored in self._sessions:
                    if secrets.compare_digest(digest, stored):
                        if action == "touch":
                            self._sessions[stored] = now + self._session_seconds
                        elif action == "presence":
                            self._presence[stored] = now + 6
                        elif action == "revoke":
                            self.lock()
                        return True
                return False
            except DeviceLockError:
                return False

    def upstream_cleanup_confirmed(self):
        """Positive verifier evidence only; unknown custom verifiers fail closed.

        Read after admitted verification has drained, not as an activity probe.
        An uncertain verification taints this process until reconciled externally;
        a later successful login cannot clear that uncertainty.
        """
        return getattr(self._verifier, "cleanup_confirmed", None) is True

    def close(self):
        with self._mutex:
            self.lock()
            self._closed = True
            try:
                if self._db is not None:
                    self._db.close()
            finally:
                self._db = None
                for field in ("_guard_fd", "_directory_fd"):
                    descriptor = getattr(self, field)
                    if descriptor is not None:
                        os.close(descriptor)
                        setattr(self, field, None)
