"""Offline reusable-answer eligibility, not a SAGE consensus-proof adapter.

The signed envelope below is an application reviewer/gateway attestation. It
asserts BOTH current public-chain state and an independent reusable-answer
review. A proposer signature, receipt, confidence score, or committed status
alone is not this authority and does not establish truth. Only an explicitly
pinned reviewer key and named policy may attest this envelope. The integrator
must implement/audit that review and chain-verification service before enabling
reuse. This module never fetches, publishes, imports private history, or votes.

Answer bytes live only in a bounded in-memory cache. A caller-provided durable,
atomic ReplayGuard is mandatory, including on restart. Its monotonic revision
watermarks and permanent local revocations MUST outlive cache eviction and
recreation, fail closed on storage failure, and never be restored from an
untrusted cache snapshot. No replay-state implementation or key provisioning is
provided here. No pinned trust or replay guard means disabled, always a miss.

Expiry bounds offline staleness; no offline cache can detect an unseen remote
challenge. The publisher must supply short-lived verified state and invalidation
updates. Callers must accurately declare personal/context/photo dependence.
Reviewer attestation is a trust decision, not a mathematical guarantee of truth.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urlsplit

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


SCHEMA = "sage.commons.reviewed-answer.v1"
MAX_ENVELOPE_BYTES = 16384
MAX_QUESTION_BYTES = 1024
MAX_ANSWER_BYTES = 8192
MAX_PROOF_AGE_SECONDS = 604800


class SharedAnswerError(RuntimeError):
    pass


class ReplayGuard(Protocol):
    """Trusted durable control state, separate from untrusted cached records.

    advance is an atomic compare-and-set: only a strictly newer positive revision
    may succeed, never a revoked identity. current requires exact revision/digest
    and no revocation. revoke permanently denies an identity, even if never seen.
    No method may silently evict, reset, or downgrade watermarks.
    """

    def advance(self, identity: tuple[str, str], revision: int, digest: str) -> bool: ...
    def current(self, identity: tuple[str, str], revision: int, digest: str) -> bool: ...
    def revoke(self, identity: tuple[str, str]) -> None: ...


@dataclass(frozen=True)
class PinnedReviewTrust:
    reviewer_id: str
    public_key: bytes
    chain_id: str
    domains: frozenset[str]
    review_policy: str
    licenses: frozenset[str]
    max_age_seconds: int


@dataclass(frozen=True)
class VerifiedSharedAnswer:
    chain_id: str
    record_id: str
    revision: int
    committed_height: int
    domain: str
    question: str
    language: str
    answer: str
    verified_at: int
    expires_at: int
    reviewer_id: str
    review_policy: str
    license: str
    sources: tuple[tuple[str, str], ...]
    source: str = "public-cached"


@dataclass(frozen=True)
class _Entry:
    answer: VerifiedSharedAnswer
    payload: bytes
    signature: bytes
    digest: str


def _token(value: object) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise SharedAnswerError("invalid_identifier")
    return value


def _text(value: object, limit: int) -> str:
    if type(value) is not str or not value.strip():
        raise SharedAnswerError("invalid_text")
    try:
        if len(value.encode("utf-8")) > limit:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise SharedAnswerError("invalid_text") from None
    if any(unicodedata.category(character) in {"Cc", "Cf"} and not character.isspace() for character in value):
        raise SharedAnswerError("invalid_text")
    return value


def normalize_question(question: str) -> str:
    """NFC and whitespace only; case, punctuation, numbers and subjects survive."""
    return " ".join(unicodedata.normalize("NFC", _text(question, MAX_QUESTION_BYTES)).split())


def normalize_language(language: str) -> str:
    """Bounded explicit language-tag subset; no fallback or inferred language."""
    if type(language) is not str or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,4}", language):
        raise SharedAnswerError("invalid_language")
    return language.lower()


def canonical_attestation(value: dict) -> bytes:
    """Canonical application signing bytes; not a native SAGE transaction codec."""
    try:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SharedAnswerError("invalid_attestation") from None
    if type(value) is not dict or not 1 <= len(data) <= MAX_ENVELOPE_BYTES:
        raise SharedAnswerError("invalid_attestation")
    return data


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise SharedAnswerError("invalid_attestation")
        result[name] = value
    return result


class SharedAnswerCache:
    def __init__(self, *, trust: PinnedReviewTrust | None = None,
                 replay_guard: ReplayGuard | None = None, clock: Callable[[], float] = time.time,
                 max_entries: int = 128, max_bytes: int = 1048576):
        if (type(max_entries) is not int or not 1 <= max_entries <= 1024
                or type(max_bytes) is not int or not MAX_ENVELOPE_BYTES <= max_bytes <= 16777216):
            raise SharedAnswerError("invalid_cache_bounds")
        if trust is not None:
            if not isinstance(trust, PinnedReviewTrust):
                raise SharedAnswerError("invalid_trust")
            for value in (trust.reviewer_id, trust.chain_id, trust.review_policy):
                _token(value)
            if (type(trust.public_key) is not bytes or len(trust.public_key) != 32
                    or type(trust.domains) is not frozenset or not 1 <= len(trust.domains) <= 32
                    or type(trust.licenses) is not frozenset or not 1 <= len(trust.licenses) <= 16
                    or type(trust.max_age_seconds) is not int
                    or not 1 <= trust.max_age_seconds <= MAX_PROOF_AGE_SECONDS):
                raise SharedAnswerError("invalid_trust")
            for value in (*trust.domains, *trust.licenses):
                _token(value)
        self._trust = trust
        self._guard = replay_guard
        self._clock = clock
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries = OrderedDict()
        self._lock = threading.RLock()
        self._last_time = None
        self._failed = False

    @property
    def enabled(self) -> bool:
        return self._trust is not None and self._guard is not None and not self._failed

    def _now(self) -> float:
        try:
            current = self._clock()
            if (type(current) not in (int, float) or not 0 <= current <= 1e12
                    or not math.isfinite(current)
                    or (self._last_time is not None and current < self._last_time)):
                raise ValueError()
            self._last_time = current
            return current
        except Exception:
            self._failed = True
            raise SharedAnswerError("clock_unavailable") from None

    def _guard_call(self, method: str, *arguments):
        try:
            result = getattr(self._guard, method)(*arguments)
            if method != "revoke" and type(result) is not bool:
                raise ValueError()
            return result
        except Exception:
            self._failed = True
            raise SharedAnswerError("replay_protection_unavailable") from None

    def _verify(self, payload: bytes, signature: bytes) -> dict:
        if (type(payload) is not bytes or not 1 <= len(payload) <= MAX_ENVELOPE_BYTES
                or type(signature) is not bytes or len(signature) != 64):
            raise SharedAnswerError("invalid_proof")
        try:
            VerifyKey(self._trust.public_key).verify(payload, signature)
        except (ValueError, BadSignatureError):
            raise SharedAnswerError("invalid_proof") from None
        try:
            value = json.loads(payload, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise SharedAnswerError("invalid_attestation") from None
        if type(value) is not dict or canonical_attestation(value) != payload:
            raise SharedAnswerError("invalid_attestation")
        return value

    def _eligible(self, value: dict, now: float) -> VerifiedSharedAnswer | None:
        fields = {"schema", "chain_id", "record_id", "revision", "domain", "classification",
                  "status", "committed_height", "verified_at", "expires_at", "question", "answer",
                  "language", "scope", "modality", "challenged", "superseded", "license", "provenance"}
        if (set(value) != fields or type(value["classification"]) is not int
                or value["classification"] != 0 or value["status"] != "committed"
                or value["scope"] != "context-free-public" or value["modality"] != "text"
                or value["challenged"] is not False or value["superseded"] is not False
                or type(value["committed_height"]) is not int or value["committed_height"] <= 0
                or type(value["expires_at"]) is not int
                or not value["verified_at"] <= now < value["expires_at"]
                or value["expires_at"] - value["verified_at"] > self._trust.max_age_seconds
                or type(value["license"]) is not str or value["license"] not in self._trust.licenses):
            return None
        provenance = value["provenance"]
        if (type(provenance) is not dict
                or set(provenance) != {"reviewer_id", "review_policy", "decision", "sources"}
                or provenance["reviewer_id"] != self._trust.reviewer_id
                or provenance["review_policy"] != self._trust.review_policy
                or provenance["decision"] != "verified-reusable"
                or type(provenance["sources"]) is not list or not 1 <= len(provenance["sources"]) <= 8):
            return None
        sources = []
        for source in provenance["sources"]:
            if type(source) is not dict or set(source) != {"uri", "sha256"}:
                return None
            uri = _text(source["uri"], 1024)
            try:
                parsed = urlsplit(uri)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    return None
            except ValueError:
                return None
            if type(source["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
                return None
            sources.append((uri, source["sha256"]))
        return VerifiedSharedAnswer(value["chain_id"], value["record_id"], value["revision"],
                            value["committed_height"], value["domain"], normalize_question(value["question"]),
                            normalize_language(value["language"]), _text(value["answer"], MAX_ANSWER_BYTES),
                            value["verified_at"], value["expires_at"], self._trust.reviewer_id,
                            self._trust.review_policy, value["license"], tuple(sources))

    def ingest_verified(self, payload: bytes, signature: bytes) -> bool:
        """Internal trusted ingestion boundary; never bind directly to a UI route.

        Signed but ineligible newer revisions replace the watermark and evict old
        answers. Unsigned/mis-scoped data cannot mutate cache or replay state.
        """
        with self._lock:
            if not self.enabled:
                return False
            now = self._now()
            value = self._verify(payload, signature)
            if (value.get("schema") != SCHEMA or value.get("chain_id") != self._trust.chain_id
                    or type(value.get("domain")) is not str or value["domain"] not in self._trust.domains
                    or type(value.get("revision")) is not int or not 1 <= value["revision"] <= 2**63 - 1
                    or type(value.get("verified_at")) is not int or not 0 <= value["verified_at"] <= now):
                raise SharedAnswerError("invalid_attestation_scope")
            identity = (value["chain_id"], _token(value.get("record_id")))
            try:
                answer = self._eligible(value, now)
            except SharedAnswerError:
                answer = None
            digest = hashlib.sha256(payload).hexdigest()
            if not self._guard_call("advance", identity, value["revision"], digest):
                return False
            self._entries.pop(identity, None)
            if answer is None:
                return False
            self._entries[identity] = _Entry(answer, payload, signature, digest)
            while (len(self._entries) > self._max_entries
                   or sum(len(entry.payload) + len(entry.signature) for entry in self._entries.values()) > self._max_bytes):
                self._entries.popitem(last=False)
            return identity in self._entries

    def lookup(self, query: str, language: str, *, standalone: bool = False,
               personal: bool = True, photo_digest: str | None = None) -> VerifiedSharedAnswer | None:
        """Exact text-only lookup; ambiguous multiple reviewed answers are a miss."""
        with self._lock:
            if not self.enabled or standalone is not True or personal is not False or photo_digest is not None:
                return None
            try:
                key = (normalize_question(query), normalize_language(language))
                now = self._now()
                matches = []
                for identity, entry in list(self._entries.items()):
                    if not self._guard_call("current", identity, entry.answer.revision, entry.digest):
                        self._entries.pop(identity, None)
                        continue
                    value = self._verify(entry.payload, entry.signature)
                    answer = self._eligible(value, now)
                    if (answer is None or answer != entry.answer
                            or (answer.chain_id, answer.record_id) != identity
                            or hashlib.sha256(entry.payload).hexdigest() != entry.digest):
                        self._entries.pop(identity, None)
                        continue
                    if (answer.question, answer.language) == key:
                        matches.append(answer)
                return matches[0] if len(matches) == 1 else None
            except SharedAnswerError:
                return None

    def invalidate(self, record_id: str) -> None:
        """Local deny-only invalidation; durable tombstone blocks later replay."""
        with self._lock:
            if not self.enabled:
                raise SharedAnswerError("cache_disabled")
            identity = (self._trust.chain_id, _token(record_id))
            self._guard_call("revoke", identity)
            self._entries.pop(identity, None)
