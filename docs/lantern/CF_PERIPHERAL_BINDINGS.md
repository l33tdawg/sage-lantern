# C–F peripheral binding handoff

**Current radio direction (9 October 2026):** Each C/D/E/F user Lantern uses one LoRa radio for the Meshticulum baseline; the second radio is optional and must justify itself in measured comparisons. Record each fitted radio's identity and independent host binding; qualify coexistence when two are fitted. GPS and WiFi via the Orange Pi remain separate peripherals. A/B each have **one** LoRa chip and serve Lamplighter/admin/knowledge/repeater support roles. See [device roles](DEVICE_ROLES.md).

27 September 2026. This is the software side of the hardware handoff to Flint.
No Orange Pi pin numbers, overlays or wiring are approved here. The exact
microphone carrier, camera module/FPC and HAT revision are not yet inspected.
Flint reports six Seeed XIAO ESP32S3 + Wio-SX1262 kits ordered on 27 September
(DigiKey 1597-102010611-ND). Arrival, actual board revision and USB identity
remain unverified; the XIAO image remains an unflashed candidate. C is the first
qualification unit, followed by D/E/F. G/H's radio purchase was brought forward;
their builds remain outside the current four-unit scope.

## Shared header worksheet

For every signal, record: component SKU/revision and pin label; physical Orange
Pi header pin; RK3588 controller/pad/function; voltage; input/output direction;
boot/default level; required pull/strap; kernel overlay and owning driver;
software consumer; and conflicts with every other row. A Raspberry Pi BCM
number is not an Orange Pi assignment. The 40-way ribbon does not establish
signal or voltage compatibility.

The HAT's actual routed nets occupy their header pins even where no software
consumer is enabled. Record them as occupied-by-HAT; absence of a PPS consumer
does not make a connected pin free for I2S or another peripheral.

| Component | Signals to resolve | Software requirement before activation |
|---|---|---|
| I2S microphone | Supply/ground, BCLK, WS/LRCLK, microphone SD output → host input; explicit SEL/LR strap and exact-carrier requirements | Bound ALSA capture device, supported channel/sample format, correct channel selection, level/clipping and silence checks; one existing capture owner |
| SX1262 HAT | SPI SCLK/MOSI/MISO/CS plus reset, busy, DIO/interrupt and RF-switch controls according to the actual schematic | Identified driver/userspace owner and exact radio protocol; no assumption that raw SPI accepts USB MeshCore frames |
| HAT GNSS | Supply/ground, receiver UART TX and any required RX; reset/enable/standby controls and PPS if used | Confirm electrical levels and actual serial framing/baud; establish receiver protocol and read-only consumer before configuring it |
| Cooler | Board fan connector and actual voltage/control requirements | Observe fan behavior and thermals under load; no assumed spare header power pin |

The microphone's five named nets must not be routed in a four-conductor cable.
Selection/decoupling may be onboard a carrier or need separate connections;
resolve the actual carrier schematic first. A registered I2S driver is not a
working ALSA capture binding. Existing USB serial-based microphone admission
must not be satisfied with a fabricated USB identity.

## Platform codec capture path (onboard microphone) — proven on C 2026-09-29

