# O2 and O3: a menu destination records its real origin, and an unreadable voice publication is not a claim

2 October 2026, 10:20 UTC. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. UI writer lane. The two items Rook left queued alongside the
auth audit: O2 (`msg-1b2b8f27`, authorised as a separate small navigation correction in
`msg-413eec41`) and O3 (the copy decision in `msg-413eec41`).

Authority: the same direct owner confirmation that released increment 5, which covered this queue
explicitly. No relayed quotation is treated as authority.

## O2 — the exact scope, inspected rather than repeated

Rook asked me to inspect the actual menu entries and name the scope, noting my earlier report said
five. The menu (`app/device/screen.tsx`, the `role="dialog"` block) has exactly **five navigation
destinations**: Past questions, Nearby, Settings, Shared knowledge, About — plus Lock, which is not a
destination. Before this correction their origin handling was:

| entry | before | origin recorded |
| --- | --- | --- |
| Past questions | `setMenuOpen(false); setPage('history')` | none — stale `backTo` survived |
| Nearby | `setMenuOpen(false); backTo.current = null; setPage('status')` | cleared outright |
| Settings | `openSettings(page)` | yes |
| Shared knowledge | `setMenuOpen(false); setShareSource(undefined); setPage('sharing')` | none |
| About | `setMenuOpen(false); setPage('about')` | none |

So four of five were wrong, and the consequence is the case Rook named: from Messages, Past questions
left `backTo` unset, `goBack` fell through to `setPage('main')`, and Back dropped the user in Chat
having lost the screen the menu was opened over.

### The fix

One helper replaces the Settings-only path, and every entry goes through it:

```ts
const openMenuRoute = (destination: Page, onOpen?: () => void) => {
  setMenuOpen(false);
  if (page === destination) return;
  onOpen?.();
  const homeFocus = HOME_TARGET_FOR_MENU[destination];
  backTo.current = page === 'home' && homeFocus ? { page: 'home', homeFocus } : { page };
  leaveHome(destination);
};
```

- **The origin is the page the menu was opened over**, assigned rather than merged, so a stale origin
  cannot survive. The menu is in the shared header, so the origin can be any of Home, Chat, Messages,
  Nearby, Settings, Shared knowledge, About or Past questions.
- **A same-destination entry is a no-op** (`if (page === destination) return`). Recording the
  destination as its own origin would make Back do nothing, and the origin that screen already holds
  is still the true one — the rule Settings already had.
- **`onOpen` runs only on a real navigation**, which is what keeps Shared knowledge's
  `setShareSource(undefined)` from resetting the screen on a repeated entry.
- **`HOME_TARGET_FOR_MENU` names a Home control only where Home has one**: the Settings gear, and the
  messages and nearby tiles. Past questions, Shared knowledge and About are menu-only destinations
  with no Home control, so no target is invented for them; Back returns Home and focus goes to the
  menu button that actually opened it, via a new `origin.page === 'home'` branch in `goBack`.
- **Home's typed gear/tile targets are preserved, not churned.** `HomeFocusTarget` is unchanged and
  `lib/home-navigation.ts` and `app/device/home.tsx` were not edited at all, so the accepted Home 2a
  hash set is untouched by this increment.

### One deliberate widening, named for review

Every entry now leaves through `leaveHome`, where only Settings did. `leaveHome` is what fences a
pending Home activation and withdraws a start that has not been accepted, so opening *any* menu
destination from Home now behaves the way Settings already did. I judged that the correct reading of
"leaving Home", and it is the only way the five entries share one helper — but it is a behaviour
change beyond origin recording and Rook should accept or reject it on its own merits.

## O3 — the copy

`lib/device-state.ts` `deviceView` switches on `status.voice.phase`, which the parser accepts as **any
string**. Everything unrecognised — blank, `starting_voice`, `quiescing`, a phase a newer runtime
invented, and what a client holds after a rotation it can no longer read — reached:

```ts
default: return { phase: 'unavailable', title: 'Starting voice…', hint: 'Waiting for the voice service.' };
```

"Starting voice…" is an assertion the reading does not support: the publication said nothing this
client can interpret, and after a rotation it is not evidence of a start or of a takeover. It is now:

```ts
default: return { phase: 'unavailable', title: 'Voice status unavailable', hint: 'Waiting for the current device status.' };
```

Neutral, and consistent with the file's existing wording for an unknown reading (`'Reconnecting…'` /
`'Waiting for device status.'`). The `starting: 'Starting…'` entry in the *processing* stage table is
untouched: that is a live publication the device actually reported, not an unreadable one.

