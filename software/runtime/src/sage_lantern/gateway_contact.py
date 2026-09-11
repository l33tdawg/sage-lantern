"""Fresh signed gateway contact over the existing MeshCore owner, not SDK auth."""

import base64
from dataclasses import asdict
import hashlib
import json
import math
import secrets
import time
import uuid

from nacl.signing import VerifyKey

from .mesh_fragments import split_envelope, decode_fragment
from .meshcore_transport import IncomingMessage, decode_incoming
from .public_mesh_protocol import OutboundRequest, canonical, identifier, proposal_data, public_key, require, parse
from .workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown


SCHEMA = "sage.lantern.gateway-contact.v1"
DOMAIN = b"SAGE-LANTERN-GATEWAY-CONTACT-v1\x00"
FRAME_PREFIX = "LC1:"
NAMESPACE = uuid.UUID("279ec130-3979-49f4-b563-ebd34eed515d")


def fragments(wire):
    return tuple(FRAME_PREFIX + item[4:] for item in split_envelope(wire))


def _wire(body, identity):
    return b"LC1." + base64.b64encode(canonical(dict(body=body,
        signature=identity.sign(DOMAIN + canonical(body)).signature.hex())))


def _open(wire, signer):
    require(type(wire) is bytes and wire.startswith(b"LC1.") and len(wire) <= 4096)
    raw = base64.b64decode(wire[4:], validate=True)
    require(base64.b64encode(raw) == wire[4:])
    proof = parse(raw)
    require(type(proof) is dict and set(proof) == {"body", "signature"} and canonical(proof) == raw)
    require(type(proof["signature"]) is str and len(proof["signature"]) == 128)
    VerifyKey(public_key(signer)).verify(DOMAIN + canonical(proof["body"]), bytes.fromhex(proof["signature"]))
    return proof["body"]


def _request(body, pins, author, now):
    require(type(body) is dict and set(body) == {"schema", "kind", "request_id", "nonce", "author", "gateway_key",
        "radio_key", "chain_id", "domain", "issued_at", "expires_at"})
    require(body["schema"] == SCHEMA and body["kind"] == "probe" and body["author"] == author
            and body["gateway_key"] == pins.gateway_key and body["radio_key"] == pins.radio_key.hex()
            and body["chain_id"] == pins.chain_id and body["domain"] == pins.domain)
    identifier(body["request_id"])
    public_key(body["nonce"])
    public_key(author)
    require(type(now) is int and type(body["issued_at"]) is int and type(body["expires_at"]) is int
            and 0 < body["issued_at"] <= now < body["expires_at"] <= body["issued_at"] + 120)


def respond_contact(wire, signing_key, *, pins, author, allowed_authors, now):
    """Gateway owner calls only on a reassembled contact probe; no REST request."""
    require(type(allowed_authors) is frozenset and author in allowed_authors
            and bytes(signing_key.verify_key).hex() == pins.gateway_key)
    body = _open(wire, author)
    _request(body, pins, author, now)
    return _wire(dict(body, kind="reply"), signing_key)


