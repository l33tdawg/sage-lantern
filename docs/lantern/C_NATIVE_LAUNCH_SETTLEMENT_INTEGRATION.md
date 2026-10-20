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

1. **The broker is still a source replay, not the fitted Linux broker.** The
   actual `loader.c` sequencing core stops the child, passes one descriptor and
   waits at `record_attempt`; the Linux backend still returns `EOPNOTSUPP` and
   no installed broker exists. `main` can now select the broker path only when
   the capture policy, socket and settlement root are all supplied together.
2. **The capture provider needs a root-owned installed dependency closure.**
   The authority can invoke the isolated capture consumer and validate its
   decision against protected policy bytes and current machine/boot. The
   consumer scripts and policy are not installed or qualified on C.
3. **Release and settlement still need one protected native transaction.**
   Publication and settlement have separate contracts. The native owner still
   needs a serialized anchor that records the failed or uncertain release and
   its settlement with rollback-resistant protection and a trusted owner
   identity.

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
`True`. A compiled replay implements the server over the actual loader core.

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

`launch_settlement_recorder.py` binds the staged child identity to the exact
capture selection and calls `SettlementOwner.track_release()` before the broker
may release. `guarded-session-authority.py` accepts an isolated capture policy,
broker socket and settlement root as one required configuration group; when
present it selects `BrokerLaunchOnce`, installs the recorder and runs startup
recovery before serving launch requests.

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

1. Turn the compiled replay broker into the fitted Linux broker and install its
   fixed policy, socket and sidecar assets as one reviewed closure.
2. Turn the isolated capture consumer and fixed policy into a root-owned
   installed dependency closure; never accept capture ID, generation or policy
   digest from bridge JSON or user-writable state.
3. Serialize the release-attempt anchor and settlement journal through the
   native owner. Preserve an unresolved attempt across supervisor crash, boot,
   cancellation and protection-store rollback.
4. Add a protected append-only pending-work/rollback boundary around the
   recoverable journal records and a bound owner identity.
5. Replay failures before and after each durable write, after child exit, at
   release-callback return, during supervisor restart and with stale or reused
   child identities.

The loader's current source-only and uninstalled status remains unchanged. This
document is an implementation-or-blocker handoff, not launch, session, privacy,
power or physical acceptance.
