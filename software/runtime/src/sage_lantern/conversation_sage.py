"""Private, bounded conversation projection over the synchronous SAGE SDK.

The caller owns the client, its finite transport timeout, and a durable journal.
Journal reservations must be atomic across processes, survive restart, and never
be evicted while an operation can be retried. Reserve before sending; a timeout
or crash leaves an uncertain reservation requiring operator reconciliation.
Ordinary observations cannot use SAGE's task-only idempotency_key parameter.

Classification is access control, not encryption or exclusive user visibility.
The integrator must audit the node's grants, validators and export policy: this
adapter never configures federation and cannot prove that the node has none.
Forget challenges/deprecates serving state; immutable ledger copies may remain.
No SDK inception/reflect/delete methods are assumed. Reflection is supplied text,
not automatic inference. Tags are hints, never session identity or authority.
Provider is supplementary serving metadata, not canonical identity. Current
REST detail and hybrid responses omit it; recent-list projections may omit it
too. An absent provider requires an exact durable journal receipt or one bounded
signed provider-filtered list match, plus strict author/domain/private
classification and hashed conversation-envelope checks. Recall stays committed
only and verifies each unlabeled hit, sharing one evidence page per batch.
Provider-bearing list rows that omit classification require an exact GET with
explicit private classification and identical identity/content/hash/status.
This complementary check never defaults classification or recursively lists.
List content_hash comes from Go []byte JSON (canonical base64), unlike GET's
hex string. Only the list path normalizes a strict 32-byte digest; both paths
still verify the complete content bytes and reject absent or conflicting hashes.
Every generic context path must use exclude_from_general_context before
emitting raw content; this adapter cannot guard calls made around it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sage_sdk import SageClient


PROVIDER = "sage-lantern-conversation-v1"
SCHEMA = "sage.lantern.conversation.v1"
MAX_CONTENT_BYTES = 16384
MAX_PAGE_SIZE = 50
MAX_OFFSET = 7900
RETENTION_LIMIT = "Lifecycle withdrawal only; immutable ledger and replica copies may remain."


class ConversationSageError(RuntimeError):
    """Sanitized failure; never includes transcript or SDK error bodies."""


@dataclass(frozen=True)
class Submission:
    operation_key: str
    memory_id: str
    tx_hash: str
    status: str
    committed: bool | None
    committed_height: int | None
    projection_confirmed: bool | None
    retryable: bool | None
    outcome: str = "acknowledged"


@dataclass(frozen=True)
class SessionInput:
    session_id: str
    interaction: str
    reflection: str | None = None


@dataclass(frozen=True)
class SessionReceipt:
    interaction: Submission
    reflection: Submission | None = None


@dataclass(frozen=True)
class JournalEntry:
    fingerprint: str
    receipt: Submission | None = None


class SubmissionJournal(Protocol):
    """Caller-provided durable storage, with an atomic insert-if-absent."""

    def reserve(self, key: str, fingerprint: str) -> bool: ...
    def get(self, key: str) -> JournalEntry: ...
    def complete(self, key: str, receipt: Submission) -> None: ...


@dataclass(frozen=True)
class ConversationMemory:
    memory_id: str
    session_id: str
    event_id: str
    kind: str
    text: str
    status: str
    classification: int
    content_hash: str


@dataclass(frozen=True)
class HistoryPage:
    records: tuple[ConversationMemory, ...]
    next_offset: int | None
    has_more: bool | None


@dataclass(frozen=True)
class ForgetResult:
    memory_id: str
    memory_status: str | None
    tx_hash: str
    committed: bool | None = None
    erased: bool = False
    retention_limit: str = RETENTION_LIMIT


def _field(value: object, name: str, default: object = None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def exclude_from_general_context(record: object) -> bool:
    """Conservative exclusion gate, never authorization or a knowledge verdict.

    Missing provider must not route raw private conversation JSON into general
    knowledge. Reject conversation markers even under a different provider or
    inside malformed JSON; schema validation belongs to the conversation path.
    Unknown/oversized content is excluded rather than parsed without a bound.
    Callers still enforce domain/classification/status and live disclosure gates.
    """
    provider = _field(record, "provider")
    if not isinstance(provider, str) or not provider.strip() or provider == PROVIDER:
        return True
    try:
        content = _text(_field(record, "content"))
    except ConversationSageError:
        return True
    if "sage.lantern.conversation" in content or PROVIDER in content:
        return True
    if content.lstrip().startswith(("{", "[", '"')):
        try:
            normalized = json.dumps(json.loads(content), ensure_ascii=False)
        except (ValueError, RecursionError):
            return True
        return "sage.lantern.conversation" in normalized or PROVIDER in normalized
    return False


def _token(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ConversationSageError("invalid_identifier")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConversationSageError("invalid_content")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ConversationSageError("invalid_content") from None
    if size > MAX_CONTENT_BYTES:
        raise ConversationSageError("invalid_content")
    return value


def _optional_bool(value: object) -> bool | None:
    if value is not None and type(value) is not bool:
        raise ConversationSageError("invalid_response")
    return value


class ConversationSage:
    def __init__(
        self, client: SageClient, *, agent_id: str, domain: str,
        classification: int, journal: SubmissionJournal,
        is_tombstoned: Callable[[str], bool], ledger_consent: bool = False,
    ) -> None:
        self.client = client
        self.agent_id = _token(agent_id)
        self.domain = _token(domain)
        if domain in {"general", "self", "meta"} or domain.startswith(("sage-", "scope:")):
            raise ConversationSageError("ordinary_owned_domain_required")
        if type(classification) is not int or not 1 <= classification <= 4:
            raise ConversationSageError("private_classification_required")
        self.classification = classification
        self.journal = journal
        self.is_tombstoned = is_tombstoned
        self.ledger_consent = ledger_consent

    def _deleted(self, session_id: str) -> bool:
        try:
            deleted = self.is_tombstoned(session_id)
            if type(deleted) is not bool:
                raise ValueError()
            return deleted
        except Exception:
            raise ConversationSageError("tombstone_state_unavailable") from None

    def ensure_session(self, item: SessionInput) -> SessionReceipt:
        """At most two writes for a newly consented item, never history backfill.

        Call without holding the history lock. Persist returned IDs even when
        deletion raced the write, then enqueue withdrawal using the tombstone.
        Uncertain operations need reconcile(), not another network submission.
        """
        interaction = self.record_turn(item.session_id, "interaction", item.interaction)
        reflection = None
        if item.reflection is not None and interaction.outcome != "unknown" and not self._deleted(item.session_id):
            reflection = self.reflect(item.session_id, "reflection", item.reflection)
        return SessionReceipt(interaction, reflection)

    @staticmethod
    def draft_session(session_id: str, *, timestamp: str, transcript: str,
                      response: str, reflection: str | None = None) -> SessionInput:
        """Deterministic caller-owned draft; never reads/backfills local history."""
        try:
            moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError()
        except (AttributeError, TypeError, ValueError):
            raise ConversationSageError("invalid_timestamp") from None
        interaction = json.dumps(dict(timestamp=timestamp, transcript=_text(transcript),
                                      response=_text(response)), ensure_ascii=False,
                                 sort_keys=True, separators=(",", ":"))
        return SessionInput(_token(session_id), _text(interaction),
                            None if reflection is None else _text(reflection))

    def _call(self, method: str, **arguments):
        try:
            return getattr(self.client, method)(**arguments)
        except Exception:
            raise ConversationSageError("sage_operation_failed") from None

    def _authorize(self, *, write: bool = False) -> None:
        profile = self._call("get_profile")
        clearance = _field(profile, "clearance")
        if (
            _field(profile, "agent_id") != self.agent_id
            or _field(profile, "role") not in ("member", "manager")
            or _field(profile, "enrollment_status") != "active"
            or _field(profile, "registration_status") != "active"
            or _field(profile, "home_domain") != self.domain
            or _field(profile, "approval_required") is not False
            or _field(profile, "can_read") is not True
            or (write and _field(profile, "can_write") is not True)
            or type(clearance) is not int
            or not self.classification <= clearance <= 4
        ):
            raise ConversationSageError("private_access_unavailable")
        sample = self._call("domain_access_sample")
        required = ["owned_domains", "readable_domains"]
        if write:
            required.append("writable_domains")
        for name in required:
            domains = _field(sample, name)
            if not isinstance(domains, list) or self.domain not in domains:
                raise ConversationSageError("private_domain_grant_unavailable")

    def authorize_action(self, *, write: bool = True) -> bool:
        """Resolve the current SAGE profile/domain grant before an action."""
        self._authorize(write=write)
        return True

    def record_turn(self, session_id: str, event_id: str, text: str) -> Submission:
        """Persist one caller-redacted turn; stable event IDs must survive restart."""
        return self._submit(session_id, event_id, "turn", text)

    def reflect(self, session_id: str, event_id: str, text: str) -> Submission:
        """Persist caller-generated reflection as an observation, not a fact."""
        return self._submit(session_id, event_id, "reflection", text)

    def _submit(self, session_id: str, event_id: str, kind: str, text: str) -> Submission:
        if self.ledger_consent is not True:
            raise ConversationSageError("immutable_ledger_consent_required")
        if self._deleted(_token(session_id)):
            raise ConversationSageError("session_tombstoned")
        envelope = dict(schema=SCHEMA, session_id=_token(session_id),
                        event_id=_token(event_id), kind=kind, text=_text(text))
        content = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        _text(content)
        identity = [self.agent_id, self.domain, self.classification, session_id, kind, event_id]
        key = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        fingerprint = hashlib.sha256(content.encode()).hexdigest()
        self._authorize(write=True)
        try:
            fresh = self.journal.reserve(key, fingerprint)
            if type(fresh) is not bool:
                raise ConversationSageError("invalid_journal")
            if not fresh:
                entry = self.journal.get(key)
                if entry.fingerprint != fingerprint:
                    raise ConversationSageError("event_content_conflict")
                if entry.receipt is None:
                    return self._unknown(key)
                return entry.receipt
        except ConversationSageError:
            raise
        except Exception:
            raise ConversationSageError("journal_unavailable") from None
        try:
            embedding = self._call("embed", text=content)
            self._validate_embedding(embedding)
            response = self._call(
                "propose", content=content, memory_type="observation", domain_tag=self.domain,
                confidence=0.8, classification=self.classification, provider=PROVIDER,
                embedding=embedding,
            )
            receipt = self._receipt(key, response)
        except ConversationSageError:
            return self._unknown(key)
        try:
            self.journal.complete(key, receipt)
        except Exception:
            raise ConversationSageError("receipt_persistence_failed_reconcile_before_retry") from None
        return receipt

    @staticmethod
    def _unknown(key: str) -> Submission:
        return Submission(key, "", "", "unknown", None, None, None, False, "unknown")

    @staticmethod
    def _receipt(key: str, response: object) -> Submission:
        height = _field(response, "committed_height")
        if height is not None and (type(height) is not int or height < 1):
            raise ConversationSageError("invalid_response")
        return Submission(
            key, _token(_field(response, "memory_id")), _token(_field(response, "tx_hash")),
            _token(_field(response, "status")), _optional_bool(_field(response, "committed")),
            height, _optional_bool(_field(response, "projection_confirmed")),
            _optional_bool(_field(response, "retryable")),
        )

    def reconcile(self, session_id: str, event_id: str, text: str, *,
                  kind: str = "turn", offset: int = 0,
                  memory_id: str | None = None) -> Submission:
        """One exact bounded signed-list scan, including tombstoned sessions.

        No match is UNKNOWN, never permission to resend. Pagination is in
        MAX_PAGE_SIZE increments up to MAX_OFFSET. A match proves projection,
        not the original transaction hash/height. Parent schedules further scans.
        """
        if kind not in ("turn", "reflection"):
            raise ConversationSageError("invalid_kind")
        envelope = dict(schema=SCHEMA, session_id=_token(session_id),
                        event_id=_token(event_id), kind=kind, text=_text(text))
        content = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        _text(content)
        identity = [self.agent_id, self.domain, self.classification, session_id, kind, event_id]
        key = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        fingerprint = hashlib.sha256(content.encode()).hexdigest()
        try:
            entry = self.journal.get(key)
            if entry.fingerprint != fingerprint:
                raise ConversationSageError("event_content_conflict")
        except ConversationSageError:
            raise
        except Exception:
            raise ConversationSageError("journal_unavailable") from None
        if memory_id is None:
            records = self._history(session_id, limit=MAX_PAGE_SIZE, offset=offset,
                                    include_deleted=True).records
        else:
            self._authorize()
            record = self._exact_record(_token(memory_id), offset=offset)
            if record.memory_id != memory_id or record.session_id != session_id:
                raise ConversationSageError("reconciliation_conflict")
            records = (record,)
        matches = [row for row in records if row.event_id == event_id and row.kind == kind]
        if len(matches) > 1 or any(row.text != text or row.content_hash != fingerprint for row in matches):
            raise ConversationSageError("reconciliation_conflict")
        if not matches:
            return self._unknown(key)
        row = matches[0]
        receipt = Submission(key, row.memory_id, "", row.status, None, None, True, False,
                             "projection_observed")
        try:
            self.journal.complete(key, receipt)
        except Exception:
            raise ConversationSageError("receipt_persistence_failed_reconcile_before_retry") from None
        return receipt

    def _record(self, record: object, *, allow_absent_provider: bool = False) -> ConversationMemory:
        classification = _field(record, "classification")
        if (
            _field(record, "domain_tag") != self.domain
            or _field(record, "submitting_agent") != self.agent_id
            or (_field(record, "provider") != PROVIDER
                and not (allow_absent_provider and _field(record, "provider") is None))
            or type(classification) is not int or classification != self.classification
            or _field(record, "memory_type") != "observation"
        ):
            raise ConversationSageError("unsafe_memory_response")
        content = _text(_field(record, "content"))
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        if _field(record, "content_hash") != content_hash:
            raise ConversationSageError("invalid_content_hash")
        try:
            envelope = json.loads(content)
        except (ValueError, RecursionError):
            raise ConversationSageError("invalid_memory_envelope") from None
        if (not isinstance(envelope, dict)
                or set(envelope) != {"schema", "session_id", "event_id", "kind", "text"}
                or envelope["schema"] != SCHEMA
                or envelope["kind"] not in ("turn", "reflection")):
            raise ConversationSageError("invalid_memory_envelope")
        status = _field(record, "status")
        if status not in ("proposed", "validated", "committed", "challenged", "deprecated"):
            raise ConversationSageError("invalid_memory_status")
        return ConversationMemory(
            _token(_field(record, "memory_id")), _token(envelope["session_id"]),
            _token(envelope["event_id"]), envelope["kind"], _text(envelope["text"]),
            str(getattr(status, "value", status)), classification, content_hash,
        )

    def _exact_record(self, memory_id: str, *, offset: int = 0) -> ConversationMemory:
        self._bounds(MAX_PAGE_SIZE, offset)
        response = self._call("get_memory", memory_id=memory_id)
        row = self._record(response, allow_absent_provider=True)
        if row.memory_id != memory_id:
            raise ConversationSageError("unsafe_memory_response")
        if _field(response, "provider") == PROVIDER:
            return row
        if self._journal_attests(row):
            return row
        records = self._provider_page(offset=offset, memory_ids={memory_id})
        matches = [record for record in records if record.memory_id == memory_id]
        if (len(matches) != 1 or matches[0].content_hash != row.content_hash
                or matches[0].status != row.status):
            raise ConversationSageError("detail_provider_evidence_unavailable")
        return row

    def _journal_attests(self, row: ConversationMemory) -> bool:
        identity = [self.agent_id, self.domain, self.classification,
                    row.session_id, row.kind, row.event_id]
        key = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        try:
            entry = self.journal.get(key)
        except Exception:
            entry = None
        if entry is not None:
            if _field(entry, "fingerprint") != row.content_hash:
                raise ConversationSageError("detail_evidence_conflict")
            receipt = _field(entry, "receipt")
            if receipt is not None:
                if (_field(receipt, "operation_key") != key
                        or _field(receipt, "memory_id") != row.memory_id
                        or _field(receipt, "outcome") not in ("acknowledged", "projection_observed")):
                    raise ConversationSageError("detail_evidence_conflict")
                return True
        return False

    def _list_record(self, record: object, *, allow_absent_provider: bool = False) -> ConversationMemory:
        digest = _field(record, "content_hash")
        if not isinstance(digest, str):
            raise ConversationSageError("invalid_content_hash")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            try:
                if len(digest) != 44:
                    raise ValueError()
                decoded = base64.b64decode(digest, validate=True)
                if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != digest:
                    raise ValueError()
            except (ValueError, binascii.Error):
                raise ConversationSageError("invalid_content_hash") from None
            fields = ("memory_id", "content", "submitting_agent", "domain_tag", "memory_type",
                      "classification", "provider", "status")
            record = {name: _field(record, name) for name in fields}
            record["content_hash"] = decoded.hex()
        if _field(record, "classification") is not None:
            return self._record(record, allow_absent_provider=allow_absent_provider)
        if (_field(record, "provider") != PROVIDER
                or _field(record, "submitting_agent") != self.agent_id
                or _field(record, "domain_tag") != self.domain
                or _field(record, "memory_type") != "observation"):
            raise ConversationSageError("unsafe_memory_response")
        memory_id = _token(_field(record, "memory_id"))
        content = _text(_field(record, "content"))
        if _field(record, "content_hash") != hashlib.sha256(content.encode()).hexdigest():
            raise ConversationSageError("invalid_content_hash")
        canonical = self._call("get_memory", memory_id=memory_id)
        row = self._record(canonical, allow_absent_provider=True)
        for field in ("memory_id", "content", "content_hash", "submitting_agent",
                      "domain_tag", "memory_type", "status"):
            if _field(record, field) != _field(canonical, field):
                raise ConversationSageError("list_detail_evidence_conflict")
        return row

    def _provider_page(self, *, offset: int = 0,
                       memory_ids: set[str] | None = None) -> list[ConversationMemory]:
        evidence = self._call("list_memories", limit=MAX_PAGE_SIZE, offset=offset,
                              domain=self.domain, provider=PROVIDER,
                              agent=self.agent_id, sort="newest")
        rows = _field(evidence, "memories")
        if not isinstance(rows, list) or len(rows) > MAX_PAGE_SIZE:
            raise ConversationSageError("invalid_response")
        return [self._list_record(record) for record in rows
                if memory_ids is None or _token(_field(record, "memory_id")) in memory_ids]

    def _recall_records(self, rows: list, *, list_projection: bool = False) -> list[ConversationMemory]:
        normalize = self._list_record if list_projection else self._record
        records = [normalize(row, allow_absent_provider=True) for row in rows]
        if any(record.status != "committed" for record in records):
            raise ConversationSageError("uncommitted_recall_response")
        evidence = None
        for raw, record in zip(rows, records):
            if _field(raw, "provider") == PROVIDER or self._journal_attests(record):
                continue
            if evidence is None:
                evidence = self._provider_page(memory_ids={candidate.memory_id for candidate in records})
            matches = [candidate for candidate in evidence if candidate.memory_id == record.memory_id]
            if (len(matches) != 1 or matches[0].content_hash != record.content_hash
                    or matches[0].status != "committed"):
                raise ConversationSageError("recall_provider_evidence_unavailable")
        return records

    def history(self, session_id: str, *, limit: int = 20, offset: int = 0) -> HistoryPage:
        """One bounded projection page, not a complete or canonical transcript."""
        return self._history(session_id, limit=limit, offset=offset)

    def _history(self, session_id: str, *, limit: int, offset: int,
                 include_deleted: bool = False) -> HistoryPage:
        _token(session_id)
        self._bounds(limit, offset)
        if not include_deleted and self._deleted(session_id):
            return HistoryPage((), None, False)
        self._authorize()
        response = self._call("list_memories", limit=limit, offset=offset,
                              domain=self.domain, provider=PROVIDER,
                              agent=self.agent_id, sort="newest")
        rows = _field(response, "memories")
        if not isinstance(rows, list) or len(rows) > limit:
            raise ConversationSageError("invalid_response")
        records = tuple(self._list_record(row) for row in rows)
        if not include_deleted and self._deleted(session_id):
            return HistoryPage((), None, False)
        more = _optional_bool(_field(response, "has_more"))
        next_offset = offset + len(rows) if more is True and rows else None
        if next_offset is not None and next_offset > MAX_OFFSET:
            next_offset = None
        return HistoryPage(tuple(row for row in records if row.session_id == session_id),
                           next_offset, more)

    def recall(self, session_id: str, query: str, *, limit: int = 5) -> tuple[ConversationMemory, ...]:
        """Bounded committed-only recall; never falls back to public/global search."""
        _token(session_id)
        _text(query)
        self._bounds(limit, 0)
        if self._deleted(session_id):
            return ()
        self._authorize()
        embedding = self._call("embed", text=query)
        self._validate_embedding(embedding)
        response = self._call("hybrid", query=query, embedding=embedding,
                              domain_tag=self.domain, provider=PROVIDER,
                              top_k=limit, status_filter="committed")
        rows = _field(response, "results")
        if not isinstance(rows, list) or len(rows) > limit:
            raise ConversationSageError("invalid_response")
        records = tuple(self._recall_records(rows))
        if self._deleted(session_id):
            return ()
        if any(row.status != "committed" for row in records):
            raise ConversationSageError("uncommitted_recall_response")
        return tuple(row for row in records if row.session_id == session_id)

    def recall_conversations(self, query: str, *, suppressed_session_ids: set[str] | frozenset[str],
                             limit: int = 5, recent_fallback: bool = True) -> tuple[ConversationMemory, ...]:
        memories, _ = self.recall_conversations_with_embedding(
            query, suppressed_session_ids=suppressed_session_ids, limit=limit, recent_fallback=recent_fallback)
        return memories

    def recall_conversations_with_embedding(self, query: str, *, suppressed_session_ids: set[str] | frozenset[str],
                                           limit: int = 5, recent_fallback: bool = True
                                           ) -> tuple[tuple[ConversationMemory, ...], list[float]]:
        """Cross-session committed recall with one optional bounded recent page.

        Recent records fill spare slots for anaphora; they are not asserted to
        be semantic matches. The live tombstone callback is checked on every
        returned session after all network calls, in addition to the snapshot.
        The validated embedding is returned for reuse within this request only;
        it conveys no authorization for subsequent calls.
        """
        _text(query)
        self._bounds(limit, 0)
        if not isinstance(suppressed_session_ids, (set, frozenset)):
            raise ConversationSageError("suppression_set_required")
        self._authorize()
        embedding = self._call("embed", text=query)
        self._validate_embedding(embedding)
        response = self._call("hybrid", query=query, embedding=embedding,
                              domain_tag=self.domain, provider=PROVIDER,
                              top_k=limit, status_filter="committed")
        rows = _field(response, "results")
        if not isinstance(rows, list) or len(rows) > limit:
            raise ConversationSageError("invalid_response")
        candidates = self._recall_records(rows)
        if recent_fallback:
            recent = self._call("list_memories", limit=limit, offset=0, domain=self.domain,
                                provider=PROVIDER, agent=self.agent_id, sort="newest", status="committed")
            rows = _field(recent, "memories")
            if not isinstance(rows, list) or len(rows) > limit:
                raise ConversationSageError("invalid_response")
            candidates.extend(self._recall_records(rows, list_projection=True))
        if any(row.status != "committed" for row in candidates):
            raise ConversationSageError("uncommitted_recall_response")
        visible = {}
        for row in candidates:
            if row.session_id not in suppressed_session_ids and not self._deleted(row.session_id):
                visible.setdefault(row.memory_id, row)
        return tuple(visible.values())[:limit], embedding

    @staticmethod
    def _validate_embedding(embedding: object) -> None:
        if (not isinstance(embedding, list) or not 1 <= len(embedding) <= 4096
                or any(type(value) not in (float, int) or not -1e100 <= value <= 1e100
                       or not math.isfinite(value) for value in embedding)):
            raise ConversationSageError("invalid_embedding")

    def status(self, session_id: str, memory_id: str, *, offset: int = 0) -> ConversationMemory | None:
        """Exact projection read, not a transaction receipt or chain-erasure proof."""
        _token(session_id)
        _token(memory_id)
        if self._deleted(session_id):
            return None
        self._authorize()
        row = self._exact_record(memory_id, offset=offset)
        if row.memory_id != memory_id or row.session_id != session_id:
            raise ConversationSageError("unsafe_memory_response")
        return None if self._deleted(session_id) else row

    def forget(self, session_id: str, memory_id: str, *, offset: int = 0) -> ForgetResult:
        """Request withdrawal only after checking exact private record ownership."""
        self._authorize(write=True)
        _token(session_id)
        _token(memory_id)
        row = self._exact_record(memory_id, offset=offset)
        if row.memory_id != memory_id or row.session_id != session_id:
            raise ConversationSageError("memory_unconfirmed")
        if row.status == "deprecated":
            return ForgetResult(memory_id, "deprecated", "")
        response = self._call("forget", memory_id=memory_id,
                              reason="User requested private conversation withdrawal")
        status = _field(response, "status")
        if status not in (None, "", "challenged", "deprecated"):
            raise ConversationSageError("invalid_forget_response")
        return ForgetResult(memory_id, status or None, _token(_field(response, "tx_hash")), True)

    def forget_session(self, session_id: str, receipt: SessionReceipt) -> tuple[ForgetResult, ...]:
        """Withdraw both known IDs; partial/unknown work stays in parent's outbox."""
        submissions = [receipt.interaction]
        if receipt.reflection is not None:
            submissions.append(receipt.reflection)
        if any(not submission.memory_id for submission in submissions):
            raise ConversationSageError("submission_uncertain_reconcile_before_forget")
        return tuple(self.forget(session_id, submission.memory_id) for submission in submissions)

    @staticmethod
    def _bounds(limit: int, offset: int) -> None:
        if (type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE
                or type(offset) is not int or not 0 <= offset <= MAX_OFFSET):
            raise ConversationSageError("invalid_page_bounds")
