# Auth context fence, increment 3: the protected reads, not only the capture

2 October 2026, 09:20 UTC. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. UI writer lane.

Authority: Rook's follow-up `msg-b97a755f-0ad6-4c33-9d33-7d28de663b61` (audit the shared protected
UI paths) and `msg-d26c0cd5-fef7-4955-80d5-a499aa74a61b` (his independent browser probes), both
extending `msg-5a31c119`. The owner authorised the full audit rather than the two reproduced paths
alone. Increments 1 and 2 are recorded in `AUTH-CONTEXT-FENCE.md` and `AUTH-FENCE-INCREMENT-2.md`.

One correction to my own earlier report: `dictation.tsx` was **my** finding from this audit, not one
Rook named. Rook named `openTyped`, question-review cancel/submit, question-correction,
suggested-question and `conversationRequest`.

## What the backend actually stamps

Read, not assumed (`scripts/lantern/unit-a-voice-loop.py`):

- `do_POST` sets `self.request_session_context = capture_session_context(device_token)` for **every**
  POST (line 1655), and `respond()` attaches it to every reply with `status < 400` and re-checks
  `context_current()` before writing the body — refusing `403 device_locked` and revoking if the
  context moved (lines 1570–1601).
- `capture_session_context()` returns `None` only when `lock_required()` is false — the legacy
  unlocked device (lines 169–179).

So the honest statement of which replies carry a stamp: **every successful protected reply on a
locked device, and none on the legacy path or on an error**. Nothing is invented for the legacy
path — the fence compares `null` to `null` there — and a locked reply carrying no stamp is refused
rather than read as "no context", which is the same rule the rest of this fence already uses.

## The shared primitive

`lib/device-session.ts` gains three things, all pinned by tests:

- `observedState: 'unknown' | 'legacy' | 'context'`. `observedContext === null` is both "nothing
  observed yet" and "the device publishes no context"; a fence that read those as one would accept a
  reply it cannot judge.
- `establishedSessionContext()` — the authority the client is currently operating under.
- `fenceProtectedReply(issuedUnder, reply)` → `ok | unverifiable | not-issued-under | moved`. The
  reply must be the one this request was admitted under, **and** that authority must still be the
  one held. A reply correctly issued under A and delivered after the epoch moved to B was true when
  written and is obsolete when it lands — that is `moved`.

## The audit, path by path

| Path | Reply adopted | What now fences it |
| --- | --- | --- |
| `lib/conversation-threads.ts` `conversationRequest` (`conversation/state`, `conversation/select`, `history/list`) | conversation state, selections, saved history — into `HistoryCache`, which has **no** generation counter | authority captured before dispatch; the reply's own stamp judged before anything is returned. The stamp is then stripped: it is transport admission metadata and does not travel into the parsers, the caches or `deepEqual`. |
| `app/device/talk.tsx` `openTyped` (`question/start`) | a typed review session | `issuedUnder = publishedContext()` before the first protected await; reply compared on both halves; a refusal cancels the credentials the reply carried and never calls `onTypedSession`. New `TYPED_SESSION_CHANGED_NOTICE`. |
| `app/device/question-review.tsx` `review/cancel` | **`recordAgain()`, which starts a new capture** | judged before `recordAgain()`; a refusal leaves the controls disabled with the existing "could not confirm cancellation" path and never replays the cancel. |
| `app/device/question-review.tsx` `review/submit` | a submission confirmed as this session's | judged after the existing correlation check. Exactly one send exists, so a refusal reports it unconfirmed and only the user's retry (same `request_id`) can repeat it. |
| `app/device/question-correction.tsx` `/correct` | a new session, `attempts.confirmed()` | judged before `attempts.confirmed` and `submitted`. This is a write that has already gone out: a refusal lands in the existing "it may have started — check before asking again" report, not a replay. |
| `app/device/suggested-question.tsx` `followup` | a new answer session | judged before `submitted`; a refusal sets the existing `uncertain` state whose retry reuses the same request. |
| `app/device/dictation.tsx` `start` (`purpose: message`) | a recording | judged before the session is held; a refusal cancels the returned credentials and says the device session changed. |
| `app/device/dictation.tsx` `message/status` | every rendered dictation phase | judged before `setStatus`; a refusal abandons the session instead of rendering another authority's progress. |
| `lib/voice-control.ts` `linkVoiceSession` | — | **Named, not fenced.** It is a write whose reply adopts no state (it returns a boolean), so there is nothing to judge. |
| `dictation.tsx` `stop`, `talk.tsx` `finish` | local phase only | A stop sets a local pending label; the next fenced `message/status`/status reading corrects it. No device state is adopted from the reply. |

