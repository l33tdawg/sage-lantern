"""Read-only, expiring root sleep-controller capability; never a preference."""

import json
import math
import os
from pathlib import Path
import stat
import time


STATUS_PATH = Path("/run/sage-lantern-sleep-status.json")
BOOT_PATH = Path("/proc/sys/kernel/random/boot_id")
MAX_AGE = 15.0


def available(*, path=STATUS_PATH, boot_path=BOOT_PATH, now=None, owner_uid=0):
    descriptor = None
    try:
        if path.resolve() != path:
            return False
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o644 or metadata.st_nlink != 1
                or metadata.st_size > 512):
            return False
        raw = os.read(descriptor, 513)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)
        current = time.clock_gettime(time.CLOCK_BOOTTIME) if now is None else now
        if (type(value) is not dict
                or set(value) != {"version", "available", "boot_id", "updated_boottime"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["available"] is not True
                or value["boot_id"] != boot_path.read_text().strip()
                or type(value["updated_boottime"]) not in (int, float)
                or not math.isfinite(value["updated_boottime"])
                or not 0 <= current - value["updated_boottime"] <= MAX_AGE):
            return False
        return True
    except (OSError, ValueError, TypeError, OverflowError, AttributeError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
