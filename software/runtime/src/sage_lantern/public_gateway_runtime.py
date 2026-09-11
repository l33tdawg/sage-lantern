"""Gateway LP1/LC1 traffic on an existing root SerialScheduler, never a USB opener.

The root demultiplexer passes canonically decoded, durably captured contact
frames here and retains its capture until the returned Future succeeds. There
is no receive loop or private mailbox scan. Every whole operation runs on the
same scheduler as other radio producers; callers must not wait from its thread.
Pins and contact_check are trusted operator policy, not radio-prefix evidence.
Encrypted envelopes authenticate the ordinary public author after reassembly.

Eight exact protected slots retain fragments, forwarding intent and sealed
responses. No automatic retry follows forwarding/serial uncertainty, including
after restart. Close fences this producer and joins its work, but never closes
or releases the shared scheduler. The storage lifecycle lease must outlive that
join. Queued/serial_accepted are not delivery, consensus or cache authority.
Version-two signatures bind traffic kind; legacy slots fail closed, not migrate.
"""

import base64
import copy
from dataclasses import dataclass
import hashlib
import math
import threading
import time
import uuid

from .mesh_fragments import decode_fragment, MAX_MESSAGE_BYTES, CHUNK_BYTES
from .community_public import PublicProposal
from .mesh_serial_owner import SerialScheduler
from .meshcore_transport import IncomingMessage, MeshCoreClient, decode_incoming
from .public_gateway_bridge import PublicGatewayBridge
from .public_mesh_protocol import canonical, open_request, public_key, require, timestamp, response_fragment_bound
from .public_mesh_provider import PublicMeshTiming, public_fragments
from .public_mesh_provider import PublicMeshPins
from .gateway_contact import respond_contact, fragments as contact_fragments, _open as open_contact
from .workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown


SCHEMA = "sage.lantern.public-gateway-runtime.v2"
SIGN_DOMAIN = SCHEMA.encode() + b"\x00"
NAMESPACE = uuid.UUID("be726501-0718-4683-8221-ccbd8bed9950")
SLOTS = 8
PER_PEER = 2


@dataclass(frozen=True)
class PublicGatewayPin:
    radio_key: bytes
    author: str


@dataclass(frozen=True)
class PublicGatewayProgress:
    slot_id: str
    state: str
    next_fragment: int
    fragment_count: int


def _decode(value):
    require(type(value) is str and len(value) <= 4 * MAX_MESSAGE_BYTES)
    raw = base64.b64decode(value, validate=True)
    require(base64.b64encode(raw).decode() == value)
    return raw


