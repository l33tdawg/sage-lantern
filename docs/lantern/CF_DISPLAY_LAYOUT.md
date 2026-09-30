# C–F portrait display layout

Local UI preparation, 2026-09-27. No unit deployment or physical display acceptance.

The ordered display recorded in `HARDWARE_BOM.md` is the 6.25-inch,
720 × 1560 HDMI capacitive-touch panel. Use portrait as the C–F layout direction.

**Display update 2026-09-29 (owner):** the **final panels are the ribbon type
(MIPI DSI), not HDMI**, and they are already ordered for units G/H — which are
getting the full peripheral set (final ribbon panel, CSI-ribbon camera, radios).
C–F run the HDMI 720 × 1560 panel with USB touch. **Owner decision 2026-09-29:
the HDMI panel on C–F is bench-only; the final product is definitely DSI.**
Consequences:

- **C–F display/touch results are bench evidence, not product acceptance.** The
  product display/touch/rotation acceptance must be repeated on a DSI unit
  (G/H, or a bench unit equipped with the final panel) before any display claim
  is made about the shipped device. Do not publish C–F panel numbers as final.
- The two-platform bake-off measures the display axis on the DSI panel for both
  platforms; HDMI measurements on C–F are only a development proxy.

The DSI panel changes the acceptance surface in three ways:

- touch arrives over the panel's I2C controller (an ordinary touchscreen input
  device) rather than a USB HID device — the status helper's direct-input
  detection covers it, but it must be re-verified on the real panel;
- the panel driver, backlight control and any rotation/orientation come from the
  board's device tree, so the DSI panel needs a per-board overlay — and the
  ROCK 5B+ and Orange Pi 5 Plus DSI connectors/ribbon pinouts are not
  interchangeable, so an adapter per platform is a procurement item;
- the two-platform bake-off compares displays only fairly if G/H (DSI) and C–F
  are measured the same way; today they would differ (HDMI + USB touch vs DSI +
  I2C touch).
The earlier approximate 6.5-inch description is superseded by that order record.

**Panel status, owner-confirmed 2026-09-29:** the two SKU 35000 panels were
**ordered** (the hardware workstream's earlier record read "added to cart" — the
owner confirms the order was placed). Despatch and arrival are still unverified,
and the pre-shell gate stands: get the connector pinout and timing table from
Waveshare and claim the DSI port in the device tree before any G/H shell is cut,
because the SKU 35000 part is Pi-documented (15/22-pin, touch on I2C0) while G/H
are ROCK.

## Layout

`apps/lantern-fleet-admin/app/device/portrait.css` adapts to the viewport's
orientation rather than a unit identifier or CSS rotation:

- One conversation column; Chats opens the existing drawer.
- Consistent 76px header on unlocked screens.
- Two rows of bottom actions, with at least 64px-tall controls.
- Attached photo above the conversation, with its height capped to preserve
  reading space.
- Passcode prompt above the keypad, including at the native 720px width.
- Camera controls can wrap; review actions no longer overlap the bottom rows.

The landscape rules remain in effect on A/B's landscape viewports.

## Scale and hardware commissioning

### Owner desktop requirement — 28 September 2026

**Settled by D-020 (owner, 2026-09-30):** portrait stays the device default —
the handheld question-and-answer posture — and landscape is the **desk mode**,
entered when a USB keyboard *and* mouse are attached (the supervisor's
automatic switch) or forced with the explicit control. Landscape is not the
boot default and no orientation sensor is required. Everything below records
how the first version was built and what remains open.

The owner wants a custom desktop when the device is turned onto its side and
used with a keyboard and mouse, alongside the portrait Lantern interface.
This is a product requirement, not an implemented mode switch. The existing
landscape CSS is only the application layout; it is not a desktop session.
Both the current Armbian Ubuntu base and a suitable board-specific Ubuntu
image can host a custom desktop. The base still needs graphics, NPU, touch,
boot and update qualification independently of its theme or branding.

Physical orientation sensing has not been verified. A proposed first version
can detect keyboard/mouse attachment and offer an explicit Desktop control;
automatic physical rotation requires a supported orientation sensor. Preserve
the running Lantern session and inference service during any mode transition,
and rotate the display and touch mapping together. These are design proposals
awaiting implementation and physical testing, not claims of current behavior.

### Implemented first version on C — 28 September 2026

`scripts/lantern/cf-mode-supervisor.py` (installed on C as
`/usr/local/libexec/sage-lantern-mode-supervisor`, session autostart) now
provides the two modes without touching the app server, the conversation state
or the device lock:

- **Portrait (default):** no mouse attached — the console runs fullscreen
  (Chromium `--kiosk`, Wayland-native) on the 720 × 1560 panel, output
  transform `normal`.
- **Docked:** USB keyboard *and* mouse both attached — the fullscreen browser is
  replaced by an ordinary window on the GNOME desktop and the output rotates to
  the side (`rotate-90` by default), so the device can be turned on its side
  with the companion app still available.
- **Explicit control:** `sage-lantern-desktop-mode on|off|auto`
  (`scripts/lantern/cf-desktop-mode.sh`) forces either mode or returns to
  detection. Detection reads `/dev/input/by-id` keyboard/pointer nodes only, so
  the panel's direct-input touchscreen is never mistaken for a mouse.
- **Rotation:** `scripts/lantern/cf-output-mode.py` changes only the logical
  monitor transform through `org.gnome.Mutter.DisplayConfig`
  (`GetCurrentState` → `ApplyMonitorsConfig` with the existing connector and
  mode, current scale preserved). Touch mapping follows the logical monitor, so
  display and touch rotate together — no CSS transform and no X11 xrandr.

