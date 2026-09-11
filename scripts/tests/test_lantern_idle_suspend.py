import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from sage_lantern.suspend_kiosk import KioskBinding, Process


SPEC = importlib.util.spec_from_file_location("idle_runner", Path(__file__).parents[1] / "lantern/lantern-idle-suspend.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class KioskControllerTests(unittest.TestCase):
    def test_controller_uses_installed_a_b_binding_not_legacy_or_other_unit(self):
        for unit, account, uid in (("unit-a", "radxa", 1000), ("unit-b", "operator", 1001)):
            with self.subTest(unit=unit):
                binding = KioskBinding(account, uid, unit)
                launcher = Process(1735, 100, 12345, uid, "/usr/bin/bash", binding.launcher_argv[0], "S")
                legacy = Process(4000, 100, 15000, 1000, "/usr/bin/bash", KioskBinding.legacy().launcher_argv[0], "S")
                backend = Mock()
                backend.snapshot.return_value = {1735: launcher, 4000: legacy}
                with patch.object(MODULE, "load_kiosk_binding", return_value=binding), \
                        patch.object(MODULE, "LinuxProcesses", return_value=backend), \
                        patch.object(MODULE, "KioskFence") as constructor:
                    constructor.return_value.preflight.return_value = True
                    self.assertIs(MODULE.kiosk_fence(), constructor.return_value)
                    self.assertEqual(constructor.call_args.kwargs["binding"], binding)
                    self.assertEqual(constructor.call_args.kwargs["launcher_pid"], 1735)
                    self.assertEqual(constructor.call_args.kwargs["launcher_start_ticks"], 12345)

    def test_controller_missing_binding_fails_before_process_inventory(self):
        with patch.object(MODULE, "load_kiosk_binding", side_effect=FileNotFoundError), \
                patch.object(MODULE, "LinuxProcesses") as backend:
            with self.assertRaises(FileNotFoundError):
                MODULE.kiosk_fence()
            backend.assert_not_called()

    def test_controller_rejects_wrong_profile_and_ambiguous_exact_launchers(self):
        binding = KioskBinding("radxa", 1000, "unit-a")
        launcher = Process(1735, 100, 12345, 1000, "/usr/bin/bash", binding.launcher_argv[0], "S")
        other = Process(1736, 100, 12346, 1000, "/usr/bin/bash", binding.launcher_argv[0], "S")
        legacy = Process(1737, 100, 12347, 1000, "/usr/bin/bash", KioskBinding.legacy().launcher_argv[0], "S")
        for table in ({1737: legacy}, {1735: launcher, 1736: other}):
            backend = Mock()
            backend.snapshot.return_value = table
            with patch.object(MODULE, "load_kiosk_binding", return_value=binding), \
                    patch.object(MODULE, "LinuxProcesses", return_value=backend), \
                    patch.object(MODULE, "KioskFence") as constructor:
                with self.assertRaisesRegex(ValueError, "kiosk_launcher_ambiguous"):
                    MODULE.kiosk_fence()
                constructor.assert_not_called()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.appliance = object.__new__(MODULE.Appliance)
        self.appliance.owner = 123
        self.appliance.auth_instance = "a" * 32
        self.appliance.transition_id = "transition-123"
        self.appliance.voice_identity = dict(instance_id="instance", pid=123, start_ticks="1234",
                                              boot_id="boot", invocation_id="b" * 32)
        prior = patch.object(MODULE, "prior_auth_drained", return_value=True)
        prior.start()
        self.addCleanup(prior.stop)

    def test_auth_receipt_requires_exact_positive_cleanup(self):
        receipt = dict(version=1, instance_id="a" * 32, closing=True, drained=True,
                       local_revoked=True, upstream_cleanup="confirmed")
        with patch.object(MODULE, "read_json", return_value=receipt):
            self.assertTrue(self.appliance.auth_drained())
        for key, value in [("version", True), ("drained", 1), ("closing", False),
                           ("local_revoked", False), ("upstream_cleanup", "unknown"),
                           ("instance_id", "c" * 32), ("extra", True)]:
            with self.subTest(key=key), patch.object(MODULE, "read_json", return_value={**receipt, key: value}):
                self.assertFalse(self.appliance.auth_drained())

    def test_voice_receipt_matches_instance_transition_and_every_check(self):
        checks = {name: True for name in ["http_drained", "http_outcome_known", "session_finalized",
                  "child_groups_absent", "memory_drained", "community_drained", "history_outcome_known", "deadline_met"]}
        receipt = dict(self.appliance.voice_identity, schema="sage.lantern.voice-drain.v1",
                       transition_id="transition-123", checks=checks, drained=True, reconcile_required=False)
        with patch.object(MODULE, "read_json", return_value=receipt):
            self.assertTrue(self.appliance.voice_drained())
        for key in checks:
            with self.subTest(key=key), patch.object(MODULE, "read_json", return_value={**receipt, "checks": {**checks, key: False}}):
                self.assertFalse(self.appliance.voice_drained())
        with patch.object(MODULE, "read_json", return_value={**receipt, "transition_id": "stale"}):
            self.assertFalse(self.appliance.voice_drained())

    def test_policy_capability_does_not_mutate_stored_preferences(self):
        value = dict(revision=1, display_off_seconds=60, deep_sleep_seconds=600, deep_sleep_available=True)
        with patch.object(MODULE, "public_json", return_value=value):
            policy = MODULE.power_policy()
        self.assertTrue(value["deep_sleep_available"])
        self.assertFalse(policy["deep_sleep_available"])
        self.assertEqual(policy["deep_sleep_seconds"], 600)

    def test_new_input_during_preparation_denies_sleep(self):
        self.appliance.initial_idle = 600
        self.appliance.sampled_at = 100
        self.appliance.policy = {"deep_sleep_seconds": 300}
        with patch.object(MODULE.time, "monotonic", return_value=110):
            with patch.object(MODULE, "idle_seconds", return_value=610):
                self.assertTrue(self.appliance.idle_eligible())
            with patch.object(MODULE, "idle_seconds", return_value=9):
                self.assertFalse(self.appliance.idle_eligible())

    def test_sleep_guard_missing_metadata_denies(self):
        with patch.object(MODULE, "read_json", side_effect=FileNotFoundError):
            self.assertFalse(MODULE.sleep_guard())

    def test_service_identity_rejects_job_and_stale_fields(self):
        data = "MainPID=123\nInvocationID=" + "a" * 32 + "\nActiveState=active\nJob="
        with patch.object(MODULE, "command", return_value=data):
            self.assertEqual(MODULE.service_identity(MODULE.AUTH), (123, "a" * 32))
        with patch.object(MODULE, "command", return_value=data + "queued"):
            with self.assertRaises(ValueError):
                MODULE.service_identity(MODULE.AUTH)


class PriorAuthTests(unittest.TestCase):
    def test_boot_unit_does_not_order_after_its_install_target(self):
        unit = (Path(__file__).parents[2] / "software/systemd/sage-lantern-idle-suspend.service").read_text()
        self.assertIn("WantedBy=graphical.target", unit)
        after = next(line for line in unit.splitlines() if line.startswith("After="))
        self.assertNotIn("graphical.target", after.split("=", 1)[1].split())
        self.assertIn("display-manager.service", after)

    def test_prior_unknown_cannot_be_cleared_by_a_new_broker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            previous = root / ("a" * 32 + ".json")
            value = dict(version=1, instance_id="a" * 32, closing=True, drained=True,
                         local_revoked=True, upstream_cleanup="confirmed")
            previous.write_text(json.dumps(value))
            previous.chmod(0o600)
            with patch.object(MODULE, "AUTH_REPORTS", root):
                self.assertTrue(MODULE.prior_auth_drained("b" * 32, os.geteuid()))
                value["upstream_cleanup"] = "unknown"
                previous.write_text(json.dumps(value))
                self.assertFalse(MODULE.prior_auth_drained("b" * 32, os.geteuid()))
                previous.unlink()
                (root / ".interrupted-receipt").write_text("")
                self.assertFalse(MODULE.prior_auth_drained("b" * 32, os.geteuid()))
