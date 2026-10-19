# Recovery

Recovery starts with evidence, not with a reset. If a unit is running but a
feature is broken, capture the state before changing it. If an uncertain write
or an unknown storage fence is involved, stop and preserve the unit.

## First checks

```sh
systemctl --failed
systemctl status sage-lantern-llm.service sage-lantern-voice-loop.service
journalctl -u sage-lantern-llm.service -n 100 --no-pager
```

Check the map server separately:

```sh
curl -fsS http://127.0.0.1:8091/packs.json
```

Check the GNSS bridge without requesting coordinates:

```sh
curl -fsS http://127.0.0.1:8090/status
```

## Restart one service

```sh
sudo systemctl restart sage-lantern-llm.service
sudo systemctl restart sage-lantern-voice-loop.service
sudo systemctl restart sage-lantern-maps.service
sudo systemctl restart sage-lantern-gnss-status.service
```

Restarting the model service does not restart the radio or the storage
supervisor. Do not stop the message broker while the radio is live.

## Desktop and kiosk

- `sage-lantern-desktop-mode off` returns the unit to portrait.
- `sage-lantern-desktop-mode auto` restores the keyboard-triggered rule.
- `Return to Lantern Portrait` in the app grid does the same from the desktop.
- If the desktop session is unusable, use the display manager's recovery path
  described in [Display layout](../lantern/CF_DISPLAY_LAYOUT.md). Do not bind
  RDP or VNC to the LAN.

## Map pack rollback

The pack manager keeps replaced packs in `.backups/` and never deletes a
verified pack in place.

```sh
python3 software/gnome/maps-pack/pack_manager.py list --root /opt/sage-lantern/maps
python3 software/gnome/maps-pack/pack_manager.py verify \
  /opt/sage-lantern/maps/malaysia-z11.pmtiles
```

To remove a pack, use `remove` and keep the backup. To restore an older pack,
copy the pair back from `.backups/<timestamp>/` through `pack_manager.py
install`.

## Release rollback

Keep the previous runtime wheel, model and guard paths, and the installed
release tree. Do not overwrite a running generation in place. The installation
and rollback contract is in
[Appliance stage contract](../lantern/APPLIANCE_STAGE_CONTRACT.md) and
[Real unit provisioning](../lantern/REAL_UNIT_PROVISIONING.md).

If a release swap fails, stop and compare the installed receipt with the
release manifest before starting the old tree. A numeric PID or a timer is not
proof that a process released its resources.

## Evidence preservation

Before a reboot or a destructive step, capture:

1. The unit name, date and time.
2. `systemctl --failed` and the relevant `journalctl` lines.
3. The output of the failing command.
4. Hashes of the installed model, runtime wheel, guards and map pack.
5. The release receipt and installed generation.
6. A photograph or screenshot of the panel state if it is a display fault.

`scripts/capture-lantern-evidence.sh evidence/<test-id>` is the standard
capture wrapper. Keep the evidence with the unit record.

## Stop conditions

- `storage_supervisor_unavailable` or an unknown write fence: do not delete the
  fence to make the service start.
- A radio transfer is live: do not restart the broker or stop the service.
- A model or guard regression is suspected: keep the previous release and do
  not install a new adapter.
- The panel, camera or power path is hot, damaged or repeatedly resetting:
  remove power and inspect before another attempt.
