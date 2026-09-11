#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 ]]; then
  printf 'Usage: %s PYTHON_EXECUTABLE FROZEN_RUNTIME_SRC\n' "$0" >&2
  exit 2
fi
root=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$2"
exec "$1" -B -m pytest -q -p no:cacheprovider \
  "$root/software/runtime/tests/test_startup_coordinator.py" \
  "$root/scripts/tests/test_install_startup_coordinator.py" \
  "$root/scripts/tests/test_startup_audio_adapter.py"
