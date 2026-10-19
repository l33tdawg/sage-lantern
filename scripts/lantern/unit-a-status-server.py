#!/usr/bin/env python3
"""Loopback-only status with explicit root-owned A/B public hardware bindings."""

from __future__ import annotations

import argparse
import glob
import http.client
import json
import os
import re
import socket
import stat
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


MESH_RUNTIME = Path("/etc/sage-lantern/mesh-runtime.json")


SCHEMA = "sage.lantern.local-status.v1"
UNIT_BINDING = None
ALLOWED_ORIGINS = {"http://127.0.0.1:3100", "http://localhost:3100"}
SERVICE_NAMES = {
    "admin": "sage-lantern-fleet-admin.service",
    "status": "sage-lantern-status.service",
    "llm": "sage-lantern-llm.service",
    "personal_sage": "sage-lantern-personal-sage.service",
    "runtime_gate": "sage-lantern-runtime.service",
    "ptt": "sage-lantern-ptt.service",
    "voice_loop": "sage-lantern-voice-loop.service",
}

SAGE_PATHS = {"/ready", "/v1/dashboard/health", "/v1/dashboard/chain/validators"}
SAGE_MAX_BODY = 262144
SAGE_INTERVAL = 10.0
SAGE_STALE_AFTER = 25.0


def _sage_integer(value: Any) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,16}", value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _sage_get(path: str) -> dict[str, Any]:
    if path not in SAGE_PATHS:
        raise ValueError("invalid_path")
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=2.0)
    deadline = time.monotonic() + 2.0
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("http_error")
        chunks = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(8192, SAGE_MAX_BODY + 1 - len(chunks)))
            chunks.extend(chunk)
            if len(chunks) > SAGE_MAX_BODY:
                raise ValueError("body_too_large")
            if not chunk:
                break
        value = json.loads(chunks)
        if not isinstance(value, dict):
            raise ValueError("invalid_payload")
        if value.get("error") not in (None, ""):
            raise ValueError("api_error")
        return value
    finally:
        connection.close()


def _sage_empty(reason: str = "not_observed") -> dict[str, Any]:
    return dict(
        available=False, observed_at=None, fresh=False, age_seconds=None,
        scope="node_projection", reason=reason, committed_memories=None,
        total_memories=None, block_height=None, block_time=None, chain_peers=None,
        mempool_txs=None, catching_up=None, idle=None, stuck=None, ready=None,
        validator_count=None, validator_count_scope="observed_roster",
        validator_roster_complete=None, validator_reason="not_observed",
        federation_enabled=None, validator_connections=None,
    )


