#!/bin/sh
# Create the repository's development virtualenv with the pinned SAGE SDK.
#
# Why this exists: the runtime pins `sage-agent-sdk` to a Lantern-specific build
# (`11.19.10+lantern.20260906`) that is NOT on PyPI. It adds `trust_env` support
# and several kilobytes of client/model code that the device runtime relies on.
# Installing the plain PyPI release instead leaves the runtime suite reporting
# hundreds of errors that are environment problems, not code problems.
#
# On a Lantern unit this is not used: the installer builds its own venv at
# /opt/sage-lantern/venv from the release bundle with --no-index --no-deps.
# This script is only for working on the repository from a development machine.
#
# Usage:
#   ./scripts/setup-dev-venv.sh [path-to-lantern-sdk-wheel]
#
# If no wheel path is given, a small set of known locations is searched. The
# wheel is not committed to the repository and lives under the gitignored
# work/ tree, so it must not be assumed to be present forever.

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

EXPECTED=$(sed -n 's/.*"sage-agent-sdk==\([^"]*\)".*/\1/p' software/runtime/pyproject.toml)
if [ -z "$EXPECTED" ]; then
  echo "setup-dev-venv: could not read the sage-agent-sdk pin from software/runtime/pyproject.toml" >&2
  exit 1
fi

WHEEL=${1:-}
if [ -z "$WHEEL" ]; then
  for candidate in \
    "work/ab-sdk-compatible-20260927/wheels/sage_agent_sdk-${EXPECTED}-py3-none-any.whl" \
    "work/ab-sdk-compatible-20260927/wheels/sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl"
  do
    if [ -f "$candidate" ]; then
      WHEEL=$candidate
      break
    fi
  done
fi
if [ -z "$WHEEL" ] || [ ! -f "$WHEEL" ]; then
  echo "setup-dev-venv: no wheel for sage-agent-sdk $EXPECTED found." >&2
  echo "  Pass one explicitly, e.g.:" >&2
  echo "    ./scripts/setup-dev-venv.sh /path/to/sage_agent_sdk-${EXPECTED}-py3-none-any.whl" >&2
  echo "  Do NOT use software/bundle/python-wheels: that is the plain PyPI build" >&2
  echo "  and it does not carry the Lantern client changes the runtime needs." >&2
  exit 1
fi

echo "setup-dev-venv: using $WHEEL"
python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet --no-deps "$WHEEL"
./.venv/bin/pip install --quiet httpx pydantic PyNaCl cryptography PyYAML pytest

INSTALLED=$(./.venv/bin/python -c 'import importlib.metadata as m; print(m.version("sage-agent-sdk"))')
if [ "$INSTALLED" != "$EXPECTED" ]; then
  echo "setup-dev-venv: installed $INSTALLED but expected $EXPECTED" >&2
  exit 1
fi

echo "setup-dev-venv: ready. sage-agent-sdk $INSTALLED in $ROOT/.venv"
echo "  Run the runtime suite:"
echo "    cd software/runtime && PYTHONPATH=src ../../.venv/bin/python -m pytest tests -q"
