#!/usr/bin/env python3
"""Run two temporary SAGE API/vault fixtures; no live ledger, device or RF IO."""

import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sage-source", type=Path, default=ROOT.parent / "sage")
    arguments = parser.parse_args()
    source = arguments.sage_source.resolve(strict=True)
    fixture = ROOT / "scripts/tests/fixtures/lantern_workflow_interop_test.go.txt"
    script = ROOT / "scripts/tests/fixtures/mesh_live_api_interop.py"
    if not all((source / "api/rest" / name).is_file() for name in
               ("workflow_journal_handler_test.go", "private_media_handler_test.go")):
        raise ValueError("matching SAGE workflow and media test fixtures required")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("SAGE_", "LANTERN_"))}
    environment.update(GOPROXY="off", GOSUMDB="off", GOENV="off", GOFLAGS="", GOTOOLCHAIN="local",
                       PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=os.pathsep.join((str(ROOT / "software/runtime/src"),
                                                  str(source / "sdk/python/src"))),
                       LANTERN_INTEROP_PYTHON=sys.executable, LANTERN_INTEROP_SCRIPT=str(script),
                       LANTERN_INTEROP_ARCH=platform.machine())
    with tempfile.TemporaryDirectory(prefix="lantern-workflow-interop-") as temporary:
        overlay = Path(temporary) / "overlay.json"
        overlay.write_text(json.dumps({"Replace": {
            str(source / "api/rest/lantern_workflow_interop_test.go"): str(fixture)}}))
        subprocess.run(["go", "test", "-overlay", str(overlay), "./api/rest", "-run",
                        "^TestLanternWorkflowInterop$", "-count=1", "-v"], cwd=source,
                       env=environment, check=True, timeout=240)


if __name__ == "__main__":
    main()
