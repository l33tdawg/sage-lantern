# B failed inhibitor release — separate reconciliation plan

Source review candidate implemented in `scripts/lantern/reconcile-b-failed-inhibitor.py`
with `scripts/tests/test_reconcile_b_failed_inhibitor.py`. No live mask changes, release attempts or edits to
the retained holder, historical helpers, R7 runtime, or prior journals.

## Exact architectural issues

`release-b-reconciled-inhibitor.py` cannot be rerun: its CLI rejects an existing
state directory and its old-owner references describe the already superseded
holder. `transfer()` calls ordinary completion verification before mask removal.
That reaches `ColdBrokerBootstrap.inputs()` and requires ordinary sleep-idle
confirmation, which correctly rejects the four retained masks.

The currently retained helper's failed-holder loop ignores SIGTERM/SIGINT. The
older helper's SIGTERM/remask protocol therefore cannot be reused to retire this
holder. It must not be killed until a separately pinned replacement inhibitor
is actually held and verified. Retirement requires explicit review of a pidfd
SIGKILL of this exact failed holder, followed by verified wrapper exit and owned
logind-row disappearance. No kill-by-name, generic stop, or timeout-to-success.

There is also an epoch compatibility issue to account for before mutation:
`handoff-first-audio-lifetime.Native.snapshot()` includes current access_epoch
in its plan, while historical `audio_check()` compares the entire plan SHA to
the original handoff. A later unlock can legitimately change that epoch. Do not
change the old plan, substitute its epoch into a current observation, or claim
this caused the historical failure. A new verifier must separately establish
historical receipt validity and current-epoch readiness.

## New files and boundaries

Implement a standalone `reconcile-b-failed-inhibitor.py` plus focused tests. Use
new unique root state `/var/lib/sage-lantern-b-failed-inhibitor-reconciliation/UUID`.
Default is bounded read-only inspection. `--apply` claims one attempt; failures
are preserved and never automatically retried. Existing helper/source hashes
remain pinned and unchanged. This is not an R7 wheel update.

Expected schema `sage.lantern.b-failed-inhibitor-reconciliation.v1` must bind:

- Own source and all loaded helper hashes; independent unit-b identity, current
  boot and access epoch; current native/auth/model/broker/voice PID/start/image/
  invocation and display activation receipt/process pins.
- Exact full prior failed-release file inventory (paths, hashes, metadata),
  including intent, expected, owner, failed-held and remask records; absent
  successful release receipt. Preserve the failure and its unknown historical
  cause. Do not reinterpret `remasked` as successful release.
- Actual retained holder/wrapper argv, executable, PID/start and exact owned
  logind inhibitor row; four current root-owned runtime `/dev/null` symlink
  identities and effective manager state. All nine sleep units must be inactive,
  job-free and reload-free; no unreviewed drop-ins or persistent substitutes.
- Actual durable lifetime, first-handoff documents, startup config/unit/enable
  link, native-handoff receipt, mesh reconciliation and commissioning pins.
  Pin the original historical plan separately from fresh current-epoch evidence.

## Two distinct completion contracts

1. **Masked, retained-inhibitor inspection:** independently verify all non-sleep
   completion predicates. Implement explicit read-only broker input checks
   equivalent to the pinned R7 `inputs()` file/config/marker/fence/effective-unit
   checks, and use its real `active()` broker proof. Repeat inventory, process,
   model, commissioning, startup ownership and epoch checks around the scan.
   Validate historical handoff documents against their original plan; compare
   current immutable fields independently and require current epoch stability.
   Return `non_sleep_completion_verified` and `retained_inhibition_verified`;
   **never** return or synthesize ordinary sleep-idle confirmation.
2. **After unmask, replacement inhibitor still held:** invoke unmodified R7
   `ColdBrokerBootstrap.inputs()` and actual broker proof, plus unmodified
   `SuspendSystem.verify_sleep_idle()` and the new historical/current completion
   verifier. Every ordinary sleep predicate must now pass. No monkeypatch,
   global subclass replacement, fake `.confirmed`, or ignored exception.

Do not call the old full-plan-hash verifier and then excuse epoch failure.
The successor completion verifier explicitly preserves historical evidence and
binds current facts; its full predicate coverage needs review before deployment.

## Once-only apply sequence

1. Recheck inspection bindings, exclusively claim and fsync new intent. Start a
   new uniquely named real logind sleep inhibitor using the reviewed native
   executable contract. Verify its row, wrapper/holder lifetime and inherited
   ownership before any old-holder signal or mask mutation.
