"""The C-F Wayland sleep bundle: staged only, and shaped like the unit it targets."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/install-cf-idle-suspend-local.py"
SPEC = importlib.util.spec_from_file_location("cf_idle_install", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)

GUARD = ("[Service]\nExecCondition=/opt/sage-lantern/venv/bin/python "
         "/usr/local/libexec/sage-lantern-idle-suspend --sleep-guard\n")


class CfInstallerTests(unittest.TestCase):
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
        self.put("scripts/lantern/lantern-idle-suspend.py", f"SLEEP_GUARD_TEXT = {GUARD!r}\n".encode())
        self.put("software/systemd/sage-lantern-idle-suspend.service", b"[Service]\nUser=root\n")
        self.put("software/systemd/sage-lantern-device-auth.service",
                 b"[Service]\nExecStart=/opt/sage-lantern/venv/bin/python /usr/local/libexec/sage-lantern-device-auth --idle-seconds 120\n")
        self.binding = self.root / "appliance-bindings.json"
        self.write_bindings("hw:CARD=rockchipes8388,DEV=0")

    def write_bindings(self, device):
        self.binding.write_text(json.dumps({"audio": {"alsa_device": device}}) + "\n")

    def test_plan_carries_the_reviewed_capture_binding_into_the_sleep_execstart(self):
        bindings = self.binding
        plan, payloads = installer.build_plan(self.source, bindings=bindings)
        voice = payloads["/etc/systemd/system/sage-lantern-voice-loop.service.d/90-sage-lantern-sleep.conf"]
        self.assertIn(b"--config /etc/sage-lantern/lantern.toml --alsa-device hw:CARD=rockchipes8388,DEV=0", voice)
        self.assertNotIn(b"hw:CARD=Audio,DEV=0", voice)
        # A missing or unreviewed binding refuses instead of pointing capture at a placeholder.
        bindings.unlink()
        with self.assertRaises(ValueError):
            installer.build_plan(self.source, bindings=bindings)
        self.write_bindings("not-a-device")
        with self.assertRaises(ValueError):
            installer.build_plan(self.source, bindings=bindings)
        self.write_bindings("hw:CARD=rockchipes8388,DEV=0")

    def put(self, path, data):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)

    def build(self):
        return installer.build_plan(self.source, bindings=self.binding)

    def test_plan_records_the_wayland_profile_without_activating_anything(self):
        plan, payloads = self.build()
        self.assertEqual(plan["scope"], "CF_wayland_stage_only")
        self.assertFalse(plan["activation"])
        self.assertEqual(plan["missing"], [])
        self.assertEqual(plan["session_profile"], installer.SESSION_PROFILE)
        self.assertEqual(payloads["/etc/sage-lantern/session-profile"], b"wayland-mutter\n")
        self.assertFalse(self.stage.exists())
        destinations = {item["destination"] for item in plan["files"]}
        self.assertIn(installer.PACKAGE + "/suspend_session.py", destinations)
        self.assertNotIn("/usr/local/libexec/sage-lantern-display-power", destinations)
        for service in installer.SERVICES:
            data = payloads[f"/etc/systemd/system/sage-lantern-{service}.service.d/90-sage-lantern-sleep.conf"]
            self.assertTrue(data.startswith(installer.FENCE.encode()))

    def test_cf_stop_set_never_stops_a_unit_the_unit_does_not_run(self):
        plan, payloads = self.build()
        destinations = {item["destination"] for item in plan["files"]}
        for absent in ("camera", "display-power"):
            self.assertNotIn(f"/etc/systemd/system/sage-lantern-{absent}.service.d/90-sage-lantern-sleep.conf",
                             destinations)
        self.assertTrue(any("The voice lane must be enabled" in item for item in plan["required_prechecks"]))
        self.assertTrue(any("lock-enabled false" in item for item in plan["required_prechecks"]))
        self.assertEqual(payloads["/etc/sage-lantern/session-profile"], b"wayland-mutter\n")

    def test_voice_dropin_carries_the_reviewed_nonmesh_command(self):
        plan, payloads = self.build()
        voice = payloads["/etc/systemd/system/sage-lantern-voice-loop.service.d/90-sage-lantern-sleep.conf"]
        self.assertIn(b"ExecStart=\nExecStart=/usr/local/libexec/sage-lantern-voice-loop"
                      b" --config /etc/sage-lantern/lantern.toml --alsa-device hw:CARD=rockchipes8388,DEV=0"
                      b" --photo-ready --drain-request /run/sage-lantern-voice-drain-request.json\n", voice)
        self.assertIn(b"Environment=SAGE_LANTERN_MESH_BROKER=0\n", voice)
        self.assertIn(b"RuntimeDirectoryPreserve=yes\nTimeoutStopSec=30\n", voice)
        self.assertIn(b"Environment=SAGE_LANTERN_INFERENCE_GUARD=/var/lib/sage-lantern/inference\n", voice)
        self.assertIn(b"ReadWritePaths=/var/lib/sage-lantern/inference\n", voice)
        auth = payloads["/etc/systemd/system/sage-lantern-device-auth.service.d/90-sage-lantern-sleep.conf"]
        self.assertIn(b"--idle-seconds 120 --drain-report-dir /var/lib/sage-lantern/device-lock/drain", auth)
        self.assertEqual(plan["voice_drain_request"], "/run/sage-lantern-voice-drain-request.json")
        self.assertTrue(any("mesh runtime is installed" in item for item in plan["required_prechecks"]))

    def test_model_baselines_and_sleep_guard_are_the_reviewed_ones(self):
        plan, payloads = self.build()
        self.assertEqual(payloads["/etc/systemd/system/sage-lantern-llm.service.d/90-sleep-baseline.conf"],
                         b"[Service]\nReadWritePaths=/run/sage-lantern-model\n"
                         b"ExecStartPost=+/opt/sage-lantern/venv/bin/python "
                         b"/usr/local/libexec/sage-lantern-idle-suspend --model-started sage-lantern-llm.service\n")
        self.assertEqual(payloads["/etc/tmpfiles.d/sage-lantern-suspend.conf"],
                         b"d /run/sage-lantern-model 0700 root root -\n")
        self.assertEqual(payloads["/etc/systemd/system/systemd-suspend.service.d/90-sage-lantern.conf"],
                         GUARD.encode())
        self.assertEqual(payloads["/etc/systemd/system/sage-lantern-vlm.service.d/90-sage-lantern-sleep.conf"],
                         installer.FENCE.encode())

    def test_stage_check_private_and_no_overwrite(self):
        plan, payloads = self.build()
        installer.stage_bundle(self.stage, plan, payloads, plan, owner=os.geteuid())
        installer.check_bundle(self.stage, plan, owner=os.geteuid())
        self.assertEqual(self.stage.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.stage / "payload/000").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            installer.stage_bundle(self.stage, plan, payloads, plan, owner=os.geteuid())

    def test_missing_module_and_changed_guard_block_staging(self):
        (self.source / "software/runtime/src/sage_lantern/suspend_session.py").unlink()
        plan, _ = self.build()
        self.assertEqual(plan["missing"], ["software/runtime/src/sage_lantern/suspend_session.py"])
        with self.assertRaises(ValueError):
            installer.stage_bundle(self.stage, plan, {}, plan, owner=os.geteuid())
        self.put("software/runtime/src/sage_lantern/suspend_session.py", b"pass\n")
        self.put("scripts/lantern/lantern-idle-suspend.py", b"SLEEP_GUARD_TEXT = 'unknown'\n")
        with self.assertRaisesRegex(ValueError, "sleep_guard_unexpected"):
            self.build()