class GatewayContactProbe:
    def __init__(self, workflow, identity, pins, *, active, timing=None, clock=lambda: int(time.time()), monotonic=time.monotonic):
        self.workflow, self.identity, self.pins = workflow, identity, pins
        self._active, self._clock, self._monotonic = active, clock, monotonic
        self._binding = dict(actor=asdict(workflow.storage.actor), author=identity.agent_id,
            gateway_key=pins.gateway_key, radio_key=pins.radio_key.hex(), chain_id=pins.chain_id, domain=pins.domain)
        self._slot = str(uuid.uuid5(NAMESPACE, canonical(self._binding).decode()))
        self._current = None
        self._deadline = 0
        self._unknown = self._closed = False
        self._timing = timing
        self._probe_id, self._probe_deadline = None, 0
        self._admissions = {}
        self._last_observation = None

    def _times(self):
        wall, mono = self._clock(), self._monotonic()
        require(type(wall) is int and wall > 0 and type(mono) in (int, float) and math.isfinite(mono))
        if self._last_observation is not None:
            if wall < self._last_observation[0] or mono < self._last_observation[1]:
                self._unknown = True
                require(False)
        self._last_observation = wall, mono
        return wall, mono

    def _request_binding(self, request, pins):
        require(type(request) is OutboundRequest and pins == self.pins)
        require(dict(actor=asdict(self.workflow.storage.actor), author=self.identity.agent_id,
            gateway_key=pins.gateway_key, radio_key=pins.radio_key.hex(), chain_id=pins.chain_id,
            domain=pins.domain) == self._binding)
        identifier(request.request_id)
        public_key(request.digest)
        require(request.gateway_key == pins.gateway_key and request.proposal.author == self.identity.agent_id
                and request.proposal.chain_id == pins.chain_id and request.proposal.domain == pins.domain)
        require(type(request.issued_at) is int and type(request.expires_at) is int
                and 0 < request.issued_at < request.expires_at <= request.issued_at + 120)
        if request.record_id is not None:
            identifier(request.record_id)
        return canonical(dict(request_id=request.request_id, digest=request.digest,
            proposal=proposal_data(request.proposal), gateway_key=request.gateway_key,
            issued_at=request.issued_at, expires_at=request.expires_at, record_id=request.record_id))

    def admit(self, request, *, pins, access_check):
        """Mint memory-only authority for one exact request while contact is fresh."""
        self._check(access_check)
        binding = self._request_binding(request, pins)
        require(type(request.wire) is bytes and bool(request.wire) and self.current(pins))
        wall, mono = self._times()
        require(request.issued_at <= wall < request.expires_at)
        self._admissions = {key: value for key, value in self._admissions.items()
                            if wall < value[3] and mono < value[2]}
        require(request.request_id not in self._admissions and len(self._admissions) < 128)
        token = object()
        self._admissions[request.request_id] = (token, binding, mono + request.expires_at - wall, request.expires_at)
        self._check(access_check)
        return token

    def admitted(self, token, request, *, pins, access_check):
        """Recheck live configuration/auth, without extending contact freshness."""
        self._check(access_check)
        binding = self._request_binding(request, pins)
        wall, mono = self._times()
        saved = self._admissions.get(request.request_id)
        require(type(token) is object and saved is not None and saved[0] is token and saved[1] == binding
                and request.issued_at <= wall < saved[3] and mono < saved[2])
        self._check(access_check)
        return True

    def release(self, token, request_id):
        saved = self._admissions.get(request_id)
        if saved is not None and saved[0] is token:
            self._admissions[request_id] = (None, *saved[1:])

    def _check(self, access_check):
        require(not self._closed and not self._unknown and self._active() is True and access_check() is True)

    def _get(self, access_check):
        try:
            row = self.workflow.get(self._slot, kind="public_proposal", access_check=access_check)
        except WorkflowNotFound:
            return None
        proof = row["payload"]
        require(type(proof) is dict and set(proof) == {"body", "signature"})
        body = proof["body"]
        self.identity._signing_key.verify_key.verify(DOMAIN + canonical(body), bytes.fromhex(proof["signature"]))
        require(type(body) is dict and set(body) == {"schema", "binding", "revision", "data"}
                and body["schema"] == SCHEMA and body["binding"] == self._binding and body["revision"] == row["revision"])
        data = body["data"]
        require(type(data) is dict and set(data) == {"probe", "state", "next", "parts", "response_digest", "count", "size"}
                and data["state"] in {"queued", "dispatching", "awaiting", "verified"}
                and type(data["next"]) is int and 0 <= data["next"] <= 86 and type(data["parts"]) is dict and len(data["parts"]) <= 86)
        _request(data["probe"], self.pins, self.identity.agent_id, data["probe"]["issued_at"])
        return row

    def _put(self, revision, data, access_check):
        body = dict(schema=SCHEMA, binding=self._binding, revision=revision + 1, data=data)
        proof = dict(body=body, signature=self.identity._signing_key.sign(DOMAIN + canonical(body)).signature.hex())
        try:
            return self.workflow.put(self._slot, "public_proposal", revision, proof, access_check=access_check)
        except WorkflowOutcomeUnknown:
            self._unknown = True
            raise

    def current(self, pins):
        try:
            wall, mono = self._times()
            return (not self._closed and not self._unknown and self._active() is True and pins == self.pins
                    and self._current is not None and self._current["issued_at"] <= wall < self._current["expires_at"]
                    and mono < self._deadline)
        except Exception:
            return False

    def begin(self, *, access_check):
        self._check(access_check)
        if self.current(self.pins):
            return True
        row = self._get(access_check)
        if row is not None:
            data = row["payload"]["body"]["data"]
            require(data["state"] != "dispatching")
            if self._clock() < data["probe"]["expires_at"]:
                return False
        now = self._clock()
        probe = dict(schema=SCHEMA, kind="probe", request_id=str(uuid.uuid4()), nonce=secrets.token_hex(32),
            author=self.identity.agent_id, gateway_key=self.pins.gateway_key, radio_key=self.pins.radio_key.hex(),
            chain_id=self.pins.chain_id, domain=self.pins.domain, issued_at=now, expires_at=now + 120)
        _request(probe, self.pins, self.identity.agent_id, now)
        count = len(fragments(_wire(probe, self.identity._signing_key)))
        require(self._timing is not None and self._timing.seconds(count, count) < 120)
        self._put(0 if row is None else row["revision"], dict(probe=probe, state="queued", next=0,
            parts={}, response_digest=None, count=None, size=None), access_check)
        self._probe_id, self._probe_deadline = probe["request_id"], self._monotonic() + 120
        self._current = None
        return False

    def active(self):
        return (not self._closed and not self._unknown and self._active() is True and self._current is None
                and self._probe_id is not None and self._monotonic() < self._probe_deadline)

    def dispatch_one(self, *, device, access_check, inventory=None):
        self._check(access_check)
        row = self._get(access_check)
        if row is None:
            return False
        data = row["payload"]["body"]["data"]
        require(data["state"] != "dispatching")
        if self._probe_id != data["probe"]["request_id"] or self._monotonic() >= self._probe_deadline:
            return False
        if data["state"] != "queued" or self._clock() >= data["probe"]["expires_at"]:
            return False
        inventory = inventory or device.get_contacts(authorize=access_check)
        prefix = inventory.unique_prefix(self.pins.radio_key)
        packet = fragments(_wire(data["probe"], self.identity._signing_key))
        require(data["next"] < len(packet))
        row = self._put(row["revision"], dict(data, state="dispatching"), access_check)
        try:
            device.send_text(recipient_prefix=prefix, text=packet[data["next"]], timestamp=self._clock(), authorize=access_check)
            index = data["next"] + 1
            self._put(row["revision"], dict(data, state="awaiting" if index == len(packet) else "queued", next=index), access_check)
        except Exception:
            self._unknown = True
            raise
        return True

    def ingest_frame(self, message, *, access_check):
        self._check(access_check)
        require(type(message) is IncomingMessage and decode_incoming(message.raw_frame) == message
                and message.kind == "contact" and message.text_type == 0 and message.sender_prefix == self.pins.radio_key[:6])
        text = message.payload.decode("ascii")
        require(text.startswith(FRAME_PREFIX))
        fragment = decode_fragment("SL1:" + text[4:])
        row = self._get(access_check)
        require(row is not None)
        data = row["payload"]["body"]["data"]
        require(self._probe_id == data["probe"]["request_id"] and self._monotonic() < self._probe_deadline)
        require(data["state"] in {"awaiting", "verified"} and self._clock() < data["probe"]["expires_at"])
        if data["response_digest"] is not None:
            require((data["response_digest"], data["count"], data["size"]) == (fragment.digest.hex(), fragment.count, fragment.size))
        parts = dict(data["parts"])
        key = str(fragment.index)
        part = base64.b64encode(fragment.payload).decode()
        require(key not in parts or parts[key] == part)
        parts[key] = part
        completed = len(parts) == fragment.count
        if completed:
            wire = b"".join(base64.b64decode(parts[str(index)], validate=True) for index in range(fragment.count))
            require(len(wire) == fragment.size and hashlib.sha256(wire).digest() == fragment.digest)
            reply = _open(wire, self.pins.gateway_key)
            require(reply == dict(data["probe"], kind="reply"))
        updated = dict(data, parts=parts, response_digest=fragment.digest.hex(), count=fragment.count, size=fragment.size,
                       state="verified" if completed else data["state"])
        if updated != data:
            self._put(row["revision"], updated, access_check)
        if completed and self._current is None:
            self._current = data["probe"]
            self._deadline = self._monotonic() + min(60, data["probe"]["expires_at"] - self._clock())
        return True

    def drain(self, *, access_check):
        require(not self._unknown)
        row = self._get(access_check)
        require(row is None or row["payload"]["body"]["data"]["state"] != "dispatching")
        return True

    def close(self):
        self._closed = True
        self._current = None
        self._admissions.clear()
        return not self._unknown
