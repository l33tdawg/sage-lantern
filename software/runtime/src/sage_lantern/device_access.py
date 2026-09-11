"""Fail-closed access checks for cooperating local Lantern services.

The nonsensitive root-owned /etc/sage-lantern-device-lock.enabled marker should
be provisioned mode 0644, outside the inaccessible /etc/sage-lantern private
configuration directory. Services must use these helpers rather than hardcode
marker paths; keep private configuration inaccessible to the camera service.
The marker is empty or contains exactly ``1\n``. Only a safely
observed absent marker selects legacy mode; unsafe/unreadable paths enable the
gate. Ancestors must be root-owned real directories without group/other write
permission. Root and trusted ancestors must not be maliciously replaced; these
checks do not validate ACLs or defend against root/same-UID daemon compromise.
Only the fixed default path permits macOS's root-owned /etc -> /private/etc
platform alias, selected at module construction. Canonical parents and marker
are still checked on each call; injected paths never receive alias resolution.

The daemon must return exactly {"authorized": bool} or {"locked": bool}.
POST /authorize sends application/json {"token": string}, without an X-auth
header. The daemon must accept this local helper contract, not browser headers.
State is for hardware admission/cancellation, NEVER browser/history authority.
Callers must recheck ongoing work; this module neither cancels work nor unloads
vault keys. Injection arguments are trusted construction/test inputs, not HTTP
parameters. No credential, result or marker state is cached.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path


def _default_marker() -> Path:
    default = Path("/etc/sage-lantern-device-lock.enabled")
    if sys.platform != "darwin":
        return default
    try:
        root = Path("/").lstat()
        alias = Path("/etc").lstat()
        if (stat.S_ISDIR(root.st_mode) and root.st_uid == 0
                and not root.st_mode & 0o022 and stat.S_ISLNK(alias.st_mode)
                and alias.st_uid == 0
                and os.readlink("/etc") in ("private/etc", "/private/etc")):
            return Path("/private/etc/sage-lantern-device-lock.enabled")
    except OSError:
        pass
    return default


MARKER = _default_marker()
TIMEOUT = 0.5
MAX_RESPONSE_BYTES = 256
MAX_TOKEN_BYTES = 4096
Transport = Callable[[str, str, bytes | None], tuple[int, bytes]]


def _marker_state(marker: Path) -> bool | None:
    descriptor = None
    try:
        if not isinstance(marker, Path) or not marker.is_absolute() or ".." in marker.parts:
            return None
        for directory in (*reversed(marker.parents), marker):
            try:
                metadata = directory.lstat()
            except FileNotFoundError:
                return False
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                return None
            if directory != marker:
                if not stat.S_ISDIR(metadata.st_mode):
                    return None
            elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                return None
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            return None
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != 0
                or opened.st_mode & 0o022 or opened.st_nlink != 1):
            return None
        return True if os.read(descriptor, 3) in (b"", b"1\n") else None
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def lock_enabled(*, marker: Path = MARKER) -> bool:
    """Unsafe marker counts as enabled, but all access checks will deny."""
    return _marker_state(marker) is not False


def _transport(method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", 3196, timeout=TIMEOUT)
    deadline = time.monotonic() + TIMEOUT
    try:
        connection.connect()
        socket = connection.sock
        socket.settimeout(max(0.001, deadline - time.monotonic()))
        connection.request(method, path, body=body, headers={
            "Content-Type": "application/json", "Connection": "close",
        })
        socket.settimeout(max(0.001, deadline - time.monotonic()))
        response = connection.getresponse()
        if path == "/access-epoch":
            if (response.headers.get_all("Content-Type") != ["application/json"]
                    or response.headers.get_all("Content-Encoding")
                    or response.headers.get_all("Cache-Control") != ["no-store"]):
                raise ValueError("access unavailable")
        if response.status != 200 or response.getheader("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise ValueError("access unavailable")
        chunks = bytearray()
        while len(chunks) <= MAX_RESPONSE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("access unavailable")
            socket.settimeout(remaining)
            chunk = response.read1(MAX_RESPONSE_BYTES + 1 - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
            if response.isclosed() is True:
                break
        if time.monotonic() > deadline:
            raise TimeoutError("access unavailable")
        return response.status, bytes(chunks)
    finally:
        connection.close()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("access unavailable")
        result[name] = value
    return result


def _check(method: str, path: str, body: bytes | None, field: str,
           expected: bool, transport: Transport) -> bool:
    try:
        status, raw = transport(method, path, body)
        if type(status) is not int or status != 200 or type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
            return False
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        return type(value) is dict and set(value) == {field} and value[field] is expected
    except Exception:
        return False


def authorized(token: str, *, marker: Path = MARKER,
               transport: Transport = _transport) -> bool:
    """Authorize a browser capability; daemon/parse failures deny access."""
    state = _marker_state(marker)
    if state is False:
        return True
    if state is None:
        return False
    if type(token) is not str or not token or len(token) > MAX_TOKEN_BYTES:
        return False
    try:
        if len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
            return False
        body = json.dumps({"token": token}, separators=(",", ":")).encode("utf-8")
    except (ValueError, UnicodeError):
        return False
    return _check("POST", "/authorize", body, "authorized", True, transport)


def device_unlocked(*, marker: Path = MARKER,
                    transport: Transport = _transport) -> bool:
    """Hardware-only admission check; not authorization to read user content."""
    state = _marker_state(marker)
    if state is False:
        return True
    if state is None:
        return False
    return _check("GET", "/state", None, "locked", False, transport)


def access_epoch(*, marker: Path = MARKER, transport: Transport = _transport) -> str | None:
    """Protected-only lifecycle snapshot, not browser/token authorization.

    Require a safely enabled marker before and after transport. Unlike legacy
    helpers, an absent marker never grants access. No observation is cached.
    """
    try:
        if _marker_state(marker) is not True:
            return None
        status, raw = transport("GET", "/access-epoch", None)
        if type(status) is not int or status != 200 or type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if (type(value) is not dict or set(value) != {"schema", "locked", "epoch"}
                or value["schema"] != "sage.lantern.access-epoch.v1" or value["locked"] is not False
                or type(value["epoch"]) is not str or re.fullmatch(r"[0-9a-f]{32}", value["epoch"]) is None
                or _marker_state(marker) is not True):
            return None
        return value["epoch"]
    except Exception:
        return None


def device_locked(*, marker: Path = MARKER, transport: Transport = _transport) -> bool:
    """True only when the broker explicitly reports the locked state.

    Fail-closed: an absent marker, transport failure or unexpected payload is
    never treated as locked. Callers use this for behaviour that is allowed
    while locked (the periodic, location-free radio beacon), never as an
    authorization input.
    """
    try:
        if _marker_state(marker) is not True:
            return False
        status, raw = transport("GET", "/access-epoch", None)
        if type(status) is not int or status != 200 or type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
            return False
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if (type(value) is not dict or set(value) != {"schema", "locked", "epoch"}
                or value["schema"] != "sage.lantern.access-epoch.v1"
                or type(value["locked"]) is not bool
                or type(value["epoch"]) is not str or re.fullmatch(r"[0-9a-f]{32}", value["epoch"]) is None
                or _marker_state(marker) is not True):
            return False
        return value["locked"] is True
    except Exception:
        return False