The Orange Pi 5 Plus's onboard microphone is wired to the ES8388/ES8323 codec's
LINPUT2 (the device tree's "Main Mic"), not to an I2S HAT. The board boots with
the capture path on a **differential line input at 0 dB gain**, where the
microphone is inaudible — that is why the first C bring-up recorded only
silence. The reviewed path is applied and verified at boot by
`sage-lantern-audio-capture.service`
(`scripts/lantern/set-platform-audio-capture.py`, installed as
`/usr/local/libexec/sage-lantern-set-platform-audio-capture`):

| Control | Value |
|---|---|
| Left PGA Mux | Line 2L |
| Right PGA Mux | Line 2R |
| ADC Data Select | Left Right |
| Main Mic | on |
| Headset Mic | off |
| Capture Digital | 192 (0 dB) |
| Left Channel / Right Channel | 8 (+24 dB analog PGA) |
| Capture Mute | off |

Measured on C with that path (ALSA `hw:CARD=rockchipes8388,DEV=0`, 16 kHz
stereo, as the service account): speech peaks −14.5 dBFS, ambient floor
≈ −41 dBFS, and real speech transcribed on the board by
`whisper-cli` + `ggml-base.bin`. The unit's stored ALSA state was a bench
stopgap only; the service is the reviewed mechanism because fresh units have no
stored state and WirePlumber starts later in the session. The 3.5 mm jack's
headset-mic path stays available and is left off; nothing here claims camera,
thermal or enclosure audio acceptance, and the runtime voice lane still owns
level/clipping/silence checks and the single-capture-owner rule once activated.

## GNSS scope

A source search on this date found no GNSS capture service or PPS consumer in
`software/runtime/src` or `scripts/lantern`; contact handling explicitly discards
GPS/route metadata. Therefore **PPS is not a current runtime dependency**, and
no application requirement to drive GNSS reset, enable or UART RX is established
by the existing code. This does not mean those pins may float: the HAT schematic
must establish safe defaults and required electrical connections.

**Live C update (18 October 2026):** the L76K HAT TXD/RXD are UART6_M1 on
header pins 8/10. The `meshticulum-sx1262-hf` overlay enables `&uart6`;
`/dev/ttyS6` emits 9600-baud NMEA and `gpsd 3.27.5` is enabled on it with
`USBAUTO=false`. The first indoor reading is TPV mode 1 / 0 satellites and the
receiver reports `ANTENNA OPEN`; connect the antenna and move to open sky. This
is serial/service proof, not a fix or location result. PPS remains unused.

An [offline NMEA replay validator](CF_GNSS_REPLAY.md) is now available for
synthetic/captured-output checks. It does not add a live receiver, PPS consumer
or serial ownership and never labels replay coordinates as verified current.

Flint reports the antenna selection as passive and the HAT as remote-mounted
on a 100 mm IDC ribbon, with ribbon/socket still unordered. These are design
choices, not GNSS fix, reception, electrical or fit acceptance. First receiver
qualification should establish serial output, valid fix/time semantics and
stale/no-fix behavior. Adding location or time to the product needs an explicit
consumer and freshness/privacy policy; no GPS data is published automatically.

## Camera handoff

Identify the actual IMX519 module SKU/revision, lens and autofocus mechanism,
board/mounting geometry, CSI connector count/pitch/orientation and a complete
cable/adapter net map. Orange Pi's documented OV13850/OV13855 flow does not
establish IMX519 support. The earlier 15-pin CSI claim is withdrawn pending
primary hardware evidence; a 30-pin LCD/DSI description must not be reused as
the CSI specification.

Before any camera service is enabled, require target-kernel sensor/CSI/ISP
support, device-tree regulators/reset/clock/I2C configuration, media graph and
actual capture/format evidence. CSI video node numbering may vary; existing USB
`/dev/v4l/by-id` admission is not a CSI binding. Bind the observed hardware path
and format under the existing camera owner without opening a second producer.
The NPU text service's CPU photo fallback is a separate inference gate after
capture works.

## First-boot evidence

Run [the read-only collector](CF_FIRST_BOOT.md). It now records ALSA inventory,
I2S/CSI/sensor driver names, media/V4L nodes, onboard UART/SPI/PPS names and native
build prerequisites. Those observations remain unqualified until the worksheet,
actual kernel/device bindings and functional checks agree. The collector never
captures audio/video, opens a serial/SPI/GPIO device, transmits RF or writes
configuration. Preserve unknowns instead of choosing pins or identities to make
a test pass.

Primary references: [Orange Pi 5 Plus camera and header documentation](https://www.orangepi.org/orangepiwiki/index.php/Orange_Pi_5_Plus),
[Waveshare HAT documentation](https://www.waveshare.com/wiki/SX1262_XXXM_LoRaWAN/GNSS_HAT),
and [microphone breakout signal definitions](https://learn.adafruit.com/adafruit-i2s-mems-microphone-breakout/pinouts).
These references do not identify the unboxed parts or approve an Orange Pi net map.
