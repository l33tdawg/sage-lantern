"""Proposed v1 pinned-gateway lifecycle codec, NOT an upstream/offline BFT proof.

Only an operator-installed CanonicalObservationSource may feed the exporter.
That adapter must freshly fetch and validate canonical disclosure, revision and
observation height on the pinned chain. PublicObservation alone cannot supply
the latter two; receipt committed_height is not a status revision. No production
source adapter is supplied here. Never expose source/key injection to callers.

Verification requires explicit gateway/chain/domain/source-class pins and an
operator-supplied durable atomic GatewayReplayStore. Its consume operation must
reject previously used gateway/recipient/request nonces, immutable-binding
changes, revision/height/time rollback, and conflicting status at one revision.
The revision watermark is keyed by gateway/chain/record, not recipient, so a new
request or recipient cannot revive an older committed observation. Retain replay
state through restarts; absent/unavailable storage must deny verification. This
codec does not implement storage or attest that a supplied implementation is
durable. Gateway revocation means removing its pin. No default trust exists.
expected_request must come from the recipient's trusted outstanding-request
context, never be reconstructed from the received bundle. Nonces are UUIDv4;
the recipient must not accept a gateway-selected nonce as its own request.

The signed payload contains public metadata/digests only, never question/answer,
photo bytes or private provenance. A valid signature authenticates this pinned
gateway's observation, not factual correctness or independent chain consensus.
Even committed observations have cache_eligible=False and factual_verified=False.
They cannot authorize BFT-accepted green: consensus_verified and
acceptance_available stay False. Four-validator resilience, inclusion/quorum
proofs and any distinct curated-publisher policy remain outside this codec.
No fake proof-digest placeholder is emitted.
Expired observations can be explicitly verified as historical only; they still
consume replay state and never become current lifecycle or cache evidence.
No networking, key loading, publication, provider activation or service wiring.
"""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
from uuid import UUID

from nacl.signing import SigningKey, VerifyKey

from .community_public import CommunityPublic, PublicObservation, PublicProposal, PublicReceipt


SCHEMA = "sage.lantern.public-gateway-observation.v1"
SIGN_DOMAIN = (SCHEMA + "/signature\x00").encode("ascii")
MAX_BYTES = 4096
MAX_TTL = 300
MAX_NUMBER = 2 ** 53 - 1
STATES = {"proposed": "pending", "validated": "pending", "committed": "accepted",
          "challenged": "challenged", "deprecated": "deprecated"}


class GatewayObservationError(ValueError):
    def __init__(self):
        super().__init__("gateway_observation_unavailable")


def _require(condition):
    if not condition:
        raise GatewayObservationError()


def _hex(value, length=64):
    _require(type(value) is str and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None)
    return value


def _token(value):
    _require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None)


def _uuid(value):
    _require(type(value) is str and len(value) == 36 and str(UUID(value)) == value and UUID(value).int != 0)


def _integer(value, minimum=1):
    _require(type(value) is int and minimum <= value <= MAX_NUMBER)


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _clock(clock):
    value = clock()
    _require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= MAX_NUMBER)
    return math.floor(value)


@dataclass(frozen=True)
class GatewayRequest:
    chain_id: str
    record_id: str
    content_digest: str
    author: str
    domain: str
    source_class: str
    request_id: str
    recipient: str


@dataclass(frozen=True)
class GatewayPolicy:
    gateway_keys: frozenset[str]
    chain_id: str
    domains: frozenset[str]
    source_classes: frozenset[str]
    max_ttl_seconds: int


@dataclass(frozen=True)
class CanonicalGatewayEvidence:
    proposal: PublicProposal
    observation: PublicObservation
    status_revision: int
    observed_height: int
    observed_at: int


class CanonicalObservationSource(ABC):
    @abstractmethod
    def fetch(self, request, *, access_check):
        """Fresh verified canonical evidence; operator trust boundary, not JSON input."""
        raise NotImplementedError


