#!/opt/sage-lantern/venv/bin/python
"""Run Unit A's private PTT -> transcript -> screen-answer loop."""

from __future__ import annotations

import argparse
import base64
import binascii
from collections import OrderedDict
from contextlib import contextmanager
import hmac
import hashlib
import json
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from sage_lantern import device_access
from sage_lantern.session_context import SessionContext
from sage_lantern.active_conversation import ActiveConversation, MAX_INPUT_BYTES
from sage_lantern.answer_suggestions import validate_suggestions
from sage_lantern.transcript_repair import repair
from sage_lantern.inference_metrics import project as project_metrics
from sage_lantern.orchestrator import VoiceOrchestrator, validated_question
from sage_lantern.community_service import CommunityError, CommunityService, ROUTES as COMMUNITY_PATHS

try:
    from sage_lantern.history_media import HistoryMediaError, HistoryMediaStore
except (ImportError, OSError):
    HistoryMediaStore = None

    class HistoryMediaError(Exception):
        pass


def stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


LEASE_SECONDS = 4
MAX_REQUESTS = 256
CONTROL_ORIGINS = {"http://127.0.0.1:3100", "http://localhost:3100"}
CONTROL_HOSTS = {"127.0.0.1:3197", "localhost:3197"}
MAX_EVENT_BYTES = 1024 * 1024
REVIEW_PATHS = {"/review/submit", "/review/cancel"}
QUESTION_PATHS = {"/question/start"}
REVIEW_SECONDS = 300
CONVERSATION_PATHS = {"/conversation/state", "/conversation/select"}
HISTORY_PATHS = {"/history/list", "/history/settings", "/history/delete", "/history/clear", "/history/photo", "/history/context"}
HISTORY_MUTATIONS = {"/history/settings", "/history/delete", "/history/clear"}
COMMUNITY_MUTATIONS = frozenset({"/community/messages/send", "/community/messages/open", "/community/messages/rendered", "/community/shares/submit", "/community/shares/refresh"})
HISTORY_BYTES = 4 * 1024 * 1024
HISTORY_ENTRIES = 100
HISTORY_SECONDS = 30 * 24 * 60 * 60
MAX_PHOTO_BYTES = 512 * 1024
MAX_ORIGINAL_BYTES = 2 * 1024 * 1024
MAX_PHOTO_BODY = 4 * ((MAX_PHOTO_BYTES + 2) // 3) + 4 * ((MAX_ORIGINAL_BYTES + 2) // 3) + 512
PROGRESS_TRANSITIONS = {
    "starting": frozenset({"transcribing"}),
    "transcribing": frozenset({"recalling"}),
    "recalling": frozenset({"photo_analysis", "generating"}),
    "photo_analysis": frozenset({"answering", "speaking"}),
    "generating": frozenset({"answering", "speaking"}),
    "answering": frozenset({"speaking"}),
    "speaking": frozenset(),
}


def decode_photo(encoded, *, original=False):
    limit = MAX_ORIGINAL_BYTES if original else MAX_PHOTO_BYTES
    if not isinstance(encoded, str) or len(encoded) > 4 * ((limit + 2) // 3):
        raise ControlError(400, "invalid_photo")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ControlError(400, "invalid_photo") from None
    validate_photo(image, original=original)
    return image


def validate_photo(image, *, original=False):
    """Bound JPEG headers/dimensions, not a full decoder; runtime must decode it."""
    limit = MAX_ORIGINAL_BYTES if original else MAX_PHOTO_BYTES
    max_width, max_height, max_pixels = (1920, 1080, 1920 * 1080) if original else (640, 640, 640 * 480)
    if not isinstance(image, bytes) or not 4 <= len(image) <= limit or not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
        raise ControlError(400, "invalid_photo")
    offset = 2
    components = None
    while offset < len(image) - 2:
        if image[offset] != 0xff:
            break
        while offset < len(image) and image[offset] == 0xff:
            offset += 1
        if offset >= len(image):
            break
        marker = image[offset]
        offset += 1
        if marker in {0, 0xd8, 0xd9, 1} or 0xd0 <= marker <= 0xd7 or offset + 2 > len(image) - 2:
            break
        length = int.from_bytes(image[offset:offset + 2], "big")
        if length < 2 or offset + length > len(image) - 2:
            break
        segment = image[offset + 2:offset + length]
        offset += length
        if marker in {0xc0, 0xc2}:
            if components is not None or len(segment) < 6:
                break
            height = int.from_bytes(segment[1:3], "big")
            width = int.from_bytes(segment[3:5], "big")
            components = segment[5]
            if segment[0] != 8 or components not in {1, 3} or length != 8 + 3 * components or not 1 <= width <= max_width or not 1 <= height <= max_height or width * height > max_pixels:
                break
        elif 0xc0 <= marker <= 0xcf and marker not in {0xc4, 0xc8, 0xcc}:
            break
        elif marker == 0xda:
            if components is not None and segment and 1 <= segment[0] <= components and length == 6 + 2 * segment[0] and offset < len(image) - 2:
                return
            break
    raise ControlError(400, "invalid_photo")


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class ControlError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


class DeviceLocked(RuntimeError):
    def __init__(self):
        super().__init__("device_locked")


def lock_required():
    try:
        return device_access.lock_enabled()
    except Exception:
        return True


def token_authorized(token):
    try:
        return isinstance(token, str) and bool(token) and device_access.authorized(token)
    except Exception:
        return False


def capture_session_context(token):
    """Protected admissions require a broker context; legacy mode has none."""
    if not lock_required():
        return None
    try:
        context = device_access.session_context(token)
    except Exception:
        context = None
    if not isinstance(context, SessionContext):
        raise DeviceLocked()
    return context


def context_current(token, context):
    try:
        return (isinstance(context, SessionContext) and token_authorized(token)
                and device_access.session_context(token) == context)
    except Exception:
        return False


def require_history_media():
    if HistoryMediaStore is None or not callable(getattr(HistoryMediaStore, "prune", None)):
        raise ControlError(503, "history_media_unavailable")


class LocalHistory:
    """Shared local history, not private SAGE facts or public publication consent.

    Consent is captured before recording and rechecked at commit. Disabling
    preserves saved items and rotates consent to block in-flight saving. Explicit
    Photo preference changes, delete and clear also invalidate in-flight consent.
    Retention is pruned on access/save and during the voice loop's maintenance.
    One controller process owns this metadata directory. Media is written before
    metadata; durable metadata precedes pruning. An uncertain metadata save must
    never trigger deletion of potentially referenced media.

    Protected mode is explicitly injected, never selected as a fallback. Its
    conversation provider must reserve_media(session_id, digest, *, ticket,
    item, access_check) durably before PUT and commit_media(intent, *,
    access_check)->True atomically settle/link after consent/suppression checks.
    Both are lifecycle-tracked synchronous operations, not plaintext journals.
    Existing settings and legacy media remain separate; protected reads require
    a validated photo_reference and never consult the filesystem media directory.
    """

    def __init__(self, directory: Path, memory=None, *, media_mode="legacy", media_store=None):
        if media_mode not in {"legacy", "protected"} or (media_mode == "legacy" and media_store is not None):
            raise ValueError("invalid history media mode")
        if media_mode == "protected" and (media_store is None or memory is None):
            raise ValueError("protected history media provider required")
        self.directory = directory
        self.path = directory / "history.json"
        self.media_directory = directory / "photos"
        self.lock = threading.Lock()
        self.memory = memory
        self.media_mode = media_mode
        self.media_store = media_store

    def require_media(self):
        if self.media_mode == "protected":
            if not all(callable(getattr(self.media_store, method, None)) for method in ("put", "get")):
                raise ControlError(503, "history_media_unavailable")
        else:
            require_history_media()

    def media_guard(self, access_check):
        if not callable(access_check):
            raise DeviceLocked()
        def checked():
            if access_check() is not True:
                raise DeviceLocked()
            return True
        return checked

    @contextmanager
    def transaction(self):
        if not self.lock.acquire(timeout=1):
            raise ControlError(503, "history_unavailable")
        try:
            yield
        except DeviceLocked:
            raise
        except (OSError, ValueError, TypeError, OverflowError, RecursionError, HistoryMediaError, RuntimeError):
            raise ControlError(503, "history_unavailable") from None
        finally:
            self.lock.release()

    def encode(self, value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def prepare(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("unsafe history directory")

    def save(self, value):
        body = self.encode(value)
        if len(body) > HISTORY_BYTES:
            raise ValueError("history too large")
        descriptor, temporary = tempfile.mkstemp(prefix=".history-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def media(self):
        if self.media_mode == "protected":
            self.require_media()
            return self.media_store
        self.media_directory.mkdir(mode=0o700, exist_ok=True)
        return HistoryMediaStore(directory=self.media_directory.absolute())

    def prune_media(self, value):
        if self.media_mode == "protected":
            return
        if not self.media_directory.exists() and not self.media_directory.is_symlink():
            return
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        items = self.memory.items() if self.memory else value["items"]
        self.media().prune({item["session_id"] for item in items if item.get("has_photo", False)})

    def load(self):
        self.require_media()
        self.prepare()
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if self.memory:
                raise ValueError("history preferences missing; restore or initialize explicitly")
            value = {"version": 2, "enabled": True, "photos_enabled": True, "generation": str(uuid.uuid4()), "items": []}
            self.save(value)
            self.prune_media(value)
            return value
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise ValueError("unsafe history file")
            raw = stream.read(HISTORY_BYTES + 1)
        if len(raw) > HISTORY_BYTES:
            raise ValueError("history too large")
        value = json.loads(raw, object_pairs_hook=unique_json_object)
        if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] not in {1, 2}:
            raise ValueError("invalid history")
        migrated = value["version"] == 1
        legacy = migrated
        fields = {"version", "enabled", "generation", "items"} | (set() if migrated else {"photos_enabled"})
        if set(value) != fields or type(value["enabled"]) is not bool or not isinstance(value["items"], list) or (not migrated and type(value["photos_enabled"]) is not bool):
            raise ValueError("invalid history")
        if not isinstance(value["generation"], str):
            raise ValueError("invalid history generation")
        uuid.UUID(value["generation"])
        if migrated:
            value.update(version=2, photos_enabled=True, generation=str(uuid.uuid4()))
        now = time.time()
        retained = []
        seen = set()
        for item in value["items"]:
            fields = {"session_id", "created_at", "transcript", "response"}
            if (not isinstance(item, dict) or not fields <= set(item) or set(item) - fields - {"has_photo", "thread_id", "supersedes"}
                    or not all(isinstance(item[field], str) for field in fields)
                    or type(item.get("has_photo", False)) is not bool or (legacy and "has_photo" in item)):
                raise ValueError("invalid history item")
            if str(uuid.UUID(item["session_id"])) != item["session_id"]:
                raise ValueError("invalid history item")
            for field in ('thread_id', 'supersedes'):
                if field in item and (type(item[field]) is not str or str(uuid.UUID(item[field])) != item[field]):
                    raise ValueError('invalid history thread')
            if "has_photo" not in item:
                item["has_photo"] = False
                migrated = True
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            if created.tzinfo is None or any(len(item[field].encode("utf-8")) > MAX_EVENT_BYTES for field in ("transcript", "response")):
                raise ValueError("invalid history item")
            if now - HISTORY_SECONDS <= created.timestamp() <= now and item["session_id"] not in seen:
                retained.append(item)
                seen.add(item["session_id"])
        retained.sort(key=lambda item: datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).timestamp(), reverse=True)
        retained = retained[:HISTORY_ENTRIES]
        if migrated or retained != value["items"]:
            value["items"] = retained
            self.save(value)
        self.prune_media(value)
        return value

    def request(self, action, payload, *, access_check=None, context_selection=None):
        if action == "context":
            if set(payload) != {"query"} or not self.memory:
                raise ControlError(503, "conversation_memory_unavailable")
            if access_check is not None and access_check() is not True:
                raise ControlError(403, "device_locked")
            try:
                if context_selection is None:
                    result = self.memory.context(payload["query"])
                else:
                    result = self.memory.context(payload["query"],
                                                 thread_id=context_selection["thread_id"],
                                                 selected_items=context_selection["items"])
            except Exception:
                raise ControlError(503, "conversation_memory_unavailable") from None
            if access_check is not None and access_check() is not True:
                raise ControlError(403, "device_locked")
            return {"context": result}
        if context_selection is not None:
            raise ControlError(400, "invalid_history_payload")
        if action in {"list", "clear"}:
            if payload != {}:
                raise ControlError(400, "invalid_history_payload")
        elif action == "settings":
            if not payload or set(payload) - {"enabled", "photos_enabled"} or any(type(field) is not bool for field in payload.values()):
                raise ControlError(400, "invalid_history_payload")
        elif action in {"delete", "photo"}:
            if set(payload) != {"session_id"} or not isinstance(payload["session_id"], str):
                raise ControlError(400, "invalid_history_payload")
            try:
                session_id = str(uuid.UUID(payload["session_id"]))
                if session_id != payload["session_id"]:
                    raise ValueError
            except ValueError:
                raise ControlError(400, "invalid_history_payload") from None
        else:
            raise ControlError(404, "not_found")
        with self.transaction():
            value = self.load()
            if self.memory:
                value["items"] = self.memory.items()
            if action == "photo":
                if not any(item["session_id"] == session_id and item["has_photo"] for item in value["items"]):
                    raise ControlError(404, "history_photo_not_found")
                if self.media_mode == "protected":
                    guard = self.media_guard(access_check)
                    guard()
                    item = next(item for item in value["items"] if item["session_id"] == session_id)
                    reference = item.get("photo_reference")
                    if reference is None:
                        raise ControlError(404, "legacy_history_photo_unavailable")
                    if (type(reference) is not dict or set(reference) != {"object_id", "digest", "backend"}
                            or reference["object_id"] != session_id or reference["backend"] != "sage.private-media.v1"
                            or type(reference["digest"]) is not str or len(reference["digest"]) != 64
                            or any(character not in "0123456789abcdef" for character in reference["digest"])):
                        raise ControlError(503, "history_media_unavailable")
                    original = self.media().get(session_id, access_check=guard, expected_digest=reference["digest"])
                    guard()
                    current = self.memory.items()
                    if not any(item["session_id"] == session_id and item.get("has_photo") is True
                               and item.get("photo_reference") == reference for item in current):
                        raise ControlError(404, "history_photo_not_found")
                    response = {"image_base64": base64.b64encode(original).decode("ascii")}
                    guard()
                    return response
                return {"image_base64": base64.b64encode(self.media().get(session_id)).decode("ascii")}
            changed = False
            if action == "settings" and any(value[field] != setting for field, setting in payload.items()):
                value.update(payload)
                value["generation"] = str(uuid.uuid4())
                changed = True
            elif action == "clear":
                if self.memory:
                    self.memory.store.suppress([item["session_id"] for item in value["items"]])
                value["items"] = []
                value["generation"] = str(uuid.uuid4())
                changed = True
            elif action == "delete":
                if self.memory:
                    self.memory.store.suppress([session_id])
                items = [item for item in value["items"] if item["session_id"] != session_id]
                changed = True
                value["generation"] = str(uuid.uuid4())
                value["items"] = items
            if changed:
                if self.memory:
                    self.memory.store.approve(value["enabled"], history_generation=value["generation"])
                    self.save({**value, "items": []})
                else:
                    self.save(value)
                self.prune_media(value)
            return {"enabled": value["enabled"], "photos_enabled": value["photos_enabled"],
                    "items": value["items"][:100], "saving_off_keeps_items": True, "photo_saving_supported": True,
                    **({"photo_storage": "sage_immutable", "photo_removal": "history_link_only"}
                       if self.media_mode == "protected" else {}),
                    **({"sage_backed": True, "memory_warning": self.memory.last_error} if self.memory else {})}

    def consent(self):
        with self.transaction():
            value = self.load()
            if self.memory:
                self.memory.store.approve(value["enabled"], history_generation=value["generation"])
                return self.memory.store.consent()
            return value["generation"] if value["enabled"] else None

    def record(self, consent, session_id, result, *, original=None, access_check=None):
        if consent is None:
            return
        with self.transaction():
            if self.media_mode == "protected":
                access_check = self.media_guard(access_check)
            if access_check is not None:
                access_check()
            value = self.load()
            expected = self.memory.store.consent() if self.memory else value["generation"]
            if not value["enabled"] or consent != expected:
                return
            session_id = str(uuid.UUID(session_id))
            if any(item["session_id"] == session_id for item in value["items"]):
                return
            if any(not isinstance(result.get(field), str) or len(result[field].encode("utf-8")) > MAX_EVENT_BYTES for field in ("transcript", "response")):
                raise ValueError("invalid history result")
            metadata = {field: result[field] for field in ('thread_id', 'supersedes') if field in result}
            for entry in metadata.values():
                if type(entry) is not str or str(uuid.UUID(entry)) != entry:
                    raise ValueError('invalid history thread')
            has_photo = original is not None and value["photos_enabled"]
            if access_check is not None:
                access_check()
            if has_photo:
                validate_photo(original, original=True)
                if self.media_mode == "protected":
                    if not all(callable(getattr(self.memory, method, None)) for method in ("reserve_media", "commit_media")):
                        raise ControlError(503, "protected_photo_link_unavailable")
                    item = {"session_id": session_id, "created_at": stamp(), "question": result["transcript"],
                            "answer": result["response"], "has_photo": True, **metadata}
                    reference = {"object_id": session_id, "digest": hashlib.sha256(original).hexdigest(),
                                 "backend": "sage.private-media.v1"}
                    intent = self.memory.reserve_media(session_id, reference["digest"], ticket=consent,
                                                       item=item, access_check=access_check)
                    if intent is None or type(intent) is bool:
                        raise ControlError(503, "protected_photo_link_unavailable")
                    access_check()
                    digest = self.media().put(session_id, original, access_check=access_check)
                    if digest != reference["digest"]:
                        raise ControlError(503, "history_media_unavailable")
                    access_check()
                    if self.memory.commit_media(intent, access_check=access_check) is not True:
                        raise ControlError(503, "protected_photo_link_unavailable")
                    access_check()
                    return
                self.media().put(session_id, original)
                directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            if access_check is not None:
                access_check()
            if self.memory:
                self.memory.record(consent, {"session_id": session_id, "created_at": stamp(),
                                            "question": result["transcript"], "answer": result["response"],
                                            "has_photo": has_photo, **metadata})
                return
            value["items"].insert(0, {
                "session_id": session_id, "created_at": stamp(),
                "transcript": result["transcript"], "response": result["response"],
                "has_photo": has_photo, **metadata,
            })
            value["items"] = value["items"][:HISTORY_ENTRIES]
            while len(self.encode(value)) > HISTORY_BYTES and value["items"]:
                value["items"].pop()
            if access_check is not None:
                access_check()
            self.save(value)
            self.prune_media(value)


class VoiceControl:
    """One owner through recording and processing; last 256 request IDs replay-protected.

    Touch disconnects expire through the four-second lease. Stop commits input;
    once processing starts, control calls return 409 rather than implying that
    an already-submitted inference can be cancelled. Tombstones are process-local.
    """

    def __init__(self, max_seconds: float, *, photo_ready=False, photo_dir=None, dictation_available=None, mesh_provider=None):
        self.lock = threading.Lock()
        self.owner = None
        self.requests = OrderedDict()
        self.max_seconds = max_seconds
        self.photo_ready = photo_ready is True and photo_dir is not None
        self.photo_dir = photo_dir
        self.device_token = None
        self.device_context = None
        self.history_media_check = require_history_media
        self.closing = threading.Event()
        self.message_result = None
        self.completed_source = None
        self.correction_epoch = 0
        self.conversation = ActiveConversation()
        self.correction_available = lambda: False
        self.review_available = lambda: False
        self.typed_available = lambda: False
        self.correction_journal = None
        self.source_session_id = None
        self.community = CommunityService(dictation_available=dictation_available, source_fetch=self.community_source,
                                          mesh_provider=mesh_provider)
        self.community_token = None
        self.community_context = None
        self.community_epoch = 0
        self.community_cleanup_failed = False
        self.community_check_at = 0

    def quiesce(self):
        self.closing.set()
        self.revoke_device()

    def require_open(self):
        if self.closing.is_set():
            raise ControlError(503, "voice_quiescing")

    def link_device(self, token):
        if lock_required() and not token_authorized(token):
            raise ControlError(403, "device_locked")
        context = capture_session_context(token)
        with self.lock:
            self.require_open()
            if self.device_token != token or self.device_context != context:
                self.conversation.clear()
                self.completed_source = None
                self.source_session_id = None
                self.correction_epoch += 1
            self.device_token = token
            self.device_context = context

    def revoke_device(self, *, clear_community=True):
        with self.lock:
            self.correction_epoch += 1
            self.conversation.clear()
            self.device_token = None
            self.device_context = None
            self.message_result = None
            self.completed_source = None
            self.source_session_id = None
            if self.owner:
                self.owner["action"] = "cancel"
            if clear_community:
                self.community_token = None
                self.community_context = None
                self.community_epoch += 1
        if clear_community:
            try:
                self.community.lock()
            except Exception:
                self.community_cleanup_failed = True

    def community_access(self, token):
        """Capture the original auth token and epoch; never grant a PTT link."""
        if not token_authorized(token):
            self.revoke_device()
            raise ControlError(403, "device_locked")
        context = capture_session_context(token)
        with self.lock:
            self.require_open()
            if self.community_cleanup_failed:
                raise ControlError(503, "community_unavailable")
            changed = self.community_token != token or self.community_context != context
            if changed:
                self.community_epoch += 1
                self.community_token = token
                self.community_context = context
                if self.source_session_id != hashlib.sha256(token.encode("utf-8")).hexdigest() or self.device_context != context:
                    self.completed_source = None
                    self.source_session_id = None
            epoch = self.community_epoch
        if changed:
            try:
                self.community.lock()
            except Exception:
                self.community_cleanup_failed = True
                raise ControlError(503, "community_unavailable") from None

        def access_check():
            with self.lock:
                current = not self.closing.is_set() and not self.community_cleanup_failed and self.community_epoch == epoch and self.community_token == token
            if not current:
                return False
            if not token_authorized(token) or (context is not None and not context_current(token, context)):
                self.revoke_device()
                return False
            with self.lock:
                return not self.closing.is_set() and not self.community_cleanup_failed and self.community_epoch == epoch and self.community_token == token

        return access_check

    def expire_community_access(self):
        with self.lock:
            token = self.community_token
            context = self.community_context
        if token is not None and time.monotonic() >= self.community_check_at:
            self.community_check_at = time.monotonic() + 0.5
            if not token_authorized(token) or (context is not None and not context_current(token, context)):
                self.revoke_device()

    def community_source(self, source_id):
        """Resolve only the requested completed source under captured authorization."""
        with self.lock:
            source = self.completed_source
            token = self.community_token
            epoch = self.community_epoch
            context = self.community_context
            if self.closing.is_set() or token is None:
                raise CommunityError("not_found", 404)
            latest = (source is not None and source["source_id"] == source_id
                      and self.device_context == context
                      and self.source_session_id == hashlib.sha256(token.encode("utf-8")).hexdigest())
            snapshot = {key: source[key] for key in ("question", "answer", "has_photo")} if latest else None
            history = getattr(self, "protected_history", None)
        def check():
            with self.lock:
                current = (not self.closing.is_set() and not self.community_cleanup_failed
                           and self.community_epoch == epoch and self.community_token == token)
            if not current or not token_authorized(token) or (context is not None and not context_current(token, context)):
                raise CommunityError("device_locked", 403)
        check()
        if snapshot is None:
            if history is None or getattr(history, "protected_storage", False) is not True:
                raise CommunityError("not_found", 404)
            matches = [item for item in history.items() if item["session_id"] == source_id]
            check()
            if len(matches) != 1:
                raise CommunityError("not_found", 404)
            item = matches[0]
            snapshot = dict(question=item["transcript"], answer=item["response"], has_photo=item["has_photo"])
        check()
        return snapshot

    def source_photo(self, session_id):
        with self.lock:
            owner = self.owner
            return bool(owner and owner["session_id"] == session_id and owner.get("purpose", "answer") == "answer" and owner.get("image_digest") is not None)

    def remember_completed_source(self, session_id, question, answer, device_token):
        from sage_lantern.community_service import _text
        if (type(session_id) is not str or not _text(question, 4096) or not _text(answer, 12000)
                or not token_authorized(device_token)):
            return
        try:
            if str(uuid.UUID(session_id)) != session_id:
                return
        except ValueError:
            return
        with self.lock:
            owner = self.owner
            if (self.closing.is_set() or not owner or owner["session_id"] != session_id
                    or owner.get("purpose", "answer") != "answer" or owner["phase"] != "processing"
                    or owner.get("action") == "cancel" or owner.get("device_token") != device_token):
                return
            if lock_required() and not context_current(device_token, owner.get('session_context')):
                return
            self.completed_source = {"source_id": session_id, "question": question,
                                     "answer": answer, "has_photo": owner.get("image_digest") is not None,
                                     "correction_photo": owner.get("correction_photo")}
            self.source_session_id = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
            self.conversation.commit(owner.get('conversation'), session_id, question, answer,
                                     has_photo=owner.get('image_digest') is not None)

    def conversation_messages(self, session_id):
        with self.lock:
            owner = self.owner
            if (self.closing.is_set() or not owner or owner['session_id'] != session_id
                    or owner['phase'] != 'processing' or owner.get('action') == 'cancel'
                    or owner.get('purpose', 'answer') != 'answer'):
                return []
            snapshot = owner.get('conversation')
            token = owner.get('device_token')
            context = owner.get('session_context')
        if not token_authorized(token) or (lock_required() and not context_current(token, context)):
            self.revoke_device()
            return []
        with self.lock:
            if (self.owner is not owner or owner.get('action') == 'cancel'
                    or snapshot is None or snapshot.revision != self.conversation.revision
                    or self.closing.is_set()):
                return []
            return snapshot.messages()

    def conversation_metadata(self, session_id):
        with self.lock:
            owner = self.owner
            snapshot = owner.get('conversation') if owner and owner['session_id'] == session_id else None
            if snapshot is None or snapshot.revision != self.conversation.revision:
                return {}
            return {'thread_id': snapshot.thread_id,
                    **({'supersedes': snapshot.supersedes} if snapshot.supersedes else {})}

    def conversation_context_selection(self, token):
        """Bind inference recall to its frozen branch without accepting client scope."""
        with self.lock:
            owner = self.owner
            if (owner is None or owner.get('phase') != 'processing'
                    or owner.get('purpose', 'answer') != 'answer'):
                return None
            snapshot = owner.get('conversation')
            context = owner.get('session_context')

        def current():
            if not token_authorized(token) or (lock_required() and not context_current(token, context)):
                return False
            with self.lock:
                return (not self.closing.is_set() and self.owner is owner
                        and owner.get('conversation') is snapshot
                        and owner.get('device_token') == token
                        and owner.get('phase') == 'processing' and owner.get('action') != 'cancel'
                        and snapshot is not None and snapshot.revision == self.conversation.revision)

        if not current():
            raise ControlError(409, 'conversation_changed')
        selection = {'thread_id': snapshot.thread_id,
                     'items': [turn.item(snapshot.thread_id) for turn in snapshot.turns]}
        return selection, current

    def conversation_request(self, path, payload, token, history):
        if not token_authorized(token):
            self.revoke_device()
            raise ControlError(403, 'device_locked')
        context = capture_session_context(token)
        with self.lock:
            self.require_open()
            if self.device_context != context:
                if self.owner:
                    raise DeviceLocked()
                self.conversation.clear()
                self.completed_source = None
                self.source_session_id = None
                self.correction_epoch += 1
                self.device_context = context
            epoch = self.correction_epoch
            if path == '/conversation/select' and self.owner:
                raise ControlError(409, 'voice_busy')
        items = None
        if path == '/conversation/select':
            try:
                if set(payload) != {'thread_id'} or str(uuid.UUID(payload['thread_id'])) != payload['thread_id']:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise ControlError(400, 'invalid_thread') from None
            if history is None:
                raise ControlError(503, 'history_unavailable')
            def access():
                return not self.closing.is_set() and token_authorized(token) is True
            rows = history.request('list', {}, access_check=access)['items']
            items = [row for row in rows if row.get('thread_id', row['session_id']) == payload['thread_id']]
            if not items:
                raise ControlError(404, 'thread_unavailable')
        elif payload != {}:
            raise ControlError(400, 'invalid_payload')
        if not token_authorized(token):
            self.revoke_device()
            raise ControlError(403, 'device_locked')
        with self.lock:
            self.require_open()
            if self.correction_epoch != epoch:
                raise ControlError(409, 'conversation_changed')
            if items is not None:
                if self.owner:
                    raise ControlError(409, 'voice_busy')
                self.conversation.restore(token, payload['thread_id'], items)
                self.device_token = token
                self.device_context = context
                self.completed_source = None
                self.source_session_id = None
                self.correction_epoch += 1
            # Bind empty state to this authenticated device session.
            elif self.owner is None:
                self.conversation.prepare(token)
            owner = self.owner
            snapshot = owner.get('conversation') if owner else None
            if snapshot is not None and owner.get('device_token') != token:
                raise ControlError(403, 'device_locked')
            thread_id = snapshot.thread_id if snapshot is not None else self.conversation.thread_id
            turns = snapshot.turns if snapshot is not None and snapshot.revision == self.conversation.revision else self.conversation.turns
            return {'thread_id': thread_id, 'revision': self.conversation.revision,
                    'active_session_id': owner['session_id'] if snapshot is not None else None,
                    'items': [turn.item(thread_id) for turn in turns], 'context_turn_limit': 4}, self.correction_epoch

    def session_device_token(self, session_id):
        with self.lock:
            if self.owner and self.owner["session_id"] == session_id:
                return self.owner.get("device_token")
        return None

    def session_context_for(self, session_id):
        with self.lock:
            if self.owner and self.owner["session_id"] == session_id:
                return self.owner.get("session_context")
        return None

    def _expire(self):
        if self.message_result and time.monotonic() >= self.message_result["expires"]:
            self.message_result = None
        owner = self.owner
        if owner and owner["phase"] == "review":
            if time.monotonic() >= owner["review_deadline"]:
                owner["action"] = "cancel"
            return
        if (owner and owner["source"] == "typed" and owner["phase"] in {"queued", "recording"}
                and time.monotonic() >= owner["limit"]):
            # A typed question is picked up by the controller immediately; this
            # is only the safety net that stops a lost session from holding the
            # device busy forever. The review deadline governs after this.
            owner["action"] = "cancel"
            return
        if owner and owner["source"] == "touch" and owner["phase"] != "processing" and owner["action"] != "stop":
            now = time.monotonic()
            if now >= owner["lease"] or now >= owner["limit"]:
                owner["action"] = "cancel"

    def begin_review(self, session_id, transcript):
        transcript = validated_question(transcript)
        with self.lock:
            self.require_open()
            owner = self.owner
            if not owner or owner['session_id'] != session_id or owner['action'] == 'cancel':
                raise ControlError(409, 'session_cancelled')
            if owner['phase'] != 'processing':
                raise ControlError(409, 'review_unavailable')
            owner.update(phase='review', action=None, review_id=str(uuid.uuid4()),
                         review_deadline=time.monotonic() + REVIEW_SECONDS)
            return owner['review_id']

    def open_typed_question(self, request_id, device_token, image=None, original=None):
        """Open a question session that will never capture audio.

        A device without a microphone (or without a working capture path) must
        stay usable: the typing lane opens the same review/submit path the
        spoken lane uses, so one validated pipeline answers typed text. The
        session carries no transcript until the review is submitted; the caller
        never sees another session's text and the device lock still governs it.
        An attached photo is held exactly as the spoken lane holds it — the
        question is typed instead of recorded, the image path is identical.
        """
        if not token_authorized(device_token):
            raise ControlError(403, 'device_locked')
        if self.typed_available() is not True:
            raise ControlError(503, 'typed_unavailable')
        if original is not None:
            self.history_media_check()
            if image is None:
                raise ControlError(400, 'original_requires_image')
            validate_photo(original, original=True)
        if image is not None:
            if not self.photo_ready:
                raise ControlError(503, 'photo_unavailable')
            validate_photo(image)
        digest = hashlib.sha256(image).hexdigest() if image is not None else None
        original_digest = hashlib.sha256(original).hexdigest() if original is not None else None
        context = capture_session_context(device_token)
        with self.lock:
            self.require_open()
            self._expire()
            if request_id in self.requests:
                owner = self.owner
                if (owner is not None and owner.get('source') == 'typed'
                        and owner.get('request_id') == request_id
                        and owner.get('device_token') == device_token
                        and owner.get('session_context') == context
                        and owner.get('image_digest') == digest
                        and owner.get('original_digest') == original_digest):
                    return 200, self._credentials()
                raise ControlError(409, 'stale_request')
            if self.owner:
                raise ControlError(409, 'voice_busy')
            now = time.monotonic()
            if self.device_context != context:
                self.conversation.clear()
            self.owner = {
                "purpose": "answer", "conversation": self.conversation.prepare(device_token),
                "question": None, "source_id": None, "correction_fingerprint": None,
                "followup_source": None, "request_id": request_id, "source": "typed",
                "phase": "uploading" if image is not None else "queued", "action": None,
                "session_id": str(uuid.uuid4()), "token": secrets.token_urlsafe(32),
                "lease": now + LEASE_SECONDS, "limit": now + self.max_seconds,
                "image_digest": digest, "image_path": None,
                "original_digest": original_digest, "original": original,
                "device_token": device_token,
                "session_context": context,
            }
            self.device_token = device_token
            self.device_context = context
            self.message_result = None
            self.completed_source = None
            self.source_session_id = None
            self.requests[request_id] = self.owner["session_id"]
            while len(self.requests) > MAX_REQUESTS:
                self.requests.popitem(last=False)
            credentials = self._credentials()
        if image is None:
            return 201, credentials
        path = None
        try:
            self.photo_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.photo_dir.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise OSError("unsafe photo directory")
            descriptor, name = tempfile.mkstemp(prefix="photo-", suffix=".jpg", dir=self.photo_dir)
            path = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(image)
            with self.lock:
                self._expire()
                if (not self.owner or self.owner["session_id"] != credentials["session_id"]
                        or self.owner["action"] == "cancel"):
                    raise ControlError(409, "session_cancelled")
                self.owner["image_path"] = path
                self.owner["phase"] = "queued"
                now = time.monotonic()
                self.owner["lease"] = now + LEASE_SECONDS
                self.owner["limit"] = now + self.max_seconds
            return 201, credentials
        except BaseException as error:
            try:
                if path is not None:
                    path.unlink(missing_ok=True)
            finally:
                self.finish(credentials["session_id"])
            if isinstance(error, OSError):
                raise ControlError(503, "photo_storage_unavailable") from None
            raise

    def begin_typed_review(self, session_id):
        """Enter the review phase with no transcript; only typed text follows."""
        with self.lock:
            self.require_open()
            self._expire()
            owner = self.owner
            if (not owner or owner['session_id'] != session_id
                    or owner.get('source') != 'typed' or owner['action'] == 'cancel'):
                raise ControlError(409, 'session_cancelled')
            if owner['phase'] not in {'queued', 'recording'}:
                raise ControlError(409, 'review_unavailable')
            owner.update(phase='review', action=None, review_id=str(uuid.uuid4()),
                         review_deadline=time.monotonic() + REVIEW_SECONDS)
            return owner['review_id']

    def review_command(self, action, payload, device_token):
        if not token_authorized(device_token):
            raise ControlError(403, 'device_locked')
        fields = {'session_id', 'review_id'} | ({'question', 'request_id'} if action == 'submit' else set())
        if action not in {'submit', 'cancel'} or set(payload) != fields:
            raise ControlError(400, 'invalid_review')
        try:
            for field in fields - {'question'}:
                if type(payload[field]) is not str or str(uuid.UUID(payload[field])) != payload[field]:
                    raise ValueError()
            if action == 'submit':
                validated_question(payload['question'])
        except (ValueError, UnicodeError, AttributeError):
            raise ControlError(400, 'invalid_review') from None
        with self.lock:
            self.require_open()
            self._expire()
            owner = self.owner
            if (not owner or owner['session_id'] != payload['session_id']
                    or owner.get('review_id') != payload['review_id']
                    or owner.get('device_token') != device_token):
                raise ControlError(409, 'review_changed')
            if lock_required() and not context_current(device_token, owner.get('session_context')):
                raise DeviceLocked()
            if owner['action'] == 'cancel':
                if action == 'cancel':
                    return {'session_id': owner['session_id'], 'status': 'cancelling'}
                raise ControlError(409, 'review_expired')
            if owner.get('review_request_id'):
                if (action == 'submit' and owner['review_request_id'] == payload['request_id']
                        and owner['review_question'] == payload['question']):
                    return {'session_id': owner['session_id'], 'status': 'submitted'}
                raise ControlError(409, 'review_already_submitted')
            if owner['phase'] != 'review':
                raise ControlError(409, 'review_unavailable')
            if action == 'cancel':
                owner['action'] = 'cancel'
                return {'session_id': owner['session_id'], 'status': 'cancelling'}
            owner.update(review_request_id=payload['request_id'], review_question=payload['question'],
                         phase='processing', action='stop')
            return {'session_id': owner['session_id'], 'status': 'submitted'}

    def reviewed_question(self, session_id):
        with self.lock:
            self._expire()
            owner = self.owner
            if not owner or owner['session_id'] != session_id or owner['action'] == 'cancel':
                raise ControlError(409, 'session_cancelled')
            return owner.get('review_question')

    def followup(self, payload, device_token):
        """Submit an edited suggestion into the same live conversation, once."""
        if (type(payload) is not dict
                or set(payload) != {'request_id', 'source_id', 'question'}):
            raise ControlError(400, 'invalid_followup')
        try:
            for key in ('request_id', 'source_id'):
                if type(payload[key]) is not str or str(uuid.UUID(payload[key])) != payload[key]:
                    raise ValueError()
            question = validated_question(payload['question'])
        except (ValueError, UnicodeError, AttributeError):
            raise ControlError(400, 'invalid_followup') from None
        if not token_authorized(device_token):
            raise ControlError(403, 'device_locked')
        with self.lock:
            self.require_open()
            epoch = self.correction_epoch
        return self.start(payload['request_id'], device_token=device_token, question=question,
                          correction_epoch=epoch, followup_source=payload['source_id'])

    def retain_correction_photo(self, session_id):
        with self.lock:
            owner = self.owner
            if not owner or owner['session_id'] != session_id or owner.get('action') == 'cancel':
                return
            path = owner.get('image_path')
            if path is not None:
                with path.open('rb') as stream:
                    image = stream.read(MAX_PHOTO_BYTES + 1)
                validate_photo(image)
                if hashlib.sha256(image).hexdigest() != owner['image_digest']:
                    raise ControlError(503, 'photo_changed')
                owner['correction_photo'] = (image, owner.get('original'))

    def correct(self, payload, device_token, history=None):
        if self.correction_journal is None:
            raise ControlError(503, 'correction_journal_unavailable')
        if type(payload) is not dict or set(payload) not in (
                {'request_id', 'source_id', 'source_kind', 'question', 'new_execution'},
                {'request_id', 'source_id', 'source_kind', 'question', 'image_base64', 'new_execution'}):
            raise ControlError(400, 'invalid_correction')
        if type(payload['source_kind']) is not str or payload['source_kind'] not in {'current', 'history'}:
            raise ControlError(400, 'invalid_correction')
        try:
            question = validated_question(payload['question'])
            for key in ('request_id', 'source_id'):
                if type(payload[key]) is not str or str(uuid.UUID(payload[key])) != payload[key]:
                    raise ValueError()
        except (ValueError, UnicodeError):
            raise ControlError(400, 'invalid_correction') from None
        if not token_authorized(device_token):
            raise ControlError(403, 'device_locked')
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        context = capture_session_context(device_token)
        with self.lock:
            self.require_open()
            if payload['request_id'] in self.requests:
                raise ControlError(409, 'correction_reconciliation_required')
            if self.owner:
                raise ControlError(409, 'processing_busy' if self.owner['phase'] == 'processing' else 'voice_busy')
            source = self.completed_source
            latest = (source is not None and source['source_id'] == payload['source_id']
                      and self.device_context == context
                      and self.source_session_id == hashlib.sha256(device_token.encode()).hexdigest())
            source = dict(source) if latest and payload['source_kind'] == 'current' else None
            if payload['source_kind'] == 'current' and source is None:
                raise ControlError(404, 'correction_source_unavailable')
            correction_epoch = self.correction_epoch

        def check():
            if self.closing.is_set() or not token_authorized(device_token):
                raise ControlError(403, 'device_locked')
            return True

        image = original = None
        if payload['source_kind'] == 'history':
            if history is None or getattr(history, 'media_mode', None) != 'protected':
                raise ControlError(404, 'correction_source_unavailable')
            check()
            matches = [item for item in history.request('list', {}, access_check=check)['items']
                       if item['session_id'] == payload['source_id']]
            check()
            if len(matches) != 1:
                raise ControlError(404, 'correction_source_unavailable')
            source = matches[0]
            if source['has_photo']:
                photo = history.request('photo', {'session_id': payload['source_id']}, access_check=check)
                original = decode_photo(photo['image_base64'], original=True)
                if 'image_base64' not in payload:
                    raise ControlError(409, 'correction_preview_required')
                image = decode_photo(payload['image_base64'])
        elif source['has_photo']:
            if source.get('correction_photo') is None:
                raise ControlError(409, 'correction_photo_unavailable')
            image, original = source['correction_photo']
            if 'image_base64' in payload:
                raise ControlError(400, 'correction_photo_already_bound')
        if not source['has_photo'] and 'image_base64' in payload:
            raise ControlError(400, 'correction_photo_conflict')
        check()
        accepted = []
        def admit():
            result = self.start(payload['request_id'], image, original, device_token=device_token,
                                question=question, source_id=payload['source_id'], correction_fingerprint=fingerprint,
                                correction_epoch=correction_epoch)
            accepted.append(result)
            return result[1]['session_id']
        try:
            self.correction_journal('submit', source_id=payload['source_id'], source_kind=payload['source_kind'],
                                    attempt_id=payload['request_id'], access_check=check, admit=admit,
                                    new_execution=payload['new_execution'],
                                    approval_context=hashlib.sha256(device_token.encode()).hexdigest())
            if not accepted:
                raise ControlError(409, 'correction_reconciliation_required')
            return accepted[0]
        except ControlError:
            if accepted:
                self.cancel_correction_attempt(accepted[0][1]['session_id'], device_token, correction_epoch)
            raise
        except Exception:
            if accepted:
                self.cancel_correction_attempt(accepted[0][1]['session_id'], device_token, correction_epoch)
            raise ControlError(503, 'correction_outcome_unknown') from None

    def cancel_correction_attempt(self, session_id, device_token, correction_epoch):
        with self.lock:
            if (self.correction_epoch == correction_epoch and self.owner is not None
                    and self.owner['session_id'] == session_id
                    and self.owner.get('device_token') == device_token):
                self.owner['action'] = 'cancel'

    def reconcile_correction(self, payload, device_token):
        if type(payload) is not dict or set(payload) != {'source_id'}:
            raise ControlError(400, 'invalid_correction')
        if self.correction_journal is None:
            raise ControlError(503, 'correction_journal_unavailable')
        def check():
            self.require_open()
            return token_authorized(device_token) is True
        if check() is not True:
            raise ControlError(403, 'device_locked')
        try:
            result = self.correction_journal('reconcile', source_id=payload['source_id'], access_check=check,
                approval_context=hashlib.sha256(device_token.encode()).hexdigest())
        except Exception:
            raise ControlError(503, 'correction_reconciliation_unavailable') from None
        if check() is not True:
            raise ControlError(403, 'device_locked')
        return result

    def start(self, request_id: str, image=None, original=None, *, device_token=None, purpose="answer",
              question=None, source_id=None, correction_fingerprint=None, correction_epoch=None,
              followup_source=None):
        if question is not None:
            question = validated_question(question)
            if purpose != 'answer' or not token_authorized(device_token):
                raise ControlError(403, 'device_locked')
        if purpose not in {"answer", "message"}:
            raise ControlError(400, "invalid_purpose")
        if (purpose == "message" or lock_required()) and not token_authorized(device_token):
            raise ControlError(403, "device_locked")
        if purpose == "message" and (image is not None or original is not None):
            raise ControlError(400, "message_photo_forbidden")
        if original is not None:
            self.history_media_check()
            if image is None:
                raise ControlError(400, "original_requires_image")
            validate_photo(original, original=True)
        if image is not None:
            if not self.photo_ready:
                raise ControlError(503, "photo_unavailable")
            validate_photo(image)
        digest = hashlib.sha256(image).hexdigest() if image is not None else None
        original_digest = hashlib.sha256(original).hexdigest() if original is not None else None
        context = capture_session_context(device_token)
        with self.lock:
            self.require_open()
            self._expire()
            if question is not None and correction_epoch != self.correction_epoch:
                raise ControlError(409, 'correction_source_changed')
            if request_id in self.requests:
                if not self.owner or self.owner["request_id"] != request_id or self.owner["action"] == "cancel":
                    raise ControlError(409, "stale_request")
                if self.owner.get("session_context") != context:
                    raise DeviceLocked()
                if self.owner.get("image_digest") != digest or self.owner.get("original_digest") != original_digest or self.owner.get("device_token") != device_token or self.owner.get("purpose", "answer") != purpose:
                    raise ControlError(409, "request_conflict")
                if (self.owner.get('question') != question or self.owner.get('source_id') != source_id
                        or self.owner.get('correction_fingerprint') != correction_fingerprint
                        or self.owner.get('followup_source') != followup_source):
                    raise ControlError(409, 'request_conflict')
                if self.owner["phase"] == "uploading":
                    raise ControlError(409, "photo_uploading")
                return 200, self._credentials()
            if self.owner:
                raise ControlError(409, "voice_busy")
            if followup_source is not None:
                if (question is None or source_id is not None or image is not None
                        or self.device_context != context
                        or self.device_token != device_token
                        or self.completed_source is None
                        or self.completed_source.get('source_id') != followup_source
                        or self.source_session_id != hashlib.sha256(device_token.encode('utf-8')).hexdigest()
                        or not self.conversation.turns
                        or self.conversation.turns[-1].session_id != followup_source
                        or self.conversation.owner != hashlib.sha256(device_token.encode('utf-8')).hexdigest()):
                    raise ControlError(409, 'followup_source_changed')
            now = time.monotonic()
            if self.device_context != context:
                self.conversation.clear()
            self.owner = {
                "purpose": purpose,
                "conversation": self.conversation.prepare(device_token, replace_session=source_id) if purpose == 'answer' else None,
                "question": question, "source_id": source_id, "correction_fingerprint": correction_fingerprint,
                "followup_source": followup_source,
                "session_id": str(uuid.uuid4()), "token": secrets.token_urlsafe(32),
                "request_id": request_id, "source": "touch", "phase": "uploading" if image is not None else "queued",
                "action": 'stop' if question is not None else None, "lease": now + LEASE_SECONDS, "limit": now + self.max_seconds,
                "image_digest": digest, "image_path": None,
                "original_digest": original_digest, "original": original,
                "device_token": device_token,
                "session_context": context,
            }
            self.device_token = device_token
            self.device_context = context
            self.message_result = None
            self.completed_source = None
            self.source_session_id = None
            self.requests[request_id] = self.owner["session_id"]
            while len(self.requests) > MAX_REQUESTS:
                self.requests.popitem(last=False)
            credentials = self._credentials()
        if image is None:
            return 201, credentials
        path = None
        try:
            self.photo_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.photo_dir.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise OSError("unsafe photo directory")
            descriptor, name = tempfile.mkstemp(prefix="photo-", suffix=".jpg", dir=self.photo_dir)
            path = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(image)
            with self.lock:
                self._expire()
                if not self.owner or self.owner["session_id"] != credentials["session_id"] or self.owner["action"] == "cancel":
                    raise ControlError(409, "session_cancelled")
                self.owner["image_path"] = path
                self.owner["phase"] = "queued"
                now = time.monotonic()
                self.owner["lease"] = now + LEASE_SECONDS
                self.owner["limit"] = now + self.max_seconds
            return 201, credentials
        except BaseException as error:
            try:
                if path is not None:
                    path.unlink(missing_ok=True)
            finally:
                self.finish(credentials["session_id"])
            if isinstance(error, OSError):
                raise ControlError(503, "photo_storage_unavailable") from None
            raise

    def _credentials(self):
        return {"session_id": self.owner["session_id"], "token": self.owner["token"], "lease_seconds": LEASE_SECONDS}

    def confirm_start(self, request_id, session_id, token, *, device_token=None):
        """Correlate a delayed reply with the current owner; never extend its lease."""
        with self.lock:
            self.require_open()
            self._expire()
            owner = self.owner
            if (not owner or owner["source"] != "touch" or owner["session_id"] != session_id
                    or owner["request_id"] != request_id or not hmac.compare_digest(owner["token"], token)
                    or owner["action"] is not None or owner["phase"] not in {"queued", "recording"}):
                raise ControlError(409, "session_mismatch")
            if (owner.get("device_token") != device_token
                    or ((lock_required() or owner.get("purpose") == "message") and not token_authorized(device_token))):
                raise ControlError(403, "device_locked")
            if lock_required() and not context_current(device_token, owner.get('session_context')):
                raise DeviceLocked()
            return {"session_id": session_id, "request_id": request_id, "status": "current"}

    def command(self, action: str, session_id: str, token: str):
        with self.lock:
            self._expire()
            owner = self.owner
            if owner is None and action == "cancel" and self.message_result:
                result = self.message_result
                if result["session_id"] == session_id and hmac.compare_digest(result["token"], token):
                    if lock_required() and not context_current(result.get('device_token'), result.get('session_context')):
                        raise DeviceLocked()
                    result["result"] = {"phase": "cancelled"}
                    return {"session_id": session_id, "status": "cancelled"}
            if not owner or owner["source"] != "touch" or owner["session_id"] != session_id or not hmac.compare_digest(owner["token"], token):
                raise ControlError(409, "session_mismatch")
            if lock_required() and not context_current(owner.get('device_token'), owner.get('session_context')):
                raise DeviceLocked()
            if owner["phase"] == "processing":
                if owner.get("purpose") == "message" and action == "cancel":
                    owner["action"] = "cancel"
                    return {"session_id": session_id, "status": "cancelling"}
                raise ControlError(409, "processing_busy")
            if owner["action"] == "cancel":
                if action == "cancel":
                    return {"session_id": session_id, "status": "cancelled"}
                raise ControlError(409, "session_cancelled")
            if action == "keepalive":
                if owner["action"] != "stop":
                    owner["lease"] = min(time.monotonic() + LEASE_SECONDS, owner["limit"])
                return {"session_id": session_id, "lease_seconds": LEASE_SECONDS}
            owner["action"] = action
            return {"session_id": session_id, "status": "stopping" if action == "stop" else "cancelled"}

    def take(self, physical_press: bool):
        with self.lock:
            if self.closing.is_set():
                return None
            self._expire()
            if self.owner is None and physical_press:
                if lock_required():
                    try:
                        admitted = (context_current(self.device_token, self.device_context)
                                    and device_access.device_unlocked())
                    except Exception:
                        admitted = False
                    if not admitted:
                        self.device_token = None
                        self.device_context = None
                        return None
                self.message_result = None
                self.completed_source = None
                self.source_session_id = None
                self.owner = {
                    "session_id": str(uuid.uuid4()), "request_id": None,
                    "conversation": self.conversation.prepare(self.device_token),
                    "source": "physical", "phase": "queued", "action": None,
                    "device_token": self.device_token,
                    "session_context": self.device_context,
                }
            if self.owner and self.owner["phase"] == "queued":
                self.owner["phase"] = "recording"
                session = {"session_id": self.owner["session_id"], "source": self.owner["source"]}
                if self.owner.get("purpose") == "message":
                    session["purpose"] = "message"
                if self.owner.get('question') is not None:
                    session['question'] = self.owner['question']
                    session['source_id'] = self.owner['source_id']
                    session['request_id'] = self.owner['request_id']
                return session
            return None

    def message_status(self, session_id, token, device_token):
        if not token_authorized(device_token):
            self.revoke_device()
            raise ControlError(403, "device_locked")
        with self.lock:
            self.require_open()
            self._expire()
            candidate = self.message_result
            if candidate is None or candidate["session_id"] != session_id:
                candidate = self.owner
            if (not candidate or candidate.get("purpose") != "message"
                    or candidate["session_id"] != session_id
                    or candidate.get("device_token") != device_token
                    or not hmac.compare_digest(candidate["token"], token)):
                raise ControlError(409, "session_mismatch")
            if lock_required() and not context_current(device_token, candidate.get('session_context')):
                raise DeviceLocked()
            if "result" in candidate:
                return {"session_id": session_id, "purpose": "message", **candidate["result"]}
            phase = "cancelling" if candidate["action"] == "cancel" else "transcribing" if candidate["phase"] == "processing" else candidate["phase"]
            return {"session_id": session_id, "purpose": "message", "phase": phase}

    def message_complete(self, session_id, result):
        with self.lock:
            owner = self.owner
            if (self.closing.is_set() or not owner or owner["session_id"] != session_id
                    or owner.get("purpose") != "message"):
                return
            if owner["action"] == "cancel":
                result = {"phase": "cancelled"}
            self.message_result = {key: owner[key] for key in ("session_id", "token", "device_token", "purpose", "session_context")}
            self.message_result.update(result=result, expires=time.monotonic() + 120)

    def source(self, session_id: str):
        with self.lock:
            return self.owner["source"] if self.owner and self.owner["session_id"] == session_id else None

    def action(self, session_id: str):
        with self.lock:
            self._expire()
            return self.owner["action"] if self.owner and self.owner["session_id"] == session_id else "cancel"

    def cancel(self, session_id: str):
        with self.lock:
            if self.owner and self.owner["session_id"] == session_id:
                self.owner["action"] = "cancel"

    def begin_processing(self, session_id: str) -> bool:
        with self.lock:
            if self.closing.is_set():
                return False
            self._expire()
            owner = self.owner
            if not owner or owner["session_id"] != session_id or owner["action"] == "cancel":
                return False
            if owner["source"] == "touch" and owner["action"] != "stop":
                owner["action"] = "cancel"
                return False
            owner["phase"] = "processing"
            return True

    def discard_photo(self, session_id: str):
        image_path = None
        with self.lock:
            if self.owner and self.owner["session_id"] == session_id:
                image_path = self.owner.get("image_path")
        if image_path is not None:
            image_path.unlink(missing_ok=True)
        with self.lock:
            if self.owner and self.owner["session_id"] == session_id:
                self.owner["image_path"] = None
                self.owner["original"] = None

    def finish(self, session_id: str):
        self.discard_photo(session_id)
        with self.lock:
            if self.owner and self.owner["session_id"] == session_id:
                self.owner = None

    def image_path(self, session_id):
        with self.lock:
            if (self.owner and self.owner["session_id"] == session_id
                    and self.owner["source"] in {"touch", "typed"} and self.owner["phase"] == "processing"):
                return self.owner.get("image_path")
        return None

    def original_bytes(self, session_id):
        with self.lock:
            if (self.owner and self.owner["session_id"] == session_id
                    and self.owner["source"] in {"touch", "typed"} and self.owner["phase"] == "processing"):
                return self.owner.get("original")
        return None

    def has_photo(self, session_id):
        with self.lock:
            return bool(self.owner and self.owner["session_id"] == session_id
                        and self.owner["source"] in {"touch", "typed"}
                        and self.owner["phase"] in {"recording", "processing", "review"}
                        and self.owner.get("image_path") is not None)

    def busy(self):
        with self.lock:
            return self.owner is not None

    def clear(self, *, device_token=None):
        context = capture_session_context(device_token)
        with self.lock:
            self.require_open()
            if self.owner:
                if self.owner["source"] == "clear":
                    return {"status": "clearing"}
                raise ControlError(409, "voice_busy")
            self.completed_source = None
            self.correction_epoch += 1
            self.conversation.clear()
            self.source_session_id = None
            self.owner = {
                "session_id": str(uuid.uuid4()), "request_id": None,
                "source": "clear", "phase": "queued", "action": None,
                "device_token": device_token,
                "session_context": context,
            }
            return {"status": "clearing"}


class VoiceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, control, history=None, *, community=None):
        if address != ("127.0.0.1", 3197):
            raise ValueError("voice control must bind to 127.0.0.1:3197")
        self.control = control
        if community is not None:
            self.control.community = community
        self.community = self.control.community
        self.history = history
        self.slots = threading.BoundedSemaphore(8)
        self.draining = threading.Condition()
        self.active_requests = 0
        self.active_community_mutations = 0
        self.mutation_uncertain = threading.Event()
        super().__init__(address, VoiceHandler)

    def drain(self, timeout):
        with self.draining:
            return self.draining.wait_for(lambda: self.active_requests == 0, timeout=timeout)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address

    def process_request(self, request, client_address):
        with self.draining:
            if self.control.closing.is_set() or not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            self.active_requests += 1
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            with self.draining:
                self.active_requests -= 1
                self.draining.notify_all()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()
            with self.draining:
                self.active_requests -= 1
                self.draining.notify_all()

    def handle_error(self, request, client_address):
        pass


class VoiceHandler(BaseHTTPRequestHandler):
    server_version = "SAGELanternVoice/1"

    def header(self, name):
        values = self.headers.get_all(name, [])
        return values[0] if len(values) == 1 else None

    def respond(self, status, payload, *, preflight=False):
        context = getattr(self, "request_session_context", None)
        if context is not None and status < 400:
            if not context_current(self.header("X-Lantern-Session"), context):
                status, payload = 403, {"error": "device_locked"}
                self.server.control.revoke_device()
            else:
                payload = {**payload, "session_context": context.to_dict()}
        body = json.dumps(payload).encode("utf-8")
        access_check = getattr(self, "community_access_check", None) or getattr(self, "history_access_check", None)
        if access_check is not None and not access_check():
            status, body = 403, b'{"error":"device_locked"}'
        selection_check = getattr(self, "context_selection_check", None)
        if status < 400 and selection_check is not None and not selection_check():
            status, body = 409, b'{"error":"conversation_changed"}'
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Origin")
        origin = self.header("Origin")
        if origin in CONTROL_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            if preflight:
                self.send_header("Access-Control-Allow-Methods", "POST")
                requested = (self.header("Access-Control-Request-Headers") or "").lower().split(",")
                session_header = ", X-Lantern-Session" if "x-lantern-session" in {value.strip() for value in requested} else ""
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Lantern-Voice" + session_header)
        self.end_headers()
        if context is not None and status < 400 and not context_current(self.header("X-Lantern-Session"), context):
            self.server.control.revoke_device()
            return
        if status < 400 and selection_check is not None and not selection_check():
            return
        self.wfile.write(body)
        if access_check is not None and not access_check():
            self.server.control.revoke_device()

    def validate(self, *, control=False):
        if self.header("Host") not in CONTROL_HOSTS:
            raise ControlError(403, "invalid_host")
        if self.headers.get_all("Transfer-Encoding"):
            raise ControlError(400, "transfer_encoding_rejected")
        if control and self.header("Origin") not in CONTROL_ORIGINS:
            raise ControlError(403, "invalid_origin")

    def do_GET(self):
        try:
            self.validate()
            if self.headers.get_all("Content-Length") and self.header("Content-Length") != "0":
                raise ControlError(400, "body_not_allowed")
            if self.path != "/health":
                raise ControlError(404, "not_found")
            status = {"ok": True} if lock_required() else {"touch_ready": True, "busy": self.server.control.busy(), "photo_ready": self.server.control.photo_ready}
            status['correction_ready'] = self.server.control.correction_available() is True
            status['review_ready'] = self.server.control.review_available() is True
            status['typed_ready'] = self.server.control.typed_available() is True
            self.respond(200, status)
        except ControlError as error:
            self.respond(error.status, {"error": error.code})

    def do_OPTIONS(self):
        try:
            self.validate(control=True)
            if self.path not in {"/followup", "/correct", "/correct/reconcile", "/start", "/start/confirm", "/keepalive", "/stop", "/cancel", "/clear", "/session/link", "/message/status"} | HISTORY_PATHS | COMMUNITY_PATHS | CONVERSATION_PATHS | REVIEW_PATHS | QUESTION_PATHS:
                raise ControlError(404, "not_found")
            requested = [value.strip().lower() for value in (self.header("Access-Control-Request-Headers") or "").split(",")]
            if self.header("Access-Control-Request-Method") != "POST" or sorted(requested) not in (["content-type", "x-lantern-voice"], ["content-type", "x-lantern-session", "x-lantern-voice"]):
                raise ControlError(403, "invalid_preflight")
            if self.headers.get_all("Content-Length") and self.header("Content-Length") != "0":
                raise ControlError(400, "body_not_allowed")
            self.respond(200, {}, preflight=True)
        except ControlError as error:
            self.respond(error.status, {"error": error.code})

    def do_POST(self):
        try:
            self.server.control.require_open()
            self.validate(control=True)
            if len(self.headers.get_all("X-Lantern-Session", [])) > 1:
                raise ControlError(400, "invalid_device_session")
            device_token = self.header("X-Lantern-Session")
            gated = self.path in CONVERSATION_PATHS or self.path in {'/followup', '/correct', '/correct/reconcile'} or self.path in COMMUNITY_PATHS or self.path in REVIEW_PATHS or self.path in QUESTION_PATHS or lock_required()
            if gated and not token_authorized(device_token):
                if self.path in COMMUNITY_PATHS:
                    self.server.control.revoke_device()
                raise ControlError(403, "device_locked")
            self.request_session_context = capture_session_context(device_token)
            if self.path not in {"/followup", "/correct", "/correct/reconcile", "/start", "/start/confirm", "/keepalive", "/stop", "/cancel", "/clear", "/session/link", "/message/status"} | HISTORY_PATHS | COMMUNITY_PATHS | CONVERSATION_PATHS | REVIEW_PATHS | QUESTION_PATHS:
                raise ControlError(404, "not_found")
            if self.header("X-Lantern-Voice") != "control" or self.header("Content-Type") != "application/json":
                raise ControlError(400, "control_headers_required")
            length = self.header("Content-Length")
            limit = 32768 if self.path in COMMUNITY_PATHS or self.path in REVIEW_PATHS or self.path == '/followup' else MAX_PHOTO_BODY if self.path in {"/start", "/correct", "/question/start"} else 65536 if self.path == "/history/context" else 512
            if not length or not length.isascii() or not length.isdecimal() or not 1 <= int(length) <= limit:
                raise ControlError(400, "invalid_length")
            selected_context = None
            if self.path == '/history/context':
                binding = self.server.control.conversation_context_selection(device_token)
                if binding is not None:
                    selected_context, self.context_selection_check = binding
            selection_options = ({"context_selection": selected_context}
                                 if selected_context is not None else {})
            raw = self.rfile.read(int(length))
            if len(raw) != int(length):
                raise ControlError(400, "incomplete_body")
            try:
                payload = json.loads(raw, object_pairs_hook=unique_json_object)
            except (ValueError, UnicodeError, RecursionError):
                raise ControlError(400, "invalid_json") from None
            if not isinstance(payload, dict):
                raise ControlError(400, "invalid_payload")
            message_request = self.path == "/message/status" or (self.path == "/start" and payload.get("purpose") == "message")
            if message_request:
                gated = True
            if (gated or lock_required()) and not token_authorized(device_token):
                raise ControlError(403, "device_locked")
            if self.path in COMMUNITY_PATHS:
                response = self.community_request(payload, device_token)
                status = 200
            elif self.path in CONVERSATION_PATHS:
                response, response_epoch = self.server.control.conversation_request(
                    self.path, payload, device_token, getattr(self.server, 'history', None))
                def conversation_access():
                    control = self.server.control
                    if control.closing.is_set() or not token_authorized(device_token):
                        return False
                    with control.lock:
                        return control.correction_epoch == response_epoch and not control.closing.is_set()
                self.history_access_check = conversation_access
                status = 200
            elif self.path in HISTORY_PATHS:
                self.server.control.require_open()
                history = getattr(self.server, "history", None)
                if history is None:
                    raise ControlError(503, "history_unavailable")
                selection_check = getattr(self, "context_selection_check", None)
                if selection_check is not None and not selection_check():
                    raise ControlError(409, 'conversation_changed')
                try:
                    if getattr(history, "media_mode", "legacy") == "protected":
                        def history_access():
                            if self.server.control.closing.is_set():
                                return False
                            return token_authorized(device_token) is True
                        self.history_access_check = history_access
                        if not history_access():
                            raise ControlError(403, "device_locked")
                        response = history.request(self.path.rsplit("/", 1)[1], payload, access_check=history_access,
                                                   **selection_options)
                    else:
                        response = history.request(self.path.rsplit("/", 1)[1], payload, **selection_options)
                except Exception as error:
                    # Only a failed history mutation can leave an unknown outcome.
                    # A definite pre-write ControlError (locked, invalid, missing)
                    # and any failed read never changed durable state, so they must
                    # not taint the drain that normal maintenance depends on.
                    if (self.path in HISTORY_MUTATIONS and not isinstance(error, ControlError)
                            and hasattr(self.server, "mutation_uncertain")):
                        self.server.mutation_uncertain.set()
                    raise
                if self.path in {'/history/delete', '/history/clear'}:
                    with self.server.control.lock:
                        self.server.control.conversation.clear()
                        self.server.control.correction_epoch += 1
                status = 200
            elif self.path in REVIEW_PATHS:
                response = self.server.control.review_command(self.path.rsplit('/', 1)[1], payload, device_token)
                status = 200
            elif self.path == '/question/start':
                if (set(payload) not in ({"request_id"}, {"request_id", "image_base64"},
                                         {"request_id", "image_base64", "original_base64"})
                        or type(payload["request_id"]) is not str):
                    raise ControlError(400, "invalid_request")
                try:
                    question_request_id = str(uuid.UUID(payload["request_id"]))
                except ValueError:
                    raise ControlError(400, "invalid_request") from None
                typed_image = None
                typed_original = None
                if "image_base64" in payload:
                    if not self.server.control.photo_ready:
                        raise ControlError(503, "photo_unavailable")
                    typed_image = decode_photo(payload["image_base64"])
                    if "original_base64" in payload:
                        typed_original = decode_photo(payload["original_base64"], original=True)
                status, response = self.server.control.open_typed_question(
                    question_request_id, device_token, typed_image, typed_original)
            elif self.path == '/followup':
                status, response = self.server.control.followup(payload, device_token)
            elif self.path == '/correct':
                with self.server.control.lock:
                    correction_request_epoch = self.server.control.correction_epoch
                status, response = self.server.control.correct(payload, device_token, getattr(self.server, 'history', None))
            elif self.path == '/correct/reconcile':
                response = self.server.control.reconcile_correction(payload, device_token)
                status = 200
            elif self.path == "/session/link":
                if payload != {}:
                    raise ControlError(400, "invalid_payload")
                self.server.control.link_device(device_token)
                response = {"linked": True}
                status = 200
            elif self.path == "/clear":
                if payload != {}:
                    raise ControlError(400, "invalid_payload")
                response = self.server.control.clear(device_token=device_token)
                status = 202
            elif self.path == "/start":
                if "request_id" not in payload or set(payload) - {"request_id", "image_base64", "original_base64", "purpose"} or not isinstance(payload["request_id"], str):
                    raise ControlError(400, "invalid_request_id")
                purpose = payload.get("purpose", "answer")
                if type(purpose) is not str or purpose not in {"answer", "message"}:
                    raise ControlError(400, "invalid_purpose")
                if purpose == "message" and set(payload) != {"request_id", "purpose"}:
                    raise ControlError(400, "invalid_message_payload")
                try:
                    request_id = str(uuid.UUID(payload["request_id"]))
                except ValueError:
                    raise ControlError(400, "invalid_request_id") from None
                image = None
                original = None
                if "original_base64" in payload and "image_base64" not in payload:
                    raise ControlError(400, "original_requires_image")
                if "image_base64" in payload:
                    if not self.server.control.photo_ready:
                        raise ControlError(503, "photo_unavailable")
                    image = decode_photo(payload["image_base64"])
                    if "original_base64" in payload:
                        original = decode_photo(payload["original_base64"], original=True)
                elif len(raw) > 512:
                    raise ControlError(400, "invalid_length")
                status, response = self.server.control.start(request_id, image, original, device_token=device_token, purpose=purpose)
            elif self.path == "/start/confirm":
                if set(payload) != {"request_id", "session_id", "token"} or not all(
                        isinstance(value, str) and value.isascii() and 1 <= len(value) <= 128 for value in payload.values()):
                    raise ControlError(400, "invalid_session")
                try:
                    if any(str(uuid.UUID(payload[key])) != payload[key] for key in ("request_id", "session_id")):
                        raise ValueError()
                except ValueError:
                    raise ControlError(400, "invalid_session") from None
                response = self.server.control.confirm_start(**payload, device_token=device_token)
                status = 200
            else:
                if set(payload) != {"session_id", "token"} or not all(isinstance(value, str) and value.isascii() and 1 <= len(value) <= 128 for value in payload.values()):
                    raise ControlError(400, "invalid_session")
                if self.path == "/message/status":
                    response = self.server.control.message_status(payload["session_id"], payload["token"], device_token)
                else:
                    with self.server.control.lock:
                        owner = self.server.control.owner or self.server.control.message_result
                        message_owner = owner is not None and owner.get("purpose") == "message"
                        owner_token = owner.get("device_token") if owner else None
                    if message_owner:
                        gated = True
                        if not token_authorized(device_token) or device_token != owner_token:
                            raise ControlError(403, "device_locked")
                    response = self.server.control.command(self.path[1:], payload["session_id"], payload["token"])
                status = 200
            if (gated or lock_required()) and not token_authorized(device_token):
                if self.path == '/correct':
                    self.server.control.cancel_correction_attempt(response.get('session_id'), device_token,
                                                                  correction_request_epoch)
                elif self.path != '/correct/reconcile':
                    self.server.control.revoke_device()
                raise ControlError(403, "device_locked")
            if self.path in COMMUNITY_PATHS and not self.community_access_check():
                raise ControlError(403, "device_locked")
            self.respond(status, response)
        except DeviceLocked:
            self.server.control.revoke_device()
            self.respond(403, {"error": "device_locked"})
        except ControlError as error:
            self.respond(error.status, {"error": error.code})
        except (OSError, TimeoutError):
            self.close_connection = True

    def log_message(self, format, *args):
        pass

    def community_request(self, payload, device_token):
        control = self.server.control
        access_check = control.community_access(device_token)
        self.community_access_check = access_check
        mutation = self.path in COMMUNITY_MUTATIONS
        tracked = mutation and hasattr(self.server, "draining")
        if tracked:
            with self.server.draining:
                self.server.active_community_mutations += 1
        try:
            if not access_check():
                raise ControlError(403, "device_locked")
            session_id = hashlib.sha256(device_token.encode("utf-8")).hexdigest()
            response = control.community.dispatch(self.path, payload, access_check, session_id=session_id)
            if not access_check():
                raise ControlError(403, "device_locked")
            if type(response) is not dict or len(json.dumps(response, allow_nan=False).encode("utf-8")) > 262144:
                raise ValueError("invalid_community_response")
            return response
        except CommunityError as error:
            if error.code == "outcome_unknown" and hasattr(self.server, "mutation_uncertain"):
                self.server.mutation_uncertain.set()
            raise ControlError(error.status if error.status in {400, 403, 404, 409, 503} else 503,
                               "device_locked" if error.status == 403 else "community_unavailable") from None
        except ControlError:
            raise
        except Exception:
            if mutation and hasattr(self.server, "mutation_uncertain"):
                self.server.mutation_uncertain.set()
            raise ControlError(503, "community_unavailable") from None
        finally:
            if tracked:
                with self.server.draining:
                    self.server.active_community_mutations -= 1
                    self.server.draining.notify_all()


class VoiceLoop:
    def __init__(self, args: argparse.Namespace, *, history_media_mode="legacy", history_media_store=None,
                 history_memory=None, mesh_provider=None) -> None:
        if history_media_mode not in {"legacy", "protected"}:
            raise ValueError("invalid history media mode")
        self.args = args
        self.last_ptt = "unknown"
        self.touch_ready = False
        self.controller_thread = None
        self.listener_thread = None
        self.serving = False
        self.control = VoiceControl(args.max_seconds, photo_ready=getattr(args, "photo_ready", False), photo_dir=getattr(args, "photo_dir", None), dictation_available=self.dictation_available, mesh_provider=mesh_provider)
        self.input_source = None
        self.memory = history_memory
        protected_config = False
        self.verifier = None
        if getattr(args, "config", None) and args.config.is_file():
            from sage_lantern.config import RuntimeConfig
            config = RuntimeConfig.load(args.config)
            protected_config = getattr(config, "protected_storage", False)
            if type(protected_config) is not bool:
                raise ValueError("invalid protected storage configuration")
            if getattr(config, "verify_answers_background", False):
                from sage_lantern.answer_verification import AnswerVerifier
                self.verifier = AnswerVerifier(config.verifier_base_url, config.verifier_model,
                                               timeout_seconds=config.verifier_timeout_seconds)
            if self.memory is None and (protected_config or config.conversation_dir):
                from sage_lantern.conversation_service import ConversationService
                self.memory = ConversationService.from_config(config)
        protected_service = getattr(self.memory, "protected_storage", False)
        if type(protected_service) is not bool:
            raise ValueError("invalid protected storage service")
        if protected_config or protected_service or history_media_mode == "protected":
            tracked_media = getattr(self.memory, "media_store", None)
            if (not protected_service or tracked_media is None or getattr(args, "history_dir", None) is None
                    or not all(callable(getattr(tracked_media, method, None)) for method in ("put", "get"))
                    or (history_media_store is not None and history_media_store is not tracked_media)):
                raise ValueError("protected history dependencies unavailable")
            history_media_mode, history_media_store = "protected", tracked_media
        self.history = LocalHistory(args.history_dir, self.memory, media_mode=history_media_mode,
                                    media_store=history_media_store) if getattr(args, "history_dir", None) is not None else None
        if self.history is not None:
            self.control.history_media_check = self.history.require_media
        self.control.protected_history = self.memory if protected_service else None
        self.control.correction_journal = self.memory.correction_operation if protected_service else None
        self.control.correction_available = self.correction_available
        self.control.review_available = self.review_available
        self.control.typed_available = self.typed_available
        self.history_maintenance = 0
        self.history_ticket = None
        self.history_start_failed = None
        self.device_session = None
        self.locked_state_written = False
        self.stop_requested = self.control.closing
        self.instance_id = str(uuid.uuid4())
        self.children = {}
        self.cleanup_failed = False
        self.history_uncertain = False
        self.drain_result = None
        self.stop_started = None

    def dictation_available(self):
        """Capability of this supervised loop, not readiness of send providers."""
        return (self.serving is True and self.touch_ready is True
                and self.controller_thread is not None and self.controller_thread.is_alive() is True
                and not self.control.closing.is_set()
                and callable(self.run_message_session) and callable(self.message_transcribe))

    def correction_available(self):
        return (self.dictation_available() and callable(getattr(VoiceOrchestrator, 'run_text', None))
                and callable(self.control.correct) and self.control.correction_journal is not None)

    def typed_available(self):
        """Typed questions need the control server and the text pipeline only.

        Deliberately independent of audio, the microphone and the review
        preference: this is the lane a device without a working capture path
        falls back to, so the appliance never becomes unusable.
        """
        return (self.dictation_available()
                and callable(getattr(VoiceOrchestrator, 'run_text', None)))

    def review_available(self):
        return ((getattr(self.args, 'review_before_answer', False) is True or self.typed_available())
                and self.dictation_available() and callable(getattr(VoiceOrchestrator, 'run_text', None)))

    def request_stop(self, *_):
        """Signal-safe latch: never acquire locks from a Python signal handler."""
        if self.stop_started is None:
            self.stop_started = time.monotonic()
        self.stop_requested.set()

    def require_session_access(self, session_id):
        if self.stop_requested.is_set():
            raise DeviceLocked()
        session = self.device_session
        if lock_required() or (session is not None and session[2]):
            if session is None or session[0] != session_id or not token_authorized(session[1]):
                self.control.revoke_device()
                raise DeviceLocked()
            if lock_required() and not context_current(session[1], self.control.session_context_for(session_id)):
                self.control.revoke_device()
                raise DeviceLocked()

    def write_state(self, phase: str, **values: Any) -> None:
        redacted = False
        protected = lock_required() or bool(self.device_session and self.device_session[2])
        idle_token = self.control.device_token if (
            phase == "idle" and self.device_session is None
            and values == {"transcript": "", "response": "", "detail": ""}
        ) else None
        publication_context = (self.control.device_context if idle_token is not None
                               else self.control.session_context_for(values.get("session_id")))

        def check_publication():
            if idle_token is not None:
                if not token_authorized(idle_token):
                    self.control.revoke_device()
                    raise DeviceLocked()
                if lock_required() and not context_current(idle_token, publication_context):
                    self.control.revoke_device()
                    raise DeviceLocked()
            else:
                self.require_session_access(values.get("session_id"))

        if protected:
            try:
                check_publication()
            except DeviceLocked:
                redacted = True
                phase, values = "idle", {"detail": "Device locked.", "transcript": "", "response": ""}
        payload = {"schema": "sage.lantern.voice-state.v1", "observed_at": stamp(), "phase": phase, **values}
        if not redacted and publication_context is not None:
            payload["session_context"] = publication_context.to_dict()
        payload["touch_ready"] = self.touch_ready and not redacted
        payload['correction_ready'] = self.correction_available() is True and not redacted
        payload['review_ready'] = self.review_available() is True and not redacted
        payload['typed_ready'] = self.typed_available() is True and not redacted
        payload["photo_ready"] = self.control.photo_ready and not redacted
        completed_answer = (phase == "complete" and self.device_session is not None
                            and self.device_session[0] == values.get("session_id")
                            and isinstance(values.get("transcript"), str) and isinstance(values.get("response"), str))
        payload["has_photo"] = (self.control.source_photo(values.get("session_id")) if completed_answer
                                else phase in {"listening", "processing", "review"} and self.control.has_photo(values.get("session_id")))
        if self.input_source and phase in {"listening", "processing", "review"}:
            payload["input_source"] = self.input_source
        self.args.state.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".state.", dir=self.args.state.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o640)
            if not redacted and protected:
                check_publication()
            os.replace(name, self.args.state)
            self.locked_state_written = redacted
            if phase == "complete" and not redacted and self.device_session is not None:
                self.control.remember_completed_source(values.get("session_id"), values.get("transcript"),
                                                       values.get("response"), self.device_session[1])
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def ptt(self) -> str:
        try:
            value = json.loads(self.args.ptt_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "unknown"
        return value.get("state") if isinstance(value, dict) and value.get("state") in {"up", "down"} else "unknown"

    @staticmethod
    def stop_recording(process: subprocess.Popen[bytes]) -> bool:
        if process.poll() is not None:
            return False
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            return False
        return True

    def begin_history_session(self, session_id):
        consent = None
        self.history_start_failed = None
        if self.history is not None:
            try:
                consent = self.history.consent()
            except ControlError:
                self.history_start_failed = session_id
        self.history_ticket = (session_id, consent)

    def capture(self, session_id: str, path: Path, *, message=False, review=False) -> bool:
        self.require_session_access(session_id)
        if not message and not review:
            self.begin_history_session(session_id)
        self.require_session_access(session_id)
        command = [
            "arecord", "-q", "-D", self.args.alsa_device,
            "-f", "S16_LE", "-r", "16000", "-c", "2", "-t", "wav", str(path),
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        self.children[process.pid] = process
        started = time.monotonic()
        capture_limit = min(30, self.args.max_seconds) if message else self.args.max_seconds
        touch = self.control.source(session_id) == "touch"
        message_ptt_held = False
        try:
            self.write_state("listening", session_id=session_id, transcript="", response="")
            while process.poll() is None:
                self.require_session_access(session_id)
                # Release/Stop and recorder exit must not bypass the deadline.
                if time.monotonic() - started >= capture_limit:
                    self.control.cancel(session_id)
                    break
                if touch:
                    if self.control.action(session_id) in {"stop", "cancel"}:
                        break
                    if message:
                        if self.ptt() == "down":
                            message_ptt_held = True
                        elif message_ptt_held:
                            with self.control.lock:
                                owner = self.control.owner
                                if owner and owner["session_id"] == session_id and owner["action"] != "cancel":
                                    owner["action"] = "stop"
                            break
                elif self.ptt() != "down":
                    break
                time.sleep(0.04)
        finally:
            try:
                duration = time.monotonic() - started
                if duration >= capture_limit:
                    self.control.cancel(session_id)
                stopped = self.stop_recording(process)
                if type(process.pid) is int and not self.group_absent(process.pid):
                    self.stop_pipeline(process)
                detail = (process.stderr.read() if process.stderr else b"").decode(errors="replace").strip()
            finally:
                if process.stderr:
                    process.stderr.close()
                self.forget_reaped_group(process)
        valid_audio = False
        audio_at_limit = False
        try:
            with wave.open(str(path), "rb") as recording:
                frames = recording.readframes(1)
                valid_audio = (
                    recording.getnchannels() == 2
                    and recording.getsampwidth() == 2
                    and recording.getframerate() == 16000
                    and len(frames) == 4
                )
                audio_at_limit = recording.getnframes() >= capture_limit * recording.getframerate()
        except (OSError, EOFError, wave.Error):
            pass
        normal_stop = stopped and process.returncode == 1 and not detail and valid_audio
        if process.returncode not in {0, 130, -signal.SIGINT} and not normal_stop:
            raise RuntimeError(f"microphone capture failed: {detail[-300:] or f'recorder exit {process.returncode}'}")
        if duration >= capture_limit or audio_at_limit:
            self.control.cancel(session_id)
            return False
        if touch and self.control.action(session_id) != "stop":
            return False
        return duration >= self.args.min_seconds and valid_audio

    def process(self, session_id: str, path: Path, *, question=None) -> None:
        self.require_session_access(session_id)
        consent = self.history_ticket[1] if self.history_ticket and self.history_ticket[0] == session_id else None
        original = self.control.original_bytes(session_id)
        progress_stage = "starting"
        self.write_state("processing", session_id=session_id, transcript=question or "", response="", progress_stage=progress_stage)
        result = None
        response = ""
        response_bytes = 0
        published_length = 0
        pending_since = None
        transcript = question or ""
        deadline = time.monotonic() + self.args.processing_timeout

        def publish_pending():
            nonlocal published_length, pending_since
            self.require_session_access(session_id)
            pending = response[published_length:]
            if not pending or pending_since is None or not response.strip():
                return
            boundary = 0
            matches = list(re.finditer(r"\S+\s+", pending))
            target = 3 if published_length == 0 else 5
            if len(matches) >= target:
                boundary = matches[-1].end()
            sentence = re.search(r"[.!?。！？](?:\s|$)", pending)
            if sentence:
                boundary = max(boundary, sentence.end())
            # Bound the delay after real content arrives while retaining word
            # and sentence batches. This changes publication only, not model
            # input, validation, completion or the final response.
            max_wait = 0.12 if published_length == 0 else 0.3
            if time.monotonic() - pending_since >= max_wait or len(pending) >= 80:
                boundary = len(pending)
            if boundary:
                published_length += boundary
                self.write_state("processing", session_id=session_id, transcript=transcript, response=response[:published_length], detail="Answering…", progress_stage=progress_stage)
                pending_since = time.monotonic() if published_length < len(response) else None
                return True

        def accept(line):
            nonlocal result, response, response_bytes, pending_since, transcript, progress_stage, published_length
            self.require_session_access(session_id)
            if len(line) > MAX_EVENT_BYTES:
                raise RuntimeError("voice event exceeds size limit")
            try:
                event = json.loads(line, object_pairs_hook=unique_json_object)
                if not isinstance(event, dict) or result is not None:
                    raise ValueError()
                if event.get("type") == "progress":
                    stage = event.get("stage")
                    allowed = {'recalling'} if question is not None and progress_stage == 'starting' else PROGRESS_TRANSITIONS[progress_stage]
                    if set(event) != {"type", "stage"} or not isinstance(stage, str) or stage not in allowed:
                        raise ValueError()
                    progress_stage = stage
                    published_length = len(response)
                    pending_since = None
                    self.write_state("processing", session_id=session_id, transcript=transcript,
                                     response=response, progress_stage=progress_stage)
                elif event.get("type") == "delta":
                    if progress_stage not in {"starting", "photo_analysis", "generating", "answering"}:
                        raise ValueError()
                    text = event.get("text")
                    transcript = event.get("transcript")
                    if not isinstance(text, str) or not isinstance(transcript, str):
                        raise ValueError()
                    if question is not None and transcript != question:
                        raise ValueError()
                    response_bytes += len(text.encode("utf-8"))
                    if response_bytes > MAX_EVENT_BYTES or len(transcript.encode("utf-8")) > MAX_EVENT_BYTES:
                        raise ValueError()
                    if text and pending_since is None:
                        pending_since = time.monotonic()
                    began_answering = bool(text.strip()) and progress_stage != "answering"
                    if began_answering:
                        progress_stage = "answering"
                    response += text
                    if not publish_pending() and began_answering:
                        self.write_state("processing", session_id=session_id, transcript=transcript,
                                         response=response[:published_length], progress_stage=progress_stage)
                elif event.get("type") == "error":
                    if event != {"type": "error", "code": "no_spoken_question"} or progress_stage not in {"starting", "transcribing"}:
                        raise ValueError()
                    raise RuntimeError("no_spoken_question")
                elif event.get("type") == "result":
                    final = event.get("result")
                    if not isinstance(final, dict) or not isinstance(final.get("timings_ms", {}), dict):
                        raise ValueError()
                    for field in ("transcript", "response"):
                        if not isinstance(final.get(field), str) or len(final[field].encode("utf-8")) > MAX_EVENT_BYTES:
                            raise ValueError()
                    if question is not None and (final['transcript'] != question or final.get('session_id') != session_id):
                        raise ValueError()
                    result = final
                else:
                    raise ValueError()
            except (ValueError, UnicodeError, RecursionError):
                raise RuntimeError("invalid voice event") from None

        try:
            command = [str(self.args.runtime), "voice", "--config", str(self.args.config), "--audio", str(path), "--session-id", session_id, "--events"]
            if question is not None:
                command = [str(self.args.runtime), 'voice', '--config', str(self.args.config),
                           '--question-stdin', '--session-id', session_id, '--events']
            conversation = self.control.conversation_messages(session_id)
            private_input = validated_question(question).encode('utf-8') if question is not None else None
            if conversation:
                command.append('--conversation-stdin')
                private_input = json.dumps({'question': question, 'messages': conversation},
                                           ensure_ascii=False).encode('utf-8')
                if len(private_input) > MAX_INPUT_BYTES:
                    raise RuntimeError('conversation_input_too_large')
            image_path = self.control.image_path(session_id)
            if image_path is not None:
                command.extend(["--image", str(image_path)])
            self.require_session_access(session_id)
            environment = os.environ.copy()
            environment.pop("LANTERN_DEVICE_TOKEN", None)
            if self.device_session is not None and self.device_session[1] is not None:
                environment["LANTERN_DEVICE_TOKEN"] = self.device_session[1]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True, env=environment,
                **({'stdin': subprocess.PIPE} if private_input is not None else {}),
            )
            self.children[process.pid] = process
            succeeded = False
            try:
                if private_input is not None:
                    # Bound time and recheck access even if the child stops reading.
                    os.set_blocking(process.stdin.fileno(), False)
                    with selectors.DefaultSelector() as writer:
                        writer.register(process.stdin, selectors.EVENT_WRITE)
                        pending_input = memoryview(private_input)
                        while pending_input:
                            self.require_session_access(session_id)
                            if time.monotonic() >= deadline:
                                raise RuntimeError('voice input timed out')
                            if not writer.select(0.2):
                                continue
                            try:
                                written = os.write(process.stdin.fileno(), pending_input)
                            except BlockingIOError:
                                continue
                            if written <= 0:
                                raise RuntimeError('question_input_incomplete')
                            pending_input = pending_input[written:]
                    process.stdin.close()
                os.set_blocking(process.stdout.fileno(), False)
                buffer = bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        self.require_session_access(session_id)
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise RuntimeError("voice pipeline timed out")
                        publication_wait = 0.05 if pending_since is not None and response.strip() else 0.2
                        if not selector.select(min(remaining, publication_wait)):
                            publish_pending()
                            continue
                        try:
                            chunk = os.read(process.stdout.fileno(), min(65536, MAX_EVENT_BYTES + 1 - len(buffer)))
                        except BlockingIOError:
                            continue
                        if not chunk:
                            if buffer:
                                accept(buffer)
                            break
                        buffer.extend(chunk)
                        while True:
                            if time.monotonic() >= deadline:
                                raise RuntimeError("voice pipeline timed out")
                            end = buffer.find(b"\n")
                            if end < 0:
                                break
                            accept(buffer[:end])
                            del buffer[:end + 1]
                        if len(buffer) > MAX_EVENT_BYTES:
                            raise RuntimeError("voice event exceeds size limit")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("voice pipeline timed out")
                while True:
                    self.require_session_access(session_id)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("voice pipeline timed out")
                    try:
                        returncode = process.wait(timeout=min(remaining, 0.2))
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if returncode != 0:
                    raise RuntimeError("voice pipeline exited unsuccessfully")
                if result is None:
                    raise RuntimeError("voice pipeline omitted final result")
                self.require_session_access(session_id)
                self.control.retain_correction_photo(session_id)
                succeeded = True
            finally:
                try:
                    if not succeeded:
                        self.stop_pipeline(process)
                finally:
                    if private_input is not None and not process.stdin.closed:
                        process.stdin.close()
                    process.stdout.close()
                    self.forget_reaped_group(process)
        finally:
            self.control.discard_photo(session_id)
        result.update(self.control.conversation_metadata(session_id))
        history_failed = self.history_start_failed == session_id
        if self.history is not None:
            try:
                self.require_session_access(session_id)
                access = {"access_check": lambda: self.require_session_access(session_id)} if self.device_session is not None and self.device_session[2] else {}
                if getattr(self.history, "media_mode", "legacy") == "protected":
                    def protected_access():
                        self.require_session_access(session_id)
                        session = self.device_session
                        return session is not None and session[0] == session_id and token_authorized(session[1]) is True
                    access = {"access_check": protected_access}
                self.history.record(consent, session_id, result, original=original, **access)
            except ControlError:
                history_failed = True
                self.history_uncertain = True
        warning = {"detail": "Could not confirm history saving. Check Past questions."} if history_failed else {}
        self.require_session_access(session_id)
        self.write_state(
            "complete", session_id=session_id,
            transcript=result["transcript"],
            response=result["response"],
            follow_up_questions=validate_suggestions(result.get('follow_up_questions', [])),
            timings_ms=result.get("timings_ms", {}), **warning,
            inference_metrics=project_metrics(result.get("inference_metrics")),
        )
        self.schedule_verification(session_id, result["transcript"], result["response"],
                                   validate_suggestions(result.get('follow_up_questions', [])))

    def schedule_verification(self, session_id, question, answer, follow_ups):
        """Fact-check the finished answer in the background; never delay the answer."""
        if self.verifier is None or not answer.strip() or self.control.source_photo(session_id):
            return
        thread = threading.Thread(target=self.verify_answer,
                                  args=(session_id, question, answer, follow_ups),
                                  name="answer-verification", daemon=True)
        thread.start()

    def verify_answer(self, session_id, question, answer, follow_ups):
        try:
            verdict = self.verifier.verify(question, answer)
        except Exception as error:                       # the checker must never take the loop down
            print(json.dumps({"event": "answer_verification_failed",
                              "error": type(error).__name__}), flush=True)
            return
        if verdict.status != "corrected" or not verdict.text or verdict.text == answer:
            print(json.dumps({"event": "answer_verification", "session_id": session_id,
                              "status": verdict.status, "seconds": round(verdict.seconds, 2)}), flush=True)
            return
        if not self.claim_correction(session_id):
            return
        try:
            self.write_state("complete", session_id=session_id, transcript=question,
                             response=verdict.text, follow_up_questions=follow_ups, detail="")
        except DeviceLocked:
            return
        print(json.dumps({"event": "answer_verification", "session_id": session_id,
                          "status": "corrected", "seconds": round(verdict.seconds, 2)}), flush=True)

    def claim_correction(self, session_id):
        """Only a still-current, idle turn of this device session may be rewritten."""
        with self.control.lock:
            owner = self.control.owner
            source = self.control.completed_source
            session = self.device_session
            return (not self.stop_requested.is_set() and owner is None
                    and source is not None and source.get("source_id") == session_id
                    and session is not None and session[0] == session_id)

    @staticmethod
    def stop_pipeline(process):
        try:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)

    def review_recording(self, session_id, audio_path):
        """Transcribe privately; neither recall nor inference runs before Submit."""
        from sage_lantern.adapters import NoSpeechDetected
        self.write_state('processing', session_id=session_id, transcript='', response='', progress_stage='transcribing')
        try:
            transcript = self.message_transcribe(session_id, audio_path)
            # The review screen is where the owner checks what was heard, so the
            # recorded confusions are repaired before it is shown rather than
            # only after submission. The turn applies the same table again, which
            # is idempotent, so the two cannot disagree.
            transcript = repair(transcript).text
            validated_question(transcript)
        except NoSpeechDetected:
            self.write_state('error', session_id=session_id, transcript='', response='', detail='no_spoken_question')
            return None
        finally:
            audio_path.unlink(missing_ok=True)
        self.require_message_access(session_id)
        review_id = self.control.begin_review(session_id, transcript)
        self.write_state('review', session_id=session_id, review_id=review_id, transcript=transcript, response='')
        return self.await_review(session_id)

    def await_review(self, session_id):
        """Wait for the reviewed question; a cancel or a lock abandons it."""
        while True:
            self.require_message_access(session_id)
            question = self.control.reviewed_question(session_id)
            if question is not None:
                return question
            self.stop_requested.wait(0.1)

    def run_typed_question(self, session_id):
        """Open review for a typed question and answer only the submitted text.

        Nothing is captured and nothing is transcribed: the microphone may be
        absent, broken or already in use. The typed text is validated by the
        same review command the spoken lane uses before any inference starts.
        """
        self.require_message_access(session_id)
        review_id = self.control.begin_typed_review(session_id)
        self.write_state('review', session_id=session_id, review_id=review_id, transcript='', response='')
        return self.await_review(session_id)

    def run_session(self, session):
        if session.get("purpose") == "message":
            return self.run_message_session(session)
        session_id = session["session_id"]
        typed = session["source"] == "typed"
        reviewing = typed or (getattr(self.args, 'review_before_answer', False) is True
                              and session.get('question') is None and session['source'] != 'clear')
        self.device_session = (session_id, self.control.session_device_token(session_id),
                               lock_required() or session.get('question') is not None or reviewing)
        if session["source"] == "clear":
            try:
                self.require_session_access(session_id)
                self.write_state("idle", session_id=session_id, transcript="", response="", detail="")
            except DeviceLocked:
                self.write_state("idle")
            finally:
                self.control.finish(session_id)
                self.device_session = None
            return
        self.input_source = session["source"]
        audio_dir = getattr(self.args, 'review_audio_dir', Path('/run/sage-lantern-voice/questions')) if reviewing else self.args.audio_dir
        audio_path = audio_dir / f"input-{session_id}.wav"
        correction_processed = False
        try:
            self.require_session_access(session_id)
            if typed:
                question = self.run_typed_question(session_id)
                self.require_message_access(session_id)
                self.begin_history_session(session_id)
                self.process(session_id, audio_path, question=question)
            else:
                audio_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                if reviewing:
                    metadata = audio_dir.lstat()
                    if (not audio_dir.is_absolute() or not stat.S_ISDIR(metadata.st_mode)
                            or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700):
                        raise RuntimeError('unsafe_question_audio_directory')
                action = self.control.action(session_id)
                if session.get('question') is not None and action != 'cancel' and self.control.begin_processing(session_id):
                    self.begin_history_session(session_id)
                    self.process(session_id, audio_path, question=session['question'])
                    correction_processed = True
                    self.control.correction_journal('finish', source_id=session['source_id'],
                        attempt_id=session['request_id'], session_id=session_id, succeeded=True,
                        access_check=lambda: token_authorized(self.control.session_device_token(session_id)) is True)
                elif action not in {"cancel", "stop"} and (self.capture(session_id, audio_path, review=True) if reviewing else self.capture(session_id, audio_path)) and self.control.begin_processing(session_id):
                    if reviewing:
                        question = self.review_recording(session_id, audio_path)
                        if question is not None:
                            self.require_message_access(session_id)
                            self.begin_history_session(session_id)
                            self.process(session_id, audio_path, question=question)
                    else:
                        self.process(session_id, audio_path)
                else:
                    self.write_state("idle", detail="Recording cancelled or too short", transcript="", response="")
        except DeviceLocked:
            self.control.revoke_device()
            self.write_state("idle", **({'session_id': session_id} if reviewing else {}),
                             detail="Question discarded." if reviewing else "Device locked.", transcript="", response="")
        except Exception as exc:
            # The published state stays redacted for a lock-bound session; the
            # journal keeps the operator-facing reason so a failed turn can be
            # diagnosed without weakening what the screen may show.
            print(json.dumps({"event": "voice_session_error", "session_id": session_id,
                              "error": type(exc).__name__, "detail": str(exc)[:300]}), flush=True)
            if session.get('question') is not None and not correction_processed:
                try:
                    self.control.correction_journal('finish', source_id=session['source_id'],
                        attempt_id=session['request_id'], session_id=session_id, succeeded=False,
                        access_check=lambda: token_authorized(self.control.session_device_token(session_id)) is True)
                except Exception:
                    self.history_uncertain = True
            self.write_state("error", session_id=session_id, detail="Voice operation failed." if self.device_session[2] else str(exc), transcript="", response="")
        finally:
            try:
                audio_path.unlink(missing_ok=True)
            finally:
                self.input_source = None
                self.history_ticket = None
                self.control.finish(session_id)
                self.device_session = None

    def require_message_access(self, session_id):
        self.require_session_access(session_id)
        if self.control.action(session_id) == "cancel":
            raise DeviceLocked()

    def message_transcribe(self, session_id, audio_path):
        """Supervise only the isolated STT worker, never the runtime voice command."""
        from sage_lantern.adapters import NoSpeechDetected
        self.require_message_access(session_id)
        environment = os.environ.copy()
        environment.pop("LANTERN_DEVICE_TOKEN", None)
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--message-stt-worker",
             "--config", str(self.args.config), "--audio", str(audio_path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            start_new_session=True, env=environment,
        )
        self.children[process.pid] = process
        deadline = time.monotonic() + min(120, self.args.processing_timeout)
        output = bytearray()
        try:
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    self.require_message_access(session_id)
                    if time.monotonic() >= deadline:
                        raise RuntimeError("dictation_timeout")
                    if not selector.select(0.2):
                        continue
                    try:
                        chunk = os.read(process.stdout.fileno(), 16385 - len(output))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > 16384:
                        raise RuntimeError("dictation_output_limit")
            while True:
                self.require_message_access(session_id)
                if time.monotonic() >= deadline:
                    raise RuntimeError("dictation_timeout")
                try:
                    code = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            value = json.loads(output, object_pairs_hook=unique_json_object)
            if code == 2 and value == {"error": "no_speech"}:
                raise NoSpeechDetected("no_speech")
            if code != 0 or type(value) is not dict or set(value) != {"text"} or type(value["text"]) is not str:
                raise RuntimeError("dictation_failed")
            return value["text"]
        finally:
            try:
                self.stop_pipeline(process)
            finally:
                process.stdout.close()
                self.forget_reaped_group(process)

    def run_message_session(self, session):
        """One ephemeral segment preview; the draft backend owns paragraph acceptance.

        No history ticket, recall, inference, TTS or shared-state transcript.
        Audio is temporary in the configured runtime directory and unlinked even
        on failure. Cancellation/lock cannot authorize an edit or a send.
        """
        from sage_lantern.message_drafts import MessageDraft
        from sage_lantern.message_dictation import MessageDictation, DictationError

        session_id = session["session_id"]
        self.device_session = (session_id, self.control.session_device_token(session_id), True)
        self.input_source = "touch"
        directory = getattr(self.args, "message_audio_dir", Path("/run/sage-lantern-voice/dictation"))
        audio_path = None
        result = {"phase": "cancelled"}
        loop = self

        class Delegate:
            def transcribe(self, path):
                return loop.message_transcribe(session_id, path)

        bridge = MessageDictation(MessageDraft(), Delegate())
        try:
            self.require_message_access(session_id)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = directory.lstat()
            if (not directory.is_absolute() or not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700):
                raise RuntimeError("unsafe_dictation_directory")
            descriptor, name = tempfile.mkstemp(prefix="segment-", suffix=".wav", dir=directory)
            os.close(descriptor)
            audio_path = Path(name)
            ticket = bridge.begin_add(expected_revision=0)
            if self.control.action(session_id) != "stop" and self.capture(session_id, audio_path, message=True) and self.control.begin_processing(session_id):
                self.require_message_access(session_id)
                self.write_state("processing", session_id=session_id, purpose="message", transcript="", response="")
                preview = bridge.transcribe(ticket, audio_path, expected_revision=ticket.revision)
                self.require_message_access(session_id)
                result = {"phase": "review", "text": preview.text}
        except DeviceLocked:
            result = {"phase": "cancelled"}
        except DictationError as error:
            result = {"phase": "error", "error": "no_speech" if str(error) == "no_speech" else "dictation_failed"}
        except Exception:
            result = {"phase": "error", "error": "dictation_failed"}
        finally:
            bridge.lock()
            try:
                if audio_path is not None:
                    audio_path.unlink(missing_ok=True)
            except OSError:
                self.cleanup_failed = True
                result = {"phase": "error", "error": "dictation_cleanup_failed"}
            finally:
                if token_authorized(self.device_session[1]):
                    self.control.message_complete(session_id, result)
                else:
                    self.control.revoke_device()
                self.control.finish(session_id)
                self.device_session = None
                self.input_source = None
                self.write_state("idle", transcript="", response="")

    def step(self):
        if self.stop_requested.is_set():
            return
        self.control.expire_community_access()
        available = not lock_required() or token_authorized(self.control.device_token)
        if not available:
            self.control.revoke_device(clear_community=False)
            if not self.locked_state_written:
                self.write_state("idle")
                self.locked_state_written = True
        elif self.locked_state_written:
            self.write_state("idle", transcript="", response="", detail="")
            self.locked_state_written = False
        if available and self.history is not None and time.monotonic() >= self.history_maintenance:
            self.history_maintenance = time.monotonic() + 60
            try:
                self.history.request("list", {})
            except ControlError:
                pass
        current = self.ptt()
        session = self.control.take(current == "down" and self.last_ptt != "down")
        self.last_ptt = current
        if session:
            self.run_session(session)
            self.last_ptt = self.ptt()

    @staticmethod
    def group_absent(pid):
        if type(pid) is not int or pid <= 1:
            return False
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

    def forget_reaped_group(self, process):
        if self.group_absent(process.pid):
            self.children.pop(process.pid, None)

    def process_identity(self):
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        invocation = os.environ.get("INVOCATION_ID")
        if invocation is not None and re.fullmatch(r"[0-9a-f]{32}", invocation) is None:
            raise ValueError("invalid_invocation_id")
        return {"instance_id": self.instance_id, "pid": os.getpid(),
                "start_ticks": fields[19], "boot_id": boot_id, "invocation_id": invocation}

    @staticmethod
    def atomic_receipt(path, payload):
        """Trusted service-owned runtime directory only; root validates custody."""
        descriptor, temporary = tempfile.mkstemp(prefix=".voice-drain-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def drain_transition(self, identity):
        """Root writes a bounded 0644 request matching the startup identity file.

        Request: schema sage.lantern.voice-drain-request.v1, transition_id
        (canonical UUID), and exactly the five process_identity fields. No
        HTTP endpoint authorizes shutdown. Missing/stale requests cannot ACK.
        """
        path = getattr(self.args, "drain_request", None)
        if path is None:
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                    or stat.S_IMODE(metadata.st_mode) != 0o644 or metadata.st_nlink != 1
                    or not 1 <= metadata.st_size <= 2048):
                raise ValueError("unsafe_drain_request")
            request = json.loads(stream.read(2049), object_pairs_hook=unique_json_object)
        if (type(request) is not dict or set(request) != set(identity) | {"schema", "transition_id"}
                or request["schema"] != "sage.lantern.voice-drain-request.v1"
                or any(type(request[key]) is not type(value) or request[key] != value for key, value in identity.items())):
            raise ValueError("stale_drain_request")
        transition = request["transition_id"]
        if type(transition) is not str or str(uuid.UUID(transition)) != transition:
            raise ValueError("invalid_transition")
        return transition

    def drain_shutdown(self, server, thread):
        """Positive local drain only; never proves LLM completion or cgroup empty.

        Unknown HTTP/store outcomes remain in their existing journals. On a
        timeout do not close/reset memory storage or convert idle into an ACK.
        Root must retain its fence, check cgroup/LLM and reconcile failures.
        """
        timeout = getattr(self.args, "drain_timeout", 20.0)
        deadline = (self.stop_started if self.stop_started is not None else time.monotonic()) + timeout
        self.control.quiesce()
        checks = {"http_drained": False, "http_outcome_known": False,
                  "session_finalized": False, "child_groups_absent": False,
                  "memory_drained": False, "history_outcome_known": not self.history_uncertain,
                  "community_drained": False}
        try:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=max(0, deadline - time.monotonic()))
            checks["http_drained"] = server.drain(max(0, deadline - time.monotonic())) is True and not thread.is_alive()
            listener_thread = self.listener_thread
            if listener_thread is not None:
                listener_thread.join(timeout=max(0, deadline - time.monotonic()))
                checks["http_drained"] = checks["http_drained"] and not listener_thread.is_alive()
            checks["http_outcome_known"] = not server.mutation_uncertain.is_set()
        except Exception:
            pass
        finally:
            server.server_close()
        for pid, process in tuple(self.children.items()):
            try:
                if not self.group_absent(pid):
                    os.killpg(pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=min(1, max(0, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                    if not self.group_absent(pid):
                        os.killpg(pid, signal.SIGKILL)
                process.wait(timeout=max(0, deadline - time.monotonic()))
                while not self.group_absent(pid) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.forget_reaped_group(process)
            except Exception:
                self.cleanup_failed = True
        checks["child_groups_absent"] = not self.children and not self.cleanup_failed
        try:
            with self.control.lock:
                owner = self.control.owner
                pending_id = owner["session_id"] if owner and owner["phase"] == "queued" else None
            if pending_id is not None:
                self.control.finish(pending_id)
            checks["session_finalized"] = self.device_session is None and not self.control.busy()
        except Exception:
            pass
        try:
            remaining = max(0, deadline - time.monotonic())
            protected = (getattr(self.memory, "protected_storage", False) is True
                         or getattr(self.history, "media_mode", "legacy") == "protected")
            if protected:
                release = getattr(self.memory, "release", None)
                checks["memory_drained"] = remaining > 0 and callable(release) and release(timeout=remaining) is True
            else:
                checks["memory_drained"] = self.memory is None or self.memory.stop(timeout=remaining) is True
        except Exception:
            pass
        try:
            checks["community_drained"] = self.control.community.close(timeout=max(0, deadline - time.monotonic())) is True and not self.control.community_cleanup_failed
        except Exception:
            pass
        checks["deadline_met"] = time.monotonic() <= deadline
        result = {"schema": "sage.lantern.voice-drain.v1", "drained": all(checks.values()),
                  "checks": checks, "memory_configured": self.memory is not None,
                  "llm_drained": False, "cgroup_verified": False,
                  "queued_writes": "preserved_not_asserted",
                  "reconcile_required": not all(checks.values())}
        path = getattr(self.args, "drain_receipt", None)
        if path is not None:
            try:
                identity = self.process_identity()
                transition = self.drain_transition(identity)
                if transition is None:
                    raise ValueError("missing_transition")
                result.update(identity, transition_id=transition, completed_at=stamp())
                self.atomic_receipt(path, result)
            except Exception:
                result["drained"] = False
                result["reconcile_required"] = True
        return result

    def acknowledge_listener(self, server, serving_thread):
        from sage_lantern.voice_listener_ack import acknowledge
        while serving_thread.is_alive() and not self.stop_requested.is_set():
            try:
                acknowledge(server, serving_thread, self.stop_requested)
            except Exception:
                pass
            self.stop_requested.wait(1)

    def run(self) -> None:
        if getattr(self.args, "drain_instance", None) is not None:
            self.atomic_receipt(self.args.drain_instance, self.process_identity())
        if self.memory:
            if getattr(self.memory, "protected_storage", False) is not True:
                self.history.consent()
            self.memory.start()
        try:
            server = VoiceHTTPServer(("127.0.0.1", 3197), self.control, self.history)
        except Exception:
            if self.memory:
                self.memory.stop(timeout=getattr(self.args, "drain_timeout", 20.0))
            self.write_state("error", detail="Touch controller unavailable", transcript="", response="")
            raise
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.controller_thread = thread
        try:
            thread.start()
            self.touch_ready = thread.is_alive()
            if not self.touch_ready:
                raise RuntimeError("Touch controller unavailable")
            if os.environ.get("SAGE_LANTERN_MESH_BROKER") == "1":
                self.listener_thread = threading.Thread(target=self.acknowledge_listener, args=(server, thread), daemon=True)
                self.listener_thread.start()
            self.serving = True
            self.write_state("idle", transcript="", response="")
            while thread.is_alive() and not self.stop_requested.is_set():
                self.step()
                time.sleep(0.04)
            if not self.stop_requested.is_set():
                raise RuntimeError("Touch controller stopped")
        finally:
            self.serving = False
            self.touch_ready = False
            try:
                self.write_state("unavailable", detail="Voice service stopped", transcript="", response="")
            finally:
                self.drain_result = self.drain_shutdown(server, thread)


def message_stt_worker(config_path, audio_path):
    """Internal child entry point: no orchestrator, identity, SAGE or LLM clients."""
    from sage_lantern.adapters import WhisperCppSTT, NoSpeechDetected
    from sage_lantern.config import RuntimeConfig
    from sage_lantern.message_drafts import MessageDraft
    from sage_lantern.message_dictation import _validate_wav
    try:
        _validate_wav(audio_path)
        config = RuntimeConfig.load(config_path)
        text = WhisperCppSTT(config.whisper_binary, config.whisper_model, config.whisper_threads).transcribe(audio_path)
        MessageDraft._text(text)
        print(json.dumps({"text": text}), flush=True)
        return 0
    except NoSpeechDetected:
        print(json.dumps({"error": "no_speech"}), flush=True)
        return 2
    except Exception:
        print(json.dumps({"error": "dictation_failed"}), flush=True)
        return 2


def main() -> int:
    if "--message-stt-worker" in sys.argv[1:]:
        worker = argparse.ArgumentParser()
        worker.add_argument("--message-stt-worker", action="store_true", required=True)
        worker.add_argument("--config", type=Path, required=True)
        worker.add_argument("--audio", type=Path, required=True)
        args = worker.parse_args()
        return message_stt_worker(args.config, args.audio)
    parser = argparse.ArgumentParser()
    parser.add_argument("--ptt-state", type=Path, default=Path("/run/sage-lantern-ptt/state.json"))
    parser.add_argument("--state", type=Path, default=Path("/run/sage-lantern-voice/state.json"))
    parser.add_argument("--runtime", type=Path, default=Path("/opt/sage-lantern/venv/bin/sage-lantern"))
    parser.add_argument("--config", type=Path, default=Path("/etc/sage-lantern/lantern.toml"))
    parser.add_argument("--audio-dir", type=Path, default=Path("/var/lib/sage-lantern/audio"))
    parser.add_argument("--message-audio-dir", type=Path, default=Path("/run/sage-lantern-voice/dictation"))
    parser.add_argument("--review-audio-dir", type=Path, default=Path("/run/sage-lantern-voice/questions"))
    parser.add_argument("--review-before-answer", action=argparse.BooleanOptionalAction, default=True,
                        help="Review recorded questions on screen before inference; requires matching touchscreen UI")
    parser.add_argument("--history-dir", type=Path, default=Path("/var/lib/sage-lantern/history"))
    parser.add_argument("--photo-dir", type=Path, default=Path("/run/sage-lantern-voice/photos"))
    parser.add_argument("--photo-ready", action="store_true", help="Enable photo answers only after validating the live backend with its matching --mmproj")
    parser.add_argument("--alsa-device", default="hw:CARD=Audio,DEV=0")
    parser.add_argument("--min-seconds", type=float, default=0.35)
    parser.add_argument("--max-seconds", type=float, default=30.0)
    parser.add_argument("--processing-timeout", type=float, default=240.0)
    parser.add_argument("--drain-timeout", type=float, default=20.0)
    parser.add_argument("--drain-instance", type=Path, default=Path("/run/sage-lantern-voice/instance.json"))
    parser.add_argument("--drain-request", type=Path, default=Path("/run/sage-lantern-voice/drain-request.json"))
    parser.add_argument("--drain-receipt", type=Path, default=Path("/run/sage-lantern-voice/drain-receipt.json"))
    args = parser.parse_args()
    if not 0 < args.drain_timeout <= 60:
        parser.error("drain timeout must be greater than zero and at most 60 seconds")
    mesh_mode = os.environ.get("SAGE_LANTERN_MESH_BROKER", "0")
    if mesh_mode not in ("0", "1"):
        parser.error("invalid mesh broker mode")
    if mesh_mode == "1":
        from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
        args.drain_request = Path("/run/sage-lantern-mesh-control/voice-drain-request.json")
        loop = VoiceLoop(args, mesh_provider=MeshVoiceFacade())
    else:
        loop = VoiceLoop(args)
    previous = {kind: signal.signal(kind, loop.request_stop) for kind in (signal.SIGTERM, signal.SIGINT)}
    try:
        loop.run()
        return 0 if loop.drain_result and loop.drain_result["drained"] else 1
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)


if __name__ == "__main__":
    raise SystemExit(main())
