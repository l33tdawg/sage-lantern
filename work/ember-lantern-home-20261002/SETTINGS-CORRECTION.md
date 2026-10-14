# Settings correction — F1, F2, R1 closed

2 October 2026, 07:51 UTC. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. UI writer lane.

This is the **correction** to `SETTINGS-REVIEW.md`, which stays on disk byte-identical as
the historical review. The authority for the work is Rook's reply to `msg-2913bdc8`,
recorded in `work/auth-integration-20261002/unit-c/INBOX_REVIEW.json` (`decisions`
field) and confirmed on the sender side at `07:29:53Z`.

## What Rook decided

| Item | Decision (quoted) |
| --- | --- |
| F1 | *"Record actual origin for every Settings entry and clear stale backTo. Home gear returns Home and gear focus; Chat menu returns Chat and menu focus. If menu can originate Home preserve that origin. Use a typed gear focus target separate from HomeDestination tile/action routing; focus after Home mounts through its own ref."* |
| F2 | *"Focus Cancel on confirmation entry; cancel/Escape restores initiating switch. Prove real keyboard entry/reachability/return without fixture focus injection. Cancel writes nothing; failed acknowledgement cannot render success."* |
| R1 | *"Preserve historical frozen bytes/results. Add dated current correction with full hashes, commands, counts and provenance, point README to it. Run affected scenarios/gates into new artifacts without overwriting the 42-scenario record. Return changed-file closure for independent review."* |
| O1 | Observation only; left for a separate copy decision. Not touched. |

## F1 — Settings now records the screen it was opened from

`goBack` used to infer its destination from the page it was leaving, so Settings — the one
destination reachable from every screen, because the menu lives in the shared header — was
the only Back in the app with no origin, and always fell through to Chat.

| Entry | Origin recorded | Back lands | Focus |
| --- | --- | --- | --- |
| Home gear | `{ page: 'home', homeFocus: { kind: 'settings' } }` | Home | the gear |
| Menu over Chat | `{ page: 'main' }` | Chat | the menu control |
| Menu over Home | `{ page: 'home', homeFocus: { kind: 'settings' } }` | Home | the gear |
| Menu over any other screen | `{ page: <that screen> }` | that screen | its own header control |
| Menu over Settings | *no origin written* — the entry is a no-op and the origin already held is still the true one | — | — |

The origin is **assigned**, not merged, so a stale value cannot survive into Settings.

The gear is a focus target but not a Home destination: `HomeFocusTarget` (`lib/home-navigation.ts:34`)
is `{ kind: 'tile'; destination: HomeDestination } | { kind: 'settings' }`, so `routeFor`,
`tileCommand` and `unavailableReason` keep taking `HomeDestination` unqualified, and Home
focuses the gear through its own ref (`home.tsx:121`, `:155-159`, `:313`).

Source: `screen.tsx:67` (origin type), `:186` (state), `:402` (focus resolution),
`:415-423` (Back restores focus from the origin), `:659-665` (`openSettings`),
`:692`, `:737` (tile/camera origins retyped), `:774` (gear), `:838` (menu).

## F2 — the confirmation takes focus, and gives it back

The dialog replaces the panel that held the switch, so the focused element was unmounted and
focus fell to `document.body`. Now the dialog takes focus on entry, and returning focus is
driven by *which* switch opened it rather than by a stale element reference:

- on open, Cancel is focused (`settings.tsx:39`);
- on close — Cancel, Escape, or the write's own outcome — focus returns to the switch that
  opened it, or to Back when a failed acknowledgement removed the panel for good
  (`settings.tsx:40-44`);
- the switches are addressed by stable refs (`:85`, `:87`) so a remounted switch is the one
  focus lands on.

## Evidence

Settings driver, corrected run (`drive-settings-fixture.mjs`, label `correction`):

```
node drive-settings-fixture.mjs http://127.0.0.1:8791/screen-fixture.html correction
```

**6/6 scenarios, 36/36 checks, 0 console errors.** The pre-correction record was 3/5 with two
failures; the two that failed now pass, and both are asserted at the level Rook asked for:

