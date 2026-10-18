# A/B operator runbook

**A/B have screens and a Lamplighter dashboard.** Both existing desktop sessions
open it automatically. The same dashboard is available remotely; SSH commands
below remain the administration fallback. See [dashboard operation](AB_DASHBOARD.md).

A/B are ready to support C's bring-up on the morning of 28 September 2026.
Both public pack seeds survived the 27 September controlled reboots. Their
native personal SAGE stores remain deleted and old endpoint services remain
inhibited. [Device roles](DEVICE_ROLES.md) defines the hardware/use-case split.

## Morning check

From the management Mac, using the existing SSH aliases and keys:

```sh
ssh lantern-a 'sage-lamplighter-admin status'
ssh lantern-b 'sage-lamplighter-admin status'
ssh lantern-a 'sage-lamplighter-admin check-peer'
ssh lantern-b 'sage-lamplighter-admin check-peer'
```

`status` exits zero only when the seed is enabled/active, its catalogue matches
the configured manifest, the wipe receipt exists and all installed old endpoint
services remain stopped/inhibited. It reports the current boot ID. An ordinary
user cannot inspect the former private directory; `personal_sage_absent_now`
is then null, while `personal_sage_wipe_recorded` reports the completed receipt.
The root checks in the closeout evidence independently verified current absence.
Status alone does not verify cached chunks or radio delivery.

`check-peer` verifies the other unit's signed pinned manifest and one sample
chunk. It is a quick availability/integrity check, not a full transfer. To verify
all 5,686 chunks locally:

```sh
ssh lantern-a 'sage-lamplighter-admin verify-cache'
ssh lantern-b 'sage-lamplighter-admin verify-cache'
```

If a seed needs restarting, use the existing sudo authorization interactively:

```sh
ssh -t lantern-a 'sudo sage-lamplighter-admin restart-seed'
```

The command validates the full cache before restarting only
`sage-lamplighter-seed.service`, then waits for readiness. Ordinary user restart
is denied. This is the working scoped SSH administration surface. The Lamplighter
web dashboard handles packs and device monitoring; the old endpoint Fleet Admin stays off.
Do not start `sage-lantern-startup`, `personal-sage`, `mesh-runtime` or the old
model/voice services to operate these seeds.

## Network and files

| Unit | Seed base URL | LoRa hardware |
| --- | --- | --- |
| A | `http://192.168.50.103:18090` | One Heltec USB companion, r7 |
| B | `http://192.168.50.96:18090` | One Heltec USB companion, r7 |

Both expose `/catalogue.json`. Public code is under
`/opt/sage-lamplighter-lab/v1/`, public cache under
`/var/lib/sage-lamplighter-lab/world-facts-lite-v3/`, and operator bindings in
`/etc/sage-lamplighter-lab/operator.json`. The service binds its configured LAN
address and allows `192.168.50.0/24`; moving networks requires an explicit
service/configuration update. This bench setup does not discover a new DHCP IP.

The independently supplied expected manifest SHA-256 is:
`9ec8c8351846383c1fe082d66e1667197be6a128991c9561bb5db87fe64a4a3b`.
The publisher trust root is provisioned separately, never learned from a seed.
See [the fetch command](LAMPLIGHTER_AB_LAB.md#operator-use) for a prepared client.

## C first session

Start with [C's read-only first-boot inventory](CF_FIRST_BOOT.md), its actual
IP/SSH account and Orange Pi hardware bindings. When the matched client and
trusted public root are provisioned, C can fetch the public pack from A/B,
verify it, interrupt/resume and test loss of either carrier. Fetching never
silently imports it into C's private SAGE. C generates independent identities.

Use the third physical radio to qualify repeater forwarding and a routed path;
then test the actual dual-radio/GPS/WiFi setup as peripherals become available.
A/B's single-radio topology cannot prove an isolated three-node repeater path.
LoRa pack discovery/control, bounded payloads, fast-LoRa profiles and airtime
coexistence remain explicit integration work. Both r7 radios previously passed
500 contact reads; the reboot check established USB presence, not a fresh RF
or firmware handshake. No private endpoint acceptance remains to run on A/B.

Evidence: [closeout](../../work/ab-closeout-20260927/README.md) and
[pack/radio preparation](../../work/ab-support-lab-20260927/README.md).
