# Units 3/4 enclosed slab — early planning brief

**Status:** Enclosure remains provisional; four 6.25-inch displays and four SX1262 boards are confirmed ordered below. No CAD freeze or compatibility acceptance.  
**Updated:** 2026-09-11 (exact screen/radio orders and CARBO 20 identification). Manufacturer shortlist sources accessed 2026-09-05.  
**Target:** January 2027 TII demonstration; exact meeting date TBD.

## Latest owner-confirmed display, radio and battery details — 2026-09-11

The owner supplied the actual order descriptions: **four 6.25-inch 720×1560
capacitive HDMI IPS displays**, with optical-bonded toughened glass and 5-point
touch, and **four SX1262 LoRaWAN Node Module Expansion Boards for Raspberry Pi
with magnetic CB antennas**, all ordered and awaiting delivery. The exact
6.25-inch order description supersedes the earlier approximate 6.5-inch wording.
The previous delivery estimate was next week. Selected radio band/GNSS option
was not supplied.

The display matches Waveshare SKU 26757; its supplier lists 74.70 × 159.18 mm
touch-screen outline and 66.60 × 144.18 mm active area. Obtain the complete
module/controller/mounting drawing before freezing CAD. The radio family uses
SPI/40-pin GPIO and is distinct from A/B's USB Heltec MeshCore companions.

The owner identified the lightweight pack as **NITECORE CARBO 20**, 20,000 mAh.
Manufacturer geometry is **128 × 48 × 29 mm**, **320 g ±5 g**, with up to 65 W
on one USB-C output and 15 W total when both outputs are used. Pack quantity,
order/receipt and Lantern qualification have not been confirmed. The old UGREEN
comparison below is historical; it is not the pack requested for this handoff.

The full updated list and primary sources are in
[the corrected ABLEMAN handoff](../../work/hardware-list-ableman-20260911/HARDWARE_LIST.md).
His previously reported 6.5-inch-based sliced shell is a concept, requiring
revision against the real screen, CARBO 20 and assembled electronics envelopes.
The September 5 shortlist below is retained as historical engineering context.

## Provisional recommendation

Keep the **ROCK 5B+ / NVMe / active cooler / USB MeshCore stack**. Following the user's updated direction on 2026-09-05, compare **7-, 9- and 10-inch-class HDMI + USB capacitive-touch screens**, a **compact USB microphone**, and an **intact, removable protected USB-C PD bank** in a ventilated, serviceable 3D-printed slab. Larger sizes are preferred for readability; final diagonal depends on full-device grip, weight, power and fit measurements. The earlier 5-inch candidate is retained below only as a historical comparison, not the preferred direction. Exact 9-/10-inch SKUs remain to be researched before purchase.

Build and validate **one Unit 3 engineering sample before separately approving duplicate Unit 4 orders**. Preserve Units 1/2 (A/B in older documents) as reference builds; do not harvest their parts, change their images or clone their identities. Existing Unit B acceptance must not be assumed complete.

Current interaction scope: **physical hold-to-talk → local speech/context/inference → screen answer**. No TTS, speaker, amplifier, keyboard-based setup or wheel dependency. A larger USB array is a fallback if a simple mic fails acoustic tests; I2S and custom battery engineering are not the January default.

**User-approved audio direction:** omit the built-in speaker from the proposed
Unit 3/4 design. Keep the onboard microphone as the independent capture path.
Bluetooth headphones are the preferred future optional output path, not a
currently implemented feature or a promise of compatibility with every headset.
Spoken answers would still require a local TTS implementation and tested output
routing; Bluetooth transport alone does not turn displayed text into speech.

Before enabling headphone output, validate touch-only discovery/pairing,
explicit device selection, reconnect after reboot, volume/mute, forgetting a
device and disconnect recovery on the actual OS/audio stack. Test representative
headphones and simultaneous Wi-Fi operation; do not silently switch capture to
a headset microphone or send private speech to an unselected output. Screen
answers and visible status must remain fully usable without headphones. This
optional lane must not block the January speech-to-screen demonstration.

## Baseline and roadmap constraints

Read against the [original PRD](PRD.md), [BOM](HARDWARE_BOM.md), [hardware–software contract](HARDWARE_SOFTWARE_CONTRACT.md), [dual-home contract](DUAL_HOME_AND_PUBLICATION_CONTRACT.md) and [decisions](DECISIONS.md). The original 3.5-inch/TTS/wheel requirements are not silently amended or passed by this study: owners must record the scoped demo variation. Maintain semantic PTT events, stable enumeration, recovery, private/public isolation and independent new-unit identities.

The private TII roadmap in `internal-docs/` targets at least two polished working units, ideally four; the [long-horizon roadmap](../roadmap/THREE_TO_FIVE_YEAR_DELIVERY_PLAN.md) and [gate contract](../roadmap/GATE_EVIDENCE_CONTRACT.md) still govern maturity. A January demo is not production or pilot acceptance. Workstream tracking remains with the parent in `DELIVERY_BACKLOG.md`; this study does not modify it.

