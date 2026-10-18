# A/B Lamplighter dashboard

A and B are support devices with existing screens. Both open the separate
Lamplighter dashboard automatically through their current desktop autologin.
A hosts the controller; B's screen connects to A. The controller runs independently
of the management Mac. If A is unavailable, B's seed still serves packs, but the
central dashboard is unavailable until A returns.

## Open from the management computer

Use the existing SSH key and host alias:

```sh
ssh -N -L 127.0.0.1:18791:127.0.0.1:18791 lantern-a
```

Leave that terminal open, then open <http://127.0.0.1:18791/>. The SSH tunnel
provides local session access automatically. An already-running tunnel can be
reused. LAN access is <http://192.168.50.103:18791/> and requires the dashboard
access code stored privately on A at
`/var/lib/sage-lamplighter-dashboard/dashboard-token`. Use the SSH path from the
management computer; never put the code in documentation or shared screenshots.
The direct HTTP path is for this isolated bench LAN, not Internet exposure.

## Packs and progress

Choose **Send from** and **Receive on**, then **Distribute pack**. Data travels
directly between A and B. The approved catalogue currently contains only signed
`world-facts-lite` v3: 5,686 chunks, 2,298,544 bytes. New pack publishing/upload,
C–F targets and LoRa delivery are not implemented by this dashboard.

A fresh transfer downloads a new staged copy even when the destination already
has the pack. **Pause transfer** preserves verified chunks. **Resume transfer**
revalidates and reuses them. A stopped worker becomes **Interrupted**, and can be
resumed after reboot or service restart. One unfinished transfer per destination
is allowed. Progress stays below 100% until verification and publication finish.
Received-byte and reused-chunk counts describe the current/resumed attempt;
zero newly received bytes can mean a fully cached resume, not a zero-byte pack.

Publication replaces only the approved public pack with verified bytes. Files
remain readable by the separate seed service; transfer state stays private.
No personal SAGE import, publisher authority or trust-root change is involved.

## Monitor devices

A/B cards show seed reachability, free disk, temperature, load, available RAM,
uptime and recent screen heartbeat. Connected USB devices are read from each
host. The existing ESP32 USB radio adapters are labelled as LoRa companions;
USB presence does not establish RF reachability or firmware health.

LAN devices come from each host's neighbour cache. **Stale** means a cached
observation, not a currently reachable device. Unknown addresses stay unidentified;
they do not become trusted SAGE units. C–F must be explicitly onboarded before
appearing as managed transfer targets. LoRa neighbour polling and repeater
forwarding remain separate integration work.

## Installed components

- A: `sage-lamplighter-dashboard.service`, enabled on port 18791.
- Both: `/opt/sage-lamplighter-lab/dashboard-v1/` worker/code;
  `/var/lib/sage-lamplighter-dashboard/` private job state and screen bootstrap.
- A → B: dedicated SSH key with a forced JSON worker command; no shell,
  forwarding or arbitrary file paths.
- Screens: `/usr/local/bin/sage-lamplighter-kiosk`, dispatched by the existing
  kiosk only when `/etc/sage-lamplighter-lab/enabled` is present. Separate Chromium
  profile: `/home/radxa/.config/sage-lamplighter/chromium`.
- Existing seeds retain port 18090 and their independently deployed code.

POST operations require the access code and matching browser origin. The
controller permits only fixed A/B worker operations and the approved manifest.
The old endpoint services remain inhibited and their native private stores
remain wiped. C–F provisioning does not inherit these support-unit settings.

[Operator checks](AB_OPERATOR_RUNBOOK.md) · [Role map](DEVICE_ROLES.md) ·
[Live verification](../../work/ab-dashboard-20260928/README.md)
