# Owner decisions, 14 October 2026

Recorded verbatim from the owner when the browser audit turned into a fix pass. These
are product decisions, not measurements, and they are the authority for the changes made
under them.

## 1. The portrait conversation list stays collapsible but becomes pin-able

**Decision:** "Persistent but collapsible on portrait" — it still starts collapsed as it
does today, but it can be pinned open and stays pinned across navigation.

**Why it was asked:** on the device's 720x1560 portrait panel the past-chats list is a
340px off-canvas drawer, because `useDrawerNavigation` treats
`(max-width: 760px), (orientation: portrait)` as narrow and every portrait panel matches
it. A real persistent left pane exists only in landscape above 760px. A permanently
persistent pane on portrait would have taken roughly 45% of the panel width, so the
owner chose a pin rather than a redesign.

**Implemented as:** a "Keep open" / "Unpin" control (`aria-pressed`) in the drawer
heading (`app/device/screen.tsx`), which switches the layout to the same 220px column
the landscape layout uses — in flow, no scrim, no focus trap (`app/device/threads.css`).
The pin is session state only; nothing is persisted, because this app persists no
protected content anywhere.

## 2. `homeEnabled` stays ON, recorded as an approved deviation

**Decision:** "Leave it ON as the current tree has it" and record it as an owner-approved
deviation from the brief.

**Why it was asked:** `app/device/access.tsx` renders `<DeviceScreen homeEnabled />` at
both call sites. `access.tsx` is not in the brief's authorised edit list, and both the
brief and `TASK_QUEUE.md` reserved the default/release change to Rook, while an earlier
handback stated the flag was still default-false.

**Status:** the code is unchanged and Home remains reachable on hardware. This is an
owner decision, above Rook's earlier reservation, and is recorded here so the next person
does not read the flip as an unauthorised change.

## 3. Shared header controls are raised to the 72px floor

**Decision:** "Raise them to the 72px floor."

**Why it was asked:** every body control meets the brief's 72px minimum
(`screen.css`: `.device-screen button { min-width: 72px; min-height: 72px }`), but four
shared header controls measured 171x56, 56x56 and 91x56 at 720x1560, 480x1040 and
360x800 — the wordmark, Nearby, Messages and Menu — against `.device-top button`'s own
64px ask.

**Implemented by:** the stylesheet lane, verified by measured rectangles at all four
viewports rather than by reading CSS.
