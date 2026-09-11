import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/install-idle-suspend-local.py"
SPEC = importlib.util.spec_from_file_location("idle_install", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_production_controller_inhibits_logind_power_key_after_wake(self):
        service = (SCRIPT.parents[2] / "software/systemd/sage-lantern-idle-suspend.service").read_text()
        starts = [line for line in service.splitlines() if line.startswith("ExecStart=")]
        self.assertEqual(len(starts), 1)
        self.assertIn("--what=sleep:handle-power-key --mode=block", starts[0])
        self.assertIn("/usr/local/libexec/sage-lantern-idle-suspend", starts[0])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.stage = self.root / "bundle"
        for module in installer.MODULES:
            self.put(f"software/runtime/src/sage_lantern/{module}.py", b"pass\n")
        for helper in installer.HELPERS:
            self.put(f"scripts/lantern/{helper}", b"pass\n")
        guard = ("[Service]\nExecCondition=/opt/sage-lantern/venv/bin/python "
                 "/usr/local/libexec/sage-lantern-idle-suspend --sleep-guard\n")
        self.put("scripts/lantern/lantern-idle-suspend.py", f"SLEEP_GUARD_TEXT = {guard!r}\n".encode())
        self.put("software/systemd/sage-lantern-idle-suspend.service", b"[Service]\nUser=root\n")
        self.put("software/systemd/sage-lantern-device-auth.service",
                 b"[Service]\nExecStart=/opt/sage-lantern/venv/bin/python /usr/local/libexec/sage-lantern-device-auth --idle-seconds 120\n")

    def put(self, path, data):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)

    def build(self):
        return installer.build_plan(self.source)

    def stage_plan(self):
        plan, payloads = self.build()
        installer.stage_bundle(self.stage, plan, payloads, plan, owner=os.geteuid())
        return plan

    def test_plan_is_read_only_and_dropins_are_exact(self):
        plan, payloads = self.build()
        self.assertFalse(self.stage.exists())
        self.assertFalse(plan["activation"])
        self.assertEqual(plan["missing"], [])
        self.assertEqual(len(plan["files"]), 36)
        for service in installer.SERVICES:
            data = payloads[f"/etc/systemd/system/sage-lantern-{service}.service.d/90-sage-lantern-sleep.conf"]
            self.assertTrue(data.startswith(installer.FENCE.encode()))
        voice = payloads["/etc/systemd/system/sage-lantern-voice-loop.service.d/90-sage-lantern-sleep.conf"]
        self.assertIn(b"--photo-ready --drain-request /run/sage-lantern-voice-drain-request.json", voice)
        self.assertIn(b"RuntimeDirectoryPreserve=yes\nTimeoutStopSec=30\n", voice)
        self.assertIn(b"Environment=SAGE_LANTERN_INFERENCE_GUARD=/var/lib/sage-lantern/inference\n", voice)
        self.assertIn(b"ReadWritePaths=/var/lib/sage-lantern/inference\n", voice)
        self.assertEqual(plan["inference_directory"], dict(path="/var/lib/sage-lantern/inference",
                         owner="sage-lantern:sage-lantern", mode=0o700))
        destinations = {item["destination"] for item in plan["files"]}
        self.assertIn(installer.PACKAGE + "/inference_guard.py", destinations)
        self.assertIn(installer.PACKAGE + "/adapters.py", destinations)
        self.assertNotIn(installer.PACKAGE + "/suspend_inference.py", destinations)
        auth = payloads["/etc/systemd/system/sage-lantern-device-auth.service.d/90-sage-lantern-sleep.conf"]
        self.assertIn(b"--idle-seconds 120 --drain-report-dir /var/lib/sage-lantern/device-lock/drain", auth)

    def test_boot_model_baseline_wiring_is_staged_without_activation(self):
        plan, payloads = self.build()
        self.assertEqual(payloads["/etc/systemd/system/sage-lantern-llm.service.d/90-sage-lantern-sleep.conf"],
                         b"[Unit]\nConditionPathExists=!/run/sage-lantern-sleep.fenced\n")
        self.assertEqual(payloads["/etc/tmpfiles.d/sage-lantern-suspend.conf"],
                         b"d /run/sage-lantern-model 0700 root root -\n")
        self.assertEqual(payloads["/etc/systemd/system/sage-lantern-llm.service.d/90-sleep-baseline.conf"],
                         b"[Service]\nReadWritePaths=/run/sage-lantern-model\n"
                         b"ExecStartPost=+/opt/sage-lantern/venv/bin/python "
                         b"/usr/local/libexec/sage-lantern-idle-suspend --model-started\n")
        self.assertEqual(plan["model_directory"], dict(path="/run/sage-lantern-model", owner="root:root", mode=0o700))
        self.assertFalse(plan["activation"])
        self.assertFalse(self.stage.exists())
        self.assertTrue(any("Never automatically purge" in item for item in plan["required_prechecks"]))

    def test_stage_check_private_and_no_overwrite(self):
        plan = self.stage_plan()
        installer.check_bundle(self.stage, plan, owner=os.geteuid())
        self.assertEqual(self.stage.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.stage / "payload/000").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.stage_plan()

    def test_missing_inference_guard_blocks_staging(self):
        (self.source / "software/runtime/src/sage_lantern/inference_guard.py").unlink()
        plan, payloads = self.build()
        self.assertEqual(len(plan["missing"]), 1)
        with self.assertRaises(ValueError):
            installer.stage_bundle(self.stage, plan, payloads, plan, owner=os.geteuid())
        self.assertFalse(self.stage.exists())

    def test_changed_source_rejects_frozen_plan(self):
        frozen, _ = self.build()
        self.put("scripts/lantern/unit-a-voice-loop.py", b"changed\n")
        plan, payloads = self.build()
        with self.assertRaises(ValueError):
            installer.stage_bundle(self.stage, plan, payloads, frozen, owner=os.geteuid())
        self.assertFalse(self.stage.exists())

    def test_bad_guard_is_not_imported_or_accepted(self):
        self.put("scripts/lantern/lantern-idle-suspend.py", b"raise RuntimeError()\nSLEEP_GUARD_TEXT='wrong'\n")
        with self.assertRaises(ValueError):
            self.build()

    def test_source_links_and_writable_files_rejected(self):
        path = self.source / "scripts/lantern/unit-a-voice-loop.py"
        path.chmod(0o666)
        with self.assertRaises(ValueError):
            self.build()
        path.chmod(0o644)
        os.link(path, self.source / "hardlink")
        with self.assertRaises(ValueError):
            self.build()
        path.unlink()
        path.symlink_to(self.source / "hardlink")
        with self.assertRaises(OSError):
            self.build()

    def test_artifact_tampering_and_loose_mode_rejected(self):
        plan = self.stage_plan()
        path = self.stage / "payload/000"
        original = path.read_bytes()
        path.write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            installer.check_bundle(self.stage, plan, owner=os.geteuid())
        path.write_bytes(original)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            installer.check_bundle(self.stage, plan, owner=os.geteuid())

    def test_extra_files_and_manifest_mismatch_rejected(self):
        plan = self.stage_plan()
        changed = copy.deepcopy(plan)
        changed["activation"] = True
        with self.assertRaises(ValueError):
            installer.check_bundle(self.stage, changed, owner=os.geteuid())
        (self.stage / "unexpected").touch()
        with self.assertRaises(ValueError):
            installer.check_bundle(self.stage, plan, owner=os.geteuid())

    def test_write_failure_cleans_partial_stage(self):
        plan, payloads = self.build()
        with patch.object(installer, "write_private", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                installer.stage_bundle(self.stage, plan, payloads, plan, owner=os.geteuid())
        self.assertFalse(self.stage.exists())
        self.assertEqual(list(self.root.glob(".idle-stage-*")), [])

    def test_duplicate_manifest_keys_rejected(self):
        with self.assertRaises(ValueError):
            installer.strict_json(b'{"activation":false,"activation":true}')


if __name__ == "__main__":
    unittest.main()
