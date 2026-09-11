"""Bounded community dispatcher owned by the existing voice HTTP lifecycle.

dispatch(path, body, access_check, *, session_id) returns JSON dictionaries or
CommunityError. session_id is a stable opaque auth-session identifier supplied
by the broker, never a raw token or a browser body field. Ownership binds its
hash and this service's random lock epoch; restart invalidates prior ownership.
The voice server owns exact Host/Origin/session-header checks and no-store JSON;
access_check must capture the original token AND lock epoch, never a global
unlocked flag. Call lock() on revocation, and close(timeout) during voice drain.
Include close's literal True in the voice drain receipt: uncertainty is sticky.
No worker, daemon, background polling, secret loading or plaintext persistence.

SDK adapters are cloned with a per-invocation client guard. Their original
identity/trust guards remain mandatory. Provider calls must have bounded IO.
The synchronous dispatcher cannot interrupt an already dispatched remote write.
An uncertain write remains fenced even after lock and requires reconciliation.

protected_store is an operator-supplied ENCRYPTED journal, never browser input:
available()->True, reserve(uuid,fingerprint)->bool, get(uuid)->dict with exactly
fingerprint/result (result None means unresolved), complete(uuid,result)->True,
pending_count()->nonnegative int. Metadata-only evidence is preferred; protected
row scans require an operator-supplied storage lifecycle lease, after producers
are fenced and before vault lock. Never decrypt while locked or expose drain
rows to the user. Missing/unavailable evidence denies clean drain across restarts.
It must atomically reserve, bind arguments, durably record results, and never
reuse reservations. No implementation/key/codec is supplied or inferred here.
Definite pre-dispatch cancellations complete only newly acquired reservations
with {"cancelled": True}; journals must support this terminal tombstone.
Without it submit is unavailable; previews are bounded memory only.
Messaging may additionally use messaging_readiness()->reason plus
messaging_drained()->True: trusted integrations must positively verify the
node vault is active, authenticated transport, recipient policy, canonical
durable send idempotency and cross-restart unresolved-operation reconciliation.
This uses the server outbox, with no duplicate local message content. Protected
request fingerprints/receipts remain required to deny cross-session retries;
the SDK gets the original request UUID, never a new random-epoch-derived key.
Merely having the SDK installed is not evidence. Callbacks must be bounded.
dictation_available is an optional trusted bounded callback; only literal True
enables its status flag, with access checks before and after invocation.

source_fetch(source_id) returns exactly question/answer/has_photo from that
authorized completed answer, not ambient history. Preview request fields are
comparison assertions. Public preview question/answer match the SDK envelope;
the digest binds the complete canonical envelope bytes, including provenance.
No images or private source IDs are sent.
Sharing limits are 4096 UTF-8 bytes for the question and 8192 for the answer;
the SDK additionally bounds the complete escaped canonical envelope to 16384.
Oversized answers return share_answer_too_long (400), never truncated content.
Contacts are restricted to explicit trusted contact_names because the SDK has
name search, not a list-all endpoint. History without provider creation dates
is unavailable, never assigned invented dates. Mutation created_at timestamps
the local receipt record. Workflow completed/claimed never mean delivered/read.
Sharing additionally requires prepare_public(proposal, created_at)->True after
exact approval and before SDK submission, durably binding immutable provenance.
Preview creation alone must not prepare or authorize submission. The SDK journal
uses a distinct namespace from HTTP requests and requires that prepared proposal.
Sharing additionally requires protected_store.record_public(proposal, receipt,
created_at)->True and list_public(limit=21)->list of exact dictionaries with
proposal:PublicProposal, receipt:PublicReceipt, created_at:ISO date. These must
durably store authenticated encrypted canonical content and receipts, never a
plaintext copy. record_public must be idempotent for an identical receipt and
reject conflicts. A successful submission records this before its HTTP journal
completion. list_public must return the newest bounded retained entries, not
session previews; storage errors must raise, never masquerade as empty lists.
History survives lock/restart and is visible to newly authorized sessions for
the same configured public author/chain/domain. Preview approval remains
session-bound. Missing protected history denies sharing readiness and listing.
"""

import copy
import hashlib
import json
import math
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone

from .community_messaging import CommunityMessaging, HistoryItem, SendOutcome
from .community_public import CommunityPublic, PublicObservation, PublicProposal, PublicReceipt
from .message_contacts import ContactClaim
from .canonical_messaging import CanonicalMessageSender
from .mesh_contact_registry import MeshContactRegistry
from .mesh_contacts import VerifiedMeshContact
from .mesh_envelope import Peer
from .mesh_mailbox import MailboxItem, MeshMailbox
from .mesh_runtime import MeshRuntime
from .mesh_voice_ipc import MeshVoiceFacade
from .community_public_mesh import CommunityPublicMeshFacade
from .mesh_community import MeshActionAuthority, MeshRequestAccess
from .device_access import device_unlocked


MAX_RESPONSE = 262144
MAX_ITEMS = 20
MAX_PREVIEWS = 32
PREVIEW_TTL = 300
ROUTES = frozenset("/community/" + route for route in (
    "status", "radio/status", "messages/list", "contacts/list", "contacts/radio", "messages/send", "messages/open",
    "messages/render-challenge", "messages/rendered",
    "shares/preview", "shares/submit", "shares/list", "shares/status", "shares/refresh", "shares/inspect",
))
REASONS = frozenset(("ready", "not_ready", "transport_unavailable", "public_identity_unavailable",
                     "encrypted_storage_unavailable", "acceptance_proof_unavailable"))


class CommunityError(RuntimeError):
    def __init__(self, code, status=503):
        self.code = code
        self.status = status
        super().__init__(code)


def _error(code="community_unavailable", status=503):
    raise CommunityError(code, status) from None


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _text(value, limit, *, nonblank=True):
    try:
        return (type(value) is str and len(value.encode("utf-8")) <= limit
                and (not nonblank or bool(value.strip()))
                and not any(ord(char) < 32 and char not in "\n\t\r" or ord(char) == 127
                            or 0x202a <= ord(char) <= 0x202e or 0x2066 <= ord(char) <= 0x2069 for char in value))
    except UnicodeError:
        return False


def _id(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) is not None


def _uuid(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _receipt_uuid(value):
    return _uuid(value) and uuid.UUID(value).int != 0


def _receipt_inbox_id(value):
    match = re.fullmatch(r"mesh:inbox:([0-9a-f-]{36}):[0-9a-f]{64}", value) if type(value) is str else None
    return match is not None and _receipt_uuid(match[1])


def _date(value):
    try:
        if (type(value) is not str or len(value) > 40
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value) is None):
            return False
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


class _ClientGuard:
    def __init__(self, client, service):
        self.client = client
        self.service = service

    def __getattr__(self, name):
        member = getattr(self.client, name)
        if not callable(member):
            return member

        def invoke(*args, **kwargs):
            self.service._check()
            if name in {"message_send", "propose"}:
                self.service._local.submitted = True
            result = member(*args, **kwargs)
            self.service._check()
            return result

        return invoke


