"""Public-only SDK submission and content-bound observations, not acceptance proof.

The caller supplies an enrolled ordinary public identity, pinned SDK transport
with finite timeout, durable journal, and approved derivative bytes. No private
Root, enrollment, photo publication, automatic retry, or reviewer signing occurs.
Journal reserve(key, fingerprint) must be atomic/durable and never recycled;
complete(key, receipt) persists the response. Unknown submissions stay reserved.

REST get_memory does not return independently verifiable chain/status evidence.
Optional operator-injected online trust verifies the client endpoint, chain,
transport and canonical-disclosure implementation before and after observation.
This permits a trusted-node lifecycle label, never an offline proof or a factual
review attestation. HTTP callers must never select or provide this verifier.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
import uuid


SCHEMA = "sage.lantern.public-qa.v1"
MAX_CONTENT_BYTES = 16384


class CommunityPublicError(RuntimeError):
    pass


def _fail():
    raise CommunityPublicError("public_adapter_unavailable")


def _token(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is None:
        _fail()
    return value


def _uuid(value):
    if type(value) is not str:
        _fail()
    try:
        if str(uuid.UUID(value)) != value:
            _fail()
    except ValueError:
        _fail()
    return value


def _mapping(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if type(value) is not dict:
        _fail()
    return value


def _status(value):
    value = value.value if isinstance(value, Enum) else value
    if type(value) is not str or value not in {"proposed", "validated", "committed", "challenged", "deprecated"}:
        _fail()
    return value


@dataclass(frozen=True)
class PublicProposal:
    operation_id: str
    content: str
    content_hash: str
    chain_id: str
    domain: str
    author: str


@dataclass(frozen=True)
class PublicReceipt:
    operation_id: str
    content_hash: str
    intended_chain_id: str
    memory_id: str
    tx_hash: str
    committed_height: int | None


@dataclass(frozen=True)
class PublicObservation:
    state: str = "pending"
    reason: str = "acceptance_proof_unavailable"
    observed_status: str | None = None
    receipt: PublicReceipt | None = None
    content_bound: bool = False
    chain_verified: bool = False
    acceptance_available: bool = False
    trust_basis: str = "unverified"
    offline_proof: bool = False
    cache_eligible: bool = False


@dataclass(frozen=True)
class OnlineNodeTrust:
    """Operator verifier result, bound to this exact SDK client instance.

    The verifier must inspect current trusted configuration/transport and node
    revision, not echo user fields. canonical_disclosure is the reviewed
    app-v25-or-later exact-envelope/status validation contract, not a version
    guessed from a response shape. Endpoint and chain changes require new trust.
    """

    client: object
    endpoint: str
    chain_id: str
    node_revision: str
    transport_authenticated: bool
    canonical_disclosure: str


CANONICAL_DISCLOSURE = "app-v25-exact-envelope-status-v1"


class CommunityPublic:
    def __init__(self, client, journal, *, chain_id, domain, author, online_verifier=None):
        self.client = client
        self.journal = journal
        self.chain_id = _token(chain_id)
        self.domain = _token(domain)
        if type(author) is not str or re.fullmatch(r"[0-9a-f]{64}", author) is None:
            _fail()
        self.author = author
        if online_verifier is not None and not callable(online_verifier):
            _fail()
        self._online_verifier = online_verifier

    def _online_trust(self):
        if self._online_verifier is None:
            return None
        evidence = self._online_verifier(self.client)
        if (type(evidence) is not OnlineNodeTrust or evidence.client is not self.client
                or evidence.chain_id != self.chain_id
                or evidence.transport_authenticated is not True
                or evidence.canonical_disclosure != CANONICAL_DISCLOSURE
                or type(evidence.endpoint) is not str or not 1 <= len(evidence.endpoint) <= 2048
                or type(evidence.node_revision) is not str or not 1 <= len(evidence.node_revision) <= 128):
            _fail()
        return evidence

    def prepare(self, *, operation_id, question, answer):
        """Pure preview construction; approval must cover the returned content."""
        _uuid(operation_id)
        for text in (question, answer):
            if type(text) is not str or not text.strip() or len(text.encode("utf-8")) > 8192:
                _fail()
        content = json.dumps(dict(schema=SCHEMA, envelope_id=operation_id,
                                  chain_id=self.chain_id, domain=self.domain, author=self.author,
                                  classification=0, source_class="generated-unverified",
                                  question=question, answer=answer),
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            _fail()
        return PublicProposal(operation_id, content, hashlib.sha256(content.encode("utf-8")).hexdigest(),
                              self.chain_id, self.domain, self.author)

    def _proposal(self, proposal):
        if (type(proposal) is not PublicProposal or type(proposal.content) is not str
                or len(proposal.content.encode("utf-8")) > MAX_CONTENT_BYTES):
            _fail()
        try:
            body = json.loads(proposal.content)
            expected = self.prepare(operation_id=proposal.operation_id,
                                    question=body["question"], answer=body["answer"])
            if proposal != expected:
                _fail()
        except (ValueError, KeyError, TypeError):
            _fail()

    def _profile(self):
        profile = _mapping(self.client.get_profile())
        if (profile.get("agent_id") != self.author or profile.get("role") != "member"
                or profile.get("enrollment_status") != "active"
                or profile.get("registration_status") != "active"
                or profile.get("approval_required") is not False
                or profile.get("can_read") is not True or profile.get("can_write") is not True
                or type(profile.get("clearance")) is not int or profile["clearance"] != 0
                or profile.get("home_domain") != self.domain):
            _fail()

    def submit(self, proposal, *, access_check):
        self._proposal(proposal)
        try:
            if access_check() is not True:
                _fail()
            self._profile()
            if self.journal.reserve(proposal.operation_id, proposal.content_hash) is not True:
                return PublicObservation(reason="reconcile_required")
            if access_check() is not True:
                return PublicObservation(reason="admission_cancelled")
            response = _mapping(self.client.propose(content=proposal.content, memory_type="inference",
                                domain_tag=self.domain, confidence=0.5, classification=0))
            status = _status(response.get("status"))
            memory_id = _uuid(response.get("memory_id"))
            tx_hash = response.get("tx_hash")
            height = response.get("committed_height")
            if (type(tx_hash) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", tx_hash) is None
                    or (height is not None and (type(height) is not int or height <= 0))):
                _fail()
            receipt = PublicReceipt(proposal.operation_id, proposal.content_hash, self.chain_id,
                                    memory_id, tx_hash.lower(), height)
            self.journal.complete(proposal.operation_id, receipt)
            return PublicObservation(reason="submitted_unverified", observed_status=status, receipt=receipt)
        except Exception:
            raise CommunityPublicError("submission_unconfirmed") from None

    def poll(self, proposal, receipt, *, access_check):
        self._proposal(proposal)
        if (type(receipt) is not PublicReceipt or receipt.operation_id != proposal.operation_id
                or receipt.content_hash != proposal.content_hash or receipt.intended_chain_id != self.chain_id):
            _fail()
        _uuid(receipt.memory_id)
        try:
            if access_check() is not True:
                raise CommunityPublicError("observation_cancelled")
            trust_before = self._online_trust()
            if access_check() is not True:
                raise CommunityPublicError("observation_cancelled")
            record = _mapping(self.client.get_memory(receipt.memory_id))
            trust_after = self._online_trust()
            status = _status(record.get("status"))
            if (record.get("memory_id") != receipt.memory_id
                    or record.get("submitting_agent") != self.author
                    or record.get("domain_tag") != self.domain
                    or record.get("memory_type") != "inference"
                    or type(record.get("classification")) is not int or record["classification"] != 0
                    or record.get("content") != proposal.content
                    or record.get("content_hash") != proposal.content_hash):
                result = PublicObservation(reason="content_binding_failed", receipt=receipt)
            else:
                if trust_before is not None:
                    if (trust_after is None or trust_after.client is not trust_before.client
                            or trust_after.endpoint != trust_before.endpoint
                            or trust_after.chain_id != trust_before.chain_id
                            or trust_after.node_revision != trust_before.node_revision):
                        _fail()
                    state = "accepted" if status == "committed" else status if status in ("challenged", "deprecated") else "pending"
                    result = PublicObservation(state=state, reason="online_node_observation",
                                               observed_status=status, receipt=receipt, content_bound=True,
                                               chain_verified=True, acceptance_available=True,
                                               trust_basis="trusted_node_online")
                else:
                    result = PublicObservation(observed_status=status, receipt=receipt, content_bound=True)
        except Exception:
            result = PublicObservation(reason="status_unavailable", receipt=receipt)
        try:
            if access_check() is not True:
                raise CommunityPublicError("observation_cancelled")
        except Exception:
            raise CommunityPublicError("observation_cancelled") from None
        return result