class SageTelemetry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.value = _sage_empty()
        self.observed_monotonic: float | None = None
        self.stop = threading.Event()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            value = dict(self.value)
            observed = self.observed_monotonic
        if observed is not None:
            age = max(0.0, time.monotonic() - observed)
            value["age_seconds"] = round(age, 1)
            value["fresh"] = age <= SAGE_STALE_AFTER
            if not value["fresh"]:
                stale = _sage_empty("stale")
                stale.update(observed_at=value["observed_at"], age_seconds=value["age_seconds"])
                return stale
        return value

    def poll(self) -> None:
        value = _sage_empty("health_unavailable")
        observed = None
        try:
            health = _sage_get("/v1/dashboard/health")
            observed = time.monotonic()
            value["observed_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            memories = health.get("memories")
            projection = health.get("memory_projection")
            projection_ok = (
                isinstance(projection, dict) and projection.get("complete") is True
                and projection.get("partial") is False
                and projection.get("stale", False) is False
                and projection.get("refreshing", False) is False
            )
            if projection_ok and isinstance(memories, dict):
                total = _sage_integer(memories.get("total_memories"))
                statuses = memories.get("by_status")
                committed = _sage_integer(statuses.get("committed")) if isinstance(statuses, dict) else None
                if total == 0 and statuses == {}:
                    committed = 0
                if total is not None and committed is not None and committed > total:
                    total = committed = None
                value.update(total_memories=total, committed_memories=committed)
            chain = health.get("chain")
            if isinstance(chain, dict):
                for source, target in (("block_height", "block_height"), ("peers", "chain_peers"), ("mempool_txs", "mempool_txs")):
                    value[target] = _sage_integer(chain.get(source))
                for field in ("catching_up", "idle", "stuck"):
                    if type(chain.get(field)) is bool:
                        value[field] = chain[field]
                timestamp = chain.get("block_time")
                if isinstance(timestamp, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", timestamp):
                    try:
                        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        value["block_time"] = timestamp
                    except ValueError:
                        pass
            try:
                ready = _sage_get("/ready")
                value["ready"] = ready.get("status") == "ready" and ready.get("cometbft") is True
            except (OSError, ValueError, http.client.HTTPException):
                pass
            value["available"] = (
                value["ready"] is True and projection_ok
                and value["total_memories"] is not None and value["block_height"] is not None
            )
            if value["available"]:
                value.pop("reason", None)
            else:
                value["reason"] = "not_ready_or_incomplete_projection"
        except (OSError, ValueError, http.client.HTTPException):
            pass
        try:
            validators = _sage_get("/v1/dashboard/chain/validators")
            value["validator_count"] = _sage_integer(validators.get("count"))
            value["validator_reason"] = "roster_completeness_unknown" if value["validator_count"] is not None else "invalid_roster"
        except (OSError, ValueError, http.client.HTTPException):
            value["validator_reason"] = "roster_unavailable"
        with self.lock:
            self.value = value
            self.observed_monotonic = observed

    def run(self) -> None:
        while not self.stop.is_set():
            started = time.monotonic()
            self.poll()
            self.stop.wait(max(0.0, SAGE_INTERVAL - (time.monotonic() - started)))


SAGE_TELEMETRY = SageTelemetry()


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _command(*argv: str, timeout: float = 2.0) -> str:
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def _service_state(unit: str) -> str:
    value = _command("systemctl", "is-active", unit)
    return value if value in {"active", "inactive", "failed", "activating", "deactivating"} else "unknown"


def _temperature() -> int | None:
    values: list[int] = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        raw = _read(path)
        try:
            value = int(raw)
        except ValueError:
            continue
        if 0 < value < 150_000:
            values.append(value)
    return max(values) if values else None


def _touch() -> dict[str, Any]:
    """Direct-input property or a known panel name; presence is not touch acceptance."""
    for device in sorted(glob.glob("/sys/class/input/event*/device")):
        name = _read(device + "/name")
        try:
            direct = bool(int(_read(device + "/properties") or "0", 16) & 0x02)
        except ValueError:
            direct = False
        if direct or "WS170120" in name or "touch" in name.lower():
            return {"present": True, "detail": name}
    return {"present": False, "detail": "No touch controller detected"}


def _display() -> dict[str, Any]:
    connected: list[str] = []
    for path in glob.glob("/sys/class/drm/card*-*/status"):
        if _read(path) == "connected":
            connected.append(Path(path).parent.name)
    return {
        "present": bool(connected),
        "detail": ", ".join(connected) if connected else "No connected DRM display",
    }


def _network() -> dict[str, Any]:
    active = _command("nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")
    lines = [line for line in active.splitlines() if line]
    return {"present": bool(lines), "detail": lines[0].replace("\\:", ":") if lines else "Offline"}


def load_unit_binding(path: Path) -> dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate binding field')
            result[key] = value
        return result
    if not path.is_absolute() or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('unsafe binding path')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 16384:
            raise ValueError('unsafe binding file')
        value = json.loads(stream.read(16385), object_pairs_hook=unique)
    if value.get('schema') != 'sage.lantern.unit-plan.v1' or value.get('unit_id') not in {'unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'}:
        raise ValueError('invalid unit binding')
    radio = value.get('radio')
    if not (isinstance(radio, dict) and radio.get('present') is False):
        if not isinstance(radio, dict) or not isinstance(radio.get('stable_path'), str):
            raise ValueError('missing device binding')
        if not re.fullmatch(r'/dev/serial/by-id/[A-Za-z0-9_.:+-]+', radio['stable_path']):
            raise ValueError('invalid stable device path')
        if not isinstance(radio.get('usb_serial'), str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', radio['usb_serial']):
            raise ValueError('invalid USB serial')
    audio = value.get('audio')
    if isinstance(audio, dict) and audio.get('source') == 'platform':
        id_path = audio.get('id_path')
        if not isinstance(id_path, str) or not re.fullmatch(r'platform-[a-z0-9]+(-[a-z0-9]+)*', id_path):
            raise ValueError('invalid platform audio binding')
        if audio.get('stable_path') != '/dev/snd/by-path/' + id_path:
            raise ValueError('platform audio binding mismatch')
        if audio.get('platform_device') != id_path[len('platform-'):]:
            raise ValueError('platform audio device mismatch')
    else:
        if not isinstance(audio, dict) or not isinstance(audio.get('stable_path'), str):
            raise ValueError('missing device binding')
        if not re.fullmatch(r'/dev/snd/by-id/[A-Za-z0-9_.:+-]+', audio['stable_path']):
            raise ValueError('invalid stable device path')
        if not isinstance(audio.get('usb_serial'), str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', audio['usb_serial']):
            raise ValueError('invalid USB serial')
        if value['unit_id'] != 'unit-a' and 'SAGE-LANTERN-A-AUDIO' in audio['stable_path']:
            raise ValueError('wrong unit audio binding')
        if audio['usb_serial'] not in Path(audio['stable_path']).name:
            raise ValueError('audio binding mismatch')
    return value


def _radio(evidence_path: Path, binding=None) -> dict[str, Any]:
    if binding is None:
        return {'present': False, 'detail': 'Radio binding unavailable', 'link_verified': False}
    if binding['radio'].get('present') is False:
        return {'present': False, 'detail': 'Radio not fitted (explicitly declared absent)',
                'link_verified': False, 'name': binding['unit_id'] + ' radio'}
    path = Path(binding['radio']['stable_path'])
    state: dict[str, Any] = {
        "present": path.exists(),
        "detail": 'Configured USB path present; radio link unverified' if path.exists() else 'Configured USB companion missing',
        'link_verified': False,
        'name': binding['unit_id'] + ' radio',
    }
    try:
        value = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(value, dict) or value.get('unit_id') != binding['unit_id']:
        return state
    firmware = value.get("firmware", {})
    config = value.get("radio_profile", value.get("configuration", value.get("radio", {})))
    identity = value.get("radio_identity", value.get("identity", {}))
    if not all(isinstance(item, dict) for item in (firmware, config, identity)):
        return state
    state.update(
        name=config.get("name") or identity.get("name") or binding['unit_id'] + ' radio',
        frequency_mhz=config.get("frequency_mhz") or config.get("frequency"),
        firmware=firmware.get("version") or value.get("firmware_version"),
        metadata_source='public_inventory_not_live_measurement',
    )
    return state


def _audio_operational() -> bool:
    """Mesh units ask the broker; a local unit answers from its durable admission.

    `operational_ready()` is the mesh profile's stronger probe: it asks the
    broker to confirm the retained capture path is live. A unit with no mesh
    runtime has no broker to ask, and that must not read as "audio is broken" —
    a missing optional capability never gates the console. The durable audio
    admission (`audio_readiness --admit`, a real capture re-commissioned from
    the reviewed profile) is the local answer; the broker stays authoritative
    wherever its runtime is actually installed.
    """
    try:
        from sage_lantern.audio_readiness import admission_verified, operational_ready
        if MESH_RUNTIME.exists() or MESH_RUNTIME.is_symlink():
            return operational_ready() is True
        return admission_verified() is True
    except Exception:
        return False


def _audio(binding=None) -> dict[str, Any]:
    audio = binding['audio'] if binding is not None else None
    usb = audio is not None and audio.get('source', 'usb') == 'usb'
    present = audio is not None and Path(audio['stable_path']).exists()
    ready = present and _audio_operational()
    return {
        "present": present,
        "ready": ready,
        "detail": ('Audio service ready; capture is checked when recording' if ready else
                   ('Configured audio USB path present; audio service unavailable' if usb else
                    'Configured platform capture device present; audio service unavailable') if present else
                   ('Configured audio USB path missing' if usb else
                    'Configured platform capture device missing') if audio else 'Audio binding unavailable'),
        "serial": audio.get('usb_serial') if usb else None,
        'capture_verified': False,
        "ptt_ready": False,
    }


def _ptt() -> dict[str, Any]:
    state_path = Path("/run/sage-lantern-ptt/state.json")
    verified = Path("/var/lib/sage-lantern/PTT_VERIFIED").exists()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "present": False,
            "detail": "GPIO adapter unavailable",
            "state": "unknown",
            "event_count": 0,
            "verified": False,
        }
    current = state.get("state") if state.get("state") in {"up", "down"} else "unknown"
    return {
        "present": True,
        "detail": f"Radxa pin 27 adapter online · button {current}",
        "state": current,
        "event_count": int(state.get("event_count", 0)),
        "verified": verified,
    }


def _voice_correction_ready(capability='correction_ready') -> bool:
    connection = http.client.HTTPConnection('127.0.0.1', 3197, timeout=0.5)
    try:
        connection.request('GET', '/health')
        response = connection.getresponse()
        raw = response.read(1025)
        if response.status != 200 or len(raw) > 1024:
            return False
        value = json.loads(raw)
        return type(value) is dict and value.get(capability) is True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _voice(*, probe_capabilities: bool = True) -> dict[str, Any]:
    state_path = Path("/run/sage-lantern-voice/state.json")
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
        if type(value) is not dict:
            raise ValueError('invalid_voice_state')
    except (OSError, ValueError):
        return {"phase": "unavailable", "transcript": "", "response": "", "detail": "Local voice service is not running",
                'correction_ready': False}
    return {
        "phase": value.get("phase", "unknown"),
        "session_context": value.get("session_context"),
        "session_id": value.get("session_id"),
        "transcript": value.get("transcript", ""),
        "response": value.get("response", ""),
        "follow_up_questions": value.get("follow_up_questions", []) if value.get('phase') == 'complete' else [],
        "detail": value.get("detail", ""),
        "timings_ms": value.get("timings_ms", {}),
        "touch_ready": value.get("touch_ready") is True,
        'correction_ready': probe_capabilities and value.get('correction_ready') is True and _voice_correction_ready(),
        'review_ready': probe_capabilities and value.get('review_ready') is True and _voice_correction_ready('review_ready'),
        'review_id': value.get('review_id') if value.get('phase') == 'review' else None,
        "photo_ready": value.get("photo_ready") is True,
        "has_photo": value.get("has_photo") is True,
        "progress_stage": value.get("progress_stage") if value.get("progress_stage") in {"starting", "transcribing", "recalling", "photo_analysis", "generating", "answering", "speaking"} else None,
        "input_source": value.get("input_source"),
    }


def collect_voice_status() -> dict[str, Any]:
    # Content only: readiness remains on /status, where its live capability
    # checks run. No hardware scan, subprocess, SAGE poll or controller probe.
    voice = _voice(probe_capabilities=False)
    fields = ('phase', 'session_context', 'session_id', 'transcript', 'response',
              'follow_up_questions', 'detail', 'timings_ms', 'review_id',
              'has_photo', 'progress_stage', 'input_source')
    return {
        'schema': 'sage.lantern.voice-status.v1',
        'observed_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
        'voice': {key: voice[key] for key in fields if key in voice},
    }


def collect_status(*, evidence_path: Path = Path("/usr/local/share/sage-lantern/meshcore-radio-public.json")) -> dict[str, Any]:
    usage = os.statvfs("/")
    uptime_parts = _read("/proc/uptime").split()
    provisioned = Path("/var/lib/sage-lantern/PROVISIONED").exists()
    local_voice_ready = Path("/var/lib/sage-lantern/LOCAL_VOICE_READY").exists()
    audio = _audio(UNIT_BINDING)
    ptt = _ptt()
    audio["ptt_ready"] = bool(audio["present"] and ptt["present"] and ptt["verified"])
    return {
        "schema": SCHEMA,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "unit_id": UNIT_BINDING['unit_id'] if UNIT_BINDING else None,
        "hostname": socket.gethostname(),
        "uptime_seconds": int(float(uptime_parts[0])) if uptime_parts else 0,
        "temperature_millicelsius": _temperature(),
        "storage_free_bytes": usage.f_bavail * usage.f_frsize,
        "storage_total_bytes": usage.f_blocks * usage.f_frsize,
        "provisioned": provisioned,
        "local_voice_ready": local_voice_ready,
        "network": _network(),
        "display": _display(),
        "touch": _touch(),
        "radio": _radio(evidence_path, UNIT_BINDING),
        "audio": audio,
        "ptt": ptt,
        "voice": _voice(),
        "sage": SAGE_TELEMETRY.snapshot(),
        "services": {name: _service_state(unit) for name, unit in SERVICE_NAMES.items()},
    }


def _session_authorized(token: str | None) -> bool:
    try:
        from sage_lantern.device_access import authorized
        return authorized(token) is True
    except Exception:
        return False


def _context_required() -> bool:
    try:
        from sage_lantern.device_access import lock_enabled
        return lock_enabled()
    except Exception:
        return True


def _session_context(token):
    try:
        from sage_lantern.device_access import session_context
        return session_context(token)
    except Exception:
        return None


def _fence_voice(voice, context):
    from sage_lantern.session_context import SessionContext
    try:
        if type(voice) is dict and SessionContext.parse(voice.get("session_context")) == context:
            return voice
    except ValueError:
        pass
    return {"phase": "unavailable", "transcript": "", "response": "", "detail": "",
            "touch_ready": False, "correction_ready": False, "review_ready": False,
            "photo_ready": False, "has_photo": False}


class StatusHandler(BaseHTTPRequestHandler):
    server_version = "SAGELanternStatus/1"

    def trusted_host(self):
        hosts = self.headers.get_all("Host", [])
        return len(hosts) == 1 and hosts[0] in {"127.0.0.1:3199", "localhost:3199"}

    def session_authorized(self):
        values = self.headers.get_all("X-Lantern-Session", [])
        return len(values) <= 1 and _session_authorized(values[0] if values else None)

    def do_OPTIONS(self):
        origins = self.headers.get_all("Origin", [])
        methods = self.headers.get_all("Access-Control-Request-Method", [])
        requested = self.headers.get_all("Access-Control-Request-Headers", [])
        if (not self.trusted_host() or self.path not in {"/status", "/voice"} or len(origins) != 1 or origins[0] not in ALLOWED_ORIGINS
                or methods != ["GET"] or len(requested) != 1
                or requested[0].strip().lower() != "x-lantern-session"
                or len(self.headers.get_all("X-Lantern-Session", [])) > 1):
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origins[0])
        self.send_header("Access-Control-Allow-Methods", "GET")
        self.send_header("Access-Control-Allow-Headers", "X-Lantern-Session")
        self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self.trusted_host():
            self.send_error(403)
            return
        if self.path not in {"/status", "/voice", "/health"}:
            self.send_error(404)
            return
        if len(self.headers.get_all("X-Lantern-Session", [])) > 1:
            self.send_error(400)
            return
        protected = self.path in {"/status", "/voice"}
        if protected and not self.session_authorized():
            self.send_error(403)
            return
        token = self.headers.get("X-Lantern-Session")
        context = None
        if protected and _context_required():
            context = _session_context(token)
            if context is None:
                self.send_error(403)
                return
        payload = ({"ok": True} if self.path == "/health" else
                   collect_voice_status() if self.path == "/voice" else collect_status())
        if context is not None:
            payload["voice"] = _fence_voice(payload.get("voice"), context)
            payload["session_context"] = context.to_dict()
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if protected and (not self.session_authorized()
                or (context is not None and _session_context(token) != context)):
            self.send_error(403)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        if protected and (not self.session_authorized()
                or (context is not None and _session_context(token) != context)):
            self.close_connection = True
            return
        if self.path == "/status":
            print(json.dumps({"event": "audio_status", "present": payload["audio"]["present"],
                              "ready": payload["audio"]["ready"]}), flush=True)
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.client_address[0]} {format % args}")


def main() -> int:
    global UNIT_BINDING
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3199)
    parser.add_argument('--unit-config', type=Path, default=Path('/etc/sage-lantern/appliance-bindings.json'))
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("status server must remain loopback-only")
    try:
        UNIT_BINDING = load_unit_binding(args.unit_config)
    except (OSError, ValueError, TypeError, AttributeError):
        parser.error('valid root-owned per-unit status configuration required')
    server = ThreadingHTTPServer((args.host, args.port), StatusHandler)
    worker = threading.Thread(target=SAGE_TELEMETRY.run, name="sage-telemetry", daemon=True)
    worker.start()
    try:
        server.serve_forever()
    finally:
        SAGE_TELEMETRY.stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
