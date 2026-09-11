#!/usr/bin/env python3
"""Build an unpromoted offline SDK checkpoint; never modify upstream sources.

Only README, pyproject and direct sage_sdk Python modules enter staging.
Requires the runtime builder's pinned toolchain. Output is not API acceptance,
a signed provenance attestation, or authorization to promote the artifact.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("runtime_builder", ROOT / "scripts/build-runtime-release.py")
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)
VERSION = "11.19.10+lantern.20260906"
UPSTREAM_VERSION = "11.19.10"


def snapshot(source):
    modules = sorted((source / "src/sage_sdk").glob("*.py"))
    if not 1 <= len(modules) <= 128 or not (source / "src/sage_sdk/__init__.py") in modules:
        raise ValueError("invalid SDK module inventory")
    paths = [source / "pyproject.toml", source / "README.md", *modules]
    return {str(path.relative_to(source)): COMMON.source_bytes(path) for path in paths}


def staged_payloads(original):
    payloads = dict(original)
    project = payloads["pyproject.toml"].decode()
    metadata = tomllib.loads(project)
    if metadata["project"]["name"] != "sage-agent-sdk" or metadata["project"]["version"] != UPSTREAM_VERSION:
        raise ValueError("unexpected upstream SDK identity/version")
    if metadata["build-system"]["build-backend"] != "setuptools.build_meta":
        raise ValueError("unexpected SDK build backend")
    project, count = re.subn(r'(?m)^version = "11\.19\.10"$', 'version = "' + VERSION + '"', project)
    if count != 1:
        raise ValueError("ambiguous project version")
    project, count = re.subn(r'(?m)^requires = \["setuptools>=68\.0", "wheel"\]$',
                             'requires = ["setuptools==80.9.0", "wheel==0.45.1"]', project)
    if count != 1:
        raise ValueError("unreviewed build requirements")
    project += '\n[tool.setuptools.package-data]\nsage_sdk = ["release-manifest.json"]\n'
    tomllib.loads(project)
    payloads["pyproject.toml"] = project.encode()
    initializer, count = re.subn(rb'(?m)^__version__ = "11\.19\.10"$',
                                  ('__version__ = "' + VERSION + '"').encode(), payloads["src/sage_sdk/__init__.py"])
    if count != 1:
        raise ValueError("unexpected SDK package version")
    payloads["src/sage_sdk/__init__.py"] = initializer
    return payloads


def hashes(payloads):
    return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payloads.items())}


def verify_payload(wheel, payloads, encoded):
    expected = {name.removeprefix("src/"): data for name, data in payloads.items() if name.startswith("src/")}
    expected["sage_sdk/release-manifest.json"] = encoded
    dist = f"sage_agent_sdk-{VERSION}.dist-info/"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        allowed = set(expected) | {dist + name for name in ("METADATA", "WHEEL", "top_level.txt", "RECORD")}
        if len(names) != len(set(names)) or set(names) != allowed:
            raise ValueError("unexpected SDK wheel payload")
        if any(info.file_size > 2 * 1024 * 1024 for info in archive.infolist()):
            raise ValueError("oversize SDK wheel payload")
        for name, data in expected.items():
            if archive.read(name) != data:
                raise ValueError("SDK wheel content mismatch")
        if f"\nVersion: {VERSION}\n" not in archive.read(dist + "METADATA").decode():
            raise ValueError("wrong SDK wheel version")


def build(source, output):
    source = Path(source).absolute()
    output = Path(output).absolute()
    if source == output or source in output.parents:
        raise ValueError("output must be outside upstream source")
    for parent in (output, *output.parents):
        if parent.is_symlink():
            raise ValueError("linked output path")
    if output.exists():
        raise ValueError("use a new output directory")
    for tool, version in COMMON.TOOLS.items():
        if COMMON.importlib.metadata.version(tool) != version:
            raise ValueError("build tool version mismatch: " + tool)
    original = snapshot(source)
    payloads = staged_payloads(original)
    manifest = {"schema": "sage.lantern.sdk-release.v1", "version": VERSION,
                "upstream_version": UPSTREAM_VERSION, "source_date_epoch": int(COMMON.EPOCH),
                "build_tools": COMMON.TOOLS, "source_sha256": hashes(original),
                "staged_sha256": hashes(payloads)}
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    with tempfile.TemporaryDirectory(prefix="lantern-sdk-build-") as temporary:
        stage = Path(temporary)
        for name, data in payloads.items():
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o644)
        manifest_path = stage / "src/sage_sdk/release-manifest.json"
        manifest_path.write_bytes(encoded)
        manifest_path.chmod(0o644)
        environment = dict(os.environ, SOURCE_DATE_EPOCH=COMMON.EPOCH, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONPATH", None)
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(output), str(stage)],
                       check=True, env=environment, timeout=120)
    if snapshot(source) != original:
        raise ValueError("SDK source changed during build; do not promote")
    wheel = output / f"sage_agent_sdk-{VERSION}-py3-none-any.whl"
    if set(output.iterdir()) != {wheel}:
        raise ValueError("unexpected build outputs")
    verify_payload(wheel, payloads, encoded)
    print("Unpromoted SDK checkpoint SHA256: " + hashlib.sha256(wheel.read_bytes()).hexdigest())
    return wheel


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT.parent / "sage/sdk/python")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    build(arguments.source, arguments.output)