class _JournalGuard:
    def __init__(self, journal, service):
        self.journal = journal
        self.service = service

    def reserve(self, identifier, fingerprint):
        return self.service._reserve(self.journal, identifier, fingerprint)

    def __getattr__(self, name):
        return getattr(self.journal, name)


class CommunityMeshProvider:
    """Root-injected mesh facade; no fake SDK, FD factory, RF or read receipts.

    runtime_ready(access_check=...) must observe actual configured runtime gates;
    runtime_close(timeout=...) must stop/join its other producers before RF/FD
    teardown. Both are mandatory trusted callbacks, never request booleans.
    The mailbox producer fence also serializes registry policy with queueing.

    Production integration uses from_config(config, coordinator=...,
    activation_check=..., verify_binding=..., storage_lifecycle_check=...).
    Config names the existing identity and pinned local endpoint/chain; the
    coordinator is already exclusively opened and handshaken by root. Activation
    requires a genuine supervisor lease, never a browser option or marker alone.
    This path forwards reads and whole producer operations to MeshRuntime, with
    volatile request grants. Explicit send returns after durable queueing; radio
    continuation belongs to the separately consented supervisor worker. Unknown
    attempts are never automatically retried. Delivery assertions require
    explicit background consent; read assertions require separate root wiring.
    """

    def __init__(self, mailbox, registry, *, runtime_ready, runtime_close, clock=time.time):
        if (type(mailbox) is not MeshMailbox or type(registry) is not MeshContactRegistry
                or getattr(mailbox, "_runtime_owned", False)
                or registry.store is not mailbox.store or registry.owner != mailbox.owner
                or not all(callable(value) for value in (runtime_ready, runtime_close, clock))):
            raise ValueError('invalid_mesh_provider')
        self.mailbox, self.registry = mailbox, registry
        self.runtime_ready, self.runtime_close, self.clock = runtime_ready, runtime_close, clock
        self._runtime = None

    @classmethod
    def from_config(cls, config, *, coordinator, activation_check, verify_binding, storage_lifecycle_check):
        authority = MeshActionAuthority()
        runtime = MeshRuntime.from_config(config, coordinator=coordinator, activation_check=activation_check,
            authorize_context=authority.authorized, verify_binding=verify_binding,
            receipt_policy=authority.receipt_policy, storage_lifecycle_check=storage_lifecycle_check)
        from .mesh_delivery_receipts import MeshDeliveryReceipts
        runtime._delivery_receipts = MeshDeliveryReceipts(runtime, authority)
        return cls.from_runtime(runtime, authority)

    @classmethod
    def from_runtime(cls, runtime, authority):
        if (type(runtime) is not MeshRuntime or type(authority) is not MeshActionAuthority
                or runtime._authorize_context != authority.authorized):
            raise ValueError("invalid_mesh_provider")
        provider = cls.__new__(cls)
        provider._runtime, provider._authority, provider.clock = runtime, authority, time.time
        return provider

    def _grant(self, access_check, operations, **kwargs):
        return self._authority.grant(self._runtime.actor, access_check, operations, **kwargs)

    def start_receiving(self, *, consent_reader, lease_factory, unlocked_check=device_unlocked, interval=1):
        """Root daemon only; lease_factory(consent) returns a real CaptureLease.

        Consent is MeshReceiveConsent from trusted configured settings, not HTTP.
        A revision change stops this worker; restart requires another explicit
        call. pause_receiving joins background work only; full close must also
        fence UI reads before supervisor vault closure.
        Voice must use authenticated IPC, never instantiate a second factory.
        """
        if self._runtime is None or not all(callable(value) for value in (consent_reader, lease_factory, unlocked_check)):
            raise ValueError("mesh_background_unavailable")
        consent = consent_reader()
        def operations():
            if consent_reader() != consent:
                raise ValueError("mesh_background_unavailable")
            return tuple(operation for operation in ("receive", "background_dispatch")
                if self._authority.receive_allowed(consent, self._runtime.actor, operation, unlocked_check))
        if not operations():
            raise ValueError("mesh_background_unavailable")
        def grant(operation):
            if consent_reader() != consent:
                raise ValueError("mesh_background_unavailable")
            return self._authority.receive_grant(self._runtime.actor, operation, consent_reader, lease_factory, unlocked_check)
        self._runtime.start_receiving(grant, operations, interval=interval)

    def pause_receiving(self, timeout=10):
        if self._runtime is None:
            return False
        return self._runtime.pause_receiving(timeout=timeout)

    def ready(self, *, access_check):
        if self._runtime is not None:
            with self._grant(access_check, ("status",)) as context:
                return self._runtime.ready(context)
        if access_check() is not True:
            return False
        ready = self.runtime_ready(access_check=access_check)
        self.mailbox.store.storage.snapshot()
        return ready is True and access_check() is True

    def list_messages(self, box, *, access_check):
        if self._runtime is not None:
            with self._grant(access_check, ("list_messages",)) as context:
                return self._runtime.list_messages(context, box)
        return self.mailbox.list_messages(box, access_check=access_check)

    def open_message(self, identifier, *, access_check):
        if self._runtime is not None:
            with self._grant(access_check, ("open_message",)) as context:
                return self._runtime.open_message(context, identifier)
        return self.mailbox.open_message(identifier, access_check=access_check)

    def contact_id(self, card):
        owner = self._runtime.actor if self._runtime is not None else self.registry.owner
        binding = {'owner': asdict(owner), 'recipient': asdict(card.recipient)}
        return 'mesh-contact:' + hashlib.sha256(_json(binding)).hexdigest()

    def contacts(self, *, access_check):
        if self._runtime is not None:
            with self._grant(access_check, ("contacts",)) as context:
                return self._runtime.contacts(context)
        with self.mailbox._read(access_check) as check:
            cards = self.registry.list_current(access_check=check)
            cards = sorted(cards, key=lambda card: (card.chain_id, card.agent_id))[:MAX_ITEMS + 1]
            verified = []
            for card in cards:
                current = self.registry.resolve(bytes.fromhex(card.radio_public_key), access_check=check)
                if current != card:
                    _error()
                verified.append(current)
            result = tuple(verified)
        return result

    def queue(self, card, *, text, request_id, approval_binding, access_check, on_submit):
        if type(card) is not VerifiedMeshContact or not _text(text, 512):
            _error('invalid_request', 400)
        if self._runtime is not None:
            original_access = access_check
            if type(original_access) is not MeshRequestAccess:
                _error()
            deadline = time.monotonic() + self._runtime.config.operation_timeout
            access_check = MeshRequestAccess(original_access.epoch,
                lambda: time.monotonic() < deadline and original_access() is True)
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now) or not 1 <= now <= 0xffffffff:
                _error()
            now = int(now)
            arguments = dict(message_id=request_id, recipient=Peer(card.chain_id, bytes.fromhex(card.agent_id)),
                radio_public_key=bytes.fromhex(card.radio_public_key), content=text,
                consent_binding=hashlib.sha256(_json(asdict(card))).hexdigest(),
                issued_at=now, expires_at=min(now + 3600, card.expires_at))
            expected = self._authority.message(self._runtime.actor, **arguments)
            with self._grant(access_check, ("queue_message", "open_message"),
                             approval_binding=approval_binding, message=expected) as context:
                on_submit()
                self._runtime.queue_message(context, **arguments, expected_contact=card)
                identifier = self._runtime.outbox_id(request_id, card.recipient)
                return self._runtime.open_message(context, identifier)
        with self.mailbox._read(access_check) as check:
            current = self.registry.resolve(bytes.fromhex(card.radio_public_key), access_check=check)
            if current != card:
                _error('conflict', 409)
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now) or not 1 <= now <= 2 ** 53 - 1:
                _error()
            now = int(now)
            consent_binding = hashlib.sha256(_json(asdict(current))).hexdigest()
            on_submit()
            progress = self.mailbox.outbox.queue(message_id=request_id,
                recipient=Peer(current.chain_id, bytes.fromhex(current.agent_id)),
                radio_public_key=bytes.fromhex(current.radio_public_key), content=text,
                approval_binding=approval_binding, consent_binding=consent_binding,
                issued_at=now, expires_at=min(now + 3600, current.expires_at), access_check=check)
            identifier = self.mailbox._id('outbox', progress.message_id, progress.message_id,
                                           self.mailbox.owner, current.recipient)
        return self.mailbox.open_message(identifier, access_check=access_check)

    def close(self, timeout):
        if self._runtime is not None:
            return self._runtime.close(timeout=timeout) is True if timeout > 0 else False
        deadline = time.monotonic() + timeout
        try:
            joined = self.mailbox.outbox.close(min(30, timeout)) is True
        except Exception:
            joined = False
        stopped = self.runtime_close(timeout=max(0, deadline - time.monotonic())) is True
        return joined and stopped


