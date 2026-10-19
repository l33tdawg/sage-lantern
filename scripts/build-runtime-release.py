#!/usr/bin/env python3
"""Offline deterministic runtime wheel build from an allowlisted source snapshot.

Requires the exact installed build toolchain; never resolves dependencies or
copies caches, state, identities, or arbitrary repository files. Build into a
new output directory, compare independent builds, then promote reviewed bytes.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
EPOCH = "1788652800"
TOOLS = {"build": "1.4.0", "setuptools": "80.9.0", "wheel": "0.45.1"}


def source_bytes(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("linked source path")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 2 * 1024 * 1024:
        raise ValueError("unsafe source file")
    return path.read_bytes()


def build(output, root=ROOT):
    for tool, version in TOOLS.items():
        if importlib.metadata.version(tool) != version:
            raise ValueError("build tool version mismatch: " + tool)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("use a new output directory")
    project = root / "software/runtime"
    payloads = {"pyproject.toml": source_bytes(project / "pyproject.toml")}
    modules = sorted((project / "src/sage_lantern").glob("*.py"))
    # The runtime has grown past the original 128-module bound (129 as of the
    # 18 October 2026 policy-wheel rebuild); keep a bounded inventory check.
    if not 1 <= len(modules) <= 160:
        raise ValueError("invalid module inventory")
    for path in modules:
        payloads[str(path.relative_to(project))] = source_bytes(path)
    metadata = tomllib.loads(payloads["pyproject.toml"].decode())
    manifest = {"schema": "sage.lantern.runtime-release.v1", "version": metadata["project"]["version"],
                "source_date_epoch": int(EPOCH), "build_tools": TOOLS,
                "dependencies": metadata["project"]["dependencies"],
                "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    with tempfile.TemporaryDirectory(prefix="lantern-runtime-build-") as temporary:
        stage = Path(temporary)
        for name, data in payloads.items():
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o644)
        (stage / "src/sage_lantern/release-manifest.json").write_bytes(encoded)
        environment = dict(os.environ, SOURCE_DATE_EPOCH=EPOCH, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONPATH", None)
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(output), str(stage)],
                       check=True, env=environment, timeout=120)
    current_modules = {str(path.relative_to(project)) for path in (project / "src/sage_lantern").glob("*.py")}
    if current_modules != {name for name in payloads if name.startswith("src/")}:
        raise ValueError("source module set changed during build; do not promote artifact")
    for name, data in payloads.items():
        if source_bytes(project / name) != data:
            raise ValueError("source changed during build; do not promote artifact")
    print("Source snapshot: " + hashlib.sha256(encoded).hexdigest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
