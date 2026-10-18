# Internal display and camera paths — ROCK 5B+ vs our parts (30 September 2026)

Owner requirement: **internal wiring only** — the panel and the camera must sit
on internal ribbons inside the shell. No cable may exit the case, so HDMI/USB
is bench-only and the product paths are MIPI DSI (panel) and MIPI CSI (camera).
This is the research result for the two candidate boards, from the vendors'
own documentation.

## ROCK 5B+ — the internal paths exist and are vendor-documented

**MIPI DSI** (`docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/mipi-dsi`)

- The board carries a DSI **FPC connector**; the hardware-interface page gives
  the pin table: `LCD_3V3`, `VCC_1V8_S0`, `LCD_RESET`, `TP_RST_L`, `TP_INT_L`,
  touch on `I2C6_SDA_M0`/`I2C6_SCL_M0`, and the `MIPI_DPHY1_TX` lanes
  (D0/D1/CLK…). Note there is **no 5 V on the DSI FPC** — a panel that needs
  5 V takes it from another internal rail.
- Vendor support list, **ROCK 5B+**: Radxa Display 8 HD (1280×800), Radxa
  Display 10HD and 10FHD (1200×1920).
- The same page documents **Raspberry Pi 7-inch Touchscreen** on the **ROCK
  5B+**: connect through the FPC cable, enable the "Raspberry Pi 7-inch
  Touchscreen" overlay, reboot, and it displays with touch. So a **Pi-style
  internal DSI panel on a ribbon is a supported configuration on the 5B+** —
  the mechanism (FPC + device-tree overlay) is proven by the vendor.

**MIPI CSI** (`docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/mipi-csi`)

- **ROCK 5B+ has two 4-lane MIPI CSI camera connectors** (ROCK 5B has one
  4-lane, splittable to 2×2-lane). Pin tables show `I2C3`/`I2C4`, `VCC_3V3_S3`
  and `VCC_5V`, per-camera clock and reset lines (CAM1–CAM4 clock outs).
- Vendor support list, **ROCK 5B+**: Radxa Camera 4K (**IMX415**, 8.29 MP) and
  **Raspberry Pi Camera V2 (IMX219, 8 MP)** — both connected by FPC with
  device-tree overlays and `v4l2src` preview commands documented.

## Orange Pi 5 Plus — connector exists, nothing verified

- DSI is a 30-pin header per our notes; no verified pinout, timing or overlay
  for a generic panel. Camera path likewise unverified. Both need the same
  class of bring-up work, with no vendor-documented Pi-panel precedent.

## Our parts against the ROCK 5B+ path

**Panel — Waveshare 6.25" DSI LCD (B), SKU 35000 (2 ordered).** From
Waveshare's own wiki/product page:

- 720×1560 IPS, 5-point capacitive touch (Goodix-class controller on I2C),
  1.1 mm bonded glass.
- Electrical path on a Pi: **22-pin DSI** on Pi 5/CM5 (DSI-Cable-12cm) or
  **15-pin FPC** on Pi 4/3-class, with **5 V supplied separately** (pogo/spring
  pins on the Pi, or the panel's 4-pin PH2.0 5 V/GND lead).
- Software on a Pi: `dtoverlay=vc4-kms-dsi-waveshare-panel,6_25_inch` — a
  **Raspberry Pi (vc4/KMS) overlay**. There is no Rockchip driver for this
  panel.

So on a ROCK 5B+ the panel needs three concrete things, all internal:

1. an **adapter FPC** from the panel's 22-pin (or 15-pin) DSI to the ROCK's
   DSI connector;
2. **5 V routed internally** to the panel's PH2.0 (the DSI FPC carries 3.3 V /
   1.8 V only);
3. a **Rockchip DRM panel driver/overlay** for this panel's init sequence and
   timings — porting work, because the vendor software is Pi-only. The touch
   controller moves to the ROCK's `I2C6` (same device, different bus).

**Camera — 4 × IMX519 modules ordered.** Not on Radxa's support list. An
internal path needs (a) an adapter FPC to one of the two 4-lane CSI
connectors, and (b) an **IMX519 driver + overlay in the Rockchip kernel**.
The zero-risk alternative is a supported sensor: **IMX219 (Pi Camera V2)** or
**IMX415 (Radxa Camera 4K)**, which Radxa documents with overlays on the 5B+.

## Verdict

- **The ROCK 5B+ passes the internal-wiring requirement at the mechanism
  level**: vendor-documented internal DSI (including a Pi-style ribbon panel)
  and two internal 4-lane CSI connectors with supported sensors and overlays.
  This is the answer to the "can it be internal at all" question, and it is
  yes — with a documented example of a Pi panel on the 5B+.
