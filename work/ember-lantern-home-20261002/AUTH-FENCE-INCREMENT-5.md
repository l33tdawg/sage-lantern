# Auth context fence, increment 5: the protected community surfaces are fenced on the reply's own stamp

2 October 2026, 09:50 UTC. Ember, task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. UI writer lane. Bounded source-only audit of the
Messages / read-receipt / community residual named in Rook's `msg-1b2b8f27` and released by
`msg-5e14aea7` and `msg-5cbe3248`; increments 1 to 4 are recorded in `AUTH-CONTEXT-FENCE.md`,
`AUTH-FENCE-INCREMENT-2.md`, `AUTH-FENCE-INCREMENT-3.md` and `AUTH-FENCE-INCREMENT-4.md`.

Authority: `msg-5cbe3248` (the handed source-only go-ahead) and the owner's direct confirmation in
the current `sage-commons` session. The relayed quotation is not treated as the authority — the
owner was asked in-session and confirmed this increment before any file was written.

## 1. The backend stamp contract, inspected rather than assumed

The audit's precondition was to read the actual stamping contract. Read from
`scripts/lantern/unit-a-voice-loop.py`:

- `capture_session_context(token)` (line 169) returns `None` **only** when `lock_required()` is
  false — the legacy/unlocked path. On a locked device a missing or unusable broker context raises
  `DeviceLocked()` instead.
- `do_POST` (line 1643) sets `self.request_session_context = capture_session_context(device_token)`
  at line 1655, **before** path routing, so it is set for `COMMUNITY_PATHS` as well.
- `respond()` (lines 1570-1581) is the single POST exit (called at line 1826). With
  `context is not None and status < 400` it merges `session_context` into the payload; if
  `context_current(header("X-Lantern-Session"), context)` is false it converts the reply to
  `403 {"error":"device_locked"}` and revokes the device. It re-checks after the body is written and
  revokes if the context moved mid-write.
- `community_access()` (line 614) already rotates its own epoch on token **or** context change
  (`changed = self.community_token != token or self.community_context != context`).

So the stamp is real on every protected community 2xx reply and needs no invented field. Two
consequences the client had to be built around: a stamped reply is guaranteed current **at emit
time**, and a rotation that lands before the reply is emitted turns a would-be 200 into a 403 rather
than a stale 200. A reply carrying no stamp means either the legacy path or an error — never
"unlocked, therefore fine".

## 2. The defect

All three named gaps were reproduced in source before any edit. `messages.tsx` and
`read-receipt.tsx` were both last modified in September and are not touched by any concurrent
writer, so there was no collision to avoid.

1. **`communityRequest`** (`lib/community.ts:119`) captured the device token before dispatch and
   compared it after (`token !== getDeviceToken()`). An access-epoch rotation keeps the token
   identical, so the only check that could have caught it could not see it, and the reply's own
   `session_context` was never read.
2. **`MessagesScreen`** (`app/device/messages.tsx:64-77`) polled `getDeviceToken()` every 250ms and
   cleared cached chats, the draft in progress and receipt outcomes only when the *token* changed.
   A same-token rotation left every cached chat and the draft rendered under a dead authority.
3. **`ReadReceiptControl`** (`app/device/read-receipt.tsx`) compared `token.current` only, so an
   outstanding consent challenge and its outcome survived an unchanged-token rotation, and the
   post-await adoption check was token equality.

## 3. A second finding: the stamp broke every exact-key community parser

This is cross-lane and worth Rook's attention independently of the fence.

The community parsers assert an **exact** key set — `parseCommunityStatus` requires exactly
`schema/messaging/sharing/dictation`, `parseRenderChallenge` exactly `id/challenge_id/expires_at`,
`parseReadReceipt` exactly `id/receipt_id/state`, and so on. Since `respond()` now merges a fifth key
into every protected 2xx reply, on a **locked** device every one of those parsers rejects a valid
reply:

