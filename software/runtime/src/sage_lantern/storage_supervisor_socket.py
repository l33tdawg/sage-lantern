"""Linux root storage broker transport; no HTTP, credential logging or retries.

Run explicitly as root with --serve and a preprovisioned private state directory.
The voice consumer uses CaptureLease BEFORE destructive receive, check only for
its admitted persistence, and closes the lease after completion. drain_check is
only for producer-fenced shutdown scans; it is never a user access callback.

Root suspend integration: request drain with the current transition after auth
has positively drained, require the returned bound DrainProof, then continue the
existing camera/kiosk/inference gates. Use close_vault only when deliberately
closing SAGE; normal suspend can leave the warm SAGE process running. Never call
systemctl stop SAGE outside this gate in cooperating management code. The broker
and its callback server must remain alive until voice has joined. No automatic
restart/recovery: an existing fence requires operator reconciliation. Wiring the
consumer and routing the suspend helper through this broker are activation
prerequisites, not actions performed by importing or testing these modules.
"""

import argparse
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import threading
import time

from .storage_supervisor import DrainProof, RootStorageSupervisor, RootVoiceBackend, StorageSupervisorError
from .suspend_io import unique_object


SOCKET = Path("/run/sage-lantern-storage-broker.sock")
LIMIT = 1024
_RETAINED_SHUTDOWN = []


def _receive(connection):
    raw = bytearray()
    while len(raw) <= LIMIT:
        chunk = connection.recv(min(256, LIMIT + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
        if b"\n" in raw:
            break
    if len(raw) > LIMIT or not raw.endswith(b"\n") or b"\n" in raw[:-1]:
        raise StorageSupervisorError()
    value = json.loads(raw, object_pairs_hook=unique_object,
                       parse_constant=lambda value: (_ for _ in ()).throw(StorageSupervisorError()))
    if type(value) is not dict:
        raise StorageSupervisorError()
    return value


def _send(connection, value):
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(raw) > LIMIT:
        raise StorageSupervisorError()
    connection.sendall(raw)


class StorageBrokerServer:
    def __init__(self, supervisor, *, path=SOCKET):
        if os.geteuid() != 0 or not hasattr(socket, "SO_PEERCRED"):
            raise StorageSupervisorError()
        self.supervisor, self.path = supervisor, Path(path)
        parent = self.path.parent
        for ancestor in (parent, *parent.parents):
            info = ancestor.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or ancestor.resolve() != ancestor):
                raise StorageSupervisorError()
        if not self.path.is_absolute() or os.path.lexists(self.path):
            raise StorageSupervisorError()
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(str(self.path))
        self._inode = self.path.lstat().st_ino
        os.chmod(self.path, 0o666)
        self._socket.listen(8)
        self._socket.settimeout(0.25)
        self._stop = threading.Event()
        self._slots = threading.BoundedSemaphore(8)
        self._workers = set()
        self._mutex = threading.Lock()
        self._proof = None
        self._serving_thread = None
        self._closed = False

    def dispatch(self, request, *, pid, uid):
        operation = request.get("op")
        fields = {"acquire": {"op", "epoch"}, "check": {"op", "lease"},
                  "finish": {"op", "lease"}, "drain-check": {"op"},
                  "start": {"op"}, "drain": {"op", "transition_id"},
                  "close-vault": {"op", "transition_id"}}
        if type(operation) is not str or operation not in fields or set(request) != fields[operation]:
            raise StorageSupervisorError()
        if operation in {"start", "drain", "close-vault"}:
            if uid != 0:
                raise StorageSupervisorError()
            if operation == "start":
                self.supervisor.start()
                return {"started": True, "broker_id": self.supervisor.broker_id}
            if operation == "drain":
                self._proof = self.supervisor.drain(request["transition_id"])
                return {"broker_id": self._proof.broker_id, "transition_id": self._proof.transition_id,
                        "vault_epoch": self._proof.vault_epoch}
            if self._proof is None or request["transition_id"] != self._proof.transition_id:
                raise StorageSupervisorError()
            self.supervisor.close_vault(self._proof)
            return {"vault_closed": True}
        if operation == "acquire":
            return {"lease": self.supervisor.acquire(pid=pid, uid=uid, epoch=request["epoch"])}
        if operation == "drain-check":
            return {"allowed": self.supervisor.drain_check(pid=pid, uid=uid)}
        if type(request["lease"]) is not str or len(request["lease"]) != 64:
            raise StorageSupervisorError()
        if operation == "check":
            return {"allowed": self.supervisor.check(request["lease"], pid=pid, uid=uid)}
        self.supervisor.finish_capture(request["lease"], pid=pid, uid=uid)
        return {"finished": True}

    def _handle(self, connection):
        try:
            connection.settimeout(5)
            pid, uid, group = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            result = self.dispatch(_receive(connection), pid=pid, uid=uid)
            _send(connection, result)
        except Exception:
            try:
                _send(connection, {"error": "storage_supervisor_unavailable"})
            except Exception:
                pass
        finally:
            connection.close()
            with self._mutex:
                self._workers.discard(threading.current_thread())
            self._slots.release()

    def serve(self):
        self._serving_thread = threading.current_thread()
        while not self._stop.is_set():
            try:
                connection, address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                connection.close()
                continue
            worker = threading.Thread(target=self._handle, args=(connection,), daemon=True)
            with self._mutex:
                if self._stop.is_set():
                    connection.close()
                    self._slots.release()
                    break
                self._workers.add(worker)
            worker.start()

    def close(self, timeout=30):
        if self._closed:
            return True
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            return False
        deadline = time.monotonic() + timeout
        self._stop.set()
        self._socket.close()
        begun = self.supervisor.begin_shutdown(timeout) is True
        serving = self._serving_thread
        if serving is not None and serving is not threading.current_thread():
            serving.join(max(0, deadline - time.monotonic()))
            if serving.is_alive():
                return False
        with self._mutex:
            workers = list(self._workers)
        for worker in workers:
            if worker is threading.current_thread():
                return False
            worker.join(max(0, deadline - time.monotonic()))
            if worker.is_alive():
                return False
        remaining = deadline - time.monotonic()
        if not begun or remaining <= 0 or self.supervisor.finish_shutdown(remaining) is not True:
            return False
        if time.monotonic() >= deadline:
            return False
        self.supervisor.close()
        self._closed = True
        if self.path.lstat().st_ino == self._inode:
            self.path.unlink()
        return True


def request(value, *, timeout=6, path=SOCKET):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout)
        connection.connect(str(path))
        if not hasattr(socket, "SO_PEERCRED"):
            raise StorageSupervisorError()
        pid, uid, group = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            raise StorageSupervisorError()
        _send(connection, value)
        result = _receive(connection)
        if "error" in result:
            raise StorageSupervisorError()
        return result
    finally:
        connection.close()