- **Our exact parts are the remaining work, not the board**: the Waveshare
  6.25" panel needs an adapter FPC, internal 5 V and a Rockchip panel driver;
  the IMX519 cameras need a driver port or a swap to IMX219/IMX415. Both are
  bench tasks with precedent, not missing hardware.
- The Orange Pi 5 Plus has no equivalent vendor-documented internal path in
  our records, which strengthens the ROCK 5B+ as the product board.

## Actions

1. Hardware: identify the exact adapter FPCs (panel 22/15-pin → ROCK DSI;
   camera → ROCK CSI) and the internal 5 V route; record them as purchase
   lines.
2. Software: port/author the 6.25" panel DRM overlay for the RK3588 kernel
   (init sequence + timings + I2C6 touch), and check IMX519 support in the
   Radxa/Armbian kernel; if it is absent, decide between porting the driver
   and swapping to IMX219.
3. First-article gate on the first 5B+ that lands: light the panel on the
   internal ribbon and capture from the internal camera before the remaining
   fleet units are ordered.

## Kernel and overlay evidence (Radxa's own trees, read 30 Sep 2026)

Checked the vendor kernel (`radxa/kernel`, branch `linux-6.1-stan-rkr5.1`) and
the overlay package (`radxa/overlays`, `main`):

- **Panel driver surface:** `drivers/gpu/drm/panel/panel-simple.c` in the
  Rockchip tree supports **DSI command sequences**
  (`panel_simple_xfer_dsi_cmd_seq`, generic short/long writes), so a panel with
  an init sequence plus timings can be described in device tree rather than
  requiring a brand-new driver. No Waveshare-specific driver exists.
- **Touch:** `drivers/input/touchscreen/goodix.c` (plus `gt9xx`) is present, so
  the Waveshare panel's Goodix-class controller is supported; it only needs to
  be pointed at the DSI connector's touch bus (`I2C6`).
- **Vendor overlay precedent:** `rock-5b-raspi-7inch-touchscreen.dts` enables
  `dsi1` with `rockchip,lane-rate`, a panel child node, a panel power
  regulator (`vcc_lcd_mipi1`), `mipi_dcphy1`, the VP3 route, and touch devices
  on `i2c6`. That file is the exact template for a Waveshare 6.25" overlay.
- **Camera:** `drivers/media/i2c/` has `imx219.c`, `imx415.c`, `ov5647.c` and
  **no `imx519.c`**. The overlay package has
  `rock-5b-plus-cam{0,1}-radxa-camera-4k.dts` and
  `rock-5b-plus-cam{0,1}-rpi-camera-v2.dts` (the 5B+ variants simply include
  the 5B overlay with 5B+ metadata). So IMX219/IMX415 are a supported internal
  path today; **IMX519 needs a driver port plus an overlay**, or a swap.

Net effort for our parts: one new DSI panel overlay (modelled directly on the
Pi-7" overlay, with our panel's timings/init sequence and 5 V regulator), and
either an IMX519 driver port or a change to IMX219/IMX415.

## Panel-size fallback, measured by the hardware workstream (30 Sep 2026)

The owner allowed the panel to grow "a bit" (6.25" → 7"). The hardware
workstream measured what that actually costs, and it is **not a parameter
change**:

| | 6.25" Waveshare (ours) | Raspberry Pi 7" display |
|---|---|---|
| module | 74.70 × 159.18 mm | 192.96 × 110.76 mm |
| lit glass | 68.70 × 153.18 mm | 154.08 × 85.92 mm |

The current device is 182 mm long, the narrow tub is 91.4 mm across and the
wide-body cavity is 85.2 mm — the Pi 7" module (193 mm) does not fit the device
length, and even its lit glass (85.9 mm) is wider than the tub. Adopting it
means a **front-end re-cut** (lip ring, locating step, gasket, bezel, cover
tray) and a device that is wider and longer than anything drawn; it needs the
owner's sign-off as a size change, not a swap.

Consequence: the 6.25" Waveshare stays the primary panel and the overlay port
is the path; the Pi 7" is a fallback only if that port fails **and** the owner
accepts the larger product geometry. Note the Pi display is 800×480 landscape
(≈133 ppi) against our 720×1560 portrait (≈281 ppi).

## Camera pricing (hardware workstream, 30 Sep 2026)

- Radxa Camera 4K (IMX415): **RM240.14** — vendor-supported on the 5B+, 8.29 MP.
- Radxa Camera 8M 219 (IMX219): **RM63.03** — vendor-supported on the 5B+,
  8 MP. (Our pick list is now a cost decision between these two.)
- Trap: Amazon's only Radxa camera is a Camera13M (IMX214) at MYR 97.86, which
  is **not** a 5B+-supported sensor.
