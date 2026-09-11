# SAGE Lantern Hardware–Software Integration Contract

**Status:** Unit A display, touch, USB audio capture/playback transport, and radio enumeration measured on 2026-09-02. Physical PTT and wheel values remain provisional.

The purpose of this document is to prevent the beta from depending on unstated hardware assumptions. Exact device paths, protocols, and schemas must be filled in from measured bring-up results.

## Ownership boundary

### Hardware owner

- procurement and arrival truth;
- component revision and compatibility checks;
- physical assembly, cooling, and power;
- cable and GPIO mapping;
- peripheral enumeration evidence;
- radio hardware/firmware installation support;
- physical controls;
- thermal, runtime, and dimensional measurements;
- enclosure inputs.

### SAGE/software owner

- SAGE runtime and API behavior;
- local model/service orchestration;
- identity, authorization, and memory semantics;
- application state machine and persistence;
- software packaging, startup, recovery, and logs;
- payload validation and safe handling.

## Public Commons role boundary

The Lantern has two SAGE-facing roles:

1. a sovereign private/personal node for local memory, voice context, and offline operation; and
2. an ordinary signed proposer/query client for the public SAGE Commons.

It is not a validator on the public Commons chain. Public validator keys and voting power remain on stable, always-on governed servers. A public proposal must cross an explicit intake boundary and carry provenance, locality, observation time, confidence, and freshness/expiry metadata. Offline proposals remain visibly queued until the public network returns authoritative submission and decision state.

### Mynah/voice-bridge stakeholder

- voice-bridge application behavior where used;
- capture/playback integration above the verified device interface;
- interaction-state coordination with the application.

## Required application states

The UI and logs shall represent at least:

```text
idle
listening
transcribing
thinking
speaking
mesh_sending
mesh_receiving
sync_queued
degraded
error
```

Transitions must be event-driven and logged. A hardware disconnect must not be shown as indefinite `thinking` or `listening`.

## Provisional interfaces

| Interface | Hardware endpoint | Software expectation | Exact value/status |
|---|---|---|---|
| Display | HDMI | Stable framebuffer/display output | Unit A: DRM `card0-HDMI-A-2`, X11 `HDMI-2`, active 640×480 after unattended reboot |
| Touch | USB data from display | Standard pointer/touch events | Unit A: X input `WaveShare WS170120`, present after unattended reboot |
| Audio | Waveshare ESP32-S3 audio board | ALSA capture/playback | Unit A: firmware VID:PID `303a:8000`, serial `SAGE-LANTERN-A-AUDIO`; standard `/dev/snd/by-id/usb-SAGE_Commons_SAGE_Lantern_Audio_SAGE-LANTERN-A-AUDIO-00`; ALSA card ID `Audio`; fixed 16 kHz, stereo, signed 16-bit PCM capture/playback verified. Recovery serial appears as `/dev/sage-lantern-audio-flash` in ROM/factory mode. |
| PTT | Dry-contact button to ROCK header physical pin 27 and GND | Active-low `ptt.down` / `ptt.up` via gpiochip4 line 22 with pull-up and 25 ms debounce | Software adapter installed; physical wiring/press-release acceptance remains `OPEN-LAN-004`. GPIO is 3.3 V only; never inject 5 V. |
| Wheel | ROCK GPIO or small intermediary | CW, CCW, press events | `OPEN-LAN-004`: hardware/runtime owners freeze pins and debounce after Unit A wiring test, before G1 |
| Mesh companion | Heltec V4 over USB | Framed companion send/receive API | Unit A: exact-serial `/dev/sage-lantern-radio`, MeshCore companion v1.17.1, identity `SAGE-LANTERN-A`, 917.375 MHz; two-node transport characterization remains pending Unit B |
| Power status | Power bank/OS-visible data if available | Optional telemetry | Do not require for beta acceptance unless observable |

## Input event contract

The hardware adaptation layer should expose semantic events rather than raw edges:

```text
ptt.down
ptt.up
wheel.clockwise
wheel.counter_clockwise
wheel.press
peripheral.connected
peripheral.disconnected
```

Each event should include a monotonic timestamp and device/unit identity. Raw GPIO debounce belongs below this boundary.

## Voice-session contract

A voice session needs a correlation identifier shared by UI and logs.

Minimum lifecycle:

```text
session.created
capture.started
capture.completed
transcription.completed | transcription.failed
agent.completed | agent.failed
tts.started
tts.completed | tts.failed
session.completed | session.failed
```

Timing is captured at each boundary. `OPEN-LAN-006` requires the audio owner to freeze format, sample rate, channel count and buffering strategy from verified Unit A audio-board evidence before G1.

## Mesh payload requirements

MeshCore is a constrained transport adapter. Radio identity and labels are not SAGE authority. The first cross-device payload must be deliberately small and versioned. The provisional envelope name is `sage.lantern.message.v1`; the software team will finalize the schema after the hardware owner measures the actual transport behavior.

The envelope must include:

- schema version;
- message identifier;
- source SAGE agent identity;
- destination or audience;
- creation time or monotonic sequence;
- payload type;
- bounded content;
- TTL;
- signature or integrity metadata;
- delivery/processing state.

The receiver must treat all incoming content as untrusted data. It must reject oversized, malformed, expired, unsupported, duplicate, or unauthorized envelopes before exposing content to an agent flow. Deduplication uses source agent identity plus message identifier. Queued transport must be shown as `sync_queued` or degraded, never as delivered or read.

The SAGE-facing boundary uses canonical `/v1/messages` semantics. Deprecated public pipe interfaces are not a beta dependency.

Before the envelope is finalized, hardware evidence must establish:

- usable host-to-Heltec protocol and framing;
- maximum reliable payload size;
- fragmentation and reassembly behavior;
- retry and timeout behavior;
- duplicate delivery behavior;
- reboot and reconnect behavior.

Record those findings in `MESHCORE_HOST_PROTOCOL_TEST_PLAN.md`.

## Logging requirements

Every beta acceptance run should allow correlation across:

- physical input event;
- audio capture/playback;
- STT;
- local agent/LLM;
- SAGE access;
- TTS;
- MeshCore send/receive;
- peripheral disconnect/reconnect;
- process restart and boot.

Logs must avoid recording secrets or unnecessary raw private audio. Debug audio capture should be explicit and temporary.

## Startup and recovery

- Services must tolerate peripherals enumerating in a different order.
- Stable identifiers should be used where the OS provides them.
- Missing audio or radio hardware should produce a visible degraded state.
- Reconnecting a peripheral should not require reimaging the unit.
- Unit identity and radio identity must not be duplicated when Unit B is cloned.
- Provision unique ordinary Member or Manager SAGE identities only after cloning.
- Root is not a messaging principal and Root/Admin keys must never be placed in the shared image.
- Never clone `~/.sage`, receive tokens, idempotency state, message journals, or radio identity.
- Record the exact SAGE version and commit used for every acceptance run; the current minimum baseline is v11.18.4.

## Integration requests to software stakeholders

No action is requested at document creation time.

The hardware owner will send a focused request when one of these is available:

1. verified audio interface and sample data;
2. verified PTT/wheel event adapter;
3. verified Heltec/MeshCore host interface;
4. measured platform performance and model constraints; or
5. a reproducible hardware failure that requires a software change.

Each request should include unit, revisions, wiring, software version, reproduction steps, logs, and the smallest concrete decision needed.
