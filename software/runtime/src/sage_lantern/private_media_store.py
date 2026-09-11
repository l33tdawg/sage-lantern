"""Protected immutable JPEG adapter; not wired to history or lifecycle services.

Uses the exact ordinary-actor SDK client bound by MessageStorageProbe. Current
access and active encrypted storage are checked before/after every operation;
PUT uncertainty is never retried here. Reconcile only the same UUID and digest.
put returns SHA256 hex; get returns unchanged bytes. No delete/retention promise,
keys, files, transforms or automatic UUID generation. JPEG validation is bounded
header validation, not a guarantee of pixel decodability or metadata redaction.

Requires the SDK media-header contract: SageNotFoundError only for schema-marked
media 404; unmarked 404 is SageAPIError. GET validates schema, actor, object UUID,
length, digest and cache headers before returning bytes. Do not deploy against
an older SDK that maps arbitrary media 404 to SageNotFoundError. No cached
positive request is a capability check. Supply the digest retained with history
for an additional exact-content check. Operator-pinned server trust is required.
"""

import hashlib
import hmac
import re
import uuid

from sage_sdk.exceptions import SageNotFoundError

from .history_media import _validate_jpeg
from .message_storage import MessageStorageProbe


class ProtectedMediaUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("protected_media_unavailable")


class ProtectedMediaOutcomeUnknown(ProtectedMediaUnavailable):
    def __init__(self):
        RuntimeError.__init__(self, "protected_media_outcome_unknown")


class ProtectedMediaNotFound(ProtectedMediaUnavailable):
    def __init__(self):
        RuntimeError.__init__(self, "protected_media_not_found")


def _identifier(value):
    if type(value) is not str or len(value) != 36:
        raise ProtectedMediaUnavailable()
    parsed = uuid.UUID(value)
    if not parsed.int or str(parsed) != value:
        raise ProtectedMediaUnavailable()


class ProtectedMediaStore:
    def __init__(self, client, *, storage, dimensions=(1920, 1080)):
        if (type(storage) is not MessageStorageProbe or storage.client is not client
                or type(dimensions) is not tuple or len(dimensions) != 2
                or any(type(value) is not int for value in dimensions)
                or dimensions not in ((1920, 1080), (1280, 720))):
            raise ProtectedMediaUnavailable()
        self.client, self.storage = client, storage
        self._actor, self._endpoint = storage.actor, storage.endpoint
        self._dimensions = dimensions

    def _access(self, check):
        if (not callable(check) or check() is not True or self.storage.client is not self.client
                or self.storage.actor != self._actor or self.storage.endpoint != self._endpoint):
            raise ProtectedMediaUnavailable()

    def _snapshot(self, check):
        self._access(check)
        epoch = self.storage.snapshot().epoch
        self._access(check)
        return epoch

    def put(self, object_id, jpeg, *, access_check):
        """Immutable same-ID/same-bytes idempotency is enforced by the server."""
        dispatched = False
        try:
            _identifier(object_id)
            _validate_jpeg(jpeg, self._dimensions)
            digest = hashlib.sha256(jpeg).hexdigest()
            epoch = self._snapshot(access_check)
            method = self.client.private_media_put
            self._access(access_check)
            dispatched = True
            metadata = method(object_id=object_id, jpeg=jpeg)
            if self._snapshot(access_check) != epoch:
                raise ProtectedMediaOutcomeUnknown()
            if hasattr(metadata, "model_dump"):
                metadata = metadata.model_dump(mode="json")
            fields = {"schema", "agent_id", "object_id", "revision", "length", "digest"}
            if (type(metadata) is not dict or set(metadata) != fields
                    or metadata["schema"] != "sage.private-media.v1"
                    or metadata["agent_id"] != self._actor.agent_id or metadata["object_id"] != object_id
                    or type(metadata["revision"]) is not int or metadata["revision"] != 1
                    or type(metadata["length"]) is not int or metadata["length"] != len(jpeg)
                    or type(metadata["digest"]) is not str or metadata["digest"] != digest):
                raise ProtectedMediaOutcomeUnknown()
            self._access(access_check)
            return digest
        except Exception:
            if dispatched:
                raise ProtectedMediaOutcomeUnknown() from None
            raise ProtectedMediaUnavailable() from None

    def get(self, object_id, *, access_check, expected_digest=None):
        try:
            _identifier(object_id)
            if expected_digest is not None and (type(expected_digest) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None):
                raise ProtectedMediaUnavailable()
            epoch = self._snapshot(access_check)
            method = self.client.private_media_get
            self._access(access_check)
            missing = False
            try:
                jpeg = method(object_id=object_id)
            except SageNotFoundError as error:
                if error.status_code != 404:
                    raise ProtectedMediaUnavailable() from None
                missing = True
            if self._snapshot(access_check) != epoch:
                raise ProtectedMediaUnavailable()
            if missing:
                self._access(access_check)
                raise ProtectedMediaNotFound()
            _validate_jpeg(jpeg, self._dimensions)
            if expected_digest is not None and not hmac.compare_digest(hashlib.sha256(jpeg).hexdigest(), expected_digest):
                raise ProtectedMediaUnavailable()
            self._access(access_check)
            return jpeg
        except ProtectedMediaNotFound:
            raise
        except Exception:
            raise ProtectedMediaUnavailable() from None
