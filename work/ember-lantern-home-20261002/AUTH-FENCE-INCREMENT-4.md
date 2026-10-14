# Auth context fence, increment 4: the photo policy is attributed to the reply that carries it

2 October 2026, 10:05 UTC. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. UI writer lane. Bounded correction closure for Rook's
revision-3 review, plus the driver false positive he reported with it.

Authority: Rook's `msg-60edd152` (the reproduced photo-policy pre-dispatch gap) and `msg-0664f54e`
(the broad driver's start counter), both extending `msg-5a31c119`. Increments 1 to 3 are recorded
in `AUTH-CONTEXT-FENCE.md`, `AUTH-FENCE-INCREMENT-2.md` and `AUTH-FENCE-INCREMENT-3.md`.

## The defect

`/history/list` is where the photo-saving policy comes from, and both capture lanes read it with a
raw, unfenced request:

```ts
const history = photo ? parseDeviceHistory(await voiceCommand('history/list', {})) : null;
if (!contextMatches(issuedUnder)) return refuseStart();
const retainOriginal = history?.photo_saving_supported === true && history.enabled && history.photos_enabled === true;
```

`contextMatches(issuedUnder)` asks whether the *currently published* reading still equals the
authority captured before dispatch. It says nothing about the reply in hand, and the reply is what
carries the policy. A broker that moved after this client's last poll — lock, owner change or
revocation — admits the read under the **new** epoch and answers with the new epoch's policy, while
the reading here still holds the old one. The current-reading check passes, the new policy decides
`retainOriginal`, and the original bytes go out on `/start` (or `/question/start`) *before* that
command's own reply is refused for carrying the same new context. The refusal was always correct and
always too late: disclosure had already happened.

Both call sites are fixed the same way, using the file's existing fence idiom
(`replySessionContext` + `sessionContextEqual` + `contextMatches`): the reply is judged — against
the authority captured before the first protected await **and** against the one the device publishes
now — before a single policy flag is read out of it. The unstamped and the malformed case are
refused by the same code: `parseSessionContext` returns `null` for absent and throws for malformed,
so a locked device's policy is never read from a reply that cannot be attributed.

### Why the shared helper was not reused

Rook offered `conversationRequest` "if it preserves the start's original expectation". It does not:
it captures its own expectation at its own dispatch, and it strips the admission stamp before
returning, so a caller can neither supply the start's `issuedUnder` nor judge the reply afterwards.
Routing through it would move the window rather than close it. The raw reply is explicitly fenced
instead.

## The driver false positive

`drive-screen-fixture.mjs` counted dispatches with `/POST \/start →/g` in eight checks, and the
fixture's own acknowledgement note read `POST /start → acknowledgement delivered` — a service note
shaped exactly like a transport line. Every one of those checks saw two. Both halves are fixed:

- the note is now `start acknowledgement delivered (not a dispatch)`, so no counter can mistake it
  for one (the auth driver's log-order assertion tracks the same string);
- the eight counters match `/POST \/start → \d{3}/g`, so they count dispatches and are immune to any
  future note, while still counting a refusal.

## New fixture states

| Knob | What it models |
| --- | --- |
| `rotation=photo-inflight` | The broker has already moved when the policy read arrives, so the reply is stamped under the new epoch. Reachable on lock, owner change or revocation. |
| `hold=1` | The capture owner's reading is frozen at the first one that lands. This is the reachable window between the rotation and the next poll; holding it makes the probe deterministic instead of a race against the 200ms tick. |
| `attach=1` | The owner holds real attached photo bytes, so the policy under test is the one that decides whether the original travels. |
| `historyStamp=missing\|malformed` | Declared unreachable, like the `confirm`/`keepalive`/`stop` rotations: the runtime stamps every successful protected reply or answers nothing. These prove the *client* refuses a policy it cannot attribute. |

The owner harness now reports `heldEpoch` (the context this client holds) beside `brokerEpoch` (what
the broker publishes), so "the client had not caught up" is asserted rather than assumed.

## Evidence

| Run | Source | Result |
| --- | --- | --- |
| `auth-fence-increment4-results.json` | this increment | **22/22 scenarios, 96/96 checks**, 0 console errors |
| `auth-fence-red4-results.json` | both fences reverted (saved, restored byte-identically) | **18/22 scenarios, 10 failed checks** — exactly the four new negatives, 17/19/22/23 |
| `screen-increment4-results.json` | this increment, broad integrated driver | **42/42 scenarios, 233/233 checks**, 0 console errors |
| `node drive-fixture.mjs increment4` | standalone Home fixture | 7/7 gestures, 8/8 geometry |
| `node --experimental-strip-types --test lib/*.test.mjs` | — | 342/342 pass |
| `npx tsc --noEmit -p tsconfig.json` | — | exit 0 |
| scoped `eslint` on the three changed product/test files | — | exit 0 |

The red run is the discriminator, and its trails are the defect stated as a log:

- scenario 17 — `POST /history/list → 200 | POST /start → 200 | POST /cancel → 200`: the start *was*
  dispatched on the unattributable policy and the original left with it, and the returned credentials
  were cancelled only afterwards.
- scenario 19 — `POST /history/list → 200 | POST /question/start → 200 | POST /cancel → 200`: the
  typed lane, identically.
- scenarios 22 and 23 — `POST /history/list → 200 | POST /start → 200 | POST /start/confirm → 200 |
  POST /keepalive`: worse than the rotation case. With no authority moving at all, an unattributable
  policy was not merely disclosed against — the capture was adopted, confirmed and kept alive, with
  no refusal shown to the user.

The 18 scenarios that pass in both runs include the three positive controls and the legacy path, so
the new checks are not satisfied by a client that has simply stopped asking.

Two existing source-shape assertions encoded the weaker rule and now encode the stronger one:
`home-integration.test.mjs` asserted `if (!contextMatches(issuedUnder)) return refuseStart();`
against the start path — a line this increment removes as insufficient — and
`device-photo-flow.test.mjs` asserted the `history?.` optional-chaining form. Both were rewritten to
the contract, not to the code: the reply's own stamp must be judged before the policy is parsed, in
both lanes, with the ordering asserted.

## What this does not establish

This is a client dispatch gate exercised against a synthetic service. It is not production broker
behaviour, not evidence of real human consent or native policy, and not device qualification.
`homeEnabled` stays false, source stays default-off, and no device, service, model, audio, radio or
deployment path was contacted. Every historical record was left untouched; the frozen
`frozen-20261002T0735Z-pre-correction/` snapshot and the earlier result files were neither read nor
written by this increment. The built preview bundle and its metafile are regenerated from the edited
fixture source, as they are for every increment; the earlier copies of those four build outputs are
the ones preserved in the frozen snapshot.

Hashes (sha256):

| File | sha256 |
| --- | --- |
| `apps/lantern-fleet-admin/app/device/talk.tsx` | `8fe4088f649d104862709d4af14c7ee3d44080fe8bf12aadba135d7d9f0af192` |
| `apps/lantern-fleet-admin/lib/home-integration.test.mjs` | `70e367c7b1e0d6a42509512f883419df84d167014ec320c2047e2cd8be016909` |
| `apps/lantern-fleet-admin/lib/device-photo-flow.test.mjs` | `7b6a7d59688193cd9fee43d15cd11262b9c6014fbfa2c2e269be41bc9328d81f` |
| `screen-fixture-entry.tsx` | `2b252bd11bdf170f2fed24a6a033fe7d8104402220e0947ceebf647bdd0a8bf2` |
| `drive-auth-fixture.mjs` | `a79549976bb3950f1f6365bf59f2ba45ebb708e283473b4069e99ef479582af7` |
| `drive-screen-fixture.mjs` | `212eacfd7bfd0b3413f290d5c5d0f4ff7d2f76aa0bfdfcca74e1061422cee2aa` |

## Still open

- The residual protected surfaces beyond Home/Chat/history stay queued as named work:
  `MessagesScreen.clear()` reacting only to token change, `ReadReceiptControl` checking token
  equality without subscribing to the seam, and the `communityRequest` returned-context contract
  audit. None is touched here, and the Home/Chat/history fence still does not cover them.
- O2 (menu-origin navigation) and O3 (blanked-publication copy) remain authorised and unstarted.
- Whole acceptance, successful Settings writes, real consent, native policy, physical focus, and
  every device/deployment gate Unit C owns.
