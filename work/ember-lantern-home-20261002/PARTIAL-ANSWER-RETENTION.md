# Partial answers: retention, the `Incomplete` marker, and the stream cases

3 October 2026. Ember, UI lane `c423d913-de77-4327-a98c-2e179f238f20`. Source and
fixture increment, handed back for review.

## The gap this closes

The brief's PROGRESSIVE/DISCONNECT rule says a normal interruption in the same
authorized session may retain partial output **explicitly marked `Incomplete`**, and
that lock, owner/epoch change and retention policy still clear protected content.
Home already had the marker and the live caret, but nothing ever set it:

- `screen.tsx` contained zero occurrences of `incomplete` and rendered `<Home>`
  without the prop, so the marker was unreachable in the real screen;
- the answer text is the latest accepted reading (`homeFeedback = answerVisible ?
  text : ''`), so when the device stops answering mid-answer the half-spoken words
  simply disappeared — there was nothing left on the wire to render;
- my own 2a note recorded the reason: *"the status contract publishes no interruption
  flag"*.

So retention is derived UI-side from what the screen already observes rather than
asking the runtime for a new field. That keeps it inside the standing prohibition on
backend/session redesign, and it is the design the owner approved before this work
started.

## The rule as implemented

A **hold** (`partialTurn` state in `screen.tsx`) records the last answer a reading
was streaming, with the session and the question it belonged to. A hold is taken
only from an **accepted** reading (`deviceView(next, true).phase === 'processing'`
with non-empty `voice.response`), so a stale, refused or superseded reply can
neither set one nor clear one. It is dropped by:

| Fence | Where | Why |
| --- | --- | --- |
| Completed answer | poll, `phase === 'complete'` | completion is exactly what makes `Incomplete` untrue |
| Epoch or owner change | poll, `observation === 'changed'` | protected content never outlives its authority |
| New voice session | poll, session id changed | a new turn's words are not the previous turn's |
| New chat | `clearAnswer()` | the user asked to stop holding the last turn |
| Lock | unmount | the device screen is rendered only while unlocked |

The turn is *shown* only while the hold still belongs to the session on screen and
was not cleared by New chat. An explicit failure is not an interruption: when the
device states an error the error stands alone rather than being dressed up as an
unfinished answer.

Both surfaces carry it, with the same words and a matching marker:

- **Home** — `answerText` is the retained partial and `incomplete` is set, so the
  existing `device-home-incomplete` marker appears beside the device's own words,
  and the caret is absent because nothing is streaming.
