# Lantern MeshCore patches

## client-repeat-917.patch — r4 node + repeater, built 2026-09-19

Lets the companion firmware enable its own repeat bit on the commissioned
917.375 MHz channel. The forwarding path already exists in the companion
(`MyMesh::allowPacketForward()` → `_prefs.isRepeatEn()`); the frequency
allowlist in `MyMesh::isValidClientRepeatFreq()` was the only blocker, and this
patch sets upstream's own `ALLOWED_REPEAT_FREQ_RANGE` to exactly `{917375,917375}`
in `[env:heltec_v4_companion_radio_usb]` and identifies the build as `r4`.

Image built with PlatformIO 6.2.0 from the pinned d9296435 tree with the r3
patch set applied: 641,936 bytes, sha256
`3d63685fe82d1e5e1d9e309b91745dfa3799a70f72c56a667b07482e48d7239a`, version
string `v1.17.1-d929643-r4`. Host admission for r4 ships in the same change set
(`meshcore_transport.SUPPORTED_FIRMWARE`). Repeat stays off until the repeat bit
is written through `CMD_SET_RADIO_PARAMS`; see
`docs/lantern/MESH_STORE_AND_FORWARD.md` for the duty-cycle gate that must be
settled before it is switched on. Build record:
`work/mesh-repeat-sf-20260919/README.md`.

## companion-bridge.patch - submitted upstream 2026-09-14

Lets a **companion** radio use a bridge, which is the piece Lantern's two-tier
plan needs: LoRa stays the long-range transport and ESP-NOW carries the same mesh
packets locally at 2.4 GHz.

Upstream had ESP-NOW as a mesh radio (`variants/generic_espnow`) and as a
repeater bridge (`heltec_v4_repeater_bridge_espnow`), but never as a companion -
and it was not an oversight. The bridges take `NodePrefs*` meaning the CLI class
in `helpers/CommonCLI.h`, the companion has its own unrelated `NodePrefs`, and
including both in one translation unit fails outright:

```
src/helpers/CommonCLI.h:25:7: error: redefinition of 'class NodePrefs'
```

The patch extracts the six bridge settings into `BridgePrefs` (both `NodePrefs`
classes inherit it, so every existing `_prefs.bridge_*` call site is unchanged
and `BridgeBase` no longer includes `CommonCLI.h` at all), then wires the bridge
into the companion using the `logRx`/`logTx` hooks the companion never
overrode. Adds `heltec_v4_companion_radio_usb_espnowbridge`.

Upstream pull request: **meshcore-dev/MeshCore#3410**, against `dev`. Not merged
as of 2026-09-14. Built clean on four environments (new bridge companion, plain
companion, nRF52 companion, nRF52 repeater); the existing ESP-NOW bridge
*repeater* environment could not be built here because its `ESPAsyncWebServer`
download kept failing on this machine - it needs a CI check. **Not hardware
tested.**

## contacts-response-atomic.patch — submitted upstream 2026-09-13

Fixes the defect that latched unit A's contact reads (see
`work/messages-ab-20260913/DIAGNOSIS_20260913.md` section 11.2). A
`CMD_GET_CONTACTS` reply is streamed one frame per `loop()` pass, while the
packet callbacks push frames (`0x80`, `0x81`, `0x82`, `0x83`, `0x8A`, `0x8F`,
`0x90`, raw/trace/log data) from that same `loop()` in between, so a push lands
inside the response. The patch holds pushes in a bounded FIFO while the iterator
is running and flushes them in order once it ends.

Upstream pull request: **meshcore-dev/MeshCore#3403**, against `dev`. Not merged
as of 2026-09-13. Where it landed is not what matters for us: the point is that
the interleaving is a firmware defect, not a host parser choice.

Base for the patch is upstream `dev`, not the pinned `d9296435` the units run, so
`git apply` it against a `dev` checkout:

```
git apply /path/to/contacts-response-atomic.patch
pio run -e heltec_v4_companion_radio_usb
```

Built for `heltec_v4_companion_radio_usb` (ESP32-S3) and
`RAK_4631_companion_radio_ble` (nRF52): RAM +1,416 bytes, flash +232 / +1,280
bytes. **Not hardware tested**, and not deployed to either unit.

Applying it to our units is a sequence, not a flash: the host admits firmware by
exact version string (`meshcore_transport.SUPPORTED_FIRMWARE`), so a patched build
needs a host admission change in the same release, plus the app-partition
preservation steps in the packet-RSSI section below. Do not flash A before the
read latch is understood end to end.

## Lantern MeshCore packet-RSSI candidate

Base: MeshCore `d92964352441e53b93e8667b802e04f6e072b39e`.
Target: `heltec_v4_companion_radio_usb` (OLED).
Candidate identity: `v1.17.1-d929643-r2`.

`packet-rssi.patch` selects the SX126x packet RSSI field rather than the
signal RSSI field used by the pinned RadioLib `getRSSI()` implementation, and
captures RSSI/SNR immediately after a valid receive, before `startReceive()`.
This is a diagnostic candidate, not a hardware-verified correction.
The wire layout and RF settings are unchanged.

The September 9 local build succeeded. App image SHA-256:
`a9c7c1db95e373b7b2a4f719abbf173c974749f15cd89e1407739a2b7f5935e8`.
Temporary build output:
`/private/tmp/lantern-meshcore-rssi/.pio/build/heltec_v4_companion_radio_usb/firmware.bin`.

Apply to the exact upstream checkout, then run:

```
git apply /path/to/packet-rssi.patch
pio run -e heltec_v4_companion_radio_usb
```

Do not flash the merged image or erase the device. Before any app-only update,
verify the actual app partition offset/size, preserve the existing identity and
settings partitions, stop the existing serial owner, and update the host's exact
firmware admission to recognize the candidate. B's app0 is at `0x10000`, size
`0x640000`; its OTA selector matches the standard factory app0 selector. The
original affected app region and boot/settings bytes are backed up locally on B
under `/var/backups/lantern-radio-rssi-b`, never exported. Hardware acceptance of
the r2 candidate remains incomplete.
After the owner unlocked B, r2 was flashed successfully with the first 64 KiB
of boot/settings/OTA metadata verified unchanged. The broker and voice services
were restored with the r2-compatible host code. Live logs identify r2 and show
repeated reception of A's pinned public radio key; A also receives B's key.
RSSI still varies between zero and unusually strong negative values (B: -8 dBm;
A on stock firmware: -11 and -4 dBm). This does not establish a reliable signal
measurement or validate either candidate as a fix. Do not deploy r2 to A as a
proven correction.

The r1 candidate (field-selection change only) booted on B with the expected
version and original radio key, heard A, and passed six audio-readiness checks,
but still reported zero RSSI. The later discovery that the upstream receive path
restarts listening before reading RSSI motivated r2; r1 is not a verified fix.

Hardware acceptance requires both configured public keys unchanged, unchanged RF
profile, bidirectional advertisements, plausible packet RSSI that responds to
distance, continuing audio readiness, and no fabricated freshness or neighbors.

References: [pinned RadioLib RSSI implementation](https://github.com/jgromes/RadioLib/blob/6d8934836678d8894e3d556550475b37dce3e2b6/src/modules/SX126x/SX126x.cpp),
[Semtech field decoding](https://github.com/Lora-net/sx126x_driver/blob/master/src/sx126x.c).
