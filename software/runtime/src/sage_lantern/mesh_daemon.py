"""Dedicated root mesh owner. Explicit root manifest; no client configuration.

The manifest is operator enrollment/radio authority, not a self-signed contact
card. Peers are exact chain/agent/radio triples independently approved by the
operator. Initial open/reset approval is separate from background messaging
consent. Existing ordinary identity only; no enrollment or key creation.

The root supervisor owns voice, this runtime, and cooperating SAGE closure.
Uncertainty retains its journal and forbids automatic service recovery. Root
outside this cooperating controller remains able to bypass Linux policy.
"""

from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import secrets
import signal
import socket
import stat
import threading
import time
import uuid

from sage_sdk import AgentIdentity, SageClient

from .community_service import CommunityMeshProvider
from .device_access import access_epoch, device_locked, MARKER, _marker_state
from .mesh_community import MeshReceiveConsent, MeshRequestAccess
from .mesh_envelope import Peer
from .mesh_outbox import MeshOutbox, SCHEMA as OUTBOX_SCHEMA
from .mesh_inbox import MeshInbox
from .mesh_custodian import STATUS_SCHEMA as CUSTODY_STATUS_SCHEMA, MeshCustodian, valid_source
from .mesh_receipt_sender import MeshReceiptSender, SCHEMA as RECEIPT_SCHEMA
from .mesh_receiver import SCHEMA as RECEIVER_SCHEMA, _validate as validate_receiver
from .mesh_runtime import MeshRuntimeConfig
from .mesh_serial_owner import RadioBinding
from .mesh_usb_opener import InitialUSBOpener, USBStartupPolicy
from .mesh_voice_ipc import CONFIG, SOCKET, MeshIPCError, card, peer, receive, require, send
from .meshcore_transport import MeshCoreError
from .message_contacts import RecipientID
from .message_storage import MessageStorageProbe
from .storage_supervisor import RootStorageSupervisor, RootVoiceBackend
from .storage_supervisor_socket import CaptureLease, shutdown_process
from .suspend_io import read_json, publish
from .suspend_system import BUSCTL
from .workflow_store import WorkflowStore, WorkflowNotFound


STATE = Path("/var/lib/sage-lantern-mesh-supervisor")
REQUEST = Path("/run/sage-lantern-mesh-control/voice-drain-request.json")
START = Path("/run/sage-lantern-mesh-control/voice-start-authorized.json")
SCHEMA = "sage.lantern.mesh-runtime.v1"


class ProviderRefused(MeshIPCError):
    """The provider was refused, and nothing was written.

    Distinct from every other start failure on purpose: a refusal means a
    precondition moved (session, epoch, unlock guard) and the request may be
    tried again, while anything else can have left an unknown outcome behind
    and must hold the unit closed. The supervisor used to fold both into
    ``MeshIPCError`` and set ``_uncertain`` for either, which is how one
    transient refusal turned unit A's every community route into a 503 until
    the process restarted (2026-09-21).
    """
# Locked units still announce so a peer keeps a real measurement. Bounded,
# zero-hop and location-free; the firmware location policy still suppresses it
# and the client's own advertisement throttle prevents duplicates.
BEACON_SECONDS = 60.0
# The messaging worker retires when an owner session rotates the storage epoch
# under a live capture grant. A supervisor thread restarts it under the current
# epoch; a request must never join it (a failed join left the runtime pausing and
# answered 503 on every route until a process restart, 2026-09-18).
BACKGROUND_SUPERVISOR_SECONDS = 1.0
BACKGROUND_SUPERVISOR_MAX_SECONDS = 30.0
# A construction whose radio open fails before any command byte reached the
# radio leaves no session, no wire state and no lock: the transport classifies
# the failure `outcome_unknown=False` and does not poison itself, and the
# opener's own failure path closes the descriptor and every lock it held. One
# flaky handshake is therefore retryable, and without a bounded retry it pinned
# every community route until the next cold start (unit A 2026-09-19 16:14:07:
# the first construction after a cold start failed at
# `mesh_usb_opener -> meshcore_transport.handshake`). Each attempt uses a fresh
# single-use opener under a freshly checked startup fence, so the unit's own
# quiescence/epoch reconciliation gate is re-proved for every physical open.
# A policy refusal, a fence refusal, a poisoned device or any post-wire failure
# stays single-shot and fail-closed.
RADIO_OPEN_ATTEMPTS = 3
RADIO_OPEN_RETRY_SECONDS = 0.35


def retryable_radio_open(error):
    """Only a pre-wire transport failure or a raw device error is retryable."""
    if isinstance(error, MeshCoreError):
        return error.outcome_unknown is False
    return isinstance(error, OSError)


def load_config(path=CONFIG):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    value = read_json(path, owner_uid=0, limit=16384)
    validate_config(value)
    return value


