#!/usr/bin/env bash
# Install or remove the SAGE Lantern desktop skin for the kiosk user.
# The skin is presentation only; it grants no authority and changes no
# application, SAGE, radio or device-auth policy.
set -euo pipefail

readonly here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly assets="$here/desktop"
readonly user=${SAGE_USER:-sage}
readonly uid=$(id -u "$user")
readonly home=$(getent passwd "$user" | cut -d: -f6)
readonly state_dir=/var/lib/sage-lantern/desktop-skin
readonly state_file="$state_dir/state.json"
readonly wallpaper=/usr/share/backgrounds/sage-lantern/sage-lantern-desktop.svg
readonly start_page=/usr/share/sage-lantern/desktop/start.html
readonly icon_dir=/usr/share/icons/hicolor/scalable/apps
readonly app_dir=/usr/share/applications

[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 2; }
[[ -f "$assets/sage-lantern-desktop.svg" ]] || { echo "missing desktop assets: $assets" >&2; exit 2; }
[[ -S "/run/user/$uid/bus" ]] || { echo "no user D-Bus session for $user" >&2; exit 2; }

g() {
  sudo -u "$user" env HOME="$home" XDG_RUNTIME_DIR="/run/user/$uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" gsettings "$@"
}

install_files() {
  install -Dm644 "$assets/sage-lantern-desktop.svg" "$wallpaper"
  install -Dm644 "$assets/sage-lantern-start.html" "$start_page"
  install -Dm644 "$assets/sage-lantern-desktop-icon.svg" "$icon_dir/sage-lantern-desktop.svg"
  install -Dm644 "$assets/sage-cerebrum.svg" "$icon_dir/sage-cerebrum.svg"
  install -Dm644 "$assets/sage-lantern-desktop.desktop" "$app_dir/sage-lantern-desktop.desktop"
  install -Dm644 "$assets/sage-cerebrum.desktop" "$app_dir/sage-cerebrum.desktop"
  if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q "$app_dir" || true; fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true; fi
}

save_state() {
  install -d -m700 "$state_dir"
  python3 - "$state_file" "$user" <<'PY'
import json, subprocess, sys
from pathlib import Path
user = sys.argv[2]
uid = subprocess.check_output(["id", "-u", user], text=True).strip()
home = subprocess.check_output(["getent", "passwd", user], text=True).split(":")[5]
env = {"HOME": home, "XDG_RUNTIME_DIR": f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}
def get(schema, key):
    return subprocess.check_output(["sudo", "-u", user, "env", *[f"{k}={v}" for k, v in env.items()], "gsettings", "get", schema, key], text=True).strip()
state = {
    "user": user,
    "accent": get("org.gnome.desktop.interface", "accent-color"),
    "color_scheme": get("org.gnome.desktop.interface", "color-scheme"),
    "background": get("org.gnome.desktop.background", "picture-uri"),
    "background_dark": get("org.gnome.desktop.background", "picture-uri-dark"),
    "picture_options": get("org.gnome.desktop.background", "picture-options"),
    "screensaver": get("org.gnome.desktop.screensaver", "picture-uri"),
    "favorites": get("org.gnome.shell", "favorite-apps"),
}
Path(sys.argv[1]).write_text(json.dumps(state, indent=2) + "\n")
PY
}

set_favorites() {
  local current
  current=$(g get org.gnome.shell favorite-apps)
  python3 - "$current" <<'PY' >/tmp/sage-lantern-favorites.$$
import ast, sys
raw = sys.argv[1].strip()
if raw.startswith("@as "): raw = raw[4:]
values = list(ast.literal_eval(raw)) if raw else []
add = ["sage-lantern-desktop.desktop", "sage-cerebrum.desktop", "sage-lantern-console.desktop"]
for item in reversed(add):
    if item in values: values.remove(item)
    values.insert(0, item)
print(str(values))
PY
  local favorites
  favorites=$(cat /tmp/sage-lantern-favorites.$$)
  rm -f /tmp/sage-lantern-favorites.$$
  g set org.gnome.shell favorite-apps "$favorites"
}

apply_settings() {
  g set org.gnome.desktop.interface accent-color "'orange'"
  g set org.gnome.desktop.interface color-scheme "'prefer-dark'"
  g set org.gnome.desktop.background picture-uri "'file://$wallpaper'"
  g set org.gnome.desktop.background picture-uri-dark "'file://$wallpaper'"
  g set org.gnome.desktop.background picture-options "'zoom'"
  g set org.gnome.desktop.screensaver picture-uri "'file://$wallpaper'"
  set_favorites
}

restore_state() {
  [[ -f "$state_file" ]] || { echo "no previous skin state at $state_file" >&2; return 1; }
  python3 - "$state_file" <<'PY' | while IFS=$'\t' read -r schema key value; do g set "$schema" "$key" "$value"; done
import json, sys
s = json.load(open(sys.argv[1]))
rows = [
    ("org.gnome.desktop.interface", "accent-color", s["accent"]),
    ("org.gnome.desktop.interface", "color-scheme", s["color_scheme"]),
    ("org.gnome.desktop.background", "picture-uri", s["background"]),
    ("org.gnome.desktop.background", "picture-uri-dark", s["background_dark"]),
    ("org.gnome.desktop.background", "picture-options", s["picture_options"]),
    ("org.gnome.desktop.screensaver", "picture-uri", s["screensaver"]),
    ("org.gnome.shell", "favorite-apps", s["favorites"]),
]
for row in rows: print("\t".join(row))
PY
}

case "${1:-install}" in
  install)
    [[ -f "$state_file" ]] || save_state
    install_files
    apply_settings
    echo "Installed SAGE Lantern desktop skin for $user"
    echo "  wallpaper: $wallpaper"
    echo "  start page: $start_page"
    echo "  shortcuts: SAGE Lantern Desktop, SAGE CEREBRUM"
    ;;
  uninstall)
    restore_state
    rm -f "$wallpaper" "$start_page" "$icon_dir/sage-lantern-desktop.svg" "$icon_dir/sage-cerebrum.svg" \
      "$app_dir/sage-lantern-desktop.desktop" "$app_dir/sage-cerebrum.desktop"
    rm -rf "$state_dir"
    echo "Removed SAGE Lantern desktop skin and restored previous desktop settings"
    ;;
  *)
    echo "usage: $0 [install|uninstall]" >&2
    exit 2
    ;;
esac
