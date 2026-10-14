# 2b — Chat, Camera/Vision, separate Gallery, Settings: verified review package

2 October 2026. Ember, sole UI writer lane `c423d913-de77-4327-a98c-2e179f238f20`,
program `acc40de9`. Milestone 2b of `IMPLEMENTATION_BRIEF.md` (revision 4).

**Status: the four 2b compositions are implemented and verified on the current
tree. 43/43 scenarios, 241/241 checks, 0 console errors.** One real layout defect
was found at 1024x600 landscape, measured, and fixed — see below.

## What this pass did

1. **Rebuilt the integrated fixture from current source.** The committed bundle
   was from 17:07; the refusal follow-up landed at 18:51 and changed
   `lib/conversation-threads.ts` and `lib/device-session.ts`, both of which the
   fixture bundles through `screen.tsx`. Re-running the old bundle would have
   validated a tree that no longer exists. Both files were confirmed present in
   the rebuilt metafile (82 non-`node_modules` inputs).
2. **Ran the whole Screen driver as a regression**, not just gallery scenarios —
   the refusal work touched the shared session seam every protected surface reads.
3. **Added the missing 1024x600 landscape viewport** the brief requires and
   neither driver contained.
4. **Found, measured and fixed a real defect** that viewport exposed.

## The four compositions and what exercises them

| Composition | Files | Scenarios |
| --- | --- | --- |
| **Chat** | `chat-shell.tsx`, `navigation.css` | `home-chat-home-preserves-context` (7), `review-routes-to-chat` (6), `conversation-drawer-does-not-cover-home` (3), `loaded-thread-survives-navigation` (4), `viewing-another-surface-does-not-stop-a-recording` (6) |
| **Camera / Vision** | `camera.tsx` (bounded edit) | `camera-live-capture-retake-and-photo-question` (8), `camera-to-gallery-and-back-to-camera` (4) |
| **Gallery** (separate destination) | `gallery.tsx`, `gallery.css`, `gallery-navigation.ts` + tests | 10 scenarios: grid, viewer boundaries, photo staged into Chat **without** starting the microphone, combined deletion, empty vs saving-off vs unsupported, missing bytes, no microphone, keyboard/hidden boundary |
| **Settings** | `settings.tsx` (bounded edit) | its own driver, 6 scenarios: Home→Settings→Home, Back returns to the real origin (Home gear vs Chat menu), grouped/honest surface, failed acknowledgement is not success, unreachable bindings stated not invented, focus and a cancelled edit |

Chat reuses the existing message stack, composer, review and correction flows. The
shell owns the title bar and the conversation-list control and nothing else.

## The defect: the landscape reader was a line-and-a-half window

The brief requires the layout be checked at **1024x600 landscape**. Neither driver
contained the string `1024`: geometry covered 720x1560, 480x1040 and 360x800 only,
so landscape had never been exercised.

The tile assertions passed at once. The screenshot did not: the answer was cut off
mid-line next to the voice control. Measured on device geometry, with the harness
banner hidden (`probe-2b-landscape.mjs`):

```
BEFORE, 1024x600
panel    .device-home            t= 72  b=600
stage    .device-home-stage      t=170  b=232   (62px tall)
reader   .device-home-feedback   t= 32  b=370   (338px tall, scrollH 336 / clientH 336)
```

A 338px reader inside a 62px stage row. Root cause, from the computed styles:

- the landscape block sets `.device-home-stage { flex-direction: row; align-items: center; }`;
- `.device-home-feedback` is `flex: 0 1 auto` with `overflow: auto`;
- under `align-items: center` a flex child is sized to its **content**, not to the
  row, so the reader was never bounded and its own `overflow: auto` never engaged
  (`scrollHeight === clientHeight`, i.e. it did not believe it was overflowing);
- the stage is `minmax(0, 1fr)`, so it got only what the header, status strip and
  three tile rows left over.

