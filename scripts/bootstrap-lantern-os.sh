#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_NAME="$(basename "$0")"

mode="check"
skip_update=false
profile=debian12

readonly -a REQUIRED_PACKAGES=(
  python3
  python3-venv
  python3-yaml
  build-essential
  cmake
  libopencv-dev
  tar
  ca-certificates
  libstdc++6
  libgomp1
  alsa-utils
  usbutils
  pciutils
)

usage() {
  cat <<EOF
Usage: ${SCRIPT_NAME} [--check | --install | --dry-run] [--skip-update] [--profile PROFILE]

Prepare an already-booted Radxa ROCK 5B+ running Debian Bookworm ARM64.
An explicit orangepi5plus-ubuntu26.04 profile supports C–F's reviewed base
with isolated Python 3.11.16 at /opt/lantern-python/3.11.16.

  --check        Report readiness without changing the system (default).
  --install      Install missing prerequisite packages. Run as root.
  --dry-run      Show the apt commands that --install would run.
  --skip-update  Do not refresh apt package indexes before --install.
  -h, --help     Show this help.

This script never flashes media, partitions disks, changes boot firmware,
creates identities, enables services, or modifies apt sources.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

shell_join() {
  local item
  printf '  '
  for item in "$@"; do
    printf '%q ' "${item}"
  done
  printf '\n'
}

while (($#)); do
  case "$1" in
    --check)
      mode="check"
      ;;
    --install)
      mode="install"
      ;;
    --dry-run)
      mode="dry-run"
      ;;
    --skip-update)
      skip_update=true
      ;;
    --profile)
      shift
      profile="${1:-}"
      [[ "$profile" == debian12 || "$profile" == orangepi5plus-ubuntu26.04 ]] || die 'unsupported platform profile'
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
  shift
done

[[ -r /etc/os-release ]] || die '/etc/os-release is unavailable; expected Debian Bookworm'

# /etc/os-release is the operating system's root-owned machine-readable identity.
# shellcheck disable=SC1091
source /etc/os-release

architecture="$(uname -m)"
[[ "${architecture}" == "aarch64" || "${architecture}" == "arm64" ]] || \
  die "expected Linux ARM64 (aarch64); found ${architecture}"

os_id="${ID:-unknown}"
os_like="${ID_LIKE:-}"
os_codename="${VERSION_CODENAME:-}"
os_version="${VERSION_ID:-}"

board_model="$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)"

validate_platform() {
if [[ "$profile" == orangepi5plus-ubuntu26.04 ]]; then
  [[ "$os_id" == ubuntu && "$os_version" == 26.04 && "$os_codename" == resolute ]] || die 'C–F profile requires Ubuntu 26.04 resolute'
  [[ "$board_model" == 'Orange Pi 5 Plus' ]] || die 'C–F profile requires Orange Pi 5 Plus'
  [[ "$(uname -r)" == 6.1.115-vendor-rk35xx ]] || die 'C–F profile requires reviewed vendor kernel 6.1.115-vendor-rk35xx'
elif [[ "${os_id}" != "debian" && " ${os_like} " != *" debian "* ]]; then
  die "expected Debian or a Debian-derived image; found ID=${os_id}"
elif [[ "${os_codename}" != "bookworm" && "${os_version}" != "12" && "${os_version}" != 12.* ]]; then
  die "expected Debian Bookworm/12; found VERSION_ID=${os_version:-unknown} VERSION_CODENAME=${os_codename:-unknown}"
fi
}
validate_platform

if [[ "$profile" == orangepi5plus-ubuntu26.04 ]]; then
  # Check the separately built interpreter before apt or the fresh installer.
  # Never redirect /usr/bin/python3 or admit an arbitrary interpreter path.
  /usr/bin/python3 - <<'CF_RUNTIME_PY'
import os, stat, subprocess
from pathlib import Path
p = Path('/opt/lantern-python/3.11.16/bin/python3.11')
for component in (p, *p.parents):
    s = component.lstat()
    if stat.S_ISLNK(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise SystemExit('C–F runtime must have root-owned, non-writable, unlinked components')
subprocess.run([str(p), '-I', '-c',
    'import sys,ssl,sqlite3,ctypes,bz2,lzma,zlib,venv,ensurepip; '
    'assert sys.version_info[:3] == (3,11,16); '
    'assert ssl.create_default_context().get_ca_certs()'], check=True, timeout=20)
if Path('/opt/lantern-python/3.11.16/bin/python3').resolve() != p:
    raise SystemExit('unexpected C–F python3 executable')
CF_RUNTIME_PY
  export PATH="/opt/lantern-python/3.11.16/bin:/usr/sbin:/usr/bin:/sbin:/bin"
fi

printf 'Platform: %s; OS: %s; architecture: %s\n' \
  "${PRETTY_NAME:-${os_id}}" "${os_codename:-${os_version}}" "${architecture}"

if [[ -r /proc/device-tree/model ]]; then
  board_model="$(tr -d '\000' </proc/device-tree/model)"
  printf 'Board: %s\n' "${board_model}"
  if [[ "$profile" == debian12 && "${board_model,,}" != *"rock 5b"* ]]; then
    printf 'WARNING: device-tree model does not identify a ROCK 5B/5B+: %s\n' "${board_model}" >&2
  fi
else
  printf 'WARNING: /proc/device-tree/model is unavailable; board model was not verified.\n' >&2
fi

command -v dpkg-query >/dev/null 2>&1 || die 'dpkg-query is unavailable'

missing_packages=()
for package in "${REQUIRED_PACKAGES[@]}"; do
  if [[ "$(dpkg-query -W -f='${Status}' "${package}" 2>/dev/null || true)" != \
      "install ok installed" ]]; then
    missing_packages+=("${package}")
  fi
done

if ((${#missing_packages[@]} == 0)); then
  printf 'Prerequisites: ready (%d required packages installed).\n' "${#REQUIRED_PACKAGES[@]}"
else
  printf 'Missing packages (%d): %s\n' "${#missing_packages[@]}" "${missing_packages[*]}"
fi

if [[ "${mode}" == "dry-run" ]]; then
  printf 'Dry run; no commands executed. Proposed commands:\n'
  if [[ "${skip_update}" == false ]]; then
    shell_join apt-get update
  fi
  if ((${#missing_packages[@]} > 0)); then
    shell_join env DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
      "${missing_packages[@]}"
  else
    printf '  (no package installation needed)\n'
  fi
  exit 0
fi

if [[ "${mode}" == "check" ]]; then
  if ((${#missing_packages[@]} > 0)); then
    printf 'Not ready. Run: sudo %s --install\n' "$0" >&2
    exit 1
  fi
else
  if ((${#missing_packages[@]} > 0)); then
    [[ "${EUID}" -eq 0 ]] || die '--install requires root; rerun with sudo'
    command -v apt-get >/dev/null 2>&1 || die 'apt-get is unavailable'

    if [[ "${skip_update}" == false ]]; then
      apt-get update
    fi
    env DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
      "${missing_packages[@]}"
  else
    printf 'No package changes needed.\n'
  fi
fi

for command_name in python3 cmake c++ tar arecord aplay lsusb lspci; do
  command -v "${command_name}" >/dev/null 2>&1 || \
    die "post-check failed; command is unavailable: ${command_name}"
done

python_version="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
python3 -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 11))' || \
  die "the offline wheelhouse requires Python 3.11 exactly; found ${python_version}"

printf 'Bootstrap check passed. Python %s and all required commands are available.\n' \
  "${python_version}"
printf 'Next: verify the repository bundle, then run install-lantern-unit.sh.\n'
