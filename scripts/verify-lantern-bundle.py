#!/usr/bin/env python3
"""Fail closed when a cached Lantern bundle artifact differs from its lock."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "software" / "stack.lock.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    payload = json.loads(LOCK.read_text(encoding="utf-8"))
    failures: list[str] = []
    for artifact in payload["files"]:
        path = ROOT / artifact["path"]
        if not path.is_file():
            failures.append(f"missing: {artifact['path']}")
            continue
        if path.stat().st_size != artifact["bytes"]:
            failures.append(f"wrong size: {artifact['path']}")
            continue
        if digest(path) != artifact["sha256"]:
            failures.append(f"wrong sha256: {artifact['path']}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"verified {len(payload['files'])} locked Lantern artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