## Evidence

| Run | Source | Result |
| --- | --- | --- |
| `auth-fence-auditprefix-results.json` | fences 13–16 reverted (fences 1–2 left in place) | **12/15** — scenarios 13, 15, 16 fail |
| `auth-fence-audit1-results.json` | this increment | 15/15 |
| `auth-fence-fence3-results.json` | this increment, after the red-run revert was restored byte-identically | **15/15**, 0 console errors |

The pre-fix failures are the discriminator, and they match Rook's independent probes:

- Scenario 13 — `typed=1`: `openTyped` adopted a typed review opened under the epoch that had already
  moved. Rook's probe recorded `onTypedSession1` for the same state.
- Scenario 15 — `{"outcome":"adopted", keys:[…,"session_context"]}`: the real `conversationRequest`
  handed a dead authority's conversation to its caller, and passed the stamp through with it.
- Scenario 16 (the positive control) failed pre-fix **only** on the stamp leak, which is the
  pre-existing wart this increment also closes.

`saving`/`context=off` runs are unaffected: with no context published the fixture stamps nothing and
the fence compares `null` to `null`.

The fixture was under-modelling one thing and now does not: `conversation/state`,
`conversation/select` and `history/list` were answered **unstamped** while `start`, `confirm`,
`review/*` and `question/start` were stamped. The real server stamps every POST through `respond()`,
so a locked device in this preview now stamps these too. Rook's probes described the same gap from
the other side.

## Verification

| Gate | Result |
| --- | --- |
| `node --experimental-strip-types --test lib/*.test.mjs` | 342/342 pass (was 331; +11, including the new `lib/protected-reply-fence.test.mjs`) |
| `npx tsc --noEmit -p tsconfig.json` | exit 0 |
| scoped `eslint` on the eleven changed product/test files | exit 0 |
| `node drive-auth-fixture.mjs … fence3` | 15/15 scenarios, no console errors |

Three existing tests modelled a *locked* device whose replies carry no stamp and whose authority was
never established (`conversation-request.test.mjs`, `history-cache.test.mjs`). They now model the
contract the server actually implements — a stamped reply and an established reading — rather than
being rewritten to match the new code, and two new tests were added that prove the fence refuses:
one for a reply issued under an authority the client has left, one for a locked reply carrying no
stamp. The negative control for the fence's own positive case is a stamped reply with nothing moving
underneath it, which is still adopted.

Repo-wide `eslint .` reports **8 pre-existing errors in 4 files**, none introduced here:
`nearby-directory.tsx` (3), `question-correction.tsx` (3, `react-hooks/refs` at a JSX line this
increment did not touch — the HEAD version of that file reports the same 3 errors), `share.tsx` (1)
and `share-render.test.mjs` (1). They are not fixed here.

Hashes (sha256):

| File | sha256 |
| --- | --- |
| `lib/device-session.ts` | `ad275cbc53f537668d20516977754ecad5d0fd958505737c18ad21ca592fb2e5` |
| `lib/conversation-threads.ts` | `d149059d240b79f870793253f5c6d3e40808c05ad826384cbb2772041e2b8429` |
| `app/device/talk.tsx` | `d04996276ac6f2cf7ad2fa5c02936dd6dd6f350e77e70d1fae157f5e8e151687` |
| `app/device/question-review.tsx` | `247275696c26f2c34f8d4a30d7623c00fdbebb0a19cdff1b3f379414ee7ad499` |
| `app/device/question-correction.tsx` | `573d6570e34f842af2b589ae7ecadafda78e7c6ece187378044e7257f568ac58` |
| `app/device/suggested-question.tsx` | `42fa64c5098160994acaebf2b1da9831fa92a7e6248a4fd2d8ca2862dceca43a` |
| `app/device/dictation.tsx` | `54b2d0998904c77899069956f6e67366992f7d02e4a4f803bfb70283238e7a45` |
| `lib/protected-reply-fence.test.mjs` | `3c288a28b8ac33e509b00df4513aba22cbee64bc8bc7241bafef5c0d596f7405` |

## Still open

- O2 (menu-origin navigation) and O3 (blanked-publication copy) — authorised by Rook, still not
  started; the owner has kept both handbacks to the fence work.
- Whole acceptance, successful Settings writes, real consent, native policy and physical focus, plus
  every device/deployment gate Unit C owns. Source stays default-off, `homeEnabled` false; no device,
  service, model, audio, radio or deployment path was contacted.
