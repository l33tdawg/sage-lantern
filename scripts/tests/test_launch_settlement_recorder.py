"""Broker-staged child binding to the durable settlement owner."""
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "software/security/desktop_loader_candidate"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


R = load("launch_settlement_recorder_tested",
         "launch_settlement_recorder.py")


@dataclass
class Selection:
    machine_id: str = "a" * 32
    boot_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    capture_id: str = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
    generation: int = 7
    policy_sha256: str = "d" * 64


@dataclass
class Request:
    launch_id: str = "eeeeeeee-ffff-aaaa-bbbb-cccccccccccc"
    capture_id: str = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
    generation: int = 7
    policy_sha256: str = "d" * 64


@dataclass
class Child:
    pid: int = 123
    start_ticks: int = 456


class RecorderTests(unittest.TestCase):
    def test_records_persistent_child_before_release(self):
        owner = Mock()
        owner.track_release.return_value = (
            object(), Mock(status="attempted"))
        recorder = R.LaunchSettlementRecorder(owner)
        self.assertIs(recorder(Child(), Request(), Selection()), True)
        release, capture, policy, resources = owner.track_release.call_args.args
        self.assertEqual(release.launch_id, Request.launch_id)
        self.assertEqual(release.selection.identity.machine_id, Selection.machine_id)
        self.assertEqual(capture, Selection.capture_id)
        self.assertEqual(policy, Selection.policy_sha256)
        self.assertEqual(resources[0].kind, "child")
        self.assertEqual(resources[0].identity, "child:123:456")

    def test_changed_selection_or_uncertain_attempt_refuses(self):
        owner = Mock()
        recorder = R.LaunchSettlementRecorder(owner)
        with self.assertRaises(R.L.SettlementRefused):
            recorder(Child(), Request(generation=8), Selection())
        owner.track_release.return_value = (
            object(), Mock(status="uncertain", reason="journal unavailable"))
        with self.assertRaisesRegex(
                R.L.SettlementRefused, "journal unavailable"):
            recorder(Child(), Request(), Selection())

    def test_invalid_child_identity_refuses_before_owner(self):
        owner = Mock()
        recorder = R.LaunchSettlementRecorder(owner)
        with self.assertRaises(R.L.SettlementRefused):
            recorder(Child(start_ticks=0), Request(), Selection())
        owner.track_release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
