# C-native launch settlement integration

Status: source review blocker, 20 October 2026. Nothing is installed, enabled
or qualified on Unit C. The launch authority remains disabled.

The settlement owner is implemented, but connecting it directly around the
current Python `LaunchOnce` path would overstate what the authority can prove.
The safe increment is to harden the owner's durable-attempt boundary and record
the exact integration blockers before changing the live launch sequence.

## Current authority boundary

The candidate launch path is:

1. `scripts/lantern/guarded-session-authority.py` admits the root bridge.
2. `LaunchOnce.start()` calls `spawn_shell()`, which execs `gnome-shell`
   directly as UID 1000.
3. The authority opens a pidfd and waits for the exact child's `READY=1`
   credential datagram.
4. Seat and native-PIN publication follow only after those checks.

`LaunchOnce` has one-attempt semantics and never retries or terminates a
possibly released session. Those controls remain correct.

The disabled C loader has the stronger stopped-child boundary needed for
release settlement: `spawn_stopped`, exact-child verification, grant
publication, freeze checks and one release call. Its Linux `prepare` operation
still returns `EOPNOTSUPP`.

## Why settlement is not wired around the live exec

1. **The native stopped-child broker server does not exist yet.** The C loader
   requires `record_attempt()` after all frozen child/map checks and before its
   sole release callback. The Python authority now has a broker-backed launch
   path that stages the stopped child, records it and releases only after an
   exact acknowledgment. The Linux backend still returns `EOPNOTSUPP`, and
   `main` supplies neither the broker connection nor a fitted settlement
   recorder, so the direct `spawn_shell()` remains the only configured path.
2. **The authority has no trusted capture-selection provider.** The source
   selection contract now validates an eligible frozen-consumer decision
   against the exact root-owned policy bytes and current machine/boot. The
   authority requires that selection before `prepare()` can spawn, but `main`
   does not yet invoke the isolated capture consumer or supply the provider.
   A command-line value or user-writable file still cannot establish it.
3. **The launch authority has no recovery hook yet.** The durable journal now
   stores the complete obligation and exposes pending attempts, and
   `SettlementOwner.recover()` reconstructs and probes them after restart. The
   authority does not instantiate or call a trusted settlement owner.
4. **No persistent child identity is recorded by the launch authority.** A
   resource named `pidfd:<fd>` is meaningful only in the process that owns that
   descriptor. The Linux probe now supports a boot-bound
   `child:<pid>:<starttime>` identity that survives supervisor restart and
   refuses PID reuse, but the launch authority does not create that attempt or
   resource yet.
5. **Release and settlement are not one transaction.** Publication and
   settlement have separate contracts. The native owner still needs a
   serialized anchor that records the failed or uncertain release and its
   settlement in the same protected lifecycle.

Each item can hide or manufacture an obligation if ignored. None can be closed
by a timeout, a numeric PID, a fresh boot, a new generation or an unchanged
service file.

## Implemented source hardening

`SettlementOwner.track_release()` now returns an obligation only after
`record_attempt()` reports `attempted`. An unavailable journal, an unconfirmed
read-back, a conflicting record or an already-settled record raises
`SettlementRefused`. A caller cannot proceed merely because it has an
in-memory obligation.

The Linux probe now supports a persistent `child:<pid>:<starttime>` resource.
It distinguishes a running child, an unreaped zombie, an absent child and a
reused PID with a different start time. A live or unreaped process is not
terminal; PID reuse is treated as the original child being absent.

The durable journal now stores the canonical launch binding and exact resource
list. `FileJournalBackend.pending()` returns attempted records and
`SettlementOwner.recover()` reconstructs each obligation and probes it after a
trusted owner restart. Missing or unavailable recovery state returns
`uncertain`; it does not appear as an empty queue.

The C sequencing core now requires a `record_attempt()` operation after the
child is stopped, granted and frozen, immediately before the one release
callback. Failure or cancellation at that point fences the loader and cannot
release the compositor. The Linux backend returns `EOPNOTSUPP`, so the path
remains deliberately closed until a fitted settlement owner exists.

`launch_selection.py` now converts an eligible frozen-consumer decision into an
exact, non-authorizing launch selection. It rejects revoked decisions, reasons,
authority claims, changed policy bytes, wrong machine/boot identity, changed
capture/generation, malformed view digests and non-absolute unit paths. The
root authority refuses to spawn unless a fresh selection provider returns such
a value.

`loader_broker.py` now defines the stopped-child handoff client. It stages one
child, requires exactly one pidfd over `SCM_RIGHTS`, validates PID/start/cookie
identity, calls a durable-record callback, aborts a still-stopped child when
recording fails, and sends `release` only after the callback returns exactly
`True`. The native broker server, authority use of this client and startup
recovery invocation remain open.

`BrokerLaunchOnce` is now the authority alternative to `spawn_shell()`. It
binds the staged child to the current frozen selection, requires an injected
settlement recorder, preserves the staged pidfd on release uncertainty, and
keeps the daemon's one-attempt/no-restart behavior.

The broker authority also requires a startup recovery callback. It probes every
durable pre-release attempt before `prepare()` can spawn and stays held if any
result is uncertain. A compiled native replay now exercises the actual
`loader.c` core over the broker socket: the child stops, the descriptor is
passed, `record_attempt` waits for the client's release acknowledgment, and an
abort kills only the still-stopped child.

Focused controls in `scripts/tests/test_settlement_owner.py` cover:

- track and settle with the durable file journal;
- missing resources;
- an uncertain durable attempt;
- an already-settled attempt;
- running, zombie, absent and PID-reused child observations.
- durable attempt recovery through a reopened file journal;
- unavailable recovery state remaining uncertain.
- an actual stopped-child loader replay proving `record_attempt` precedes
  release and a callback refusal prevents release.
- strict frozen-capture selection binding and authority refusal without it.
- strict loader-broker framing, pidfd handoff and record-before-release order.
- an authority broker path that cannot be constructed without a settlement
  recorder and preserves the staged pidfd on uncertainty.
- a clean-recovery gate before broker-backed launch and a compiled native
  stopped-child/handoff/release replay.

## Required implementation order

1. Add a protected append-only pending-work/rollback boundary around the new
   recoverable journal records. A fresh trusted process can enumerate and
   probe them, but file-level rollback protection and owner identity remain
   open.
2. Record the persistent child identity at attempt time using boot ID, PID and
   process start time, or a native pidfd handoff that remains valid after
   authority restart. A reused numeric PID/FD must not match.
3. Turn the compiled replay broker into the fitted Linux broker, connect its
   `record_attempt` callback to the settlement owner, and configure `main` to
   select `BrokerLaunchOnce` instead of `spawn_shell()`. Do not insert
   settlement after a live exec.
4. Put the isolated frozen-consumer process behind a fixed root-owned provider
   and connect it to `Authority.selection_provider`; never accept capture ID,
   generation or policy digest from bridge JSON or user-writable state.
5. Serialize the release-attempt anchor and settlement journal through the
   native owner. Preserve an unresolved attempt across supervisor crash, boot,
   cancellation and protection-store rollback.
6. Replay failures before and after each durable write, after child exit, at
   release-callback return, during supervisor restart and with stale or reused
   child identities.

The loader's current source-only and uninstalled status remains unchanged. This
document is an implementation-or-blocker handoff, not launch, session, privacy,
power or physical acceptance.
