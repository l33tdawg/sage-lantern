#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo 'Usage: install-fleet-admin-local.sh [--unit-id unit-a|unit-b|unit-c|unit-d|unit-e|unit-f] [--radio-evidence FILE] [--dry-run [--metadata FILE]]'
  echo 'Dry-run validates public bindings only. Live Unit B and guarded-appliance updates are not supported here.'
}
unit_id=unit-a
radio_evidence=''
metadata=/etc/sage-lantern/unit.env
dry_run=0
seen_unit=0
seen_radio=0
seen_metadata=0
while (($#)); do
  case "$1" in
    --help) (($# == 1)) || exit 2; usage; exit 0 ;;
    --dry-run) ((dry_run == 0)) || exit 2; dry_run=1; shift ;;
    --unit-id) (($# >= 2 && seen_unit == 0)) || exit 2; unit_id=$2; seen_unit=1; shift 2 ;;
    --radio-evidence) (($# >= 2 && seen_radio == 0)) || exit 2; radio_evidence=$2; seen_radio=1; shift 2 ;;
    --metadata) (($# >= 2 && seen_metadata == 0)) || exit 2; metadata=$2; seen_metadata=1; shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ $unit_id =~ ^unit-[a-f]$ ]] || exit 2
[[ $dry_run == 1 || $seen_metadata == 0 ]] || { echo 'Alternate metadata is dry-run only.' >&2; exit 2; }
if (( ! dry_run )); then
  [[ $unit_id == unit-a ]] || { echo 'Unit B requires the unfinished fresh secure appliance installer; no changes made.' >&2; exit 2; }
  [[ ! -e /etc/sage-lantern-device-lock.enabled && ! -L /etc/sage-lantern-device-lock.enabled ]] || {
    echo 'Guarded appliance detected. Use the reviewed update path; no changes made.' >&2; exit 2;
  }
fi
readonly source_scripts=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[[ -n $radio_evidence ]] || radio_evidence="/home/radxa/sage-commons/evidence/${unit_id}-radio/meshcore-radio-public.json"
preflight=$(python3 "$source_scripts/fleet-install-preflight.py" --unit-id "$unit_id" \
  --metadata "$metadata" --radio-evidence "$radio_evidence") || exit 2
if ((dry_run)); then printf '%s\n' "$preflight"; exit 0; fi

readonly service_name=sage-lantern-fleet-admin.service
readonly app_dir=/home/radxa/sage-commons/apps/lantern-fleet-admin
readonly repo_root=/home/radxa/sage-commons
readonly service_source="${app_dir}/deploy/${service_name}"
readonly service_target="/etc/systemd/system/${service_name}"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

if [[ $(uname -m) != aarch64 ]]; then
  echo "Refusing to install: expected Unit A aarch64, found $(uname -m)." >&2
  exit 1
fi

if [[ ! -f ${app_dir}/package-lock.json || ! -f ${service_source} ]]; then
  echo "Lantern Fleet Admin checkout is incomplete at ${app_dir}." >&2
  exit 1
fi

run_as_radxa() {
  runuser -u radxa -- env PATH="${PATH}" "$@"
}

run_as_radxa node -e '
  const [major, minor] = process.versions.node.split(".").map(Number);
  if (major < 22 || (major === 22 && minor < 13)) {
    throw new Error(`Node >=22.13.0 is required; found ${process.versions.node}`);
  }
'

cd "${app_dir}"
run_as_radxa npm ci
run_as_radxa npm test
run_as_radxa npm run lint
run_as_radxa env LANTERN_EDGE_LOCAL=1 "LANTERN_EDGE_OPERATOR=Unit A operator" npm run build

install -o root -g root -m 0644 "${service_source}" "${service_target}"
install -d -o root -g root -m 0755 /usr/local/libexec
install -d -o root -g root -m 0755 /usr/local/share/sage-lantern
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/unit-a-status-server.py" /usr/local/libexec/sage-lantern-status-server
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/unit-a-ptt-monitor.py" /usr/local/libexec/sage-lantern-ptt-monitor
readonly runtime_python=/opt/sage-lantern/venv/bin/python
if [[ ! -x ${runtime_python} ]]; then
  echo "Install the Lantern runtime before the photo-history voice service." >&2
  exit 1
fi
runtime_package=$(${runtime_python} -c 'import pathlib, sage_lantern; print(pathlib.Path(sage_lantern.__file__).parent)')
[[ ${runtime_package} == /opt/sage-lantern/venv/lib/python*/site-packages/sage_lantern ]] || exit 1
install -o root -g root -m 0644 "${repo_root}/software/runtime/src/sage_lantern/history_media.py" "${runtime_package}/history_media.py"
runuser -u sage-lantern -- "${runtime_python}" -c 'from sage_lantern.history_media import HistoryMediaStore; assert callable(HistoryMediaStore.prune)'
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/unit-a-voice-loop.py" /usr/local/libexec/sage-lantern-voice-loop
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/verify-local-voice.py" /usr/local/libexec/sage-lantern-verify-local-voice
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/approve-local-voice.py" /usr/local/libexec/sage-lantern-approve-local-voice
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/activate-unit-a-local-voice.sh" /usr/local/sbin/activate-unit-a-local-voice
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/configure-kiosk-login.py" /usr/local/libexec/sage-lantern-configure-kiosk-login
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/configure-unit-a-hostname.py" /usr/local/libexec/sage-lantern-configure-unit-a-hostname
install -o root -g root -m 0644 "${radio_evidence}" /usr/local/share/sage-lantern/meshcore-radio-public.json
install -o root -g root -m 0644 "${repo_root}/software/systemd/sage-lantern-status.service" /etc/systemd/system/sage-lantern-status.service
install -o root -g root -m 0644 "${repo_root}/software/systemd/sage-lantern-ptt.service" /etc/systemd/system/sage-lantern-ptt.service
install -o root -g root -m 0644 "${repo_root}/software/systemd/sage-lantern-llm.service" /etc/systemd/system/sage-lantern-llm.service
install -o root -g root -m 0644 "${repo_root}/software/systemd/sage-lantern-personal-sage.service" /etc/systemd/system/sage-lantern-personal-sage.service
install -o root -g root -m 0644 "${repo_root}/software/systemd/sage-lantern-runtime.service" /etc/systemd/system/sage-lantern-runtime.service
install -o root -g root -m 0644 "${repo_root}/software/systemd/sage-lantern-voice-loop.service" /etc/systemd/system/sage-lantern-voice-loop.service
install -o root -g root -m 0644 "${repo_root}/software/udev/71-sage-lantern-audio.rules" /etc/udev/rules.d/71-sage-lantern-audio.rules
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/sage-lantern-kiosk" /usr/local/bin/sage-lantern-kiosk
install -o root -g root -m 0755 "${repo_root}/scripts/lantern/sage-lantern-session" /usr/local/bin/sage-lantern-session
install -o root -g root -m 0644 "${app_dir}/deploy/sage-lantern.desktop" /usr/share/xsessions/sage-lantern.desktop
install -d -o radxa -g radxa -m 0755 /home/radxa/.config/autostart
install -o radxa -g radxa -m 0644 "${app_dir}/deploy/sage-lantern-kiosk.desktop" /home/radxa/.config/autostart/sage-lantern-kiosk.desktop
/usr/local/libexec/sage-lantern-configure-kiosk-login
/usr/local/libexec/sage-lantern-configure-unit-a-hostname --unit-id "$unit_id"
run_as_radxa dbus-run-session gsettings set org.gnome.desktop.session idle-delay 0
run_as_radxa dbus-run-session gsettings set org.gnome.desktop.screensaver lock-enabled false
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty
udevadm trigger --subsystem-match=sound
systemctl daemon-reload
systemctl enable sage-lantern-status.service sage-lantern-ptt.service "${service_name}"
systemctl restart sage-lantern-ptt.service sage-lantern-status.service "${service_name}"

for _ in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:3100/device >/dev/null \
      && curl --fail --silent --show-error http://127.0.0.1:3199/status >/dev/null; then
    systemctl --no-pager --full status "${service_name}"
    echo "Lantern touchscreen is ready at http://127.0.0.1:3100/device on Unit A."
    exit 0
  fi
  sleep 1
done

journalctl -u "${service_name}" --no-pager -n 80 >&2
echo "Lantern Fleet Admin did not become ready within 30 seconds." >&2
exit 1
