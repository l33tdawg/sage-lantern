#!/usr/bin/python3 -I -S
"""Isolated frozen-capture decision producer for the launch authority."""
import importlib.util
from pathlib import Path
import json
import sys


def _consumer():
    path = Path(__file__).with_name("frozen_gnome_consumer.py")
    spec = importlib.util.spec_from_file_location("capture_consumer_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if len(sys.argv) != 2:
        return 64
    decision = _consumer().review_capture(sys.argv[1])
    print(json.dumps({
        "evidence_consumable": decision.evidence_consumable,
        "reasons": list(decision.reasons),
        "policy_sha256": decision.policy_sha256,
        "analysis_view_digest": decision.analysis_view_digest,
        "unit_path": decision.unit_path,
        "capture_id": decision.capture_id,
        "generation": decision.generation,
        "runtime_authorized": decision.runtime_authorized,
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
