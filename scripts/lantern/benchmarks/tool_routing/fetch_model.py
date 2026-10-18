#!/usr/bin/env python3
"""Download a pinned public MLX conversion for a local, synthetic comparison."""
import argparse
import hashlib
import json
from pathlib import Path

REPO = "mlx-community/functiongemma-270m-it-bf16"
REVISION = "bb327a9ad61044e1496a2bee2365a6b6a6684c72"
FILES = ["README.md", "config.json", "generation_config.json", "chat_template.jinja",
         "model.safetensors", "model.safetensors.index.json", "tokenizer.json",
         "tokenizer_config.json"]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from huggingface_hub import snapshot_download
    snapshot_download(REPO, revision=REVISION, local_dir=args.output,
                      allow_patterns=FILES, token=False, max_workers=3)
    provenance = {
        "repository": REPO, "revision": REVISION, "license": "gemma",
        "upstream": "google/functiongemma-270m-it",
        "upstream_revision_observed": "39eccb091651513a5dfb56892d3714c1b5b8276c",
        "upstream_revision_of_conversion": "not recorded by converter",
        "converter": "mlx-lm 0.28.4 (model card)",
        "files": {name: {"sha256": digest(args.output / name),
                          "bytes": (args.output / name).stat().st_size} for name in FILES},
        "scope": "local evaluation; no upstream-weight parity or RK3588 qualification",
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "revision": REVISION,
                      "total_bytes": sum(x["bytes"] for x in provenance["files"].values())}))


if __name__ == "__main__":
    main()
