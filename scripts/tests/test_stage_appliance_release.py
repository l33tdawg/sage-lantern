import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("stage_appliance", ROOT / "scripts/stage-appliance-release.py")
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)


class StageApplianceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.artifacts = self.root / "source", self.root / "artifacts"
        self.source.mkdir()
        self.artifacts.mkdir()
        self.output = self.root / "output"
        for row in STAGE.required_sources():
            self.write(self.source / row["path"], (ROOT / row["path"]).read_bytes()
                       if row["path"] in STAGE.RADIO_PINS or row["path"] in STAGE.MAINTENANCE_PINS or row["path"] in STAGE.NPU_SOURCE_PINS else b"reviewed fixture source\n")
        self.write(self.source / "software/runtime/pyproject.toml", b"fixture\n")
        self.write(self.source / "software/runtime/src/sage_lantern/__init__.py", b"__version__ = '0.2.0'\n")
        self.write(self.artifacts / STAGE.SDK_ROOT / "pyproject.toml", b"fixture\n")
        self.write(self.artifacts / STAGE.SDK_ROOT / "README.md", b"fixture\n")
        self.write(self.artifacts / STAGE.SDK_ROOT / "src/sage_sdk/__init__.py", b"fixture\n")
        self.write(self.artifacts / STAGE.SAGE_MANIFEST, b"{}\n")
        package = dict(name="sage-lantern-fleet-admin", version="0.1.0", dependencies={"vinext": "1.0.0"})
        self.ui_lock = dict(lockfileVersion=3, packages={"": package,
            "node_modules/vinext": dict(version="1.0.0"),
            "node_modules/@test/linux": dict(version="2.0.0", os=["linux"], cpu=["arm64"], optional=True),
            "node_modules/@test/mac": dict(version="2.0.0", os=["darwin"], optional=True)})
        for base in (self.source, self.artifacts):
            self.write(base / STAGE.APP / "package.json", package)
            self.write(base / STAGE.APP / "package-lock.json", self.ui_lock)
            self.write(base / STAGE.APP / "public/favicon.svg", b"<svg/>\n")
        self.write(self.artifacts / STAGE.APP / "dist/server/index.js", b"export {};\n")
        self.write(self.artifacts / STAGE.APP / "node_modules/vinext/package.json", dict(name="vinext", version="1.0.0"))
        self.write(self.artifacts / STAGE.APP / "node_modules/vinext/dist/cli.js", b"fixture\n")
        self.write(self.artifacts / STAGE.APP / "node_modules/@test/linux/package.json", dict(name="@test/linux", version="2.0.0"))
        files = [STAGE.BINARY, *STAGE.NPU_ARTIFACTS, *["software/cache/models/" + name for name in STAGE.MODELS],
                 *["software/cache/sources/" + name for name in STAGE.ARCHIVES],
                 STAGE.WHEELS + "sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl",
                 STAGE.WHEELS + "sage_lantern_runtime-0.2.0-py3-none-any.whl",
                 *[STAGE.WHEELS + prefix + "fixture.whl" for prefix in STAGE.BASE_WHEEL_PREFIXES]]
        entries = []
        for name in files:
            data = b"fixture artifact\n"
            self.write(self.artifacts / name, data)
            entries.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        self.lock = dict(schema="sage.lantern.stack-lock.v1", target=dict(os="linux", architecture="arm64", python="3.11"),
                         components={"sage": dict(version="lantern-local.fixture"), "sage-agent-sdk": dict(version="11.19.10+lantern.20260906"),
                                     "lantern-runtime": dict(version="0.2.0")}, files=entries)
        self.write(self.artifacts / STAGE.LOCK, self.lock)
        pins = patch.object(STAGE, 'NPU_ARTIFACT_PINS', {
            name: hashlib.sha256(b'fixture artifact\n').hexdigest() for name in STAGE.NPU_ARTIFACTS})
        pins.start()
        self.addCleanup(pins.stop)
        matcher = patch.object(STAGE.MANIFEST, "matched")
        self.match = matcher.start()
        self.addCleanup(matcher.stop)

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(STAGE.MANIFEST.canonical(data) if isinstance(data, dict) else data)
        path.chmod(0o644)

    def build(self):
        return STAGE.build(self.source, self.artifacts, self.output, "fixture-1")

    def test_reproducible_complete_inventory_and_destinations(self):
        first = self.build()
        second = self.root / "second"
        STAGE.build(self.source, self.artifacts, second, "fixture-1")
        self.assertEqual((self.output / "manifest.json").read_bytes(), (second / "manifest.json").read_bytes())
        self.assertEqual(STAGE.MANIFEST.verify(self.output / "payload", self.output / "manifest.json"), first)
        plan = json.loads((self.output / "payload/release/destinations.json").read_bytes())
        rows = {row["path"]: row for row in plan["files"]}
        self.assertEqual(rows[STAGE.APP + "dist/server/index.js"]["destination"], "/opt/sage-lantern/fleet-admin/dist/server/index.js")
        self.assertEqual(rows[STAGE.BINARY]["destination"], "/opt/sage-lantern/bin/sage-gui")
        self.assertEqual(rows["scripts/lantern/unit-a-device-auth.py"]["destination"], "/usr/local/libexec/sage-lantern-device-auth")
        self.assertIn("scripts/lantern/install-fleet-appliance-local.py", rows)
        self.assertEqual(plan["ui_dependencies"], {"node_modules/@test/linux": "2.0.0", "node_modules/vinext": "1.0.0"})
        for key in ("deployment_ready", "signed_authenticity", "activation_authorized"):
            self.assertIs(plan[key], False)
        self.assertNotIn("/var/lib/sage-lantern", str(plan))
        for path in self.output.rglob("*"):
            self.assertEqual(int(path.stat().st_mtime), STAGE.EPOCH)

    def test_npu_cpu_and_embedding_payloads_have_explicit_destinations(self):
        self.build()
        rows = {row['path']: row for row in json.loads(
            (self.output / 'payload/release/destinations.json').read_bytes())['files']}
        self.assertEqual(rows['software/npu/rkllm_engine.cpp']['operation'], 'offline_build')
        self.assertEqual(rows['software/npu/rkllm_engine.cpp']['destination'], '/opt/sage-lantern/npu/bin/rkllm_engine')
        self.assertEqual(rows['software/npu/rkllm_server.py']['destination'], '/opt/sage-lantern/npu/libexec/rkllm_server.py')
        self.assertEqual(rows['software/npu/vlm_engine.cpp']['operation'], 'offline_build')
        self.assertEqual(rows['software/npu/vlm_engine.cpp']['destination'], '/opt/sage-lantern/npu/bin/vlm_engine')
        self.assertEqual(rows['software/npu/vlm_server.py']['destination'], '/opt/sage-lantern/npu/libexec/vlm_server.py')
        self.assertEqual(rows[STAGE.NPU_ROOT + 'lib/librkllmrt.so']['destination'], '/opt/sage-lantern/npu/lib/librkllmrt.so')
        self.assertEqual(rows[STAGE.NPU_ROOT + 'lib/librknnrt.so']['destination'], '/opt/sage-lantern/npu/lib/librknnrt.so')
        self.assertEqual(rows[STAGE.NPU_ROOT + 'include/rkllm.h']['operation'], 'evidence_only')
        self.assertEqual(rows[STAGE.NPU_ROOT + 'include/rknn_api.h']['operation'], 'evidence_only')
        self.assertEqual(rows[STAGE.NPU_ROOT + 'models/qwen3.5-0.8b_vision_rk3588.rknn']['destination'],
                         '/opt/sage-lantern/npu/models/qwen3.5-0.8b_vision_rk3588.rknn')
        self.assertEqual(rows[STAGE.NPU_ROOT + 'models/qwen3.5-0.8b_w8a8_rk3588.rkllm']['destination'],
                         '/opt/sage-lantern/npu/models/qwen3.5-0.8b_w8a8_rk3588.rkllm')
        for name in ('llm', 'llm-cpu', 'vlm', 'embed', 'embed-shim'):
            unit = 'sage-lantern-' + name + '.service'
            self.assertEqual(rows['software/systemd/' + unit]['destination'], '/etc/systemd/system/' + unit)
        self.assertEqual(rows['software/cache/models/nomic-embed-text-v1.5.Q8_0.gguf']['destination'],
            '/opt/sage-lantern/models/nomic-embed-text-v1.5.Q8_0.gguf')
        self.assertEqual(rows['scripts/lantern/nomic-shim.py']['destination'], '/opt/sage-lantern/nomic-shim/nomic-shim.py')
        self.assertIsNone(rows['software/config/sage-personal.example.yaml']['destination'])

    def test_relocking_replaced_npu_model_does_not_bypass_reviewed_artifact(self):
        name = STAGE.NPU_ROOT + 'models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm'
        replacement = b'unqualified other model'
        self.write(self.artifacts / name, replacement)
        entry = next(entry for entry in self.lock['files'] if entry['path'] == name)
        entry.update(bytes=len(replacement), sha256=hashlib.sha256(replacement).hexdigest())
        self.write(self.artifacts / STAGE.LOCK, self.lock)
        with self.assertRaisesRegex(ValueError, 'unreviewed NPU artifact'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_exact_source_not_replaced_by_role_filler(self):
        (self.source / "scripts/lantern/install-fleet-appliance-local.py").unlink()
        self.write(self.source / "scripts/other-installer.py", b"not the installer\n")
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.build()
        self.assertFalse(self.output.exists())

    def test_exact_maintenance_mapping_and_packaged_hash_closure(self):
        expected = {
            'complete-runtime-maintenance-audio.py': '2d44a31392dc4fc0e624ea928c6da7de5e8bb99848ecc4a81ae11f379f51cae9',
            'build-runtime-maintenance-env.py': '05b496ff69d4017faef914816028ecd9b61a10b421f9cfc0638937c1e325454f',
            'collect-fresh-vision-inputs.py': 'caf8a4883dd7761e9eae260f9971cc9ad67d3fb579255988db911dca79c3d2f8',
            'prepare-runtime-maintenance.py': 'd7b07dec5ea29a789a8c312aa6b3bb8e79ad0444d31ea64b7f64d2e776aa44c8',
            'preflight-fresh-native-update.py': '31734f5538fe9e363e0f75cc97d7af2d5560e914c55356ef4538124619691c2d',
            'update-fresh-runtime.py': '202f71b7ea8f213f3eaf922fb51b6f23a3c280872b48f5605c4418f8a284160e',
        }
        self.assertEqual(STAGE.MAINTENANCE_PINS, {'scripts/lantern/' + name: digest for name, digest in expected.items()})
        with patch('subprocess.run', side_effect=AssertionError('staging must not execute helpers')):
            manifest = self.build()
        plan = json.loads((self.output / 'payload/release/destinations.json').read_bytes())
        entries = {entry['path']: entry for entry in manifest['files']}
        for name, digest in STAGE.MAINTENANCE_PINS.items():
            with self.subTest(name=name):
                rows = [row for row in plan['files'] if row['path'] == name]
                self.assertEqual(rows, [dict(path=name, role='support', destination=None,
                    operation='review_only', destination_mode=None)])
                self.assertEqual(entries[name]['sha256'], digest)
                self.assertEqual((self.output / 'payload' / name).read_bytes(), (ROOT / name).read_bytes())
                self.assertNotIn(name, STAGE.INSTALLERS)
                self.assertNotIn(Path(name).name, STAGE.HELPERS)
        for flag in ('deployment_ready', 'activation_authorized', 'signed_authenticity'):
            self.assertFalse(plan[flag])

    def test_console_lane_and_sleep_bundle_tooling_travels_review_only(self):
        expected = (
            'scripts/lantern/build-console-bundle-local.py',
            'scripts/lantern/deploy-staged-console-bundle-local.py',
            'scripts/lantern/check-console-lane-local.py',
            'scripts/lantern/rehearse-typed-question-local.py',
            'scripts/lantern/configure-kiosk-session-settings.py',
            'scripts/lantern/install-cf-idle-suspend-local.py',
            'scripts/lantern/deploy-staged-idle-bundle-local.py',
            'docs/lantern/CF_CONSOLE_LANE_ACTIVATION.md',
        )
        manifest = self.build()
        plan = json.loads((self.output / 'payload/release/destinations.json').read_bytes())
        entries = {entry['path']: entry for entry in manifest['files']}
        for name in expected:
            with self.subTest(name=name):
                self.assertIn(name, STAGE.SUPPORT)
                self.assertNotIn(name, STAGE.INSTALLERS)
                self.assertNotIn(Path(name).name, STAGE.HELPERS)
                rows = [row for row in plan['files'] if row['path'] == name]
                self.assertEqual(rows, [dict(path=name, role='support', destination=None,
                    operation='review_only', destination_mode=None)])
                self.assertIn(name, entries)
                self.assertEqual((self.output / 'payload' / name).read_bytes(),
                                 (self.source / name).read_bytes())
        # The reviewed local question gate travels as a drop-in for review, and
        # the app installer never installs it: activation stays per unit.
        gate = 'software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf'
        self.assertIn(gate, entries)
        gate_rows = [row for row in plan['files'] if row['path'] == gate]
        self.assertEqual([row['operation'] for row in gate_rows], ['reviewed_copy'])
        self.assertEqual(gate_rows[0]['destination'],
                         '/etc/systemd/system/sage-lantern-voice-loop.service.d/60-local-question-lane.conf')
        self.assertNotIn(gate, (ROOT / 'scripts/lantern/install-fleet-appliance-local.py').read_text())

    def test_missing_or_mutated_maintenance_refuses_before_output(self):
        for name in STAGE.MAINTENANCE_PINS:
            original = (self.source / name).read_bytes()
            for failure in ('missing', 'mutation', 'symlink'):
                with self.subTest(name=name, failure=failure):
                    (self.source / name).unlink()
                    if failure == 'mutation':
                        self.write(self.source / name, original + b'\n')
                    elif failure == 'symlink':
                        (self.source / name).symlink_to(ROOT / name)
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        self.build()
                    self.assertFalse(self.output.exists())
                    if (self.source / name).exists() or (self.source / name).is_symlink():
                        (self.source / name).unlink()
                    self.write(self.source / name, original)

    def test_maintenance_change_during_copy_never_publishes_manifest(self):
        original = STAGE.copy_verified
        target = next(iter(STAGE.MAINTENANCE_PINS))

        def changing_copy(source, destination, expected):
            original(source, destination, expected)
            if source == self.source / target:
                source.write_bytes(source.read_bytes() + b'\n')

        with patch.object(STAGE, 'copy_verified', side_effect=changing_copy):
            with self.assertRaises(ValueError):
                self.build()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_maintenance_not_in_fresh_installer_or_static_helper_maps(self):
        for label, filename, attributes in (
            ('maintenance_app', 'install-fleet-appliance-local.py', ('APPLIANCE_FILES',)),
            ('maintenance_static', 'install-fresh-startup-helpers.py', ('NAMES',)),
        ):
            spec = importlib.util.spec_from_file_location(label, ROOT / 'scripts/lantern' / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for attribute in attributes:
                mapping = getattr(module, attribute)
                for name in STAGE.MAINTENANCE_PINS:
                    self.assertNotIn(name, mapping)
                    self.assertNotIn(Path(name).name, mapping)

    def test_radio_artifact_mappings_are_exact_reviewed_copies(self):
        rows = {row['path']: row for row in STAGE.required_sources()}
        self.assertEqual(rows[STAGE.RADIO_JOURNAL], STAGE.mapping(STAGE.RADIO_JOURNAL, 'dropin',
            '/etc/systemd/system/sage-lantern-mesh-runtime.service.d/25-radio-commission-journal.conf', 'reviewed_copy'))
        cli = rows['scripts/lantern/request-broker-contact.py']
        self.assertEqual(cli['destination'], '/usr/local/libexec/sage-lantern-request-broker-contact')
        self.assertEqual(cli['destination_mode'], 0o755)
        self.assertEqual(cli['operation'], 'reviewed_copy')

    def test_rehashed_radio_artifact_is_not_admitted(self):
        for name in STAGE.RADIO_PINS:
            with self.subTest(name=name):
                original = (self.source / name).read_bytes()
                self.write(self.source / name, original + b'\n')
                with self.assertRaisesRegex(ValueError, 'unreviewed radio commissioning artifact'):
                    self.build()
                self.assertFalse(self.output.exists())
                self.write(self.source / name, original)

    def test_startup_closure_is_required_without_automatic_installation(self):
        self.build()
        plan = json.loads((self.output / "payload/release/destinations.json").read_bytes())
        rows = {row["path"]: row for row in plan["files"]}
        sources = (
            "scripts/lantern/install-startup-coordinator.py",
            "scripts/lantern/handoff-first-audio-lifetime.py",
            "scripts/lantern/handoff-fresh-audio-lifetime.py",
            "scripts/lantern/provision-postcommission-startup.py",
            "scripts/lantern/install-fresh-startup-helpers.py",
            "software/systemd/sage-lantern-startup.service",
        )
        for source in sources:
            self.assertIn(source, rows)
            self.assertEqual(rows[source]["operation"], "review_only")
            self.assertIsNone(rows[source]["destination"])
            self.assertIsNone(rows[source]["destination_mode"])
        expected = {
            "scripts/lantern/" + name: "/opt/sage-lantern/libexec/" + name
            for name in ("startup-coordinator.py", "startup-audio-adapter.py",
                         "recover-cross-boot-audio.py", "collect-cross-boot-audio-pins.py",
                         "activate-fresh-display.py")
        }
        actual = {source: row["destination"] for source, row in rows.items()
                  if (row["destination"] or "").startswith("/opt/sage-lantern/libexec/")}
        self.assertEqual(actual, expected)
        for source in expected:
            self.assertEqual(rows[source]["operation"], "reviewed_copy")
            self.assertEqual(rows[source]["destination_mode"], 0o644)
        self.assertFalse(plan["deployment_ready"])
        self.assertFalse(plan["signed_authenticity"])
        self.assertFalse(plan["activation_authorized"])

    def test_missing_startup_adapter_refuses_release(self):
        (self.source / "scripts/lantern/startup-audio-adapter.py").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_generic_postcommission_runner_refuses_release(self):
        (self.source / "scripts/lantern/provision-postcommission-startup.py").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_generic_handoff_refuses_release(self):
        (self.source / "scripts/lantern/handoff-fresh-audio-lifetime.py").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.build()
        self.assertFalse(self.output.exists())

    def test_display_observer_copy_is_not_activation(self):
        self.build()
        plan = json.loads((self.output / "payload/release/destinations.json").read_bytes())
        rows = {row["path"]: row for row in plan["files"]}
        entry = rows["scripts/lantern/activate-fresh-display.py"]
        self.assertEqual(entry["role"], "installer")
        self.assertEqual(entry["operation"], "reviewed_copy")
        self.assertEqual(entry["destination"], "/opt/sage-lantern/libexec/activate-fresh-display.py")
        self.assertEqual(entry["destination_mode"], 0o644)
        self.assertFalse(plan["activation_authorized"])

    def test_missing_static_installer_refuses_release(self):
        (self.source / "scripts/lantern/install-fresh-startup-helpers.py").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.build()
        self.assertFalse(self.output.exists())

    def test_app_install_does_not_implicitly_install_static_helpers(self):
        spec = importlib.util.spec_from_file_location(
            "static_app_contract", ROOT / "scripts/lantern/install-fleet-appliance-local.py")
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        self.assertFalse(any(destination.startswith("/opt/sage-lantern/libexec/")
                             for destination in app.APPLIANCE_FILES.values()))
        spec = importlib.util.spec_from_file_location(
            "static_runner_contract", ROOT / "scripts/lantern/provision-fresh-appliance.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        from types import SimpleNamespace
        args = SimpleNamespace(unit_id="unit-b", phase="app", app_options=[],
                               allow_native_build=False, install_os_packages=False)
        commands = runner.commands(args, Path("/canonical/payload"))
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(command[1].endswith("/install-fleet-appliance-local.py")
                            for command in commands))
        self.assertNotIn("--install", commands[0])
        self.assertIn("--install", commands[1])

    def test_missing_display_activation_refuses_release(self):
        (self.source / "scripts/lantern/activate-fresh-display.py").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.build()
        self.assertFalse(self.output.exists())

    def test_ui_missing_dependency_or_public_asset(self):
        for name in ("node_modules/@test/linux/package.json", "public/favicon.svg", "node_modules/vinext/dist/cli.js"):
            with self.subTest(name=name):
                target = self.artifacts / STAGE.APP / name
                saved = target.read_bytes()
                target.unlink()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.build()
                self.write(target, saved)

    def test_ui_extra_package_version_and_lock_mismatch(self):
        target = self.artifacts / STAGE.APP / "node_modules/vinext/package.json"
        self.write(target, dict(name="vinext", version="9.0.0"))
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            self.build()
        self.write(target, dict(name="vinext", version="1.0.0"))
        self.write(self.artifacts / STAGE.APP / "node_modules/unlocked/package.json", dict(version="1.0.0"))
        with self.assertRaisesRegex(ValueError, "extra.*dependency"):
            self.build()

    def test_nested_dependency_is_required(self):
        lock = copy.deepcopy(self.ui_lock)
        lock["packages"]["node_modules/vinext/node_modules/nested"] = dict(version="3.0.0")
        for base in (self.source, self.artifacts):
            self.write(base / STAGE.APP / "package-lock.json", lock)
        with self.assertRaisesRegex(ValueError, "dependency inventory"):
            self.build()
        self.write(self.artifacts / STAGE.APP / "node_modules/vinext/node_modules/nested/package.json", dict(version="3.0.0"))
        self.build()

    def test_excluded_platform_parent_excludes_nested_child(self):
        lock = copy.deepcopy(self.ui_lock)
        lock["packages"]["node_modules/@test/mac/node_modules/child"] = dict(version="1.0.0")
        for base in (self.source, self.artifacts):
            self.write(base / STAGE.APP / "package-lock.json", lock)
        self.build()

    def test_bundled_test_fixture_is_part_of_owning_package(self):
        self.write(self.artifacts / STAGE.APP / "node_modules/vinext/test/fixture/node_modules/util/index.js", b"export {};\n")
        self.build()

    def test_state_extra_and_link_rejection(self):
        for name in (".env.local", "private/agent.key", "extra.txt", STAGE.APP + "history.json"):
            with self.subTest(name=name):
                path = self.artifacts / name
                self.write(path, b"not copied\n")
                with self.assertRaises(ValueError):
                    self.build()
                path.unlink()
                if path.parent.name == "private":
                    path.parent.rmdir()
        path = self.artifacts / STAGE.APP / "node_modules/vinext/dist/cli.js"
        saved = path.read_bytes()
        path.unlink()
        path.symlink_to(self.source / STAGE.INSTALLERS[0])
        with self.assertRaisesRegex(ValueError, "linked"):
            self.build()
        path.unlink()
        self.write(path, saved)
        os.link(path, self.root / "hardlink")
        with self.assertRaisesRegex(ValueError, "hardlinked"):
            self.build()

    def test_missing_models_bad_hash_and_legacy_artifacts(self):
        self.lock["files"][0]["sha256"] = "0" * 64
        self.write(self.artifacts / STAGE.LOCK, self.lock)
        with self.assertRaisesRegex(ValueError, "locked artifact mismatch"):
            self.build()

    def test_missing_required_model_and_base_wheel(self):
        for name in ("software/cache/models/ggml-base.bin", "software/cache/models/mmproj-BF16.gguf",
                     *[STAGE.WHEELS + prefix + "fixture.whl" for prefix in STAGE.BASE_WHEEL_PREFIXES]):
            with self.subTest(name=name):
                lock = copy.deepcopy(self.lock)
                lock["files"] = [entry for entry in lock["files"] if entry["path"] != name]
                self.write(self.artifacts / STAGE.LOCK, lock)
                with self.assertRaisesRegex(ValueError, "missing.*required base"):
                    self.build()

    def test_empty_artifact_directory_rejected(self):
        (self.artifacts / "extra-empty").mkdir()
        with self.assertRaisesRegex(ValueError, "empty artifact directory"):
            self.build()

    def test_dependency_absent_from_lock_graph_rejected(self):
        lock = copy.deepcopy(self.ui_lock)
        lock["packages"]["node_modules/vinext"]["dependencies"] = {"missing": "1.0.0"}
        for base in (self.source, self.artifacts):
            self.write(base / STAGE.APP / "package-lock.json", lock)
        with self.assertRaisesRegex(ValueError, "lock graph"):
            self.build()

    def test_artifact_change_during_matching_rejected(self):
        self.match.side_effect = lambda *args: self.write(self.artifacts / STAGE.APP / "dist/server/index.js", b"changed\n")
        with self.assertRaisesRegex(ValueError, "source inventory changed"):
            self.build()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_actual_repository_canonical_sources_exist(self):
        for row in STAGE.required_sources():
            with self.subTest(path=row["path"]):
                self.assertTrue((ROOT / row["path"]).is_file())

    def test_old_sdk_and_unselected_download_refused(self):
        for name in (STAGE.WHEELS + "sage_agent_sdk-11.19.10-py3-none-any.whl", "software/cache/downloads/unused.tar.gz"):
            with self.subTest(name=name):
                lock = copy.deepcopy(self.lock)
                data = b"extra\n"
                self.write(self.artifacts / name, data)
                lock["files"].append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
                self.write(self.artifacts / STAGE.LOCK, lock)
                with self.assertRaisesRegex(ValueError, "ambiguous local wheels|unselected legacy"):
                    self.build()
                (self.artifacts / name).unlink()
                if "downloads" in name:
                    (self.artifacts / name).parent.rmdir()

    def test_added_runtime_module_during_copy_fails_without_manifest(self):
        self.match.side_effect = lambda *args: self.write(self.source / "software/runtime/src/sage_lantern/new_module.py", b"changed\n")
        with self.assertRaisesRegex(ValueError, "source inventory changed"):
            self.build()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_source_replacement_identical_bytes_rejected(self):
        target = self.source / STAGE.INSTALLERS[0]
        def replace(*args):
            temporary = self.source / "replacement"
            self.write(temporary, target.read_bytes())
            os.replace(temporary, target)
        self.match.side_effect = replace
        with self.assertRaisesRegex(ValueError, "source inventory changed"):
            self.build()
        self.assertFalse((self.output / "manifest.json").exists())

    def test_private_key_marker_fails_before_output(self):
        self.write(self.source / STAGE.INSTALLERS[0], b"-----BEGIN PRIVATE KEY-----\n" + b"A" * 40)
        with self.assertRaisesRegex(ValueError, "private-key"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_existing_output_and_nested_roots_refused(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "output must be new"):
            self.build()
        with self.assertRaisesRegex(ValueError, "separate"):
            STAGE.build(self.source, self.artifacts, self.source / "stage", "fixture-1")


if __name__ == "__main__":
    unittest.main()
