# Lantern GNSS status bridge

Loopback-only bridge between `gpsd` and the desktop Maps page. It never binds a
LAN address, never publishes coordinates by default and never stores a track.

## API

- `GET /health` → `{"status":"ok"}`
- `GET /status` → fix state, mode, satellites, HDOP, accuracy and age. No
  latitude or longitude.
- `GET /status?position=1` → adds latitude, longitude, altitude, speed and
  track only when there is a fresh mode 2/3 fix.

`has_fix` requires TPV mode 2 or 3 and a timestamp no older than 10 seconds.
Mode 1, missing time, malformed gpsd data and a gpsd outage all fail closed.
The response includes CORS headers so the `file://` Chromium app can fetch it.

## Install on Unit C

```sh
sudo install -D -m 0644 software/gnome/gnss/status_bridge.py \
  /opt/sage-lantern/gnss/status_bridge.py
sudo install -D -m 0644 software/gnome/gnss/sage-lantern-gnss-status.service \
  /etc/systemd/system/sage-lantern-gnss-status.service
sudo systemctl daemon-reload
sudo systemctl enable --now sage-lantern-gnss-status.service
curl -s http://127.0.0.1:8090/health
curl -s http://127.0.0.1:8090/status
```

The unit runs as `sage`, reads gpsd at `127.0.0.1:2947`, and binds only
`127.0.0.1:8090`.

## Rollback

```sh
sudo systemctl disable --now sage-lantern-gnss-status.service
sudo rm /etc/systemd/system/sage-lantern-gnss-status.service
sudo systemctl daemon-reload
```

## Tests

```sh
.venv/bin/pytest software/gnome/gnss/test_status_bridge.py -q
```

Tests use a fake gpsd reader and do not require a receiver or a bound port.
