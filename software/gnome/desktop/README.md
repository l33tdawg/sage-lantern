# SAGE Lantern desktop skin

First-version desktop presentation for the `sage` kiosk user:

- navy/amber SAGE Lantern wallpaper;
- dark Adwaita with the orange accent;
- local start page with Lantern, CEREBRUM, Maps, Foundation and Meshticulum
  links;
- `SAGE Lantern Desktop`, `SAGE CEREBRUM` and `SAGE Maps & Location`
  application shortcuts;
- CEREBRUM opens `http://127.0.0.1:8080/ui/` in a Chromium app window.
- Maps opens the branded location panel with online Google Maps/OSM links and
  the offline OpenStreetMap plan.
- `Return to Lantern Portrait` is available in the app grid.

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
GNOME landscape desktop entered by attaching a USB keyboard; the mouse is
optional. The desktop login is the `sage` account with the owner's Lantern
password; the password is not stored here.
