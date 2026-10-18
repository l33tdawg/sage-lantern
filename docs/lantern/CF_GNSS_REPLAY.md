# C–F GNSS offline validation candidate

`scripts/lantern/cf-gnss-replay.py` validates captured or synthetic NMEA events
from JSONL stdin. It does not select/open a serial port, configure the HAT,
start a service, set the system clock, save locations or publish data. This is
separate from the frozen r2 appliance; no GNSS service has been installed.
Run it with Python 3.9 or newer; it needs no third-party runtime packages.

The parser follows the [L76K protocol specification](https://files.waveshare.com/upload/d/dd/Quectel_L76K_GNSS_Protocol_Specification_V1.1.pdf)
for checksum, RMC fields/status/mode and GGA quality. Actual HAT/receiver revision
and output framing remain first-board checks. This candidate handles the
specified GP/GN/GL/BD talkers; unsupported formats refuse rather than guessing.

## Observation contract

One JSON object per line, at most 4 KiB per line and 10,000 events:

```json
{"sentence":"$...*HH\r\n","received_monotonic":1.0,"received_utc":"2026-09-27T12:00:00Z","clock_trusted":true}
{"now":5.0}
```

The sentence placeholder above is deliberately not a valid fix. Synthetic valid
examples are generated in `scripts/tests/test_cf_gnss_replay.py`. Feed a reviewed
fixture explicitly:

```sh
python3 scripts/lantern/cf-gnss-replay.py < /path/to/receiver-fixture.jsonl
```

`received_monotonic` belongs to one receiver/session clock. `received_utc` and
`clock_trusted` are replay input assertions, not independent clock attestation.
Without a separately trusted UTC, an RMC record cannot become a valid sample.
The parser never establishes trust by comparing GNSS time with itself. A future
live owner must establish and bind that trust, device identity and receipt time;
replaying a capture now cannot establish current location.

## Position validity

- Checksum/framing, coordinate ranges, date/time, RMC fix status and mode must
  pass. Estimated/dead-reckoned or invalid fixes revoke the previous position.
- A new accepted receiver timestamp must advance. A bad/untrusted future date
  cannot poison the accepted timestamp watermark.
- Receipt freshness and trusted UTC agreement are both required. Default bounds
  are three seconds; they are candidate policy, not receiver accuracy claims.
- GGA may revoke a fix, but cannot create or refresh it because it lacks a date.
  Other messages do not refresh position age. This is not a full sensor-fusion
  parser or a complete NMEA implementation.
- An explicit `now` event tests expiry during silence. Stale positions, backward
  receipt clocks and malformed sentences return no position; old coordinates
  cannot reappear by moving the replay clock backward.
- Year interpretation is explicitly 2000–2099; leap-second text is rejected.
  Navigational status is retained separately and never treated as a navigation
  safety certificate.

Every output says `source: offline-replay`, `current_position_verified: false`,
`hardware_accepted: false` and `navigation_safety_verified: false`, including a
`valid_sample`. The sample's coordinates are not permission to publish a user's
location. Raw NMEA lines are not echoed in errors; valid coordinates are emitted
only to the chosen stdout stream. Keep real captures and output private.

A new Tracker starts a new replay session. Future live serial ownership,
clock/freshness binding, disconnect handling, UI/map integration and real
no-fix/indoors/cold-start tests are still required. Map source/licence, offline
map storage and routing remain separate backlog gates.

## Live receiver on C — 18 October 2026

The HAT is the L76K GNSS variant. Its TXD/RXD map to UART6_M1 on Orange Pi
header pins 8/10. The `meshticulum-sx1262-hf` overlay now enables `&uart6`;
UART2 remains the debug console and is not the GNSS path. `/dev/ttyS6` emits
9600-baud NMEA, and `gpsd 3.27.5` is enabled on that device with
`USBAUTO=false`.

The first indoor reading is **TPV mode 1, 0 satellites, HDOP 25.5**, and the
receiver reports **`ANTENNA OPEN`**. This is receiver/service proof, not a
location or navigation result. Connect the GNSS antenna and move to open sky,
then re-read `gpspipe -w`/`xgps` before making any fix, accuracy or map-position
claim.

## Verification

```sh
python3 -m pytest -q scripts/tests/test_cf_gnss_replay.py
```

38 tests cover checksum/framing, coordinate/date boundaries, no-fix/dead reckoning,
unsupported talkers, old/future/repeated timestamps, untrusted UTC, midnight,
receipt rollback, silence expiry, rejected-future recovery, nonfinite/duplicate
JSON, bounded input and absence of hardware/current-position acceptance.
