# The lock cycle, driven through the real access boundary

10 October 2026. Ember, UI lane `c423d913-de77-4327-a98c-2e179f238f20`.
Closes item 21 of the milestone's acceptance list — "lock/epoch/sleep" — for its
lock half, and closes the sleep/lock gap that every prior increment recorded as
covered *by construction*.

**Nothing under `apps/` was changed.** The fixture mounts the real components;
it does not edit them. Every application hash in this note is the file as it
stands in the working tree, read before and after the run.

## The gap this closes

The milestone's own acceptance sentence says to exercise "stale status,
lock/epoch/sleep, focus and busy guards", and `PARTIAL-ANSWER-RETENTION.md`
recorded the honest limit on its own retention rule:

> **The lock case is covered by construction** (the hold is component state and
> the device screen is rendered only while unlocked), not by a browser lock
> cycle in this increment.

That is an argument about the code, not a measurement of it. The epoch and token
halves *were* measured — `drive-screen-fixture.mjs` rotates the authority and
replaces the token, and `work/ember-c-delivered-drive-20261010/` drives a real
authority move against the delivered bytes. The lock half had no exercising
browser probe at all: `lock.tsx` and `access.tsx` were covered only by unit tests
(`lock-settings.test.mjs`, `device-session.test.mjs`) and by the 4 October
on-unit walk, which is an observation log with no pass/fail field.

## What is real, and what is synthetic

| | |
| --- | --- |
| Real | `app/device/access.tsx` — the setup/status read, the passcode keypad, `unlock` → `activate` → `profile/get`, `lock()` and its revocation; and through it the real `screen.tsx`, `home.tsx`, `settings.tsx`, `device-session.ts` |
| Real | Layout and styling. The app's own stylesheets are injected into the bundle, so `.device-screen button { min-height: 72px }` and the rest apply exactly as on the device |
| Real | Input. Chrome DevTools Protocol trusted events at 720×1560, `deviceScaleFactor: 1`, reduced motion |
| Synthetic | The **device service only**: `127.0.0.1:3196/*` (auth broker), `:3199/status`, `:3197/*` (voice/history) and `:3198/*` (camera stream) are answered from an in-page state machine installed before any app script runs |
| Refused | Every other origin. A blocked request is recorded and throws, so this run cannot reach a live service, a LAN peer or the internet even by accident |

One deliberate intervention, stated because it shapes the result: `document.hidden`
is pinned to `false` before the app loads. The access boundary locks on
`visibilitychange` when the document is hidden, and a headless page can be
reported hidden for reasons that have nothing to do with a scenario. Pinning it
means every lock in this run is one the driver actually asked for.

## Results

`27/27 checks passed across 5 scenarios. Console errors: 0. Exceptions: 0.`
Recorded in [lock-cycle-results.json](evidence/lock-cycle-results.json).

| Scenario | Checks | What it establishes |
| --- | --- | --- |
| an unlocked owner sees the device surface | 5 | The real keypad takes the passcode; the broker is driven through `setup/status → unlock → activate → profile/get`; Home carries the brief's `Tap to talk · hold for push-to-talk`; the status strip is a real reading and is not a static "Systems online"; no non-device origin was contacted |
| Settings offers the real lock control before locking | 3 | The Settings surface renders and its **Lock now** control is present, is a genuine 135×72 target, and was pressed |
| a lock clears every protected surface | 9 | After the lock: the passcode screen is back, the device screen is **unmounted rather than covered**, and zero chat surfaces, retained question text, retained answer text, attached photo or `Incomplete` marker survive anywhere in the panel |
| the same owner unlocks again onto a cleared device | 8 | The same passcode brings Home back with the previous question, answer, photo and marker **all absent**, after a second, separate broker cycle |
| the run itself was clean | 2 | No console errors and no uncaught exceptions |

Screenshots, at 720×1560:
[01-unlocked-owner](evidence/screenshots/01-unlocked-owner.png) ·
[02-settings-before-lock](evidence/screenshots/02-settings-before-lock.png) ·
[03-locked](evidence/screenshots/03-locked.png) ·
[04-unlocked-again](evidence/screenshots/04-unlocked-again.png).

The decisive pair is 02 against 03: the same window before and after the lock,
showing the Settings surface replaced by the passcode screen with no retained
question, answer or photo anywhere.

### One measurement correction worth recording

The first version of this probe asserted on `root.innerText`. It reported the
previous question as **still present** after a lock that had visibly cleared it.
The string was in the document — React leaves stray text nodes behind — but
outside the panel's box, so nothing was on screen. A visibility claim has to be
about rectangles: the reader now walks leaf nodes and requires a non-empty
intersection with the panel.

Because a predicate that never fires passes for the wrong reason, the run also
carries a **positive control**: the same reader is pointed at a string that *is*
on screen. It returns `{present: true, absent: false}`, so the "does not come
back" checks are not vacuous. That control is counted as a check and labelled as
a control, not as a requirement met.

## Provenance

