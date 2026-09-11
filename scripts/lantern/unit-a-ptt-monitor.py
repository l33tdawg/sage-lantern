#!/usr/bin/env python3
"""Translate one active-low Radxa GPIO input into semantic PTT state."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


SCHEMA = "sage.lantern.ptt-state.v1"
DEBOUNCE_SECONDS = 0.025


def write_state(destination: Path, *, state: str, sequence: int, event_count: int) -> None:
    payload = {
        "schema": SCHEMA,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "state": state,
        "sequence": sequence,
        "event_count": event_count,
    }
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}")
    temporary.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o640)
    os.replace(temporary, destination)


def monitor(*, chip: str, line: int, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    sequence = 0
    event_count = 0
    current_state = "up"
    last_event = 0.0
    write_state(destination, state=current_state, sequence=sequence, event_count=event_count)
    print(f"ptt.ready chip={chip} line={line} physical_pin=27 state=up", flush=True)

    command = [
        "gpiomon",
        "--active-low",
        "--bias=pull-up",
        "--line-buffered",
        "--format=%e",
        chip,
        str(line),
    ]
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as process:
        assert process.stdout is not None
        for raw in process.stdout:
            now = time.monotonic()
            if now - last_event < DEBOUNCE_SECONDS:
                continue
            value = raw.strip()
            if value not in {"0", "1"}:
                continue
            next_state = "down" if value == "1" else "up"
            if next_state == current_state:
                continue
            last_event = now
            current_state = next_state
            sequence += 1
            event_count += 1
            write_state(
                destination,
                state=current_state,
                sequence=sequence,
                event_count=event_count,
            )
            print(f"ptt.{current_state} sequence={sequence}", flush=True)
        return process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chip", default="gpiochip4")
    parser.add_argument("--line", type=int, default=22)
    parser.add_argument("--state", type=Path, default=Path("/run/sage-lantern-ptt/state.json"))
    args = parser.parse_args()
    return monitor(chip=args.chip, line=args.line, destination=args.state)


if __name__ == "__main__":
    raise SystemExit(main())
