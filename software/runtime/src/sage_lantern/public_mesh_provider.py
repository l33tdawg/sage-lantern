"""Lantern-side public derivative transport; no chat, enrollment or acceptance.

Root supplies the existing dedicated public identity, protected WorkflowStore,
exact approval verifier and current gateway/radio binding verifier. Callers own
the exclusive serial scheduler and a lifecycle lease through each operation.
No worker or key loader is created here. Fresh signed REST packets live only in
memory for their short contact window; restart never regenerates/resends them.
Only request metadata, signed progress, sealed response fragments and consumed
results persist in the existing SAGE vault. Gateway results are NOT consensus.
"""

from dataclasses import dataclass
from functools import wraps
import base64
import hashlib
import math
import threading
import time
import uuid

from sage_sdk.auth import AgentIdentity

from .community_public import PublicProposal, PublicReceipt
from .gateway_contact import GatewayContactProbe
from .mesh_fragments import decode_fragment, split_envelope
from .meshcore_transport import IncomingMessage, MeshCoreClient, decode_incoming
from .meshcore_contacts import ContactInventory
from .public_mesh_protocol import (OutboundRequest, PublicMeshError, canonical, identifier, open_result,
    proposal_data, public_key, require, response_fragment_bound, result_data, seal_poll, seal_submission, timestamp, _open)
from .workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


SCHEMA = "sage.lantern.public-mesh-local.v1"
FRAME_PREFIX = "LP1:"
NAMESPACE = uuid.UUID("cfaab941-a873-4698-9eb3-cf66f15f5e30")
DOMAIN = b"sage.lantern.public-mesh-local.v1\x00"
PUBLIC_BURST = 4
PUBLIC_POLL_INTERVAL = 0.01


@dataclass(frozen=True)
class PublicMeshTiming:
    """Trusted measured per-frame bound including radio, journal and scheduling IO."""
    frame_seconds: float
    gateway_seconds: float = 30.0
    safety_seconds: float = 10.0

    def validate(self):
        require(all(type(value) in (int, float) and math.isfinite(value) for value in
                    (self.frame_seconds, self.gateway_seconds, self.safety_seconds)))
        require(0.1 <= self.frame_seconds <= 30 and 30 <= self.gateway_seconds <= 60 and 10 <= self.safety_seconds <= 30)

    def seconds(self, request_count, response_count):
        self.validate()
        pacing = 2 * PUBLIC_POLL_INTERVAL * (math.ceil(request_count / PUBLIC_BURST) + math.ceil(response_count / PUBLIC_BURST))
        return ((request_count + response_count) * self.frame_seconds + pacing
                + self.gateway_seconds + self.safety_seconds)


class PublicMeshRejected(PublicMeshError):
    """Untrusted wire rejection, distinct from protected storage uncertainty."""


@dataclass(frozen=True)
class PublicMeshPins:
    gateway_key: str
    radio_key: bytes
    chain_id: str
    domain: str


@dataclass(frozen=True)
class PublicMeshProgress:
    request_id: str
    state: str
    next_fragment: int
    fragment_count: int


def public_fragments(wire):
    return tuple(FRAME_PREFIX + fragment[4:] for fragment in split_envelope(wire))