**A correction to my own first report.** I initially described this as the reader
"painting outside the panel" and quoted `t=139` against a panel top of 179. That
was wrong on both counts. The base `.device-home-stage` already carries
`overflow-y: auto`, so the reader was *clipped by the stage*, not painted over the
tiles — `getBoundingClientRect` returns a layout box whether or not an ancestor
clips it, so a check built on those boxes can describe a defect it has
misidentified. The `t=139` figure was also measured with the harness banner in
flow, which is a fact about the harness and not the device. The defect itself is
real and reproducible either way; the description was not.

### The fix

`app/device/home.css`, a new block declared last so it outranks both earlier
blocks a 1024x600 panel matches simultaneously:

- `.device-home-stage { align-items: stretch; }` — the actual fix. Only a **bounded**
  box scrolls; sizing the reader to its content is what removed the bound.
- `.device-home-feedback { min-width: 0; }` — so the reader cannot refuse to shrink
  in the row.
- tiles concede size in this one context (`min-height: 88px`, tighter padding, gap
  and glyph) to return ~100px of height to the middle region. This follows the
  convention the short-panel block above already states: the tiles give up size,
  never their labels, never the status words, never the answer text.

```
AFTER, 1024x600
stage    .device-home-stage      t=164  b=296   (132px tall)
reader   .device-home-feedback   t=164  b=296   (132px tall, scrollH 336 / clientH 130)
tiles                            88px each, >= the brief's 72px floor
```

The reader is now exactly bounded by the stage, and because `scrollHeight (336) >
clientHeight (130)` its own scrolling finally engages — a long answer scrolls
inside a readable region instead of being cut mid-line. Portrait geometry is
unchanged (stage 732, reader 238, tiles 516 at 720x1560).

The naive fix is worth recording because it looks right and is not:
`max-height: 100%` alone would have engaged the scrollbar but capped the reader at
the 62px row, trading a clipped answer for an unreadably short one.

### The check was strengthened, not just satisfied

The first version compared stage children against the **panel** box, which is why
it could not see the real problem — a 338px box in a 62px row is still inside the
panel. It now asserts that every direct child of the stage stays inside the
**stage**, which is the bounded region the contract names. Direct children only: a
descendant inside a scroll container is supposed to exceed it.

## Gates on the current tree

```
node --experimental-strip-types --test lib/*.test.mjs        -> 367 tests, 367 pass, 0 fail, exit 0
npx tsc --noEmit --incremental false                         -> exit 0
npx eslint lib/home-integration.test.mjs                     -> exit 0

node build-screen-fixture.mjs                                -> built, React packages: react, react-dom
node drive-screen-fixture.mjs http://127.0.0.1:8791/screen-fixture.html 2b
                                                             -> 43/43 scenarios, 241/241 checks, 0 console errors
node drive-settings-fixture.mjs http://127.0.0.1:8791/screen-fixture.html 2b
                                                             -> 6/6 scenarios, 36/36 checks, 0 console errors
```

Tile rows, all four viewports, equal within 1px and no overlap:

| Viewport | Rows | Floor asserted |
| --- | --- | --- |
| 720x1560 portrait | 160 | 160 |
| 480x1040 | 140 | 132 |
| 360x800 narrow | 106 | 104 |
| 1024x600 landscape | 88 | 72 |

Counts re-derived from `screen-2b-results.json` and `settings-2b-results.json`.
Screenshots written as `screen-2b-*.png` (48) and `settings-2b-*.png` (6). The
earlier frozen records (`screen-auth-*`, `screen-correction-*`,
`settings-correction-*`) are untouched — the `2b` label redirected every output.

**One existing test was updated, deliberately.** `lib/home-integration.test.mjs`
enumerates every declared tile minimum height and asserts the exact set, so that a
new one cannot slip in unnoticed. It caught this change and failed with the fifth
value. The expectation was extended to `['104','104','132','160','88']` with a
comment naming the new context; the assertion's purpose — that every declared tile
minimum outranks `.device-screen button` — is untouched and still enforced for all
five blocks.

## Hashes (sha256)

Production source changed by this pass — **two files**:

```
0cd25398d457a0ba9fe1d4551fd763c4d275f77fa79bd57ea27296a9a493eb9d  app/device/home.css
58458e1f6c19a40a19c1cb07a33ae028f2ad2ace3b1b196a0ea516dcbb3cc4bb  lib/home-integration.test.mjs
```