def validate_config(value):
    require(type(value) is dict and set(value) - {"public_mesh", "public_gateway", "delivery_receipts_enabled", "airtime", "custody"} == {"schema", "unit_id", "voice_uid", "endpoint", "chain_id",
        "identity_file", "agent_id", "receiver_slot", "radio", "peers", "approve_initial_open", "acknowledge_reset",
        "messaging_enabled", "receive_while_locked", "dispatch_enabled", "dispatch_while_locked"})
    require(value["schema"] == SCHEMA and type(value["voice_uid"]) is int and value["voice_uid"] > 0)
    require(type(value["identity_file"]) is str and re.fullmatch(
        r"/var/lib/sage-lantern/identity/[A-Za-z0-9_.-]+", value["identity_file"]) is not None)
    require(type(value["endpoint"]) is str and re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", value["endpoint"]) is not None
            and int(value["endpoint"].rsplit(":", 1)[1]) <= 65535)
    require(type(value["receiver_slot"]) is str and str(uuid.UUID(value["receiver_slot"])) == value["receiver_slot"]
            and uuid.UUID(value["receiver_slot"]).int != 0)
    require(type(value["radio"]) is dict and set(value["radio"]) == {"stable_path", "serial", "vendor_id", "product_id", "public_key"})
    radio = dict(value["radio"])
    require(type(radio["public_key"]) is str and re.fullmatch(r"[0-9a-f]{64}", radio["public_key"]) is not None)
    radio["public_key"] = bytes.fromhex(radio["public_key"])
    RadioBinding(unit_id=value["unit_id"], **radio)
    RecipientID(value["chain_id"], "01" * 32)
    RecipientID(value["chain_id"], value["agent_id"])
    for name in ("approve_initial_open", "acknowledge_reset", "messaging_enabled", "receive_while_locked", "dispatch_enabled", "dispatch_while_locked"):
        require(type(value[name]) is bool)
    delivery_receipts = value.get("delivery_receipts_enabled", False)
    require(type(delivery_receipts) is bool)
    require(not delivery_receipts or (value["messaging_enabled"] and value["dispatch_enabled"]))
    if "airtime" in value:
        from .airtime_budget import policy_from_settings
        policy_from_settings(value["airtime"])
    if "custody" in value:
        custody = value["custody"]
        require(type(custody) is dict and set(custody) == {"enabled", "depositors"})
        require(type(custody["enabled"]) is bool)
        require(type(custody["depositors"]) is list and len(custody["depositors"]) <= 64
                and all(valid_source(item) is True for item in custody["depositors"]))
        require(not custody["enabled"] or (custody["depositors"] and value["messaging_enabled"]))
    require(value["approve_initial_open"] and value["acknowledge_reset"])
    require(type(value["peers"]) is list and len(value["peers"]) <= 256)
    seen = set()
    for record in value["peers"]:
        require(type(record) is dict and set(record) == {"chain_id", "agent_id", "radio_public_key"})
        recipient = RecipientID(record["chain_id"], record["agent_id"])
        require(type(record["radio_public_key"]) is str and re.fullmatch(r"[0-9a-f]{64}", record["radio_public_key"]) is not None)
        binding = (recipient.chain_id, recipient.agent_id)
        require(binding not in seen)
        seen.add(binding)
    if "public_mesh" in value:
        from .mesh_public_root import validate_settings
        validate_settings(value["public_mesh"], value)
    if "public_gateway" in value:
        from .mesh_gateway_root import validate
        require("public_mesh" not in value)
        validate(value["public_gateway"], value)


