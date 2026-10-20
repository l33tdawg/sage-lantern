#!/usr/bin/env python3
"""Seal an already-staged native tree with the exact closure manifest."""
import argparse
import importlib.util
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-uid", type=int)
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parents[2] / (
        "software/security/desktop_loader_candidate/native_bundle.py")
    spec = importlib.util.spec_from_file_location(
        "native_bundle_stage", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    root = args.root
    if not root.is_absolute() or (root / module.MANIFEST).exists():
        print("bundle_manifest_already_present", file=sys.stderr)
        return 1
    try:
        value = module.build(
            root, args.source_commit, expected_uid=args.expected_uid)
        module.write(root, value)
        module.verify(root, expected_uid=args.expected_uid)
    except (module.BundleRefused, OSError) as error:
        print(f"bundle_refused: {error}", file=sys.stderr)
        return 1
    print(value["source_commit"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
