#!/usr/bin/env bash
set -euo pipefail

camera_device=
mode=plan
while (($#)); do
  case $1 in
    --device)
      [[ -z $camera_device && $# -ge 2 ]] || { echo 'Supply one camera --device path.' >&2; exit 2; }
      camera_device=$2
      shift 2
      ;;
    --install)
      [[ $mode == plan ]] || { echo 'Duplicate --install.' >&2; exit 2; }
      mode=install
      shift
      ;;
    *) echo 'Usage: install-camera-local.sh --device /dev/v4l/by-id/NAME-video-indexN [--install]' >&2; exit 2 ;;
  esac
done
[[ $camera_device =~ ^/dev/v4l/by-id/[A-Za-z0-9_.:-]+-video-index[0-9]+$ ]] || {
  echo 'An explicit stable /dev/v4l/by-id camera path is required; never reuse another unit camera identity.' >&2; exit 2;
}

readonly repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly service_name=sage-lantern-camera.service
readonly base_command='ExecStart=/opt/sage-lantern/venv/bin/python /usr/local/libexec/sage-lantern-camera-server'
unit_definition=$(cat "${repo_root}/software/systemd/${service_name}")
[[ $(printf '%s\n' "$unit_definition" | grep -Fxc "$base_command") == 1 ]] || {
  echo 'Unexpected camera service template.' >&2; exit 2;
}
unit_definition=${unit_definition/"$base_command"/"$base_command --device=$camera_device"}
if [[ $mode == plan ]]; then
  printf '%s\n' "$unit_definition"
  python3 -I -B "${repo_root}/scripts/lantern/camera-vision-defaults.py" --device "$camera_device" >&2
  echo 'Plan only: no hardware detection, file installation, capture or activation performed.' >&2
  exit 0
fi

if [[ ${EUID} -ne 0 ]]; then
  echo 'Run this installer with sudo.' >&2
  exit 1
fi
if [[ $(uname -m) != aarch64 ]]; then
  echo 'Expected the Unit A ARM64 host.' >&2
  exit 1
fi

command -v python3 >/dev/null
command -v v4l2-ctl >/dev/null
/opt/sage-lantern/venv/bin/python -I -B -c '
from pathlib import Path
import stat
from sage_lantern.device_access import authorized, lock_enabled
from sage_lantern.camera_identity import verify_camera_identity
marker = Path("/etc/sage-lantern-device-lock.enabled")
info = marker.lstat()
if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o644 and marker.read_bytes() == b"1\n"
        and lock_enabled() is True and authorized(None) is False):
    raise SystemExit("Mandatory device access gate must be provisioned before camera activation")
'
[[ $(systemctl show "$service_name" --property=LoadState --value) == not-found ]] || {
  echo 'Existing camera service requires reviewed maintenance.' >&2; exit 1;
}
[[ -L $camera_device && -c $camera_device ]] || { echo 'Selected camera is not an attached character device.' >&2; exit 1; }
resolved_device=$(readlink -f -- "$camera_device")
[[ $resolved_device =~ ^/dev/video[0-9]+$ ]] || { echo 'Unexpected camera device target.' >&2; exit 1; }
[[ ! -e /usr/local/libexec/sage-lantern-camera-server && ! -L /usr/local/libexec/sage-lantern-camera-server \
  && ! -e /etc/systemd/system/sage-lantern-camera.service && ! -L /etc/systemd/system/sage-lantern-camera.service ]] || {
  echo 'Existing camera installation requires a reviewed update, not fresh installation.' >&2; exit 1;
}
test -f "${repo_root}/scripts/lantern/unit-a-camera-server.py"
test -f "${repo_root}/software/systemd/${service_name}"
python3 -I -B "${repo_root}/scripts/lantern/camera-vision-defaults.py" --device "$camera_device" --inspect
python3 -I -B "${repo_root}/scripts/lantern/camera-vision-defaults.py" --device "$camera_device" --install
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/unit-a-camera-server.py" /usr/local/libexec/sage-lantern-camera-server
temporary_unit=$(mktemp)
trap 'rm -f "$temporary_unit"' EXIT
printf '%s\n' "$unit_definition" > "$temporary_unit"
unit_sha256=$(sha256sum "$temporary_unit" | cut -d ' ' -f1)
helper_sha256=$(sha256sum "${repo_root}/scripts/lantern/unit-a-camera-server.py" | cut -d ' ' -f1)
install -o root -g root -m 0644 "$temporary_unit" "/etc/systemd/system/${service_name}"
systemctl daemon-reload
systemctl enable "${service_name}"
systemctl restart "${service_name}"

for attempt in {1..20}; do
  if /opt/sage-lantern/venv/bin/python -I -B -m sage_lantern.camera_identity \
      --device "$camera_device" --unit-sha256 "$unit_sha256" --helper-sha256 "$helper_sha256"; then
    echo 'Camera transport verified. Default vision commissioning is REQUIRED before photo readiness.'
    echo 'Run reviewed activate-fresh-vision with live pinned inputs; no photo or audio was captured by this installer.'
    exit 0
  fi
  sleep 1
done
echo 'Camera service did not become healthy; inspect its systemd journal.' >&2
exit 1
