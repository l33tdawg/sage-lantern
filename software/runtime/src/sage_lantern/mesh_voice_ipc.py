"""Fixed root mesh IPC facade. No browser socket, keys, paths or radio commands.

The kernel peer must be root. Root binds the other end to the exact supervised
voice process. Guard challenges keep the original HTTP authorization alive;
device access epochs independently fence lock/reunlock. No automatic retries.
"""

from dataclasses import asdict, fields
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import threading
import time
import uuid

from .device_access import access_epoch
from .mesh_contacts import VerifiedMeshContact, SCHEMA as CONTACT_SCHEMA, _validate as validate_contact
from .mesh_mailbox import MailboxItem, MailboxPage
from .message_contacts import RecipientID
from .suspend_io import unique_object


SOCKET = Path("/run/sage-lantern-mesh-control/broker.sock")
CONFIG = Path("/etc/sage-lantern/mesh-runtime.json")
MAX_FRAME = 65536


class MeshIPCError(RuntimeError):
    def __init__(self):
        super().__init__("mesh_ipc_unavailable")


def require(value):
    if value is not True:
        raise MeshIPCError()


def receive(connection, limit=MAX_FRAME):
    header = bytearray()
    while len(header) < 4:
        chunk = connection.recv(4 - len(header))
        require(bool(chunk))
        header.extend(chunk)
    length = int.from_bytes(header, "big")
    require(0 < length <= limit)
    raw = bytearray()
    while len(raw) < length:
        chunk = connection.recv(length - len(raw))
        require(bool(chunk))
        raw.extend(chunk)
    result = json.loads(raw, object_pairs_hook=unique_object,
                        parse_constant=lambda value: (_ for _ in ()).throw(MeshIPCError()))
    require(type(result) is dict)
    return result


def send(connection, value):
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    require(len(raw) <= MAX_FRAME)
    connection.sendall(len(raw).to_bytes(4, "big") + raw)


def peer(connection):
    require(hasattr(socket, "SO_PEERCRED"))
    pid, uid, group = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return pid, uid


def connect():
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(5)
        connection.connect(str(SOCKET))
        require(peer(connection)[1] == 0)
        return connection
    except Exception:
        connection.close()
        raise MeshIPCError() from None


def managed():
    if not os.path.lexists(CONFIG):
        return False
    from .mesh_daemon import load_config
    load_config()
    return True