@dataclass(frozen=True)
class GatewayCheckpoint:
    gateway_key: str
    chain_id: str
    record_id: str
    binding_digest: str
    status: str
    status_revision: int
    observed_height: int
    observed_at: int
    request_id: str
    recipient: str
    bundle_digest: str


class GatewayReplayStore(ABC):
    @abstractmethod
    def consume(self, checkpoint):
        """Durable atomic nonce + monotonic binding/status CAS; only literal True succeeds."""
        raise NotImplementedError


@dataclass(frozen=True)
class GatewayObservation:
    request: GatewayRequest
    gateway_key: str
    status: str
    status_revision: int
    observed_height: int
    observed_at: int
    valid_until: int
    fresh: bool
    current_lifecycle_available: bool
    trust_basis: str = field(default="pinned_gateway", init=False)
    offline_proof: bool = field(default=False, init=False)
    consensus_verified: bool = field(default=False, init=False)
    acceptance_available: bool = field(default=False, init=False)
    factual_verified: bool = field(default=False, init=False)
    cache_eligible: bool = field(default=False, init=False)


def _policy(policy):
    _require(type(policy) is GatewayPolicy)
    _token(policy.chain_id)
    for values in (policy.gateway_keys, policy.domains, policy.source_classes):
        _require(type(values) is frozenset and 1 <= len(values) <= 64)
    for key in policy.gateway_keys:
        _hex(key)
    for value in policy.domains | policy.source_classes:
        _token(value)
    _integer(policy.max_ttl_seconds)
    _require(policy.max_ttl_seconds <= MAX_TTL)


def _request(request, policy):
    _require(type(request) is GatewayRequest)
    _require(request.chain_id == policy.chain_id and request.domain in policy.domains
             and request.source_class in policy.source_classes)
    _uuid(request.record_id)
    _uuid(request.request_id)
    _require(UUID(request.request_id).version == 4)
    for value in (request.content_digest, request.author, request.recipient):
        _hex(value)


class GatewayExporter:
    def __init__(self, signing_key, source, *, policy, clock, ttl_seconds):
        _policy(policy)
        _require(isinstance(signing_key, SigningKey) and isinstance(source, CanonicalObservationSource) and callable(clock))
        _integer(ttl_seconds)
        _require(ttl_seconds <= policy.max_ttl_seconds and signing_key.verify_key.encode().hex() in policy.gateway_keys)
        self._key = signing_key
        self._source = source
        self._policy = policy
        self._clock = clock
        self._ttl = ttl_seconds

    def export(self, request, *, access_check):
        try:
            _request(request, self._policy)
            _require(callable(access_check) and access_check() is True)
            started = _clock(self._clock)
            evidence = self._source.fetch(request, access_check=access_check)
            _require(access_check() is True and type(evidence) is CanonicalGatewayEvidence)
            proposal, observation = evidence.proposal, evidence.observation
            _require(type(proposal) is PublicProposal and type(observation) is PublicObservation)
            CommunityPublic(None, None, chain_id=request.chain_id, domain=request.domain, author=request.author)._proposal(proposal)
            body = json.loads(proposal.content)
            _require(body["classification"] == 0 and body["source_class"] == request.source_class
                     and proposal.content_hash == request.content_digest)
            receipt = observation.receipt
            _require(type(receipt) is PublicReceipt and receipt.operation_id == proposal.operation_id
                     and receipt.memory_id == request.record_id and receipt.content_hash == request.content_digest
                     and receipt.intended_chain_id == request.chain_id)
            _hex(receipt.tx_hash)
            if receipt.committed_height is not None:
                _integer(receipt.committed_height)
            _require(observation.observed_status in STATES and observation.state == STATES[observation.observed_status]
                     and observation.reason == "online_node_observation" and observation.content_bound is True
                     and observation.chain_verified is True and observation.acceptance_available is True
                     and observation.trust_basis == "trusted_node_online" and observation.offline_proof is False
                     and observation.cache_eligible is False)
            _integer(evidence.status_revision)
            _integer(evidence.observed_height)
            _require(receipt.committed_height is None or receipt.committed_height <= evidence.observed_height)
            _integer(evidence.observed_at, 0)
            now = _clock(self._clock)
            valid_until = evidence.observed_at + self._ttl
            _integer(valid_until)
            _require(started <= evidence.observed_at <= now < valid_until)
            signed = dict(schema=SCHEMA, gateway_key=self._key.verify_key.encode().hex(), **asdict(request),
                          classification=0, status=observation.observed_status, status_revision=evidence.status_revision,
                          observed_height=evidence.observed_height, observed_at=evidence.observed_at, valid_until=valid_until)
            signature = self._key.sign(SIGN_DOMAIN + _canonical(signed)).signature.hex()
            encoded = _canonical(dict(body=signed, signature=signature))
            _require(len(encoded) <= MAX_BYTES and access_check() is True)
            return encoded
        except Exception:
            raise GatewayObservationError() from None


