# Settings review — 2b remainder, against `SECONDARY_SCREEN_DECISIONS.md`

2 October 2026. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. Decision document:
`work/pocket-ui-start-review-20261002/SECONDARY_SCREEN_DECISIONS.md` (§28 the Settings
contract, §34 navigation, §36 acceptance evidence).

## What this review was for

2b delivered Gallery, the Camera→Gallery shortcut and the Chat shell, and left one
item: the Settings review. I have treated that item as verification and have **not
edited any product source**. `settings.tsx`, `power-settings.tsx` and `name.tsx` are
unmodified from `HEAD` `91b7a9c`; git reports no change to any of the three. So 2b
added no Settings construction, and this review asks whether the pre-existing Settings
screen already satisfies a contract written after it.

## Method

Source inspection of every bindings module, plus a new additive driver that exercises
the real `settings.tsx` inside the real `screen.tsx` in the real fixture:

```
node drive-settings-fixture.mjs          # 5 scenarios, settings-review-results.json
```

`drive-settings-fixture.mjs` is deliberately separate from `drive-screen-fixture.mjs`.
It writes its own results file and its own `screenshots/settings-*.png`, so the frozen
42-scenario record is untouched and Rook can accept or reject either set alone.

## Result

**3 of 5 scenarios pass. Two product findings, one evidence finding, one observation.**

| Scenario | Result |
| --- | --- |
| `home-to-settings-and-back-returns-home` | **FAIL 3/4** — Back lands in Chat |
| `settings-surface-is-grouped-and-honest` | PASS 6/6 |
| `failed-settings-acknowledgement-is-not-rendered-as-success` | PASS 5/5 |
| `unreachable-bindings-are-stated-not-invented` | PASS 4/4 |
| `settings-focus-and-a-cancelled-edit` | **FAIL 4/5** — confirm step loses focus |

Console errors: 0.

## F1 — Home → Settings → Back lands in Chat (contract violation)

§34 states the requirement as a rule, not a preference: *"Home → Settings → Home. A
Back action restores the originating view and focus, rather than always landing in
Chat."* It always lands in Chat.

Measured: `home=false chat=true` after clicking Back from Settings opened from Home's
gear. Reproduce with scenario 1; `screenshots/settings-02-after-back.png` is the panel.

The source agrees with the measurement. There are exactly two ways into Settings and
neither records an origin:

- `screen.tsx:728` — `onOpenSettings={() => leaveHome('settings')}`. `leaveHome`
  (`:613`) fences the activation and sets the page; it never touches `backTo`.
- `screen.tsx:792` — the device menu's Settings entry calls `setPage('settings')`
  directly.

`goBack` (`:390`) reads `backTo.current`; when it is null it falls through to
`setPage('main')` (`:403`). Every other route into a subscreen writes that ref —
Nearby/Messages explicitly null it so their Back returns to Chat (`:677`, `:791`),
Camera records an origin (`:691`), and the Messages/Nearby/Gallery tiles record
`{ page: 'home', focusTile }` (`:646`). Settings is the only destination that writes
nothing, so it is the only Back that cannot find its origin.

**Why I have not fixed it.** The fix is not the one line it looks like. `BackOrigin`
restores focus either through `focusTile`, whose `HomeDestination` union
(`lib/home-navigation.ts:23`) is `chat | vision | agent | gallery | messages | nearby`
and has no member for the gear, or through the `focusRef === 'camera'` special case
(`:398`). Restoring focus to the Settings gear therefore needs a third mechanism, or a
named destination — a small design choice, in a file inside the frozen hash set, and
after the set was handed back for independent acceptance. Rook's call.

**Recommendation.** Give the gear a recorded origin (`{ page: 'home' }` plus a focus
target for `.device-home-settings`) inside `leaveHome`'s two callers, and leave the
menu entry landing in Chat, which is correct for the menu.

## F2 — the confirm step drops focus to `document.body`

§36 requires focus-reachability evidence. Opening Settings moves focus correctly
(`document.activeElement` is the Back button, inside the screen). Opening the
confirmation step does not: focus is on `BODY`, outside the Settings section.

Cause is structural, not incidental: the dialog replaces the panel subtree
(`settings.tsx:51`) that held the switch the user just activated, so the focused element
is unmounted and focus falls to the document. The dialog is `role="alertdialog"`
(`:51`) but nothing moves focus into it or traps it.

Keyboard operation is not fully broken, which is why this is minor rather than blocking:
with focus moved inside the dialog, Escape dismisses it
(`settings-focus-and-a-cancelled-edit`, 4th check). I moved focus there programmatically;
I did not walk the tab order to prove a keyboard user reaches it unaided. The measured
gap is that the dialog neither takes focus nor announces itself on open.

**Recommendation.** Focus the confirm dialog's Cancel (or the primary) on open and
return focus to the switch on cancel. `settings.tsx` is inside the authorised bounded
edit list, so this is a one-file change if Rook wants it now.

## R1 — the frozen record on disk does not describe the current revision

Not a product defect, but it affects what Rook is being asked to accept, so it is worth
fixing before acceptance. `README.md` was last written at 13:37 and its hash list and
gate numbers predate the current bytes.

**6 of 16 recorded hashes do not match the working tree:**

