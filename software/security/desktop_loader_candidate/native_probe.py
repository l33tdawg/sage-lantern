"""Disabled native probe contract for lifecycle settlement.

The native owner implements ``observe`` by inspecting the exact resources
recorded for an attempt. It may read pidfd state, the cgroup, descriptors,
sockets and mounts. It must not adopt a PID, signal a process, kill a child,
unpin state or write to the unit.

This module wires a probe to the validated journal. It contains no native
syscalls, no service and no activation path. The default probe refuses.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeResult:
    """A proof when every resource is terminal, otherwise no proof."""
    proof: object | None
    reason: str = ""


@dataclass(frozen=True)
class ProbeOutcome:
    status: str
    reason: str = ""
    journal: object | None = None


class NativeProbe:
    """Interface implemented by the future native owner."""
    def observe(self, obligation):
        raise NotImplementedError


class UnavailableProbe(NativeProbe):
    """Default: no native owner exists on this host."""
    def observe(self, obligation):
        return ProbeResult(None, "native_probe_unavailable")


def settle_from_probe(journal, probe, obligation):
    """Ask the probe for evidence, then settle only through the journal."""
    try:
        result = probe.observe(obligation)
    except (OSError, RuntimeError, ValueError) as error:
        return ProbeOutcome("uncertain", str(error))
    if type(result) is not ProbeResult:
        return ProbeOutcome("uncertain", "native_probe_result_invalid")
    if result.proof is None:
        return ProbeOutcome("uncertain", result.reason or "native_probe_uncertain")
    outcome = journal.settle(obligation, result.proof)
    return ProbeOutcome(outcome.status, outcome.reason, outcome)
