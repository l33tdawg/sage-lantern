# Refusal follow-up applied and verified — two shared-transport gaps closed

2 October 2026. Ember, sole UI writer lane `c423d913-de77-4327-a98c-2e179f238f20`
(program `acc40de9`). Source-only, default-off. No device, service, model, audio,
radio, deployment or private-action step occurred.

This closes the two adjacent gaps Rook reproduced in
`msg-5de0d823` and supplied as a tested isolated candidate in `msg-d24eb6ae`.

## What was applied, and from where

Applied `work/rook-auth-fence-review-20261002/refusal-followup-candidate/source-and-fixtures.patch`
in full (source plus the three fixture files) with `git apply -p1`, exit 0.

Provenance: the candidate, its isolated test evidence and its patches are Rook's.
Credit for the candidate design and its isolated verification stays with him; what
is recorded here is the application to the live tree, the fixture audit, and my own
independent re-run.

## Base and result verification

Measured before writing, not taken from the candidate's own record:

- All five `baseline_sha256` in `refusal-followup-candidate/CHANGESET.json` matched
  the live source immediately before `git apply`. No drift, no concurrent edit to
  preserve.
- After applying, all five live files hash to exactly the `candidate_sha256` values
  in that same `CHANGESET.json`. The live tree is therefore byte-identical to the
  candidate Rook verified in isolation, rather than merely similar to it.

## Fixture audit (the part I said I would not wave through)

`source-and-fixtures.patch` also edits three existing test files. Each edit was read
before application. All three are contract corrections forced by the `legacy`
snapshot gaining a `generation` field; none weakens an existing assertion.

1. `device-session.test.mjs` — the exact-object `deepEqual(..., { state: 'legacy' })`
   cannot survive the shape change. Replaced by a state assertion plus a new
   `Number.isSafeInteger(...generation)` assertion, so this is strictly stronger.
   The two `fenceProtectedReply({ state: 'legacy' }, ...)` calls become
   `fenceProtectedReply(establishedSessionContext(), ...)`, which exercises the real
   producer instead of a hand-built literal. Same two assertions, same expected
   verdicts, no deleted coverage.
2. `history-cache.test.mjs` — was `assert.equal(calls, 3)` after token replacement.
   Now asserts that replacement plus `establish()` plus `settle()` dispatches
   NOTHING (`calls === 2`), that the cache is cleared, and that an explicit
   `refresh()` is what produces the third call. Strictly stronger: it adds the
   no-dispatch-in-the-gap assertion the fix exists to guarantee.
3. `protected-reply-fence.test.mjs` — two source-text pins updated to the new type
   line and the new guard line. Both remain full structural `assert.match` pins on
   the exact source text; the behavioural coverage lives in the behavioural suites
   below. Contract correction, not a loosening.

## Commands and results

Pre-apply (unpatched tree, same harness and environment) — this is the red half of
the discriminator:

```
REFUSAL_APP_ROOT=<app> node --experimental-strip-types --test <RB>/refusal-authority-independent.test.mjs   -> 29 tests, 29 pass, 0 fail
REFUSAL_APP_ROOT=<app> node --experimental-strip-types --test <RB>/refusal-surface-independent.test.mjs     -> 19 tests, 19 pass, 0 fail
REFUSAL_APP_ROOT=<app> node --experimental-strip-types --test <RB>/refusal-supplement-independent.test.mjs  -> 14 tests, 5 pass, 9 FAIL
```

Post-apply (current tree), run from `apps/lantern-fleet-admin`:

```
node --experimental-strip-types --test lib/*.test.mjs                       -> 367 tests, 367 pass, 0 fail, exit 0
npx tsc --noEmit --incremental false                                        -> exit 0
npx eslint lib/conversation-threads.ts lib/device-session.ts \
  lib/device-session.test.mjs lib/history-cache.test.mjs \
  lib/protected-reply-fence.test.mjs                                        -> exit 0, no output

REFUSAL_APP_ROOT=<app> node --experimental-strip-types --test <RB>/refusal-authority-independent.test.mjs   -> 29 tests, 29 pass, 0 fail
REFUSAL_APP_ROOT=<app> node --experimental-strip-types --test <RB>/refusal-surface-independent.test.mjs     -> 19 tests, 19 pass, 0 fail
REFUSAL_APP_ROOT=<app> node --experimental-strip-types --test <RB>/refusal-supplement-independent.test.mjs  -> 14 tests, 14 pass, 0 fail
```

The supplement moves 5/14 to 14/14 with the harness, environment and command line
unchanged; only the applied source differs. That is the discriminator, and it is
why the green run is evidence rather than a rerun that was going to pass anyway.

`--incremental false` was used deliberately so the typecheck writes no
`tsconfig.tsbuildinfo`. The existing `tsconfig.tsbuildinfo` (mtime 16:57) predates
these runs and was not touched.

Logs: `refusal-followup-20261002/PRE-*.log` and `POST-*.log`, exit codes and counts
also in `refusal-followup-20261002/RESULTS.json`.

## Changed files and final hashes (sha256)

```
9cd42af8190270a7775c8cf2c9380c35f6344e7dc3c661bb5c612d1d59585c6b  apps/lantern-fleet-admin/lib/conversation-threads.ts
25649d8b513d7d579f7fe00c9d04c1527343246e7c0f56d38717df3670e0c8df  apps/lantern-fleet-admin/lib/device-session.ts
0a297482dbbad277b8c5f5235c4984deab99e435c163a1cb985f4e151d3667f3  apps/lantern-fleet-admin/lib/device-session.test.mjs
da5594a087e92cca5370853710ba3075ad44a2481e87b3c036306b077a31b52b  apps/lantern-fleet-admin/lib/history-cache.test.mjs
23201a72e2f8609589938d0b46eae01fa4da77bd40bff34b5391d5d9eee83a93  apps/lantern-fleet-admin/lib/protected-reply-fence.test.mjs
```

Five files changed, two of them source. No other file was written.

## What this does and does not establish

Establishes: `conversationRequest` now refuses before the first dispatch and before
every permitted history retry when the captured authority is no longer usable, so
`conversation/select` can no longer change state under lost authority; and every
usable authority snapshot, legacy included, now carries the generation that the
renewal check tests, so a delayed legacy write can no longer adopt its old success
across token-away-and-back. Uninterrupted and fresh legacy work still works, which
the supplementary positive controls assert.

Does NOT establish: anything live. Tests use synthetic transport and a component
hook harness, not browser events or a device. Cold first-frame establishment, live
stamp emission, live browser/broker qualification, real owner authentication and
consent, native/private policy, deployment and hardware acceptance all remain open,
as do successful Settings writes, physical focus restoration and whole-UI
acceptance. No live request, write, receipt or selection was issued. This is
bounded source evidence.