def load_identity(config):
    """Pin the existing ordinary key before any SDK call; never follow symlinks."""
    validate_config(config)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    key_fd = sealed = None
    try:
        parts = Path(config["identity_file"]).parts[1:]
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
            info = os.fstat(descriptor)
            require(info.st_uid in (0, config["voice_uid"]) and not info.st_mode & 0o022)
        key_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        info = os.fstat(key_fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, config["voice_uid"])
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and info.st_size == 32)
        seed = os.read(key_fd, 33)
        require(len(seed) == 32)
        identity = AgentIdentity.from_seed(seed)
        require(identity.agent_id == config["agent_id"])
        sealed = os.memfd_create("lantern-existing-identity", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        require(os.write(sealed, seed) == 32)
        os.fchmod(sealed, 0o400)
        fcntl.fcntl(sealed, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK)
        return identity, sealed
    except Exception:
        if sealed is not None:
            os.close(sealed)
        raise MeshIPCError() from None
    finally:
        if key_fd is not None:
            os.close(key_fd)
        os.close(descriptor)


class HardwareRuntime:
    def __init__(self, supervisor, config, identity, probe, *, identity_path, config_reader=load_config, opener_factory=InitialUSBOpener,
                 provider_factory=CommunityMeshProvider.from_config, epoch_reader=access_epoch,
                 lock_marker_check=lambda: _marker_state(MARKER) is True, public_contact_check=None):
        self.supervisor, self.config, self.identity, self.probe = supervisor, config, identity, probe
        require(probe.actor.agent_id == identity.agent_id == config["agent_id"])
        require(isinstance(identity_path, Path) and re.fullmatch(r"/proc/self/fd/[0-9]+", str(identity_path)) is not None)
        self.identity_path = identity_path
        self._config_reader, self._opener_factory, self._provider_factory, self._epoch = config_reader, opener_factory, provider_factory, epoch_reader
        self._lock_marker = lock_marker_check
        self._mutex = threading.Lock()
        self._attempted = self._closed = self._uncertain = False
        from .mesh_radio_commission import no_unknown
        self._uncertain = not no_unknown()
        self._provider = self._coordinator = None
        self._public = None
        self._public_identity = None
        self._gateway = self._gateway_identity = None
        self._read_receipts = None
        self._public_contact_check = public_contact_check
        self._initializing = False
        # Set once the radio open loop is entered. A refusal raised before this
        # point wrote nothing, so it must not latch the unit (see provider()).
        self._wire_started = False
        self._reconciled = None
        self._closing = False
        self._background_stop = threading.Event()
        threading.Thread(target=self._supervise_background,
                         name="lantern-mesh-background-supervisor", daemon=True).start()
        from .mesh_radio_events import RadioEvents
        self._radio_events = RadioEvents()
        self._radio_observer = None
        self.actor = probe.actor

    def configured(self):
        try:
            return self._lock_marker() is True and self._config_reader() == self.config
        except Exception:
            return False

    def active(self):
        if self._provider is not None and not self._provider._runtime.admission_safe():
            self._uncertain = True
        return (not self._closed and not self._uncertain and self.configured()
                and self.supervisor.phase == "active" and self.supervisor.resource_lifecycle_check() is True)

    def binding(self, owner, contact, radio_key):
        return (self.active() and owner == self.actor and type(radio_key) is bytes
                and dict(chain_id=contact.chain_id, agent_id=contact.agent_id, radio_public_key=radio_key.hex()) in self.config["peers"])

    def import_contact(self, request, *, broker_id, epoch_reader):
        from .mesh_contacts import MAX_CARD_BYTES, verify_contact_card
        require(type(request) is dict and set(request) == {"op", "broker_id", "epoch", "config_sha256", "card_sha256", "card"}
                and request["op"] == "import-contact" and request["broker_id"] == broker_id)
        require(type(request["epoch"]) is str and re.fullmatch(r"[0-9a-f]{32}", request["epoch"]) is not None)
        require(type(request["card"]) is str)
        raw = request["card"].encode("utf-8")
        require(0 < len(raw) <= MAX_CARD_BYTES and hashlib.sha256(raw).hexdigest() == request["card_sha256"])
        revision = hashlib.sha256(json.dumps(self.config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        require(request["config_sha256"] == revision)
        contact = verify_contact_card(raw, now=time.time())
        radio_key = bytes.fromhex(contact.radio_public_key)
        deadline = time.monotonic() + 15
        def guard():
            return (time.monotonic() < deadline and epoch_reader() == request["epoch"]
                    and self.supervisor.broker_id == broker_id and self.binding(self.actor, contact, radio_key))
        require(self._mutex.acquire(blocking=False))
        try:
            require(guard())
            if self._provider is None:
                self.provider(guard)
            require(guard() and self._provider is not None)
            provider = self._provider
            access = MeshRequestAccess(request["epoch"], guard)
            with provider._authority.grant(self.actor, access, ("contact_update",)) as context:
                saved = provider._runtime.put_contact(context, raw, known_actor=contact.recipient)
            require(guard() and saved == contact)
            return dict(card_sha256=request["card_sha256"], revision=saved.revision,
                        findable=saved.findable, messaging_allowed=saved.messaging_allowed)
        finally:
            self._mutex.release()

    def custody_stats(self, guard):
        """Counts-only custody view. No deposit wire, source or recipient key."""
        settings = self.config.get("custody")
        if not settings or not settings["enabled"]:
            return dict(schema=CUSTODY_STATUS_SCHEMA, enabled=False, held=0, bytes=0,
                        recipients=0, oldest_expires_at=None, delivered=0, depositors=0)
        store = WorkflowStore(self.probe.client, storage=self.probe)
        custodian = MeshCustodian(store, access_check=guard,
                                  depositors=tuple(settings["depositors"]))
        result = custodian.stats()
        require(guard() and self.active())
        return result

    def reconcile(self, guard):
        if not (self._initializing and self._coordinator is None and self.active() and guard()):
            self._refuse_provider("reconcile-entry", guard)
        store = WorkflowStore(self.probe.client, storage=self.probe)
        epoch = self.probe.snapshot().epoch
        outbox = MeshOutbox(store, self.identity, sender=Peer(self.actor.chain_id, bytes.fromhex(self.actor.agent_id)),
                            guard=lambda action: guard(), verify_contact=lambda *args: False)
        inbox = MeshInbox(store, recipient=outbox.sender, recipient_signing_key=self.identity._signing_key,
                          resolve_peer=lambda message: None, access_check=guard, storage_check=guard)
        receipts = MeshReceiptSender(store, self.identity, inbox=inbox, sender=outbox.sender,
                                     guard=lambda action: guard(), policy=lambda action: None, verify_contact=lambda *args: False)
        for row in store.scan(limit=50, max_records=4096, access_check=guard):
            if row["kind"] == "mesh_outbound":
                schema = row["payload"].get("body", {}).get("schema")
                if schema == OUTBOX_SCHEMA:
                    body = outbox._verified(row)
                    if body["state"] == "dispatching":
                        # An unknown dispatch outcome inside the row's own signed
                        # window is retryable (owner decision 2026-09-13): the
                        # runtime adopts the row on its first scan and the retry
                        # driver requeues it, so construction proceeds instead of
                        # refusing every route until an operator intervenes. A
                        # lapsed window still refuses right here.
                        require(body["approved"]["issued_at"] <= int(time.time())
                                < body["approved"]["expires_at"])
                    elif body["state"] == "cancelled":
                        # An owner cancel is the audited resolution of a row that
                        # never became confirmed evidence (owner decision
                        # 2026-09-21). It is terminal and nothing here adopts it
                        # as an unknown, so construction proceeds and the unit's
                        # community routes come up instead of refusing forever on
                        # a window that lapsed while nobody could act on it. The
                        # decision binding travels with the row; a row that claims
                        # cancellation without one is not an owner decision and is
                        # refused.
                        require(body["retry_binding"] != "")
                elif schema == RECEIPT_SCHEMA:
                    require(receipts._verified(row)[0]["state"] != "dispatching")
                elif schema == "sage.lantern.mesh-read-consent.v1":
                    from .mesh_read_root import verify_consent_row
                    require(verify_consent_row(row, identity=self.identity, owner=self.actor, inbox=inbox) is True)
                else:
                    methods = {"sage.lantern.mesh-receipt-replay.v1": "LANTERN-OUTBOX-RECEIPT-REPLAY",
                               "sage.lantern.mesh-receipt-slot.v1": "LANTERN-OUTBOX-RECEIPT-SLOT"}
                    require(schema in methods and row["revision"] == 1)
                    outbox._verify_proof(row["payload"], method=methods[schema])
            if row["kind"] == "mesh_inbound" and row["payload"].get("schema") == RECEIVER_SCHEMA:
                validate_receiver(row["payload"])
                if row["payload"]["state"] == "receiving":
                    require(guard() and self.active() and self._coordinator is None)
                    interrupted = dict(row["payload"], state="interrupted")
                    saved = store.put(row["record_id"], "mesh_inbound", row["revision"], interrupted,
                        access_check=guard)
                    require(saved["payload"] == interrupted)
        require(guard() and self.probe.snapshot().epoch == epoch)
        if "public_mesh" in self.config:
            from .public_mesh_provider import PublicMeshProvider, PublicMeshPins
            settings = self.config["public_mesh"]
            identity_config = {key: value for key, value in self.config.items() if key != "public_mesh"}
            identity_config.update(identity_file=settings["identity_file"], agent_id=settings["author"])
            public_identity, descriptor = load_identity(identity_config)
            try:
                require(guard() and self.active())
                verifier = PublicMeshProvider(store, public_identity,
                    PublicMeshPins(settings["gateway_key"], bytes.fromhex(settings["radio_key"]), settings["chain_id"], settings["domain"]),
                    approval_check=lambda proposal: False, contact_check=lambda pins: False)
                require(verifier.drain(access_check=guard) is True)
                from .gateway_contact import GatewayContactProbe
                contact = GatewayContactProbe(store, public_identity, verifier.pins, active=self.active)
                require(contact.drain(access_check=guard) is True)
                self._public_identity = public_identity
            finally:
                os.close(descriptor)
            require(guard() and self.probe.snapshot().epoch == epoch)
        self._reconciled = epoch
        if "public_gateway" in self.config:
            from .mesh_gateway_root import backend_current, shutdown_supported
            settings = self.config["public_gateway"]
            calibration = settings["calibration"]
            require(backend_current(settings) is True and shutdown_supported() is True
                and calibration["issued_at"] <= time.time() < calibration["expires_at"])
            key_config = {key: value for key, value in self.config.items() if key != "public_gateway"}
            key_config.update(identity_file=settings["identity_file"], agent_id=settings["role"]["gateway_key"])
            identity, descriptor = load_identity(key_config)
            try:
                from .mesh_gateway_root import verify_before_open
                require(verify_before_open(store, identity, settings, access_check=guard) is True)
                require(guard() and self.active() and self.probe.snapshot().epoch == epoch)
                self._gateway_identity = identity
            finally:
                os.close(descriptor)

    def _provider_facts(self, guard):
        """The individual conjuncts behind a provider/liveness refusal.

        ``active()`` folds four conditions into one bool and
        ``resource_lifecycle_check`` swallows its own exceptions, so a refusal
        on its own is undiagnosable from the journal. Each fact is read
        defensively: a diagnostic must never raise the thing it is describing.
        """
        def safely(call):
            try:
                return bool(call())
            except Exception:
                return None
        return {"guard": safely(guard), "active": safely(self.active),
                "initializing": bool(self._initializing),
                "coordinator": self._coordinator is not None,
                "attempted": bool(self._attempted),
                "wire_started": bool(self._wire_started),
                "uncertain": bool(self._uncertain),
                "closed": bool(self._closed),
                "configured": safely(self.configured),
                "supervisor_phase": getattr(self.supervisor, "phase", None),
                "resource_lifecycle": safely(self.supervisor.resource_lifecycle_check)}

    def _refuse_provider(self, stage, guard):
        """Refuse a provider start, loudly and with the facts that refused it."""
        import syslog
        syslog.syslog(syslog.LOG_ERR, json.dumps(dict(
            event="mesh_provider_refused", stage=stage, **self._provider_facts(guard))))
        raise ProviderRefused()

    def provider(self, guard):
        # Fail-closed, but say which conjunct closed. Unit A answered 503 on
        # every community route for a day with `mesh_ipc_unavailable` and no
        # journal line naming the reason: the guard, the supervisor's
        # resource-lifecycle check and the configured() marker all return a
        # bare False, so a refusal has to carry its facts out with it.
        if not (guard() and self.active()):
            self._refuse_provider("provider-entry", guard)
        if self._provider is not None:
            # A retired worker is restarted by the supervisor thread, never
            # here: a request must not wait on a join, and a failed pause used to
            # leave the runtime pausing (every route 503) until a process
            # restart. This request proceeds against the runtime as it stands.
            return self._provider
        if self._attempted:
            self._refuse_provider("provider-retry-after-refusal", guard)
        store = WorkflowStore(self.probe.client, storage=self.probe)
        try:
            receiver = store.get(self.config['receiver_slot'], kind='mesh_inbound', access_check=guard)
        except WorkflowNotFound:
            receiver = None
        if receiver is not None:
            validate_receiver(receiver['payload'])
            if receiver['payload']['state'] == 'receiving':
                self._uncertain = True
                require(False)
            require(receiver['payload']['state'] != 'interrupted')
        require(guard() and self.active())
        if self._coordinator is not None:
            require(self._coordinator.close(timeout=3) is True)
            self._coordinator = None
        self._attempted, self._initializing = True, True
        try:
            self.reconcile(guard)
            @contextmanager
            def startup_fence():
                def check():
                    return self._initializing and self._coordinator is None and self.active() and guard()
                require(check())
                yield check
            def reconciled():
                return (self._initializing and self._reconciled is not None and self.active() and guard()
                        and self.probe.snapshot().epoch == self._reconciled)
            policy = USBStartupPolicy(True, True, "quiescent_startup")
            radio = dict(self.config["radio"])
            radio["public_key"] = bytes.fromhex(radio["public_key"])
            binding = RadioBinding(unit_id=self.config["unit_id"], **radio)
            coordinator = None
            self._wire_started = True
            for attempt in range(1, RADIO_OPEN_ATTEMPTS + 1):
                opener = self._opener_factory(policy=policy, startup_fence=startup_fence, journals_reconciled=reconciled)
                try:
                    coordinator = opener.open(binding=binding, lock_directory=STATE / "serial", timeout=3)
                    break
                except Exception as error:
                    if attempt == RADIO_OPEN_ATTEMPTS or not retryable_radio_open(error):
                        raise
                    if not (guard() and self.active() and reconciled()):
                        raise
                    import syslog
                    syslog.syslog(syslog.LOG_WARNING, json.dumps(dict(event="radio_open_retry",
                        attempt=attempt, error=str(error),
                        outcome_unknown=bool(getattr(error, "outcome_unknown", False)))))
                    time.sleep(RADIO_OPEN_RETRY_SECONDS)
            require(coordinator is not None)
            self._coordinator = coordinator
            require(guard() and self.active())
            def observe(event):
                epoch = self._epoch()
                if not self.active():
                    self._radio_events.clear()
                    return
                self._radio_events.observe(event, epoch)
            self._radio_observer = observe
            coordinator.submit(lambda client: client.set_receive_observer(observe)).result(timeout=3)
            airtime = None
            if "airtime" in self.config:
                from .airtime_budget import policy_from_settings
                airtime = policy_from_settings(self.config["airtime"])
            custody = None
            if "custody" in self.config and self.config["custody"]["enabled"]:
                custody = tuple(self.config["custody"]["depositors"])
            config = MeshRuntimeConfig(self.config["endpoint"], self.config["chain_id"], self.identity_path,
                                       self.config["receiver_slot"], 10, airtime, custody)
            self._provider = self._provider_factory(config, coordinator=coordinator, activation_check=self.active,
                verify_binding=self.binding, storage_lifecycle_check=self.supervisor.resource_lifecycle_check)
            from .mesh_read_root import RootReadReceipts
            self._read_receipts = RootReadReceipts(self._provider, self.identity, active=self.active)
            if "public_mesh" in self.config:
                from .mesh_public_root import RootPublicMesh
                settings = self.config["public_mesh"]
                require(guard() and self.active() and self._public_identity is not None)
                self._public = RootPublicMesh(self._provider._runtime, self._provider._authority,
                    settings, self._public_identity, self.identity, active=self.active, contact_check=self._public_contact_check)
            if "public_gateway" in self.config:
                from .mesh_gateway_root import GatewayRoot
                require(self._gateway_identity is not None)
                self._gateway = GatewayRoot(self, self._gateway_identity)
            self.resume_background(guard)
            return self._provider
        except ProviderRefused:
            # A refusal wrote nothing, so the next request may try again
            # instead of every route answering 503 until a restart. This is the
            # only start failure that is retryable: an unknown capture state
            # (_uncertain) or anything raised after the radio loop is entered
            # stays closed until an operator intervenes.
            self._attempted = False
            raise
        except Exception:
            self._uncertain = True
            raise
        finally:
            self._initializing = False

    def resume_background(self, guard):
        if "public_gateway" in self.config:
            require(self._gateway is not None and guard() and self.active() and self._gateway.evidence())
            if not self._gateway.role._started:
                require(self._gateway.start(guard) is True)
            else:
                require(self._provider._runtime._poller is not None and self._provider._runtime._poller.is_alive())
            return
        if not self.config["messaging_enabled"]:
            return
        poller = self._provider._runtime._poller
        if poller is not None and poller.is_alive():
            return
        require(guard() and self.active())
        require(self._provider.restart_receiving(consent_reader=self.consent, lease_factory=self.capture_lease,
                                                 unlocked_check=lambda: self._epoch() is not None) is True)
        require(guard() and self.active())

    def _supervise_background(self):
        """Bring the messaging worker back under the current epoch, off every request.

        The worker retires when its capture grant is denied - most often an
        owner session rotating the storage epoch while a wake is admitted. The
        restart used to happen inside the request that followed
        (resume_background -> pause_receiving); a join that could not observe the
        retired thread left the runtime pausing, so every route answered 503
        until a process restart (units A and B, 2026-09-18 18:16-18:39).
        This thread owns that job instead: it never holds a request, takes the
        resource mutex without waiting, and never marks the runtime as pausing.
        """
        backoff = BACKGROUND_SUPERVISOR_SECONDS
        while not self._background_stop.wait(backoff):
            try:
                if (self._closing or self._closed or "public_gateway" in self.config
                        or not self.config["messaging_enabled"] or not self.active()):
                    backoff = BACKGROUND_SUPERVISOR_SECONDS
                    continue
                provider = self._provider
                if provider is None:
                    continue
                poller = provider._runtime._poller
                if poller is not None and poller.is_alive():
                    backoff = BACKGROUND_SUPERVISOR_SECONDS
                    continue
                if not self._mutex.acquire(blocking=False):
                    continue
                try:
                    started = (not self._closing and not self._closed and self._provider is provider
                               and self.active()
                               and provider.restart_receiving(consent_reader=self.consent,
                                       lease_factory=self.capture_lease,
                                       unlocked_check=lambda: self._epoch() is not None) is True)
                finally:
                    self._mutex.release()
            except Exception:
                backoff = min(max(backoff, BACKGROUND_SUPERVISOR_SECONDS) * 2, BACKGROUND_SUPERVISOR_MAX_SECONDS)
                continue
            backoff = (BACKGROUND_SUPERVISOR_SECONDS if started
                       else min(max(backoff, BACKGROUND_SUPERVISOR_SECONDS) * 2, BACKGROUND_SUPERVISOR_MAX_SECONDS))

    def consent(self):
        require(self.configured())
        revision = hashlib.sha256(json.dumps(self.config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return MeshReceiveConsent(self.actor, revision, self.config["messaging_enabled"], self.config["receive_while_locked"],
                                  self.config["dispatch_enabled"], self.config["dispatch_while_locked"],
                                  delivery_receipts_enabled=self.config.get("delivery_receipts_enabled", False))

    def capture_lease(self, consent):
        require(self.active() and consent == self.consent())
        return self._capture_lease(consent, 30, lambda: self.consent() == consent)

    def beacon(self, guard):
        """One bounded locked-state advertisement; never opens or retries the radio.

        The unlocked UI announces while it polls. This path keeps a locked unit
        discoverable so a peer keeps a real per-packet measurement. It reuses the
        reviewed announcement inside health(announce=True), which stays zero-hop
        and location-free and is suppressed whenever the firmware reports a
        location-sharing policy. Nothing is sent when the radio is closed, the
        device is not explicitly locked, or another serial operation holds the
        device. Bounded single attempt; no queueing.
        """
        require(guard() and self.active())
        if not self._mutex.acquire(blocking=False):
            return False
        try:
            require(guard() and self.active())
            coordinator = self._coordinator
            device = None if coordinator is None else coordinator._client
            identity = (device is not None and not device._poisoned and device._identity is not None
                and device._key.hex() == self.config["radio"]["public_key"])
            if not identity:
                return False
            epoch = self._epoch()
            coordinator.submit(lambda radio: radio.health(
                authorize=lambda: self.active() and self._epoch() == epoch, announce=True)).result(timeout=5)
            require(guard() and self.active())
            return True
        finally:
            self._mutex.release()

    def gateway_lease(self, consent, seconds, *, on_finish=lambda: None):
        from .mesh_gateway_root import shutdown_supported
        require(self._gateway is not None and self.active() and self._gateway.evidence()
            and consent == self._gateway.role._consent() and type(seconds) in (int, float)
            and seconds == self.config["public_gateway"]["role"]["lease_seconds"] and 0 < seconds <= 55
            and shutdown_supported() is True)
        return self._capture_lease(consent, seconds, lambda: consent == self._gateway.role._consent(), on_finish=on_finish)

    def _capture_lease(self, consent, seconds, consent_check, *, on_finish=lambda: None):
        token = secrets.token_hex(32)
        deadline = time.monotonic() + seconds
        supervisor = self.supervisor
        broker = supervisor.broker_id
        epoch = self.probe.snapshot().epoch
        ended = False
        def transport(value):
            nonlocal ended
            if value == {"op": "acquire", "epoch": consent.revision}:
                require(not ended and self.active() and consent_check() is True)
                return {"lease": token}
            require(value.get("lease") == token)
            if value["op"] == "finish":
                ended = True
                on_finish()
                return {"finished": True}
            require(value["op"] == "check")
            try:
                # The epoch comparison rides the broker's own bounded
                # verification instead of a signed snapshot per admission
                # check: a 10 s dispatch wake makes ~165 of these, and a signed
                # request each saturated the local SAGE node's replay window
                # (503 Replay window saturated) on unit A 2026-09-19 17:02. The
                # check is still fail-closed: any refusal, phase change or moved
                # epoch inside the window denies the capture (see
                # LIVE_CHECK_SECONDS).
                allowed = (not ended and time.monotonic() < deadline and self.supervisor is supervisor
                    and supervisor.broker_id == broker and supervisor.resource_lifecycle_check() is True
                    and supervisor.verified_epoch() == epoch and time.monotonic() < deadline)
            except Exception:
                allowed = False
            return {"allowed": allowed}
        return CaptureLease(consent.revision, transport=transport)

    def release_failed_health_radio(self, guard):
        require(guard() and self.active())
        coordinator = self._coordinator
        if coordinator is None or self._provider is not None or not coordinator._client._poisoned:
            return
        require(coordinator.close(timeout=3) is True)
        require(self._coordinator is coordinator and self._provider is None)
        self._coordinator = None
        self._radio_events.clear()
        require(guard() and self.active())

    def invoke(self, method, arguments, guard, *, request_binding=None, background_check=None):
        require(self._mutex.acquire(blocking=False))
        try:
            if method == 'custody_status':
                require(arguments == {} and guard() and self.active())
                return self.custody_stats(guard)
            if method in ('radio_status', 'contacts'):
                require(arguments == {} and guard() and self.active())
                self.release_failed_health_radio(guard)
                if self._coordinator is None:
                    self._initializing = True
                    try:
                        self.reconcile(guard)
                        @contextmanager
                        def health_fence():
                            def check():
                                return self._initializing and self._coordinator is None and self.active() and guard()
                            require(check())
                            yield check
                        opener = self._opener_factory(policy=USBStartupPolicy(True, True, 'quiescent_startup'),
                            startup_fence=health_fence, journals_reconciled=lambda: self._initializing
                            and self._reconciled is not None and self.active() and guard()
                            and self.probe.snapshot().epoch == self._reconciled)
                        binding = dict(self.config['radio'])
                        binding['public_key'] = bytes.fromhex(binding['public_key'])
                        self._coordinator = opener.open(binding=RadioBinding(unit_id=self.config['unit_id'], **binding),
                            lock_directory=STATE / 'serial', timeout=3)
                    finally:
                        self._initializing = False
                if method == 'contacts':
                    from .mesh_contact_registry import MeshContactRegistry
                    coordinator = self._coordinator
                    def read_contacts(device):
                        def check():
                            return guard() and self.active() and self._coordinator is coordinator
                        require(check())
                        inventory = device.get_contacts(authorize=check)
                        store = WorkflowStore(self.probe.client, storage=self.probe)
                        registry = MeshContactRegistry(store, self.identity, owner=self.actor,
                            inventory=lambda: inventory, verify_binding=self.binding)
                        cards = sorted(registry.list_current(access_check=check),
                            key=lambda contact: (contact.chain_id, contact.agent_id))[:21]
                        for contact in cards:
                            require(registry.resolve(bytes.fromhex(contact.radio_public_key), access_check=check) == contact)
                        require(check())
                        return [asdict(contact) for contact in cards]
                    result = coordinator.submit(read_contacts).result(timeout=10)
                    require(guard() and self.active() and self._coordinator is coordinator)
                    return result
                epoch = self._epoch()
                require(epoch is not None)
                coordinator = self._coordinator
                device = None if coordinator is None else coordinator._client
                pinned_identity = None if device is None else device._identity
                identity = (device is not None and not device._poisoned and device._identity is not None
                    and device._key.hex() == self.config['radio']['public_key'])
                worker = None if self._provider is None else self._provider._runtime._poller
                receiving = (identity and worker is not None and worker.is_alive()
                    and device._receive_observer is self._radio_observer)
                result = self._radio_events.snapshot(epoch, identity_verified=identity, receiving=receiving,
                    repeat=device._identity.repeat if identity else None)
                if identity:
                    result['firmware'] = device._identity.firmware
                    result['health'] = coordinator.submit(lambda radio: radio.health(
                        authorize=lambda: self.active() and self._epoch() == epoch, announce=True)).result(timeout=4)
                    result['observed_at_unix_ms'] = int(time.time() * 1000)
                    if time.monotonic() >= getattr(self, '_next_radio_diagnostic', 0):
                        import syslog
                        health = result['health']
                        syslog.syslog(syslog.LOG_INFO, json.dumps(dict(event='radio_measurement',
                            firmware=device._identity.firmware,
                            received=health['packets_received'], sent=health['packets_sent'],
                            radios_heard=len(health['discovered_nodes']),
                            heard_keys=[node['public_key'] for node in health['discovered_nodes']],
                            last_rssi_dbm=health['last_rssi_dbm'] if health['packets_received'] else None,
                            last_snr_db=health['last_snr_db'] if health['packets_received'] else None)))
                        self._next_radio_diagnostic = time.monotonic() + 60
                require(guard() and self.active() and self._epoch() == epoch and self._coordinator is coordinator)
                if identity:
                    require(not device._poisoned and device._identity is pinned_identity)
                if receiving:
                    require(worker.is_alive() and device._receive_observer is self._radio_observer)
                return result
            shapes = {"ready": set(), "contacts": set(), "list_messages": {"box"}, "open_message": {"id"},
                      "queue": {"card", "text", "request_id", "approval_binding"},
                      "public_begin": {"proposal", "request_id", "receipt"}, "public_inspect": {"request_id"},
                      "public_ready": set(), "public_prepare": {"operation_id", "question", "answer"},
                      "public_queue": {"proposal", "source_id", "created_at", "request_id", "approval_binding", "consent"},
                      "public_list": set(), "public_sources": {"source_ids"},
                      "render_challenge": {"id", "rendered"},
                      "rendered": {"id", "challenge_id", "rendered", "share_read_receipt"}}
            require(method in shapes and type(arguments) is dict and set(arguments) == shapes[method])
            if method in ("render_challenge", "rendered"):
                require(request_binding is not None and type(arguments["id"]) is str
                        and re.fullmatch(r"mesh:inbox:[0-9a-f-]{36}:[0-9a-f]{64}", arguments["id"]) is not None
                        and arguments["rendered"] is True)
                if method == "rendered":
                    from .mesh_read_receipts import identifier
                    identifier(arguments["challenge_id"])
                    require(arguments["share_read_receipt"] is True)
            if method.startswith("public_"):
                require("public_mesh" in self.config)
                from .community_public import PublicProposal, PublicReceipt
                from .public_mesh_protocol import proposal_data, identifier
                if "request_id" in arguments:
                    identifier(arguments["request_id"])
                if method in ("public_begin", "public_queue"):
                    require(type(arguments["proposal"]) is dict)
                    proposal = PublicProposal(**arguments["proposal"])
                    proposal_data(proposal)
                    settings = self.config["public_mesh"]
                    require((proposal.author, proposal.chain_id, proposal.domain) ==
                            (settings["author"], settings["chain_id"], settings["domain"]))
                    if method == "public_begin" and arguments["receipt"] is not None:
                        require(type(arguments["receipt"]) is dict)
                        PublicReceipt(**arguments["receipt"])
                if method == "public_prepare":
                    identifier(arguments["operation_id"])
                    for field, bound in (("question", 4096), ("answer", 8192)):
                        require(type(arguments[field]) is str and 0 < len(arguments[field].encode()) <= bound and "\x00" not in arguments[field])
                if method == "public_queue":
                    require(arguments["consent"] is True)
            if method == "list_messages":
                require(arguments["box"] in ("inbox", "outbox"))
            if method == "open_message":
                require(type(arguments["id"]) is str and len(arguments["id"]) <= 128)
            contact = card(arguments["card"]) if method == "queue" else None
            if method == "queue":
                require(type(arguments["text"]) is str and 0 < len(arguments["text"].encode()) <= 512)
                require(type(arguments["request_id"]) is str and str(uuid.UUID(arguments["request_id"])) == arguments["request_id"])
                require(type(arguments["approval_binding"]) is str and re.fullmatch(r"[0-9a-f]{64}", arguments["approval_binding"]) is not None)
            provider = self.provider(guard)
            access = MeshRequestAccess(secrets.token_hex(16), guard)
            if method in ("render_challenge", "rendered"):
                require(self._read_receipts is not None)
                return self._read_receipts.invoke(method, arguments, access, request_binding, background_check)
            if method.startswith("public_"):
                require(self._public is not None)
                return self._public.invoke(method, arguments, access)
            if method == "queue":
                result = provider.queue(contact, text=arguments["text"], request_id=arguments["request_id"],
                    approval_binding=arguments["approval_binding"], access_check=access, on_submit=lambda: require(guard()))
            elif method in ("list_messages", "open_message"):
                result = getattr(provider, method)(next(iter(arguments.values())), access_check=access)
            else:
                result = getattr(provider, method)(access_check=access)
            require(guard())
            return result if type(result) is bool else [asdict(value) for value in result] if type(result) is tuple else asdict(result)
        finally:
            self._mutex.release()

    def close(self, timeout):
        self._radio_events.clear()
        self._closing = True
        self._background_stop.set()
        deadline = time.monotonic() + timeout
        if not self._mutex.acquire(timeout=max(0, timeout)):
            return False
        try:
            if self._closed:
                return True
            if self._uncertain:
                return False
            if self._provider is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._provider.pause_receiving(timeout=remaining) is not True:
                    return False
            if self._read_receipts is not None and self._read_receipts.close(max(0, deadline-time.monotonic())) is not True:
                return False
            if self._provider is not None:
                if self._provider.close(max(0, deadline-time.monotonic())) is not True:
                    return False
            elif self._coordinator is not None:
                if self._coordinator.close(timeout=max(0, deadline-time.monotonic())) is not True:
                    return False
            elif self._coordinator is not None:
                return False
            if self._gateway is not None:
                self._gateway.rest.close()
            self._closed = True
            return True
        finally:
            self._mutex.release()


class MeshDaemon:
    def __init__(self, supervisor, resource, *, epoch_reader=access_epoch):
        from .voice_listener_ack import ListenerAcknowledgements
        self.supervisor, self.resource, self._epoch = supervisor, resource, epoch_reader
        self._proof = None
        self._listener_ack = ListenerAcknowledgements()
        from .mesh_contact_route import OwnerContactRoute
        self._owner_contacts = OwnerContactRoute()

    def beacon(self, guard):
        """Locked-state advertisement through the current resource, never a stale one."""
        return self.resource.beacon(guard)

    def handle(self, connection, *, pid, uid):
        request = receive(connection)
        if request.get("op") == "radio-snapshot":
            require(uid == 0)
            from .mesh_radio_snapshot import snapshot
            send(connection, {"result": snapshot(self.resource, request,
                broker_id=self.supervisor.broker_id, epoch_reader=self._epoch)})
            return
        if request.get("op") == "commission-radio":
            require(uid == 0)
            from .mesh_radio_commission import commission
            send(connection, {"result": commission(self.resource, request,
                broker_id=self.supervisor.broker_id, epoch_reader=self._epoch)})
            return
        if request.get("op") == "owner-contact-route":
            self._owner_contacts.handle(connection, request, resource=self.resource,
                supervisor=self.supervisor, uid=uid)
            return
        if request.get("op") == "import-contact":
            require(uid == 0)
            send(connection, {"result": self.resource.import_contact(request,
                broker_id=self.supervisor.broker_id, epoch_reader=self._epoch)})
            return
        if request.get("op") == "ack-voice-listener":
            self._listener_ack.exchange(connection, self.supervisor, request, pid=pid, uid=uid)
            return
        if request.get("op") == "verify-audio-operational":
            from .audio_readiness import operational_broker_request
            send(connection, operational_broker_request(self.supervisor, request,
                peer_uid=uid, listener_ack=self._listener_ack))
            return
        if request.get("op") in {"verify-deployment-drain", "verify-deployment-completion"}:
            from .deployment_drain import verify_broker_request
            send(connection, verify_broker_request(self.supervisor, self._proof, request, peer_uid=uid))
            return
        if request.get("op") == "verify-deployment-active":
            from .deployment_drain import verify_broker_active_request
            send(connection, verify_broker_active_request(self.supervisor, request, peer_uid=uid))
            return
        if uid == 0 and request.get("op") in {"drain", "close-vault", "resume"}:
            require(set(request) == {"op", "transition_id"})
            if request["op"] == "drain":
                if "public_gateway" in self.resource.config:
                    self._proof = self.supervisor.drain(request["transition_id"], timeout=60)
                else:
                    self._proof = self.supervisor.drain(request["transition_id"])
                send(connection, {"result": asdict(self._proof)})
            elif request["op"] == "close-vault":
                require(self._proof is not None and request["transition_id"] == self._proof.transition_id)
                self.supervisor.close_vault(self._proof)
                send(connection, {"result": True})
            else:
                require(self._proof is not None and request["transition_id"] == self._proof.transition_id)
                self.supervisor.resume(self._proof)
                old = self.resource
                self.resource = HardwareRuntime(self.supervisor, old.config, old.identity, old.probe,
                    identity_path=old.identity_path, public_contact_check=old._public_contact_check)
                self.supervisor.own_resource(self.resource)
                self._proof = None
                send(connection, {"result": True})
            return
        require(request == {"op": "bind"} and self.supervisor.voice_peer(pid=pid, uid=uid, draining=True))
        send(connection, {"broker_id": self.supervisor.broker_id, "actor": asdict(self.resource.actor)})
        request = receive(connection, 8192)
        require(set(request) == {"op", "broker_id", "epoch", "method", "args"} and request["op"] == "mesh")
        require(self.supervisor.voice_peer(pid=pid, uid=uid, broker_id=request["broker_id"], draining=True))
        if request["method"] == "close":
            require(request["args"] == {} and request["epoch"] is None)
            send(connection, {"result": self.supervisor.resource_drain(20) is True})
            return
        epoch = request["epoch"]
        require(type(epoch) is str and re.fullmatch(r"[0-9a-f]{32}", epoch) is not None)
        challenge_mutex = threading.Lock()
        failed = threading.Event()
        deadline = time.monotonic() + 45
        def guard():
            with challenge_mutex:
                try:
                    require(not failed.is_set() and time.monotonic() < deadline and self._epoch() == epoch
                            and self.supervisor.voice_peer(pid=pid, uid=uid, broker_id=request["broker_id"]))
                    nonce = secrets.token_hex(16)
                    send(connection, {"guard": nonce})
                    answer = receive(connection, 256)
                    require(type(answer) is dict and set(answer) == {"guard", "allowed"}
                            and answer["guard"] == nonce and answer["allowed"] is True
                            and self._epoch() == epoch and time.monotonic() < deadline)
                    return True
                except Exception:
                    failed.set()
                    return False
        require(guard())
        if request["method"] in ("render_challenge", "rendered"):
            binding = hashlib.sha256(json.dumps([request["broker_id"], pid, uid, epoch], separators=(",", ":")).encode()).hexdigest()
            def background_check():
                return (self._epoch() == epoch and self.supervisor.voice_peer(pid=pid, uid=uid, broker_id=request["broker_id"]))
            result = self.resource.invoke(request["method"], request["args"], guard,
                                          request_binding=(epoch, binding), background_check=background_check)
        else:
            result = self.resource.invoke(request["method"], request["args"], guard)
        require(guard())
        send(connection, {"result": result})


class MeshVoiceBackend(RootVoiceBackend):
    def start_voice(self):
        deadline = self._deadline()
        require(self.system._show("sage-lantern-voice-loop.service", deadline, ("Restart",))["Restart"] == "no")
        text = self.system._execute([BUSCTL, "--system", "--no-pager", "get-property", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1/unit/sage_2dlantern_2dvoice_2dloop_2eservice",
            "org.freedesktop.systemd1.Unit", "Conditions"], deadline)
        values = shlex.split(text)
        require(len(values) >= 2 and values[0] == "a(sbbsi)" and values[1].isdigit()
                and int(values[1]) <= 128 and len(values) == 2 + int(values[1]) * 5)
        require(any(values[offset:offset+4] == ["ConditionPathExists", "false", "false", str(START)]
                    for offset in range(2, len(values), 5)))
        require(not os.path.lexists(START))
        publish(START, {"request_id": str(uuid.uuid4())}, mode=0o644)
        try:
            return super().start_voice()
        finally:
            START.unlink()
            descriptor = os.open(START.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def device_policy(config):
    validate_config(config)
    return "[Service]\nDevicePolicy=closed\nDeviceAllow=\nDeviceAllow=" + config["radio"]["stable_path"] + " rw\n"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Explicit root mesh supervisor; never launches voice as root")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--print-device-policy", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0 and hasattr(socket, "SO_PEERCRED"))
    config = load_config()
    if args.print_device_policy:
        print(device_policy(config), end="")
        return
    require(not os.path.lexists(SOCKET) and not os.path.lexists(START))
    require(_marker_state(MARKER) is True)
    identity, key_fd = load_identity(config)
    client = None
    try:
        client = SageClient(config["endpoint"], identity, timeout=5, trust_env=False)
        probe = MessageStorageProbe(client, actor=RecipientID(config["chain_id"], identity.agent_id), endpoint=config["endpoint"])
    except BaseException:
        try:
            if client is not None:
                client.__exit__(None, None, None)
        finally:
            os.close(key_fd)
        raise
    supervisor = server = None
    stopped = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda signum, frame: stopped.set())
    try:
        supervisor = RootStorageSupervisor(STATE, backend=MeshVoiceBackend(voice_uid=config["voice_uid"], request_path=REQUEST),
                                           snapshot=probe.snapshot, access_epoch=access_epoch)
        serial = STATE / "serial"
        serial.mkdir(mode=0o700, exist_ok=True)
        while not stopped.is_set():
            try:
                probe.snapshot()
                break
            except Exception:
                stopped.wait(2)
        require(not stopped.is_set())
        supervisor.start()
        resource = HardwareRuntime(supervisor, config, identity, probe, identity_path=Path(f"/proc/self/fd/{key_fd}"))
        supervisor.own_resource(resource)
        daemon = MeshDaemon(supervisor, resource)
        from .storage_supervisor_socket import StorageBrokerServer
        class Server(StorageBrokerServer):
            def _handle(self, connection):
                try:
                    connection.settimeout(5)
                    pid, uid = peer(connection)
                    daemon.handle(connection, pid=pid, uid=uid)
                except Exception as error:
                    import syslog
                    import traceback
                    frames = traceback.extract_tb(error.__traceback__)
                    syslog.syslog(syslog.LOG_ERR, json.dumps(dict(event="mesh_request_failed",
                        error=type(error).__name__, frames=[f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames])))
                    try:
                        send(connection, {"error": "mesh_ipc_unavailable"})
                    except Exception:
                        pass
                finally:
                    connection.close()
                    with self._mutex:
                        self._workers.discard(threading.current_thread())
                    self._slots.release()
        server = Server(supervisor, path=SOCKET)
        worker = threading.Thread(target=server.serve, daemon=True)
        worker.start()
        beacon_stop = threading.Event()
        def beacon_loop():
            while not beacon_stop.wait(BEACON_SECONDS):
                try:
                    if device_locked():
                        daemon.beacon(lambda: not beacon_stop.is_set() and not stopped.is_set())
                except Exception:
                    pass
        beacon = threading.Thread(target=beacon_loop, name="lantern-locked-beacon", daemon=True)
        beacon.start()
        stopped.wait()
    finally:
        if "beacon_stop" in locals():
            beacon_stop.set()
        if shutdown_process(supervisor, server, client, key_fd=key_fd,
                **({"timeout": 60} if "public_gateway" in config else {})) is not True:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