class PublicGatewayRuntime:
    def __init__(self, bridge, scheduler, pins, *, contact_check, lifecycle_check, timing):
        require(type(bridge) is PublicGatewayBridge and type(scheduler) is SerialScheduler)
        require(type(pins) is tuple and 1 <= len(pins) <= 32 and type(timing) is PublicMeshTiming)
        require(callable(contact_check) and callable(lifecycle_check))
        timing.validate()
        for pin in pins:
            require(type(pin) is PublicGatewayPin and type(pin.radio_key) is bytes and len(pin.radio_key) == 32)
            public_key(pin.radio_key.hex())
            public_key(pin.author)
            require(pin.author in bridge._authors)
        require(len({pin.radio_key[:6] for pin in pins}) == len(pins)
                and len({pin.author for pin in pins}) == len(pins))
        self.bridge, self.scheduler, self.pins = bridge, scheduler, pins
        self._contact, self._lifecycle, self._timing = contact_check, lifecycle_check, timing
        self._binding = dict(bridge=bridge._binding,
            pins=[dict(radio=pin.radio_key.hex(), author=pin.author) for pin in sorted(pins, key=lambda pin: pin.radio_key)])
        self._ids = tuple(str(uuid.uuid5(NAMESPACE, canonical([bridge._binding, index]).decode())) for index in range(SLOTS))
        self._condition = threading.Condition()
        self._pending = 0
        self._closing = self._uncertain = False
        self._last = None

    def _now(self):
        now = timestamp(self.bridge._clock())
        require(self._last is None or now >= self._last)
        self._last = now
        return now

    def _storage(self):
        return self._lifecycle() is True and self.bridge._storage() is True

    def _check(self, access_check, pin=None):
        require(not self._closing and not self._uncertain and self._storage() is True
                and access_check() is True and self.bridge._access() is True)
        if pin is not None:
            require(pin in self.pins and self._contact(pin) is True)
        require(access_check() is True)
        return True

    def _enqueue(self, operation, access_check):
        require(callable(access_check))
        with self._condition:
            require(not self._closing and not self._uncertain and self._pending < SLOTS)
            self._pending += 1
        def finished(future):
            with self._condition:
                self._pending -= 1
                self._condition.notify_all()
        def run(device):
            self._check(access_check)
            require(type(device) is MeshCoreClient)
            return operation(device)
        try:
            future = self.scheduler.submit(run)
        except Exception:
            finished(None)
            raise
        future.add_done_callback(finished)
        return future

    def _load(self, slot):
        require(slot in self._ids)
        try:
            row = self.bridge._workflow.get(slot, kind="public_proposal", access_check=self._storage)
        except WorkflowNotFound:
            return None
        try:
            proof = row["payload"]
            require(type(proof) is dict and set(proof) == {"body", "signature"})
            body = proof["body"]
            require(type(body) is dict and set(body) == {"schema", "binding", "slot", "revision", "data"}
                    and body["schema"] == SCHEMA and body["binding"] == self._binding
                    and body["slot"] == slot and body["revision"] == row["revision"])
            require(type(proof["signature"]) is str and len(proof["signature"]) == 128)
            self.bridge._key.verify_key.verify(SIGN_DOMAIN + canonical(body), bytes.fromhex(proof["signature"]))
            self._validate(body["data"])
            return body
        except Exception:
            self._uncertain = True
            raise

    def _validate(self, data):
        require(type(data) is dict and set(data) == {"traffic", "radio", "author", "digest", "count", "size", "parts",
            "expires_at", "state", "response", "next"})
        require(data["traffic"] in {"LP1", "LC1"})
        require(any(data["radio"] == pin.radio_key.hex() and data["author"] == pin.author for pin in self.pins))
        public_key(data["digest"])
        timestamp(data["expires_at"])
        require(type(data["size"]) is int and 1 <= data["size"] <= MAX_MESSAGE_BYTES
                and type(data["count"]) is int and data["count"] == math.ceil(data["size"] / CHUNK_BYTES)
                and type(data["parts"]) is dict and len(data["parts"]) <= data["count"])
        for index, value in data["parts"].items():
            require(type(index) is str and index.isascii() and index.isdecimal()
                    and str(int(index)) == index and 0 <= int(index) < data["count"])
            require(len(_decode(value)) == min(CHUNK_BYTES, data["size"] - int(index) * CHUNK_BYTES))
        require(data["state"] in {"receiving", "forwarding", "queued", "dispatching", "sent"})
        require(type(data["next"]) is int and data["next"] >= 0)
        if data["state"] in {"receiving", "forwarding"}:
            require(data["response"] is None and data["next"] == 0)
        else:
            wire = _decode(data["response"])
            require(1 <= len(wire) <= MAX_MESSAGE_BYTES)
            count = len(self._frames(data))
            require(data["next"] <= count and (data["state"] == "sent") == (data["next"] == count))
        if data["state"] != "receiving":
            require(len(data["parts"]) == data["count"])
            self._wire(data)

    @staticmethod
    def _wire(data):
        wire = b"".join(_decode(data["parts"][str(index)]) for index in range(data["count"]))
        require(len(wire) == data["size"] and hashlib.sha256(wire).hexdigest() == data["digest"])
        return wire

    @staticmethod
    def _frames(data):
        return (contact_fragments if data["traffic"] == "LC1" else public_fragments)(_decode(data["response"]))

    def _save(self, slot, previous, data):
        self._validate(data)
        revision = 0 if previous is None else previous["revision"]
        body = dict(schema=SCHEMA, binding=self._binding, slot=slot, revision=revision + 1, data=data)
        proof = dict(body=body, signature=self.bridge._key.sign(SIGN_DOMAIN + canonical(body)).signature.hex())
        try:
            self.bridge._workflow.put(slot, "public_proposal", revision, proof, access_check=self._storage)
        except WorkflowOutcomeUnknown:
            self._uncertain = True
            raise
        return body

    def _progress(self, row):
        data = row["data"]
        state = data["state"]
        if state in {"forwarding", "dispatching"}:
            state = "unknown"
        elif data["expires_at"] <= self._now() and state != "sent":
            state = "expired"
        elif state == "sent":
            state = "serial_accepted"
        count = 0 if data["response"] is None else len(self._frames(data))
        return PublicGatewayProgress(row["slot"], state, data["next"], count)

    def ingest_frame(self, message, *, access_check):
        """Future[progress | None]; unrelated traffic is not consumed or indexed."""
        return self._enqueue(lambda device: self._ingest_message(device, message, access_check), access_check)

    def ingest_on_owner(self, device, message, *, access_check):
        """Synchronous MeshReceiver consumer; only on the borrowed owner thread."""
        return self._on_owner(lambda device: self._ingest_message(device, message, access_check), device, access_check)

    def _on_owner(self, operation, device, access_check):
        require(threading.current_thread() is self.scheduler._thread and device is self.scheduler._client)
        with self._condition:
            require(not self._closing and not self._uncertain and self._pending < SLOTS)
            self._pending += 1
        try:
            self._check(access_check)
            return operation(device)
        finally:
            with self._condition:
                self._pending -= 1
                self._condition.notify_all()

    def _ingest_message(self, device, message, access_check):
        require(type(message) is IncomingMessage and decode_incoming(message.raw_frame) == message)
        if message.kind != "contact" or message.text_type != 0 or not message.payload.startswith((b"LP1:", b"LC1:")):
            return None
        fragment = decode_fragment("SL1:" + message.payload[4:].decode("ascii"))
        matches = [pin for pin in self.pins if pin.radio_key[:6] == message.sender_prefix]
        require(len(matches) == 1)
        pin = matches[0]
        return self._ingest(device, pin, fragment, access_check, message.payload[:3].decode("ascii"))

    def _ingest(self, device, pin, fragment, access_check, traffic):
        self._check(access_check, pin)
        device.get_contacts(authorize=lambda: self._check(access_check, pin)).unique_prefix(pin.radio_key)
        now = self._now()
        rows = [(slot, self._load(slot)) for slot in self._ids]
        selected = next(((slot, row) for slot, row in rows if row is not None
            and row["data"]["radio"] == pin.radio_key.hex() and row["data"]["digest"] == fragment.digest.hex()
            and row["data"]["traffic"] == traffic), None)
        if selected is None:
            occupied = lambda row: row is not None and (row["data"]["expires_at"] > now
                or row["data"]["state"] in {"forwarding", "dispatching"})
            require(sum(occupied(row) and row["data"]["radio"] == pin.radio_key.hex() for slot, row in rows) < PER_PEER)
            selected = next(((slot, row) for slot, row in rows if not occupied(row)), None)
            require(selected is not None)
            slot, row = selected
            data = dict(traffic=traffic, radio=pin.radio_key.hex(), author=pin.author, digest=fragment.digest.hex(), count=fragment.count,
                size=fragment.size, parts={}, expires_at=now + 120, state="receiving", response=None, next=0)
        else:
            slot, row = selected
            data = row["data"]
            require((data["count"], data["size"]) == (fragment.count, fragment.size))
            if data["state"] != "receiving" or data["expires_at"] <= now:
                return self._progress(row)
        index, part = str(fragment.index), base64.b64encode(fragment.payload).decode()
        if index in data["parts"]:
            require(data["parts"][index] == part)
        else:
            self._check(access_check, pin)
            row = self._save(slot, row, dict(data, parts={**data["parts"], index: part}))
            data = row["data"]
        if len(data["parts"]) != data["count"]:
            return self._progress(row)
        wire = self._wire(data)
        if traffic == "LC1":
            pins = PublicMeshPins(bytes(self.bridge._key.verify_key).hex(), device._key, self.bridge._chain, self.bridge._domain)
            self._check(access_check, pin)
            response = respond_contact(wire, self.bridge._key, pins=pins, author=pin.author,
                allowed_authors=self.bridge._authors, now=self._now())
            body = open_contact(wire, pin.author)
            require(len(contact_fragments(response)) * self._timing.frame_seconds + self._timing.safety_seconds
                    < body["expires_at"] - self._now(), "public_mesh_budget_exceeded")
            row = self._save(slot, row, dict(data, state="queued", expires_at=body["expires_at"],
                response=base64.b64encode(response).decode()))
            return self._progress(row)
        request = open_request(wire, self.bridge._key, author=pin.author,
            chain_id=self.bridge._chain, domain=self.bridge._domain, now=self._now())
        response_bound = response_fragment_bound(PublicProposal(**request["proposal"]),
            gateway_key=bytes(self.bridge._key.verify_key).hex())
        require(self._timing.seconds(0, response_bound) < request["expires_at"] - self._now(), "public_mesh_budget_exceeded")
        self._check(access_check, pin)
        row = self._save(slot, row, dict(data, expires_at=request["expires_at"], state="forwarding"))
        self._check(access_check, pin)
        guarded_bridge = copy.copy(self.bridge)
        guarded_bridge._access = lambda: self._check(access_check, pin)
        response = guarded_bridge.handle(wire, author=pin.author)
        require(type(response) is bytes and 1 <= len(response) <= MAX_MESSAGE_BYTES)
        require(len(public_fragments(response)) <= response_bound, "public_mesh_oversize")
        row = self._save(slot, row, dict(row["data"], state="queued", response=base64.b64encode(response).decode()))
        return self._progress(row)

    def dispatch_one(self, slot_id, *, access_check):
        """Future[progress]; one LP1 frame, with durable intent before USB."""
        return self._enqueue(self._dispatch_operation(slot_id, access_check), access_check)

    def dispatch_on_owner(self, device, slot_id, *, access_check):
        return self._on_owner(self._dispatch_operation(slot_id, access_check), device, access_check)

    def _dispatch_operation(self, slot_id, access_check):
        require(slot_id in self._ids)
        def dispatch(device):
            row = self._load(slot_id)
            require(row is not None and row["data"]["state"] == "queued")
            data = row["data"]
            pin = next(pin for pin in self.pins if pin.radio_key.hex() == data["radio"] and pin.author == data["author"])
            frames = self._frames(data)
            def check():
                self._check(access_check, pin)
                require((len(frames) - data["next"]) * self._timing.frame_seconds + self._timing.safety_seconds
                        < data["expires_at"] - self._now(), "public_mesh_budget_exceeded")
                return True
            check()
            prefix = device.get_contacts(authorize=check).unique_prefix(pin.radio_key)
            check()
            row = self._save(slot_id, row, dict(data, state="dispatching"))
            result = device.send_text(recipient_prefix=prefix, text=frames[data["next"]], timestamp=self._now(), authorize=check)
            require(result.serial_accepted is True and result.delivered is False and result.read is False)
            index = data["next"] + 1
            row = self._save(slot_id, row, dict(data, next=index, state="sent" if index == len(frames) else "queued"))
            return self._progress(row)
        return dispatch

    def inspect(self, *, access_check):
        """Future[tuple[progress, ...]]; exact eight slots, never a private scan."""
        return self._enqueue(lambda device: self._inspect(access_check), access_check)

    def inspect_on_owner(self, device, *, access_check):
        return self._on_owner(lambda device: self._inspect(access_check), device, access_check)

    def _inspect(self, access_check):
        rows = [self._load(slot) for slot in self._ids]
        self._check(access_check)
        return tuple(self._progress(row) for row in rows if row is not None)

    def close(self, timeout=0):
        """Fence/join this producer only. False keeps shared ownership required."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 60:
            return False
        deadline = time.monotonic() + timeout
        with self._condition:
            self._closing = True
            if not self._condition.wait_for(lambda: self._pending == 0, timeout) or self._uncertain:
                return False
        def drain(device):
            require(self._storage() is True)
            rows = [self._load(slot) for slot in self._ids]
            require(self._storage() is True)
            return all(row is None or row["data"]["state"] not in {"forwarding", "dispatching"} for row in rows)
        try:
            return self.scheduler.submit(drain).result(timeout=max(0, deadline - time.monotonic())) is True
        except Exception:
            return False
