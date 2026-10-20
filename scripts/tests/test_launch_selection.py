"""Frozen capture decisions produce only an exact, non-authorizing selection."""
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "software/security/desktop_loader_candidate/launch_selection.py"
SPEC = importlib.util.spec_from_file_location("launch_selection_tested", SOURCE)
S = importlib.util.module_from_spec(SPEC)
sys.modules["launch_selection_tested"] = S
SPEC.loader.exec_module(S)

MACHINE = "a" * 32
BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CAPTURE = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
VIEW = "1" * 64
UNIT = "/usr/share/gnome-shell/org.gnome.Shell@ubuntu.service"
POLICY_PATH = "/etc/sage-lantern/root-gnome-capture-policy.json"


@dataclass(frozen=True)
class Decision:
    evidence_consumable: bool
    reasons: tuple[str, ...]
    policy_sha256: str | None
    analysis_view_digest: str | None
    unit_path: str | None
    capture_id: str | None
    generation: int | None
    runtime_authorized: bool = False


def policy():
    return {
        "schema": S.SCHEMA,
        "contract_version": 3,
        "machine_id": MACHINE,
        "boot_id": BOOT,
        "capture_id": CAPTURE,
        "generation": 7,
        "authority_file": "/var/lib/sage-lantern-desktop/gnome-capture/authority.json",
        "source_directory": "/usr/local/lib/sage-lantern/gnome-contract",
        "capture_directory": "/var/lib/sage-lantern-desktop/gnome-capture/current",
        "consumer_sha256": "2" * 64,
        "python_sha256": "3" * 64,
        "payload_digests": {},
    }


class LaunchSelectionTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.dumps(policy(), sort_keys=True).encode()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.decision = Decision(
            True, (), self.digest, VIEW, UNIT, CAPTURE, 7)

    def select(self, decision=None, raw=None, identity=None):
        return S.from_capture_decision(
            POLICY_PATH, self.decision if decision is None else decision,
            read_file=lambda _: self.raw if raw is None else raw,
            platform_identity=(MACHINE, BOOT)
            if identity is None else identity)

    def test_eligible_decision_binds_exact_policy(self):
        value = self.select()
        self.assertEqual(
            (value.machine_id, value.boot_id, value.capture_id,
             value.generation, value.policy_sha256),
            (MACHINE, BOOT, CAPTURE, 7, self.digest))
        self.assertFalse(value.runtime_authorized)

    def test_revoked_or_nonempty_reasons_refuse(self):
        for decision in (
                replace(self.decision, evidence_consumable=False),
                replace(self.decision, reasons=("capture_is_not_current",)),
                replace(self.decision, runtime_authorized=True)):
            with self.assertRaisesRegex(
                    S.LaunchSelectionRefused, "capture_not_eligible"):
                self.select(decision)

    def test_changed_policy_or_platform_identity_refuses(self):
        changed = json.dumps({**policy(), "generation": 8},
                             sort_keys=True).encode()
        with self.assertRaisesRegex(
                S.LaunchSelectionRefused, "capture_decision_policy_mismatch"):
            self.select(raw=changed)
        with self.assertRaisesRegex(
                S.LaunchSelectionRefused, "capture_policy_mismatch"):
            self.select(identity=("b" * 32, BOOT))

    def test_decision_cannot_substitute_capture_or_view(self):
        for decision in (
                replace(self.decision, capture_id="d" * 8 + CAPTURE[8:]),
                replace(self.decision, generation=8),
                replace(self.decision, analysis_view_digest="bad"),
                replace(self.decision, unit_path="relative/path")):
            with self.assertRaises(S.LaunchSelectionRefused):
                self.select(decision)

    def test_duplicate_policy_keys_refuse(self):
        raw = ('{"schema":"%s","schema":"%s"}' %
               (S.SCHEMA, S.SCHEMA)).encode()
        with self.assertRaisesRegex(
                S.LaunchSelectionRefused, "capture_policy_decode"):
            self.select(raw=raw)

    def test_provider_uses_isolated_consumer_and_refreshes_selection(self):
        calls = []
        payload = json.dumps({
            "evidence_consumable": True,
            "reasons": [],
            "policy_sha256": self.digest,
            "analysis_view_digest": VIEW,
            "unit_path": UNIT,
            "capture_id": CAPTURE,
            "generation": 7,
            "runtime_authorized": False,
        }).encode()
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return type("Result", (), {
                "returncode": 0, "stdout": payload})()
        provider = S.provider_from_capture(
            POLICY_PATH, read_file=lambda _: self.raw,
            platform_identity=(MACHINE, BOOT), run=run)
        first = provider()
        second = provider()
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][1:3], ["-I", "-S"])


if __name__ == "__main__":
    unittest.main()
