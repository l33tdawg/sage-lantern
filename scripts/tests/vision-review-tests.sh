#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1

# Prefer the repository's own development virtualenv when it exists. The shared
# system interpreter is not guaranteed to carry the pinned SAGE SDK, and these
# tests exercise runtime code that depends on it.
if [ -x "$(pwd)/.venv/bin/python" ]; then
  PY="$(pwd)/.venv/bin/python"
else
  PY="python3"
fi

# Preflight the SDK.
#
# This script previously put a wheel file on PYTHONPATH:
#   .../test-support/sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl
# That directory has never existed in this repository, and a wheel cannot be
# imported from a PYTHONPATH entry in any case -- Python does not read zip
# archives as package directories. The entry was therefore inert, and the tests
# silently ran against whatever `sage_sdk` happened to be installed. When the
# installed version is older than the pinned one, that produces confidently wrong
# evidence, so it now fails loudly instead.
EXPECTED=$(sed -n 's/.*"sage-agent-sdk==\([^"]*\)".*/\1/p' software/runtime/pyproject.toml)
if [ -z "$EXPECTED" ]; then
  echo "preflight: could not read the sage-agent-sdk pin from software/runtime/pyproject.toml" >&2
  exit 1
fi
if ! "$PY" -c 'import importlib.metadata as m, sys; sys.exit(0 if m.version("sage-agent-sdk") == sys.argv[1] else 1)' "$EXPECTED" 2>/dev/null; then
  ACTUAL=$("$PY" -c 'import importlib.metadata as m; print(m.version("sage-agent-sdk"))' 2>/dev/null || echo "not installed")
  echo "preflight: sage-agent-sdk $ACTUAL, but the runtime pins $EXPECTED" >&2
  echo "  Fix: ./scripts/setup-dev-venv.sh   (then re-run this script)" >&2
  exit 1
fi

export PYTHONPATH="$(pwd)/software/runtime/src"
"$PY" -m pytest -q \
  scripts/tests/test_prepare_runtime_maintenance.py \
  scripts/tests/test_build_runtime_maintenance_env.py \
  scripts/tests/test_complete_runtime_maintenance_audio.py \
  scripts/tests/test_install_startup_coordinator.py \
  scripts/tests/test_startup_audio_adapter.py \
  scripts/tests/test_normal_shutdown_audio_startup.py \
  scripts/tests/test_startup_sleep_batch.py \
  scripts/tests/test_collect_cross_boot_audio_pins.py \
  scripts/tests/test_guarded_benchmark_report.py \
  scripts/tests/test_observe_voice_benchmark.py \
  scripts/tests/test_activate_fresh_vision.py \
  scripts/tests/test_maintenance_initial_exec.py \
  scripts/tests/test_maintenance_inactive_display.py \
  scripts/tests/test_collect_fresh_vision_inputs.py \
  scripts/tests/test_camera_vision_defaults.py \
  scripts/tests/test_install_camera_local.py