def _exclusive(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        require(self._mutex.acquire(blocking=False))
        try:
            require(not self._closed and not self._uncertain)
            return method(self, *args, **kwargs)
        except WorkflowOutcomeUnknown:
            self._uncertain = True
            raise PublicMeshError() from None
        except PublicMeshError:
            raise
        except Exception:
            raise PublicMeshError() from None
        finally:
            self._mutex.release()
    return call


class PublicMeshProvider:
    def __init__(self, workflow, identity, pins, *, approval_check, contact_check, clock=lambda: int(time.time()), timing=None,
                 contact_admission=None):
        require(type(workflow) is WorkflowStore and isinstance(identity, AgentIdentity) and type(pins) is PublicMeshPins)
        public_key(pins.gateway_key)
        require(type(pins.radio_key) is bytes and len(pins.radio_key) == 32)
        require(type(pins.chain_id) is str and type(pins.domain) is str)
        require(all(callable(check) for check in (approval_check, contact_check, clock)))
        require(contact_admission is None or type(contact_admission) is GatewayContactProbe
                and contact_admission.workflow is workflow and contact_admission.identity is identity
                and contact_admission.pins == pins)
        self._contact_admission = contact_admission
        self._contact_tokens = {}
        require(timing is None or type(timing) is PublicMeshTiming)
        if timing is not None:
            timing.validate()
        self.timing = timing
        self.workflow, self.identity, self.pins = workflow, identity, pins
        self._approval, self._contact, self._clock = approval_check, contact_check, clock
        self._binding = dict(actor=workflow.storage.actor.agent_id, storage_chain=workflow.storage.actor.chain_id,
            author=identity.agent_id, gateway=pins.gateway_key, radio=pins.radio_key.hex(),
            chain=pins.chain_id, domain=pins.domain)
        self._mutex = threading.Lock()
        self._closed = self._uncertain = False
        self._live = {}
        self._assembly_slots = {}
        self._last = None

    def _now(self):
        now = timestamp(self._clock())
        require(self._last is None or now >= self._last)
        self._last = now
        return now

    def _check(self, access_check, proposal=None):
        require(not self._closed and not self._uncertain and access_check() is True)
        if proposal is not None:
            proposal_data(proposal)
            require((proposal.author, proposal.chain_id, proposal.domain) ==
                    (self.identity.agent_id, self.pins.chain_id, self.pins.domain))
            require(self._approval(proposal) is True)
        require(access_check() is True)
        return True

    def _id(self, kind, value):
        return str(uuid.uuid5(NAMESPACE, canonical([self._binding, kind, value]).decode()))

    def _request_contact(self, request, access_check):
        self._check(access_check, request.proposal)
        require(self._now() < request.expires_at)
        if self._contact_admission is None:
            require(self._contact(self.pins) is True)
        else:
            require(self._contact_admission.admitted(self._contact_tokens.get(request.request_id), request,
                pins=self.pins, access_check=access_check) is True)
        self._check(access_check, request.proposal)
        return True

    def _get(self, record_id, kind, access_check):
        try:
            row = self.workflow.get(record_id, kind="public_proposal", access_check=access_check)
        except WorkflowNotFound:
            return None
        proof = row["payload"]
        require(type(proof) is dict and set(proof) == {"body", "signature"})
        body = proof["body"]
        require(type(body) is dict and set(body) == {"schema", "binding", "id", "revision", "kind", "data"})
        require(body["schema"] == SCHEMA and body["binding"] == self._binding and body["id"] == record_id
                and body["revision"] == row["revision"] and body["kind"] == kind)
        require(type(proof["signature"]) is str and len(proof["signature"]) == 128)
        self.identity._signing_key.verify_key.verify(DOMAIN + canonical(body), bytes.fromhex(proof["signature"]))
        return body

    def _put(self, record_id, kind, revision, data, access_check):
        body = dict(schema=SCHEMA, binding=self._binding, id=record_id, revision=revision + 1, kind=kind, data=data)
        proof = dict(body=body, signature=self.identity._signing_key.sign(DOMAIN + canonical(body)).signature.hex())
        try:
            self.workflow.put(record_id, "public_proposal", revision, proof, access_check=access_check)
        except WorkflowOutcomeUnknown:
            self._uncertain = True
            raise
        return body

    def _request(self, row):
        data = row["data"]
        require(type(data) is dict and set(data) == {"request_id", "proposal", "digest", "issued_at", "expires_at",
            "memory_id", "state", "next", "count", "result", "response_hash"})
        proposal = PublicProposal(**data["proposal"])
        proposal_data(proposal)
        require((proposal.author, proposal.chain_id, proposal.domain) ==
                (self.identity.agent_id, self.pins.chain_id, self.pins.domain))
        require(data["state"] in {"queued", "dispatching", "awaiting_response", "consumed"})
        require(type(data["next"]) is int and type(data["count"]) is int and 0 <= data["next"] <= data["count"] <= 86
                and data["count"] > 0 and data["request_id"] == row["id"])
        require(0 < timestamp(data["expires_at"]) - timestamp(data["issued_at"]) <= 120)
        if data["memory_id"] is not None:
            identifier(data["memory_id"])
        if data["state"] == "consumed":
            public_key(data["response_hash"])
            result = data["result"]
            require(type(result) is dict and result == result_data(result["state"], receipt=result["receipt"],
                    observed_status=result["observed_status"]))
            require(all(result[key] is False for key in ("acceptance_available", "consensus_verified", "cache_eligible")))
        else:
            require(data["result"] is None and data["response_hash"] is None)
        identifier(data["request_id"])
        public_key(data["digest"])
        return OutboundRequest(b"", data["request_id"], data["digest"], proposal, self.pins.gateway_key,
            data["expires_at"], data["issued_at"], data["memory_id"])

    def _progress(self, row):
        data = row["data"]
        return PublicMeshProgress(data["request_id"], data["state"], data["next"], data["count"])

    def preflight(self, proposal, *, receipt=None):
        """Exact complete derivative limit; missing trusted timing is not readiness."""
        proposal_data(proposal)
        require(self.timing is not None, "public_mesh_timing_unavailable")
        self.timing.validate()
        auth = {"X-Agent-ID": self.identity.agent_id, "X-Signature": "f" * 128,
                "X-Timestamp": str(2**53-1), "X-Nonce": "f" * 16}
        payload = dict(schema="sage.lantern.public-mesh.v1", kind="submit" if receipt is None else "poll",
            sender=self.identity.agent_id, recipient=self.pins.gateway_key, request_id="f" * 36,
            issued_at=2**53-1, expires_at=2**53-1, proposal=proposal_data(proposal),
            record_id=None if receipt is None else receipt.memory_id, auth=auth, profile_auth=auth)
        inner_size = len(canonical(dict(payload=payload, signature="f" * 128)))
        sealed_size = 5 + 4 * math.ceil((inner_size + 48) / 3)
        require(sealed_size <= 4096, "public_mesh_oversize")
        require(self.timing.seconds(math.ceil(sealed_size / 48), self._response_count(proposal)) < 120,
                "public_mesh_budget_exceeded")

    def _response_count(self, proposal):
        return response_fragment_bound(proposal, gateway_key=self.pins.gateway_key)

    @_exclusive
    def begin(self, proposal, *, request_id, device, access_check, receipt=None):
        """Fresh contact only. Duplicate submit/UUID never regenerates HTTP auth."""
        self.preflight(proposal, receipt=receipt)
        self._check(access_check, proposal)
        identifier(request_id)
        require(type(device) is MeshCoreClient)
        require(self._contact(self.pins) is True)
        device.get_contacts(authorize=access_check).unique_prefix(self.pins.radio_key)
        check = lambda: self._check(access_check, proposal)
        require(self._get(request_id, "request", check) is None)
        if receipt is None:
            slot = self._id("submit", proposal.operation_id)
            require(self._get(slot, "submit", check) is None)
        else:
            require(type(receipt) is PublicReceipt)
        now = self._now()
        self._live = {key: value for key, value in self._live.items() if value.expires_at > now}
        if self._contact_admission is not None:
            for stale in set(self._contact_tokens) - set(self._live):
                self._contact_admission.release(self._contact_tokens.pop(stale), stale)
        require(not self._live, "public_mesh_busy")
        options = dict(request_id=request_id, now=now, access_check=check, approval_check=self._approval)
        request = (seal_submission(proposal, self.identity, self.pins.gateway_key, **options) if receipt is None else
                   seal_poll(proposal, receipt, self.identity, self.pins.gateway_key, **options))
        require(self.timing.seconds(len(public_fragments(request.wire)), self._response_count(proposal)) <
                request.expires_at - self._now(), "public_mesh_budget_exceeded")
        if self._contact_admission is not None:
            self._contact_tokens[request_id] = self._contact_admission.admit(request, pins=self.pins, access_check=check)
            check = lambda: self._request_contact(request, access_check)
        if receipt is None:
            self._put(slot, "submit", 0, dict(request_id=request_id, proposal=proposal_data(proposal)), check)
        data = dict(request_id=request_id, proposal=proposal_data(proposal), digest=request.digest,
            issued_at=request.issued_at, expires_at=request.expires_at, memory_id=request.record_id,
            state="queued", next=0, count=len(public_fragments(request.wire)), result=None, response_hash=None)
        row = self._put(request_id, "request", 0, data, check)
        self._live[request_id] = request
        return self._progress(row)

    @_exclusive
    def dispatch_one(self, request_id, *, device, access_check, inventory=None):
        identifier(request_id)
        require(type(device) is MeshCoreClient)
        row = self._get(request_id, "request", access_check)
        require(row is not None)
        request = self._request(row)
        require(request_id in self._live and self._live[request_id].digest == request.digest)
        request = self._live[request_id]
        data = row["data"]
        require(data["state"] == "queued" and self._now() < request.expires_at)
        def check():
            return self._request_contact(request, access_check)
        check()
        if inventory is None:
            inventory = device.get_contacts(authorize=check)
        require(type(inventory) is ContactInventory)
        prefix = inventory.unique_prefix(self.pins.radio_key)
        row = self._put(request_id, "request", row["revision"], dict(data, state="dispatching"), check)
        try:
            result = device.send_text(recipient_prefix=prefix, text=public_fragments(request.wire)[data["next"]],
                                      timestamp=self._now(), authorize=check)
            require(result.serial_accepted is True and result.delivered is False and result.read is False)
            index = data["next"] + 1
            row = self._put(request_id, "request", row["revision"], dict(data, next=index,
                state="awaiting_response" if index == data["count"] else "queued"), check)
        except Exception:
            self._uncertain = True
            raise
        return self._progress(row)

    def _receive(self, wire, access_check):
        self._check(access_check)
        now = self._now()
        try:
            envelope = _open(wire, self.identity._signing_key, self.pins.gateway_key, self.identity.agent_id, now)
            request_id = identifier(envelope["request_id"])
        except Exception:
            raise PublicMeshRejected() from None
        row = self._get(request_id, "request", access_check)
        if row is None:
            raise PublicMeshRejected()
        request = self._request(row)
        digest = hashlib.sha256(wire).hexdigest()
        consumed = row["data"]["state"] == "consumed"
        if consumed:
            if row["data"]["response_hash"] != digest or row["data"]["result"] != envelope.get("result"):
                raise PublicMeshRejected()
            return None
        if self._contact_admission is not None:
            original_access = access_check
            access_check = lambda: self._request_contact(request, original_access)
            access_check()
        consuming = False
        def consume(nonce, request_digest):
            nonlocal consuming
            require(nonce == request_id and request_digest == request.digest)
            require(row["data"]["state"] in {"dispatching", "awaiting_response"})
            consuming = True
            self._put(request_id, "request", row["revision"], dict(row["data"], state="consumed",
                      result=envelope["result"], response_hash=digest), access_check)
            return True
        try:
            result = open_result(wire, self.identity, request=request, now=now,
                                 consume=consume, access_check=access_check)
        except PublicMeshError:
            if not consuming and access_check() is True:
                raise PublicMeshRejected() from None
            raise
        self._check(access_check)
        self._live.pop(request_id, None)
        if self._contact_admission is not None:
            self._contact_admission.release(self._contact_tokens.pop(request_id, None), request_id)
        return result

    @_exclusive
    def receive(self, wire, *, access_check):
        """Expose a validated result only after protected nonce+result CAS."""
        return self._receive(wire, access_check)

    @_exclusive
    def ingest_frame(self, message, *, access_check):
        self._check(access_check)
        try:
            require(type(message) is IncomingMessage and decode_incoming(message.raw_frame) == message)
            require(message.sender_prefix == self.pins.radio_key[:6])
            require(message.kind == "contact" and message.text_type == 0)
            text = message.payload.decode("ascii")
            require(text.startswith(FRAME_PREFIX))
            fragment = decode_fragment("SL1:" + text[4:])
        except Exception:
            raise PublicMeshRejected() from None
        if self._contact_admission is None:
            require(self._contact(self.pins) is True)
        else:
            require(len(self._live) == 1)
            request = next(iter(self._live.values()))
            original_access = access_check
            access_check = lambda: self._request_contact(request, original_access)
            access_check()
        now = self._now()
        selected = None
        cached = self._assembly_slots.get(fragment.digest.hex())
        if cached is not None:
            saved = self._get(cached, "assembly", access_check)
            if saved is not None and saved["data"]["digest"] == fragment.digest.hex() and saved["data"]["expires_at"] > now:
                selected = (cached, saved)
        for index in range(8) if selected is None else ():
            candidate = self._id("assembly", index)
            saved = self._get(candidate, "assembly", access_check)
            if saved is not None and saved["data"]["digest"] == fragment.digest.hex() and saved["data"]["expires_at"] > now:
                selected = (candidate, saved)
                break
            if selected is None and (saved is None or saved["data"]["expires_at"] <= now):
                selected = (candidate, saved)
        if selected is None:
            raise PublicMeshRejected()
        slot, row = selected
        data = None if row is None else row["data"]
        if data is None or data["expires_at"] <= now:
            data = dict(digest=fragment.digest.hex(), count=fragment.count, size=fragment.size,
                        expires_at=now + 120, parts={})
        if (data["digest"], data["count"], data["size"]) != (fragment.digest.hex(), fragment.count, fragment.size):
            raise PublicMeshRejected()
        part = base64.b64encode(fragment.payload).decode()
        index = str(fragment.index)
        if index in data["parts"] and data["parts"][index] != part:
            raise PublicMeshRejected()
        data = dict(data, parts={**data["parts"], index: part})
        self._put(slot, "assembly", 0 if row is None else row["revision"], data, access_check)
        self._assembly_slots = {digest: saved_slot for digest, saved_slot in self._assembly_slots.items() if saved_slot != slot}
        self._assembly_slots[fragment.digest.hex()] = slot
        if len(data["parts"]) != data["count"]:
            return None
        wire = b"".join(base64.b64decode(data["parts"][str(index)], validate=True) for index in range(data["count"]))
        if len(wire) != data["size"] or hashlib.sha256(wire).hexdigest() != data["digest"]:
            raise PublicMeshRejected()
        return self._receive(wire, access_check)

    @_exclusive
    def inspect(self, request_id, *, access_check):
        self._check(access_check)
        row = self._get(identifier(request_id), "request", access_check)
        require(row is not None)
        self._request(row)
        progress = self._progress(row)
        if progress.state == "queued" and request_id not in self._live:
            progress = PublicMeshProgress(request_id, "unknown", progress.next_fragment, progress.fragment_count)
        return progress, row["data"]["result"]

    def pending_ids(self):
        now = self._now()
        self._live = {key: value for key, value in self._live.items() if value.expires_at > now}
        return tuple(self._live)

    def active(self):
        return not self._closed and bool(self._live)

    @_exclusive
    def drain(self, *, access_check):
        self._check(access_check)
        for row in self.workflow.scan(limit=50, max_records=4096, access_check=access_check):
            body = row["payload"].get("body", {})
            if body.get("schema") != SCHEMA:
                continue
            verified = self._get(row["record_id"], body["kind"], access_check)
            if verified["kind"] == "request":
                self._request(verified)
                require(verified["data"]["state"] != "dispatching")
        return True

    def close(self):
        require(self._mutex.acquire(blocking=False))
        try:
            self._closed = True
            if self._contact_admission is not None:
                for request_id, token in self._contact_tokens.items():
                    self._contact_admission.release(token, request_id)
            self._contact_tokens.clear()
            self._live.clear()
            self._assembly_slots.clear()
            return not self._uncertain
        finally:
            self._mutex.release()