| File | README records | Working tree |
| --- | --- | --- |
| `lib/home-integration.test.mjs` | `f3d3acad` | `70cf1ab8` |
| `app/device/screen.tsx` | `abea9f13` | `04aaacf3` |
| `app/device/talk.tsx` | `fc7db4df` | `7cc81ee1` |
| `app/device/device-header.tsx` | `6c3295fd` | `7415ee9e` |
| `app/device/gallery.tsx` | `d50d6afc` | `36d5b4dc` |
| `app/device/gallery.css` | `38192fab` | `12dbb09c` |

The other ten match. The stale values are the ones carried in the 05:36 handoff note;
the files were last written between 05:38 and 06:57, so the list was never recomputed
after those edits.

Two gate numbers in the README are also stale, while the handback message quotes the
current ones:

| Gate | README (13:37) | Measured now |
| --- | --- | --- |
| app tests | 314 passed | **320 passed, 0 failed** |
| integrated driver | 38/38 | **42/42, 0 console errors** (read from `screen-gesture-results.json`) |

I ran the tests, `tsc` and ESLint myself; I **read** the 42/42 driver result rather than
re-running it, because re-running would overwrite the frozen evidence Rook is reviewing.

**Recommendation.** Recompute the README's hash list and gate numbers from the current
tree before Rook accepts any group, or point him at this section. Note that
`device-header.tsx` now measures `7415ee9e`, which is Rook's own reference hash for that
file, consistent with his authorship of it.

## O1 — a disabled switch reads "On"

Observation only, no action requested. In the fixture's default state question saving is
off, so "Keep attached photos" renders `aria-checked=true`, `disabled`, reading "On",
next to the note *"Photo saving is paused while question saving is off."* The note does
explain it and I am not calling it a contract breach — but a disabled control asserting
"On" is the kind of pair a reviewer notices, and Rook may prefer "Paused" there.

## Contract lines that hold, with the evidence

Measured in the fixture:

- Full-screen Back/title and grouped controls (§28) — header at `settings.tsx:45-46`;
  groups `Automatic lock`, `Screen and sleep`, `Save questions`, `Bluetooth audio`.
- History/photo-saving policy (§28) — `history/list` read at `:21`, switch state rendered
  from the parsed reply, `aria-checked=false` in the served state.
- Photo policy offered only when the device attests it — `Keep attached photos` appears
  because the fixture serves `photo_saving_supported: true`; it is disabled and explained
  while question saving is off.
- Unsupported Bluetooth audio is stated, not implied (`settings.tsx:65`) —
  *"Pairing and audio output controls are not available in Lantern yet."*
- No entries to undelivered 2c destinations — §28 says entries appear *"as those screens
  are delivered"*; Agent/Heartbeat/Hardware are not delivered and the check finds none.
- No inferred success before an authoritative reply (§28) — the write is attempted
  (`POST /history/settings → 404`), the refusal is stated
  (*"Could not confirm the setting. Go back and reopen Settings before retrying."*), no
  switch claims On and no success wording is shown.
- Unavailable bindings are stated, not invented — the lock and sleep panels show why they
  cannot load and expose no `select` they could not honour.
- A cancelled edit writes nothing — Cancel returns to the panel, the switch is unchanged,
  and no `history/settings` request appears in the transport log.

Source-only, not measured in this fixture:

- Name/profile reuse (`settings.tsx:48`, `DeviceNameScreen` at `:44`) and `Lock now`
  (`settings.tsx:48`). They arrive as `profile` / `onProfileChange` / `onLock`, declared
  as `DeviceScreen` props at `screen.tsx:76` and forwarded to `SettingsScreen` at `:681`;
  the preview mounts `<DeviceScreen homeEnabled />` without them, so neither renders here.
- The "Discoverability belongs in Nearby" note (`settings.tsx:48`) sits inside that same
  profile-gated panel.
- Authenticated display/sleep and lock preferences exist
  (`lib/power-settings.ts`, `lib/lock-settings.ts`) and are bound with the device token;
  in this fixture every 3196 request is refused by design, so only their failure branch is
  exercised.
- No duplicate settings state: each panel owns its own, and `screen.tsx` holds none.

## What this review does not claim

No Settings write ever succeeds in this fixture, so a **successful** change is not
measured anywhere in this file. The power and lock panels were exercised only in their
unavailable branch. Physical panel, device session, lock/epoch transitions, deployment
and backend behaviour are all unexercised. The `Home 2a` gate set is unchanged by this
work: `homeEnabled` stays false and nothing here accepts the frozen revision.

## Commands and current hashes

```
cd apps/lantern-fleet-admin
node --experimental-strip-types --test lib/*.test.mjs     # 320 passed, 0 failed
npx tsc --noEmit                                          # exit 0
npx eslint <changed ts/tsx/mjs files>                     # exit 0, no warnings

cd work/ember-lantern-home-20261002
node drive-settings-fixture.mjs                           # 3/5, findings F1 and F2
```

sha256 (first 16), measured on the current tree:

- `app/device/settings.tsx 629ca3982490627f` (unmodified from HEAD)
- `app/device/power-settings.tsx 73c9b2296ca5e1ab` (unmodified from HEAD)
- `app/device/name.tsx 22cc7a8bdad0ab6b` (unmodified from HEAD)
- `app/device/screen.tsx 04aaacf3daaefe49` (differs from the README — see R1)
- `lib/home-integration.test.mjs 70cf1ab8` (differs from the README — see R1)