def root_control(operation, transition_id):
    require(os.geteuid() == 0 and operation in {"drain", "resume", "close-vault"}
            and type(transition_id) is str and str(uuid.UUID(transition_id)) == transition_id)
    connection = connect()
    try:
        connection.settimeout(65)
        send(connection, {"op": operation, "transition_id": transition_id})
        response = receive(connection, 1024)
        require(set(response) == {"result"})
        value = response["result"]
        if operation == "drain":
            require(type(value) is dict and set(value) == {"broker_id", "transition_id", "vault_epoch"}
                    and value["transition_id"] == transition_id
                    and type(value["broker_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["broker_id"]) is not None
                    and type(value["vault_epoch"]) is str and re.fullmatch(r"[0-9a-f]{64}:[1-9][0-9]{0,19}", value["vault_epoch"]) is not None
                    and int(value["vault_epoch"].split(":")[1]) <= 2**64-1)
        else:
            require(value is True)
        return value
    finally:
        connection.close()


def card(value):
    require(type(value) is dict and set(value) == {field.name for field in fields(VerifiedMeshContact)})
    return validate_contact({"schema": CONTACT_SCHEMA, **value}, time.time())


def item(value):
    require(type(value) is dict and set(value) == {field.name for field in fields(MailboxItem)})
    value = dict(value)
    for name in ("sender", "recipient"):
        require(type(value[name]) is dict and set(value[name]) == {"chain_id", "agent_id"})
        value[name] = RecipientID(**value[name])
    require(value["box"] in ("inbox", "outbox"))
    require(type(value["id"]) is str and re.fullmatch(r"mesh:(inbox|outbox):[0-9a-f-]{36}:[0-9a-f]{64}", value["id"]) is not None)
    for name in ("local_id", "message_id"):
        require(type(value[name]) is str and str(uuid.UUID(value[name])) == value[name] and uuid.UUID(value[name]).int != 0)
    require(type(value["text"]) is str and len(value["text"].encode()) <= 512)
    require(type(value["created_at"]) is str and len(value["created_at"]) <= 40)
    require(datetime.fromisoformat(value["created_at"].replace("Z", "+00:00")).tzinfo is not None)
    require((value["state"], value["workflow_state"]) in {
        ("unknown", "received"), ("queued", "queued"), ("unknown", "dispatching"),
        ("sent", "awaiting_receipt"), ("delivered", "delivered"), ("read", "read"),
        ("cancelled", "cancelled")})
    return MailboxItem(**value)


class MeshVoiceFacade:
    def __init__(self, *, connector=connect, epoch_reader=access_epoch):
        self._connector, self._epoch = connector, epoch_reader
        self._broker = None
        self._actor = None
        self._mutex = threading.Lock()

    @contextmanager
    def _serialized(self, timeout):
        deadline = time.monotonic() + timeout
        require(self._mutex.acquire(timeout=timeout))
        try:
            yield deadline
        finally:
            self._mutex.release()

    def _call(self, method, arguments, access_check=None, on_submit=None, timeout=60):
        with self._serialized(timeout) as deadline:
            epoch = self._epoch() if method != "close" else None
            if method != "close":
                require(type(epoch) is str and re.fullmatch(r"[0-9a-f]{32}", epoch) is not None)
                require(access_check() is True)
            connection = self._connector()
            try:
                require(time.monotonic() < deadline)
                connection.settimeout(min(5, deadline-time.monotonic()))
                send(connection, {"op": "bind"})
                bound = receive(connection)
                require(set(bound) == {"broker_id", "actor"} and type(bound["broker_id"]) is str
                        and re.fullmatch(r"[0-9a-f]{32}", bound["broker_id"]) is not None)
                require(type(bound["actor"]) is dict and set(bound["actor"]) == {"chain_id", "agent_id"})
                actor = RecipientID(**bound["actor"])
                if self._broker is None:
                    self._broker = bound["broker_id"]
                    self._actor = actor
                require(self._broker == bound["broker_id"] and self._actor == actor)
                if on_submit is not None:
                    require(access_check() is True and self._epoch() == epoch)
                    on_submit()
                send(connection, dict(op="mesh", broker_id=self._broker, epoch=epoch, method=method, args=arguments))
                while True:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0)
                    # A request can wait behind a whole background radio job.
                    # Silence while queued is not loss of authorization: the
                    # daemon challenges the live session again before acting.
                    # Match its 45-second request bound instead of abandoning
                    # an otherwise valid request after five quiet seconds.
                    connection.settimeout(remaining if method == "close" else min(45, remaining))
                    response = receive(connection)
                    if set(response) == {"guard"}:
                        require(method != "close" and type(response["guard"]) is str
                                and re.fullmatch(r"[0-9a-f]{32}", response["guard"]) is not None)
                        allowed = access_check() is True and self._epoch() == epoch
                        send(connection, {"guard": response["guard"], "allowed": allowed})
                        require(allowed)
                        continue
                    require(set(response) == {"result"})
                    if method != "close":
                        require(access_check() is True and self._epoch() == epoch)
                    return response["result"]
            except Exception:
                raise MeshIPCError() from None
            finally:
                connection.close()

    def ready(self, *, access_check):
        return self._call("ready", {}, access_check) is True

    def radio_status(self, *, access_check):
        from .mesh_radio_events import validate_status
        return validate_status(self._call('radio_status', {}, access_check, timeout=30))

    def custody_status(self, *, access_check):
        from .mesh_custodian import validate_status
        return validate_status(self._call('custody_status', {}, access_check))

    def contacts(self, *, access_check):
        result = self._call("contacts", {}, access_check)
        require(type(result) is list and len(result) <= 21)
        return tuple(card(value) for value in result)

    def contact_id(self, contact):
        require(type(contact) is VerifiedMeshContact and type(self._actor) is RecipientID)
        binding = {"owner": asdict(self._actor), "recipient": asdict(contact.recipient)}
        return "mesh-contact:" + hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def list_messages(self, box, *, access_check):
        result = self._call("list_messages", {"box": box}, access_check)
        require(type(result) is dict and set(result) == {"items", "more"}
                and type(result["items"]) is list and len(result["items"]) <= 20 and type(result["more"]) is bool)
        return MailboxPage(tuple(item(value) for value in result["items"]), result["more"])

    def open_message(self, identifier, *, access_check):
        return item(self._call("open_message", {"id": identifier}, access_check))

    def queue(self, contact, *, text, request_id, approval_binding, access_check, on_submit):
        require(type(contact) is VerifiedMeshContact)
        return item(self._call("queue", dict(card=asdict(contact), text=text, request_id=request_id,
                                            approval_binding=approval_binding), access_check, on_submit))

    def close(self, timeout):
        try:
            require(type(timeout) in (int, float) and 0 < timeout <= 60)
            return self._call("close", {}, timeout=timeout) is True
        except Exception:
            return False

    def public_begin(self, proposal, *, request_id, receipt=None, access_check, on_submit):
        from .public_mesh_protocol import proposal_data
        from .community_public import PublicReceipt
        from .mesh_public_root import progress
        require(receipt is None or type(receipt) is PublicReceipt)
        response = self._call("public_begin", dict(proposal=proposal_data(proposal), request_id=request_id,
            receipt=None if receipt is None else asdict(receipt)), access_check, on_submit)
        value = progress(response)
        require(value.request_id == request_id)
        return value

    def public_inspect(self, request_id, *, access_check):
        from .mesh_public_root import progress, observation
        response = self._call("public_inspect", {"request_id": request_id}, access_check)
        require(type(response) is dict and set(response) == {"progress", "result"})
        value = progress(response["progress"])
        require(value.request_id == request_id)
        return value, observation(response["result"])

    def render_challenge(self, identifier, *, rendered, access_check):
        from .mesh_read_receipts import identifier as valid_uuid
        require(rendered is True)
        response = self._call("render_challenge", {"id": identifier, "rendered": rendered}, access_check)
        require(type(response) is dict and set(response) == {"id", "challenge_id", "expires_at"}
                and response["id"] == identifier and type(response["expires_at"]) is int
                and int(time.time()) < response["expires_at"] <= int(time.time()) + 60)
        valid_uuid(response["challenge_id"])
        return response

    def rendered(self, identifier, *, challenge_id, rendered, share_read_receipt, access_check, on_submit):
        from .mesh_read_receipts import identifier as valid_uuid
        require(rendered is True and share_read_receipt is True)
        valid_uuid(challenge_id)
        response = self._call("rendered", dict(id=identifier, challenge_id=challenge_id,
            rendered=rendered, share_read_receipt=share_read_receipt), access_check, on_submit)
        require(type(response) is dict and set(response) == {"id", "receipt_id", "state"}
                and response["id"] == identifier and response["state"] == "queued")
        valid_uuid(response["receipt_id"])
        return response
