"""Bounded protected conversation schemas; no persistence or SDK side effects."""

import copy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
import hashlib
import json
import re
from uuid import UUID, uuid5

from .conversation_store import ConversationStoreError
from .workflow_store import MAX_PAYLOAD, _payload


CONTROL_SCHEMA = "sage.lantern.conversation-control.v1"
SESSION_SCHEMA = "sage.lantern.conversation-session.v1"
NAMESPACE = UUID("cf712c35-e95e-4df0-a106-1c6f7785cf5b")
OPERATION_NAMESPACE = UUID("85fc645f-757a-4386-aaf5-f82076c47036")
STATES = {"queued", "dispatching", "unknown", "accepted", "recall_ready", "cancelled"}
STATUSES = {"proposed", "validated", "committed", "challenged", "deprecated", "committed_unconfirmed"}
DEPRECATION = {"pending", "challenged", "deprecated", "blocked_permission", "unknown"}


@dataclass(frozen=True)
class ConversationDrainSnapshot:
    storage_epoch: str
    queued: int
    dispatching: int
    unknown: int
    pending_media: int


@dataclass(frozen=True)
class MediaIntent:
    session_id: str
    intent_id: str
    digest: str
    store_generation: str
    history_generation: str | None
    actor_chain_id: str
    actor_id: str


def require(condition, reason="invalid_record"):
    if not condition:
        raise ConversationStoreError(reason)


def identifier(value):
    try:
        require(type(value) is str and len(value) == 36 and str(UUID(value)) == value and UUID(value).int != 0)
        return value
    except (ValueError, AttributeError):
        raise ConversationStoreError("invalid_identifier") from None


def token(value, maximum=256):
    require(type(value) is str and 1 <= len(value) <= maximum and value.isascii()
            and all(33 <= ord(character) <= 126 for character in value))
    return value


