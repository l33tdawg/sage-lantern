# Offline mesh runtime preparation

Owner handoff to the canonical installer: `scripts/lantern/prepare-mesh-runtime.py`
prepares a **fresh bundle only**. It does not install, activate, reload systemd,
open a device, contact SAGE, load private keys, allocate receiver UUIDs, enroll
peers, create transient grants, or inspect/clear existing runtime journals.
This is not a replacement for the canonical fresh-unit installer.

## CLI / inputs

Run with the matched runtime wheel and SDK installed in the Python environment:

```
python scripts/lantern/prepare-mesh-runtime.py \
  --manifest /root/mesh-input/manifest.json \
  --inventory /root/mesh-input/unit-inventory.env \
  --provisioned /root/mesh-input/PROVISIONED \
  --unit-metadata /root/mesh-input/unit.env \
  --radio-inventory /root/mesh-input/radio-public.json \
  --output-dir /root/mesh-prepared-unit-b
```

CLI requires root; `--help` is read-only. Unknown arguments, abbreviation,
`--serve`, and `--dry-run` are rejected. Output directory must not exist,
including empty directories or dangling links. No overwrite/recovery option.

Inputs are bounded 16 KiB regular single-link root-owned files, modes
0600/0640/0644, reached through root-owned non-group/world-writable directories
without symlinks. Supply independently reviewed root-controlled snapshots:
the original service-owned inventory directory is intentionally not trusted as
a root input path. Copying a snapshot is not independent verification.

The input manifest has exactly the daemon schema documented in
`MESH_ROOT_DAEMON.md`. Every identity, receiver slot, radio pin, endpoint, peer
triple and policy boolean is supplied explicitly. No template/example values
are substituted. The helper cannot detect every plausible placeholder or prove
that a UUID was allocated safely: that remains the provisioning authority's job.

`unit.env`, inventory and `PROVISIONED` must match the actual
`provision-lantern-unit.sh` schemas. The exact inventory bytes must match the
PROVISIONED digest; all four provisioning attestations must be PASS. Unit and
chain must match; voice fingerprint is SHA256 of the raw ordinary public key.
The existing `RadioBinding.from_inventory` validates radio fingerprint, unit,
stable path and USB metadata against the manifest. Peer allowlists are explicit
operator trust, not inferred from radio prefixes or unsigned self-assertions.
An empty peer list grants no peers. No protected contact cards are manufactured.

## Output / installer contract

The helper exclusively claims a fresh root0700 bundle directory. It writes and
fsyncs `mesh-runtime.json` (0600), `20-radio.conf` (0644), then `PREPARED.json`
(0600) last. The receipt binds exact input and artifact SHA256 hashes; it is a
local preparation receipt, **not** an identity signature or activation proof.
Parent directories are fsynced. A failure leaves partial artifacts intact and
a repeated invocation refuses the existing directory. An interrupted receipt
write may leave an invalid receipt: existence alone never means completion.

The canonical installer must validate receipt schema/hashes, ownership and
current unit/provisioning bindings again, then safely install the two artifacts
to `/etc/sage-lantern/mesh-runtime.json` and
`/etc/systemd/system/sage-lantern-mesh-runtime.service.d/20-radio.conf`.
The device policy resets inherited DeviceAllow entries and allows only the
operator-pinned stable radio path; effective systemd policy still needs review.
Bundle creation does not authorize overwriting either destination.

Installer ownership includes exclusive target-install coordination, fresh-target
checks, preserving root fences/unknown journals, matched wheel/scripts/units,
and the guarded stop/start sequence. The bundle claim excludes concurrent
preparers for the same output only, not independent installers elsewhere.
No existing live voice adoption, automatic rollback, journal deletion, grant
creation, service start or boot enablement is authorized by this helper.

Linux permission/systemd checks and first electrical open remain native
acceptance gates. These tests use temporary filesystem fixtures, never native
devices, target `/etc`, private keys or network operations.
