# Final compute requirement — working determination (30 September 2026)

Owner asked what the final compute hardware actually needs to be, after the
question of ordering six ROCK 5B+ 12 GB units came up. This is the software
side's measured answer plus the hardware gates that are still open. It does
not decide the January 2027 platform bake-off (D-015); it states what any
winner must satisfy.

## What the product must run

Measured on unit C (Orange Pi 5 Plus 16 GB) with the full stack live and both
model lanes resident and answering, 30 Sep 2026:

| Component | Resident cost |
|---|---|
| NPU text lane — 2B w8a8 RKLLM (`rkllm_engine`) | 2.17 GB RSS (2.9 GB file) |
| NPU vision lane — 0.8B vision RKNN + w8a8 RKLLM (`vlm_engine`) | 1.48 GB RSS (1.5 GB files) |
| Personal SAGE node (CometBFT chain) | 0.20 GB |
| Console app (Next.js/Node) + kiosk Chromium + GNOME Shell | ~1.2 GB |
| Voice loop, device-auth, status server | ~0.10 GB |
| **Whole system** | **5.3 GB used, 10.6 GB available** (swap unused) |

The retired heavy tier, if it ever returns: CPU `llama-server` 4B (2.7 GB model
+ 675 MB projector, ~3.4 GB RSS) or NPU 4B W8A8 (**5.58 GB / 5725 MiB peak**,
measured in the earlier qualification work). Adding either on top of today's
stack lands at roughly 9 GB.

## Requirement any final board must satisfy

| Axis | Requirement | Status |
|---|---|---|
| SoC | RK3588-class, 8 cores, 6 TOPS NPU (both model lanes are NPU-resident) | both candidates |
| RAM | **12 GB floor works (≈2× measured headroom); 16 GB preferred; 8 GB fails** (the 4B tier alone needs 5.6–5.7 GB) | measured |
| Storage | 512 GB NVMe, boot without SD | PRD |
| Power | Must accept the bring-your-own-charger rule: **PD negotiation or a fixed 5 V/4 A source the user can be asked to carry** | ROCK negotiates 12 V/3 A (36 W) measured; Orange Pi 5 Plus does no PD and resets without 5 V/4 A |
| Wireless | WiFi/BT required | ROCK 5B+ onboard; Orange Pi 5 Plus needs an added module (open procurement) |
| Display | Final product is the **DSI** panel (Waveshare SKU 35000, 2 ordered) on **internal ribbon wiring only** — no external cabling may exit the shell (owner, 2026-09-30). Host support per board **unverified** | `CF_DISPLAY_LAYOUT.md` — the gate that can invalidate a board for the product |
| Camera | **Internal CSI ribbon only** (owner, 2026-09-30); IMX519 modules ordered, not arrived; host connector/pinout unverified | netmap open |
| Radio | C–F: two LoRa radios + GNSS + coexistence | `CF_PERIPHERAL_BINDINGS.md` |
| Cooling | Active cooler; closed-shell thermals unmeasured on either board | open |

## Determination

1. **The compute class is settled**: RK3588 + 6 TOPS NPU + **≥12 GB RAM** +
   512 GB NVMe + active cooling. 12 GB runs the shipping stack with ~2×
   headroom and still fits a future 4 B tier; 16 GB is the comfort pick, 8 GB
   is not viable.
2. **Memory is not the deciding axis** for the platform choice.
3. The two product-critical differentiators in our own records favour the
   **ROCK 5B+**: it negotiates PD (matching the bring-your-own-power rule and
   the PRD's "known-good 65 W USB-C PD supply" line) and it carries onboard
   WiFi/BT, which the Orange Pi 5 Plus lacks.
4. **"Final board" cannot be signed off today.** Two gates are open on the
   ROCK side: DSI panel host support/pinout for SKU 35000 (the product's
   display), and closed-shell thermals. The owner's January 2027 bake-off
   remains the decision point.

## Consequence for the purchase

**Committed 2026-09-30 (owner direction, D-015 amendment): ROCK 5B+ 12 GB is
the main compute.** Take up to 6 units where stock allows and the 16 GB SKU is
not available at a comparable price; G/H are the first two.

**Hard product gate added 2026-09-30 (owner): internal wiring only.** The
product may not have a display or camera cable exiting the shell, so the
product paths are an **internal DSI ribbon** for the panel and an **internal
CSI ribbon** for the camera. HDMI and USB cabling are bench-only and are not an
acceptable product path. The consequence for the purchase: **neither candidate
board has a proven internal display or camera path today** — the ROCK 5B+'s
DSI/CSI connectors and overlays are unread, the Orange Pi's 30-pin DSI pinout
is unverified for a generic panel, the ordered Waveshare SKU 35000 panel is
Pi-standard and needs an adapter/ribbon on either host, and the camera netmap
is open. The first-article acceptance job on the first board that lands is to
prove the internal panel and camera wiring (connector, ribbon, overlay, panel
and sensor bring-up); the remaining fleet units are held until that gate
passes. Compute, RAM, power input, wireless and NPU-artifact reuse are all
cleared by the evidence above.
