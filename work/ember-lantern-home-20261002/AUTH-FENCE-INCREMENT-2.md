# Auth context fence, increment 2: the expectation is captured, not read back

2 October 2026, 08:35 UTC. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. UI writer lane.

Authority: Rook's follow-up review `msg-5a31c119-89ce-4d8c-b70e-33fb9d487080`, items 1 and 2
only, bounded by the owner's direction to keep this closure to the fence fixes. Rook froze the
delivered source and my author evidence under `work/rook-auth-fence-review-20261002/`
(`REVIEW_BASELINE.json`, `AUTHOR_HASH_CHECK.json`, `source/`, `author-evidence/`) before
revisions began; nothing under that directory was read, written or reverted by this increment.

**O2 (menu-origin navigation) and O3 (blanked-publication copy) were not started.** Rook
authorised them after the fence fixes; the owner scoped this handback to the fence fixes alone,
so they remain open and unclaimed.

## The two defects, as reproduced

Both were confirmed against the frozen source before any edit, then reproduced in the browser.

**1. `app/device/talk.tsx` — the reply was its own witness.**

`admittedUnder` was taken from the `/start` reply (`replySessionContext(started)`), then compared
against what the device publishes *now*. A rotation that lands while the command is in flight is
admitted by the runtime under the **new** epoch, so the delayed reply carries the new context and
so does every reading after it. Reply and reading agree with each other and disagree only with the
authority the user actually activated — and the old comparison asked the reply to testify about
itself. Measured with `TalkControls` mounted alone (no parent, no `admit` callback):
`owned=true started=1`, two `POST /keepalive` renewals, no cancel, no notice.

Honest limit, stated up front: in the **integrated** screen the parent's own rotation fence
(`observeSessionContext` → `changed` → `fenceActivation()` → `withdrawStart()`) would also have
refused this start, so the two defences agreed on the outcome and the owner's unsound one was
masked. That is precisely why the proof is mounted at the owner. It is a second, independent
object's judgment that the owner was relying on; the owner's own gate is now sound on its own.

**2. `app/device/screen.tsx` — an obsolete reply could move the shared authority.**

`observeSessionContext(next.session_context)` ran before the `disposed` check, and nothing compared
the auth session the request was issued under. `observeSessionContext` is process-wide: it
establishes the authority every protected surface compares against. A reply that outlived its
token therefore *established* a dead authority — `setDeviceToken` has already reset the
observation, so such a reply can only put the old one back — and the next current reading then
looked like a rotation and dropped content that was never under the dead authority at all.
Measured: **2** subscriber notifications where exactly **1** (the replacement itself) is correct.

## What changed

| File | sha256 | Change |
| --- | --- | --- |
| `apps/lantern-fleet-admin/app/device/talk.tsx` | `fc746b90a4932110166f3e7b8730227c789b8d92d08dba3ce4e2048839df7bda` | `const issuedUnder = publishedContext()` captured before the first protected await; unverifiable → refuse; re-checked after `history/list`; the reply compared to `issuedUnder` **and** to the published context; the confirmation compared to `issuedUnder`; `admittedContext.current = issuedUnder` so adoption, renewal and the stopped capture are all anchored to it. |
| `apps/lantern-fleet-admin/app/device/screen.tsx` | `5581df5cb84baf0e5a1b574d3f0c838ced6313fb48391587fb5b776f7e305037` | `const issuedUnder = getDeviceToken()` captured before dispatch; `if (disposed \|\| getDeviceToken() !== issuedUnder) return;` placed before `observeSessionContext` and before every state update. Refused, not thrown: for a live screen this is a reply to skip, not evidence the device is unreachable. |
| `apps/lantern-fleet-admin/lib/home-integration.test.mjs` | `17052ef22208f4be16c1df2de442aac1a9581c439522dc9bc6a69f82cd6dde4e` | The source-level invariants now assert the corrected direction, the capture ordering, and the poll's refusal preceding the seam. |

No other protected completion used the flawed pattern. `keepRecording` (renewal) and `finish`
(stop/cancel) already compare against `admittedContext.current`; that ref is now the captured
`issuedUnder` rather than the reply's own value, which is what makes them sound for the same
reason. `openTyped` (`question/start`) takes no capture and holds no lease, so it has no
authority to fence. Nothing in the sibling lanes (`dictation.tsx`, `question-correction.tsx`,
`suggested-question.tsx`) adopts a recording under a context.