| Scenario | Checks | What is now measured |
| --- | --- | --- |
| `home-to-settings-and-back-returns-home` | 5/5 | Back returns Home **and focus is the gear it was opened from** |
| `settings-back-returns-to-the-screen-it-was-opened-from` | 6/6 | New. Menu→Settings over Chat returns to Chat with menu focus; the same entry over Home returns Home with gear focus |
| `settings-focus-and-a-cancelled-edit` | 9/9 | New keyboard path — see below |
| `settings-surface-is-grouped-and-honest` | 6/6 | unchanged |
| `failed-settings-acknowledgement-is-not-rendered-as-success` | 6/6 | Cancel still writes nothing, a refused write still renders no success, and the failure leaves focus on a real control rather than the document |
| `unreachable-bindings-are-stated-not-invented` | 4/4 | unchanged |

The focus scenario contains **no `element.focus()` call**. It walks the real tab order with
real key events until `button[role="switch"][aria-label="Save questions"]` holds focus,
activates it with Enter, asserts the dialog took focus itself, dismisses with Escape and
asserts focus is back on the switch, reopens with Enter, and closes with Space on Cancel.
The reviewed version injected focus with `el.focus()` before pressing Escape, which proved
only that the dialog *can* hold focus once somebody puts it there.

Integrated driver (regression, because `goBack`/`BackOrigin` are shared wiring) — label
`correction`, new artifacts only:

```
node drive-screen-fixture.mjs http://127.0.0.1:8791/screen-fixture.html correction
```

**42/42 scenarios, 233/233 checks, 0 console errors**, including the four Home-route Back scenarios
(`home-to-messages-and-back-to-home`, `home-to-nearby-and-back-to-home`,
`home-to-gallery-and-back`, `camera-to-gallery-and-back-to-camera`).

Gates:

```
cd apps/lantern-fleet-admin
node --experimental-strip-types --test lib/*.test.mjs   # 323 passed, 0 failed
npx tsc --noEmit                                        # exit 0
npx eslint <changed files>                              # exit 0, 0 errors
```

## R1 — the frozen record is preserved, and the correction is dated

The pre-correction evidence was copied, with a SHA-256 manifest, to
`frozen-20261002T0735Z-pre-correction/` (84 files: the three results records, both bundles
and their metafiles, and all 75 screenshots as they stood). Nothing was deleted or moved.
`settings-review-results.json` and its `settings-*.png` are still on disk byte-identical, and
the 42-scenario record `screen-gesture-results.json` with its screenshots was never written
to: every corrected run went to new names instead.

| Record | File | Content |
| --- | --- | --- |
| Settings, corrected | `settings-correction-results.json` + `screenshots/settings-correction-*.png` | 6/6, 36 checks |
| Integrated, corrected | `screen-correction-results.json` + `screenshots/screen-correction-*.png` | 42/42, 0 console errors |
| Standalone Home, corrected | `gesture-correction-results.json` + `screenshots/*-correction-*.png` | 7/7 gestures, 8/8 geometry |

The three drivers gained an optional trailing `label` argument for exactly this: with no
label they write the original names, so re-running them the original way is unchanged.

### An evidence defect found while proving the record

Three scenarios named a screenshot in `record(...)` but never captured one, so the frozen
42-scenario record referenced files that do not exist — verified against the frozen snapshot:
`screen-04-hold.png`, `screen-08-withdrawn-start.png` and
`screen-25-gallery-boundaries.png` are absent from it, and were absent from the working tree
before this correction. The corrected run completes those three captures at the state each
scenario asserts, and `screen-correction-results.json` now references 42 existing files and
no missing ones. The frozen record itself was not edited.

## What this does not claim

- `homeEnabled` stays `false`; nothing here is accepted, released or device-qualified.
- No Settings **write** ever succeeds in this fixture, so a successful change is still
  unmeasured; the power/lock panels were exercised only in their unavailable branch.
- The name/profile panel and "Lock now" still need props the preview does not pass.
- Focus behaviour is measured in headless Chrome on this host, not on the physical panel.
- No device, microphone, model, radio, lock/epoch transition or deployment was touched.

## Observation — not fixed, awaiting direction (O2)

Rook's "clear stale backTo" is satisfied for **Settings**, which is what was asked. The other
five menu entries still neither record nor clear an origin, so a previous Home origin can
survive into them: Home → Messages tile (records `{ page: 'home', focusTile: 'messages' }`) →
menu → Past questions (`screen.tsx:836`, no origin write) → Back returns **Home**, not
Messages. Source-derived, not measured in the browser, and left alone deliberately: the fix
is the same one-line-per-entry change but it alters five screens' Back behaviour, which is
Rook's call rather than mine.
