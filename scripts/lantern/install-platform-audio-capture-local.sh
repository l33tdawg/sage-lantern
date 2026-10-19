#!/usr/bin/env bash
# Install (and boot-enable) the reviewed C-F platform capture path unit.
# The unit itself only sets and verifies the qualified ES8388 mixer values.
set -euo pipefail

[[ $# -eq 0 ]] || { echo 'Usage: install-platform-audio-capture-local.sh (no arguments)' >&2; exit 2; }

readonly repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
readonly helper=/usr/local/libexec/sage-lantern-set-platform-audio-capture
readonly unit=/etc/systemd/system/sage-lantern-audio-capture.service
readonly service=sage-lantern-audio-capture.service
readonly device=/dev/snd/by-path/platform-es8388-sound

[[ ${EUID} -eq 0 && $(uname -m) == aarch64 ]] || { echo 'Expected root on an aarch64 Lantern.' >&2; exit 1; }
[[ -e $device ]] || { echo "No platform ES8388 sound device at $device." >&2; exit 1; }
test -x /usr/bin/amixer
test -x /opt/sage-lantern/venv/bin/python
test ! -L "$helper"
test ! -L "$unit"
/usr/bin/systemd-analyze verify "$repo_root/software/systemd/sage-lantern-audio-capture.service"

readonly backup=/var/backups/sage-lantern-audio-capture-$(date -u +%Y%m%dT%H%M%SZ)
install -d -o root -g root -m 0700 "$backup"
for source in "$helper" "$unit"; do
  if [[ -f $source ]]; then cp -p "$source" "$backup/"; fi
done

install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$repo_root/scripts/lantern/set-platform-audio-capture.py" "$helper"
install -o root -g root -m 0644 "$repo_root/software/systemd/sage-lantern-audio-capture.service" "$unit"
sync -f "$helper"
sync -f "$unit"
systemctl daemon-reload
systemctl enable "$service" >/dev/null
systemctl start "$service"
/opt/sage-lantern/venv/bin/python -I -B "$helper" --check
echo "Platform capture path installed, enabled and verified; backups: $backup"
echo 'The unit sets only the reviewed capture controls (no playback control, no file writes).'
echo "Rollback: restore $backup contents, systemctl daemon-reload, systemctl disable --now $service."
