"""Bounded encrypted PUBLIC derivative transport, not chat or acceptance proof.

Sign fresh only after contact, under the Lantern's ordinary public SDK identity.
Persist logical approval separately, never a reusable HTTP authentication packet.
Gateway and author pins are trusted configuration, not discoveries from the wire.
The approval callback must validate a retained exact user-approved derivative,
not merely accept any caller JSON. It and the access guard are local capabilities.
Ordinary SDK REST v1 signatures do not independently bind a destination chain;
use a dedicated public identity enrolled only on the intended chain and a pinned
gateway/backend mapping. This does not turn gateway trust into BFT verification.
"""

import base64
from dataclasses import dataclass
import hashlib
import json
import re
import struct
import uuid

from nacl.public import SealedBox
from nacl.bindings import crypto_box_SEALBYTES
from nacl.signing import SigningKey, VerifyKey
from sage_sdk.auth import AgentIdentity
from sage_sdk.models import MemorySubmitRequest

from .community_public import CommunityPublic, PublicProposal, PublicReceipt
from .mesh_fragments import CHUNK_BYTES


PREFIX = b"LPM1."
DOMAIN = b"sage.lantern.public-mesh.v1\x00"
SCHEMA = "sage.lantern.public-mesh.v1"
MAX_WIRE = 4096
MAX_CONTENT = 1024
MAX_LIFETIME = 120
MAX_INTEGER = 2 ** 53 - 1
FLAGS = dict(acceptance_available=False, consensus_verified=False, cache_eligible=False)


class PublicMeshError(RuntimeError):
    def __init__(self, code="public_mesh_unavailable"):
        super().__init__(code)


def require(value, code="public_mesh_unavailable"):
    if not value:
        raise PublicMeshError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def identifier(value):
    require(type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0)
    return value


