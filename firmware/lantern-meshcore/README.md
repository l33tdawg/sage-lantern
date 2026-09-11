# Lantern MeshCore packet-RSSI candidate

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
