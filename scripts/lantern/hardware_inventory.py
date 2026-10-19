"""Read-only hardware inventory for a Lantern unit.

Collects board, CPU, memory, storage, USB, serial, I2C and display state from
procfs and sysfs. It opens no device, writes nothing and makes no network
request. Serial numbers are omitted unless --include-serials is passed.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "sage.lantern.hardware-inventory.v1"
BLOCK_SKIP = ("loop", "ram", "zram", "dm-", "sr")


def _read(root, path, default=None):
    try:
        return (Path(root) / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def _lines(root, path):
    text = _read(root, path, "")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _nul_list(root, path):
    text = _read(root, path, "")
    return [item for item in text.split("\x00") if item]


def _int(text, default=0):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


def _storage(root):
    result = []
    base = Path(root) / "sys/block"
    for device in sorted(base.glob("*")) if base.is_dir() else []:
        if device.name.startswith(BLOCK_SKIP):
            continue
        sectors = _int(_read(root, f"sys/block/{device.name}/size", "0"))
        model = (_read(root, f"sys/block/{device.name}/device/model", "") or "").strip()
        partitions = sorted(
            path.name for path in device.glob("*")
            if (path / "partition").is_file())
        result.append({
            "name": device.name,
            "bytes": sectors * 512,
            "model": model or None,
            "removable": _int(_read(
                root, f"sys/block/{device.name}/removable", "0")) == 1,
            "partitions": partitions,
        })
    return result


def _usb(root):
    result = []
    base = Path(root) / "sys/bus/usb/devices"
    for device in sorted(base.glob("*")) if base.is_dir() else []:
        vendor = (_read(root, f"sys/bus/usb/devices/{device.name}/idVendor", "")
                  or "").strip()
        if not vendor:
            continue
        result.append({
            "id": device.name,
            "vendor": vendor,
            "product": (_read(
                root, f"sys/bus/usb/devices/{device.name}/idProduct", "")
                or "").strip() or None,
            "name": (_read(
                root, f"sys/bus/usb/devices/{device.name}/product", "")
                or "").strip() or None,
            "manufacturer": (_read(
                root, f"sys/bus/usb/devices/{device.name}/manufacturer", "")
                or "").strip() or None,
        })
    return result


def _displays(root):
    result = []
    base = Path(root) / "sys/class/drm"
    for entry in sorted(base.glob("card*-*")) if base.is_dir() else []:
        status = (_read(root, f"sys/class/drm/{entry.name}/status", "")
                  or "").strip()
        if not status:
            continue
        result.append({
            "connector": entry.name,
            "status": status,
            "enabled": (_read(
                root, f"sys/class/drm/{entry.name}/enabled", "")
                or "").strip() or None,
        })
    return result


def _devices(root, pattern):
    return sorted(path.name for path in (Path(root) / "dev").glob(pattern))


def collect(root="/", include_serials=False):
    root = Path(root)
    model = (_read(root, "proc/device-tree/model", "") or "").replace("\x00", "")
    serial = None
    if include_serials:
        serial = ((_read(root, "proc/device-tree/serial-number", "") or "")
                  .replace("\x00", "").strip() or None)
    cpuinfo = _read(root, "proc/cpuinfo", "") or ""
    processors = len(re.findall(r"^processor\s*:", cpuinfo, re.MULTILINE))
    meminfo = _read(root, "proc/meminfo", "") or ""
    match = re.search(r"^MemTotal:\s*(\d+)\s*kB", meminfo, re.MULTILINE)
    return {
        "schema": SCHEMA,
        "collected_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"),
        "host": {
            "hostname": (_read(
                root, "proc/sys/kernel/hostname", "") or "").strip() or None,
            "model": model.strip() or None,
            "compatible": _nul_list(root, "proc/device-tree/compatible"),
            "serial": serial,
        },
        "cpu": {"processors": processors},
        "memory": {"mem_total_kb": int(match.group(1)) if match else None},
        "storage": _storage(root),
        "usb": _usb(root),
        "serial_ports": _devices(root, "ttyS*"),
        "i2c_buses": _devices(root, "i2c-*"),
        "displays": _displays(root),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/",
                        help="Root to read; default is the live system")
    parser.add_argument("--include-serials", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(collect(args.root, args.include_serials), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
