# Retained partial: clearing it through the authority seam

3 October 2026. Ember, UI writer lane `c423d913-de77-4327-a98c-2e179f238f20`, in
response to Rook's UI re-freeze disposition (`msg-ec995a19`, follow-up
`msg-cc823fdf`). Source and fixture increment, handed back for independent
re-freeze.

## The defect

Rook reproduced one authority defect against the partial-answer retention
increment (`screen.tsx` `162f3d01…`) and it is real:

- A **missing or malformed** `session_context` is dropped inside
  `observeSessionContext`, which then reports the refusal. The retained words and
  the `Incomplete` marker stayed visible through the refusal **and returned once
  the same context was restored**.
- A **replaced token** (A→B→A) left the old partial alive.
- A **refusal first observed by another surface** left it alive too.

The cause is not a missing check inside this screen's poll — it is that the hold
was never subscribed to the seam. The poll's own catch reports the device as
unreachable rather than clearing protected content (deliberately: see the
positive control below), and the other two ways the authority moves never pass
through this poll at all.

## The correction

`app/device/screen.tsx`, one subscription beside the hold it protects:

```tsx
useEffect(() => subscribeDeviceSession(() => { setPartialTurn(null); }), []);
```

This is Rook's `RETENTION_CANDIDATE.patch` adapted rather than applied blindly.
The base hash matched live source exactly (`162f3d01…`) before the change, and
the shape is kept because it is the one this file already uses for every other
protected surface: the token setter, a changed context and a refused reading all
report through `subscribeDeviceSession`, and no second invalidation channel is
introduced. `apps/lantern-fleet-admin/lib/device-session.ts` is **not** modified.

Restoring the old context cannot revive the words because the hold is already
gone, and nothing but an accepted `processing` reading ever sets it — pinned
below so a later edit cannot add a second path back.

## What deliberately did *not* change

The poll's catch still does not clear the hold. A transport failure inside one
unchanged authority is exactly the interruption retention exists for; clearing
there would delete the answer the `Incomplete` marker is meant to keep. This is
asserted as a test, not left as a comment.

## Evidence

**Writer regressions** — `lib/home-integration.test.mjs`, two new tests:

| Test | Pins |
| --- | --- |
| `a retained partial leaves through the one authority seam, not through the poll` | the subscription exists; exactly one seam subscription; the hold's only setter sits behind the accepted-reading guard |
| `an ordinary transport interruption still retains the words` | the catch does not clear the hold; the authority fences that do clear it are still present |

RED → GREEN, source reverted in place, re-run, then restored and hash-verified:

- `node --experimental-strip-types --test lib/home-integration.test.mjs`
  — **RED 24/26** (2 fail) → **GREEN 26/26**
- Integrated Screen driver, two new scenarios:
  — **RED 53/55**, with `a-refused-authority-reading-drops-the-retained-partial`
  and `a-replaced-token-drops-the-retained-partial` both at **1/4**
  → **GREEN 55/55**, zero console errors

The rotation scenario (`an-authority-rotation-drops-the-retained-partial`) passes
in **both** states, which is the discrimination: the old fence already caught a
changed context, and caught neither of the two cases above.

The two new driver scenarios are behavioural, against the real `screen.tsx`:
retention is established first and asserted non-vacuous, the device's own wire is
emptied by the interrupt (so nothing on screen afterwards can have been
republished by a reading), and each asserts that the words and marker are gone
*and stay gone* after the authority is restored.

Fixture support added for this, fixture-only: `window.fixtureAuthority` exposes
the **real** `device-session` module (not a stub) so the driver can move the
authority the way an auth replacement or another surface does. Nothing under
`apps/` reads it.

## Gates

| Check | Result |
| --- | --- |
| `node --experimental-strip-types --test lib/*.test.mjs` | 402/402 pass, exit 0 |
| `npx tsc --noEmit --incremental false` | exit 0 |
| `npx eslint app/device/screen.tsx lib/home-integration.test.mjs` | exit 0, no diagnostics |
| `node drive-screen-fixture.mjs … seamfix` | 55/55 scenarios, 0 console errors |

## Hashes (sha256)

| Path | sha256 |
| --- | --- |
| `app/device/screen.tsx` | `cb5765b119cc0e0ccb781768de1c8efb9a875092414362234611352db5e1c2b2` |
| `lib/home-integration.test.mjs` | `86f5b516a291e656a30dd023c2f86b9a4dd1ee0f1f4e50c008c84f2d67ec91e9` |
| `work/ember-lantern-home-20261002/screen-fixture-entry.tsx` | `68799598256fc6534884c7155c380253d484a6cde6e9275f8d351a90a2ce032c` |
| `work/ember-lantern-home-20261002/drive-screen-fixture.mjs` | `3d6c5777e454066ff94204d60ef98ec6ca75bb314b8e3221e1f811f2db376152` |

`screen.tsx` supersedes the hash in the partial-answer retention handback
(`162f3d01…`). Nothing else in that increment changed, and the accepted
Heartbeat/Hardware and Agent source is untouched.

## Not proven

- No device, broker, real transport drop or real auth replacement was observed.
  Both new scenarios drive the real seam functions from a labelled synthetic
  fixture.
- The *missing* (as opposed to malformed) context is exercised at the same seam
  entry point rather than by a device that stops publishing a context mid-run;
  the fixture models `context=off|on` for the whole run. Rook's own probe covers
  the device-published missing reading.
- `Home` remains default-off and nothing here is device, panel or DSI acceptance.
  Full UI/runtime acceptance stays with the reviewer.