- `messages.tsx` would report "Community status unavailable" / chats unavailable;
- `RenderedReceiptFlow.beginRendered` throws, so the control reports "Read receipts are unavailable.
  No receipt was requested." — which is at least the safe direction, but it is not the truth.

The legacy/unlocked path is unaffected, which is why the existing suites did not catch it: they
mocked replies without a stamp. Consuming the stamp in `communityRequest` fixes the whole class at
one point, because the stamp is transport metadata and not part of the community record — the same
thing `conversation-threads.ts` already does with `delete state.session_context`. Six pre-existing
lib tests (radio, read-receipts, share-refresh ×3, share-render) were fabricating a locked device
with unstamped replies; they now model the real contract, per the convention already documented in
`conversation-request.test.mjs`.

## 4. The fix

`lib/community.ts` — `communityRequest` captures `establishedSessionContext()` before dispatch,
reads the reply's own stamp through `replySessionContext`, and refuses anything that is not `ok`
from `fenceProtectedReply`, then deletes the stamp so the exact-key parsers see the record they
require. This is the shared transport for every community path, so one fence covers the status read,
the message lists, the contact lists, `messages/open`, `messages/send`, the consent challenge, the
acknowledgement and the share/custody/radio routes.

`app/device/messages.tsx` — the clearing body is extracted to `clearProtected` and subscribed to
`subscribeDeviceSession`, the one seam. The 250ms token poll and the `visibilitychange` handler keep
their existing behaviour.

`app/device/read-receipt.tsx` — subscribes to the same seam. On a rotation the flow is closed, the
challenge and the checkbox are dropped, and the surface becomes terminal: it says the device session
changed and asks the user to reopen the message. No replacement challenge is requested. A `generation`
counter taken before the dispatch means an acknowledgement still in flight can no longer be adopted as
a positive state — closing the flow makes `RenderedReceiptFlow.live()` refuse, so the worst case is the
`unknown` the pre-await mark already recorded. Nothing is replayed.

## 5. Verification

Run from `apps/lantern-fleet-admin`:

- `npm test` — **356/356 pass, 0 fail** (was 342/342 before this increment; +14 new, and the 6
  unstamped-device tests corrected in place).
- `npx tsc --noEmit --incremental false` — exit 0.
- `npx eslint` on the nine changed files other than `share-render.test.mjs` — clean, exit 0.
  `share-render.test.mjs` keeps a `@next/next/no-assign-module-variable` error at line 46 that is
  byte-identical at `HEAD` and predates this increment; it is in the harness this test borrows from,
  and it was left alone rather than folded into this change. Repo-wide `npm run lint` reports 8
  errors in 4 files (`nearby-directory.tsx`, `question-correction.tsx`, `share.tsx`,
  `share-render.test.mjs`) — all pre-existing, none introduced here, and none in a file this
  increment authored source in.

New deterministic coverage:

- `lib/community-fence.test.mjs` — behavioural, over the real `communityRequest`: a stamped reply is
  adopted and the stamp does not reach the parser; a reply admitted under another epoch is refused; a
  missing stamp on a context-publishing device is refused (including on `messages/send`); a malformed
  stamp is refused rather than read as absent; an epoch that moves mid-flight refuses the reply; a
  legacy device still gets its unstamped replies; nothing is adopted before a reading establishes an
  authority; a lock that lands mid-flight still refuses; and the two surfaces subscribe to the one
  seam.
- `lib/protected-surface-rotation.test.mjs` — the **real components**, compiled from source and
  mounted against the real `device-session` seam and the real `RenderedReceiptFlow`, with only the
  network replaced: a rotation drops the cached chats and does **not** refetch; a rotated consent
  challenge is dropped and never reissued; an acknowledgement in flight when the rotation lands is
  reported only as `unknown`, never as queued, and the send control is gone; and a legacy device that
  publishes no context still gets a working challenge (the positive/legacy control).

## 6. Changed files and hashes

