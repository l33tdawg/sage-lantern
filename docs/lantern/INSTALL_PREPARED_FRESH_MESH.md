# Install prepared fresh mesh, without activation

Run `install-prepared-fresh-mesh.py` using installed Python `-I -B`. Default is
read-only, including signed local storage GETs via the original preparer.
No RF handshake, microphone capture, native restart, service start, or daemon
reload is performed. Owner-unlocked storage must still match the prepared epoch.

## Explicit reviewed JSON

Schema `sage.lantern.install-prepared-mesh.v1` with exactly these fields:

- `schema`, `unit_id` (`unit-a` or `unit-b`), `boot_id` (exact current Linux boot).
- `prepared_dir`: root0700 directory containing exactly PREPARED.json root0600,
  mesh-runtime.json root0600 and 20-radio.conf root0644.
- `prepared_sha256`: receipt byte SHA256.
- `artifact_sha256`: exactly `mesh-runtime.json` and `20-radio.conf` hashes.
- `preparer_dir`: trusted installed directory containing the original preparer.
- `preparer_sha256`: exactly the hashes for `prepare-fresh-mesh-runtime.py`,
  `prepare-mesh-runtime.py`, and `provision-fresh-appliance.py`. All three are
  root-controlled and verified before the original preparer is loaded.
- `preparation_args`: the original reviewed argument values, with these exact
  underscore-separated keys: `manifest`, `radio_inventory`, `current_sage_policy`,
  `manifest_sha256`, `radio_inventory_sha256`, `commission_sha256`, `app_sha256`,
  `bindings_sha256`, `current_sage_sha256`, `current_sage_policy_sha256`,
  `sage_binary_sha256`, `radio_fingerprint`. No output_dir is needed.
- `service_files`: path-to-SHA256 map for the loaded broker/voice/LLM fragments
  and all their current drop-ins, including retained native maintenance guards.

Paths and identity come from reviewed inputs, never a cloned Unit A inventory.
The helper calls the pinned preparer's `inputs()` and `observe()` twice per
validation; it does not replace its semantic, identity, native config or storage
validators. It requires exact original receipt fields and regenerated artifact
bytes. A changed storage epoch, input, unit, USB observation or artifact rejects;
prepare a newly reviewed bundle rather than rewriting old proof.

```sh
/opt/sage-lantern/venv/bin/python -I -B <installed-helper> \
  --expected <root-owned-input.json> --expected-sha256 <SHA256>
```

Only separately authorized application adds `--apply`. Before either mode, both
destinations must be absent, all three services inactive with PID0/no jobs and
empty canonical cgroups, and effective native start guards still present. The
native maintenance directory must remain root0700. Existing unrelated service
or sleep activity refuses. No guard is removed or rewritten.

Apply exclusively creates `/var/lib/sage-lantern-fresh-mesh-install`, preserves
the expected inputs and original receipt, and fsyncs intent/verified records
around each exclusive no-follow write. It writes only:

1. `/etc/sage-lantern/mesh-runtime.json` as root0600.
2. `/etc/systemd/system/sage-lantern-mesh-runtime.service.d/20-radio.conf` as root0644.

Existing destinations are never overwritten. Failure preserves partial files,
journal and guards; there is no rollback, auto-retry or completion inferred from
file existence. The two-file installation is not atomic. Current inputs/storage
and containment are rechecked after each write. A broker daemon-reload-needed
state is permitted only after this invocation writes its exact radio drop-in.

Success means installed, not ready/activated. Parent owns later reviewed daemon
reload and effective radio-policy inspection, microphone consent/commissioning,
and scoped guard release. An apply journal is not an activation authorization.
The operation has a 150-second watchdog and a 120-second observation deadline.
