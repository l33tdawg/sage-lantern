# Auth context fence, and the refused-start notice

2 October 2026, 08:21 UTC. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. UI writer lane.

Authority: Rook's message `msg-7a6db3c6` (the auth context increment) plus the two decisions
already recorded in `work/auth-integration-20261002/unit-c/INBOX_REVIEW.json` —
`recording_confirmation_409` (option b) and `context_fence`. The owner selected this work
directly before it started.

## What the fence is built on

The backend contract was read, not guessed:

| Fact | Where |
| --- | --- |
| `session_context = {schema, epoch, session_ref}`, schema `sage.lantern.session-context.v1`, `epoch` 32 hex, `session_ref` 64 hex, exactly three keys, anything else `session_context_unavailable` | `software/runtime/src/sage_lantern/session_context.py` |
| `session_ref = HMAC(key, epoch + ":" + token)` — so the context is **stable across every voice publication** and changes only when the epoch rotates or the token is replaced | `scripts/lantern/unit-a-device-auth.py` `session_context()` |
| `/status` publishes the broker's current context at the top level and the voice block's own context inside `voice`; the voice block is replaced with the empty unavailable shape unless the two are equal | `scripts/lantern/unit-a-status-server.py` `_fence_voice()` |
| Every successful protected reply carries the context captured at admission, re-checked immediately before the answer is sent | `scripts/lantern/unit-a-voice-loop.py` `capture_session_context()` / `context_current()` / `respond()` |

That is what makes `voice.session_id` unusable as an epoch and the expectation counter
unnecessary: a new voice session is a publication, and only the context moves on a rotation.

## What changed

**`lib/device-session.ts`** — the fence primitive and the seam:

- `SessionContext`, `parseSessionContext` (absent → `null`, malformed → throws
  `session_context_unavailable`), `readSessionContext` (never throws; malformed → `'invalid'`),
  `sessionContextEqual` (both halves must match).
- `observeSessionContext(reading)` returns `legacy | established | same | changed | refused`.
  **This is the same-token half of the seam.** A rotation does not change the device token, so
  `setDeviceToken` never fires and nothing subscribed would learn the authority moved; the
  observation reaches those subscribers *through the same `subscribeDeviceSession` listeners*,
  rather than through a second invalidation channel beside it. `setDeviceToken` clears the
  observed context so a new token is never compared against a dead epoch.

**`lib/device-state.ts`** — `DeviceStatus.session_context: SessionContextReading`, parsed out of
the payload explicitly (`readSessionContext(value.session_context)`) instead of being spread
through as data.

**`lib/voice-control.ts`** — `replySessionContext(reply)` and `confirmTouchSession` now returns
the context the confirmation was admitted under. A reply whose context is present but
unreadable is not confirmable, so it takes the confirmation's own refusal path rather than
surfacing an internal string.

**`app/device/talk.tsx`** — the capture is admitted, renewed and finished under one context:

- captured from the `/start` reply before anything is adopted; the returned credentials are
  cancelled with their own token if it cannot be read or disagrees with what the device
  publishes;
- the confirmation must come back under both the admitted context **and** the currently
  published one;
- every `/keepalive` renewal is compared before another is scheduled; a disagreement stops the
  recording, cancels it with its own credentials and says so;
