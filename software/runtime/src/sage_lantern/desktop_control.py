"""Optional root-peer-only Unix control surface for the native desktop adapter.

No TCP, browser origin, persisted grants or PIN logs. One bounded JSON request
per connection. This is broker plumbing, not proof of compositor isolation.
Root must verify the actual native peer/session before requesting a binding.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import struct
import threading
import time


MAX_BODY = 512
MAX_WORKERS = 4
READ_TIMEOUT = 2.0
DRAIN_TIMEOUT = 5.0
ERRORS = frozenset({
    'invalid_request', 'stale_guard', 'already_active', 'unlock_cancelled',
    'verification_busy', 'verification_failed', 'invalid_pin', 'rate_limited',
    'setup_required', 'setup_unavailable', 'unavailable',
})


def root_peer(request):
    """Kernel credentials; neither JSON assertions nor caller-supplied UID."""
    try:
        _, uid, _ = struct.unpack('3i', request.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        return uid == 0
    except (AttributeError, OSError, struct.error):
        return False


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def dispatch(broker, raw):
    """Called only after root peer verification; never add to HTTP ROUTES."""
    if type(raw) is not bytes or not raw.endswith(b'\n') or len(raw) > MAX_BODY + 1:
        raise ValueError
    payload = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    if type(payload) is not dict or type(payload.get('action')) is not str:
        raise ValueError
    action = payload['action']
    if action == 'open':
        if set(payload) != {'action', 'binding', 'mode'}:
            raise ValueError
        return broker.desktop_open(payload['binding'], payload['mode'])
    if action not in ('unlock', 'lock', 'activate', 'check', 'presence', 'activity'):
        raise ValueError
    fields = {'action', 'guard'}
    if action != 'lock':
        fields.add('pin' if action == 'unlock' else 'token')
    if (set(payload) != fields or any(type(value) is not str for value in payload.values())
            or re.fullmatch(r'[A-Za-z0-9_-]{43}', payload['guard']) is None):
        raise ValueError
    if action == 'lock':
        return broker.desktop_lock(payload['guard'])
    if action == 'unlock':
        return broker.desktop_unlock(payload['guard'], payload['pin'])
    if re.fullmatch(r'[A-Za-z0-9_-]{43}', payload['token']) is None:
        raise ValueError
    return broker.desktop_access(payload['guard'], payload['token'], action)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(READ_TIMEOUT)
        try:
            raw = self.rfile.readline(MAX_BODY + 2)
            result = dispatch(self.server.broker, raw)
            reply = {'ok': True, 'result': result}
        except (ValueError, UnicodeError, RecursionError):
            reply = {'ok': False, 'error': 'invalid_request'}
        except Exception as error:
            code = getattr(error, 'code', 'unavailable')
            reply = {'ok': False, 'error': code if type(code) is str and code in ERRORS else 'unavailable'}
        try:
            body = json.dumps(reply, separators=(',', ':'), allow_nan=False).encode('ascii') + b'\n'
            if len(body) > MAX_BODY + 1:
                body = b'{"ok":false,"error":"unavailable"}\n'
            self.wfile.write(body)
        except (OSError, ValueError):
            pass


class DesktopControlServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, path, broker, *, peer_check=root_peer):
        self.path = Path(path)
        self.broker = broker
        self.peer_check = peer_check
        self.thread = None
        self.slots = threading.BoundedSemaphore(MAX_WORKERS)
        self.changed = threading.Condition()
        self.active = set()
        self.identity = None
        self._validate_path()
        try:
            super().__init__(str(self.path), Handler, bind_and_activate=False)
            self.server_bind()
            os.chmod(self.path, 0o600)
            metadata = self.path.lstat()
            self.identity = (metadata.st_dev, metadata.st_ino)
            self.server_activate()
        except Exception:
            if hasattr(self, 'socket'):
                self.socket.close()
            # A bound socket may remain after construction fails; do not unlink
            # any object unless we recorded its own identity successfully.
            raise

    def _validate_path(self):
        if not self.path.is_absolute() or self.path.parent.resolve(strict=True) != self.path.parent:
            raise ValueError('desktop_socket_unavailable')
        metadata = self.path.parent.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700):
            raise ValueError('desktop_socket_unavailable')
        for parent in self.path.parent.parents:
            metadata = parent.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in (0, os.geteuid())
                    or (metadata.st_mode & 0o022 and not (
                        metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX))):
                raise ValueError('desktop_socket_unavailable')
        if os.path.lexists(self.path):
            raise ValueError('desktop_socket_unavailable')

    def verify_request(self, request, client_address):
        try:
            return self.peer_check(request) is True
        except Exception:
            return False

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self.changed:
            if self.broker.closing:
                self.slots.release()
                self.shutdown_request(request)
                return
            self.active.add(request)
            self.changed.notify_all()
        try:
            super().process_request(request, client_address)
        except Exception:
            with self.changed:
                self.active.discard(request)
                self.changed.notify_all()
            self.slots.release()
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.changed:
                self.active.discard(request)
                self.changed.notify_all()
            self.slots.release()

    def handle_error(self, request, client_address):
        pass

    def start(self):
        if self.thread is not None:
            raise ValueError('desktop_socket_unavailable')
        self.thread = threading.Thread(target=self.serve_forever,
                                       kwargs={'poll_interval': 0.25}, daemon=True)
        self.thread.start()

    def server_close(self):
        """Close all admission; caller must retain a false drain result.

        Return only this surface's worker result. The owning broker combines
        it with HTTP workers before publishing its one final drain receipt.
        """
        self.broker.shutdown()
        if self.thread is not None:
            self.shutdown()
            self.thread.join(timeout=1)
        with self.changed:
            requests = tuple(self.active)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().server_close()
        deadline = time.monotonic() + DRAIN_TIMEOUT
        with self.changed:
            while self.active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.changed.wait(remaining)
            drained = not self.active
        try:
            metadata = self.path.lstat()
            if (stat.S_ISSOCK(metadata.st_mode)
                    and (metadata.st_dev, metadata.st_ino) == self.identity):
                self.path.unlink()
        except FileNotFoundError:
            pass
        return drained
