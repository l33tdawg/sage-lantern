"""Source-contract tests for lifecycle settlement. No native owner exists."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "software/security/desktop_loader_candidate/lifecycle_settlement.py"
SPEC = importlib.util.spec_from_file_location("lifecycle_settlement_tested", SOURCE)
p = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = p
SPEC.loader.exec_module(p)

MACHINE = "a" * 32
BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_BOOT = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
CAPTURE = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
LAUNCH = "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc"
POLICY = "1" * 64

BINDING = p.LaunchBinding(MACHINE, BOOT, CAPTURE, 7, POLICY, LAUNCH)
RESOURCES = (
    p.ResourceRef("pidfd", "pidfd:4242"),
    p.ResourceRef("cgroup", "/sys/fs/cgroup/sage-session-7"),
    p.ResourceRef("socket", "unix:/run/sage/native-7"),
)
OBLIGATION = p.SettlementObligation(BINDING, RESOURCES)


def obligation_digest(obligation=OBLIGATION):
    return hashlib.sha256(p.canonical({
        "binding": {
            "machine_id": obligation.binding.machine_id,
            "boot_id": obligation.binding.boot_id,
            "capture_id": obligation.binding.capture_id,
            "generation": obligation.binding.generation,
            "policy_sha256": obligation.binding.policy_sha256,
            "launch_id": obligation.binding.launch_id,
        },
        "resources": [
            {"kind": item.kind, "identity": item.identity}
            for item in obligation.resources
        ],
    })).hexdigest()


def make_proof(states=None, boot=BOOT, machine=MACHINE, owner="native-owner",
               observations=None, obligation=OBLIGATION):
    states = states or {
        "pidfd": "dead",
        "cgroup": "empty",
        "socket": "closed",
    }
    if observations is None:
        observations = tuple(
            p.ResourceObservation(item, states[item.kind], "2" * 64)
            for item in obligation.resources
        )
    proof = p.SettlementProof(
        obligation_digest(obligation), machine, boot, owner,
        observations, "0" * 64)
    return p.SettlementProof(
        proof.obligation_sha256, proof.observed_machine_id,
        proof.observed_boot_id, proof.owner, proof.observations,
        proof.expected_sha256())


class Backend:
    def __init__(self, proof=None):
        self.proof = proof

    def read_settlement_proof(self, obligation):
        if self.proof is None:
            raise p.SettlementBackendUnavailable("missing")
        return self.proof


class SettlementTests(unittest.TestCase):
    def test_terminal_proof_settles_once(self):
        authority = p.SettlementAuthority(Backend(make_proof()))
        outcome = authority.settle(OBLIGATION)
        self.assertEqual(outcome.status, "settled")
        self.assertFalse(outcome.receipt.runtime_authorized)
        again = authority.settle(OBLIGATION)
        self.assertEqual(again.status, "uncertain")
        self.assertIn("already_settled", again.reason)

    def test_missing_resource_does_not_settle(self):
        proof = make_proof(observations=tuple(
            p.ResourceObservation(item, "dead", "2" * 64)
            for item in RESOURCES[:1]))
        outcome = p.SettlementAuthority(Backend(proof)).settle(OBLIGATION)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIn("missing", outcome.reason)

    def test_extra_resource_does_not_settle(self):
        extra = p.ResourceObservation(p.ResourceRef("fd", "fd:9"),
                                      "closed", "2" * 64)
        proof = make_proof(observations=make_proof().observations + (extra,))
        outcome = p.SettlementAuthority(Backend(proof)).settle(OBLIGATION)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIn("unexpected", outcome.reason)

    def test_unknown_state_does_not_settle(self):
        proof = make_proof(states={"pidfd": "maybe", "cgroup": "empty",
                                   "socket": "closed"})
        outcome = p.SettlementAuthority(Backend(proof)).settle(OBLIGATION)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIn("not_terminal", outcome.reason)

    def test_new_boot_with_full_evidence_settles(self):
        proof = make_proof(states={"pidfd": "dead", "cgroup": "empty",
                                   "socket": "closed"}, boot=OTHER_BOOT)
        authority = p.SettlementAuthority(Backend(proof))
        outcome = authority.settle(OBLIGATION)
        self.assertEqual(outcome.status, "settled")

    def test_obligation_without_child_identity_is_refused(self):
        no_child = p.SettlementObligation(
            BINDING, (p.ResourceRef("socket", "unix:/run/sage/native-7"),))
        outcome = p.SettlementAuthority(Backend(make_proof())).settle(no_child)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIn("child_identity", outcome.reason)

    def test_wrong_launch_does_not_settle(self):
        other = p.SettlementObligation(
            p.LaunchBinding(MACHINE, BOOT, CAPTURE, 7, POLICY,
                            "ffffffff-aaaa-bbbb-cccc-dddddddddddd"),
            RESOURCES)
        outcome = p.SettlementAuthority(Backend(make_proof())).settle(other)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIn("digest_mismatch", outcome.reason)

    def test_wrong_machine_does_not_settle(self):
        proof = make_proof(machine="b" * 32)
        outcome = p.SettlementAuthority(Backend(proof)).settle(OBLIGATION)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIn("machine_mismatch", outcome.reason)

    def test_tampered_proof_digest_does_not_settle(self):
        proof = make_proof()
        tampered = p.SettlementProof(
            proof.obligation_sha256, proof.observed_machine_id,
            proof.observed_boot_id, proof.owner, proof.observations, "f" * 64)
        outcome = p.SettlementAuthority(Backend(tampered)).settle(OBLIGATION)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIn("digest_mismatch", outcome.reason)

    def test_unavailable_backend_preserves_obligation(self):
        outcome = p.SettlementAuthority().settle(OBLIGATION)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIsNone(outcome.receipt)

    def test_duplicate_obligation_resource_refused(self):
        duplicate = p.SettlementObligation(BINDING, (RESOURCES[0], RESOURCES[0]))
        outcome = p.SettlementAuthority(Backend(make_proof())).settle(duplicate)
        self.assertEqual(outcome.status, "uncertain")
        self.assertIn("duplicated", outcome.reason)


if __name__ == "__main__":
    unittest.main()
