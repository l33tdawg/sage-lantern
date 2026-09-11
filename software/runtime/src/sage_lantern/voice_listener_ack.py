"""Authenticated listener diagnostics, not capture authorization or startup admission.

Voice initiates one bounded exchange on the existing root Unix socket after its
HTTP listener is serving. The daemon owns one in-memory acknowledgement cache.
No persisted marker survives broker restart; live producer/storage checks remain
mandatory at both publication and consumption. Shared daemon/voice wiring is
intentionally separate from these protocol primitives.
"""

from dataclasses import asdict
import hashlib
import json
import secrets
import socket
import threading
import time

from .mesh_voice_ipc import MeshIPCError, connect, receive, require, send
from .storage_supervisor import RootStorageSupervisor


OP = "ack-voice-listener"
TIMEOUT = 2
MAX_AGE = 3


class ListenerCheckBusy(MeshIPCError):
    def __init__(self, lock_name):
        require(lock_name in {"control", "state"})
        self.lock_name = lock_name
        super().__init__()


def binding(supervisor, *, pid, uid):
    require(type(supervisor) is RootStorageSupervisor and type(pid) is int and type(uid) is int)

    def retained():
        if not supervisor._control.acquire(timeout=0.05):
            raise ListenerCheckBusy("control")
        try:
            if not supervisor._mutex.acquire(timeout=0.05):
                raise ListenerCheckBusy("state")
            try:
                supervisor._held()
                require(supervisor.phase == "active" and not supervisor._shutdown_requested
                        and supervisor._proof is None and supervisor._resource is not None
                        and not supervisor._resource_joined)
                producer = supervisor._producer
                require((pid, uid) == (producer.pid, producer.uid))
                raw = json.dumps(dict(broker=supervisor.broker_id, epoch=supervisor._baseline,
                    vault=supervisor._vault, producer=asdict(producer), resource=id(supervisor._resource)),
                    sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                return hashlib.sha256(raw).hexdigest(), supervisor._baseline, supervisor._vault, producer
            finally:
                supervisor._mutex.release()
        finally:
            supervisor._control.release()

    before = retained()
    require(supervisor._epoch() == before[1] and supervisor._backend.vault_identity() == before[2]
            and supervisor._backend.producer() == before[3] and retained() == before)
    return before[0]


class ListenerAcknowledgements:
    def __init__(self):
        self._mutex = threading.Lock()
        self._ack = None

    def exchange(self, connection, supervisor, request, *, pid, uid):
        require(request == {"op": OP})
        started = time.monotonic()
        original = binding(supervisor, pid=pid, uid=uid)
        nonce = secrets.token_hex(32)
        connection.settimeout(TIMEOUT)
        send(connection, {"listener_challenge": nonce, "binding": original})
        response = receive(connection, 512)
        require(set(response) == {"listener_challenge", "binding", "listening"}
                and response["listener_challenge"] == nonce and response["binding"] == original
                and response["listening"] is True)
        require(binding(supervisor, pid=pid, uid=uid) == original
                and time.monotonic() - started < TIMEOUT)
        with self._mutex:
            self._ack = (original, pid, uid, started)
        send(connection, {"result": True})

    def diagnostic(self):
        if not self._mutex.acquire(blocking=False):
            return dict(ack_status="cache_busy", ack_age_ms=None)
        try:
            observed = self._ack
        finally:
            self._mutex.release()
        if observed is None:
            return dict(ack_status="absent", ack_age_ms=None)
        age = time.monotonic() - observed[3]
        if not 0 <= age < float("inf"):
            return dict(ack_status="clock_invalid", ack_age_ms=None)
        return dict(ack_status="fresh" if age < MAX_AGE else "expired",
                    ack_age_ms=min(86400000, int(age * 1000)))

    def verified(self, supervisor, *, pid, uid, propagate_busy=False):
        try:
            with self._mutex:
                observed = self._ack
            require(observed is not None and observed[1:3] == (pid, uid)
                    and 0 <= time.monotonic() - observed[3] < MAX_AGE)
            require(binding(supervisor, pid=pid, uid=uid) == observed[0])
            with self._mutex:
                current = self._ack
                require(current is not None and current[:3] == observed[:3]
                        and current[3] >= observed[3] and 0 <= time.monotonic() - current[3] < MAX_AGE)
            return True
        except ListenerCheckBusy:
            if propagate_busy is True:
                raise
            return False
        except Exception:
            return False


def acknowledge(server, serving_thread, stop_requested):
    """Call only for the voice-owned server; no HTTP request or microphone capture."""
    connection = connect()
    try:
        connection.settimeout(TIMEOUT)
        send(connection, {"op": OP})
        challenge = receive(connection, 512)
        require(set(challenge) == {"listener_challenge", "binding"})
        require(all(type(value) is str and len(value) == 64
                    and all(character in "0123456789abcdef" for character in value)
                    for value in challenge.values()))
        require(serving_thread.is_alive() is True and not stop_requested.is_set())
        listener = server.socket
        require(listener.family == socket.AF_INET and listener.type == socket.SOCK_STREAM
                and listener.getsockname() == ("127.0.0.1", 3197)
                and listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1)
        send(connection, {**challenge, "listening": True})
        result = receive(connection, 128)
        require(set(result) == {"result"} and result["result"] is True)
        require(serving_thread.is_alive() is True and not stop_requested.is_set())
        return True
    finally:
        connection.close()
