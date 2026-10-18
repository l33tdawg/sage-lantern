# SAGE Lantern desktop skin

First-version desktop presentation for the `sage` kiosk user:

- navy/amber SAGE Lantern wallpaper;
- dark Adwaita with the orange accent;
- local start page with Lantern, CEREBRUM, Foundation and Meshticulum links;
- `SAGE Lantern Desktop` and `SAGE CEREBRUM` application shortcuts;
- CEREBRUM opens `http://127.0.0.1:8080/ui/` in a Chromium app window.

Install or remove from the repository root on the device:

```sh
sudo software/gnome/install-desktop-skin.sh install
sudo software/gnome/install-desktop-skin.sh uninstall
```

The installer saves the previous accent, color scheme, wallpaper, lock-screen
wallpaper and dock favorites under `/var/lib/sage-lantern/desktop-skin/` and
restores them on uninstall. It changes no SAGE, radio, lock, device-auth or
application policy. CEREBRUM may ask for the vault passphrase; the skin does
not store it.

The portrait Lantern kiosk remains the device default. This skin is for the
keyboard-and-mouse GNOME landscape desktop.
