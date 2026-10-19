#!/usr/bin/env bash
# Explicit Desktop control fallback: sage-lantern-desktop-mode on|off|auto
set -euo pipefail
runtime=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
override="$runtime/sage-lantern-mode.override"
case ${1:-} in
  on|desktop) printf 'desktop\n' > "$override" ;;
  off|kiosk) printf 'kiosk\n' > "$override" ;;
  auto) rm -f "$override" ;;
  *) echo "usage: $(basename "$0") on|off|auto" >&2; exit 2 ;;
esac
sleep 3
cat "$runtime/sage-lantern-mode.state" 2>/dev/null || echo "supervisor state unavailable"