class GatewayVerifier:
    def __init__(self, *, policy, clock, replay_store):
        _policy(policy)
        _require(callable(clock) and isinstance(replay_store, GatewayReplayStore))
        self._policy = policy
        self._clock = clock
        self._replay = replay_store

    def verify(self, bundle, *, expected_request, allow_expired_history=False):
        try:
            _require(type(bundle) is bytes and 1 <= len(bundle) <= MAX_BYTES and type(allow_expired_history) is bool)
            _request(expected_request, self._policy)
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    _require(key not in result)
                    result[key] = value
                return result
            envelope = json.loads(bundle, object_pairs_hook=unique)
            _require(type(envelope) is dict and set(envelope) == {"body", "signature"} and _canonical(envelope) == bundle)
            body = envelope["body"]
            fields = set(asdict(expected_request)) | {"schema", "gateway_key", "classification", "status", "status_revision",
                                                       "observed_height", "observed_at", "valid_until"}
            _require(type(body) is dict and set(body) == fields and body["schema"] == SCHEMA)
            for name, value in asdict(expected_request).items():
                _require(type(body[name]) is str and body[name] == value)
            _require(_hex(body["gateway_key"]) in self._policy.gateway_keys)
            _require(type(body["classification"]) is int and body["classification"] == 0)
            _require(type(body["status"]) is str and body["status"] in STATES)
            for field in ("status_revision", "observed_height", "valid_until"):
                _integer(body[field])
            _integer(body["observed_at"], 0)
            _require(0 < body["valid_until"] - body["observed_at"] <= self._policy.max_ttl_seconds)
            VerifyKey(bytes.fromhex(body["gateway_key"])).verify(SIGN_DOMAIN + _canonical(body), bytes.fromhex(_hex(envelope["signature"], 128)))
            now = _clock(self._clock)
            _require(body["observed_at"] <= now)
            fresh = now < body["valid_until"]
            _require(fresh or allow_expired_history)
            binding = {name: body[name] for name in ("chain_id", "record_id", "content_digest", "author", "domain", "source_class", "classification")}
            checkpoint = GatewayCheckpoint(body["gateway_key"], body["chain_id"], body["record_id"],
                hashlib.sha256(_canonical(binding)).hexdigest(), body["status"], body["status_revision"], body["observed_height"],
                body["observed_at"], body["request_id"], body["recipient"], hashlib.sha256(bundle).hexdigest())
            _require(self._replay.consume(checkpoint) is True)
            completed = _clock(self._clock)
            _require(completed >= now)
            fresh = completed < body["valid_until"]
            _require(fresh or allow_expired_history)
            return GatewayObservation(expected_request, body["gateway_key"], body["status"], body["status_revision"],
                                      body["observed_height"], body["observed_at"], body["valid_until"], fresh, fresh)
        except Exception:
            raise GatewayObservationError() from None
