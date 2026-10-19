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

## Loopback bridge and Maps UI — 19 October 2026

Unit C now runs `sage-lantern-gnss-status.service` on
`127.0.0.1:8090`. It queries gpsd with a JSON `WATCH`, returns bounded TPV/SKY
status, and exposes coordinates only for a fresh mode 2/3 fix when
`/status?position=1` is explicitly requested. Mode 1, missing time, stale data,
malformed gpsd output and a gpsd outage all fail closed. The service is
loopback-only and sends CORS headers for the `file://` Maps page.

- Bridge: `software/gnome/gnss/status_bridge.py` (`e414bdf6…`)
- Unit: `software/gnome/gnss/sage-lantern-gnss-status.service` (`b38df316…`)
- Maps UI: `software/gnome/desktop/maps.html` (`3efcb6f1…`)

Live readback with the antenna still open:

```text
{"ok":true,"has_fix":false,"mode":1,"satellites_used":0,
 "satellites_visible":0,"hdop":25.5,"stale":true}
```

Headless Chromium on C rendered the Maps page with **No GNSS fix**, mode **1**,
satellites **0 / 0**, HDOP **25.5** and bridge **Live**. The privacy toggle is
off by default; `?position=1` returns no coordinates while there is no fix.

The offline pack planner is
`software/gnome/maps-pack/build_pack.py` (`49e2ae68…`). A Penang test plan
(bbox `100.20,5.20,100.50,5.50`, zoom `10-13`) estimated 80 tiles / 960,000
bytes; the real Planetiler build is recorded below.

### Penang pack and local renderer — 19 October 2026

The first real pack is built from the 252,129,357-byte Geofabrik
Malaysia-Singapore-Brunei extract (`bc65fdec…`) with Planetiler 0.10.3. The
Penang bbox produced **90 tiles / 2,766,338 bytes** at zoom 0–13:

- Pack: `/opt/sage-lantern/maps/penang.pmtiles`
- Pack SHA-256: `15d9a3ec13f6dd1664beb4807a72417fd7beac4ba0aaef44b9757c605272984d`
- Manifest: `/opt/sage-lantern/maps/penang.pmtiles.manifest.json`
- Range server: `sage-lantern-maps.service` on `127.0.0.1:8091`
- Server SHA-256: `beb359a5…`; unit `c8f731f2…`

The server is loopback-only, supports HTTP Range with a 16-byte first range
returning `PMTiles` magic/version 3, and exposes CORS for the `file://` page.
MapLibre GL JS 4.7.1 and PMTiles JS 3.2.1 are vendored locally; no external
tiles are requested. The Maps page (`c8fbad00…`) uses
`pmtiles://http://127.0.0.1:8091/penang.pmtiles` with a minimal water,
landuse, road and building style and the required
“© OpenMapTiles © OpenStreetMap contributors” credit.

A 1560×720 headless Chromium render on C shows the Penang local map, the
no-fix GNSS panel (mode 1, 0/0 satellites, HDOP 25.5, bridge Live), the
privacy toggle off, and the installed-pack status. The screenshot is
`work/lantern-maps-20261019/maps-1560-final.png`; this is a software/render
check, not physical panel or antenna acceptance. The antenna is still open and
no fix is claimed.

## Verification

```sh
python3 -m pytest -q scripts/tests/test_cf_gnss_replay.py
```

38 tests cover checksum/framing, coordinate/date boundaries, no-fix/dead reckoning,
unsupported talkers, old/future/repeated timestamps, untrusted UTC, midnight,
receipt rollback, silence expiry, rejected-future recovery, nonfinite/duplicate
JSON, bounded input and absence of hardware/current-position acceptance.
