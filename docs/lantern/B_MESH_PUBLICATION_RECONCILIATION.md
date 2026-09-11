# B partial mesh publication reconciliation

Review candidate only. No B actions performed. Historical cause is unproven:
301.623 seconds input-to-failure supports the shared 300-second alarm hypothesis,
but WorkflowUnavailable suppressed the underlying exception. Current successful
signed scans do not retroactively prove the failed operation succeeded.

The observed original mesh directory contains exactly 15 files and no completion
or mesh-expected publication. Both containment metadata files already hold the
projected values. Three archived original cold guards remain absent; successor
guards remain authoritative. Never replay transfer, recreate original completion,
restore old guards speculatively, or republish metadata.

## Exact separate route

Use the new handoff helper and a new mesh expected document identical to the
original intent except for its new `handoff_sha256` and an added `partial_mesh`
pin `{path, sha256}`. This pins a root-owned inventory JSON with schema
`sage.lantern.b-failed-mesh-inventory.v1` and `files` mapping the exact 15 filenames
to hashes in `/private/tmp/lantern-b-handoff-failure-public-inventory-20260908.txt`.
Its canonical helper.encoded SHA must be
`024b7e76f824907a3bd562ec92b3624e3d1c08e43538a8050cd1b6bd4618d5d2`.
The helper independently hard-pins the failed receipt and both projected hashes.
Keep all original/recovery evidence bindings; do not substitute the new helper
for the authority that executed the failed attempt.

Ordinary `--expected PATH --expected-sha256 SHA` is read-only inspection.
Separately approved `--apply` exclusively claims
`/var/lib/sage-lantern-b-policy-guard-handoff/mesh-publication-reconciliation`.
It performs one full validation, then records a separate completion. It performs
no guard removal, metadata replacement, service action, or automatic retry.
Failures preserve the original and new attempt, with bounded exception class and
elapsed-time diagnostics; no raw response, key, or workflow payload is recorded.

Full artifact/native-image hashes bracket the signed workflow scans. Nested scan
callbacks retain native PID/invocation/listener/boot/image-stat/health checks,
physical successor guard checks, producer inactivity, empty cgroups and sleep
checks; they do not repeatedly hash historical chains or the native binary.
The existing scanner retains live access/storage epochs and two bounded empty
scans. Callback elapsed time is capped at 120 seconds; CLI retains its 300-second
alarm. This is not a historical timeout diagnosis or an unlimited budget extension.

## Subsequent authority and final release integration

Native-phase expected input adds `mesh_reconciliation` equal to the inventory pin,
and `mesh_handoff_sha256` pins the real separate reconciliation completion.
Original mesh intent/failure remains immutable and is rechecked. No synthesized
original mesh completion is accepted.

The already-running sleep inhibitor still pins the original handoff helper.
Its source and dependency pins are deliberately NOT silently changed. The new
`scripts/lantern/release-b-reconciled-inhibitor.py` implements explicit final
ownership transfer rather than asking the old verifier to accept a new schema.
Do not overwrite the old staged helper underneath the resident holder.

After actual commissioned native handoff completion, prepare a separately reviewed
root-owned release JSON with exact keys:

```
schema: sage.lantern.b-reconciled-inhibitor-release.v1
self_sha256: reviewed release helper hash
old_helper: {path: actual unchanged resident helper source, sha256: 56ee0cc1616a022afd919676cdff6805a8a705d5d2e58da0de0a568d460aa4a3}
old_expected: {path: actual resident STATE/expected.json, sha256: freshly collected hash}
old_owner: {path: actual resident STATE/owner.json, sha256: freshly collected hash}
native_expected: {path: actual reconciled native-phase expected document, sha256: actual hash}
native_complete_sha256: actual native handoff completion hash
```

Stage the new handoff verifier separately, preserving old source paths. Invocation:

```sh
/opt/sage-lantern/venv/bin/python -I -B /NEW_STAGE/release-b-reconciled-inhibitor.py \
  --verifier /NEW_STAGE/handoff-b-policy-guards.py \
  --expected /NEW_STAGE/final-release.json --expected-sha256 REVIEWED_SHA
```

Default is read-only: validate old ownership, installed executable contract and
actual new native completion. Append `--apply` only after separate approval.
Exclusive journal is `/var/lib/sage-lantern-b-reconciled-inhibitor-release`.
No retry or resume mode exists. These are actual receipt inputs, not permission to
invent future completion or assume the old helper hash matches target deployment.

The child runs under a second real `systemd-inhibit --what=sleep --mode=block`,
with root/DEVNULL stdin, distinct owner name, exact wrapper/holder argv and image,
PID/start ticks and logind row verification. Only after new inhibition and new
completion validation does it send SIGTERM through a revalidated pidfd to the old
holder. The old code performs its own fail-closed remask. The bridge requires its
actual successful remask receipt, both old processes gone, old logind row absent,
and all four exact masks present. It archives those new mask identities, then
removes only those masks under its continuously checked real inhibitor.

Finally it revalidates actual native commissioning/startup completion and ordinary
sleep/job state, then exits to release the new inhibitor. The parent records
completion only after the wrapper exits successfully and both inhibitor rows are
absent. It never calls this sleep acceptance or RF delivery proof. The old release
receipt is not forged; its explicit remask/exit is separate transfer evidence.

The holder operation has a 240-second deadline; parent wait is 300 seconds.
On any failure it records bounded diagnostics, attempts exact four-mask restoration,
and retains the remaining inhibitor indefinitely for manual reconciliation. It
does not auto-release, resume, or retry. SIGKILL/logind loss/root interference
cannot be made atomic with remasking; this retains the original physical-system
limitation rather than claiming unconditional inhibition.

Local tests cover transfer ordering, stage failures and exact verifier dependency;
they do not replace target pidfd/logind/remask protocol inspection. All helper code
is source-only pending independent review. No B action was performed.
