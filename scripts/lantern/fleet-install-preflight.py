#!/usr/bin/env python3
"""Validate public unit binding for the legacy appliance installer; no writes."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


def load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit-id", choices=("unit-a", "unit-b", "unit-c", "unit-d", "unit-e", "unit-f"), required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--radio-evidence", required=True)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    config = load_helper("lantern_unit_config", "lantern-unit-config.py")
    hostname = load_helper("lantern_hostname", "configure-unit-a-hostname.py")
    try:
        metadata = config.safe_path(args.metadata, local_input=True)
        if metadata.name != "unit.env" or hostname.prepared_unit(metadata) != args.unit_id:
            raise ValueError
        radio = config.radio_inventory(args.radio_evidence)
        if radio["unit_id"] != args.unit_id:
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("Preflight rejected: missing or mismatched public unit evidence.", file=sys.stderr)
        return 2
    print(json.dumps({
        "schema": "sage.lantern.appliance-preflight.v1", "mode": "plan-only",
        "unit_id": args.unit_id, "hostname": hostname.hostname_for_unit(args.unit_id),
        "radio_public_fingerprint": radio["public_fingerprint"],
        "radio_usb_serial": radio["usb_serial"],
        "mutations_performed": False, "hardware_verified": False,
        "fresh_secure_install_supported": False,
        "unit_b_activation_supported": False,
        "blockers": ["Fresh secure setup and artifact containment require a separate installer.",
                     "Audio udev rules and remaining activation paths are not yet unit-independent.",
                     "A guarded installed appliance must use its reviewed update path, not this legacy installer."],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
