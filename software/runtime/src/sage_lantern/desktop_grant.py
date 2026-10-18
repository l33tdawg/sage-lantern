"""RAM-only presentation authority for a trusted native compositor guard.

Not a compositor, PIN verifier, private-API token or public HTTP endpoint.
The owning AuthBroker serializes every operation with its own admission gate.
Only a privileged native adapter may supply a kernel-verified session binding.
Browser status and user-written readiness files never establish this authority.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import time


class DesktopGrantError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class DesktopGrant:
    """One guard, one pending ticket, one active grant; RAM only.

    Tokens are stored as hashes. Pending possession must be acknowledged within
    30 seconds; active access requires a six-second presence lease and explicit
    activity inside the owner's idle policy. Polls/activation renew neither.
    Expiry, mode change, guard replacement or revoke fences in-flight checks.
    Binding verification and actual concealment belong to the native adapter.
    """

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.last_now = None
        self.guard = None
        self.binding = None
        self.mode = None
        self.generation = 0
        self.pending = None
        self.active = None
        self.last_activity = None
        self.presence_until = None

    def revoke(self):
        self.generation += 1
        self.pending = self.active = None
        self.last_activity = self.presence_until = None

    def close(self):
        self.revoke()
        self.guard = self.binding = self.mode = None

    def _now(self, idle_seconds):
        try:
            now = self.clock()
            if (type(now) not in (int, float) or not math.isfinite(now) or now < 0
                    or (self.last_now is not None and now < self.last_now)
                    or type(idle_seconds) not in (int, float)
                    or not math.isfinite(idle_seconds) or not 1 <= idle_seconds <= 3600):
                raise ValueError
            self.last_now = now
            if self.pending is not None and self.pending[1] <= now:
                self.pending = None
                self.generation += 1
            if self.active is not None and (
                    self.last_activity + idle_seconds <= now or self.presence_until <= now):
                self.revoke()
            return now
        except Exception:
            self.close()
            raise DesktopGrantError('unavailable') from None

    @staticmethod
    def _digest(token):
        if type(token) is not str or re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None:
            return None
        return hashlib.sha256(token.encode('ascii')).digest()

    def _require_guard(self, guard):
        if (self.guard is None or type(guard) is not str
                or re.fullmatch(r'[A-Za-z0-9_-]{43}', guard) is None
                or not secrets.compare_digest(self.guard, guard)):
            raise DesktopGrantError('stale_guard')

    def open(self, binding, mode, *, idle_seconds):
        if (type(binding) is not str or re.fullmatch(r'[A-Za-z0-9_:.-]{1,160}', binding) is None
                or mode not in ('kiosk', 'desktop') or type(mode) is not str):
            raise DesktopGrantError('invalid_request')
        self._now(idle_seconds)
        self.close()
        self.guard = secrets.token_urlsafe(32)
        self.binding = binding
        self.mode = mode
        return {'guard': self.guard}

    def verification_stamp(self, guard, *, idle_seconds):
        self._now(idle_seconds)
        self._require_guard(guard)
        if self.active is not None:
            raise DesktopGrantError('already_active')
        return self.guard, self.generation

    def issue(self, stamp, *, idle_seconds):
        now = self._now(idle_seconds)
        if stamp != (self.guard, self.generation) or self.guard is None:
            raise DesktopGrantError('unlock_cancelled')
        token = secrets.token_urlsafe(32)
        self.pending = (self._digest(token), now + 30)
        # Any earlier verification result must not replace this new ticket.
        self.generation += 1
        return {'token': token}

    def access(self, guard, token, *, action, idle_seconds):
        now = self._now(idle_seconds)
        self._require_guard(guard)
        digest = self._digest(token)
        if digest is None:
            return {'authorized': False}
        if action == 'activate' and self.pending is not None:
            if secrets.compare_digest(digest, self.pending[0]):
                self.active = digest
                self.pending = None
                self.last_activity = now
                self.presence_until = now + 6
                return {'authorized': True}
        authorized = self.active is not None and secrets.compare_digest(digest, self.active)
        if authorized:
            if action == 'presence':
                self.presence_until = now + 6
            elif action == 'activity':
                self.last_activity = now
            elif action not in ('activate', 'check'):
                raise DesktopGrantError('invalid_request')
        return {'authorized': authorized}

    def lock(self, guard, *, idle_seconds):
        self._now(idle_seconds)
        self._require_guard(guard)
        self.revoke()
        return {'locked': True}

    def synchronize(self, *, idle_seconds):
        """Return whether an active grant expired, so the broker can relock APIs."""
        was_active = self.active is not None
        self._now(idle_seconds)
        return was_active and self.active is None
