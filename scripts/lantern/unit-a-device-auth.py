#!/opt/sage-lantern/venv/bin/python
"""Unit A loopback auth broker; other services must enforce its decisions.

One process owns one DeviceLock. Profile setup configures only the display name,
not device provisioning or federation readiness. No PIN bootstrap, discovery,
proxy, or root-cookie endpoints exist. Other services must gate sensitive work.

Optional --drain-report-dir is a pre-provisioned service-owned 0700 directory
under trusted parents (no hostile same-UID writers/ACLs). Root captures systemd's
INVOCATION_ID before stop and reads <id>.json only after verifying the fenced
service cgroup is empty. Require exact version 1, matching instance_id, closing,
drained, local_revoked all true and upstream_cleanup == "confirmed". Files are
0600, regular, single-link, at most 512 bytes; root must validate ownership and
parents with no-follow reads. Bind the instance to the root transition journal;
never accept the newest file or let a restart clear an earlier unknown outcome.
No receipt proves vault-key unloading or cleanup of other services/sessions.
Missing report, forced kill or timeout is not positive cleanup evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import signal
import socket
import stat
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

from sage_lantern.device_lock import DeviceLock, DeviceLockError
from sage_lantern.desktop_grant import DesktopGrant, DesktopGrantError
from sage_lantern.desktop_control import DesktopControlServer
from sage_lantern.power_settings import PowerSettingsStore, PowerSettingsError, PowerRevisionConflict, validate_policy
from sage_lantern.power_settings import LockSettingsStore, validate_lock_policy
from sage_lantern.device_profile import (
    MAX_REVISION, DeviceProfileStore, ProfileError, ProfileRevisionConflict,
    normalize_display_name,
)
from sage_lantern.vault_setup import RootFileSigner, VaultSetup, VaultSetupError
from sage_lantern.suspend_capability import available as suspend_available
from sage_lantern.mesh_contact_forwarding import ROUTES as CONTACT_ROUTES, OwnerContactForwarder, validate_payload as validate_contact_payload


HOSTS = frozenset({"127.0.0.1:3196", "localhost:3196"})
ORIGINS = frozenset({"http://127.0.0.1:3100", "http://localhost:3100"})
ROUTES = {"/state": "GET", "/unlock": "POST", "/lock": "POST", "/authorize": "POST", "/activity": "POST"}
ROUTES["/access-epoch"] = "GET"
ROUTES["/owner-contact-snapshot"] = "POST"
ROUTES["/session-context"] = "POST"
ROUTES.update({path: "POST" for path in ("/activate", "/presence", "/cancel")})
PROFILE_ROUTES = frozenset({"/profile/get", "/profile/setup", "/profile/rename"})
ROUTES.update({path: "POST" for path in PROFILE_ROUTES})
SETUP_ROUTES = frozenset({"/setup/status", "/setup/enable", "/setup/recovery", "/setup/confirm"})
ROUTES.update({path: "POST" for path in SETUP_ROUTES})
POWER_ROUTES = frozenset({"/settings/power/get", "/settings/power/set"})
ROUTES.update({path: "POST" for path in POWER_ROUTES})
LOCK_ROUTES = frozenset({"/settings/lock/get", "/settings/lock/set"})
ROUTES.update({path: "POST" for path in LOCK_ROUTES})
ROUTES["/power-policy"] = "GET"
ROUTES["/sleep-ready"] = "GET"
ROUTES.update({path: "POST" for path in CONTACT_ROUTES})
MAX_BODY = 512
MAX_WORKERS = 8
READ_TIMEOUT = 2.0
DRAIN_TIMEOUT = 5.0


class DrainReport:
    """One exclusive instance record, atomically replaced after bounded drain."""

    def __init__(self, directory, instance_id):
        self.descriptor = None
        if type(instance_id) is not str or re.fullmatch(r"[0-9a-f]{32}", instance_id) is None or instance_id == "0" * 32:
            raise ValueError("drain_report_unavailable")
        self.instance_id = instance_id
        self.name = instance_id + ".json"
        try:
            if not directory.is_absolute() or directory.resolve(strict=True) != directory:
                raise ValueError
            for parent in directory.parents:
                metadata = parent.lstat()
                if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in (0, os.geteuid())
                        or (metadata.st_mode & 0o022 and not (metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX))):
                    raise ValueError
            self.descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            metadata = os.fstat(self.descriptor)
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise ValueError
            descriptor = os.open(self.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=self.descriptor)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self._body(False, False, False, False))
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(self.descriptor)
        except Exception:
            self.close()
            raise ValueError("drain_report_unavailable") from None

    def _body(self, closing, drained, local_revoked, confirmed):
        return json.dumps(dict(version=1, instance_id=self.instance_id, closing=closing,
                               drained=drained, local_revoked=local_revoked,
                               upstream_cleanup="confirmed" if confirmed else "unknown"),
                          separators=(",", ":")).encode("ascii")

    def finish(self, state):
        temporary = ".drain-" + uuid.uuid4().hex
        try:
            metadata = os.stat(self.name, dir_fd=self.descriptor, follow_symlinks=False)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1):
                raise ValueError
            body = self._body(state["closing"] is True, state["drained"] is True,
                              state["local_revoked"] is True, state["upstream_cleanup"] == "confirmed")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=self.descriptor)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.name, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor)
            os.fsync(self.descriptor)
        except Exception:
            raise ValueError("drain_report_unavailable") from None
        finally:
            try:
                os.unlink(temporary, dir_fd=self.descriptor)
            except FileNotFoundError:
                pass

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def initialization_allowed(marker):
    """Nonsensitive root-owned 0644 flag at a deployment-trusted path."""
    if marker is None:
        return False
    try:
        if not marker.is_absolute() or marker.resolve(strict=True) != marker:
            return False
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(descriptor)
            return (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
                    and stat.S_IMODE(metadata.st_mode) == 0o644 and metadata.st_nlink == 1
                    and metadata.st_size == len(b"allow-vault-initialize\n")
                    and os.read(descriptor, 64) == b"allow-vault-initialize\n")
        finally:
            os.close(descriptor)
    except (OSError, RuntimeError):
        return False


class RequestError(Exception):
    def __init__(self, status, code):
        self.status = status
        self.code = code


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class AuthBroker:
    """Serialize lock decisions, never hold this gate over PIN verification."""

    def __init__(self, device, profile=None, vault=None, power=None, capability=None,
                 *, desktop_clock=time.monotonic):
        self.device = device
        self.profile = profile
        self.vault = vault
        self.power = power
        self.capability = capability
        self.gate = threading.RLock()
        self.upstream_gate = threading.Lock()
        self.generation = 0
        self.closing = False
        self.local_revoked = False
        self.cleanup_unknown = False
        self.upstream_active = 0
        self._device_epoch = None
        self._observed_epoch = os.urandom(16).hex()
        self._session_context_key = os.urandom(32)
        self._revocation_listeners = []
        self._dependent_drain_probes = []
        self.contacts = OwnerContactForwarder(self)
        self.desktop = DesktopGrant(clock=desktop_clock)
        self._browser_locked = True

    def subscribe_revocation(self, listener, *, drained=None):
        """Trusted in-process lifecycle hook; never exposed as an HTTP route.

        Callbacks run under the auth gate and must be bounded/nonblocking. An
        action coordinator shares this gate so a lock cannot race its sends or
        local publication. Registration alone never enables private actions.
        """
        if not callable(listener) or (drained is not None and not callable(drained)):
            raise ValueError('invalid_revocation_listener')
        with self.gate:
            self.admit()
            if len(self._revocation_listeners) >= 8:
                raise ValueError('revocation_listener_capacity')
            self._revocation_listeners.append(listener)
            if drained is not None:
                self._dependent_drain_probes.append(drained)

    def _dependents_drained(self):
        try:
            return all(probe() is True for probe in self._dependent_drain_probes)
        except Exception:
            return False

    def _revoke_dependents(self):
        failed = False
        for listener in self._revocation_listeners:
            try:
                listener()
            except Exception:
                failed = True
        if failed:
            # A listener failure cannot leave dispatch and auth admission open.
            self.closing = True
            self.cleanup_unknown = True
            self.device.lock()
            raise DeviceLockError('unavailable')

    def _lock_device(self):
        with self.gate:
            self._revoke_dependents()
            return self.device.lock()

    def admit(self):
        with self.gate:
            if self.closing:
                raise DeviceLockError("unavailable")

    def _begin_upstream(self):
        with self.gate:
            if self.closing:
                raise DeviceLockError("unavailable")
            if not self.upstream_gate.acquire(blocking=False):
                return False
            self.upstream_active += 1
            return True

    def _end_upstream(self):
        with self.gate:
            try:
                confirmed = self.device.upstream_cleanup_confirmed() is True
                if self.vault is not None:
                    confirmed = confirmed and self.vault.cleanup_confirmed is True
            except Exception:
                confirmed = False
            self.cleanup_unknown = self.cleanup_unknown or not confirmed
            self.upstream_active -= 1
            self.upstream_gate.release()

    def drain_state(self, workers_drained):
        with self.gate:
            drained = (workers_drained is True and self.upstream_active == 0
                       and self._dependents_drained())
            if not drained:
                self.cleanup_unknown = True
            return dict(closing=self.closing, drained=drained, local_revoked=self.local_revoked,
                        upstream_cleanup="confirmed" if drained and not self.cleanup_unknown else "unknown")

    def sleep_ready(self):
        """Preflight only: readiness neither closes admission nor proves drain."""
        with self.gate:
            ready = False
            if not self.closing and self.upstream_active == 0 and not self.cleanup_unknown:
                try:
                    ready = self.device.upstream_cleanup_confirmed() is True
                    if self.vault is not None:
                        ready = ready and self.vault.cleanup_confirmed is True
                    ready = ready and self._dependents_drained()
                    if ready and self.desktop.guard is not None:
                        self._desktop_sync()
                        ready = self.desktop.active is None and self.desktop.pending is None
                except Exception:
                    ready = False
            return {"ready": ready}

    def state(self):
        with self.gate:
            self.admit()
            try:
                state = self.device.status()
            except Exception:
                self.desktop.close()
                raise
            if not isinstance(state, dict) or set(state) != {"locked"} or type(state["locked"]) is not bool:
                self.desktop.close()
                raise DeviceLockError("unavailable")
            if state['locked'] and not self._browser_locked:
                self.desktop.revoke()
                self._revoke_dependents()
            self._browser_locked = state['locked']
            if self._revocation_listeners:
                self.access_epoch()
            return state

    def _desktop_sync(self):
        self.admit()
        if self.cleanup_unknown:
            self.desktop.close()
            self._lock_device()
            raise DeviceLockError('unavailable')
        idle = self.device.session_seconds()
        self.state()
        try:
            if self.desktop.synchronize(idle_seconds=idle):
                self.generation += 1
                self._lock_device()
                self._browser_locked = True
        except DesktopGrantError:
            self.generation += 1
            self._lock_device()
            self._browser_locked = True
            raise
        return idle

    def desktop_open(self, binding, mode):
        """Privileged adapter only; never wire this to the public HTTP handler."""
        with self.gate:
            idle = self._desktop_sync()
            result = self.desktop.open(binding, mode, idle_seconds=idle)
            self.generation += 1
            self._lock_device()
            self._browser_locked = True
            return result

    def desktop_unlock(self, guard, pin):
        with self.gate:
            idle = self._desktop_sync()
            stamp = self.desktop.verification_stamp(guard, idle_seconds=idle)
            generation = self.generation
        if not self._begin_upstream():
            self.device.reserve_attempt()
            raise DeviceLockError('verification_busy')
        try:
            if self.vault is not None:
                status = self._setup_status(self.vault.status())
                if not status['enabled'] or not status['recovery_backup_confirmed']:
                    raise RequestError(409, 'setup_required')
            with self.gate:
                self.admit()
                if generation != self.generation or stamp != self.desktop.verification_stamp(
                        guard, idle_seconds=self.device.session_seconds()):
                    raise DeviceLockError('unlock_cancelled')
            if self.device.verify_pin(pin) is not True:
                raise DeviceLockError('verification_failed')
        finally:
            self._end_upstream()
        with self.gate:
            idle = self._desktop_sync()
            if generation != self.generation:
                raise DeviceLockError('unlock_cancelled')
            return self.desktop.issue(stamp, idle_seconds=idle)

    def desktop_access(self, guard, token, action):
        if action not in ('activate', 'check', 'presence', 'activity'):
            raise DesktopGrantError('invalid_request')
        with self.gate:
            idle = self._desktop_sync()
            return self.desktop.access(guard, token, action=action, idle_seconds=idle)

    def desktop_lock(self, guard):
        with self.gate:
            idle = self._desktop_sync()
            result = self.desktop.lock(guard, idle_seconds=idle)
            self.generation += 1
            self._lock_device()
            self._browser_locked = True
            return result

    def access_epoch(self):
        with self.gate:
            self.admit()
            value = self.device.access_epoch()
            if (type(value) is not dict or set(value) != {"schema", "locked", "epoch"}
                    or value["schema"] != "sage.lantern.access-epoch.v1" or type(value["locked"]) is not bool
                    or type(value["epoch"]) is not str or re.fullmatch(r"[0-9a-f]{32}", value["epoch"]) is None):
                self.desktop.close()
                self._lock_device()
                raise DeviceLockError("unavailable")
            if value["epoch"] != self._device_epoch:
                self._revoke_dependents()
                self._observed_epoch = os.urandom(16).hex()
                self._device_epoch = value["epoch"]
            return {**value, "epoch": self._observed_epoch}

    def power_policy(self):
        self.admit()
        if self.power is None:
            raise RequestError(503, "power_settings_unavailable")
        return self._power_capability(self.power.load())

    def session_context(self, token):
        """Live token-bound context, distinct from every voice publication.

        This is not a human-name/SAGE-identity assertion or private-action
        consent. Token and secret never leave the broker. All reads and lock
        decisions share the existing gate; no presence/idle lease is renewed.
        """
        with self.gate:
            self.require_token(token)
            before = self.access_epoch()
            if before["locked"] is not False:
                raise RequestError(401, "unauthorized")
            ref = hmac.new(self._session_context_key,
                (before["epoch"] + ":" + token).encode("ascii"), hashlib.sha256).hexdigest()
            self.require_token(token)
            if self.access_epoch() != before:
                raise RequestError(401, "unauthorized")
            return dict(schema="sage.lantern.session-context.v1", epoch=before["epoch"], session_ref=ref)

    def owner_contact_snapshot(self, token):
        with self.gate:
            self.require_token(token)
            before = self.access_epoch()
            if before['locked'] is not False:
                raise RequestError(401, 'unauthorized')
        profile = self.profile_request('/profile/get', {'token': token})
        with self.gate:
            self.require_token(token)
            if self.access_epoch() != before:
                raise RequestError(401, 'unauthorized')
            return dict(schema='sage.lantern.owner-contact-snapshot.v1', epoch=before['epoch'], profile=profile)

    def lock_settings_request(self, path, payload):
        with self.gate:
            token = payload["token"]
            self.require_token(token)
            if self.power is None:
                raise RequestError(503, "power_settings_unavailable")
            settings = LockSettingsStore(self.power.directory)
            if path.endswith("/set"):
                try:
                    result = settings.update(expected_revision=payload["expected_revision"],
                                             lock_seconds=payload["lock_seconds"],
                                             authorize=lambda: self.device.authorized(token))
                    self.device.set_session_seconds(result["lock_seconds"])
                except PowerRevisionConflict:
                    raise
                except Exception:
                    self.desktop.close()
                    self._lock_device()
                    self.closing = True
                    raise
            else:
                result = settings.load()
            self.require_token(token)
            return result

    def _power_capability(self, policy):
        policy = dict(policy)
        try:
            policy["deep_sleep_available"] = self.capability is not None and self.capability() is True
        except Exception:
            policy["deep_sleep_available"] = False
        return policy

    def power_request(self, path, payload):
        token = payload["token"]
        self.require_token(token)
        if self.power is None:
            raise RequestError(503, "power_settings_unavailable")
        if path == "/settings/power/set":
            result = self.power.update(
                expected_revision=payload["expected_revision"],
                display_off_seconds=payload["display_off_seconds"],
                deep_sleep_seconds=payload["deep_sleep_seconds"],
                authorize=lambda: self.device.authorized(token),
            )
        else:
            result = self.power.load()
        self.require_token(token)
        return self._power_capability(result)

    def authorize(self, token):
        with self.gate:
            self.admit()
            authorized = self.device.authorized(token)
            if self._revocation_listeners:
                self.access_epoch()
            if type(authorized) is not bool:
                raise DeviceLockError("unavailable")
            return {"authorized": authorized}

    def activity(self, token):
        with self.gate:
            self.admit()
            authorized = self.device.touch(token)
            if self._revocation_listeners:
                self.access_epoch()
            if type(authorized) is not bool:
                raise DeviceLockError("unavailable")
            return {"authorized": authorized}

    def presence(self, token):
        with self.gate:
            self.admit()
            authorized = self.device.presence(token)
            if self._revocation_listeners:
                self.access_epoch()
            if type(authorized) is not bool:
                raise DeviceLockError("unavailable")
            return {"authorized": authorized}

    def activate(self, token):
        with self.gate:
            if self.closing:
                raise DeviceLockError("unavailable")
            was_active = self.device.authorized(token)
            activated = self.device.activate(token)
            if type(activated) is not bool or type(was_active) is not bool:
                self.desktop.close()
                self._lock_device()
                self.generation += 1
                raise DeviceLockError("unavailable")
            if activated and not was_active:
                self._revoke_dependents()
                self.generation += 1
                self._browser_locked = False
            return {"authorized": activated}

    def cancel(self, token):
        with self.gate:
            if self.closing:
                raise DeviceLockError("unavailable")
            was_active = self.device.authorized(token)
            cancelled = self.device.revoke(token)
            if self._revocation_listeners:
                self.access_epoch()
            if type(cancelled) is not bool or type(was_active) is not bool:
                raise DeviceLockError("unavailable")
            if was_active and cancelled:
                self._revoke_dependents()
                self.generation += 1
                self.desktop.revoke()
                self._browser_locked = True
            return {"cancelled": cancelled}

    def require_token(self, token):
        self.admit()
        authorized = self.device.authorized(token)
        # Observe lazy presence/idle expiry even when the caller was refused.
        # An invalid token does not revoke another live epoch.
        if self._revocation_listeners:
            self.access_epoch()
        if type(authorized) is not bool:
            raise DeviceLockError("unavailable")
        if not authorized:
            raise RequestError(401, "unauthorized")

    def profile_request(self, path, payload):
        """A raced write may commit; revoked callers never receive profile data.

        Re-fetch the revision after re-authentication rather than assuming a
        missing response rolled a profile mutation back. Profile IO never holds
        the auth gate, so locking cannot be stalled by profile storage.
        """
        token = payload["token"]
        self.require_token(token)
        error = None
        result = None
        try:
            if self.profile is None:
                raise RequestError(503, "profile_unavailable")
            if path == "/profile/get":
                profile = self.profile.load()
            else:
                try:
                    name = normalize_display_name(payload["display_name"])
                except ProfileError:
                    raise RequestError(400, "invalid_profile") from None
                self.require_token(token)
                method = self.profile.complete_setup if path == "/profile/setup" else self.profile.rename
                profile = method(name, expected_revision=payload["expected_revision"])
            value = profile.to_dict()
            fields = {"version", "configured", "display_name", "public_discoverable", "revision", "setup_required"}
            if (not isinstance(value, dict) or set(value) != fields
                    or type(value["version"]) is not int or value["version"] != 1
                    or type(value["configured"]) is not bool
                    or type(value["public_discoverable"]) is not bool
                    or type(value["revision"]) is not int or not 0 <= value["revision"] <= MAX_REVISION
                    or type(value["setup_required"]) is not bool
                    or value["setup_required"] == value["configured"]
                    or not isinstance(value["display_name"], str)):
                raise RequestError(503, "profile_unavailable")
            if value["configured"]:
                if value["revision"] == 0 or normalize_display_name(value["display_name"]) != value["display_name"]:
                    raise RequestError(503, "profile_unavailable")
            elif value["display_name"] != "" or value["revision"] != 0:
                raise RequestError(503, "profile_unavailable")
            result = value
        except ProfileRevisionConflict:
            error = RequestError(409, "revision_conflict")
        except RequestError as caught:
            error = caught
        except Exception:
            error = RequestError(503, "profile_unavailable")
        self.require_token(token)
        if error is not None:
            raise error
        return result

    def unlock(self, pin):
        with self.gate:
            if self.closing:
                raise DeviceLockError("unavailable")
            generation = self.generation
        if not self._begin_upstream():
            self.device.reserve_attempt()
            raise DeviceLockError("verification_busy")
        try:
            if self.vault is not None:
                status = self._setup_status(self.vault.status())
                if not status["enabled"] or not status["recovery_backup_confirmed"]:
                    raise RequestError(409, "setup_required")
            return self._unlock(pin, generation)
        finally:
            self._end_upstream()

    def _unlock(self, pin, generation):
        with self.gate:
            if generation != self.generation:
                raise DeviceLockError("unlock_cancelled")
        token = self.device.unlock(pin)
        with self.gate:
            if generation != self.generation:
                self.device.revoke(token)
                raise DeviceLockError("unlock_cancelled")
            if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
                self.desktop.close()
                self._lock_device()
                self.generation += 1
                raise DeviceLockError("unavailable")
            if self.device.pending(token) is not True:
                raise DeviceLockError("unlock_cancelled")
            return {"token": token}

    @staticmethod
    def _setup_status(value):
        fields = {"enabled", "recovery_backup_confirmed", "has_existing_vault", "can_initialize"}
        if (type(value) is not dict or set(value) != fields or any(type(value[field]) is not bool for field in fields)
                or (value["enabled"] and not value["has_existing_vault"])
                or (value["recovery_backup_confirmed"] and not value["enabled"])
                or (value["can_initialize"] and (value["enabled"] or value["has_existing_vault"]))):
            raise RequestError(503, "setup_unavailable")
        return value

    def setup_request(self, path, payload):
        with self.gate:
            self.admit()
            generation = self.generation
        if self.vault is None:
            raise RequestError(503, "setup_unavailable")
        if path != "/setup/status":
            self.device.reserve_attempt()
        if not self._begin_upstream():
            raise DeviceLockError("verification_busy")
        try:
            with self.gate:
                if self.closing or generation != self.generation:
                    raise DeviceLockError("unlock_cancelled")
            if path == "/setup/status":
                result = self._setup_status(self.vault.status())
            elif path == "/setup/enable":
                result = self.vault.enable(payload["pin"], payload["confirmation"])
            elif path == "/setup/recovery":
                result = self.vault.recovery(payload["pin"])
            else:
                result = self._setup_status(self.vault.confirm(payload["pin"]))
                if not result["enabled"] or not result["recovery_backup_confirmed"]:
                    raise RequestError(503, "setup_unavailable")
                result = {"recovery_backup_confirmed": True}
            if path in {"/setup/enable", "/setup/recovery"}:
                if not isinstance(result, dict):
                    raise RequestError(503, "setup_unavailable")
                key = result.get("recovery_key")
                try:
                    if type(key) is not str or len(key) != 44:
                        raise ValueError
                    decoded = base64.b64decode(key, validate=True)
                    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != key:
                        raise ValueError
                except (ValueError, UnicodeError):
                    raise RequestError(503, "setup_unavailable") from None
                result = {"recovery_key": key}
            with self.gate:
                if generation != self.generation:
                    raise DeviceLockError("unlock_cancelled")
            return result
        finally:
            self._end_upstream()

    def lock(self, token):
        with self.gate:
            if self.closing:
                raise DeviceLockError("unavailable")
            if self.device.pending(token) is True:
                self.device.revoke(token)
                return self.state()
            authorized = self.device.authorized(token)
            if authorized is not True:
                if type(authorized) is not bool:
                    raise DeviceLockError("unavailable")
                if self.state()["locked"]:
                    return {"locked": True}
                raise RequestError(401, "unauthorized")
            self.generation += 1
            self.desktop.revoke()
            self._lock_device()
            return self.state()

    def shutdown(self):
        with self.gate:
            self.desktop.close()
            if self.closing:
                return
            self.closing = True
            self.generation += 1
            try:
                result = self._lock_device()
                self.local_revoked = type(result) is dict and set(result) == {"locked"} and result["locked"] is True
            except Exception:
                self.local_revoked = False
                self.cleanup_unknown = True


class AuthHandler(BaseHTTPRequestHandler):
    server_version = "LanternAuth"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(READ_TIMEOUT)
        self.cors_origin = None
        super().setup()

    def log_message(self, format, *args):
        pass

    def _reply(self, status, payload=None, *, preflight=False):
        self.close_connection = True
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.send_header("Vary", "Origin")
        if self.cors_origin is not None:
            self.send_header("Access-Control-Allow-Origin", self.cors_origin)
            if preflight:
                self.send_header("Access-Control-Allow-Methods", ROUTES[self.path])
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Lantern-Auth")
                self.send_header("Access-Control-Max-Age", "60")
        if status == 429:
            self.send_header("Retry-After", "60")
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        self._reply(code, {"error": "invalid_request"})

    def _header(self, name, *, required=False):
        values = self.headers.get_all(name, [])
        if len(values) > 1 or (required and not values):
            raise RequestError(400, "invalid_request")
        return values[0] if values else None

    def _validate(self):
        if self.path != "/sleep-ready" or self.command != "GET":
            self.server.broker.admit()
        if self.client_address[0] != "127.0.0.1":
            raise RequestError(403, "forbidden")
        if self._header("Host", required=True) not in HOSTS:
            raise RequestError(403, "forbidden")
        if self.path not in ROUTES:
            raise RequestError(404, "not_found")
        if sum(len(name) + len(value) for name, value in self.headers.items()) > 8192:
            raise RequestError(431, "invalid_request")
        if any(name.lower() in {"forwarded", "proxy-authorization", "cookie", "transfer-encoding", "expect"}
               or name.lower().startswith("x-forwarded-") for name in self.headers):
            raise RequestError(400, "invalid_request")
        origin = self._header("Origin")
        if ((self.path == "/access-epoch" and (origin is not None or self.command != "GET"))
                or (self.path == "/owner-contact-snapshot" and (origin is not None or self.command != "POST"))):
            raise RequestError(403, "forbidden")
        if origin is not None:
            if origin not in ORIGINS:
                raise RequestError(403, "forbidden")
            self.cors_origin = origin
        elif self.path not in {"/state", "/authorize", "/power-policy", "/sleep-ready", "/access-epoch", "/owner-contact-snapshot", "/session-context"} or self.command == "OPTIONS":
            raise RequestError(403, "forbidden")
        if self.command not in {ROUTES[self.path], "OPTIONS"}:
            raise RequestError(405, "method_not_allowed")
        control = self._header("X-Lantern-Auth")
        if self.command == "POST" and origin is not None and control != "control":
            raise RequestError(403, "forbidden")
        length = self._header("Content-Length", required=self.command == "POST")
        if length is not None and re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", length) is None:
            raise RequestError(400, "invalid_request")
        if length is not None and int(length) > MAX_BODY:
            raise RequestError(413, "body_too_large")
        if self.command != "POST" and length not in (None, "0"):
            raise RequestError(400, "invalid_request")
        if self.command == "POST" and self._header("Content-Type", required=True) != "application/json":
            raise RequestError(415, "invalid_request")
        return int(length or "0")

    def _payload(self, length):
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError(400, "invalid_request")
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise RequestError(400, "invalid_request") from None
        if self.path in CONTACT_ROUTES:
            try:
                validate_contact_payload(self.path, payload)
            except Exception:
                raise RequestError(400, "invalid_request") from None
            return payload
        if self.path in LOCK_ROUTES:
            fields = {"token"} if self.path.endswith("/get") else {"token", "expected_revision", "lock_seconds"}
            if type(payload) is not dict or set(payload) != fields or type(payload["token"]) is not str:
                raise RequestError(400, "invalid_request")
            if self.path.endswith("/set"):
                try:
                    validate_lock_policy(dict(revision=payload["expected_revision"], lock_seconds=payload["lock_seconds"]))
                except PowerSettingsError:
                    raise RequestError(400, "invalid_request") from None
            return payload
        if self.path in POWER_ROUTES:
            fields = {"token"} if self.path.endswith("/get") else {
                "token", "expected_revision", "display_off_seconds", "deep_sleep_seconds",
            }
            if type(payload) is not dict or set(payload) != fields or type(payload["token"]) is not str:
                raise RequestError(400, "invalid_request")
            if self.path.endswith("/set"):
                try:
                    validate_policy(dict(revision=payload["expected_revision"],
                                         display_off_seconds=payload["display_off_seconds"],
                                         deep_sleep_seconds=payload["deep_sleep_seconds"], deep_sleep_available=False))
                except PowerSettingsError:
                    raise RequestError(400, "invalid_request") from None
            return payload
        if self.path in SETUP_ROUTES:
            fields = set() if self.path == "/setup/status" else {"pin", "confirmation"} if self.path == "/setup/enable" else {"pin"}
            if type(payload) is not dict or set(payload) != fields or any(type(value) is not str for value in payload.values()):
                raise RequestError(400, "invalid_request")
            return payload
        if self.path in PROFILE_ROUTES:
            fields = {"token"} if self.path == "/profile/get" else {"token", "display_name", "expected_revision"}
            if (not isinstance(payload, dict) or set(payload) != fields or not isinstance(payload["token"], str)):
                raise RequestError(400, "invalid_request")
            if self.path != "/profile/get" and (not isinstance(payload["display_name"], str)
                    or type(payload["expected_revision"]) is not int or not 0 <= payload["expected_revision"] <= MAX_REVISION):
                raise RequestError(400, "invalid_request")
            return payload
        field = "pin" if self.path == "/unlock" else "token"
        if not isinstance(payload, dict) or set(payload) != {field} or not isinstance(payload[field], str):
            raise RequestError(400, "invalid_request")
        return payload[field]

    def _dispatch(self):
        try:
            length = self._validate()
            if self.command == "OPTIONS":
                method = self._header("Access-Control-Request-Method", required=True)
                headers = self._header("Access-Control-Request-Headers")
                requested = [] if headers is None else [value.strip().lower() for value in headers.split(",")]
                expected = {"content-type", "x-lantern-auth"} if method == "POST" else set()
                if method != ROUTES[self.path] or set(requested) != expected or len(requested) != len(set(requested)):
                    raise RequestError(403, "forbidden")
                self._reply(204, preflight=True)
                return
            if self.command == "GET":
                if self.path == "/access-epoch":
                    result = self.server.broker.access_epoch()
                elif self.path == "/sleep-ready":
                    result = self.server.broker.sleep_ready()
                else:
                    result = self.server.broker.power_policy() if self.path == "/power-policy" else self.server.broker.state()
            else:
                value = self._payload(length)
                if self.path == "/session-context":
                    result = self.server.broker.session_context(value)
                elif self.path == "/owner-contact-snapshot":
                    result = self.server.broker.owner_contact_snapshot(value)
                elif self.path in CONTACT_ROUTES:
                    self.server.broker.require_token(value["token"])
                    try:
                        result = self.server.broker.contacts.handle(self.path, value)
                    except Exception:
                        raise RequestError(503, "contact_unavailable_reconcile_if_uncertain") from None
                    self.server.broker.require_token(value["token"])
                elif self.path in LOCK_ROUTES:
                    result = self.server.broker.lock_settings_request(self.path, value)
                    self.server.broker.require_token(value["token"])
                elif self.path in POWER_ROUTES:
                    result = self.server.broker.power_request(self.path, value)
                    self.server.broker.require_token(value["token"])
                elif self.path in SETUP_ROUTES:
                    result = self.server.broker.setup_request(self.path, value)
                elif self.path in PROFILE_ROUTES:
                    result = self.server.broker.profile_request(self.path, value)
                    self.server.broker.require_token(value["token"])
                elif self.path == "/unlock":
                    result = self.server.broker.unlock(value)
                elif self.path == "/lock":
                    result = self.server.broker.lock(value)
                elif self.path == "/activity":
                    result = self.server.broker.activity(value)
                elif self.path == "/activate":
                    result = self.server.broker.activate(value)
                elif self.path == "/presence":
                    result = self.server.broker.presence(value)
                elif self.path == "/cancel":
                    result = self.server.broker.cancel(value)
                else:
                    result = self.server.broker.authorize(value)
            if self.path == "/sleep-ready":
                result = self.server.broker.sleep_ready()
            else:
                self.server.broker.admit()
            self._reply(200, result)
        except RequestError as error:
            self._reply(error.status, {"error": error.code})
        except PowerRevisionConflict:
            self._reply(409, {"error": "revision_conflict"})
        except PowerSettingsError:
            self._reply(503, {"error": "power_settings_unavailable"})
        except DeviceLockError as error:
            statuses = {"invalid_pin": 400, "rate_limited": 429, "verification_busy": 409,
                        "verification_failed": 401, "unlock_cancelled": 409, "unavailable": 503}
            code = error.code if error.code in statuses else "unavailable"
            self._reply(statuses[code], {"error": code})
        except VaultSetupError as error:
            if error.code in {"pin_must_be_8_to_12_ascii_digits", "pin_confirmation_mismatch"}:
                self._reply(400, {"error": "invalid_pin"})
            elif error.code == "vault_authorization_or_pin_rejected":
                self._reply(401, {"error": "verification_failed"})
            elif error.code in {"vault_already_exists", "vault_not_enabled"}:
                self._reply(409, {"error": "setup_state_changed"})
            elif error.code == "vault_enable_not_allowed":
                self._reply(403, {"error": "setup_not_allowed"})
            elif error.code in {"vault_enable_uncertain_reconcile_required", "session_revocation_failed_reconcile_required", "vault_confirmation_unverified"}:
                self._reply(503, {"error": "setup_uncertain_reconcile_required"})
            else:
                self._reply(503, {"error": "setup_unavailable"})
        except TimeoutError:
            self._reply(408, {"error": "request_timeout"})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self._reply(503, {"error": "unavailable"})

    do_GET = _dispatch
    do_POST = _dispatch
    do_OPTIONS = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch
    do_HEAD = _dispatch


class DeviceAuthServer(ThreadingMixIn, HTTPServer):
    daemon_threads = False
    block_on_close = False
    request_queue_size = MAX_WORKERS
    allow_reuse_address = True

    def __init__(self, device, *, profile=None, vault=None, power=None, capability=None, bind_and_activate=True):
        self.broker = AuthBroker(device, profile, vault, power, capability)
        self.slots = threading.BoundedSemaphore(MAX_WORKERS)
        self.requests_lock = threading.Lock()
        self.active_requests = set()
        self.requests_changed = threading.Condition(self.requests_lock)
        super().__init__(("127.0.0.1", 3196), AuthHandler, bind_and_activate=bind_and_activate)
        self.timeout = 0.25

    def verify_request(self, request, client_address):
        return client_address[0] == "127.0.0.1"

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(READ_TIMEOUT)
                body = b'{"error":"unavailable"}'
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        with self.requests_lock:
            if self.broker.closing:
                self.slots.release()
                self.shutdown_request(request)
                return
            self.active_requests.add(request)
        try:
            super().process_request(request, client_address)
        except Exception:
            with self.requests_lock:
                self.active_requests.discard(request)
                self.requests_changed.notify_all()
            self.slots.release()
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.requests_lock:
                self.active_requests.discard(request)
                self.requests_changed.notify_all()
            self.slots.release()

    def handle_error(self, request, client_address):
        pass

    def server_close(self):
        deadline = time.monotonic() + DRAIN_TIMEOUT
        self.broker.shutdown()
        with self.requests_lock:
            requests = tuple(self.active_requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().server_close()
        with self.requests_changed:
            while self.active_requests:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.requests_changed.wait(remaining)
            return self.broker.drain_state(not self.active_requests)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unit A private loopback device authentication")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--idle-seconds", type=int, default=300, help="Legacy argument; persisted lock settings take precedence")
    parser.add_argument("--profile-dir", type=Path)
    parser.add_argument("--operator-key", type=Path)
    parser.add_argument("--setup-marker", type=Path)
    parser.add_argument("--drain-report-dir", type=Path)
    parser.add_argument("--desktop-socket", type=Path,
                        help="Opt-in root-peer-only native control socket in a private runtime directory")
    args = parser.parse_args(argv)
    if not args.state_dir.is_absolute():
        parser.error("--state-dir must be an absolute private directory")
    if not 30 <= args.idle_seconds <= 3600:
        parser.error("--idle-seconds must be between 30 and 3600")
    profile_dir = args.profile_dir if args.profile_dir is not None else args.state_dir / "profile"
    if not profile_dir.is_absolute() or profile_dir.resolve() != profile_dir:
        parser.error("--profile-dir must be an absolute path without symlinks")
    if args.operator_key is not None and not args.operator_key.is_absolute():
        parser.error("--operator-key must be an absolute configured current-Root seed path")
    if args.setup_marker is not None and (args.operator_key is None or not args.setup_marker.is_absolute()):
        parser.error("--setup-marker requires --operator-key and an absolute marker path")
    device = None
    server = None
    stopped = threading.Event()
    previous = {}
    report = None
    desktop_server = None
    try:
        device = DeviceLock(args.state_dir, session_seconds=300)
        lock_policy = LockSettingsStore(args.state_dir / "power").load()
        device.set_session_seconds(lock_policy["lock_seconds"])
        if args.drain_report_dir is not None:
            report = DrainReport(args.drain_report_dir, os.environ.get("INVOCATION_ID"))
        vault = None if args.operator_key is None else VaultSetup(RootFileSigner(args.operator_key),
                        enable_allowed=lambda: initialization_allowed(args.setup_marker))
        server = DeviceAuthServer(device, profile=DeviceProfileStore(profile_dir), vault=vault,
                                 power=PowerSettingsStore(args.state_dir / "power"), capability=suspend_available)
        if args.desktop_socket is not None:
            desktop_server = DesktopControlServer(args.desktop_socket, server.broker)
            desktop_server.start()
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda received, frame: stopped.set())
        while not stopped.is_set():
            server.handle_request()
    except Exception:
        parser.exit(1, "Device authentication unavailable\n")
    finally:
        try:
            if server is not None:
                server.broker.shutdown()
                if desktop_server is not None:
                    if not desktop_server.server_close():
                        with server.broker.gate:
                            server.broker.cleanup_unknown = True
                drained = server.server_close()
                if report is not None:
                    report.finish(drained)
        finally:
            if device is not None:
                device.close()
            if report is not None:
                report.close()
            for signum, handler in previous.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    main()
