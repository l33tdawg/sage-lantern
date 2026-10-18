#!/usr/bin/env bash
set -euo pipefail

readonly repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
readonly helper=/usr/local/libexec/sage-lantern-display-power
readonly supervisor=/usr/local/libexec/sage-lantern-display-session
readonly unit=/etc/systemd/system/sage-lantern-display-power.service
readonly service=sage-lantern-display-power.service

[[ ${EUID} -eq 0 && $(uname -m) == aarch64 ]] || { echo 'Expected root on Unit A aarch64.' >&2; exit 1; }
[[ $# -ge 2 && $1 == --unit-id && $2 =~ ^unit-[a-f]$ ]] || {
  echo 'Usage: install-display-power-local.sh --unit-id unit-a|unit-b|unit-c|unit-d|unit-e|unit-f [--legacy]. Install only; never activates.' >&2; exit 1;
}
readonly unit_id=$2
shift 2
legacy=()
if [[ $# -eq 1 && $1 == --legacy && $unit_id == unit-a ]]; then legacy=(--legacy); shift; fi
[[ $# -eq 0 ]] || { echo 'Unsupported display installation argument.' >&2; exit 1; }
test -x /usr/bin/xset
test -x /usr/bin/python3
test ! -L "$helper"
test ! -L "$supervisor"
test ! -L "$unit"
/opt/sage-lantern/venv/bin/python -I -B "$repo_root/scripts/lantern/lantern-display-session.py" \
  --check --unit-id "$unit_id" "${legacy[@]}"
/usr/bin/systemd-analyze verify "$repo_root/software/systemd/sage-lantern-display-power.service"
[[ $(systemctl show "$service" --property=ActiveState --value) == inactive ]] || {
  echo 'Display service must already be inactive under separately reviewed session-safe maintenance.' >&2; exit 1;
}

readonly backup=/var/backups/sage-lantern-display-power-$(date -u +%Y%m%dT%H%M%SZ)
install -d -o root -g root -m 0700 "$backup"
for source in "$helper" "$supervisor" "$unit"; do
  if [[ -f $source ]]; then cp -p "$source" "$backup/"; fi
done
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$repo_root/scripts/lantern/lantern-display-power.py" "$helper"
install -o root -g root -m 0755 "$repo_root/scripts/lantern/lantern-display-session.py" "$supervisor"
install -o root -g root -m 0644 "$repo_root/software/systemd/sage-lantern-display-power.service" "$unit"
sync -f "$helper"
sync -f "$supervisor"
sync -f "$unit"
systemctl daemon-reload
echo "Display helper and session supervisor installed, NOT started; backups: $backup"
echo 'Boot enablement is unchanged. Do not activate before owner PIN/recovery onboarding is complete.'
echo 'Separate root approval policy: /etc/sage-lantern-display-power.enabled, regular single-link root-owned 0644.'
echo 'Policy bytes: fresh unit-a through fresh unit-f + newline, or explicitly legacy unit-a + newline.'
echo 'After reviewed activation, verify physical screen-off/touch-wake/PIN before separate boot enablement.'
echo 'File rollback is separate: restore saved helper/unit from the backup, then systemctl daemon-reload.'
