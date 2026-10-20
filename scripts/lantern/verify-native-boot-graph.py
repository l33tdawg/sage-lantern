#!/usr/bin/env python3
"""Verify the source boot graph for the native admission barrier."""
import argparse
import json
from pathlib import Path
import sys


EARLY = "sage-lantern-early-boot-admission.service"
GUARDED = "sage-lantern-guarded-session.service"


def _unit(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise ValueError(f"unit_unavailable:{path.name}") from None
    section = None
    values = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section is None or "=" not in line:
            raise ValueError(f"unit_line_invalid:{path.name}")
        key, value = line.split("=", 1)
        if not key or key != key.strip():
            raise ValueError(f"unit_key_invalid:{path.name}")
        values.setdefault((section, key), []).append(value.strip())
    return values


def _one(values, section, key, name):
    items = values.get((section, key), [])
    if len(items) != 1 or not items[0]:
        raise ValueError(f"unit_field_invalid:{name}:{key}")
    return items[0]


def verify(unit_dir):
    unit_dir = Path(unit_dir)
    early = _unit(unit_dir / EARLY)
    guarded = _unit(unit_dir / GUARDED)
    before = _one(early, "Unit", "Before", EARLY).split()
    required = {"display-manager.service", "getty.target", "ssh.service", "ssh.socket"}
    if not required <= set(before):
        raise ValueError("admission_before_entrypoints_missing")
    if _one(early, "Service", "Type", EARLY) != "oneshot":
        raise ValueError("admission_not_oneshot")
    if _one(early, "Service", "StateDirectory", EARLY) != "sage-lantern-early-boot":
        raise ValueError("admission_state_directory_invalid")
    if "/usr/local/libexec/early_boot_admission.py" not in (
            _one(early, "Service", "ExecStart", EARLY).split()):
        raise ValueError("admission_exec_invalid")
    if _one(guarded, "Unit", "ConditionPathExists", GUARDED) != (
            "/etc/sage-lantern/desktop-pin-activation.json"):
        raise ValueError("guarded_activation_condition_invalid")
    guarded_requires = set(_one(guarded, "Unit", "Requires", GUARDED).split())
    guarded_after = set(_one(guarded, "Unit", "After", GUARDED).split())
    if EARLY not in guarded_requires or EARLY not in guarded_after:
        raise ValueError("guarded_session_does_not_require_admission")
    return {
        "schema": "sage.lantern.native-boot-graph-verification.v1",
        "source_verified": True,
        "installed_on_target": False,
        "runtime_authorized": False,
        "early_unit": EARLY,
        "guarded_unit": GUARDED,
        "before": sorted(before),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify(args.unit_dir), sort_keys=True))
    except (ValueError, OSError) as error:
        print(f"boot_graph_refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
