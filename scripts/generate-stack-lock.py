#!/usr/bin/env python3
"""Generate the deterministic integrity manifest for the Lantern beta bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "software" / "stack.lock.json"

COMPONENTS = {
    "sage": {
        "version": "11.23.27",
        "source": "https://github.com/l33tdawg/sage/releases/tag/v11.23.27",
        "license": "Apache-2.0",
    },
    "llama.cpp": {
        "version": "b9637",
        "source": "https://github.com/ggml-org/llama.cpp/releases/tag/b9637",
        "license": "MIT",
    },
    "whisper.cpp": {
        "version": "1.8.6",
        "source": "https://github.com/ggml-org/whisper.cpp/releases/tag/v1.8.6",
        "license": "MIT",
    },
    "piper-tts": {
        "version": "1.4.2",
        "source": "https://github.com/OHF-Voice/piper1-gpl/releases/tag/v1.4.2",
        "license": "GPL-3.0-or-later",
    },
    "numpy": {
        "version": "1.26.4",
        "source": "https://pypi.org/project/numpy/1.26.4/",
        "license": "BSD-3-Clause",
    },
    "qwen3.5-4b": {
        "version": "Q4_K_M",
        "source": "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/tree/e87f176479d0855a907a41277aca2f8ee7a09523",
        "license": "Apache-2.0",
    },
    "lantern-runtime": {
        "version": "0.2.0",
        "source": "software/runtime",
        "license": "Apache-2.0",
    },
    "sage-agent-sdk": {
        "version": "11.19.10+lantern.20260906",
        "source": "../sage/sdk/python (local staged release; embedded source manifest)",
        "license": "Apache-2.0",
    },
    "cryptography": {
        "version": "46.0.3",
        "source": "https://pypi.org/project/cryptography/46.0.3/",
        "license": "Apache-2.0 OR BSD-3-Clause",
    },
    "pyyaml": {
        "version": "6.0.2",
        "source": "https://pypi.org/project/PyYAML/6.0.2/",
        "license": "MIT",
    },
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    roots = [
        ROOT / "software" / "bundle",
        ROOT / "software" / "cache" / "downloads",
        ROOT / "software" / "cache" / "models",
        ROOT / "software" / "cache" / "sources",
    ]
    files = []
    for directory in roots:
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            if any(part.startswith(("tmp-", ".tmp-")) for part in path.relative_to(directory).parts):
                continue
            files.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "bytes": path.stat().st_size,
                    "sha256": digest(path),
                }
            )
    payload = {
        "schema": "sage.lantern.stack-lock.v1",
        "target": {"os": "linux", "architecture": "arm64", "python": "3.11"},
        "components": COMPONENTS,
        "files": files,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(files)} files")


if __name__ == "__main__":
    main()
