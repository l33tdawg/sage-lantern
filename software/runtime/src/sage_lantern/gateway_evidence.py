"""Narrow root-only listener evidence; privileged despite not loading SAGE keys.

CAP_SYS_PTRACE remains security-sensitive authority, including procfs access.
Filesystem masks and syscall restrictions do not make this helper incapable of
accessing secrets if compromised. Only fixed-manifest metadata is inspected.
"""

import hashlib
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import threading
import time

from .mesh_voice_ipc import peer, receive, send
from .public_mesh_protocol import canonical, require


SOCKET = Path("/run/sage-lantern-gateway-evidence/control.sock")


class DeadlineConnection:
    def __init__(self, connection, deadline):
        self.connection, self.deadline = connection, deadline

    def check(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0)
        self.connection.settimeout(remaining)

    def recv(self, size):
        self.check()
        result = self.connection.recv(size)
        self.check()
        return result

    def sendall(self, data):
        self.check()
        self.connection.sendall(data)
        self.check()


def notify_ready():
    address = os.environ.get("NOTIFY_SOCKET")
    if address is None:
        return
    require(type(address) is str and (address.startswith("/") or address.startswith("@")))
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
        connection.settimeout(1)
        connection.sendto(b"READY=1", address)


def current(settings):
    connection = None
    deadline = time.monotonic() + 3
    try:
        for directory in SOCKET.parents:
            info = directory.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        before = SOCKET.lstat()
        require(stat.S_ISSOCK(before.st_mode) and before.st_uid == 0 and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_nlink == 1)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(3)
        connection.connect(str(SOCKET))
        require(peer(connection)[1] == 0)
        bounded = DeadlineConnection(connection, deadline)
        nonce = secrets.token_hex(16)
        digest = hashlib.sha256(canonical(settings)).hexdigest()
        send(bounded, dict(nonce=nonce, config_digest=digest))
        response = receive(bounded, 512)
        after = SOCKET.lstat()
        return (time.monotonic() < deadline and (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
            and type(response) is dict and set(response) == {"nonce", "config_digest", "current"}
            and response["nonce"] == nonce and response["config_digest"] == digest and response["current"] is True)
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def handle(connection, *, config_reader, inspect, peer_reader=peer):
    import re
    bounded = DeadlineConnection(connection, time.monotonic() + 2)
    require(peer_reader(connection)[1] == 0)
    request = receive(bounded, 512)
    require(type(request) is dict and set(request) == {"nonce", "config_digest"}
        and type(request["nonce"]) is str and re.fullmatch(r"[0-9a-f]{32}", request["nonce"]) is not None
        and type(request["config_digest"]) is str and re.fullmatch(r"[0-9a-f]{64}", request["config_digest"]) is not None)
    settings = config_reader()["public_gateway"]
    digest = hashlib.sha256(canonical(settings)).hexdigest()
    require(request["config_digest"] == digest)
    allowed = inspect(settings) is True and config_reader()["public_gateway"] == settings
    send(bounded, dict(nonce=request["nonce"], config_digest=digest, current=allowed))


def serve(*, config_reader, inspect, stopped):
    require(os.geteuid() == 0 and hasattr(socket, "SO_PEERCRED"))
    if "public_gateway" not in config_reader():
        notify_ready()
        return
    directory = SOCKET.parent
    info = directory.lstat()
    require(directory.resolve() == directory and stat.S_ISDIR(info.st_mode) and info.st_uid == 0
        and stat.S_IMODE(info.st_mode) == 0o700 and not os.path.lexists(SOCKET))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    inode = None
    try:
        listener.bind(str(SOCKET))
        os.chmod(SOCKET, 0o600)
        inode = SOCKET.lstat().st_ino
        listener.listen(4)
        listener.settimeout(0.25)
        notify_ready()
        while not stopped.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(2)
                try:
                    handle(connection, config_reader=config_reader, inspect=inspect)
                except Exception:
                    pass
    finally:
        listener.close()
        if inode is not None and SOCKET.lstat().st_ino == inode:
            SOCKET.unlink()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fixed-manifest root gateway listener evidence only")
    parser.add_argument("--serve", action="store_true", required=True)
    parser.parse_args()
    from .mesh_daemon import load_config
    from .mesh_gateway_root import backend_process_current
    stopped = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda signum, frame: stopped.set())
    serve(config_reader=load_config, inspect=backend_process_current, stopped=stopped)


if __name__ == "__main__":
    main()