def digest(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return value


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checksum(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def record_id(binding, session_id=None):
    if session_id is not None:
        identifier(session_id)
    return str(uuid5(NAMESPACE, encode([binding, "control" if session_id is None else session_id])))


def operation_id(session_id, kind):
    identifier(session_id)
    require(kind in {"interaction", "reflection"})
    return str(uuid5(OPERATION_NAMESPACE, session_id + ":" + kind))


def history_generation(value):
    require(value is None or type(value) is str and 1 <= len(value) <= 128)


def validate_item(item):
    required = {"session_id", "created_at", "question", "answer", "has_photo"}
    require(type(item) is dict and required <= set(item) and set(item) <= required | {"reflection", "thread_id", "supersedes"})
    identifier(item["session_id"])
    for field in ('thread_id', 'supersedes'):
        if field in item:
            identifier(item[field])
    require('supersedes' not in item or 'thread_id' in item and item['supersedes'] != item['session_id'])
    require(type(item["has_photo"]) is bool and all(type(item[field]) is str for field in set(item) - {"has_photo"}))
    try:
        require(datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).utcoffset() is not None)
        require(len(encode(item).encode("utf-8")) <= 12000, "capacity_exceeded")
    except (ValueError, UnicodeError):
        raise ConversationStoreError("invalid_item") from None
    return copy.deepcopy(item)


def payload(item, kind):
    content = ({key: item[key] for key in ("session_id", "created_at", "question", "answer", "has_photo")}
               if kind == "interaction" else {key: item[key] for key in ("session_id", "created_at", "reflection")})
    # Optional metadata keeps old interaction payloads and operation identities intact.
    if kind == 'interaction':
        content.update({field: item[field] for field in ('thread_id', 'supersedes') if field in item})
    return encode({"version": 1, "kind": kind, **content})


def new_operation():
    return dict(state="queued", owner=None, journal_key=None, fingerprint=None, receipt=None,
                memory_id=None, sage_status=None, deprecation_status=None)


def validate_receipt(receipt, key):
    try:
        value = asdict(receipt) if is_dataclass(receipt) else dict(receipt) if type(receipt) is dict else dict(vars(receipt))
        fields = {"operation_key", "memory_id", "tx_hash", "status", "committed", "committed_height",
                  "projection_confirmed", "retryable", "outcome"}
        require(set(value) == fields and value["operation_key"] == digest(key)
                and value["outcome"] in {"acknowledged", "projection_observed"} and value["status"] in STATUSES)
        token(value["memory_id"])
        require(type(value["tx_hash"]) is str and len(value["tx_hash"]) <= 256 and value["tx_hash"].isascii())
        if value["tx_hash"]:
            token(value["tx_hash"])
        require(all(value[field] is None or type(value[field]) is bool for field in ("committed", "projection_confirmed", "retryable")))
        require(value["committed_height"] is None or type(value["committed_height"]) is int
                and 1 <= value["committed_height"] <= 2 ** 53 - 1)
        return value
    except (TypeError, ValueError):
        raise ConversationStoreError("invalid_receipt") from None


def validate_link(link):
    require(type(link) is dict and set(link) == {"object_id", "digest", "backend"})
    token(link["object_id"])
    digest(link["digest"])
    require(type(link["backend"]) is str and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", link["backend"]) is not None)
    return copy.deepcopy(link)


def capacity(value, *, reserve_receipts=False):
    candidate = copy.deepcopy(value)
    if reserve_receipts:
        for operation in candidate["operations"].values():
            operation.update(memory_id="\\" * 256, journal_key="a" * 64, fingerprint="b" * 64,
                             owner="00000000-0000-4000-8000-000000000001", deprecation_status="blocked_permission",
                             state="recall_ready", sage_status="committed_unconfirmed")
            operation["receipt"] = dict(operation_key="a" * 64, memory_id="\\" * 256, tx_hash="\\" * 256,
                status="committed_unconfirmed", committed=False, committed_height=2 ** 53 - 1,
                projection_confirmed=False, retryable=False, outcome="projection_observed")
        candidate["media"] = dict(link=dict(object_id="\\" * 256, digest="d" * 64, backend="b" * 128),
                                  state="pending", intent_id="00000000-0000-4000-8000-000000000001")
    try:
        detached, encoded = _payload(candidate)
        require(len(encoded.encode("ascii")) <= MAX_PAYLOAD, "capacity_exceeded")
    except Exception:
        raise ConversationStoreError("capacity_exceeded") from None


def validate_control(value, binding):
    require(type(value) is dict and set(value) == {"schema", "binding", "enabled", "generation", "history_generation",
                                                 "dispatch_epoch", "clear_epoch", "suppressed"})
    require(value["schema"] == CONTROL_SCHEMA and value["binding"] == binding and type(value["enabled"]) is bool)
    for field in ("generation", "dispatch_epoch", "clear_epoch"):
        identifier(value[field])
    history_generation(value["history_generation"])
    require(type(value["suppressed"]) is list and len(value["suppressed"]) <= 4096)
    require(value["suppressed"] == sorted(set(identifier(entry) for entry in value["suppressed"])))
    capacity(value)
    return value


def validate_session(value, binding):
    require(type(value) is dict and set(value) == {"schema", "binding", "session_id", "item", "item_hash",
        "consent_generation", "history_generation", "dispatch_epoch", "clear_epoch", "operations", "media"})
    require(value["schema"] == SESSION_SCHEMA and value["binding"] == binding)
    for field in ("session_id", "consent_generation", "dispatch_epoch", "clear_epoch"):
        identifier(value[field])
    history_generation(value["history_generation"])
    item = value["item"]
    require(type(value["operations"]) is dict)
    if item is None:
        require(value["item_hash"] is None and value["operations"] == {})
    else:
        validate_item(item)
        require(item["session_id"] == value["session_id"] and value["item_hash"] == checksum(item))
        require(set(value["operations"]) == ({"interaction", "reflection"} if "reflection" in item else {"interaction"}))
    for operation in value["operations"].values():
        require(type(operation) is dict and set(operation) == set(new_operation()) and operation["state"] in STATES)
        if operation["owner"] is not None:
            identifier(operation["owner"])
        require((operation["journal_key"] is None) == (operation["fingerprint"] is None))
        if operation["journal_key"] is not None:
            digest(operation["journal_key"])
            digest(operation["fingerprint"])
            require(operation["state"] != "queued")
        if operation["memory_id"] is not None:
            token(operation["memory_id"])
        require(operation["state"] not in {"accepted", "recall_ready"} or operation["memory_id"] is not None)
        require(operation["state"] != "dispatching" or operation["owner"] is not None)
        require(operation["sage_status"] is None or operation["sage_status"] in STATUSES)
        require(operation["deprecation_status"] is None or operation["deprecation_status"] in DEPRECATION)
        if operation["receipt"] is not None:
            receipt = validate_receipt(operation["receipt"], operation["journal_key"])
            require(receipt["memory_id"] == operation["memory_id"])
    if value["media"] is not None:
        media = value["media"]
        require(type(media) is dict and set(media) == {"link", "intent_id", "state"} and media["state"] in {"pending", "linked"})
        identifier(media["intent_id"])
        validate_link(media["link"])
    capacity(value)
    return value
