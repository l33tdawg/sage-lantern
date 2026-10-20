# C-native lifecycle settlement

Status: disabled source contract, 20 October 2026. Nothing is installed on C.
No native owner, durable store or device probe is supplied. Wiring this into
the launch authority is currently blocked on the exact stopped-child, frozen
capture selection and restart-recoverable journal boundaries documented in
[`C_NATIVE_LAUNCH_SETTLEMENT_INTEGRATION.md`](C_NATIVE_LAUNCH_SETTLEMENT_INTEGRATION.md).

The desktop launch authority can record an attempted release and then lose the
ability to prove what happened to the child. A timeout, a new boot, a new
generation or a numeric PID cannot settle that uncertainty. The obligation
stays until a native owner shows that the exact launch and every resource it
owned are gone.

## What the contract requires

`software/security/desktop_loader_candidate/lifecycle_settlement.py` defines a
source-level settlement contract:

- `LaunchBinding` records the machine, boot, capture, generation, policy digest
  and launch UUID the obligation belongs to.
- `SettlementObligation` carries that binding and the exact owned resources.
  At least one resource must be a pidfd or child identity.
- `bind_release` maps the frozen release selection and launch UUID to that
  obligation without validating or settling it.
- `SettlementProof` carries the obligation digest, the observing machine and
  boot, the native owner name, one terminal observation per resource, and a
  digest over the proof payload.
- `SettlementAuthority` accepts the proof only when every resource is present
  exactly once, has a terminal state for its kind, and the proof digests match.

Terminal states are kind-specific: a pidfd must be dead, a child reaped or
absent, a cgroup absent or empty, a file descriptor closed, a socket closed or
absent, and a mount unmounted or absent.

A different boot ID is accepted only with the complete resource evidence. A
boot change alone, a closed socket alone, or a missing observation returns
`uncertain` and preserves the obligation.

## Durable journal contract

`software/security/desktop_loader_candidate/settlement_journal.py` adds the
next layer: one record per obligation, with `record_attempt` creating the
attempt once and `settle` moving it from `attempted` to `settled` through a
compare-and-exchange. The durable record includes the canonical launch binding
and exact resource list, so a fresh trusted owner can recover and probe an
attempt after restart.

`FileJournalBackend` is the durable source implementation. It requires a
private mode-0700 directory, writes each record through a mode-0600 temporary
file, fsyncs it, renames it into place and fsyncs the directory. A lock file
serialises create and compare-exchange. Reopening the same directory returns
the same record.

The journal checks the authority result first, then writes the settlement with
the exact obligation digest, proof digest and a derived receipt digest. It
reads the record back before returning `settled`. A repeated settlement with
the same proof returns the existing record; a different proof cannot replace
it. A CAS conflict, a missing attempt, an unavailable backend or an
unconfirmed readback returns `uncertain`.

If a write reports unknown after committing, the next read sees `settled` and
a retry with the same proof returns `settled`. If the write never committed,
the record stays `attempted`. The in-memory test backend models both cases.
`FileJournalBackend` is the durable source implementation, but it is not
installed or qualified on the unit.

## Native probe contract

`software/security/desktop_loader_candidate/native_probe.py` defines the
interface a future native owner implements:

- `NativeProbe.observe(obligation)` reads the exact recorded resources and
  returns a `SettlementProof` only when all of them are terminal.
- It may inspect a pidfd, cgroup, descriptor, socket or mount.
- It must not adopt a PID, signal a process, kill a child, unpin state or
  write to the unit.
- `settle_from_probe` passes the proof through the validated journal. A
  missing probe, an invalid result or a probe error returns `uncertain`.

The default `UnavailableProbe` refuses. The Linux implementation is described
below and is still a source-only, uninstalled component.

`software/security/desktop_loader_candidate/linux_probe.py` is the Linux
implementation. It reads the machine ID and boot ID, checks a pidfd with
`pidfd_send_signal(fd, 0, ...)`, reads `cgroup.events`, scans
`/proc/net/unix`, parses mountinfo and checks descriptor state. It accepts a
configurable procfs root for tests and an injectable pidfd liveness function.
It returns a proof only when every recorded resource is terminal.

The pidfd check sends signal 0, which checks liveness without delivering a
signal. It does not adopt a PID or kill a child. A disposable `sleep` child was
probed on Unit C: the pidfd was opened, the child exited 0, the probe produced
a proof and the durable journal moved `attempted` to `settled` at revision 1.
The evidence is in
`docs/lantern/evidence/LINUX_PROBE_CHILD_C_20261019.json`. The probe is not
wired to a service or the desktop launch authority. The C loader now requires a
stopped-child `record_attempt` callback before release; the fitted Linux
backend refuses that callback with `EOPNOTSUPP`.