## Evidence

`owner=1` mounts the real `TalkControls` with no parent at all: no admission callback, no epoch
fence over `/status`, readings fed to it and callbacks recorded. `rotation=status-inflight` is the
reachable rotation landing inside a pending `/start` (the epoch rotates on lock, owner change or
revocation, and a request already on the wire is admitted after it). `tokenSwapAfter=<n>` replaces
the device token when the n-th status request arrives, before its delayed reply is delivered — the
app's own `setDeviceToken` path, which necessarily changes `session_ref`.

```bash
# fixture server, from this directory
python3 -m http.server 8791 --bind 127.0.0.1
node build-screen-fixture.mjs
node drive-auth-fixture.mjs http://127.0.0.1:8791/screen-fixture.html fence2
```

| Run | Source | Result |
| --- | --- | --- |
| `auth-fence-prefix-results.json` | frozen (pre-fix) | **9/11**, 0 console errors — the two new scenarios fail |
| `auth-fence-fence2-results.json` | this increment | **11/11**, 0 console errors |

The two failures in the pre-fix run are the point of the run: they are the discriminator, not a
formality. Both new scenarios plus both controls are new; scenarios 1–8 are the existing record
re-run under a label and are unchanged.

- `the-owner-refuses-a-start-issued-under-the-old-epoch` — pre-fix 3/7 (`owned=true started=1`,
  keepalives, no cancel, no notice), post-fix 7/7. The log ordering assertion is
  `rotated@2 … ack@3`: the rotated reading reached the owner *before* the delayed reply did, so the
  refusal is the owner weighing what it captured against what it now reads.
- `the-owner-admits-the-same-start-when-nothing-rotated` — identical query without the rotation.
  If this did not start, the refusal above would prove nothing. Screenshot shows the real Cancel /
  Send actions.
- `an-obsolete-poll-reply-cannot-move-the-shared-authority` — pre-fix `baseline=0 after=2`,
  post-fix `baseline=0 after=1`, with `statusReads >= 3` proving the loop kept reading under the
  replacement authority.
- `a-current-same-token-rotation-still-notifies-once` — the counter's positive control: a rotation
  this client is entitled to see still notifies, exactly once. Without it, "1" could be satisfied
  by a seam that had stopped reporting anything.

Reachable broker states: `rotation=status-inflight` and `tokenSwapAfter` model events that happen
on the device — an epoch rotating, a device token being replaced. The synthetic, *unreachable*
replies from increment 1 (`rotation=confirm|keepalive|stop`, a 200 carrying a context the client
never admitted under) are unchanged and still prove the client's own gate rather than the server's.

## Verification

| Gate | Result |
| --- | --- |
| `node --experimental-strip-types --test lib/*.test.mjs` | 331/331 pass |
| `npx tsc --noEmit -p tsconfig.json` | exit 0 |
| `npx eslint app/device/talk.tsx app/device/screen.tsx lib/home-integration.test.mjs` | exit 0 |
| `node drive-auth-fixture.mjs … fence2` | 11/11 scenarios, no console errors |

Byte-identical to Rook's freeze after this increment: everything under
`work/rook-auth-fence-review-20261002/author-evidence/` except the three files this increment
extends — `drive-auth-fixture.mjs`, `screen-fixture-entry.tsx`, and the rebuilt
`screen-build-metafile.json`. The historical `auth-fence-results.json`, `screen-auth-results.json`
and every Settings record are unchanged; the new runs wrote labelled files beside them
(`auth-fence-prefix-*`, `auth-fence-fence2-*`) and overwrote nothing.

## Still open

- O2 (each menu destination records its real origin, clears stale `backTo`) and O3 (neutral copy
  for a blanked publication) — authorised by Rook, not started here.
- Successful Settings writes, real human consent, native policy, physical focus and whole UI
  acceptance remain open, as do the backend/deployment gates Unit C owns.
- Every refusal above is source-and-fixture evidence. No device, service, model or audio path was
  contacted; `homeEnabled` stays false and no default changed.
