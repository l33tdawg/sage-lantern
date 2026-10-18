"""The parent deployer: backups, predecessor anchor, receipt, and no activation."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/deploy-staged-idle-bundle-local.py"
SPEC = importlib.util.spec_from_file_location("idle_deployer", SCRIPT)
deployer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployer)


class FakeRun:
    def __init__(self, environment=""):
        self.calls = []
        self.environment = environment

    def __call__(self, argv):
        self.calls.append(argv)
        return type("Result", (), {"returncode": 0, "stdout": self.environment.encode()})()


class DeployerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "root"
        self.stage = self.base / "stage"
        self.backups = self.base / "backups"
        self.run = FakeRun()
        self.owner = os.geteuid()
        (self.root / "etc/systemd/system/sage-lantern-voice-loop.service.d").mkdir(parents=True)
        (self.root / "opt/sage-lantern").mkdir(parents=True)

    def bundle(self, files, *, scope="CF_wayland_stage_only"):
        (self.stage / "payload").mkdir(parents=True, exist_ok=True)
        items = []
        for index, (destination, data, mode) in enumerate(files):
            artifact = f"payload/{index:03d}"
            (self.stage / artifact).write_bytes(data)
            (self.root / destination.lstrip("/")).parent.mkdir(parents=True, exist_ok=True)
            items.append(dict(destination=destination, source="generated", mode=mode, owner="root:root",
                              size=len(data), sha256=hashlib.sha256(data).hexdigest(), artifact=artifact))
        plan = dict(version=1, scope=scope, activation=False, missing=[], files=items)
        (self.stage / "manifest.json").write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
        self.manifest = self.base / "frozen.json"
        self.manifest.write_text((self.stage / "manifest.json").read_text())
        return plan

    def deploy(self, **overrides):
        options = dict(root=self.root, predecessor=None, accepted=(), owner=self.owner, run=self.run)
        options.update(overrides)
        return deployer.deploy(self.stage, self.manifest, self.backups, **options)

    def put_existing(self, destination, data, mode=0o644):
        target = self.root / destination.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
        return target

    def test_installs_new_files_with_plan_modes_and_never_activates(self):
        self.bundle([("/opt/sage-lantern/sage_lantern/suspend_session.py", b"module\n", 0o644),
                     ("/usr/local/libexec/sage-lantern-idle-suspend", b"runner\n", 0o755)])
        receipt = self.deploy()
        self.assertEqual((self.root / "opt/sage-lantern/sage_lantern/suspend_session.py").read_bytes(), b"module\n")
        self.assertEqual((self.root / "opt/sage-lantern/sage_lantern/suspend_session.py").stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.root / "usr/local/libexec/sage-lantern-idle-suspend").stat().st_mode & 0o777, 0o755)
        self.assertFalse(receipt["activated"])
        self.assertEqual(receipt["replaced_unverifiable_targets"], [])
        self.assertEqual(self.run.calls, [])
        self.assertEqual((self.backups / "receipt.json").exists(), True)

    def test_reloads_systemd_only_for_installed_unit_paths(self):
        self.bundle([("/etc/systemd/system/sage-lantern-llm.service.d/90-sleep-baseline.conf", b"[Service]\n", 0o644)])
        receipt = self.deploy()
        self.assertEqual(self.run.calls, [["/usr/bin/systemctl", "daemon-reload"]])
        self.assertIs(receipt["daemon_reload"], True)
        self.assertNotIn("enable", json.dumps(self.run.calls))
        self.assertNotIn("start", json.dumps(self.run.calls))

    def test_creates_a_missing_reviewed_dropin_directory_only(self):
        self.bundle([("/etc/systemd/system/sage-lantern-device-auth.service.d/90-sage-lantern-sleep.conf",
                      b"[Unit]\n", 0o644)])
        self.deploy()
        directory = self.root / "etc/systemd/system/sage-lantern-device-auth.service.d"
        self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
        self.assertEqual((directory / "90-sage-lantern-sleep.conf").read_bytes(), b"[Unit]\n")
        self.bundle([("/etc/nowhere/else/file.conf", b"[Unit]\n", 0o644)])
        shutil.rmtree(self.root / "etc/nowhere")
        with self.assertRaisesRegex(deployer.Refused, "destination_parent_missing"):
            self.deploy()

    def test_refuses_an_overwrite_that_neither_matches_the_predecessor_nor_is_accepted(self):
        self.put_existing("/opt/sage-lantern/sage_lantern/suspend_system.py", b"unit-evolved\n")
        self.bundle([("/opt/sage-lantern/sage_lantern/suspend_system.py", b"release\n", 0o644)])
        with self.assertRaisesRegex(deployer.Refused, "destination_differs_from_predecessor"):
            self.deploy()
        self.assertEqual((self.root / "opt/sage-lantern/sage_lantern/suspend_system.py").read_bytes(),
                         b"unit-evolved\n")
        receipt = self.deploy(accepted=["/opt/sage-lantern/sage_lantern/suspend_system.py"])
        self.assertEqual(receipt["replaced_unverifiable_targets"],
                         [{"destination": "/opt/sage-lantern/sage_lantern/suspend_system.py",
                           "observed_sha256": hashlib.sha256(b"unit-evolved\n").hexdigest()}])
        self.assertEqual((self.root / "opt/sage-lantern/sage_lantern/suspend_system.py").read_bytes(), b"release\n")
        backups = [path for path in self.backups.iterdir() if path.name != "receipt.json"]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"unit-evolved\n")

    def test_predecessor_anchor_accepts_a_reviewed_successor_without_a_stopgap(self):
        self.put_existing("/opt/sage-lantern/sage_lantern/suspend_system.py", b"r16\n")
        predecessor = self.base / "predecessor"
        (predecessor / "opt/sage-lantern/sage_lantern").mkdir(parents=True)
        (predecessor / "opt/sage-lantern/sage_lantern/suspend_system.py").write_bytes(b"r16\n")
        self.bundle([("/opt/sage-lantern/sage_lantern/suspend_system.py", b"r17\n", 0o644)])
        receipt = self.deploy(predecessor=predecessor)
        self.assertEqual(receipt["replaced_unverifiable_targets"], [])
        self.assertEqual(receipt["files"][0]["replaced_sha256"], hashlib.sha256(b"r16\n").hexdigest())

    def test_refuses_symlinked_destinations_untrusted_modes_and_bad_payloads(self):
        target = self.root / "opt/sage-lantern/sage_lantern"
        target.mkdir(parents=True)
        (target / "linked.py").symlink_to(target / "elsewhere.py")
        self.bundle([("/opt/sage-lantern/sage_lantern/linked.py", b"new\n", 0o644)])
        with self.assertRaisesRegex(deployer.Refused, "destination_not_a_regular_file"):
            self.deploy()
        (target / "linked.py").unlink()
        self.put_existing("/opt/sage-lantern/sage_lantern/linked.py", b"old\n", 0o666)
        with self.assertRaisesRegex(deployer.Refused, "untrusted_destination"):
            self.deploy()
        self.put_existing("/opt/sage-lantern/sage_lantern/linked.py", b"old\n", 0o644)
        (self.stage / "payload/000").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(deployer.Refused, "payload_does_not_match_manifest"):
            self.deploy()

    def test_refuses_the_mesh_voice_lane_by_name(self):
        (self.root / "etc/sage-lantern").mkdir(parents=True)
        (self.root / "etc/sage-lantern/mesh-runtime.json").write_text("{}\n")
        self.bundle([(deployer.VOICE_DROPIN, b"[Service]\n", 0o644)])
        with self.assertRaisesRegex(deployer.Refused, "mesh_voice_lane_owns_execstart"):
            self.deploy()

    def test_mesh_state_is_reported_for_the_operator(self):
        """C today: the fleet installer sets the mesh env with no mesh runtime."""
        self.run = FakeRun(environment="SAGE_LANTERN_MESH_BROKER=1 ALSA_CONFIG_PATH=/etc/x.conf")
        self.assertEqual(deployer.mesh_managed(Path("/"), self.run), (False, "dangling_mesh_environment"))
        self.assertEqual(deployer.mesh_managed(self.root, self.run), (False, "unknown"))
        (self.root / "etc/sage-lantern").mkdir(parents=True)
        (self.root / "etc/sage-lantern/mesh-runtime.json").write_text("{}\n")
        self.assertEqual(deployer.mesh_managed(self.root, self.run), (True, "configured"))
        (self.root / "etc/sage-lantern/mesh-runtime.json").unlink()
        self.run = FakeRun(environment="ALSA_CONFIG_PATH=/etc/x.conf")
        self.assertEqual(deployer.mesh_managed(Path("/"), self.run), (False, "absent"))

    def test_voice_dropin_is_installed_and_recorded_when_no_mesh_runtime_exists(self):
        self.run = FakeRun(environment="SAGE_LANTERN_MESH_BROKER=1")
        self.bundle([(deployer.VOICE_DROPIN, b"[Service]\nEnvironment=SAGE_LANTERN_MESH_BROKER=0\n", 0o644)])
        receipt = self.deploy(run=self.run)
        self.assertEqual(receipt["voice_environment"], "unknown")
        self.assertEqual((self.root / deployer.VOICE_DROPIN.lstrip("/")).read_bytes(),
                         b"[Service]\nEnvironment=SAGE_LANTERN_MESH_BROKER=0\n")

    def test_refuses_an_incomplete_stage_or_unfrozen_manifest(self):
        self.bundle([("/opt/sage-lantern/sage_lantern/suspend_session.py", b"module\n", 0o644)])
        other = json.loads((self.stage / "manifest.json").read_text())
        other["files"][0]["destination"] = "/opt/sage-lantern/sage_lantern/other.py"
        self.manifest.write_text(json.dumps(other) + "\n")
        with self.assertRaisesRegex(deployer.Refused, "stage_manifest_mismatch"):
            self.deploy()
        self.manifest.write_text((self.stage / "manifest.json").read_text())
        plan = json.loads((self.stage / "manifest.json").read_text())
        plan["missing"] = ["software/runtime/src/sage_lantern/gone.py"]
        (self.stage / "manifest.json").write_text(json.dumps(plan))
        self.manifest.write_text(json.dumps(plan))
        with self.assertRaisesRegex(deployer.Refused, "manifest_incomplete"):
            self.deploy()

    def test_never_creates_the_idle_enable_marker(self):
        self.bundle([("/etc/sage-lantern/session-profile", b"wayland-mutter\n", 0o644)])
        self.deploy()
        self.assertEqual((self.root / "etc/sage-lantern/session-profile").read_bytes(), b"wayland-mutter\n")
        self.assertFalse((self.root / "etc/sage-lantern-idle-suspend.enabled").exists())
