# Lantern desktop mode — browser, skin and CEREBRUM

Status: first implementation on unit C, 18 October 2026. Portrait remains the
device default; landscape is the keyboard desk mode, with the mouse optional. Physical panel
acceptance is still open.

The desktop login is the `sage` account with the owner's Lantern password; D-022
requires that fresh login before the desktop is exposed. The password is not
stored in this document, the skin, or the repository.

## Browser

Ship **Chromium** as the single graphical browser. The kiosk and desktop
launchers already use Chromium with `--ozone-platform=wayland`, and the
installed version is 151.0.7922.108. Firefox is not installed; its Armbian
candidate is 156.0 with roughly 80.5 MB download / 283 MB installed footprint,
plus a second profile, update and launcher stack. No measured requirement
currently justifies that duplication. Keep `links` as the terminal fallback.

## Desktop skin

The first skin is source-controlled under `software/gnome/`:

- navy/amber SAGE Lantern wallpaper for the desktop and lock screen;
- dark Adwaita with the orange accent;
- a local start page linking the companion, CEREBRUM, the Foundation and
  Meshticulum;
- `SAGE Lantern Desktop`, `SAGE CEREBRUM` and `SAGE Maps & Location`
  application shortcuts;
- `Return to Lantern Portrait` in the app grid.

Install or remove on the device:

```sh
sudo software/gnome/install-desktop-skin.sh install
sudo software/gnome/install-desktop-skin.sh uninstall
```

The installer saves the previous accent, color scheme, wallpaper, lock-screen
wallpaper and dock favorites under `/var/lib/sage-lantern/desktop-skin/` and
restores them on uninstall. It grants no SAGE, radio, lock or device-auth
authority and stores no passphrase.

On C the installed settings were checked live: accent `orange`, color scheme
`prefer-dark`, the SAGE Lantern SVG on the desktop/dark/lock backgrounds, and
SAGE Lantern Desktop, SAGE CEREBRUM, SAGE Maps & Location and the Lantern
Console first in the dock.

## CEREBRUM

The local CEREBRUM UI is available at `http://127.0.0.1:8080/ui/` and returned
HTTP 200 on C. The vault is encrypted and asks for the owner's passphrase; the
desktop shortcut only opens the local page. CEREBRUM is a desktop-mode
destination and is not exposed through the portrait kiosk.

## Maps and GNSS

The first Maps page is `software/gnome/desktop/maps.html`. It provides:

- a branded illustrative location panel centered on Kuala Lumpur;
- online shortcuts to Google Maps and OpenStreetMap;
- an honest GNSS status panel;
- the offline-map plan and attribution.

Google Maps remains an **online-only** web shortcut. Its tiles are not cached
or redistributed. Offline maps will use OpenStreetMap data (ODbL) in a bounded
regional pack served locally, with attribution and a documented disk budget.

The current C hardware check found no GNSS receiver: no `/dev/ttyACM*` or
`/dev/ttyUSB*`, no `gpsd` package/service and no kernel GNSS device. C–F are
planned to have GNSS, and the SX1262/GNSS HAT may carry a receiver, but the
receiver/interface still has to be confirmed and wired. The existing
`cf-gnss-replay.py` parser is an offline validation candidate, not a live
location service. The Maps page therefore says “GNSS receiver not detected”
rather than showing a fake fix.

The next maps increment is a bounded OSM/PMTiles regional pack plus a local
map renderer, followed by a `gpsd`/NMEA live owner and a UI fix/accuracy
indicator. Exact position is private by default and is not published by the
desktop.

## Companion landscape

The real `/device` UI was exercised through the synthetic preview at the panel
and window sizes:

- 1560 × 720 landscape main answer: sidebar, centered answer column, composer,
  Share and camera controls fit without clipping;
- 1560 × 720 landscape lock screen: two-column unlock layout fits;
- 720 × 1560 portrait control: still renders correctly.

The preview was updated to serve the current `sage.lantern.voice-status.v1`
`/voice` endpoint so the live voice-screen state can be exercised without a
device. The product layout did not need a landscape-specific override for
these screens.

## Open work

- Physical touch/rotation mapping and all four corners/centre on the panel.
- The keyboard-only trigger is implemented (`wanted_mode`); mouse is optional.
- Decide whether desktop mode requires a login and how the app session survives
  a portrait/docked switch without persisting the RAM-only unlock token.
- Direct visual check of the installed wallpaper and dock on the physical
  panel.
- Custom dock/icon polish beyond the first navy/amber pass.
- RDP/VNC is not enabled. `gnome-remote-desktop` 50.0 and `grdctl` are installed
  for a future loopback-only test harness; never expose it on the LAN.