def public_key(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return bytes.fromhex(value)


def timestamp(value):
    require(type(value) is int and 0 < value <= MAX_INTEGER)
    return value


def proposal_data(proposal):
    require(type(proposal) is PublicProposal)
    CommunityPublic(None, None, chain_id=proposal.chain_id, domain=proposal.domain, author=proposal.author)._proposal(proposal)
    identifier(proposal.operation_id)
    require(len(proposal.content.encode("utf-8")) <= MAX_CONTENT, "public_mesh_oversize")
    return dict(operation_id=proposal.operation_id, content=proposal.content, content_hash=proposal.content_hash,
                chain_id=proposal.chain_id, domain=proposal.domain, author=proposal.author)


def submit_body(proposal):
    proposal_data(proposal)
    request = MemorySubmitRequest(content=proposal.content, memory_type="inference", domain_tag=proposal.domain,
                                  confidence_score=0.5, classification=0)
    return json.dumps(request.model_dump(mode="json", exclude_none=True, by_alias=True), separators=(",", ":")).encode()


def verify_http(headers, author, method, path, body, issued_at):
    require(type(headers) is dict and set(headers) == {"X-Agent-ID", "X-Signature", "X-Timestamp", "X-Nonce"})
    require(headers["X-Agent-ID"] == author and headers["X-Timestamp"] == str(issued_at))
    require(type(headers["X-Nonce"]) is str and re.fullmatch(r"[0-9a-f]{16}", headers["X-Nonce"]) is not None)
    require(type(headers["X-Signature"]) is str and re.fullmatch(r"[0-9a-f]{128}", headers["X-Signature"]) is not None)
    digest = hashlib.sha256(method.encode() + b" " + path.encode() + b"\n" + body).digest()
    VerifyKey(public_key(author)).verify(digest + struct.pack(">q", issued_at) + bytes.fromhex(headers["X-Nonce"]),
                                        bytes.fromhex(headers["X-Signature"]))


def _seal(payload, signing_key, recipient):
    require(isinstance(signing_key, SigningKey))
    raw = canonical(payload)
    inner = canonical(dict(payload=payload, signature=signing_key.sign(DOMAIN + raw).signature.hex()))
    wire = PREFIX + base64.b64encode(SealedBox(VerifyKey(public_key(recipient)).to_curve25519_public_key()).encrypt(inner))
    require(len(wire) <= MAX_WIRE, "public_mesh_oversize")
    return wire


def _open(wire, signing_key, sender, recipient, now):
    require(type(wire) is bytes and len(PREFIX) < len(wire) <= MAX_WIRE and wire.startswith(PREFIX))
    require(isinstance(signing_key, SigningKey) and bytes(signing_key.verify_key).hex() == recipient)
    encrypted = base64.b64decode(wire[len(PREFIX):], validate=True)
    require(base64.b64encode(encrypted) == wire[len(PREFIX):])
    raw = SealedBox(signing_key.to_curve25519_private_key()).decrypt(encrypted)
    value = parse(raw)
    require(type(value) is dict and set(value) == {"payload", "signature"} and raw == canonical(value))
    require(type(value["signature"]) is str and re.fullmatch(r"[0-9a-f]{128}", value["signature"]) is not None)
    payload = value["payload"]
    VerifyKey(public_key(sender)).verify(DOMAIN + canonical(payload), bytes.fromhex(value["signature"]))
    require(payload["schema"] == SCHEMA and payload["sender"] == sender and payload["recipient"] == recipient)
    timestamp(now)
    require(timestamp(payload["issued_at"]) <= now < timestamp(payload["expires_at"])
            and 0 < payload["expires_at"] - payload["issued_at"] <= MAX_LIFETIME, "public_mesh_expired")
    identifier(payload["request_id"])
    return payload


@dataclass(frozen=True, repr=False)
class OutboundRequest:
    wire: bytes
    request_id: str
    digest: str
    proposal: PublicProposal
    gateway_key: str
    expires_at: int
    issued_at: int
    record_id: str | None


def _request(proposal, identity, gateway_key, *, request_id, now, lifetime, record_id, access_check, approval_check):
    try:
        require(isinstance(identity, AgentIdentity) and identity.agent_id == proposal.author)
        require(callable(access_check) and access_check() is True and callable(approval_check)
                and approval_check(proposal) is True, "public_mesh_not_approved")
        proposal_data(proposal)
        timestamp(now)
        identifier(request_id)
        require(type(lifetime) is int and 1 <= lifetime <= MAX_LIFETIME)
        method, path, body = ("POST", "/v1/memory/submit", submit_body(proposal)) if record_id is None else (
            "GET", "/v1/memory/" + identifier(record_id), b"")
        payload = dict(schema=SCHEMA, kind="submit" if record_id is None else "poll", sender=proposal.author,
                       recipient=gateway_key, request_id=request_id, issued_at=now, expires_at=now + lifetime,
                       proposal=proposal_data(proposal), record_id=record_id,
                       auth=identity.sign_request(method, path, body, timestamp=now),
                       profile_auth=identity.sign_request("GET", "/v1/agent/me", b"", timestamp=now))
        wire = _seal(payload, identity._signing_key, gateway_key)
        require(access_check() is True, "public_mesh_not_approved")
        return OutboundRequest(wire, request_id, hashlib.sha256(canonical(payload)).hexdigest(), proposal, gateway_key, now + lifetime, now, record_id)
    except PublicMeshError:
        raise
    except Exception:
        raise PublicMeshError() from None


def seal_submission(proposal, identity, gateway_key, *, request_id, now, access_check, approval_check, lifetime=120):
    return _request(proposal, identity, gateway_key, request_id=request_id, now=now, lifetime=lifetime,
                    record_id=None, access_check=access_check, approval_check=approval_check)


def seal_poll(proposal, receipt, identity, gateway_key, *, request_id, now, access_check, approval_check, lifetime=120):
    require(type(receipt) is PublicReceipt and receipt.operation_id == proposal.operation_id
            and receipt.content_hash == proposal.content_hash and receipt.intended_chain_id == proposal.chain_id)
    return _request(proposal, identity, gateway_key, request_id=request_id, now=now, lifetime=lifetime,
                    record_id=receipt.memory_id, access_check=access_check, approval_check=approval_check)


def open_request(wire, signing_key, *, author, chain_id, domain, now):
    try:
        payload = _open(wire, signing_key, author, bytes(signing_key.verify_key).hex(), now)
        require(set(payload) == {"schema", "kind", "sender", "recipient", "request_id", "issued_at", "expires_at",
                                 "proposal", "record_id", "auth", "profile_auth"} and payload["kind"] in {"submit", "poll"})
        proposal = PublicProposal(**payload["proposal"])
        require(proposal_data(proposal) == payload["proposal"] and proposal.author == author
                and proposal.chain_id == chain_id and proposal.domain == domain)
        if payload["kind"] == "submit":
            require(payload["record_id"] is None)
            method, path, body = "POST", "/v1/memory/submit", submit_body(proposal)
        else:
            method, path, body = "GET", "/v1/memory/" + identifier(payload["record_id"]), b""
        verify_http(payload["auth"], author, method, path, body, payload["issued_at"])
        verify_http(payload["profile_auth"], author, "GET", "/v1/agent/me", b"", payload["issued_at"])
        return payload
    except PublicMeshError:
        raise
    except Exception:
        raise PublicMeshError() from None


def result_data(state, *, receipt=None, observed_status=None):
    require(state in {"unknown", "receipt", "observed", "unavailable"})
    require(observed_status is None or observed_status in {"proposed", "validated", "committed", "challenged", "deprecated"})
    require((state in {"receipt", "observed"}) == (receipt is not None))
    require((state == "observed") == (observed_status is not None))
    return dict(state=state, receipt=receipt, observed_status=observed_status, trust_basis="pinned_gateway", **FLAGS)


def response_fragment_bound(proposal, *, gateway_key):
    """Bound every valid receipt/status response, not the generic wire capacity."""
    proposal_data(proposal)
    public_key(proposal.author)
    public_key(gateway_key)
    receipt = dict(operation_id=proposal.operation_id, content_hash=proposal.content_hash,
        intended_chain_id=proposal.chain_id, memory_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        tx_hash="f" * 64, committed_height=MAX_INTEGER)
    payload = dict(schema=SCHEMA, kind="result", sender=gateway_key, recipient=proposal.author,
        request_id="ffffffff-ffff-ffff-ffff-ffffffffffff", issued_at=MAX_INTEGER - 1, expires_at=MAX_INTEGER,
        request_digest="f" * 64, result=result_data("observed", receipt=receipt, observed_status="deprecated"))
    inner_size = len(canonical(dict(payload=payload, signature="f" * 128)))
    sealed_size = len(PREFIX) + 4 * ((inner_size + crypto_box_SEALBYTES + 2) // 3)
    require(sealed_size <= MAX_WIRE, "public_mesh_oversize")
    return (sealed_size + CHUNK_BYTES - 1) // CHUNK_BYTES


def seal_result(request, result, signing_key, *, now):
    require(now < request["expires_at"], "public_mesh_expired")
    payload = dict(schema=SCHEMA, kind="result", sender=request["recipient"], recipient=request["sender"],
                   request_id=request["request_id"], issued_at=now, expires_at=request["expires_at"],
                   request_digest=hashlib.sha256(canonical(request)).hexdigest(), result=result)
    return _seal(payload, signing_key, request["sender"])


def open_result(wire, identity, *, request, now, consume, access_check):
    """consume(request UUID, digest) must durably consume an outstanding request once."""
    try:
        require(type(request) is OutboundRequest and access_check() is True)
        payload = _open(wire, identity._signing_key, request.gateway_key, identity.agent_id, now)
        require(set(payload) == {"schema", "kind", "sender", "recipient", "request_id", "issued_at", "expires_at", "request_digest", "result"}
                and payload["kind"] == "result" and payload["request_id"] == request.request_id
                and payload["request_digest"] == request.digest and payload["expires_at"] == request.expires_at
                and payload["issued_at"] >= request.issued_at)
        result = payload["result"]
        require(type(result) is dict and result == result_data(result["state"], receipt=result["receipt"], observed_status=result["observed_status"]))
        require(all(result[key] is False for key in FLAGS))
        require(result["state"] != "observed" or request.record_id is not None)
        if result["receipt"] is not None:
            receipt = PublicReceipt(**result["receipt"])
            require(receipt.operation_id == request.proposal.operation_id and receipt.content_hash == request.proposal.content_hash
                    and receipt.intended_chain_id == request.proposal.chain_id)
            identifier(receipt.memory_id)
            require(request.record_id is None or receipt.memory_id == request.record_id)
            public_key(receipt.tx_hash)
            require(receipt.committed_height is None or type(receipt.committed_height) is int and 0 < receipt.committed_height <= MAX_INTEGER)
        require(access_check() is True and callable(consume) and consume(request.request_id, request.digest) is True)
        require(access_check() is True)
        return result
    except PublicMeshError:
        raise
    except Exception:
        raise PublicMeshError() from None
