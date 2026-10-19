# C-native lifecycle settlement

Status: disabled source contract, 19 October 2026. Nothing is installed on C.
No native owner, durable store or device probe is supplied.

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

## What is proven

The tests in `scripts/tests/test_lifecycle_settlement.py` cover:

- a complete terminal proof settles once and returns a receipt;
- a second settlement attempt is refused;
- a missing, extra or duplicate resource does not settle;
- an unknown state does not settle;
- a wrong launch, machine or proof digest does not settle;
- a different boot with full resource evidence settles;
- a different boot without child evidence is refused;
- an unavailable backend returns uncertain and settles nothing.

These are source tests against synthetic evidence. They do not prove that a
real pidfd is dead, that a cgroup is empty, that a socket is closed, or that
the native owner can persist settlement across power loss.

## What the native owner still needs

1. A serialized authority that records the attempt and the settlement in the
   same durable transaction.
2. Independently protected monotonic state, with rollback resistance and a
   complete read-back after every write.
3. A real pidfd and cgroup probe that reports terminal state without adopting
   a PID or killing a possibly released child.
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