- **Chat** — `QuestionTurn` gained an `incomplete` prop and renders
  `device-answer-incomplete` (declared in `question-flow.css` with the same values as
  Home's marker). The turn is rendered at all, instead of the reconnecting
  placeholder taking its place.

## Evidence

Fixture parameters added for this (`screen-fixture-entry.tsx`, fixture-only):

- `interrupt=<n>` — the device stops answering after `n` status reads and publishes
  **no further words**, so anything still on screen afterwards can only have come
  from the hold. This is the case a screen that renders the wire cannot cover.
- `grow=1` — each read while processing appends a numbered sentence, so text moves
  (for the follow rule) and a duplicated or replayed reading would show a number
  twice.
- The `streaming` scenario now publishes its transcript, because a device answering a
  question also publishes the question — which is what Home shows beside the answer.

Five scenarios added to the integrated driver (`drive-screen-fixture.mjs`), run
against the real `screen.tsx`:

| Scenario | Checks | What it establishes |
| --- | --- | --- |
| `a-partial-answer-survives-a-normal-interruption` | 6 | caret while streaming; the words retained exactly as they streamed (no duplication or extension); the marker present; the caret gone; the question kept with the answer |
| `the-retained-partial-is-marked-in-chat-too` | 4 | the same turn in Chat, marked, with no placeholder instead |
| `new-chat-drops-the-retained-partial` | 4 | the explicit drawer New chat clears the hold |
| `an-authority-rotation-drops-the-retained-partial` | 3 | retention live, then `rotateAuthority()` clears it — asserted in that order, so the check is not vacuous |
| `streaming-text-never-doubles-and-a-released-reader-stays-put` | 7 | the answer grows monotonically, no sentence twice, the reader has something to scroll, it follows at the bottom, a released reader stays where it was left |

**Red run.** With the hold not taken (the single write removed, everything else
intact), the driver gives **49/53**: exactly the four retention scenarios fail —
`a-partial-answer-survives…` (6/6 → 2/6 in its retention checks), `the-retained-
partial-is-marked-in-chat-too` (2/4), `new-chat-drops-the-retained-partial` (3/4),
`an-authority-rotation-drops-the-retained-partial` (2/3) — all on the *precondition*
that the partial was retained. The other 49 scenarios, including the streaming
growth and released-follow one, stay green, which is what says those two pin
existing behaviour rather than this change. The red run was taken before the
ref→state correction below; the write site is the same and the final source was
re-verified green.

**Green run.** `53/53 scenarios passed. Console errors: none` (48 before this
increment).

## One correction worth recording

The first version held the partial in a `useRef` and read it during render. The
repository's lint refuses that (`react-hooks/refs` — *"Cannot access refs during
render"*, 24 errors), and it is right for a reason that matters here: a ref read
during render does not re-render when it changes, so the marker could lag the words
it describes. The hold is `useState` now, written where the reading is accepted.

## Gates

| Gate | Command | Result |
| --- | --- | --- |
| Typecheck | `npx tsc --noEmit --incremental false` | exit 0 |
| Library suite | `node --experimental-strip-types --test lib/*.test.mjs` | **400/400 pass** |
| Scoped lint | `npx eslint app/device/screen.tsx app/device/question-turn.tsx` | exit 0 |
| Integrated Screen fixture | `node drive-screen-fixture.mjs <url> partial` | **53/53 scenarios, 0 console errors** |

Re-run from this directory:
`node build-screen-fixture.mjs && python3 -m http.server 8791 --bind 127.0.0.1`
then `node drive-screen-fixture.mjs http://127.0.0.1:8791/screen-fixture.html partial`.

The `partial` label redirects every output to `screen-partial-*`, so the frozen
`correction2c` record beside it is untouched.

## Hashes (sha256)

| Path | sha256 |
| --- | --- |
| `app/device/screen.tsx` | `162f3d01ef25521e22ead29faafa4a80f1f3590f200dd56d28740f009f2e2862` |
| `app/device/question-turn.tsx` | `6f0b8e981236f08709f1979c25180b4477df77a8d87c83d1904bc3ffd27aca59` |
| `app/device/question-flow.css` | `f502e5f36a764ae06b24a2afab0b2ff61b1049c9efdec772965cd0b2927832cd` |
| `work/ember-lantern-home-20261002/screen-fixture-entry.tsx` | `67f0717342209dfced2b8b01d9d8be639f16dfd395238fe712ca436e659cc97f` |
| `work/ember-lantern-home-20261002/drive-screen-fixture.mjs` | `5b5bf60afab7167b5a742fbba1efb58df57effc6013810900ce6d521f4e64373` |

These three application hashes supersede the ones the 2a record lists for the same
files; every other 2a/2b hash is unchanged by this increment.

## Not proven

- **No runtime interruption signal exists, and none was added.** The rule is derived
  from phase transitions the screen already sees; if the runtime later publishes an
  explicit interruption flag, this derivation should be replaced by it rather than
  kept beside it.
- **The fixture's interruption is a synthetic device that stops answering.** No
  device, broker or real transport drop was observed; transport-level interruption
  on the target is not qualified here.
- **The lock case is covered by construction** (the hold is component state and the
  device screen is rendered only while unlocked), not by a browser lock cycle in this
  increment.
- `Home` remains default-off and nothing here is device or panel acceptance.
