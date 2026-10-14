# Canonical Lantern UI harness — status, 14 October 2026

This directory is the canonical real-component UI harness. It was **repaired and
rebuilt on 14 October 2026** because it had fallen behind the app and was
manufacturing convincing false failures.

## What was wrong

The fixture's fetch interceptor stubbed `127.0.0.1:3199/status` but not
`127.0.0.1:3199/voice`. The current app polls `/voice` for live voice status
(`app/device/screen.tsx`), and that channel is the **only** one that establishes the
session authority: its reply feeds `observeSessionContext`. With no `/voice` reply the
client's authority stays `unknown` (`lib/device-session.ts`), so `requireAuthority()`
(`lib/conversation-threads.ts`) refuses **every** protected read.

Measured consequence before the repair: 25 of 32 recorded scenarios "failed" with
"No connection to the device service", a status strip reading "Reconnecting…", a
composer disabled with "The device is offline", and the Gallery reporting "Saved
photos unavailable". None of it was a product defect.

`/voice` is genuinely served on the device: `scripts/lantern/unit-a-status-server.py`
answers `/status`, `/voice` and `/health`, with `/voice` returning
`collect_voice_status()`.

## The repair, and the two subtleties that each cost a run

The `/voice` branch answers from the same synthetic service state, with two rules that
are easy to miss and both of which produced a fake P1 when omitted:

1. **Fault parity.** `statusFault()` must gate `/voice` exactly as it gates `/status`.
   One service, one transport. If `/voice` keeps answering while telemetry fails, the
   screen never reports the lost connection and keeps an orphaned session alive with
   keepalives.
2. **A `/voice` read must not advance the scripts.** `status()` is not pure: it
   increments `state.statusReads` and runs the read-counted scripting
   (`interruptAfter`, the `grow` appender, `rotateStatus()`, `tokenSwapAfter`). The app
   polls `/voice` far more often than `/status`, so calling `status()` from both
   channels advanced every script at roughly double rate. The repair uses
   `status(foreignSession = false, pure = false)` and calls `status(false, true)` from
   `/voice`.

## Driver repairs in this copy

- **The gallery race.** The driver read the grid and clicked `thumbs[0]` with no wait,
  so it died with a TypeError whenever the read had not landed. It now polls for the
  thumbnail count and, if the grid is still empty, records a failed check carrying the
  gallery's own state and reason instead of throwing.
- **Incremental results.** `record()` wrote the results file only at the end, so one
  crash discarded the pass/fail record of every scenario that had already run. It now
  writes after each scenario.
- **Opt-in diagnostics.** `DIAGNOSE_PROTECTED_READ`, `DIAGNOSE_CHAT_LAYOUT`,
  `DIAGNOSE_NAV_ORIGIN`, `DIAGNOSE_DESIGN` and `DIAGNOSE_CLEAR_REFRESH` dump the state
  behind a failure (which gate refused a protected read, where focus went, what a screen
  measures) instead of leaving it to inference. They are env-gated and never fire by
  default, so the 59-scenario suite is unchanged.

## The frozen 4 October bundle

`frozen-20261004-bundle/` holds the pre-repair `screen-fixture.js`, `.html`, `.css` and
metabundle. **This directory is not tracked by git**, so those copies are the only
surviving ones — `build-screen-fixture.mjs` writes to fixed filenames and would
otherwise have destroyed them. Do not delete them.

## Still not proven here

`conversation/select` is a stub that returns `{ok:true}` without switching threads, and
the fixture serves a single conversation. "Does selecting a past chat load the right
thread among several" is therefore **not provable** with this harness. Multi-thread
support is the next fixture increment; until it exists, treat multi-thread selection as
untested rather than working.