## Disabled owner facade

`software/security/desktop_loader_candidate/settlement_owner.py` composes the
pieces into one reviewable owner:

- `track_release(release, capture_id, policy_sha256, resources)` binds the
  frozen release selection to the settlement obligation and records the
  attempt.
- `settle(obligation)` asks the probe for evidence and passes it through the
  journal.

The owner takes a journal and a probe in its constructor. It has no service
wiring, no activation file and no device path.

## What is proven

The tests in `scripts/tests/test_lifecycle_settlement.py` cover:

- a complete terminal proof settles once and returns a receipt;
- a second settlement attempt is refused;
- a missing, extra or duplicate resource does not settle;
- an unknown state does not settle;
- a wrong launch, machine or proof digest does not settle;
- a different boot with full resource evidence settles;
- a different boot without child evidence is refused;
- a release binding preserves the launch, machine and generation;
- a release binding with no resources is refused;
- an unavailable backend returns uncertain and settles nothing.

The tests in `scripts/tests/test_settlement_journal.py` cover:

- attempt creation is idempotent;
- settlement is idempotent with the same proof;
- a different proof cannot replace an existing settlement;
- settlement without an attempt is refused;
- a CAS conflict leaves the attempt in place;
- an unknown write after commit is recovered by readback;
- an unknown write before commit leaves the attempt in place;
- an invalid proof writes nothing;
- an unavailable journal backend is uncertain.

`scripts/tests/test_file_journal.py` adds 8 tests for the durable store:
round-trip persistence, exclusive create, compare-exchange conflict, private
directory enforcement, symlink refusal, failure before replace, failure after
replace, and concurrent create.

`scripts/tests/test_native_probe.py` adds 4 tests: unavailable probe, valid
probe settling through the journal, probe error and invalid probe result.

`scripts/tests/test_linux_probe.py` adds 13 tests against a synthetic procfs:
terminal proof, live pidfd, populated cgroup, present socket, present mount,
open descriptor, machine mismatch, absent cgroup, missing identity and an
end-to-end settlement through the durable journal, plus running, zombie and
PID-reused child identities.

`scripts/tests/test_settlement_owner.py` adds six end-to-end and failure tests:
track and settle a release through the Linux probe and file journal, refuse a
release with no resources, refuse an uncertain or already-settled durable
attempt, recover a durable attempt through a reopened journal, and keep an
unavailable recovery state uncertain.

`scripts/lantern/settlement_fault_replay.py` runs 16 source-level cases and
writes `docs/lantern/evidence/SETTLEMENT_FAULT_REPLAY_20261019.json`. The
record covers normal settlement, create and CAS failures before and after the
rename, CAS conflict, duplicate settlement, different proof, new boot with
full evidence, missing child identity, unavailable backend, counter read and
advance failures, owner substitution, and a stale counter after restart.

These are source tests against synthetic evidence. They do not prove that a
real pidfd is dead, that a cgroup is empty, that a socket is closed, or that
the native owner can persist settlement across power loss.

## What the native owner still needs

1. A serialized authority that records the attempt and the settlement in the
   same durable transaction on the target filesystem.
2. Independently protected monotonic state, with rollback resistance and a
   complete read-back after every write. The source now has an injectable
   counter port, refuses a missing/stale counter, and includes a TPM2 NV-counter
   adapter; actual TPM/RPMB provisioning and cold-power drills remain open.
3. A real pidfd and cgroup probe that reports terminal state without adopting
   a PID or killing a possibly released child. The Linux source exists and
   passed a disposable-child run on C; it still needs integration with the
   actual launch authority and a full native fault replay.
4. Resource identities recorded at attempt time, including the child and every
   owned descriptor or mount.
5. A redacted evidence digest per observation and a signed or otherwise
   protected owner identity.
6. Disabled fault replays at every write and probe boundary, including crashes,
   cancellation, stale boot records and a settlement write whose result is
   unknown.

Until those exist, the correct result is `uncertain`. The obligation remains.

## Integration boundary

This module is intentionally separate from
`capture_publication_prototype.py`, whose publication and release sequencing is
frozen. The settlement contract consumes the launch UUID, machine ID and
selection fields from that record; it does not change the frozen prototype.

The contract grants no desktop access, radio access, storage authority or model
authority. It is not installed on C and it is not a D-022 acceptance result.