2. Repeat the complete masked contract with both inhibitors held. Preserve the
   four mask identities. Record exact retirement intent; retire only the pinned
   failed holder using reviewed pidfd handling. Verify its exit, wrapper exit and
   owned-row absence while proving the replacement remains held.
3. Record and remove each exact mask individually, fsync and daemon-reload, then
   recheck expected partial mask state, inhibitor ownership and all other process/
   file/epoch pins. Refuse replacement links or unexpected manager changes.
4. Run the strict ordinary checks and successor completion verification. Record
   exact stage, elapsed time and sanitized failing function/line if unsuccessful.
   No private response bodies, exception messages, locals or credentials.
5. Only after all strict checks pass, fsync release authorization. Release the
   new inhibitor; verify its actual row/process exit and persist completion.
   Mark sleep/physical/reboot acceptance false. Never restart audio or display.

Use an aggregate operation deadline and separately protected bounded cleanup.
After any failed or cancelled mutation, restore only owned removed masks,
reload/verify them and retain the replacement inhibitor. Log-write failure must
not prevent cleanup. If cleanup cannot be proved, record unknown if possible and
keep inhibition; do not claim confirmed containment. A parent failure after the
child exits needs its own bounded recovery/unknown handling, not a fictional
child cleanup claim.

## Required focused tests

- Real pinned non-sleep checks reject changed inventory/config/fences/unit
  drop-ins, broker proof, model, epoch, native or startup ownership.
- Masked admission checks all nine effective sleep states and exact symlink
  identities; unknown masks, reloads, jobs, wrong owner/inhibitor row fail.
- Historical epoch differs from current legitimately: history remains unchanged,
  current epoch is independently pinned; changes during inspection fail.
- Existing SIGTERM-ignoring holder is not treated as retired; replacement
  ownership must precede exact pidfd retirement, with timeout/exit races covered.
- Every partial-unmask boundary, real ordinary verifier rejection, expiry,
  cancellation and journal failure retains/restores inhibition without retry.
- Diagnostics preserve the underlying bounded stage/frame; no secrets or false
  historical-cause attribution. Success requires actual ordinary predicates and
  actual inhibitor disappearance, not file presence alone.

## Candidate CLI and exact additional input fields

Run only after independent source review, with the installed venv `-I -B`:

```sh
/opt/sage-lantern/venv/bin/python -I -B /ROOT/reconcile-b-failed-inhibitor.py \
  --expected /ROOT/new-reconciliation.json --expected-sha256 REVIEWED_SHA
```

Default is inspection; `--apply` is a separate reviewed operation. Do not invoke
the internal `--holder` manually. No staged/live expected input has been generated
for this candidate. The candidate has not been deployed or run against B.

The exact schema fields are `schema`, `attempt_id`, `boot_id`, `access_epoch`,
`self`, `releaser`, `verifier`, `failed_tree`, `masks`, `historical_plan`,
`processes`, `current_files`, and `display_receipt`. Input bytes must use the
helper's canonical `encoded()` serialization. `self`, `releaser`, `verifier`,
`historical_plan` and `display_receipt` are `{path, sha256}` pins. The prior releaser
and verifier are fixed to 241d1a93… and 5d172fb6… respectively.

`failed_tree` maps every relative prior-state entry to `identity` (the helper's
`signature(lstat)` excluding atime) and either `sha256`, `target`, or
`directory: true`. `masks` uses the original mask helper's signature format,
not the file-tree signature. `current_files` maps exact reviewed files to hashes,
including display policy/helpers and every effective protected service fragment
and drop-in. `processes` requires precisely personal-sage, auth, model, mesh,
voice and display services, each with `manager`, `start_ticks`, `image_sha256`;
manager fields are listed in `Backend.pins()`. All are actual current observations,
not values copied from A or an earlier access epoch.

The new verifier reuses the historical native guard/file/receipt checks but
supplies its explicitly separate audio completion checker. It does not substitute
an epoch or synthetic successful snapshot into the old verifier. The masked
checker explicitly duplicates the pinned R7 non-sleep input predicates; strict
post-unmask checking invokes the original R7 `inputs()` unchanged. This duplicated
predicate coverage and holder lifecycle are important independent review targets.

The parent path records `active_state_unknown` if child exit/authorization/final
verification/journal completion cannot be established. It does not claim the
exited child performed cleanup. Such a result is not retry authorization.