class CommunityService:
    def __init__(self, *, community_messaging=None, community_public=None, protected_store=None,
                 source_fetch=None, contact_names=(), public_readiness=None,
                 messaging_readiness=None, messaging_drained=None, dictation_available=None, clock=time.monotonic,
                 wall_clock=time.time, canonical_messages=None, mesh_provider=None):
        if (type(contact_names) not in (tuple, list) or len(contact_names) > 4
                or any(not _text(name, 128) for name in contact_names)):
            raise ValueError("invalid_community_configuration")
        if mesh_provider is not None and (type(mesh_provider) not in (CommunityMeshProvider, MeshVoiceFacade)
                                          or community_messaging is not None or canonical_messages is not None):
            raise ValueError('invalid_mesh_provider')
        self._mutex = threading.Condition()
        self._busy = False
        self._closed = False
        self._generation = 0
        self._epoch = str(uuid.uuid4())
        self._uncertain = False
        self._local = threading.local()
        self._previews = {}
        self._render_challenges = {}
        self._contacts = {}
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_clock = None
        self._store = protected_store
        self._source = source_fetch
        self._names = tuple(contact_names)
        self._public_readiness = public_readiness
        self._messaging_readiness = messaging_readiness
        self._messaging_drained = messaging_drained
        self._dictation_available = dictation_available
        self._messaging = None
        self._public = None
        self._canonical = None
        self._mesh = mesh_provider
        self._public_mesh = (CommunityPublicMeshFacade(mesh_provider)
                             if type(mesh_provider) is MeshVoiceFacade and community_public is None else None)
        if community_messaging is not None:
            if not isinstance(community_messaging, CommunityMessaging):
                raise ValueError("invalid_community_provider")
            self._messaging = copy.copy(community_messaging)
            self._messaging._client = _ClientGuard(community_messaging._client, self)
        if canonical_messages is not None:
            if type(canonical_messages) is not CanonicalMessageSender or canonical_messages.adapter is not community_messaging:
                raise ValueError("invalid_canonical_messaging")
            self._canonical = canonical_messages.bind(self._messaging)
        if community_public is not None:
            if not isinstance(community_public, CommunityPublic):
                raise ValueError("invalid_community_provider")
            self._public = copy.copy(community_public)
            self._public.client = _ClientGuard(community_public.client, self)
            self._public.journal = _JournalGuard(community_public.journal, self)
            if community_public._online_verifier is not None:
                original_client = community_public.client

                def online_trust(client):
                    if client is not self._public.client or community_public.client is not original_client:
                        _error()
                    evidence = self._call(community_public._online_trust)
                    if community_public.client is not original_client or evidence is None:
                        _error()
                    return replace(evidence, client=client)

                self._public._online_verifier = online_trust

    def lock(self):
        with self._mutex:
            self._generation += 1
            self._epoch = str(uuid.uuid4())
            self._previews.clear()
            self._render_challenges.clear()
            self._contacts.clear()

    def close(self, timeout=0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 60:
            return False
        deadline = time.monotonic() + timeout
        with self._mutex:
            self._closed = True
            self.lock()
            drained = self._mutex.wait_for(lambda: not self._busy, timeout)
        mesh_drained = True
        if self._mesh is not None:
            try:
                mesh_drained = self._mesh.close(max(0, deadline - time.monotonic())) is True
            except Exception:
                mesh_drained = False
        with self._mutex:
            if not drained or self._uncertain or not mesh_drained:
                return False
            if self._canonical is not None and self._canonical.drained() is not True:
                return False
            if self._messaging_readiness is not None:
                try:
                    if self._messaging_drained is None or self._messaging_drained() is not True:
                        return False
                except Exception:
                    return False
            if self._store is not None:
                try:
                    pending = self._store.pending_count()
                    if type(pending) is not int or pending != 0:
                        return False
                except Exception:
                    return False
            return True

    def _check(self):
        with self._mutex:
            if self._closed or self._local.generation != self._generation:
                _error("device_locked", 403)
        try:
            allowed = self._local.access_check() is True
        except Exception:
            allowed = False
        if not allowed:
            self.lock()
            _error("device_locked", 403)
        with self._mutex:
            if self._closed or self._local.generation != self._generation:
                _error("device_locked", 403)

    def _call(self, method, *args, **kwargs):
        self._check()
        result = method(*args, **kwargs)
        self._check()
        return result

    def _guard(self):
        self._check()
        return True

    def _mesh_access(self):
        self._check()
        generation, access_check = self._local.generation, self._local.access_check
        active = self._local.mesh_active
        epoch = hashlib.sha256(_json(self._local.owner)).hexdigest()[:32]
        def check():
            with self._mutex:
                current = active.is_set() and not self._closed and generation == self._generation
            if not current or access_check() is not True:
                return False
            with self._mutex:
                return active.is_set() and not self._closed and generation == self._generation
        return MeshRequestAccess(epoch, check)

    def _storage_ready(self):
        return self._store is not None and self._call(self._store.available) is True

    def _now(self):
        value = self._clock()
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                or self._last_clock is not None and value < self._last_clock):
            self.lock()
            _error()
        self._last_clock = value
        return value

    @contextmanager
    def _admit(self, access_check, session_id):
        if not callable(access_check) or not _id(session_id):
            _error("device_locked", 403)
        with self._mutex:
            if self._closed:
                _error("device_locked", 403)
            if self._busy:
                _error("busy", 409)
            self._busy = True
            self._local.generation = self._generation
            self._local.access_check = access_check
            self._local.session = hashlib.sha256(session_id.encode()).hexdigest()
            self._local.owner = (self._local.session, self._epoch)
            self._local.submitted = False
            self._local.reservations = []
            self._local.preparations = []
            self._local.mesh_active = threading.Event()
            self._local.mesh_active.set()
        try:
            self._check()
            yield
        finally:
            with self._mutex:
                self._busy = False
                self._local.mesh_active.clear()
                self._local.__dict__.clear()
                self._mutex.notify_all()

    def dispatch(self, path, body, access_check, *, session_id):
        with self._admit(access_check, session_id):
            try:
                if path not in ROUTES:
                    _error("not_found", 404)
                self._validate(path, body)
                if path == "/community/shares/status":
                    return self._source_status(body["source_ids"])
                now = self._now()
                with self._mutex:
                    self._previews = {key: value for key, value in self._previews.items() if value["expires"] > now}
                response = self._dispatch(path.rsplit("/", 2)[-2:], body, now)
                self._check()
                self._validate_response(path, response)
                if len(_json(response)) > MAX_RESPONSE:
                    _error()
                return response
            except Exception as error:
                if path == "/community/radio/status":
                    import syslog
                    import traceback
                    frames = traceback.extract_tb(error.__traceback__)
                    syslog.syslog(syslog.LOG_ERR, json.dumps(dict(event="radio_status_failed",
                        error=type(error).__name__, frames=[f"{frame.name}:{frame.lineno}" for frame in frames])))
                if not self._local.submitted and not self._uncertain:
                    for journal, identifier in reversed(self._local.reservations):
                        try:
                            if journal.complete(identifier, {"cancelled": True}) is not True:
                                self._uncertain = True
                        except Exception:
                            self._uncertain = True
                    if not self._uncertain:
                        for proposal, created_at in self._local.preparations:
                            try:
                                if self._store.cancel_prepared_public(proposal, created_at) is not True:
                                    self._uncertain = True
                            except Exception:
                                self._uncertain = True
                if (self._uncertain or getattr(self._local, "submitted", False)
                        or isinstance(error, CommunityError) and error.code == "outcome_unknown"):
                    self._uncertain = True
                    _error("outcome_unknown")
                if isinstance(error, CommunityError):
                    raise
                _error()

    def _reserve(self, journal, identifier, fingerprint):
        self._check()
        try:
            reserved = journal.reserve(identifier, fingerprint)
        except Exception:
            self._uncertain = True
            _error("outcome_unknown")
        if reserved is True:
            self._local.reservations.append((journal, identifier))
        self._check()
        return reserved

    @staticmethod
    def _validate(path, body):
        fields = {
            "status": set(), "radio/status": set(), "messages/list": {"box"}, "contacts/list": set(), "contacts/radio": set(),
            "messages/send": {"recipient_id", "paragraphs", "request_id", "consent"},
            "messages/open": {"id", "share_read_receipt"},
            "messages/render-challenge": {"id", "rendered"},
            "messages/rendered": {"id", "challenge_id", "rendered", "share_read_receipt"},
            "shares/preview": {"source_id", "question", "answer", "has_photo"},
            "shares/submit": {"preview_id", "digest", "request_id", "consent"}, "shares/list": set(),
            "shares/status": {"source_ids"},
            "shares/refresh": {"id", "request_id", "consent"},
            "shares/inspect": {"id", "request_id"},
        }[path[len("/community/"):]]
        if type(body) is not dict or set(body) != fields:
            _error("invalid_request", 400)
        if len(_json(body)) > 32768:
            _error("invalid_request", 400)
        if "source_ids" in body:
            identifiers = body["source_ids"]
            if (type(identifiers) is not list or len(identifiers) > 20
                    or any(not _uuid(value) for value in identifiers) or len(set(identifiers)) != len(identifiers)):
                _error("invalid_request", 400)
        for name in ("source_id", "preview_id", "request_id"):
            if name in body and not _uuid(body[name]):
                _error("invalid_request", 400)
        for name in ("id", "recipient_id"):
            if name in body and not _id(body[name]):
                _error("invalid_request", 400)
        if "box" in body and body["box"] not in ("inbox", "outbox"):
            _error("invalid_request", 400)
        if "consent" in body and body["consent"] is not True:
            _error("invalid_request", 400)
        rendered_route = path in ("/community/messages/render-challenge", "/community/messages/rendered")
        if rendered_route and (not _receipt_inbox_id(body["id"]) or body["rendered"] is not True):
            _error("invalid_request", 400)
        if "challenge_id" in body and not _receipt_uuid(body["challenge_id"]):
            _error("invalid_request", 400)
        if "share_read_receipt" in body and body["share_read_receipt"] is not (path == "/community/messages/rendered"):
            _error("invalid_request", 400)
        if "paragraphs" in body:
            paragraphs = body["paragraphs"]
            if (type(paragraphs) is not list or not 1 <= len(paragraphs) <= 32
                    or any(not _text(text, 2048) for text in paragraphs) or not _text("\n\n".join(paragraphs), 8192)):
                _error("invalid_request", 400)
        if "source_id" in body and _text(body["answer"], 32768) and len(body["answer"].encode("utf-8")) > 8192:
            _error("share_answer_too_long", 400)
        if "source_id" in body and (not _text(body["question"], 4096) or not _text(body["answer"], 8192)
                                  or type(body["has_photo"]) is not bool):
            _error("invalid_request", 400)
        if "digest" in body and (type(body["digest"]) is not str or re.fullmatch(r"[0-9a-f]{64}", body["digest"]) is None):
            _error("invalid_request", 400)

    def _caps(self):
        if self._messaging is None:
            return {}
        value = self._call(self._messaging.capabilities, self._local.session)
        if type(value) is not dict or any(type(value.get(key)) is not bool for key in ("inbox", "outbox", "directory", "send")):
            _error()
        return value

    def _public_reason(self):
        if self._public_mesh is not None:
            if self._source is None:
                return "not_ready"
            try:
                return self._call(self._public_mesh.readiness, access_check=self._mesh_access())
            except Exception:
                return "not_ready"
        if self._public is None:
            return "not_ready"
        if not self._storage_ready():
            return "encrypted_storage_unavailable"
        source_ready = getattr(self._store, "source_approval_available", None)
        if callable(source_ready) and self._call(source_ready) is not True:
            return "not_ready"
        if any(not callable(getattr(self._store, name, None))
               for name in ("prepare_public", "list_public", "record_public")):
            return "not_ready"
        if self._public_readiness is None:
            return "public_identity_unavailable"
        reason = self._call(self._public_readiness)
        return reason if type(reason) is str and reason in REASONS else "not_ready"

    def _status(self):
        if self._uncertain:
            message_reason = public_reason = "not_ready"
        else:
            message_reason = self._messaging_reason()
            public_reason = self._public_reason()
        return {"schema": "sage.lantern.community.v1",
                "messaging": {"available": message_reason == "ready", "reason": message_reason},
                "sharing": {"available": public_reason == "ready", "reason": public_reason},
                "dictation": {"available": not self._uncertain and callable(self._dictation_available)
                              and self._call(self._dictation_available) is True}}

    def _messaging_reason(self):
        if self._mesh is not None:
            return 'ready' if self._call(self._mesh.ready, access_check=self._mesh_access()) is True else 'not_ready'
        if self._messaging is None:
            return "not_ready"
        if self._canonical is not None:
            if self._call(self._canonical.ready) is not True:
                return "encrypted_storage_unavailable"
        elif not self._storage_ready():
            return "encrypted_storage_unavailable"
        if self._messaging_readiness is not None:
            reason = self._call(self._messaging_readiness)
            if type(reason) is not str or reason not in REASONS:
                return "not_ready"
            if reason != "ready":
                return reason
            if self._messaging_drained is None:
                return "not_ready"
        return "ready" if self._caps().get("send") is True else "not_ready"

    def _page(self, items, *, more=False):
        result = {"items": [], "more": more}
        for item in items:
            if len(result["items"]) >= MAX_ITEMS or len(_json({"items": [*result["items"], item], "more": True})) > MAX_RESPONSE:
                result["more"] = True
                break
            result["items"].append(item)
        return result

    def _messages(self, box):
        if self._mesh is not None:
            page = self._call(self._mesh.list_messages, box, access_check=self._mesh_access())
            return self._page([self._mesh_item(item) for item in page.items], more=page.more)
        operation = "inbox" if box == "inbox" else "outbox"
        if not self._caps().get(operation):
            _error()
        method = self._messaging.inbox_history if box == "inbox" else self._messaging.outbox
        rows = self._call(method, self._local.session, limit=21)
        if type(rows) is not tuple or len(rows) > 21:
            _error()
        items = []
        for row in rows:
            if type(row) is not HistoryItem or not _id(row.message_id) or not _text(row.payload, 8192, nonblank=False):
                _error()
            peer = row.sender if box == "inbox" else row.recipient
            created_at = getattr(row, "created_at", None)
            if not _date(created_at):
                _error()
            items.append(dict(id=row.message_id, peer_name=peer.agent_id, text=row.payload, state="unknown", created_at=created_at))
        return self._page(items, more=len(rows) == 21)

    def _directory(self, *, radio=False):
        if self._mesh is not None:
            cards = self._call(self._mesh.contacts, access_check=self._mesh_access())
            page = self._page([dict(id=self._mesh.contact_id(card), name=card.display_name,
                **(dict(radio_public_key=card.radio_public_key, chain_id=card.chain_id,
                    agent_id=card.agent_id, expires_at=card.expires_at) if radio else {})) for card in cards],
                              more=len(cards) > MAX_ITEMS)
            selected = {item['id'] for item in page['items']}
            with self._mutex:
                self._check()
                self._contacts = {self._mesh.contact_id(card): (self._local.owner, card)
                                  for card in cards if self._mesh.contact_id(card) in selected}
            return page
        if radio or not self._names or not self._caps().get("directory"):
            _error()
        items = {}
        contacts = {}
        more = False
        for name in self._names:
            claims = self._call(self._messaging.directory, self._local.session, name=name, limit=21)
            if type(claims) is not tuple or len(claims) > 21:
                _error()
            more = more or len(claims) == 21
            for claim in claims:
                if type(claim) is not ContactClaim or not _text(claim.display_name, 256):
                    _error()
                key = claim.recipient.agent_id
                contacts[key] = claim
                items[key] = {"id": key, "name": claim.display_name}
        page = self._page(list(items.values()), more=more)
        with self._mutex:
            self._check()
            self._contacts = {item["id"]: (self._local.owner, contacts[item["id"]]) for item in page["items"]}
        return page

    def _preview(self, body, now):
        if (self._public is None and self._public_mesh is None) or self._source is None:
            _error()
        snapshot = self._call(self._source, body["source_id"])
        expected = {key: body[key] for key in ("question", "answer", "has_photo")}
        if type(snapshot) is not dict or snapshot != expected or type(snapshot.get("has_photo")) is not bool:
            _error("conflict", 409)
        identifier = str(uuid.uuid4())
        if self._public_mesh is not None:
            proposal = self._call(self._public_mesh.prepare, operation_id=identifier,
                question=snapshot["question"], answer=snapshot["answer"], access_check=self._mesh_access())
        else:
            proposal = self._call(self._public.prepare, operation_id=identifier,
                                  question=snapshot["question"], answer=snapshot["answer"])
        if type(proposal) is not PublicProposal or hashlib.sha256(proposal.content.encode()).hexdigest() != proposal.content_hash:
            _error()
        response = dict(preview_id=identifier, digest=proposal.content_hash,
                        question=snapshot["question"], answer=snapshot["answer"], photo_included=False,
                        expires_at=self._timestamp(PREVIEW_TTL))
        with self._mutex:
            self._check()
            if len(self._previews) >= MAX_PREVIEWS:
                _error("busy", 409)
            self._previews[identifier] = dict(proposal=proposal, response=response, expires=now + PREVIEW_TTL,
                                             revision=self._generation, owner=self._local.owner, item=None, receipt=None,
                                             source_id=body["source_id"], has_photo=snapshot["has_photo"])
        return copy.deepcopy(response)

    def _mutation(self, body, operation):
        if self._uncertain:
            _error("outcome_unknown")
        if operation == "submit" and self._public_mesh is not None:
            return self._mesh_public_submit(body)
        if operation == 'send' and self._mesh is not None:
            text = '\n\n'.join(body['paragraphs'])
            if not _text(text, 512):
                _error('invalid_request', 400)
            card = self._send_preflight(body)
            approval = hashlib.sha256(_json({'body': body, 'owner': self._local.owner})).hexdigest()
            def submitted():
                self._check()
                self._local.submitted = True
            item = self._call(self._mesh.queue, card, text=text, request_id=body['request_id'],
                              approval_binding=approval, access_check=self._mesh_access(), on_submit=submitted)
            self._mesh_item(item)
            return dict(id=item.id, state=item.state, created_at=item.created_at)
        if operation == "send" and self._canonical is not None:
            claim = self._send_preflight(body)
            outcome = self._call(self._canonical.send, self._local.session, request_id=body["request_id"],
                                 recipient=claim.recipient, payload="\n\n".join(body["paragraphs"]), access_check=self._guard)
            if type(outcome) is not SendOutcome or outcome.outcome != "accepted_by_node" or not _id(outcome.message_id):
                _error("outcome_unknown")
            return dict(id=outcome.message_id, state="sent", created_at=self._timestamp())
        if not self._storage_ready():
            _error()
        if operation == "send":
            self._send_preflight(body)
        else:
            preview = self._previews.get(body["preview_id"])
            if preview is None or preview["owner"] != self._local.owner:
                _error("not_found", 404)
            if preview["response"]["digest"] != body["digest"]:
                _error("conflict", 409)
            if self._public_reason() != "ready":
                _error()
        fingerprint = hashlib.sha256(_json({"operation": operation, "body": body, "owner": self._local.owner})).hexdigest()
        reserved = self._reserve(self._store, body["request_id"], fingerprint)
        if reserved is not True:
            record = self._call(self._store.get, body["request_id"])
            if type(record) is not dict or set(record) != {"fingerprint", "result"}:
                _error("outcome_unknown")
            if record["fingerprint"] != fingerprint:
                _error("conflict", 409)
            if record["result"] is None:
                _error("outcome_unknown")
            if record["result"] == {"cancelled": True}:
                _error("conflict", 409)
            return record["result"]
        if operation == "send":
            response = self._send(body)
        else:
            created_at = self._timestamp()
            prepare_source = getattr(self._store, "prepare_public_source", None)
            prepared = (self._call(prepare_source, preview["proposal"], created_at, preview["source_id"])
                        if callable(prepare_source) else self._call(self._store.prepare_public, preview["proposal"], created_at))
            if prepared is not True:
                _error("outcome_unknown")
            if callable(getattr(self._store, "cancel_prepared_public", None)):
                self._local.preparations.append((preview["proposal"], created_at))
            observation = self._call(self._public.submit, preview["proposal"], access_check=self._guard)
            if type(observation) is not PublicObservation or observation.receipt is None:
                _error("outcome_unknown")
            item = dict(id=observation.receipt.memory_id, question=preview["response"]["question"],
                        state="pending", created_at=created_at)
            entry = dict(proposal=preview["proposal"], receipt=observation.receipt, created_at=item["created_at"])
            self._validate_public_entry(entry)
            if self._call(self._store.record_public, entry["proposal"], entry["receipt"], entry["created_at"]) is not True:
                _error("outcome_unknown")
            preview["item"], preview["receipt"] = item, observation.receipt
            response = {key: item[key] for key in ("id", "state", "created_at")}
        if self._call(self._store.complete, body["request_id"], response) is not True:
            _error("outcome_unknown")
        return response

    def _mesh_public_submit(self, body):
        preview = self._previews.get(body["preview_id"])
        if preview is None or preview["owner"] != self._local.owner:
            _error("not_found", 404)
        if preview["response"]["digest"] != body["digest"]:
            _error("conflict", 409)
        if self._public_reason() != "ready":
            _error()
        snapshot = self._call(self._source, preview["source_id"])
        expected = dict(question=preview["response"]["question"], answer=preview["response"]["answer"],
                        has_photo=preview["has_photo"])
        if type(snapshot) is not dict or snapshot != expected or type(snapshot.get("has_photo")) is not bool:
            _error("conflict", 409)
        if "request_id" in preview and preview["request_id"] != body["request_id"]:
            _error("conflict", 409)
        preview.setdefault("request_id", body["request_id"])
        preview.setdefault("created_at", self._timestamp())
        approval = hashlib.sha256(_json(dict(body=body, owner=self._local.owner))).hexdigest()
        def submitted():
            self._check()
            self._local.submitted = True
        return self._call(self._public_mesh.queue, preview["proposal"], source_id=preview["source_id"],
            created_at=preview["created_at"], request_id=body["request_id"], approval_binding=approval,
            access_check=self._mesh_access(), on_submit=submitted)

    def _send_preflight(self, body):
        entry = self._contacts.get(body["recipient_id"])
        if entry is None or entry[0] != self._local.owner or self._messaging_reason() != "ready":
            _error()
        return entry[1]

    def _send(self, body):
        claim = self._send_preflight(body)
        outcome = self._call(self._messaging.send, self._local.session, recipient=claim.recipient,
                             payload="\n\n".join(body["paragraphs"]), idempotency_key=body["request_id"],
                             ttl_minutes=1440, approved=True)
        if type(outcome) is not SendOutcome or outcome.outcome != "accepted_by_node" or not _id(outcome.message_id):
            _error("outcome_unknown")
        return dict(id=outcome.message_id, state="sent", created_at=self._timestamp())

    def _render_receipt(self, action, body):
        if type(self._mesh) is not MeshVoiceFacade:
            _error()
        if self._uncertain:
            _error("outcome_unknown")
        wall = self._wall_clock()
        if type(wall) not in (int, float) or not math.isfinite(wall) or wall <= 0:
            _error()
        now = self._now()
        with self._mutex:
            self._render_challenges = {key: value for key, value in self._render_challenges.items()
                                       if value["expires_at"] > wall and value["deadline"] > now}
        access = self._mesh_access()
        if action == "render-challenge":
            if len(self._render_challenges) >= MAX_PREVIEWS:
                _error("busy", 409)
            response = self._call(self._mesh.render_challenge, body["id"], rendered=True, access_check=access)
            self._validate_response("/community/messages/render-challenge", response)
            current_wall = self._wall_clock()
            if (type(current_wall) not in (int, float) or not math.isfinite(current_wall)
                    or not wall <= current_wall < response["expires_at"] <= current_wall + 60
                    or response["id"] != body["id"]):
                _error()
            with self._mutex:
                self._check()
                if response["challenge_id"] in self._render_challenges:
                    _error()
                self._render_challenges[response["challenge_id"]] = dict(
                    id=body["id"], owner=self._local.owner, expires_at=response["expires_at"], deadline=now + 60)
        else:
            saved = self._render_challenges.get(body["challenge_id"])
            if saved is None or saved["id"] != body["id"] or saved["owner"] != self._local.owner:
                _error("invalid_request", 400)
            def submitted():
                self._check()
                dispatch_wall = self._wall_clock()
                if (type(dispatch_wall) not in (int, float) or not math.isfinite(dispatch_wall)
                        or not wall <= dispatch_wall < saved["expires_at"] or self._now() >= saved["deadline"]):
                    _error("invalid_request", 400)
                self._local.submitted = True
                self._render_challenges.pop(body["challenge_id"], None)
            response = self._call(self._mesh.rendered, body["id"], challenge_id=body["challenge_id"],
                                  rendered=True, share_read_receipt=True, access_check=access, on_submit=submitted)
            self._validate_response("/community/messages/rendered", response)
            if response["id"] != body["id"]:
                _error()
            if response["state"] == "unknown":
                self._uncertain = True
        return response

    def _dispatch(self, route, body, now):
        section, action = route
        if section == 'radio' and action == 'status':
            if type(self._mesh) is not MeshVoiceFacade:
                _error()
            return self._call(self._mesh.radio_status, access_check=self._mesh_access())
        if action == "status":
            return self._status()
        if section == "messages":
            if action in ("render-challenge", "rendered"):
                return self._render_receipt(action, body)
            if action == "list":
                return self._messages(body["box"])
            if action == "send":
                return self._mutation(body, "send")
            if self._mesh is not None:
                item = self._call(self._mesh.open_message, body['id'], access_check=self._mesh_access())
                self._mesh_item(item)
                return dict(id=item.id, state=item.state, created_at=item.created_at)
            for box in ("inbox", "outbox"):
                for item in self._messages(box)["items"]:
                    if item["id"] == body["id"]:
                        return {key: item[key] for key in ("id", "state", "created_at")}
            _error("not_found", 404)
        if section == "contacts":
            return self._directory(radio=action == 'radio')
        if action == "preview":
            return self._preview(body, now)
        if action == "submit":
            return self._mutation(body, "submit")
        if action in ("refresh", "inspect"):
            return self._share_refresh(body, inspect=action == "inspect")
        return self._public_history()

    def _share_refresh(self, body, *, inspect):
        if self._public_mesh is not None:
            if self._uncertain and not inspect:
                _error("outcome_unknown")
            access = self._mesh_access()
            proposal, original, created_at = self._call(self._public_mesh.resolve, body["id"], access_check=access)
            result = None
            if inspect:
                progress, result = self._call(self._public_mesh.inspect, proposal,
                    request_id=body["request_id"], access_check=access)
            else:
                def submitted():
                    self._check()
                    self._local.submitted = True
                progress = self._call(self._public_mesh.refresh, proposal, submission_request_id=original,
                    request_id=body["request_id"], access_check=access, on_submit=submitted)
            state = "unknown"
            if progress.state == "queued" and progress.next_fragment == 0:
                state = "queued"
            elif progress.state == "consumed" and result is not None and result["state"] in {"receipt", "observed"}:
                state = "pending"
            return dict(id=body["id"], request_id=body["request_id"], created_at=created_at, state=state,
                observed_status=None if result is None else result["observed_status"], trust_basis="pinned_gateway",
                acceptance_available=False, consensus_verified=False, cache_eligible=False)
        if self._public_reason() != "ready":
            _error()
        rows = self._call(self._store.list_public, limit=21)
        if type(rows) is not list or len(rows) > 21:
            _error()
        for entry in rows:
            self._validate_public_entry(entry)
        identifiers = [entry["receipt"].memory_id for entry in rows]
        if len(set(identifiers)) != len(identifiers):
            _error()
        for entry in rows[:MAX_ITEMS]:
            if entry["receipt"].memory_id != body["id"]:
                continue
            observation = self._call(self._public.poll, entry["proposal"], entry["receipt"], access_check=self._guard)
            state = self._public_observation_state(observation)
            return dict(id=body["id"], request_id=body["request_id"], created_at=entry["created_at"], state=state,
                observed_status=observation.observed_status, trust_basis=observation.trust_basis,
                acceptance_available=state in {"accepted", "challenged", "deprecated"},
                consensus_verified=False, cache_eligible=False)
        _error("not_found", 404)

    @staticmethod
    def _public_observation_state(observation):
        if type(observation) is not PublicObservation:
            _error()
        state = "pending" if observation.content_bound else "unknown"
        if observation.observed_status in ("challenged", "deprecated"):
            state = "unknown"
        verified = {"committed": "accepted", "challenged": "challenged", "deprecated": "deprecated"}
        if (observation.content_bound is True and observation.chain_verified is True
                and observation.acceptance_available is True and observation.trust_basis == "trusted_node_online"
                and observation.reason == "online_node_observation"
                and observation.state == verified.get(observation.observed_status)):
            state = observation.state
        return state

    @staticmethod
    def _mesh_item(item):
        if type(item) is not MailboxItem:
            _error()
        peer = item.sender if item.box == 'inbox' else item.recipient
        return dict(id=item.id, peer_name=peer.chain_id + ':' + peer.agent_id, text=item.text,
                    state=item.state, created_at=item.created_at)

    def _validate_public_entry(self, entry):
        if type(entry) is not dict or set(entry) != {"proposal", "receipt", "created_at"}:
            _error()
        proposal, receipt = entry["proposal"], entry["receipt"]
        self._call(self._public._proposal, proposal)
        if (type(receipt) is not PublicReceipt or not _date(entry["created_at"])
                or receipt.operation_id != proposal.operation_id or receipt.content_hash != proposal.content_hash
                or receipt.intended_chain_id != self._public.chain_id or not _uuid(receipt.memory_id)
                or type(receipt.tx_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", receipt.tx_hash) is None
                or receipt.committed_height is not None and (type(receipt.committed_height) is not int or receipt.committed_height <= 0)):
            _error()
        content = json.loads(proposal.content)
        if not _text(content["question"], 4096) or not _text(content["answer"], 8192):
            _error()
        return content["question"]

    def _public_history(self):
        if self._public_mesh is not None:
            if self._uncertain or self._public_reason() != "ready":
                _error()
            return self._call(self._public_mesh.list, access_check=self._mesh_access())
        if self._public_reason() != "ready":
            _error()
        rows = self._call(self._store.list_public, limit=21)
        if type(rows) is not list or len(rows) > 21:
            _error()
        questions = [self._validate_public_entry(entry) for entry in rows]
        identifiers = [entry["receipt"].memory_id for entry in rows]
        if len(set(identifiers)) != len(identifiers):
            _error()
        items = []
        for entry, question in zip(rows[:MAX_ITEMS], questions[:MAX_ITEMS]):
            observation = self._call(self._public.poll, entry["proposal"], entry["receipt"], access_check=self._guard)
            if type(observation) is not PublicObservation:
                _error()
            state = "pending" if observation.content_bound else "unknown"
            if observation.observed_status in ("challenged", "deprecated"):
                state = "unknown"
            verified_states = {"committed": "accepted", "challenged": "challenged", "deprecated": "deprecated"}
            if (observation.content_bound is True and observation.chain_verified is True
                    and observation.acceptance_available is True
                    and observation.trust_basis == "trusted_node_online"
                    and observation.reason == "online_node_observation"
                    and observation.state == verified_states.get(observation.observed_status)):
                state = observation.state
            items.append(dict(id=entry["receipt"].memory_id, question=question, state=state, created_at=entry["created_at"]))
        return self._page(items, more=len(rows) > MAX_ITEMS)

    def _source_status(self, source_ids):
        if self._public_mesh is not None:
            if self._uncertain or self._public_reason() != "ready":
                _error()
            return self._call(self._public_mesh.sources, source_ids, access_check=self._mesh_access())
        lookup = getattr(self._store, "public_sources", None)
        if self._uncertain or self._public_reason() != "ready" or not callable(lookup):
            _error()
        rows = self._call(lookup, source_ids)
        if type(rows) is not dict or set(rows) != set(source_ids):
            _error()
        items = []
        for source in source_ids:
            entry = rows[source]
            state = "not_shared" if entry is None else "unknown"
            if entry is not None:
                if type(entry) is not dict or set(entry) != {"proposal", "receipt", "created_at"}:
                    _error()
                self._call(self._public._proposal, entry["proposal"])
                if not _date(entry["created_at"]):
                    _error()
                if entry["receipt"] is not None:
                    self._validate_public_entry(entry)
                    observation = self._call(self._public.poll, entry["proposal"], entry["receipt"], access_check=self._guard)
                    if type(observation) is not PublicObservation:
                        _error()
                    state = "pending" if observation.content_bound is True else "unknown"
                    verified = {"committed": "accepted", "challenged": "challenged", "deprecated": "deprecated"}
                    if observation.observed_status in ("challenged", "deprecated"):
                        state = "unknown"
                    if (observation.content_bound is True and observation.chain_verified is True
                            and observation.acceptance_available is True and observation.trust_basis == "trusted_node_online"
                            and observation.reason == "online_node_observation"
                            and observation.state == verified.get(observation.observed_status)):
                        state = observation.state
            items.append(dict(source_id=source, state=state))
        self._check()
        return dict(items=items)

    def _timestamp(self, offset=0):
        value = self._wall_clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            _error()
        return datetime.fromtimestamp(value + offset, timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate_response(path, response):
        if path == '/community/radio/status':
            from .mesh_radio_events import validate_status
            validate_status(response)
            return
        def exact(value, keys):
            if type(value) is not dict or set(value) != set(keys):
                _error()

        def receipt(value, share):
            if not _id(value["id"]) or not _date(value["created_at"]):
                _error()
            states = {"queued", "pending", "accepted", "challenged", "deprecated", "rejected", "superseded", "unknown"} if share else {
                "queued", "sent", "delivered", "read", "unknown", "failed"}
            if type(value["state"]) is not str or value["state"] not in states:
                _error()

        if path in ("/community/shares/refresh", "/community/shares/inspect"):
            exact(response, ("id", "request_id", "created_at", "state", "observed_status", "trust_basis",
                             "acceptance_available", "consensus_verified", "cache_eligible"))
            receipt(response, True)
            if (not _uuid(response["request_id"]) or response["consensus_verified"] is not False
                    or response["cache_eligible"] is not False
                    or type(response["acceptance_available"]) is not bool
                    or response["trust_basis"] not in ("pinned_gateway", "trusted_node_online", "unverified")
                    or response["observed_status"] not in (None, "proposed", "validated", "committed", "challenged", "deprecated")):
                _error()
            accepted = response["state"] in {"accepted", "challenged", "deprecated"}
            if (response["acceptance_available"] != accepted
                    or accepted and (response["trust_basis"] != "trusted_node_online"
                        or response["state"] != {"committed": "accepted", "challenged": "challenged", "deprecated": "deprecated"}.get(response["observed_status"]))
                    or response["trust_basis"] == "pinned_gateway" and response["state"] not in {"queued", "pending", "unknown"}):
                _error()
        elif path == "/community/messages/render-challenge":
            exact(response, ("id", "challenge_id", "expires_at"))
            if (not _receipt_inbox_id(response["id"]) or not _receipt_uuid(response["challenge_id"])
                    or type(response["expires_at"]) is not int or response["expires_at"] <= 0):
                _error()
        elif path == "/community/messages/rendered":
            exact(response, ("id", "receipt_id", "state"))
            if (not _receipt_inbox_id(response["id"]) or not _receipt_uuid(response["receipt_id"])
                    or type(response["state"]) is not str
                    or response["state"] not in ("queued", "serial_accepted", "unknown")):
                _error()
        elif path.endswith("/status"):
            exact(response, ("schema", "messaging", "sharing", "dictation"))
            if response["schema"] != "sage.lantern.community.v1":
                _error()
            for name in ("messaging", "sharing"):
                item = response[name]
                exact(item, ("available", "reason"))
                if (type(item["available"]) is not bool or type(item["reason"]) is not str
                        or item["reason"] not in REASONS or item["available"] != (item["reason"] == "ready")):
                    _error()
            exact(response["dictation"], ("available",))
            if type(response["dictation"]["available"]) is not bool:
                _error()
        elif path.endswith("/preview"):
            exact(response, ("preview_id", "digest", "question", "answer", "photo_included", "expires_at"))
            if (not _uuid(response["preview_id"]) or type(response["digest"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", response["digest"]) is None
                    or not _text(response["question"], 4096) or not _text(response["answer"], 8192)
                    or response["photo_included"] is not False or not _date(response["expires_at"])):
                _error()
        elif path.endswith("/list") or path == '/community/contacts/radio':
            exact(response, ("items", "more"))
            if type(response["items"]) is not list or len(response["items"]) > MAX_ITEMS or type(response["more"]) is not bool:
                _error()
            ids = set()
            for item in response["items"]:
                if "/contacts/" in path:
                    exact(item, ("id", "name", "radio_public_key", "chain_id", "agent_id", "expires_at")
                        if path == '/community/contacts/radio' else ("id", "name"))
                    if path == '/community/contacts/radio' and (
                            any(type(item[key]) is not str or re.fullmatch(r'[0-9a-f]{64}', item[key]) is None
                                for key in ('radio_public_key', 'agent_id'))
                            or not _text(item['chain_id'], 256) or type(item['expires_at']) is not int
                            or item['expires_at'] <= 0):
                        _error()
                    if not _text(item["name"], 256):
                        _error()
                elif "/shares/" in path:
                    exact(item, ("id", "question", "state", "created_at"))
                    receipt(item, True)
                    if not _text(item["question"], 4096):
                        _error()
                else:
                    exact(item, ("id", "peer_name", "text", "state", "created_at"))
                    receipt(item, False)
                    if not _text(item["peer_name"], 256) or not _text(item["text"], 8192):
                        _error()
                if not _id(item["id"]) or item["id"] in ids:
                    _error()
                ids.add(item["id"])
        else:
            exact(response, ("id", "state", "created_at"))
            receipt(response, "/shares/" in path)
