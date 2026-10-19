# Software stack

This is the install order for a C-F unit. Commands marked "example" show the
shape of the operation; check the installed paths and the current release
record before running them on a unit.

## 1. Base image and NVMe root

Follow [C-F SD provisioning and NVMe root](../lantern/CF_SD_NVME_SETUP.md). The
path was verified on Unit C on 28 September 2026. It prepares a fresh SD image,
boots it, migrates the root filesystem to NVMe and then can remove the SD
dependency.

Do not clone a live unit's image. Generate fresh host and SAGE identities for
each device.

## 2. Provisioning

Use [Real unit provisioning](../lantern/REAL_UNIT_PROVISIONING.md) as the
contract. The sequence covers the device lock, vault, private listeners, fleet
admin, model lanes, embeddings, packs, audio commission and the first release
pin.

The complete one-script sterile installer is still open. Where it does not
exist, follow the documented steps and record what was actually run.

## 3. Runtime

The runtime is shipped as a wheel built from the whole of `software/runtime`
(D-023). Review the wheel contents before installing it; a commit range is not
the deployment unit.

```sh
# Example. Use the reviewed wheel path for the current release.
python3 -m pip install /path/to/sage_lantern_runtime-*.whl
```

Install the configuration for the unit, including the answer runtime and
embedding settings. Keep the previous runtime available for rollback.

## 4. Model and guards

Unit C runs the V81 RKLLM token engine on loopback port 8081 with
16k context and 8k output. The 2B prompt-string service remains the rollback.
The vision lane uses its own model and port.

The service unit is `software/systemd/sage-lantern-llm.service`, with the
installed model and guard paths pinned in the release record. Do not swap a
model without the open-panel and regression gates in the model task.

## 5. Embeddings

The base installer stages the llama.cpp embedding service on port 8083 and the
Ollama-compatible shim on port 8084. Use `nomic-embed-text-v1.5` and verify
actual semantic retrieval after startup.

## 6. Device UI

The touchscreen app and local fleet-admin console are installed from the fleet
appliance release. The console listens on loopback port 3100 in the normal
device setup.

Keep the installed generation and its receipt. Do not overwrite a running
generation in place; stage the new one and switch after verification.

## 7. Maps and GNSS

The map server is installed from `software/gnome/maps-pack/`. It serves packs on
loopback port 8091 and exposes `/packs.json`.

```sh
python3 software/gnome/maps-pack/serve_pack.py \
  --root /opt/sage-lantern/maps --host 127.0.0.1 --port 8091
```

The GNSS status bridge listens on loopback port 8090. It reads gpsd and returns
no coordinates without a fresh mode 2 or 3 fix.

Install map packs with `pack_manager.py`, either from a local file or from the
public catalog:

```sh
python3 software/gnome/maps-pack/pack_manager.py fetch \
  --catalog https://raw.githubusercontent.com/l33tdawg/sage-lantern/main/maps/catalog.json \
  --id malaysia-z11
```

## 8. Voice loop

The voice loop is `sage-lantern-voice-loop.service`. Its capture path depends on
the platform codec and the four capture-contract files. Re-run the audio
commission after any change to those files.

## 9. Desktop mode

The optional desktop skin is installed from `software/gnome/`:

```sh
sudo software/gnome/install-desktop-skin.sh install
```

The uninstall path restores the previous accent, colour scheme, wallpaper and
dock favourites. It stores no passphrase and grants no SAGE authority.

## Verification

```sh
systemctl is-active sage-lantern-llm.service sage-lantern-voice-loop.service \
  sage-lantern-gnss-status.service gpsd
curl -fsS http://127.0.0.1:8091/packs.json
python3 software/gnome/maps-pack/pack_manager.py list --root /opt/sage-lantern/maps
```

Then open the device UI and check one typed question, one spoken question, one
camera capture and one map load. A healthy service is not a conversation.

## Rollback

- Keep the previous runtime wheel and release tree.
- Keep the previous model and guard paths.
- Keep map pack replacements in the pack manager's `.backups/` directory.
- Restore the previous service files rather than editing a running unit in
  place.
- If an uncertain write or an unknown storage fence appears, stop and preserve
  evidence. Do not delete a fence to make a service start.