| sha256 | file |
| --- | --- |
| `d4a1805b09ffbcf6712988ef05d6126f29886b52c7655fa25f0041e5b7ca8e23` | `apps/lantern-fleet-admin/lib/community.ts` |
| `4551019889bc8d67418f1b878ba1dc4748ea29d0230de433357e003b45cec978` | `apps/lantern-fleet-admin/app/device/messages.tsx` |
| `a145afbf6df5856a10c01258429d63bbc83cabd05fef56af443b21e48729090c` | `apps/lantern-fleet-admin/app/device/read-receipt.tsx` |
| `b94468a1452dd8f3adb174b6cb5c6ccb8e0110f990206b92cb8879a7012558fb` | `apps/lantern-fleet-admin/lib/community-fence.test.mjs` (new) |
| `b77cd4ba7204ba84cc2b26e78ea2abae5419cdecdc0c166b524996427aa378cf` | `apps/lantern-fleet-admin/lib/protected-surface-rotation.test.mjs` (new) |
| `94d0f489b410d4ce9793bbfc71f8cbe80bf3f8f5abc9e9bc69e77e151e8d65a7` | `apps/lantern-fleet-admin/lib/community.test.mjs` |
| `a41c301ea07d20f6d717e5412cff9722271a3c55086cdf830d075ba38150e717` | `apps/lantern-fleet-admin/lib/radio-request.test.mjs` |
| `769d03124153b172b927c949b93215a880d981ad1bd37d88be9dedca8d43e428` | `apps/lantern-fleet-admin/lib/read-receipts.test.mjs` |
| `76b624be696436220a79ab477152370904b289ddd5a0a339f2dbb2214e7ea225` | `apps/lantern-fleet-admin/lib/share-refresh.test.mjs` |
| `f0fc132743defe16f822403cda21c24da61c71aad87a504a9eb6c56efc7e9a72` | `apps/lantern-fleet-admin/lib/share-render.test.mjs` |

Historical records under `work/` were not modified.

## 7. Named residuals — open, not smoothed over

1. **A community request now requires an established authority.** `establishedSessionContext()`
   returns `unknown` until a validated status reading has run, and `fenceProtectedReply` refuses in
   that state. This is the same dependency every already-accepted protected surface (typed review,
   correction, follow-up, dictation, suggested question) already has, and refusing is the designed
   direction — but it means the community screens depend on `screen.tsx`'s `/status` poll having
   completed once. Measured: not reproduced as a user-visible race in this increment, and not proved
   absent either. It wants a driver scenario on a cold first frame if Rook wants it closed.
2. **`observeSessionContext` does not notify on `refused`.** A malformed context, or a context that
   goes missing on a device that was publishing one, is refused at the reply — so nothing new is
   adopted — but already-rendered cached content is not cleared, because the seam never fires. Same
   for the read-receipt challenge: it is not dropped until the next `changed`. This is pre-existing
   seam behaviour in `device-session.ts`, outside the two surfaces named for this increment, and it is
   the one gap I can see that this suite does not make safe.
3. **The stamp's own emission is not verified end-to-end from the UI.** Everything here is source
   audit plus synthetic replies. No live device was contacted and no request was made to a real
   broker.
4. **`lib/community.ts` now imports `voice-control.ts` for `replySessionContext`.** No cycle exists
   (`voice-control` imports only `device-session` and `saved-history`), but the community transport
   now depends on the voice helper for one function. If that direction is unwanted, the two-line
   `parseSessionContext` call can be inlined instead.
5. Unchanged and still open: O2 (each menu destination records its real origin and clears stale
   `backTo`) and O3 (neutral copy for a blanked/unavailable voice publication), both still queued
   under this lane.

## 8. Boundaries preserved

Source-only, default-off, `homeEnabled` false. No new writer. No device, service, session, radio,
model, audio, installation or deployment action. No private-action enablement. No live send, receipt
or consent challenge was issued — every write path in the tests runs against a mocked transport. Whole
auth, consent, hardware and acceptance gates remain open.