2b new surfaces:

```
36d5b4dc5088a5b23cd390f3c5092ddde75ca28b55e5035a1e357375e7303335  app/device/gallery.tsx
12dbb09c2ccefa4cf5468378d02dd65b8cd59a7856912718f6426de7c20b4be8  app/device/gallery.css
a189d7d0ae7c1b824027d2cd44c56577121f017e94ab1a3966e79495b51fe5d0  app/device/chat-shell.tsx
87fa198873a01c4c053cf5dafe3201ee38f7ef66953701c0619884f204c2dadf  app/device/navigation.css
cc33201494cff518e92c2e86acfdd8563c115d3278e65f5a9540468f6820ff82  lib/gallery-navigation.ts
799b96632199725a40c22720e5c84cbe70b02e0a8e6cc83a4299b21f04110a6e  lib/gallery-navigation.test.mjs
```

2b bounded integration:

```
bdfeebb5c56db4eef7776f6dad80663a50125afb54020370da01eab077594981  app/device/camera.tsx
2ddcb3e104b011ec4ce9682c672379a12356d3015526c03a3029e8f087b99c0b  app/device/settings.tsx
f0b95d4b69bbcde0c53062a75dd9391688780a8b7a254584cfa214214e65cb45  app/device/screen.tsx
```

Evidence, bundle and probes:

```
0d789d97db1d87e67d098f8b6bfb32e154e28a21b74df7cfb384b0b4b68b49f2  drive-screen-fixture.mjs
1fdd81c52c53f2611ce0e17d181ede39b9f37368cd853072f03afe011d6e67bb  drive-settings-fixture.mjs
2c628179002efe0f95f6cd9f0e69bc14d16249d70b93c0cba4eb56d8a3c05cda  build-screen-fixture.mjs
b3e8c1fbb76dca70c6d13dc3f397595a06fcaf74349d81e1552f6de20ac4967f  probe-2b-landscape.mjs
976d705c090406f54656dbeeb62325145e96b210071ea683f1f5e31de4ba2747  screen-fixture.html
c901063b71add24bcd2f9f957d2fcf395f126d2d8e3d0359da087170af32e7ec  screen-fixture.css  (rebuilt; carries the fix)
9def582c25de2187679ed4115b40eac088949ef2a0df967f3e01f5bca8b7589c  screen-fixture.js   (unchanged: esbuild extracts CSS to the sibling file)
```

## Reproducing

```
cd work/ember-lantern-home-20261002
python3 -m http.server 8791 --bind 127.0.0.1 &
node build-screen-fixture.mjs
node drive-screen-fixture.mjs   http://127.0.0.1:8791/screen-fixture.html 2b
node drive-settings-fixture.mjs http://127.0.0.1:8791/screen-fixture.html 2b
node probe-2b-landscape.mjs 1024 600
```

Headless Chrome over the DevTools Protocol, no test framework and no browser
dependencies. Real layout, real trusted pointer input, real screenshots, the real
`screen.tsx` -> real `TalkControls` -> real `Home` component tree, against a
synthetic device service.

## What this is not, and the one thing Rook should weigh

Everything is synthetic transport and headless Chrome on this host. No device,
microphone, camera, speaker, model, radio, lock/epoch transition or physical panel
was exercised. `homeEnabled` stays false and nothing here is a release default.
Successful Settings writes against a real service, physical focus restoration and
cold first-frame establishment remain open and unclaimed, as do real owner
authentication/consent/policy and hardware acceptance.

**`home.css` is part of the frozen 2a acceptance set and Rook has verified its
hash.** This pass changes it. The change is confined to one new media block for
`(orientation: landscape) and (max-height: 700px)`, the canonical 720x1560 panel's
geometry is measured unchanged, and the rationale is above — but the frozen 2a
hash no longer describes the live file, and that should be re-reviewed rather than
assumed.

2c (Agent/Heartbeat/Hardware) is not in this package. `agent` remains the one Home
destination that reports itself unavailable, in its stable tile position.
