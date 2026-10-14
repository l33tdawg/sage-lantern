# Refusal authority: a reading that cannot be compared drops the authority, tells the seam, and stays unusable

2 October 2026, 10:03 UTC. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. UI writer lane. Bounded source-only correction of the
refused-context gap, in the increment-5 lane.

Authority: Rook's `msg-eb1aac13` (the reproduced gap and the correction request) and `msg-f5a2da91`
(the owner's direct "lets work on that", confirming this is the work). `msg-6cfe4b57` supplied the
independent acceptance matrix. The O2/O3 increment is recorded separately in
`MENU-ORIGIN-AND-VOICE-COPY.md` and its hashes are unchanged by this correction.

## 1. Where I was wrong

I named this gap in increment 5 as residual 2, and I understated it. I wrote that a refused reading
"is refused at the reply but already-rendered cached content is not cleared". Rook reproduced it and
found it is stronger than that, and he is right:

```
establish A; originalAuthority = establishedSessionContext();   // {state:'context', context:A}
observeSessionContext('invalid')  → 'refused', 0 notifications
establishedSessionContext()       → still {state:'context', context:A}
fenceProtectedReply(originalAuthority, replyA) → 'ok'
```

So the refusal was cosmetic. The authority stayed advertised, the seam was never told, and a
well-stamped late reply under the *previous* authority still fenced `ok` — because refusing a reading
had never invalidated the expectation it was refused against. Re-establishing the same context A left
the old in-flight reply acceptable too. My own real-component suite could not see any of it: every
rotation scenario I wrote injects a **changed** reading, which did notify, so the refusal path was
never exercised.

## 2. The correction

`lib/device-session.ts`:

- **A refusal now drops the authority.** `loseAuthority()` clears `observedContext`, moves the state to
  a new `'lost'`, and notifies the one seam. That notification is what invalidates retained content,
  cached consent challenges and in-flight work in the same tick — the wiring already added in
  increment 5, which until now had nothing to fire on.
- **Nothing is downgraded to legacy by a failed reading.** A device that was publishing a context and
  then publishes none is refused, not read as the unlocked path; `'lost'` refuses `null` and `'invalid'`
  indefinitely. Only a fresh valid reading re-establishes an authority.
- **A generation counter separates "the same context" from "the same authority".** `authorityGeneration`
  moves on a replaced token, on a changed context, and on a refusal. `EstablishedContext` now carries
  `generation` for a context, and `fenceProtectedReply` refuses an expectation whose generation is no
  longer current — which is what stops A→B→A and lose-then-restore from reviving work issued before
  the move, even though the context compares equal.
- **The diagnostic labels are preserved.** The generation check runs *after* the context comparisons,
  so a reply issued under A and landed after A→B still reports `'moved'` rather than being flattened
  into `'not-issued-under'`.

`lib/community.ts`: `communityRequest` now refuses **before dispatch** when there is no usable
authority (`'unknown'` or `'lost'`), not only when judging the reply. Rook identified this precisely:
fencing after the fetch is a fence around the answer, not around the act — a message, a read receipt or
a consent challenge has already left the device by then. `'legacy'` remains usable, so the cold
unlocked path is untouched.

## 3. One deliberate change to frozen increment-5 copy, named for review

The read-receipt control's rotation notice is now reached by a refusal as well as by a rotation, and
"The device session changed" is not true of a refusal — the session may not have changed at all, it
just could not be verified. The same reasoning I applied in O3 applies here, so the copy is now:

```
The device session is no longer verified. Reopen the message to review it again.
```

This edits a string frozen in increment 5. It is in this increment because the refusal path is what
newly reaches it, and I am flagging it rather than burying it: if Rook prefers a distinct notice per
cause, that is a two-line change.

## 4. Verification

**Rook's independent matrix**, run against my source with `REFUSAL_APP_ROOT`:

```
node --experimental-strip-types --test work/rook-auth-fence-review-20261002/refusal-authority-independent.test.mjs
```

- Before: **29 cases, 5 pass, 24 fail** — I reproduced his baseline exactly before touching anything.
- After: **29 cases, 29 pass, 0 fail.**

**My own suite**, from `apps/lantern-fleet-admin`:

- `npm test` — **367/367 pass, 0 fail** (363 before this correction; +4 net: 2 new refusal
  discriminator tests over the real components, taking `protected-surface-rotation.test.mjs` from 4 to
  6, and 2 new contract tests in `community-fence.test.mjs`, taking it from 10 to 12 — alongside the
  reshaped device-session cases).
- `npx tsc --noEmit --incremental false` — exit 0.
- `npx eslint` on all eight changed files — exit 0.

**Red run — the new discriminators catch the defect rather than merely passing.** Reverting the single
line `if (reading === 'invalid') return loseAuthority();` back to `return 'refused';`:

- my two new real-component refusal tests fail (5 and 6), while all four rotation/legacy controls still
  pass — the discrimination is exactly on the refusal path;
- Rook's matrix drops from 29/29 to **18/29**.

Restoring the line returns both to green.

**Six tests that pinned the old behaviour were corrected in place**, and each was pinning a defect:

- `device-session.test.mjs` asserted `observeSessionContext(null)` on a device that had just published
  a context is `'legacy'` — the downgrade Rook's correction forbids.
- The same file asserted the whole `EstablishedContext` shape, which now carries a generation.
- `community-fence.test.mjs` expected the post-reply message where the refusal is now pre-dispatch.
- `protected-reply-fence.test.mjs` pinned the old union and the old `'moved'` return form.
- `share-refresh.test.mjs`'s lock/cancel case never established an authority, so it silently relied on
  reaching the network without one.

## 5. Changed files and hashes

| sha256 | file |
| --- | --- |
| `b6d0684b7f7e398decdb5d356ead057fa3977a56d8a57b5283a3d9bd9ef72425` | `apps/lantern-fleet-admin/lib/device-session.ts` |
| `10ce55117e15b35d7557f75c3da615eb2f17ac44dca9ad21bb093047e1f5b9b2` | `apps/lantern-fleet-admin/lib/community.ts` |
| `bb42cdc0f15892567e536bb625209b6b38cfa1694d83ebb7572238f9ea5a7542` | `apps/lantern-fleet-admin/app/device/read-receipt.tsx` |
| `7dd497eb6585838e854fc2cd39dd097c3c996d2f86bafe67e923e4b698c01893` | `apps/lantern-fleet-admin/lib/device-session.test.mjs` |
| `478c41aa5dbf9f3710517e0d9ab2d4ff5b11217a5af6e54f3ae9deb600e45e5e` | `apps/lantern-fleet-admin/lib/community-fence.test.mjs` |
| `df79ac68203182cf46a81a9acc813eba0325596a6f7ab74100697cd52a252634` | `apps/lantern-fleet-admin/lib/protected-reply-fence.test.mjs` |
| `1910eea76c24c9b54c6d3f568096325e80ced31db8e480a46fae4b3571f5f009` | `apps/lantern-fleet-admin/lib/share-refresh.test.mjs` |
| `00a86eac3652b66dc383bb78ca8b0dafd3994fa1d45089552d7bd91ac97a7ffa` | `apps/lantern-fleet-admin/lib/protected-surface-rotation.test.mjs` |

Unchanged by this correction and still as handed back in `MENU-ORIGIN-AND-VOICE-COPY.md`:
`app/device/screen.tsx` `f0b95d4b…`, `lib/device-state.ts` `57c279ee…`, `lib/device-state.test.mjs`
`682afeee…`, `lib/home-integration.test.mjs` `5aec15bd…`, `lib/menu-origin-navigation.test.mjs`
`af9726dd…`.

## 6. Named residuals — open, not smoothed over

1. **This is not a whole-fence claim.** It corrects the refusal, the downgrade, the restoration and
   the pre-dispatch paths Rook probed. Owner authentication, consent refusal, private policy, the
   native/private SDK action, hardware and deployment remain open and untouched.
2. **Cold-first-frame and live-stamp qualification remain open**, as Rook says. A community request
   still needs a reading to have established an authority, and everything here was measured against
   synthetic transport — no live broker, no device.
3. **`conversationRequest` has no pre-dispatch guard.** Rook's matrix requires delayed conversation
   *reads* to be rejected after restoration, which the generation fence does; it does not require
   conversation reads to be blocked before dispatch, and I did not add that. If the same
   act-not-answer argument applies to history reads, it is a further bounded change.
4. **The `'moved'` label is now reachable only from a stale-context reply**, not from A→B→A, which
   reports `'not-issued-under'`. Both are refusals; the label difference is diagnostic only.
5. The read-receipt copy change in §3 is a change to previously frozen text.

## 7. Boundaries preserved

Source-only, default-off, `homeEnabled` false. No new writer. No device, service, session, radio,
model, audio, installation or deployment action. No private-action enablement. No live send, receipt or
consent challenge — every write path was exercised against synthetic transport. No automatic replay
anywhere: the matrix asserts exactly one original dispatch and zero refusals-then-retries.
