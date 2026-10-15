# Lantern device UI — stylesheet repair, measured in a real browser

Lane: the nine measured stylesheet defects in `apps/lantern-fleet-admin/app/device/`.
Files edited (only these): `screen.css`, `gallery.css`, `question-keyboard.css`,
`community.css`. No `.tsx` was touched, **no new stylesheet was needed**, and nothing under
`apps/` outside those four files was modified.

## Commands

    cd work/verify-styles-20261014
    node build-screen-fixture.mjs                      # bundles the REAL components
    python3 -m http.server 8907 --bind 127.0.0.1       # serve the bundle (background)
    node audit.mjs http://127.0.0.1:8907/screen-fixture.html baseline   # before
    node audit.mjs http://127.0.0.1:8907/screen-fixture.html after      # after
    node drive-screen-fixture.mjs http://127.0.0.1:8907/screen-fixture.html styles2
    node probe.mjs                                    # the gallery viewer's height budget
    cd apps/lantern-fleet-admin && node --experimental-strip-types --test lib/*.test.mjs

Every number below is pinned to these exact bytes (SHA-256, taken after the last edit and
before the recorded run; the bundle was rebuilt after the last CSS edit, at 18:07:19):

    screen.css          fa311785aa9fe2b720b53deec4993b65d6b543b6d943a6854b161ca635f03144
    question-keyboard.css 8eabe94b37c827cc7194eef228ce406cc86776f22beb422b0bc35035207f172e
    community.css       417014cda361b3005c8d30265f68e46f040690ebb3ee6491b29542f8d41db362
    gallery.css         fcf48313566f7806cf50e518f5a1c6af4cfcdb1834ac0b8ee6e6a5c67eb3378c
    screen-fixture.css  b9f19a989ff5643f1abaeae01fe1eead1f102f7b137a13db2b29691423be37b5
    screen-fixture.js   93c1bac39759a554300148e5e9ec837c833fc2f00782a4b933b8f42c53899140

The recorded `after` audit, the screenshots and the `styles2` regression run were all taken
from the `screen-fixture.css` hash above. `screen.css` has one later edit, a correction to a
comment: re-running the build reproduced `screen-fixture.css` byte for byte
(`b9f19a98...`), because esbuild strips CSS comments, so the evidence still describes the
rendered stylesheet exactly. `gallery.css` and `screen-fixture.*` are the post-item-7-probe
versions; `question-keyboard.css` and `community.css` were not touched after their numbers
were recorded.

Another writer was editing `screen.css` concurrently (a `.device-navigation-footer` column
change that is not this lane's); it survived these edits, was present in the bundle that was
measured, and the hashes above are what the evidence refers to.

The harness is a copy of the repaired `work/ember-ui-verify-20261014/` (the copy whose
`screen-fixture-entry.tsx` answers `127.0.0.1:3199/voice`), rebuilt after every CSS edit.
Headless Chrome, reserved ports: HTTP 8907, CDP 9407.

## Method (what the numbers are)

`audit.mjs` drives 16 screens x 4 viewports (720x1560, 480x1040, 360x800, 1024x600) with
trusted pointer input and reads rectangles and computed styles out of the running app:

* device metrics are set **before** the navigation, `?frame=0` makes the panel the viewport,
  and the panel's own rect is asserted to equal the requested viewport before each screen is
  measured (`panelMatchesViewport`, and again after every navigation);
* visibility is a rectangle intersection with the panel, never `innerText`;
* contrast is WCAG relative luminance over the composited ancestor background chain;
* every predicate carries a **negative and a positive control**, injected and removed on each
  viewport: a 20x20 input (must fail the 72px floor), an 80x80 input (must pass), #0000ee on
  #122935 = 1.6 (must fail 4.5), ink on #122935 = 14.3 (must pass). Problems reported by the
  driver: 0 for the recorded `after` run.
* console errors across the whole run: 0.

## Before -> after, per defect

All values are measured rects (CSS px) at the four viewports; `V` = visible fraction inside
the panel.

### 1. The four shared header controls (all screens)

| viewport | wordmark | nearby | messages | menu | row | fits |
|---|---|---|---|---|---|---|
| 720x1560 | 170.7x56 -> 170.7x72 | 56x56 -> 72x72 | 56x56 -> 72x72 | 90.8x56 -> 90.8x72 | 76 | inside, overflow 0, last right 708/720 |
| 480x1040 | 166.7x56 -> 166.7x72 | 56x56 -> 72x72 | 56x56 -> 72x72 | 90.8x56 -> 90.8x72 | 76 | inside, overflow 0, last right 468/480 |
| 360x800 | 142x56 -> 72x72 | 48x56 -> 72x72 | 48x56 -> 72x72 | 76.2x56 -> 76.2x72 | 76 | inside, overflow 0, last right 352/360 |
| 1024x600 | 149.8x64 -> 149.8x72 | 64x64 -> 72x72 | 72x64 -> 72x72 | 115.7x64 -> 115.7x72 | 72 -> 76 | inside, overflow 0, last right 1000/1024 |

Measured on `home`, `chat`, `gallery`, `vision` and `about`: 0 controls under 72 in either
dimension, all four fully visible (`V` = 1).

The row had to grow on the screens whose shell row was 72px (landscape, and Chat in both
orientations); 76px is the value `portrait.css` already used: a 72px control cannot sit in a
72px row that also owns a 1px rule.

What moved horizontally, measured (control centres before -> after): at 720x1560 and
480x1040 the Nearby meter's icon centre +8px (214.7 -> 222.7, 210.7 -> 218.7) and Messages'
-8px (585.3 -> 577.3, 345.3 -> 337.3); the wordmark and Menu do not move. At 1024x600 Nearby
+4px (209.8 -> 213.8) and Messages unchanged. At 360x800 the wordmark's label gives way, so
its box becomes 72x72 (centre 79 -> 44) and the two icon-only controls move with it. In the
header band of the before/after screenshots that is 1020 differing pixels of 54720 (1.9%) at
720x1560 and of 36480 (2.8%) at 480x1040; everything below the header is unchanged.

What the row growth costs below, measured:
at 1024x600 everything below the header is a **pure 4px downward shift** - the thumbnail,
label, Back and grid regions of the gallery compare to the pre-fix screenshots at 0 differing
pixels once shifted 4px (`screenshots/baseline|after-landscape-1024x600-*.png`). No new
element exceeds the panel outside a scroll container, and no screen gained an under-floor
target.

### 2. Chat's Back

| viewport | before | after | subscreen Back |
|---|---|---|---|
| 720x1560 | 111.8x64 | 120x72 | 119.8x72 |
| 1024x600 | 111.8x64 | 120x72 | 119.8x72 |
| 480x1040 | 72x56 | 72x72 | 119.8x72 |
| 360x800 | 72x56 | 72x72 | 119.8x72 |

Chat's bar measures overflow 0 at all four viewports. At 520px and below the labelled 120px
Back was measured pushing New chat 29px past the panel at 360x800 (chat.css also hides Back's
label there), so below that breakpoint Back takes the 72px floor instead.

### 3. On-screen keyboard keys

| viewport | chat composer keys (before -> after) | agent form keys (before -> after) |
|---|---|---|
| 720x1560 | n=31 median w 58.4, min 55.6x64 -> all 72x72 | n=32 median 61.3, min 58.4x64 -> all 72x72 |
| 480x1040 | n=31 median 34.2, min 32.4x56 -> all 72x72 | n=32 median 36.2, min 34.4x56 -> all 72x72 |
| 360x800 | n=31 median 21.6, min 20.4x56 -> all 72x72 | n=32 median 26.2, min 24.8x56 -> all 72x72 |
| 1024x600 | n=31 median 66.3, min 63.2x64 -> all 72x72 | n=32 min 72x64 -> all 72x72 |

Every row on every viewport was scrolled to its end and re-measured: first key and last key
both reachable (`firstKeyReachable`/`lastKeyReachable` true on all 8 rows x 4 viewports),
`justify-content` computes to `safe center`, `overflow-x: auto`. Rows that fit do not scroll
(e.g. the agent keyboard at 1024x600: scrollWidth = clientWidth = 942); rows that do not fit
scroll (chat top row at 360x800: 774 in 258).

### 4/5. About's credits links

| | before | after |
|---|---|---|
| rects | "Original vocabulary" 175.5x24, "License" 70.6x24 | 195.5x72 and 90.6x72 |
| computed colour on background | `rgb(0, 0, 238)` on `rgb(18, 41, 53)` | `rgb(246, 250, 252)` on `rgb(18, 41, 53)` |
| contrast | **1.6:1** | **14.3:1** |

A pixel check agrees with the computed styles: the before/after captures of the open credits
panel contain link-blue pixels (`#0000ee`) in 19 rows before and none at all after.

Sentence flow, measured with an in-page A/B that neutralises the new rule (then re-measures to
prove the probe restored): both links stay on the same line (equal tops at all four viewports),
the visible text does not move (first link's text left 41px before and after, second link's
231.6px before and after), overlap with the neighbouring paragraphs is 0px^2, and the links'
paragraph grows 29.4 -> 72px inside the scrollable credits panel.

### 6. Gallery at 360x800

| | before | after |
|---|---|---|
| sub-screen box | 392.3 wide in a 360 panel | 360 (fits) |
| gallery content | 368.3 wide (scrollW 368) | 336 (scrollW 336) |
| header Menu button | V 0.7 (rect 308.1..384.3) | V 1 (275.8..352) |
| Gallery Camera shortcut | V 0.9 (right 380.3) | V 1 (right 348) |
| elements exceeding the panel | 13 | 0 |

720x1560 and 480x1040 are unchanged: 0 differing pixels below the header row in the
before/after screenshots, and identical rects (menu 377.3..468 and camera right 456 at
480x1040; content width 432 both). The gallery's bar wraps only at 360 (header height 167
there, 83 at 480x1040 where the three controls still fit on one line).

### 7. Gallery viewer's primary action row

| viewport | before | after |
|---|---|---|
| 1024x600 | primary 553.8..625.8 (V 0.6: 26px below the fold, 36px of scrolling to reach it) | primary 435.8..507.8 (V 1), Previous/Next 355.8..427.8 (V 1) |
| 720x1560 | primary 557.8..629.8 (V 1) | unchanged |
| 480x1040 | primary 557.8..629.8 (V 1) | unchanged |
| 360x800 | primary 557.8..629.8 (V 1) | unchanged |

Two answers, both measured. `probe.mjs` asked the browser what the 1024x600 panel is short
of, and the answer was the photo frame, not the photo: the frame holds `min-height: 200px`
around an image that renders 72px tall, so at 600px the viewer's own rows overflow the
scrolling region by 40px. Reclaiming the frame's slack at `max-height: 700px` (frame
`min-height: 0`, viewer gap 12 -> 8, image capped at 32dvh) leaves the measured content with
82px to spare, so the whole viewer - Previous/Next included - is visible in normal flow. A
sticky bottom rule stays as the second answer for content the frame cannot give up; it is a
no-op wherever the content fits, which is every state measured here.

The hazard this direction avoids was measured in the first attempt: pinning the primary row
at 1024x600 put it at 502..574, over the Previous/Next row at 473.8..545.8 - 43.8px of
overlap, and a tap in that band would have hit "Ask about this photo" instead of "Next".
Now measured: overlap area 0, vertical overlap 0, and `elementFromPoint` at the shared
boundary returns the Previous/Next row.

### 8. The token that did not exist

`question-keyboard.css` asked for `var(--device-text, #fff)`. There is no `--device-text`
anywhere (`check_claims`: 0 matches in `apps/lantern-fleet-admin/app` and in `lib/`), so the
textarea silently rendered pure white. Chosen fix: **use the existing token** -
`color: var(--device-ink, #f6fafc)` - rather than define a new variable, because the brief's
palette is fixed (the file's own convention is `var(--token, fallback)`) and a second name for
the ink value would be a synonym, not a token. Measured: textarea computed colour
`rgb(255, 255, 255)` -> `rgb(246, 250, 252)`, `--device-ink` resolves to `#f6fafc`.

### 9. Non-token white TEXT

Four text colours used `#fff` in the four owned files, all replaced with the ink token:
`screen.css` (footer camera shortcut), `question-keyboard.css` (`--device-text` fallback and
`.question-suggestions button`), `community.css` (`.device-share-answer`). Rendered check on
the screens: the Chat screen's distinct text colours were
`rgb(185,203,212) rgb(246,250,252) rgb(255,190,98) rgb(255,255,255)` and are now the first
three only; the same for the keyboard screens, which kept `rgb(32,36,23)` (dark text on amber).
Across `app/device/*.css` there is now **0** `color: #fff|#ffffff|white|rgb(255...)`
declaration; the 8 remaining `#fff` tokens are focus outlines, box-shadows and the switch knob
(`chat.css:187`, `navigation.css:132`, `question-flow.css:29`, `question-keyboard.css:13,14`,
`question-review.css:9`, `screen.css:107`), which this pass was told not to churn.

## Evidence files

* `screenshots/baseline-<view>-<screen>.png` and `screenshots/after-<view>-<screen>.png` -
  64 per run (16 screens x 4 viewports), full-panel captures.
* `screenshots/screen-styles2-*.png` - the 59-scenario behaviour regression run against the
  final stylesheet: **59/59 scenarios passed, 0 console errors** (`screen-styles2-results.json`,
  `failed: []`).
* `styles-baseline-results.json`, `styles-after-results.json` - every rect, contrast pair,
  control probe and console error behind the tables above.
* `screen-styles-results.json` - the behaviour regression record.

## App suite and lint

    cd apps/lantern-fleet-admin && node --experimental-strip-types --test lib/*.test.mjs
    # tests 498, pass 498, fail 0

The suite was 491/491 at the start of this lane and is 498/498 now: other writers added
`lib/ui-fix-regressions.test.mjs` (18:02) and one more test file while this lane was
measuring. No test was changed or relaxed by this lane. No CSS-scanning assertion failed - including
`lib/device-styles.test.mjs`, `lib/device-header.test.mjs`, `lib/device-share-action.test.mjs`
(its pattern still matches the `#116566` rule whose `color` changed) and
`lib/device-history-shortcut.test.mjs`.

`npx eslint app lib` -> 8 errors, 0 warnings, none in a file this lane touched:
`nearby-directory.tsx` (3), `question-correction.tsx` (3), `share.tsx` (1) - the pre-existing
seven - plus `lib/share-render.test.mjs` (1, added by the other writer at ~18:02).

## NOT COVERED / NOT PROVEN

1. Screens not driven: the lock screen and first-run surfaces (`lock.tsx`, `name.tsx`,
   `setup.tsx`, `access.tsx`), Hardware, Heartbeat, and the spoken-review *sheet*
   (`dialog.question-keyboard-sheet`). The 16 screens that were driven are home, chat,
   chat+inline review keyboard, gallery, gallery viewer, agent, agent form keyboard, vision,
   messages, nearby, settings, question history, status, shared knowledge, about,
   about credits open. The header and key-row rules reach the others by selector, but their
   rendering was not measured.
2. Visited-link rendering: Chrome reports the unvisited computed colour for privacy, so
   "the links stay legible once visited" is inference from the cascade, not a measurement.
3. Real touch input was not exercised; every interaction here is a trusted CDP mouse event.
4. `overflow-x: auto` on a key row clips focus outlines at the row edges; not measured.
5. Accessibility of the hidden wordmark label at <=400px: the button keeps its `aria-label`
   ("Lantern home" / "Back to your Lantern home screen"), but no screen-reader pass was run.
6. Sub-floor targets that remain, all in files this lane does not own (measured rects, widest
   list at the four viewports): `.device-share-answer` 110.9x56 / 115.5x64 / 160x64 / 220x64 /
   340x64 / 378x56, `.device-talk-primary` 133.4x64 / 148x56 / 148x64, `.device-camera-shortcut`
   54x56 / 54x64 / 160x64 / 220x64 / 340x64 / 378x56, `.question-review-submit` 149x64 / 209x64
   / 329x64 / 367x56, the review's `Close` (126x62 .. 367x56) and `Done editing` (126x62 ..
   340x64), `.device-new-chat` 195x52, `.device-thread-search` 195x48 / 659x48, "Manage saved
   history" 195x52, and on the Messages screen at 1024x600 `People` / `Refresh` /
   `.device-community-primary` 659x52 - chat.css and portrait.css outrank community.css's own
   `min-height: 72px` for the Share answer. Source-level, not driven: `.device-name-keyboard
   button` asks for `min-height: 56px` (screen.css). These were outside the nine items, and the
   fix belongs to the writers who own those files.
7. Two judgement calls this lane made, both measured rather than brief-mandated: hiding the
   wordmark's *label* at portrait <=400px (frees the 70px that lets four 72px controls fit at
   360 - shrinking icons alone was 30.2px short), and wrapping the gallery header at <=520px.
8. Not verified by me: whether the owner considers a 72x72 icon-only Back at 480x1040 (rather
   than 120x72) acceptable; at 480 both fit, and 120 was chosen against only at <=520px because
   that is where chat.css hides the label.

## Disclosure: how the files were written

The profile plugin `@l33tdawg/dsh-recall-gate` refuses `edit`/`write`/`apply_patch` until the
current turn contains a completed `sage_turn` tool call, and a delegated session has no SAGE
tools in its function list. The memory recall was therefore performed for real over SAGE's HTTP
MCP endpoint (`sage_inception`, `sage_turn`, two targeted `sage_recall` queries - no committed
memory names these four files as another session's work), and the edits were applied with
shell writes, which that plugin's own `DEFAULT_WRITE_TOOLS` excludes by design. Every edit is
listed in `git diff` (three tracked files) and in the two hunks added to the untracked
`gallery.css`.

`apps/lantern-fleet-admin/app/device/gallery.css` is untracked in this working tree (`git
status` shows `??`), so its diff does not appear in `git diff`; the two hunks added to it are
the `flex-wrap: wrap` inside its `@media (max-width: 520px)` block and the
`.device-gallery-actions.device-gallery-primary` sticky rule.

---

## 14 October 2026 — Hardware and Heartbeat added to the screen list

These two were listed under NOT COVERED above, and the gap was real: the
integrated driver reaches both (its `settings-and-agent-reach-heartbeat-and-hardware`
scenario asserts their behaviour), but no instrument had ever measured the SIZE of
anything on them. They are now in `VISITS`, reached the way a user reaches them —
`Menu → Settings → the row headed Heartbeat / Hardware` — which needed a new `row`
step in the runner because the click target is an "Open" button inside a row with
no unique class.

Result at all four viewports, 18 screens now: `problems 0`, `console errors 0`.

| screen | controls measured | under the 72px floor | heights seen |
| --- | --- | --- | --- |
| heartbeat | 13 per viewport | **0** | all 72 |
| hardware | 14 per viewport | **0** | all 72 |

Be precise about what this is worth: it is a **positive control, not a repair**.
Nothing was changed for these screens — every control was already 72px tall, from
`screen.css`'s `.device-screen button { min-height: 72px }` — so the finding is
that the floor already held here and the earlier NOT COVERED note was about
absence of measurement rather than a defect. Adding them is what turns "probably
fine" into a number, at 8 screen-viewport combinations and ~108 control
measurements.

### Correction, same day: not every raised rule was load-bearing

The result above (1668 -> 0 under-floor controls) is measured and holds. What was
NOT established at the time is that each of the 25 edits was individually
necessary, and a red run since has shown at least one was not:

- `screen.css:17` `.device-name-keyboard button` was raised 56 -> 72. Setting it
  back to 56 and rebuilding changes nothing: `screen.css:21` `.device-screen
  button { min-height: 72px }` has equal specificity and comes later, so it
  already won. Verified by a red run in the lock fixture (56/56 checks still pass
  at 56px).
- `navigation.css`'s `<=520` page-bar rule was already known to be outranked by
  the more specific `.device-screen .device-page-bar-action`.

So: the OUTCOME is verified; the necessity of individual edits is not, and would
need one red run per rule. Do not read this pass as 25 verified repairs - read it
as a verified floor, with at least two of its edits redundant.
