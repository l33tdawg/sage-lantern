#!/usr/bin/env python3
"""Verify real private voice dependencies without retaining microphone audio."""

import argparse
import json
from pathlib import Path
import runpy
import sys
import tempfile
import time
from urllib.parse import urlsplit


def require_loopback(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Private voice checks require an explicit loopback HTTP endpoint")


def verify(config_path: Path, loop_path: Path, alsa_device: str) -> dict:
    from sage_lantern.adapters import SageSDKContext
    from sage_lantern.config import RuntimeConfig

    config = RuntimeConfig.load(config_path)
    require_loopback(config.sage_base_url)
    require_loopback(config.llama_base_url)
    if config.static_context or config.sage_identity_path is None:
        raise ValueError("Real signed SAGE context is required; static context cannot pass readiness")
    context = SageSDKContext(config.sage_base_url, config.sage_identity_path, config.sage_domain)
    context.recall("Local voice readiness diagnostic")
    module = runpy.run_path(str(loop_path), run_name="lantern_capture_check")
    loop = module["VoiceLoop"](argparse.Namespace(
        alsa_device=alsa_device, min_seconds=0.35, max_seconds=2.0,
    ))
    loop.write_state = lambda *args, **kwargs: None
    started = time.monotonic()
    loop.ptt = lambda: "down" if time.monotonic() - started < 1.0 else "up"
    with tempfile.TemporaryDirectory(prefix="lantern-readiness-") as directory:
        if not loop.capture("readiness", Path(directory) / "capture.wav"):
            raise RuntimeError("Microphone did not produce a valid capture")
    return {
        "schema": "sage.lantern.voice-readiness.v1",
        "signed_context": "passed",
        "microphone_capture_stop": "passed",
        "physical_ptt_end_to_end": "requires_operator_test",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--voice-loop", type=Path, default=Path("/usr/local/libexec/sage-lantern-voice-loop"))
    parser.add_argument("--alsa-device", default="hw:CARD=Audio,DEV=0")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.config, args.voice_loop, args.alsa_device), sort_keys=True))
    except Exception as exc:
        print(f"Local voice readiness failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
