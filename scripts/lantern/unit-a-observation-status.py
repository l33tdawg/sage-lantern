#!/usr/bin/env python3
"""Write a small allow-listed Unit A status document for the Fleet Device Agent."""

from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path


SERVICES = {
    "fleet_admin": "sage-lantern-fleet-admin.service",
    "local_status": "sage-lantern-status.service",
    "llm": "sage-lantern-llm.service",
    "personal_sage": "sage-lantern-personal-sage.service",
    "runtime_gate": "sage-lantern-runtime.service",
}


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def service_state(unit: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    value = result.stdout.strip()
    return value if value in {"active", "inactive", "failed", "activating", "deactivating"} else "unknown"


def temperature() -> int | None:
    values: list[int] = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            value = int(read_text(path))
        except ValueError:
            continue
        if 0 < value < 150_000:
            values.append(value)
    return max(values) if values else None


def write_status(destination: Path) -> None:
    usage = os.statvfs("/")
    uptime = read_text("/proc/uptime").split()
    payload = {
        "status": {
            "unit_id": "unit-a",
            "hostname": socket.gethostname(),
            "provisioned": Path("/var/lib/sage-lantern/PROVISIONED").exists(),
        },
        "services": {name: service_state(unit) for name, unit in SERVICES.items()},
        "hardware": {
            "temperature_millicelsius": temperature(),
            "display_connected": any(
                read_text(path) == "connected" for path in glob.glob("/sys/class/drm/card*-*/status")
            ),
            "touch_present": any(
                "touch" in read_text(path).lower() or "WS170120" in read_text(path)
                for path in glob.glob("/sys/class/input/event*/device/name")
            ),
            "radio_present": Path("/dev/sage-lantern-radio").exists(),
            "audio_present": bool(
                glob.glob(
                    "/dev/snd/by-id/usb-SAGE_Commons_SAGE_Lantern_Audio_"
                    "SAGE-LANTERN-A-AUDIO-*"
                )
            )
            or Path("/dev/sage-lantern-audio-flash").exists(),
        },
        "resources": {
            "uptime_seconds": int(float(uptime[0])) if uptime else 0,
            "storage_free_bytes": usage.f_bavail * usage.f_frsize,
            "storage_total_bytes": usage.f_blocks * usage.f_frsize,
        },
    }

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(prefix=".sanitized-status-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/run/sage-lantern/sanitized-status.json"),
    )
    args = parser.parse_args()
    write_status(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
