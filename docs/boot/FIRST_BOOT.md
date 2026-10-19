# First boot

This is the owner setup path for a provisioned C-F unit. Do not use a default
password from a document. The owner password and PIN are set during
provisioning and belong in the owner's password manager.

## Before power

- Check that the panel, Ethernet and power path are connected.
- Keep the unit on a non-conductive bench.
- Have the unit's provisioning record available. It names the unit and the
  identity material that was generated for it.
- Do not insert another unit's SD card or clone its home directory.

## 1. Power on

Apply the fixed 5 V/4 A supply. Watch the panel. A healthy first boot reaches
the portrait kiosk. If the unit resets or the panel stays dark, stop and go to
[Recovery](RECOVERY.md).

## 2. Unlock

Unlock with the Lantern PIN created for this unit. The PIN is not the owner
account password and is not stored in the repository.

## 3. Network

Ethernet is the simplest first connection. For Wi-Fi, follow
[First install setup](../lantern/FIRST_INSTALL_SETUP.md): forward the local
admin ports over SSH, open the setup page on loopback, start the broker on the
unit and paste the short-lived setup capability. The broker accepts visible
WPA2 networks and does not support WPA3-only, enterprise or hidden networks.

Wi-Fi association is not proof of internet reachability. Check the reported
SSID and then test a local service.

## 4. Device name and display

Use `Settings -> Your Lantern -> Change name` to set the display name. This does
not rotate keys or delete memories.

Set the screen-off and sleep preferences. The defaults are 30 seconds for the
screen and 5 minutes for deep sleep. Deep sleep is saved as a preference but
remains unavailable as an automatic action.

## 5. Owner account and recovery

The desktop login is the `sage` account with the owner's Lantern password. D-022
requires a fresh login before the desktop is exposed. Store the password and
recovery material in the password manager, not in this repository.

If the unit was provisioned with recovery media or a recovery code, verify that
it is present and readable before relying on it.

## 6. Service check

```sh
systemctl is-active sage-lantern-llm.service sage-lantern-voice-loop.service \
  sage-lantern-gnss-status.service gpsd
curl -fsS http://127.0.0.1:8091/packs.json
curl -fsS http://127.0.0.1:8090/status
```

The map endpoint should list the installed packs. The GNSS endpoint should
return a status even when there is no fix. A missing fix is normal indoors.

## 7. First conversation

1. Ask a short factual question in the device UI.
2. Ask a question that needs a saved label or note, then correct the label and
   ask again.
3. Hold the physical PTT control and ask a spoken question.
4. Take a photo and ask a question about it.
5. Open Maps and confirm the world base and any installed pack render.

Record what worked and what failed. Do not describe a service start as a
conversation result.

## 8. Desktop mode

Attach a USB keyboard. The mouse is optional. Log in as `sage` with the owner's
Lantern password. The desktop has shortcuts for CEREBRUM at
`http://127.0.0.1:8080/ui/`, Maps, the Lantern Console and
`Return to Lantern Portrait`.

`sage-lantern-desktop-mode off` forces portrait. `auto` returns to the
keyboard-triggered rule.

## 9. Still to accept

- Touch mapping at all four corners and the centre.
- Panel rotation direction.
- Cold and warm start, and disconnect behaviour.
- Same-instance sleep and wake.
- A real GNSS fix with mode 2 or 3, satellites and HDOP.
