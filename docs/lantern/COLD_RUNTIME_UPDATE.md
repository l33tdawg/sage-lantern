# Unit A cold runtime replacement after native recovery

`scripts/lantern/update-fresh-runtime.py` is a separate local operation. It never
requests a native restart, edits native/UI/helper bytes, starts a service, clears
a guard, or grants readiness. It requires successful native capability
supersession with auth/broker/voice/LLM still stopped and inhibited. An absent
idle unit is supported by the shared reader. A failed native update is not an
eligible runtime checkpoint.

## Public inputs to obtain and review

- Exact successful supersession evidence path/hash and its independently pinned
  binding path/hash. The binding supplies Linux boot, unit/root/chain/initialized
  receipt, old/new source inventories and binaries, SAGE PID/start/UID/invocation
  and application BootIDs, plus auth invocation/receipt hashes.
- Hash-pinned installed preflight and sibling supersession helper. The preflight
  must implement the four-argument governance check including `/abci_info`.
- Old runtime wheel staged root-controlled; its actual installed package and
  wheel metadata bytes must match before any writes. Do not infer installed
  wheel identity solely from version `0.2.0`.
- Reviewed R6 runtime wheel and new native manifest, root-controlled paths.
- Current camera, display-power supervisor, and status service process identities
  (PID, start ticks, invocation, UID, executable and exact argv), or explicit null
  for a verified inactive service. Camera may have a dynamic UID; display uses
  root. Do not substitute the operator's UID for those service identities.
- All three services' loaded fragment/drop-in paths and hashes; active interpreter
  executable and installed helper hashes. Unknown/unpinned drop-ins reject.
- R6 voice-loop/status helper paths/hashes for later installation only.

Status must stop too: the old R3 status helper lazily imports `device_access`;
the new one also imports `audio_readiness`. No consumer may continue executing
mixed package generations. The root updater itself is the sole intentional
holder of already-imported old validation modules; it exits after verification.
Other operator-run runtime processes must be absent under the exclusive cold
maintenance boundary; this script does not claim a global process census.

## Expected document

Schema `sage.lantern.cold-runtime-update.v1`, exactly these fields:

```json
{
  "schema": "sage.lantern.cold-runtime-update.v1",
  "unit_id": "unit-a",
  "preflight": {"path": "<installed-preflight>", "sha256": "<reviewed>"},
  "supersession": {
    "evidence": {"path": "<retained-evidence>", "sha256": "<reviewed>"},
    "binding": {"path": "<retained-binding>", "sha256": "<reviewed>"},
    "helper_sha256": "<reviewed-sibling-helper>",
    "manifest_path": "<staged-new-native-manifest>"
  },
  "old_wheel": {"path": "<old-wheel>", "sha256": "<reviewed>"},
  "new_wheel": {"path": "<R6-wheel>", "sha256": "163a219664f8b60f72d4a1905cac965d67adf11a0b657a4ece40cbbb6f5a3d78"},
  "consumers": {
    "sage-lantern-camera.service": null,
    "sage-lantern-display-power.service": null,
    "sage-lantern-status.service": null
  },
  "files": {"<installed-fragment/dropin/helper/interpreter-path>": "<reviewed-sha256>"},
  "later_helpers": {
    "voice_loop": {"path": "<staged>/unit-a-voice-loop.py", "sha256": "d8114fa3165f9e65f466f0d4b0d3961039483bc2d5bdab67944d711eaff4ed06"},
    "status": {"path": "<staged>/unit-a-status-server.py", "sha256": "5682338aa01ddadb032f4ec6c1ad3e29daeaed179be577f4cf44152367578c59"}
  }
}
```

Null is not a suggested inventory: replace each active consumer with exactly
`{pid, start_ticks, invocation, uid, executable, argv}` using actual observations.
All paths are absolute, local, root-controlled and non-symlink files. The `files`
map covers immutable service configuration/helpers/interpreters, not the package
files intentionally being replaced. No keys or cookies belong in these inputs.

## Execution boundary

Read-only command, after staging and explicit input review:

```sh
/opt/sage-lantern/venv/bin/python -I -B <installed-runtime-helper> \
  --expected <installed-expected.json> --expected-sha256 <SHA>
```

Only a separately authorized invocation adds `--apply`. It creates exclusive
`/var/lib/sage-lantern-cold-runtime-update`, preserves the expected document and
an installable old wheel filename, and journals each phase with fsync. Existing
state refuses automatic re-entry. It writes persistent start-inhibition drop-ins,
reloads systemd, stops camera/display/status, verifies inactive empty cgroups,
and only then invokes pip with `--no-index --no-deps --no-cache-dir --no-compile
--force-reinstall`. Pip gets a sanitized environment and disabled config file;
no dependency acquisition, wheel build, or network step is performed.

Every package file and wheel metadata file except pip-regenerated RECORD must
match the candidate archive bytes. Extra package overlays reject. Old package
bytecode caches are removed while consumers remain stopped. The source and
metadata bytes are checked again afterward. Wheel names cannot escape the sole
package/dist-info directories or introduce installation script paths.

The aggregate watchdog is 240 seconds; native/consumer observations and pip also
share a 180-second backend deadline. Failure leaves durable state, backups and
any installed guards in place. Partial guard creation is not complete
containment; inspect the journal. There is no automatic rollback/retry, and no
claim that the update is atomic. Success leaves consumers inhibited and does not
install the pinned voice/status helpers. Those installs and owner activation
remain separate reviewed operations.