## Shortlist — documented specifications, not approved parts

All primary links accessed **2026-09-05**. Supplier dimensions exclude any additional enclosure/cable allowances unless stated. **Availability, landed cost and lead time are TBD for every candidate.**

| Candidate | Manufacturer-documented starting point | Disposition / unresolved items |
|---|---|---|
| ROCK 5B+ | Board footprint **100 × 75 mm**. [Radxa introduction](https://docs.radxa.com/en/rock5/rock5b/getting-started/introduction) | Retain platform. Exact new-unit revision/RAM and populated stack height TBD. |
| Radxa 6240B V2.0 | V2.0 addresses earlier ROCK 5B+ interference. [Radxa product note](https://radxa.com/products/accessories/heatsink-6240b/) | Retain active cooling; measure actual mounted height and airflow clearances before CAD. |
| Waveshare **5inch HDMI LCD (H) V4** | **800 × 480**, capacitive touch, HDMI/USB; face **121 × 89.48 mm**; supplier lists **5 V, 2 W**. [Manufacturer wiki](https://www.waveshare.com/wiki/5inch_HDMI_LCD_%28H%29_V4) | Smaller-face alternative. Complete depth/mass TBD; battery and computer may dominate overall size. |
| Waveshare **7inch HDMI LCD (C)** | **1024 × 600 IPS**, capacitive USB touch, HDMI; **164.90 × 106.96 × 8 mm**; supplier lists **5 V, 2.5 W**. [Manufacturer wiki](https://www.waveshare.com/wiki/7inch_HDMI_LCD_%28C%29) | Preferred reading-first candidate. Exact revision, protrusions and mass TBD. |
| Adafruit **Mini USB Microphone 3367** | **22.2 × 18.3 × 7 mm**, **2.2 g**, USB. [Manufacturer product](https://www.adafruit.com/product/3367) | First compact capture-only candidate. Native audio formats, serial identity and enclosed sound quality TBD. |
| Seeed **reSpeaker Lite** | Dual mic, USB Audio Class 2.0/I2S, **35 × 86 mm**, USB 5 V; documented maximum sampling rate 16 kHz. [Manufacturer wiki](https://wiki.seeedstudio.com/reSpeaker_usb_v3/) | Larger USB fallback; use capture only, no speaker. Firmware, full height, power and actual audio quality TBD. |
| Adafruit **SPH0645LM4H I2S breakout 3421** | Mono digital mic, bottom acoustic port, **16.7 × 12.7 × 1.8 mm**, **1.6–3.6 V** device. [Manufacturer product](https://www.adafruit.com/product/3421) | Smaller sensor, greater integration work; no ROCK driver/sample-rate compatibility assumed. |

HDMI/USB minimizes interface changes but does **not** prove either screen works on the installed ROCK kernel/compositor. Validate native mode, touch mapping, boot/reconnect and backlight behavior. Screen wattages are supplier nominal figures, not maximum rail budgets. Do not substitute similarly named resistive/SPI or MIPI variants.

**Design targets, not compliance claims:** answer-first text around 28–32 CSS px and large touch controls around 72 px; verify actual physical size, viewing distance, glare and hand reach. Use the larger display for reading, not administration.

## Audio interface and enclosure constraints

- **USB first:** preserves the ALSA-facing architecture, but still needs format/gain validation, stable device selection and reconnect tests. The reference capture path is 16 kHz stereo signed-16-bit; a new mic may need explicit conversion. Do not spoof the existing board’s identity/readiness string.
- **I2S alternative:** requires verified pinmux, kernel/ASoC configuration, clocks, channel alignment and possible resampling. Header I2S functions are not proof of a working capture path. Audit PTT pin ownership. [Radxa interface tables](https://docs.radxa.com/en/rock5/rock5b/hardware-design/hardware-interface)
- Put the mic aperture near the speaking edge, clear of fingers, fan exhaust and structure-borne PTT noise. Qualify any gasket/mesh acoustically. Compare fan-idle/inference and representative room-noise recordings. No TTS removes the local speaker echo path, not fan/room noise.

## Camera addition — 2026-09-05

User intends to obtain one simple USB camera for Unit A; it is listed as planned,
not ordered or received, in the [BOM](HARDWARE_BOM.md). First prove capture and
the photo-plus-spoken-question workflow before selecting duplicate modules for
Units 3/4. Exact module, focus range, resolution and power requirements remain
TBD; a working webcam does not establish vision-model compatibility or useful
answer quality on the ROCK hardware.

Reserve an outward-facing camera aperture, secure mount, lens clearance,
service access and internal cable routing; confirm orientation and grip with a
physical mockup. Evaluate a privacy cover and clearly indicate when capture is
active. Do not let any webcam microphone silently replace the dedicated mic.
Photos remain private by default; explicit capture/confirmation and deletion
are required, with no automatic upload to Commons. The existing voice-only
flow must remain usable when the camera is absent or vision processing fails.

## Packaged battery, not custom lithium work

Prefer an **intact protected PD bank in a removable cradle**, retaining its original housing, labels and controls. Manufacturer protection claims do not certify safety inside a new enclosure.

| Geometry candidate | Documented facts | Main caution |
|---|---|---|
| UGREEN Nexode **130W / 20,000mAh, SKU 35524** | **72 Wh; 131 × 54 × 50.8 mm; 476 g**. C1 lists up to 100 W including 20 V/5 A; C2 up to 30 W. Supplier advertises overheat/overcurrent protections. [UGREEN specification](https://uk.ugreen.com/products/ugreen-nexode-power-bank-130w-20000mah) | Column shape is at least 50.8 mm thick before housing. Exact match to existing inventory TBD; “130 W total” is not per-port power. |
| UGREEN **145W / 25,000mAh, SKU 90597A** | **90 Wh; 160 × 80 × 26 mm; 513 g**. [UGREEN specification](https://uk.ugreen.com/products/ugreen-145w-25000mah-power-bank-for-laptop-3-ports-power-bank) | Flatter but larger/heavier. Product title and C1/C2 output table need exact-revision clarification; obtain manual/PDO before electrical selection. |

Radxa recommends a **PD65W adapter for ROCK 5B+**. Retain the known-good bench-power baseline; adapter rating is not actual load. [Radxa power guidance](https://docs.radxa.com/en/rock5/rock5b/getting-started/power-supply)

Measure negotiated PD, startup/inference peaks, USB peripheral load, connector heating and port-change behavior with the exact manufactured cables. Do not assume pass-through charging, UPS behavior, telemetry or hot source switching. Initially charge the removed bank per its instructions and shut down before changing power sources. Runtime is **TBD**: use measured delivered Wh and average whole-device W, not nameplate mAh alone. Freeze demo duration plus reserve before testing.

**Defer custom packs/BMS:** they introduce charger/protection/load-sharing, mechanical, thermal and qualified assembly work beyond this brief. No cell assembly or lithium wiring recipe. Inspect selected-bank safety/temperature documentation; damage, swelling, unusual heating/odor or repeated protection trips stop use.

## Measure before CAD freeze

- **Packaging:** compare 7-/9-/10-inch-class inert mockups and battery beside-versus-behind compute. Full outer dimensions, mass, tolerances and cost remain TBD. Do not open a bank to meet a thickness target.
- **Cables:** record actual plug shells, strain relief, insertion/removal sweeps and manufacturer bend limits for HDMI, USB, PD and coax. Numeric bend radii are TBD; verify right-angle orientation and adjacent-port clashes.
- **Thermals:** retain unobstructed fan intake/exhaust, isolate the battery from hot components/exhaust and measure SoC/SSD, bank, connector and user-contact temperatures under repeated workloads. Material grade, ventilation area, wall thickness and safe operating limits require review before freeze; no printed-plastic fire/IP/drop certification claim.
- **Mechanical:** use a serviceable tray/cradle, no glass point loading or battery compression, and no screws toward the bank. Keep recovery, bank indicators and service access available without dismantling the screen.
- **RF:** reserve antenna zones away from battery metal, heatsink, LCD backing, fasteners, high-speed cables and normal hand grip; avoid conductive print material there. Numeric keepouts, exact antenna dimensions/band and coax limits are TBD from antenna guidance and measurements. Heltec V4’s FPC/IPEX 2.4 GHz path is distinct from LoRa. [Heltec documentation](https://docs.heltec.org/en/node/esp32/wifi_lora_32/index.html)
- Later authorized RF testing must compare open/closed enclosure and hand grips with the same permitted profile. USB detection is not range/delivery proof.

## Bounded next steps

1. **Now:** parent agrees demo variation, reviewers, duration/reserve and budget; compare mockups and obtain exact SKU drawings/quotes. No purchasing authorized here.
2. **Unit 3 only, after approval:** validate display/touch, USB mic, independent identities, five cold boots and five offline known-context PTT-to-text loops; establish open-stack power/thermal/audio baseline.
3. **Before CAD/duplicate approval:** measure cables/fit, closed-case thermals, battery duration and source-change safety; verify readable long answers and recovery. Test enclosed RF or explicitly withhold that demo claim.
4. **Then Unit 4:** separately approve duplicates of the accepted configuration; run independent acceptance. Leave December/January rehearsal margin and preserve reference-device fallback rather than waive safety.

**Costs/availability:** all landed totals, stock and delivery dates TBD; include new compute, peripherals, battery, cables, print iterations, assembly, test equipment and shipping/tax. This is not a procurement plan.

**Work performed:** repository review and primary-source browsing only. No purchase, live hardware action, CAD, compatibility/runtime measurement, Site edit or backlog edit.
