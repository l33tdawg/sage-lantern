# Boot and first run

This section describes what happens when a Lantern unit powers on, how the
owner gets in, and how to recover a unit that does not reach a usable state.

## The boot path

1. Power is applied through the unit's fixed 5 V/4 A path.
2. The board boots its installed operating system from NVMe.
3. The portrait kiosk starts and opens the device UI.
4. The owner unlocks the device with the Lantern PIN.
5. The local services start: personal SAGE, chat model, voice loop, status
   bridge, GNSS bridge and map server.
6. A USB keyboard, with the mouse optional, switches the unit to the GNOME
   landscape desktop. The desktop asks for a fresh login under D-022.

Portrait remains the default. The desktop is a desk mode, not a second product.

## Current state

| Area | State |
| --- | --- |
| Portrait kiosk | Installed on C. Physical touch and rotation acceptance open. |
| Owner unlock | Installed. The full fresh-install ceremony is still open. |
| Desktop trigger | Implemented with a USB keyboard. Physical acceptance open. |
| Chat model | V81 active on C, 2B rollback retained. |
| Voice loop | Active on C. Quiet and noisy capture acceptance open. |
| GNSS | Receiver live. No fix because the antenna is not connected. |
| Maps | World base and five packs installed on C. |
| Sleep and wake | Tested in source. Physical same-instance acceptance open. |

## Services on Unit C

| Service | Port | Purpose |
| --- | --- | --- |
| `sage-lantern-llm.service` | 8081 | Local chat model |
| `sage-lantern-vlm.service` | 8082 | Vision model lane |
| `sage-lantern-embed.service` | 8083 | Local embeddings |
| `sage-lantern-embed-shim.service` | 8084 | Ollama-compatible shim |
| `sage-lantern-personal-sage.service` | 8080 | Private SAGE and CEREBRUM UI |
| `sage-lantern-voice-loop.service` | local | Push-to-talk and spoken answers |
| `sage-lantern-gnss-status.service` | 8090 | Bounded GNSS status JSON |
| `sage-lantern-maps.service` | 8091 | Local PMTiles server and pack index |
| `gpsd` | local | L76K receiver on `/dev/ttyS6` |

The fleet-admin UI runs on loopback port 3100 in the normal device setup.

## Guides

- [First boot](FIRST_BOOT.md): owner setup and the first checks.
- [Recovery](RECOVERY.md): service, map and generation recovery.
- [First install setup](../lantern/FIRST_INSTALL_SETUP.md): the network and
  preference implementation.
- [Desktop mode](../lantern/DESKTOP_MODE.md): portrait and landscape behaviour.
- [SD and NVMe setup](../lantern/CF_SD_NVME_SETUP.md): the image path.
