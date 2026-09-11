"""Bounded metadata-only IO for the privileged sleep controller."""

import http.client
import json
import os
from pathlib import Path
import stat
import time
import uuid


FENCE = Path("/run/sage-lantern-sleep.fenced")
STATUS = Path("/run/sage-lantern-sleep-status.json")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


def read_json(path, *, owner_uid, mode=0o600, limit=4096):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner_uid
                or stat.S_IMODE(metadata.st_mode) != mode or metadata.st_nlink != 1
                or metadata.st_size > limit or Path(path).resolve() != Path(path)):
            raise ValueError("unsafe_receipt")
        raw = os.read(descriptor, limit + 1)
        if len(raw) > limit:
            raise ValueError("oversized_receipt")
        return json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid_number")))
    finally:
        os.close(descriptor)


def publish(path, value, *, mode=0o600, owner_uid=0):
    if os.geteuid() != owner_uid or path.parent.resolve() != path.parent:
        raise ValueError("unsafe_output")
    parent = path.parent.stat()
    if parent.st_uid != owner_uid or parent.st_mode & 0o022:
        raise ValueError("unsafe_output")
    if path.exists() or path.is_symlink():
        read_json(path, owner_uid=owner_uid, mode=mode)
    temporary = path.with_name(".sleep-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def public_json(port, path, *, timeout=1.0, limit=32768):
    if (port, path) not in {(3196, "/state"), (3196, "/power-policy"), (3196, "/sleep-ready"), (8081, "/slots")}:
        raise ValueError("unsupported_public_endpoint")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        connection.connect()
        transport = connection.sock
        connection.request("GET", path, headers={"Connection": "close"})
        transport.settimeout(max(0.001, deadline - time.monotonic()))
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Type", "").split(";")[0].strip() != "application/json":
            raise ValueError("metadata_unavailable")
        body = bytearray()
        while len(body) <= limit:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("metadata_timeout")
            transport.settimeout(remaining)
            chunk = response.read1(limit + 1 - len(body))
            if not chunk:
                break
            body.extend(chunk)
            if response.isclosed():
                break
        if len(body) > limit or time.monotonic() > deadline:
            raise ValueError("metadata_unavailable")
        return json.loads(body, object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid_number")))
    finally:
        connection.close()


def model_idle(value):
    return (type(value) is list and 1 <= len(value) <= 16
            and all(type(slot) is dict and type(slot.get("id")) is int
                    and slot.get("is_processing") is False for slot in value)
            and len({slot["id"] for slot in value}) == len(value))
