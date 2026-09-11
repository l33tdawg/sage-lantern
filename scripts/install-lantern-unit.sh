#!/usr/bin/env bash

set -euo pipefail

usage() {
  printf '%s\n' 'Usage: install-lantern-unit.sh [--help]' \
    'Fresh target installation only; no upgrade, recovery, or dry-run mode.' \
    'Run base installation before provision-lantern-unit.sh prepare.' \
    'Existing Lantern roots, private markers or systemd units are refused without deletion.' \
    'Interrupted installations remain blocked for manual review.'
}

if [[ $# -eq 1 && ( $1 == --help || $1 == -h ) ]]; then
  usage
  exit 0
fi
if [[ $# -ne 0 ]]; then
  usage >&2
  printf 'Unsupported arguments; nothing installed.\n' >&2
  exit 2
fi

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Run as root on the target Lantern.\n' >&2
  exit 2
fi

if [[ "$(uname -m)" != "aarch64" ]]; then
  printf 'This bundle targets Linux aarch64; found %s.\n' "$(uname -m)" >&2
  exit 2
fi

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_root="/opt/sage-lantern"
state_root="/var/lib/sage-lantern"
config_root="/etc/sage-lantern"

for command in python3 cmake c++ tar nproc systemctl useradd install mkdir; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    printf 'Missing prerequisite: %s\n' "${command}" >&2
    exit 2
  fi
done

fresh_target_preflight() {
  python3 - / <<'FRESH_TARGET_PY'
from pathlib import Path
import os, stat, subprocess, sys

root = Path(sys.argv[1])

def refuse(reason):
    raise SystemExit('Fresh-target preflight refused: ' + reason + '. No automatic deletion or recovery.')

def exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        refuse('cannot inspect target')

def lantern_name(name):
    return name == 'sage-lantern' or name.startswith(('sage-lantern-', 'sage-lantern.', 'sage-lantern@'))

for name in ('opt/sage-lantern', 'var/lib/sage-lantern', 'etc/sage-lantern',
             'etc/sage-lantern-device-lock.enabled', 'etc/sage-lantern-vault-setup.enabled',
             'run/sage-lantern-suspend'):
    if exists(root / name):
        refuse('existing /' + name)

def inspect_units(directory):
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if lantern_name(entry.name):
                    refuse('existing Lantern unit, dependency link, mask or drop-in')
                if entry.is_dir(follow_symlinks=False):
                    inspect_units(Path(entry.path))
                elif entry.is_symlink() and entry.name.endswith(('.wants', '.requires', '.d')):
                    refuse('cannot safely inspect linked unit configuration directory')
    except OSError:
        refuse('cannot inspect systemd configuration')

for name in ('etc/systemd/system', 'run/systemd/system', 'usr/local/lib/systemd/system',
             'usr/lib/systemd/system', 'lib/systemd/system', 'run/systemd/transient',
             'etc/systemd/system.control', 'run/systemd/system.control',
             'etc/systemd/system.attached', 'run/systemd/system.attached',
             'run/systemd/generator', 'run/systemd/generator.early', 'run/systemd/generator.late'):
    directory = root / name
    if exists(directory):
        try:
            if not stat.S_ISDIR(directory.stat().st_mode):
                refuse('invalid systemd configuration directory')
        except OSError:
            refuse('cannot inspect systemd configuration directory')
        inspect_units(directory)

for operation in ('list-units', 'list-unit-files'):
    try:
        result = subprocess.run(['systemctl', operation, '--all', '--plain', '--no-legend', '--no-pager'],
                                check=False, capture_output=True, text=True, timeout=10,
                                env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        refuse('cannot establish existing systemd units')
    if result.returncode != 0:
        refuse('cannot establish existing systemd units')
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and lantern_name(fields[0]):
            refuse('existing loaded or installed Lantern unit')
FRESH_TARGET_PY
}

fresh_target_preflight

python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "${python_version}" != "3.11" ]]; then
  printf 'This offline wheelhouse requires Python 3.11; found %s.\n' "${python_version}" >&2
  exit 2
fi

build_jobs="${SAGE_LANTERN_BUILD_JOBS:-2}"
if [[ ! "${build_jobs}" =~ ^[1-9][0-9]*$ ]] || ((build_jobs > $(nproc))); then
  printf 'SAGE_LANTERN_BUILD_JOBS must be an integer from 1 through %s; found %s.\n' "$(nproc)" "${build_jobs}" >&2
  exit 2
fi

python3 "${source_root}/scripts/verify-lantern-bundle.py"
python3 "${source_root}/scripts/verify-runtime-release.py" --bundle
python3 "${source_root}/scripts/verify-runtime-release.py" --binary-target

fresh_target_preflight

claim_install_root() {
  if ! mkdir -m 0755 -- "${install_root}"; then
    printf 'Fresh install claim failed; another install or existing target requires review.\n' >&2
    exit 2
  fi
}
claim_install_root

if ! id sage-lantern >/dev/null 2>&1; then
  useradd --system --home-dir "${state_root}" --create-home --shell /usr/sbin/nologin sage-lantern
fi

install -d -o root -g root -m 0755 "${install_root}/bin" "${install_root}/models" "${install_root}/llama.cpp"
install -d -o root -g root -m 0755 /usr/local/sbin
install -d -o sage-lantern -g sage-lantern -m 0700 "${state_root}" "${state_root}/audio" "${state_root}/identity" "${state_root}/logs" "${state_root}/sage-personal"
install -d -o root -g sage-lantern -m 0750 "${config_root}"

install -m 0755 "${source_root}/software/bundle/linux-arm64/bin/sage-gui" "${install_root}/bin/sage-gui"
install -m 0644 "${source_root}/software/cache/models/ggml-base.bin" "${install_root}/models/ggml-base.bin"
install -m 0644 "${source_root}/software/cache/models/qwen3.5-4b-q4_k_m.gguf" "${install_root}/models/qwen3.5-4b-q4_k_m.gguf"
install -m 0644 "${source_root}/software/cache/models/mmproj-BF16.gguf" "${install_root}/models/mmproj-BF16.gguf"
install -m 0644 "${source_root}/software/cache/models/en_US-lessac-medium.onnx" "${install_root}/models/en_US-lessac-medium.onnx"
install -m 0644 "${source_root}/software/cache/models/en_US-lessac-medium.onnx.json" "${install_root}/models/en_US-lessac-medium.onnx.json"

python3 -m venv "${install_root}/venv"
"${install_root}/venv/bin/python" -m pip install --no-index --find-links "${source_root}/software/bundle/python-wheels" \
  numpy==1.26.4 sage-agent-sdk==11.19.10+lantern.20260906 sage-lantern-runtime==0.2.0 piper-tts==1.4.2
"${install_root}/venv/bin/python" "${source_root}/scripts/verify-runtime-release.py" --installed

llama_build_root="$(mktemp -d /private/tmp/sage-lantern-llama.XXXXXX 2>/dev/null || mktemp -d /tmp/sage-lantern-llama.XXXXXX)"
whisper_build_root="$(mktemp -d /private/tmp/sage-lantern-whisper.XXXXXX 2>/dev/null || mktemp -d /tmp/sage-lantern-whisper.XXXXXX)"
trap 'rm -rf "${llama_build_root}" "${whisper_build_root}"' EXIT

# The upstream Ubuntu ARM64 release requires glibc 2.38 and cannot run on
# Debian Bookworm (glibc 2.36). Build the exact locked source natively and
# statically so the service does not depend on release-host shared libraries.
tar -xzf "${source_root}/software/cache/sources/llama.cpp-b9637.tar.gz" -C "${llama_build_root}" --strip-components=1
cmake -S "${llama_build_root}" -B "${llama_build_root}/build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF
cmake --build "${llama_build_root}/build" --config Release --target llama-server -j "${build_jobs}"
install -m 0755 "${llama_build_root}/build/bin/llama-server" "${install_root}/llama.cpp/llama-server"

# whisper.cpp defaults to shared libraries on Linux. A static target avoids a
# CLI that installs successfully but fails later because libwhisper.so is absent.
tar -xzf "${source_root}/software/cache/sources/whisper.cpp-v1.8.6.tar.gz" -C "${whisper_build_root}" --strip-components=1
cmake -S "${whisper_build_root}" -B "${whisper_build_root}/build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON
cmake --build "${whisper_build_root}/build" --config Release --target whisper-cli -j "${build_jobs}"
install -m 0755 "${whisper_build_root}/build/bin/whisper-cli" "${install_root}/bin/whisper-cli"

if [[ ! -e "${config_root}/lantern.toml" ]]; then
  install -m 0640 -o root -g sage-lantern "${source_root}/software/config/lantern.example.toml" "${config_root}/lantern.toml"
else
  printf 'Preserving existing configuration: %s/lantern.toml\n' "${config_root}"
fi
install -m 0644 "${source_root}/software/systemd/sage-lantern-llm.service" /etc/systemd/system/sage-lantern-llm.service
install -m 0644 "${source_root}/software/systemd/sage-lantern-personal-sage.service" /etc/systemd/system/sage-lantern-personal-sage.service
install -m 0644 "${source_root}/software/systemd/sage-lantern-runtime.service" /etc/systemd/system/sage-lantern-runtime.service
install -m 0755 "${source_root}/scripts/smoke-test-lantern-unit.sh" /usr/local/sbin/smoke-test-lantern-unit
systemctl daemon-reload

"${install_root}/venv/bin/sage-lantern" --version

printf '\nInstalled the sterile Lantern runtime. No identity or validator key was created.\n'
printf 'Base only; no services enabled. Next: provision-lantern-unit.sh prepare --unit-id YOUR_UNIT_ID, then independent identity/recovery and verified inventory.\n'
printf 'Secure app and owner-PIN setup are not provided by this base installer.\n'
