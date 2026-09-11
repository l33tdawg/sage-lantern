# Fresh display activation (source review candidate)

`scripts/lantern/activate-fresh-display.py` is the explicit post-onboarding
activation path for independently provisioned unit-a and unit-b. It does not
install helpers, start audio, suspend the machine, release inhibitors, or import
historical A recovery evidence. No device activation or physical acceptance has
been performed for this change. The reviewed activator is now a required
canonical installer artifact with `review_only` operation and no destination.
Packaging does not install, execute, enable or authorize it. The collector below
is a separate source-review candidate and is not yet in the canonical map.

## Prerequisites and expected input

Install the reviewed display helper, session supervisor and unit using the
existing `install-display-power-local.sh --unit-id UNIT` install-only path.
Complete owner setup, including recovery backup confirmation. Collect independent
root-controlled public pins; do not copy A's expected input to B.

The exact `sage.lantern.fresh-display.v1` JSON object contains `attempt_id`
(new nonzero UUID), `unit_id`, current `boot_id`, `files` (absolute path to SHA256
map), `session`, `auth_process`, `auth_dropins`, and `policy`. The code's `Native.files()` lists
mandatory installed artifacts, including all installed runtime Python sources,
this coordinator, helpers, units, interpreter, launcher and appliance receipts.
Include a trusted systemd/executor image pin if the platform uses a pre-exec
Type=simple transition. Unknown images never pass.

`session` is `session_public()` of the existing supervisor's actual nonblanking
`Native.check_x11(binding_for('fresh', UNIT))` result. Scheduling state is omitted;
launcher PID/start, account, session, X authority and socket identities remain
bound. `auth_process` contains PID, start_ticks, invocation and image_sha256.
`policy` is either `{ "state": "absent" }` or
`{ "state": "present", "sha256": "ACTUAL_HASH" }`.
Present policy must be exactly `fresh UNIT` followed by newline, root-owned 0644.
It is preserved, never overwritten. The coordinator verifies setup directly
against the pinned auth process and repeats the nonblanking check.
The setup caller uses the installed auth contract: POST `/setup/status`, an empty
JSON object, `Origin: http://127.0.0.1:3100`, JSON content type and
`X-Lantern-Auth: control`. This status route does not enable/unlock/confirm a vault.
Auth/CORS validation is unchanged. The loopback 3196 listener inode must belong to that PID/start/boot both before
and after HTTP; wildcard, IPv6 competitors and a changed listener are refused.

Collect the actual auth manager properties read-only using:

```sh
systemctl show sage-lantern-device-auth.service \
  -p FragmentPath -p DropInPaths -p NeedDaemonReload -p MainPID -p InvocationID
```

`auth_dropins` is the exact reviewed absolute-path/SHA256 map of those effective
drop-ins (empty only if the actual list is empty). Include each in `files` too.
Only root-controlled `.conf` files directly under this auth unit's `/etc` or
`/run/systemd/system` drop-in directory are admitted. Their bytes and exact
effective list are rechecked, not automatically approved by collection. Unknown
additions, removals, changed bytes or pending reloads refuse activation. The
display unit still admits no drop-ins. This is the collector input contract;
no live auth configuration has been collected or assumed by this source patch.

## Explicit activation

### Read-only input collection

`collect-fresh-display-inputs.py` accepts only the reviewed activator SHA256
`81142b2354e07c61962d291bd79e3d81571451d8749fd41e8d1fb4d71c3aa542`.
This caller-contract correction is pending review; it has not replaced the
previously staged B tools or been retried on B.
Prepare a root-controlled reviewed inventory with exactly `schema` =
`sage.lantern.display-reviewed-inventory.v1`, independent `unit_id`, `files`,
and `auth_dropins` as defined above. Collection does not turn arbitrary observed
files into approved source pins. Missing required files or mismatched live
effective drop-ins must be reviewed, not dynamically allowed.

```sh
/opt/sage-lantern/venv/bin/python -I -B /ROOT/collect-fresh-display-inputs.py \
  --activator /ROOT/activate-fresh-display.py \
  --inventory /ROOT/display-reviewed-inventory.json --inventory-sha256 REVIEWED_SHA \
  --attempt-id NEW_UUID
```

The collector performs the existing nonblanking X/session check, observes the
actual auth process, then runs the real activator inspection (including owned
listener/setup/policy/manager checks and a repeated X/session check). It emits
public JSON to stdout only: `expected`, its canonical `expected_sha256`, and
explicit non-activation/non-physical flags. Serialize `expected` with the helper's
`encoded()` function to obtain matching input bytes; the collector writes no
target files. It is bounded by 90 seconds and contains no service start/stop or
activation call. A failed check produces no success receipt.

Collection is for inactive, boot-disabled display activation only, not a shortcut
to boot enablement or physical acceptance. Coordinate any live invocation with
the sole device owner; this change has made no A/B requests. Apply still requires
the owner-unlock/power window and a fresh exact preflight.

Run with the installed venv interpreter using `-I -B`:

```sh
/opt/sage-lantern/venv/bin/python -I -B /ROOT/activate-fresh-display.py \
  --expected /ROOT/activation.json --expected-sha256 REVIEWED_SHA
```

Default inspection does not write or activate. Add `--apply` only for the
reviewed activation. It exclusively records intent under
`/var/lib/sage-lantern-display-activation/ATTEMPT/`, creates an absent approved
policy, and starts only the display service. It waits for the same supervisor
lifetime and its independently observed power child. It does **not** enable boot.
The supervisor must be UID 0, and supervisor and account-owned power child must
both belong to the exact display unit's unified cgroup, checked again at readiness.
Existing deployment/sleep fences remain subject to the real session helper.

## Separate boot enablement

Physically test screen off, touch wake and required PIN on that unit. Supply a
root-controlled `sage.lantern.display-physical-acceptance.v1` object with
`unit_id`, `activation: {path, sha256}` referencing the successful `complete.json`,
and explicit true `screen_off`, `touch_wake`, `pin_required`, `operator_approved`.
This is operator attestation, not automatically inferred physical evidence.

Prepare a new expected attempt UUID with present-policy hash and unchanged
artifact/session/auth/boot pins. Pass `--boot-acceptance PATH
--boot-acceptance-sha256 SHA` first for inspection, then with `--apply` for
enablement. The actual activated supervisor lifetime must still match. This
operation enables the existing unit without starting/restarting it. Reboot
acceptance is still separate and is never marked complete by this helper.

## Failure behavior

All attempts are exclusive and retained. Bounded protected cleanup attempts to
stop only the display service and remove only its exact newly enabled link.
It retains policy and partial evidence. A failed or uncertain attempt is not
retry authorization: inspect the receipt and real current state before any new
reviewed transition. No idle-suspend approval or B inhibitor reconciliation is
provided by this display-only path.