## Verification

Run from `apps/lantern-fleet-admin`:

- `npm test` — **363/363 pass, 0 fail** (356 after increment 5, +6 navigation, +1 copy).
- `npx tsc --noEmit --incremental false` — exit 0.
- `npx eslint` on all five changed files — clean, exit 0.

`lib/menu-origin-navigation.test.mjs` (new, 6 tests) mounts the **real** `screen.tsx` and drives its
real handlers, replacing only the child screens and replacing them with prop recorders, so the
parent's own decisions are what is asserted:

1. Home→Messages→menu→Past questions→Back **returns Messages** — the case Rook named.
2. A destination opened over Chat returns to Chat (the matching positive control: this one already
   passed and must keep passing).
3. A Home origin restores the typed tile target the entry belongs to (`{kind:'tile',
   destination:'nearby'}`), proving the gear/tile targets still work.
4. A menu-only destination from Home names **no** Home control, so none is invented.
5. Re-entering the destination already on screen leaves the true origin intact — from Messages,
   menu→Settings, menu→Settings again, Back returns **Messages**, not Settings.
6. Home's gear returns to Home and its gear target.

**Red run.** The demonstration was verified to discriminate rather than merely pass: reverting the
Past questions entry to its pre-fix `setPage('history')` makes test 1 fail with `showing()` = `home`
instead of `messages`, and restoring the fix makes it pass. Tests 2–6 stay green in both runs, which
is the point of the positive controls.

`lib/device-state.test.mjs` gains one test: four unreadable phases must give the neutral title and
hint, and the copy must not match `/starting|takeover|took over/i`.

`lib/home-integration.test.mjs` was corrected in place. Its old assertions pinned the pre-fix
structure — one of them asserted `setMenuOpen(false); backTo.current = null; setPage('status')`, i.e.
it pinned the defect itself as intended behaviour. They now pin the shared helper, the no-op rule,
the Home target map and all five entries.

## Changed files and hashes

| sha256 | file |
| --- | --- |
| `f0b95d4b69bbcde0c53062a75dd9391688780a8b7a254584cfa214214e65cb45` | `apps/lantern-fleet-admin/app/device/screen.tsx` |
| `57c279ee3a70ae4a005ff201367fdef946a553bc7fb3693fea7eb48441053765` | `apps/lantern-fleet-admin/lib/device-state.ts` |
| `682afeee06157f6d9f7784de6a6efe7a438541de5692a9efa971ff6206683026` | `apps/lantern-fleet-admin/lib/device-state.test.mjs` |
| `5aec15bd395139e9ea5cb0641f86120e81078ae80ec0c1a64b3ea27356e2e9e4` | `apps/lantern-fleet-admin/lib/home-integration.test.mjs` |
| `af9726dd15b12977553400e67daa08767df9383ea098be15b8e25fa22b638f2c` | `apps/lantern-fleet-admin/lib/menu-origin-navigation.test.mjs` (new) |

`lib/home-navigation.ts`, `app/device/home.tsx` and the Home 2a hash set were not modified.
Historical records under `work/` were not modified.

## Named residuals — open, not smoothed over

1. **The header's own Nearby and Messages shortcuts still clear `backTo`** and deliberately keep
   `main` as their origin, which is documented in the source and is not a menu destination. So
   Messages → header Nearby → Back still lands in Chat. If that is the same complaint, it is a
   separate decision about the header entries, not O2, and I have not changed it.
2. **Focus restoration is asserted at the target, not in a live DOM.** The new tests prove which page
   renders and which Home focus target is requested; `focusPageControl` and the new menu-button focus
   call are covered by source assertions and were not exercised against a real document. The existing
   Settings driver record covers that ground for the Settings path only.
3. **The browser driver was not extended.** Increments 1–4 added scenarios to
   `drive-auth-fixture.mjs`, whose bundle is frozen review evidence; I deliberately did not rebuild it
   for a navigation-only change. If Rook wants O2 in the driver, that is a fixture rebuild and a new
   labelled run.
4. **The `leaveHome` widening above** is the one judgement in this increment that Rook has not
   previously accepted.

## Boundaries preserved

Source-only, default-off, `homeEnabled` false. No new writer. No device, service, session, radio,
model, audio, installation or deployment action. No private-action enablement. No live send, receipt
or consent challenge. Whole auth, consent, hardware and acceptance gates remain open.