The bundle is built from the working tree at these hashes, so this is evidence
about the current revision rather than about a fixture built on 4 October — the
staleness the acceptance audit found in every other browser probe.

| Path | sha256 |
| --- | --- |
| `apps/lantern-fleet-admin/app/device/access.tsx` | `4fe99ae4fdb0a46a2291c6f1224b6ec76e1df49d11b5920a347cad1e3731202f` |
| `apps/lantern-fleet-admin/app/device/lock.tsx` | `1ac0a5e9bf5df13ff3680db988414cfdc3f2a25b04cc5b25b86e523fdaff1358` |
| `apps/lantern-fleet-admin/app/device/screen.tsx` | `a633b4957b2f29885f6a25373de600e1ec3427af96388fd7cbf753d1c7a90695` |
| `apps/lantern-fleet-admin/app/device/settings.tsx` | `18bf8475efc24a35b96d33c3828687b9dadd79d77bdab2da93418b03247cf906` |
| `apps/lantern-fleet-admin/lib/device-session.ts` | `25649d8b513d7d579f7fe00c9d04c1527343246e7c0f56d38717df3670e0c8df` |
| `probe/lock-fixture-entry.tsx` | `f0a014103e5165ebae6e734ec7839d0eaafc9b751eeef0c5418df66d1c4eb197` |
| `probe/drive-lock-cycle.mjs` | `7862d32adc54dc8ed01c0e0a9dd82908f1133182ddfc60f65e72039f18cc0606` |

`probe/smoke.mjs` and `probe/inspect.mjs` are diagnostics, kept because they are
how the fixture's two real faults were found (the stub was defined but never
installed, and the stylesheet never reached the page) rather than guessed at.
`evidence/smoke.png` is their output, not evidence about the app.

## Reproducing

```
cd work/ember-lock-cycle-20261010
node probe/build-lock-fixture.mjs
python3 -m http.server 8796 --bind 127.0.0.1 &
node probe/drive-lock-cycle.mjs http://127.0.0.1:8796/probe/lock-fixture.html
```

`LOCK_LABEL` prefixes every output file and `LOCK_CDP_PORT` moves the devtools
port, so a second run cannot overwrite this record. Both the fixture and the
driver write only inside this directory.

## Not proven

- **No device was contacted.** No SSH, no tunnel, no glass. This is evidence
  about the submitted source in a browser, never about C's panel, services,
  hardware or radio.
- **Sleep is still not exercised.** The `device-sleep` branch
  (`access.tsx:210`) and the wake guard (`lib/wake-input.ts`) remain covered by
  unit tests only. Reaching it needs the idle timers to elapse for real, which
  this increment did not attempt — it is named here so the gap is not mistaken
  for closed.
- **The lock is a UI lock.** The `3196/lock` reply is synthetic, so this shows
  the client clears its own protected state and asks the broker to revoke; it
  does not show a real broker revoking, nor a hardware key being dropped.
- **Auto-lock and sleep timers are untouched.** Only the explicit **Lock now**
  path was driven.
- **A stray text node remains in the document after the lock** (the retained
  question string, in an element with no class and no box inside the panel). It
  is not painted, and no user can reach it, but it is a real difference between
  "the DOM is clean" and "the screen is clean". Recorded as an observation, not
  as a defect: it was seen in this fixture and was not investigated in the app's
  own render path.

---

## 14 October 2026 — the floor, and a repair that was overdue

Extended by the same lane after the 72px touch-target pass raised two `lock.css`
rules (`lock.css:11` keypad base rule 64 -> 72, `lock.css:8` reveal 48 -> 72)
without a measurement, on the belief that nothing reached this screen.

### What was measured

A sixth scenario, `every lock-screen control meets the 72px floor`, measures
`.device-lock-keypad button` and `.device-lock-copy .device-lock-reveal` as
rectangles, and instantiates a 20x20 element measured by the same predicate so a
predicate that simply always passes cannot look green.

The run is parameterised by `LOCK_VIEWPORT` for a reason worth keeping:
`lock.css:23` is `@media (min-height: 550px) { .device-lock-keypad button
{ min-height: 72px } }`, so at the canonical 720x1560 panel the keypad was
ALREADY 72px before the change and that panel cannot discriminate it.

