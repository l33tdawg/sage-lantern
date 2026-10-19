#!/usr/bin/env python3
"""Root-enrolled, exact-compositor Unix adapter candidate; never auto-enroll.

Run only in a guarded qualification session, using a root-owned policy and
the observed GNOME Shell PID. Does not restart, unlock or launch a session.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import select
import signal
import socket
import socketserver
import stat
import threading
import time

from sage_lantern.desktop_native import (BrokerClient, NativeError, NativeSession,
    PeerAnchor, decode, kernel_peer, pidfd_alive, trusted_file)
from sage_lantern.desktop_control import MAX_BODY


class Handler(socketserver.StreamRequestHandler):
    rbufsize = 0

    def handle(self):
        native = NativeSession(self.server.anchor.binding, self.server.broker,
                               self.server.peer_valid)
        self.server.native = native
        self.request.settimeout(4)
        stopped = threading.Event()

        def watch():
            next_identity_check = 0
            while not stopped.wait(0.1):
                try:
                    valid = pidfd_alive(self.server.pidfd)
                    if time.monotonic() >= next_identity_check:
                        valid = valid and self.server.peer_valid()
                        next_identity_check = time.monotonic() + 1
                    if not valid:
                        native.close()
                        self.request.shutdown(socket.SHUT_RDWR)
                        return
                    if select.select([self.request], [], [], 0)[0]:
                        if self.request.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b'':
                            native.close()  # Fence PIN verification before it returns.
                            return
                except (OSError, ValueError):
                    native.close()
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            while True:
                raw = self.rfile.readline(MAX_BODY + 2)
                if not raw:
                    break
                try:
                    result = native.request(decode(raw))
                    reply = {'ok': True, 'result': result}
                except NativeError as error:
                    # A rejected PIN can be retried within the shared attempt budget.
                    code = error.code
                    recoverable = code in {'invalid_pin', 'verification_failed',
                                           'rate_limited', 'verification_busy'}
                    if not recoverable:
                        native.close()
                    reply = {'ok': False, 'error': code if recoverable else 'unavailable'}
                self.wfile.write(json.dumps(reply, separators=(',', ':')).encode() + b'\n')
                self.wfile.flush()
                if native.closed:
                    break
        except Exception:
            pass  # No exception payload/traceback can contain a PIN or token.
        finally:
            stopped.set()
            native.close()
            watcher.join(timeout=1)
            self.server.native = None


class NativeServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, path, anchor, broker, pidfd, *, policy_check=lambda: True):
        self.anchor, self.broker, self.pidfd = anchor, broker, pidfd
        self.path, self.policy_check = Path(path), policy_check
        self.slot = threading.Lock()
        self.native = None
        self.client = None
        self.identity = None
        parent = self.path.parent
        if not self.path.is_absolute() or parent.resolve(strict=True) != parent:
            raise NativeError()
        for directory in (parent, *parent.parents):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise NativeError()
        if os.path.lexists(self.path):
            raise NativeError()
        super().__init__(str(path), Handler, bind_and_activate=False)
        try:
            self.server_bind()
            info = self.path.lstat()
            self.identity = (info.st_dev, info.st_ino)
            os.chown(path, 0, pwd.getpwuid(anchor.uid).pw_gid)
            os.chmod(path, 0o660)
            self.server_activate()
        except Exception:
            self.server_close()
            raise

    def peer_valid(self):
        try:
            return (pidfd_alive(self.pidfd) and self.policy_check() is True
                    and self.anchor.matches(self.anchor.pid, self.anchor.uid))
        except Exception:
            return False

    def verify_request(self, connection, address):
        try:
            return kernel_peer(connection) == (self.anchor.pid, self.anchor.uid) and self.peer_valid()
        except Exception:
            return False

    def process_request(self, request, address):
        if not self.slot.acquire(blocking=False):
            self.shutdown_request(request)
            return
        self.client = request
        try:
            super().process_request(request, address)
        except Exception:
            self.client = None
            self.slot.release()
            self.shutdown_request(request)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.client = None
            self.slot.release()

    def handle_error(self, request, address):
        pass

    def server_close(self):
        if self.native is not None:
            self.native.close()
        if self.client is not None:
            try:
                self.client.shutdown(2)
            except OSError:
                pass
        super().server_close()
        try:
            info = self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.identity:
                self.path.unlink()
        except FileNotFoundError:
            pass


def load_policy(path):
    policy_bytes = trusted_file(path)
    policy = json.loads(policy_bytes)
    if (type(policy) is not dict or set(policy) != {'schema', 'uid', 'overlay',
        'executable_sha256', 'files', 'native_socket', 'broker_socket', 'broker_uid'}
        or policy['schema'] != 'sage.lantern.desktop-native-policy.v1'
        or type(policy['files']) is not dict or not policy['files']
        or type(policy['uid']) is not int or policy['uid'] <= 0
        or type(policy['broker_uid']) is not int or policy['broker_uid'] <= 0):
        raise NativeError()
    overlay = policy['overlay']
    if type(overlay) is not str or not overlay.startswith('/org/gnome/shell='):
        raise NativeError()
    directory = Path(overlay.split('=', 1)[1])
    if not directory.is_absolute() or directory.name != 'overlay':
        raise NativeError()
    mandatory = {str(directory / 'ui' / name) for name in
        ('main.js', 'init.js', 'lanternPinGuard.js', 'lanternGuardState.js')}
    mandatory.update({str(directory.parent / 'manifest.json'),
                      '/usr/lib/gnome-shell/libshell-18.so'})
    if (set(policy['files']) != mandatory
            or any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None
                   for value in [policy['executable_sha256'], *policy['files'].values()])
            or policy['native_socket'] != '/run/sage-lantern-desktop/native.sock'
            or policy['broker_socket'] != '/run/sage-lantern-desktop-auth/control.sock'):
        raise NativeError()
    payloads = {str(path): hashlib.sha256(policy_bytes).hexdigest(),
                '/usr/bin/gnome-shell': policy['executable_sha256'], **policy['files']}
    paths = {Path(path), *(Path(filename) for filename in payloads)}
    for filename in tuple(paths):
        paths.update(filename.parents)
    def metadata():
        records = {}
        for filename in paths:
            info = filename.lstat()
            if (info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode)):
                raise NativeError()
            identity = (info.st_dev, info.st_ino, info.st_mode)
            if stat.S_ISREG(info.st_mode):
                identity += (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            elif not stat.S_ISDIR(info.st_mode):
                raise NativeError()
            records[str(filename)] = identity
        return records
    baseline = metadata()
    for filename, digest in payloads.items():
        trusted_file(filename, digest)
    if metadata() != baseline:
        raise NativeError()  # An update raced the enrollment hashes.
    def check():
        # Root-owned payloads were hashed at enrollment. Any subsequent mutation
        # revokes enrollment rather than repeatedly reading the shared library.
        return metadata() == baseline
    return policy, check


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True, type=Path)
    parser.add_argument('--shell-pid', required=True, type=int)
    parser.add_argument('--session', required=True)
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or not hasattr(os, 'pidfd_open'):
        raise NativeError()
    policy, check = load_policy(args.policy)
    fd = os.pidfd_open(args.shell_pid)
    try:
        anchor = PeerAnchor.enroll(args.shell_pid, policy['uid'], args.session,
                                  policy['overlay'], policy['executable_sha256'])
        if not pidfd_alive(fd) or not anchor.matches(anchor.pid, anchor.uid):
            raise NativeError()
        broker = BrokerClient(policy['broker_socket'], policy['broker_uid'])
        with NativeServer(policy['native_socket'], anchor, broker, fd, policy_check=check) as server:
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            server.timeout = 0.25
            while not stop.is_set() and pidfd_alive(fd) and check():
                server.handle_request()
    finally:
        os.close(fd)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit('desktop_adapter_unavailable') from None