class CaptureLease:
    def __init__(self, epoch, *, transport=request):
        self._transport = transport
        self._lease = None
        value = transport({"op": "acquire", "epoch": epoch})
        if (type(value) is not dict or set(value) != {"lease"} or type(value["lease"]) is not str
                or len(value["lease"]) != 64 or any(member not in "0123456789abcdef" for member in value["lease"])):
            raise StorageSupervisorError()
        self._lease = value["lease"]

    def check(self):
        try:
            if self._lease is None:
                return False
            value = self._transport({"op": "check", "lease": self._lease})
            return type(value) is dict and set(value) == {"allowed"} and value["allowed"] is True
        except Exception:
            return False

    def close(self):
        lease, self._lease = self._lease, None
        if lease is not None:
            self._transport({"op": "finish", "lease": lease})


def drain_check(*, transport=request):
    try:
        value = transport({"op": "drain-check"})
        return type(value) is dict and set(value) == {"allowed"} and value["allowed"] is True
    except Exception:
        return False


def drain(transition_id, *, transport=request):
    value = transport({"op": "drain", "transition_id": transition_id}, timeout=65)
    if (type(value) is not dict or set(value) != {"broker_id", "transition_id", "vault_epoch"}
            or value["transition_id"] != transition_id
            or type(value["broker_id"]) is not str or re.fullmatch(r"[0-9a-f]{32}", value["broker_id"]) is None
            or type(value["vault_epoch"]) is not str or re.fullmatch(r"[0-9a-f]{64}:[1-9][0-9]{0,19}", value["vault_epoch"]) is None
            or int(value["vault_epoch"].split(":")[1]) > 2**64 - 1):
        raise StorageSupervisorError()
    return DrainProof(**value)


def shutdown_process(supervisor, server, client, *, key_fd=None, timeout=25):
    """Keep failed dependencies strongly referenced until process exit, not GC."""
    deadline = time.monotonic() + timeout
    try:
        if server is not None:
            clean = server.close(timeout=timeout) is True
        elif supervisor is not None:
            begun = supervisor.begin_shutdown(timeout) is True
            remaining = deadline - time.monotonic()
            clean = begun and remaining > 0 and supervisor.finish_shutdown(remaining) is True
            if clean:
                supervisor.close()
        else:
            clean = True
    except BaseException:
        clean = False
    if not clean:
        _RETAINED_SHUTDOWN.append((supervisor, server, client, key_fd))
        return False
    try:
        client.__exit__(None, None, None)
    finally:
        if key_fd is not None:
            os.close(key_fd)
    return True


def main():
    parser = argparse.ArgumentParser(description="Explicit root voice/storage lifecycle broker; no automatic recovery")
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--chain-id", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--voice-uid", type=int, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.identity_file.is_absolute():
        raise StorageSupervisorError()
    from sage_sdk import AgentIdentity, SageClient
    from .device_access import access_epoch
    from .message_contacts import RecipientID
    from .message_storage import MessageStorageProbe
    identity = AgentIdentity.from_file(str(args.identity_file))
    client = SageClient(args.endpoint, identity, timeout=5, trust_env=False)
    supervisor = server = None
    try:
        probe = MessageStorageProbe(client, actor=RecipientID(args.chain_id, identity.agent_id), endpoint=args.endpoint)
        supervisor = RootStorageSupervisor(args.state_dir, backend=RootVoiceBackend(voice_uid=args.voice_uid),
                                           snapshot=probe.snapshot, access_epoch=access_epoch)
        server = StorageBrokerServer(supervisor)
        server.serve()
    finally:
        if shutdown_process(supervisor, server, client) is not True:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