| run | panel | keys | verdict |
| --- | --- | --- | --- |
| GREEN `LOCK_VIEWPORT=1024x520` | 115x72 | 13 keys | floor held |
| RED (base rule set back to 64px) | 115x64 | 13 keys | **fails the floor** |
| GREEN 720x1560 | 125x76 | 13 keys | floor held (portrait's own 76px rule binds) |

The red run is the evidence that matters: with the old value the same assertion
reports 13 failures, so the check measures this change rather than passing
regardless. `apps/lantern-fleet-admin/app/device/lock.css` was restored to 72px
afterwards and `git diff` for that file is empty, i.e. byte-identical to the
committed state.

### The reveal could not be measured, and that is now stated

`allowReveal` is passed only by `setup.tsx` (`phase === 'choose'` and
`'confirm'`), never by the unlock path, so the control cannot render in any
lock-cycle run. The scenario records that as an explicit check rather than
passing silently: if it ever does render here, the check fails and demands a
measurement. Verifying the reveal rule needs a first-run/setup fixture, which
does not exist yet.

### Two defects in this fixture, both repaired

1. **It refused `3199/voice`, which the app now reads to establish authority.**
   The allow-list was written on 10 Oct, before `screen.tsx:377` existed, so
   every run since recorded four blocked origins and failed four scenarios on
   them — the fixture measuring its own staleness. `/voice` is served from the
   same synthetic state as `/status`, under the schema the live-voice parser
   requires. 27/32 -> 32/32 checks.
2. **A comment quoting an endpoint in unescaped backticks** closed the `STUB`
   template literal early. esbuild exited 0, `node --check` passed, and the page
   died with `ReferenceError: voice is not defined`. Fixed, and the hazard is now
   documented at the top of `STUB`; `probe/smoke.mjs` is the check that catches
   it.

### State

`probe/drive-lock-cycle.mjs` now runs 6 scenarios / 32 checks, green at both
720x1560 and 1024x520 with 0 console errors and 0 exceptions. One earlier 720 run
reported a stale exception captured from the pre-repair bundle; the clean re-run
is green and that run's evidence file (`green720-*`) should be read with that in
mind.

---

## 14 October 2026 (later) — the reveal, measured at last

The one rule from the floor pass that had no measurement is now measured, and the
reason it had none is now a feature of the fixture.

`?screen=setup` mounts the **real** `setup.tsx` instead of `access.tsx`
(`DeviceSetup` with `enabled: false` selects its 'choose' phase, which is where
`allowReveal` is true). A part-0 pass in the driver loads that screen, measures
the reveal and the keypad with the same predicates, records a scenario, and then
navigates back — so the six lock-cycle scenarios are untouched.

| run | reveal | first-run keys | verdict |
| --- | --- | --- | --- |
| GREEN 720x1560 | 170x72 | 13 x 125x76 | floor held |
| GREEN 1024x520 viewport | 170x72 | 13 x 115x72 | floor held |
| **RED** (reveal back to 48px) | **170x48** | — | **fails the floor** |

The red run is why this counts: with the old value the same assertion reports
`the reveal meets the 72px floor — 170x48`, so the check measures this change
rather than passing regardless. `lock.css` was restored afterwards and
`git diff` for that file is empty, i.e. byte-identical to the committed state.

Driver now: **7 scenarios / 38 checks**, green at both viewports, 0 console
errors, 0 exceptions.

### Caveat that applies to every number above

The fixture's preview panel stays **720x1560 whatever the viewport**, so the
1024x520 run is a 1024x520 *viewport* containing a 720-wide panel: it changes
which media queries match (`@media (min-height: 550px)` and `orientation`), not
the panel's own size. Measurements here are viewport-scoped and are stated that
way. This is the same trap that once made a set of viewport "checks" prove
nothing in an earlier fixture.

### Still unexercised

Sleep (the `device-sleep` branch), the name and access first-run screens, and
only the 'choose' phase of setup — 'confirm', 'recover', 'backup' and 'check' are
not driven. Hardware, Heartbeat and the spoken-review sheet remain outside every
instrument.

---

## 14 October 2026 (later still) — the rest of the first-run surface

Five first-run states are now measured, all by the same floor predicate and the
same injected 20x20 control, and all green at 720x1560 and at a 1024x520
viewport: **11 scenarios / 50 checks, 0 console errors, 0 exceptions**.

| state | control measured | size | floor |
| --- | --- | --- | --- |
| choose | reveal + 13 keys | 170x72, keys 125x76 | held |
| confirm | reveal + 13 keys | 170x72, keys worst 76 | held |
| recovery key | "I have saved it safely" | 253x72 | held |
| check setup | "Check again" | 164x72 | held |
| recover | 13 keys, and **no** reveal | keys worst 76 | held |

Two things this adds beyond the numbers:

1. **The phases are reached, not assumed.** The choose phase types the passcode
   and submits; the driver then *waits for the confirm heading* before measuring.
   Confirming returns a recovery key the app's own `validRecoveryKey` accepts (44
   characters, base64 shape) and the backup screen renders, so the stub answers
   `setup/enable` in the shape the screen demands rather than a placeholder.
2. **The recover mount is a control on the reveal.** `allowReveal` is false in the
   recover phase and the control is absent there — which is what ties the reveal's
   presence to the prop instead of to a stylesheet rule.

Be precise about what these five rows are worth: the reveal's evidence is the
red/green pair above (170x48 fails, 170x72 passes). The other four rows are
positive controls showing the floor *holds* on those screens; they are not
verification of a change, because no rule was altered for them.

Still unexercised after this: sleep, the name and access first-run screens, and
Hardware, Heartbeat and the spoken-review sheet.