- `/stop` and `/cancel` replies are compared before the outcome is treated as this owner's;
- **409 on the confirmation** (Rook's option b): an *active* activation is told
  `The recording did not start. Tap to try again.`; a *withdrawn* one stays silent; the message
  claims nothing about a takeover, because a 409 is not evidence of one.

**`app/device/screen.tsx`** — the epoch fence replaced the expectation counter:

- `expectedSessionChanges` is **gone** (declaration, both `= 1` sites, the reset in the poll,
  the reset in `fenceActivation` and the one on unmount).
- Each validated reading is reported through `observeSessionContext`. `refused` (malformed, or
  a context that disappeared while one was established) refuses the reading outright rather
  than rendering it; `changed` fences the activation and drops everything protected under the
  old authority — handoff, draft, correction, photo, session photo, reading position and the
  conversation thread — before the new authority's reading is rendered.
- A changed `voice.session_id` **no longer fences**: admission is the parent's correlation plus
  the runtime's own confirmation, which is what already refused the foreign sessions in the
  reproduced defects.
- `not-started` from the capture owner becomes the same notice on Home, so both surfaces say
  the same thing.

## Evidence

New driver, new artifacts, historical records untouched:

```
node drive-auth-fixture.mjs http://127.0.0.1:8791/screen-fixture.html
```

**7/7 scenarios, 30/30 checks, 0 console errors** — `auth-fence-results.json` and
`screenshots/auth-*.png`. Both halves of Rook's requirement are here: positive controls
that the fence does not fire on a context-preserving device, and negative controls that it
fires on every disagreement.

| Scenario | What it proves |
| --- | --- |
| `a-context-device-records-normally` | Positive: a context device records, confirms and renews normally; the fence is not a blanket refusal |
| `a-same-token-epoch-rotation-is-fenced` | Reachable rotation (token unchanged, epoch moved) while a capture is held: ownership dropped, capture cancelled with its own credentials, surface no longer presents a usable voice lane |
| `a-rotation-clears-protected-content` | The answer is on screen under the first context and gone after the rotation; the surface is not left presenting the old authority |
| `a-confirmation-under-another-context-is-refused` | A 200 reply carrying a context the client never admitted under: credentials cancelled, nothing adopted, **no lease renewed** |
| `a-released-lease-under-another-context-stops-the-capture` | A `/keepalive` reply under another context stops the recording and says the session changed |
| `an-active-refused-start-is-reported` | 409 on the confirmation: credentials cancelled, nothing adopted, no lease, and the exact notice — with no takeover claim |
| `a-withdrawn-refused-start-stays-silent` | The same refusal after the user withdrew: **silent**, and the withdrawal was a real activation of Home's own Cancel control after the start was dispatched |

Regression, because `screen.tsx` and `talk.tsx` are shared wiring:

```
node drive-screen-fixture.mjs http://127.0.0.1:8791/screen-fixture.html auth
```

**42/42 scenarios, 233/233 checks, 0 console errors** — `screen-auth-results.json` and
`screenshots/screen-auth-*.png`. The four Home-route Back scenarios, both fault scenarios, the
silent-takeover scenario and the positive slow-start control all still pass.

Gates:

```
cd apps/lantern-fleet-admin
node --experimental-strip-types --test lib/*.test.mjs   # 330 passed, 0 failed
npx tsc --noEmit                                        # exit 0
npx eslint <changed files>                              # exit 0, 0 errors
```

New tests: the context contract in `lib/device-session.test.mjs` (malformed is refused rather
than read as absent; both halves must match; a rotation fires the one seam with the token
unchanged; a reading that cannot be compared is refused; replacing the token clears the
observation) and the wiring pins in `lib/home-integration.test.mjs` (the counter is gone, the
fence is wired to the observation, a session id does not fence, adoption/renewal/completion are
all context-gated, and the refused-start notice reaches Home while a withdrawn start stays
silent). One earlier pin of mine was **rewritten, not weakened**: it asserted the expectation
counter that this increment removed.

Hashes of the changed source and of the fixture:

```
376d4910148b4348f202e4c0f5a3905b922851043f29b10f9e1ea0c879a98f24  app/device/talk.tsx
05c2bf119eddb1f3927a53a82e54f16fb2906bc23749acf91dcef27927d849ba  app/device/screen.tsx
ec95fb1ea049427310ea366217e7c1ebd405ab632c8b5dcb7dbaa7f6dd6286bd  lib/device-session.ts
0f8fd89129bc6a7ab9bfde8bbe17290512061fb024cf4172658039dd65237b10  lib/voice-control.ts
cc3cdc58ec85f051c686946bd0cbad4e22c51f2ff336def9e45a6e1c06808b1f  lib/device-state.ts
8af515809c31f3b2e5cbd6f0d997166b7767507c287b53328785ff551d698b5d  lib/device-session.test.mjs
e745d41a5eccbee31456d2abd1a1bcce11e947b17b3f806014fc35974483bcb1  lib/home-integration.test.mjs
4555e7bb77c3b38b16890cac633990b666f7b2240386e33fb781410206c2b00f  screen-fixture-entry.tsx
e03716600ec7a8de5e3f2c009641f288f616f8c9c07668455296cda3bdb6fba6  drive-auth-fixture.mjs
```

## What the fixture models, and what it deliberately does not

`context=on` publishes the real contract: the broker context on `/status`, the voice block's own
context inside `voice`, blanking on disagreement, and the admission context on every successful
protected reply. `rotation=status` and `rotation=status-held` are **reachable** states — the
epoch moves with the device token unchanged. `rotation=confirm|keepalive|stop` answer 200 with a
context the client never admitted under, which the real runtime would refuse to send: those
three exist to prove the *client's* gate rather than the server's, and are labelled as such in
the driver and here. The default remains `context=off` (legacy unlocked device), so the frozen
and corrected records remain comparable with what produced them.

## Limits

- No Settings write, no device, no service, no model, no audio, no radio and no deployment was
  touched. `homeEnabled` stays false; this is a default-off source candidate, and nothing here is
  accepted or device-qualified.
- Context behaviour is measured in headless Chrome against the synthetic service. The runtime
  itself was read, not run.
- The fixture cannot produce a *real* same-token rotation from a live broker; it produces the
  state the broker's own code produces, with a fixed epoch value.

## Observation — not fixed, awaiting direction (O3)

After a rotation, Home's status strip reads **"Starting voice… / Waiting for the voice service."**
Measured in both rotation scenarios. That string comes from `deviceView`'s default branch
(`lib/device-state.ts:159`), which sees only the blanked voice block: the server replaces both
"the service is not running" and "this state was published under another context" with the same
empty shape, so the client cannot tell them apart from the block alone. The strip does stop
saying "Ready to ask", and the protected content is gone — but "Starting voice…" is a guess
about *why*, and the client now knows the epoch moved. The honest wording after a rotation is a
copy decision in the accepted Home 2a hash set, so it is reported rather than changed.