Verified on C: cold boot (boot id `cacae86c…`) lands in portrait with all four
services active, one Wayland Chromium on `127.0.0.1:3100/device`, transform
`normal`, screen unlocked; forcing docked mode applies `rotate-90` and a single
windowed browser; returning to `auto` restores portrait. One browser instance at
a time, enforced by a supervisor lock and a kill-before-start rule.

**Known gaps, recorded honestly:** the app keeps its unlock token in browser
memory only, so a mode change re-locks the UI — persisting it is an app-side
change (next release). The installed `sage-lantern-kiosk` launcher targets X11
and needs `--ozone-platform=wayland` added in the release; the supervisor
carries its own Wayland-native kiosk command meanwhile. The default
`rotate-90` direction and the physical touch mapping after rotation still need
the owner's eyes on the panel; flip to `rotate-270` in
`/etc/sage-lantern/display-mode.json` if the side orientation is upside down.

**Docked desktop contents (C, 28 September 2026):** the image shipped Chromium,
Files (Nautilus), Text Editor, Calculator, System Monitor and Software, but no
terminal and no maps app. `gnome-terminal` (3.58.0) and `gnome-maps` (49.3) were
installed on the owner's instruction, and the dock favourites were set through
`/etc/dconf/db/local.d/01-sage-lantern-dock` to Lantern, Chromium, Files,
Terminal, Maps, Google Maps (web), Text Editor, Calculator and Software.
A `sage-lantern-console.desktop` entry launches the companion app as a window,
so it stays reachable in docked mode. Note honestly: `gnome-maps` needs network
for tiles and the Google Maps entry is a web app — the offline map pipeline
remains its own open task.

Resolution alone does not establish comfortable physical text and touch sizes.
The initial browser review covers 720 × 1560 CSS pixels and 480 × 1040 CSS
pixels. The latter corresponds geometrically to a 1.5 display scale on the
native panel and is a candidate for the bench review, not an installed setting.

On C, record the actual display output, reported mode, compositor/session type,
browser viewport, device pixel ratio, and touch input identity. Establish which
edge is physically up in the enclosure. Configure output orientation and the
matching touch mapping together if needed; do not assume a 90° rotation simply
because the product is portrait. No CSS transform should rotate the application.

Check all four corners and the centre, keyboard targets, drawer opening,
passcode entry, question review, long-answer scrolling, camera capture/retake,
and wake/restart persistence. Repeat the physical mapping check on D/E/F.
Browser simulations do not qualify HDMI mode, touch alignment, or wake behavior.

### Panel scale must be 1.0 — 29 September 2026

The vendor image drives the 720×1560 panel (HDMI-2, WSD Waveshare-625,
720x1560@59.987) at GNOME fractional scale **1.6667** with no stored monitor
configuration. The kiosk's Chromium renders at 1×, so the compositor scaled the
720-px-wide console by 1.667 and the panel showed only the middle ~60% of the
page — text cut on both edges ("Check setup" clipped both sides on C).

The reviewed rule is therefore: **scale 1.0 for the 720×1560 panel.**
`scripts/lantern/cf-output-mode.py` now takes an explicit `--scale` and applies
it with the transform through a single reviewed logical-monitor tuple (same
position, connector, current mode and primary flag), and
`scripts/lantern/cf-mode-supervisor.py` reads `panel_scale` from
`/etc/sage-lantern/display-mode.json` (default `1.0`, bounded 0.5–4.0) and passes
it on every transform application, so both portrait and docked transitions keep
the panel at 1:1. A hand-written `/home/<operator>/.config/monitors.xml`
pinning `<scale>1</scale>` was used as a bench stopgap on C before the
supervisor change; provisioning should ship the reviewed equivalent.

Consequence for the pending 480×1040 (1.5×) candidate: this Chromium build
crops under fractional scaling, so any non-integer scale needs its own
on-panel verification before it is chosen for the product.

### Recovery: a panel link drop ends the graphical session — 29 September 2026

When the panel was physically disconnected on C (both HDMI connectors read
`disconnected`, kernel logged `dw-dp … AUX timeout` plus a USB disconnect), the
sage GNOME session ended with the link and GDM showed its greeter; GDM's
configured `AutomaticLogin = sage` (`/etc/gdm3/custom.conf`) only applies when
GDM starts, not after a session ends. The kiosk therefore stayed down even
after the panel was reconnected. Recovery, verified on C:

```sh
sudo systemctl restart gdm3     # autologin starts the session again
# the session autostart then brings up the mode supervisor and the kiosk
```

That restart is a bench-acceptable recovery, not the product behaviour: a
display glitch should not be able to strand a field unit at a login prompt.
Options for the release lane are a session/displays watchdog (restart GDM when a
configured output disappears and returns) or at minimum this documented
recovery in the operator runbook. The console itself was unaffected (HTTP 200),
and the pinned scale 1.0 survived the session restart.

## Local verification

The synthetic preview imports the real UI components and refuses external
requests. Start from the repository root:

```sh
node work/cf-portrait-20260927/preview/server.mjs
```

Open `http://127.0.0.1:5193/`. Synthetic controls provide an unlock-screen link
and simulated question/answer states. These are review fixtures, not live unit
state. The camera backend is deliberately unavailable.

Verified in the browser: native and scaled portrait unlock stacking; native
main-screen/footer bounds; scaled question review, keyboard editing and answer
display; 1024 × 600 landscape retains the 220px chat sidebar and four-column
footer. TypeScript and the changed TSX file's ESLint check pass.

Outstanding: physical scale/rotation/touch qualification and actual photo view,
camera capture, Messages and onboarding checks on the panel.
