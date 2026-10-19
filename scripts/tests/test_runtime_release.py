import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("runtime_release", ROOT / "scripts/verify-runtime-release.py")
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
BUILD_SPEC = importlib.util.spec_from_file_location("build_runtime_release", ROOT / "scripts/build-runtime-release.py")
BUILDER = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(BUILDER)
WHEEL = Path(os.environ.get("LANTERN_RELEASE_WHEEL", str(ROOT / "software/bundle/python-wheels/sage_lantern_runtime-0.2.0-py3-none-any.whl")))


class RuntimeReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.fixture_root = Path(cls.temporary.name).resolve()
        project = cls.fixture_root / "software/runtime"
        package = project / "src/sage_lantern"
        package.mkdir(parents=True)
        dependencies = ["PyNaCl==1.6.2", "cryptography==46.0.3", "sage-agent-sdk==11.19.10+lantern.20260906", "PyYAML==6.0.2"]
        payloads = {"pyproject.toml": ('[project]\nversion="0.2.0"\ndependencies=' + json.dumps(dependencies) + '\n').encode()}
        for module in RELEASE.REQUIRED | {"__init__", "__main__"}:
            payloads[f"src/sage_lantern/{module}.py"] = b'__version__ = "0.2.0"\n'
        for name, data in payloads.items():
            (project / name).write_bytes(data)
        manifest = {"schema": "sage.lantern.runtime-release.v1", "version": "0.2.0",
                    "dependencies": dependencies, "build_tools": dict(BUILDER.TOOLS),
                    "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}
        cls.fixture_wheel = cls.fixture_root / "fixture.whl"
        with zipfile.ZipFile(cls.fixture_wheel, "w") as archive:
            for name, data in payloads.items():
                if name.startswith("src/"):
                    archive.writestr(name.removeprefix("src/"), data)
            archive.writestr("sage_lantern/release-manifest.json", json.dumps(manifest))
            for name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD"):
                data = "\n".join("Requires-Dist: " + dependency for dependency in dependencies) if name == "METADATA" else ""
                archive.writestr("sage_lantern_runtime-0.2.0.dist-info/" + name, data)

    def test_wheel_contains_frozen_fixture_source_and_required_modules(self):
        manifest = RELEASE.verify_wheel(self.fixture_wheel, self.fixture_root)
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertIn("sage-agent-sdk==11.19.10+lantern.20260906", manifest["dependencies"])
        self.assertEqual(manifest["build_tools"], {"build": "1.4.0", "setuptools": "80.9.0", "wheel": "0.45.1"})

    def test_no_state_keys_caches_or_sdk_inside_wheel(self):
        with zipfile.ZipFile(WHEEL) as archive:
            for name in archive.namelist():
                self.assertNotIn("..", Path(name).parts)
                self.assertNotIn("__pycache__", name)
                self.assertFalse(name.endswith((".key", ".db", ".sqlite", ".pyc")))
                self.assertTrue(name.startswith(("sage_lantern/", "sage_lantern_runtime-0.2.0.dist-info/")))
                if name.startswith("sage_lantern/"):
                    self.assertTrue(name.endswith(".py") or name == "sage_lantern/release-manifest.json")

    def test_packaged_modules_parse_as_python_311(self):
        with zipfile.ZipFile(WHEEL) as archive:
            for name in archive.namelist():
                if name.endswith(".py"):
                    ast.parse(archive.read(name), filename=name, feature_version=(3, 11))

    def test_missing_sdk_methods_or_keyword_parameters_rejected(self):
        methods = {name: set(parameters) for name, parameters in RELEASE.SDK_APIS.items()}
        RELEASE.verify_sdk_methods(methods)
        for name in methods:
            with self.subTest(name=name):
                missing = dict(methods)
                del missing[name]
                with self.assertRaises(ValueError):
                    RELEASE.verify_sdk_methods(missing)
            for parameter in methods[name]:
                with self.subTest(name=name, parameter=parameter):
                    missing = {method: set(arguments) for method, arguments in methods.items()}
                    missing[name].remove(parameter)
                    with self.assertRaises(ValueError):
                        RELEASE.verify_sdk_methods(missing)
        methods["message_send"] -= {"idempotency_key"}
        with self.assertRaises(ValueError):
            RELEASE.verify_sdk_methods(methods)

    def test_modified_or_missing_module_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for altered in (False, True):
                destination = Path(temporary) / f"bad-{altered}.whl"
                with zipfile.ZipFile(self.fixture_wheel) as source, zipfile.ZipFile(destination, "w") as target:
                    for name in source.namelist():
                        if name == "sage_lantern/network_setup.py":
                            if altered:
                                target.writestr(name, b"pass\n")
                            continue
                        target.writestr(name, source.read(name))
                with self.assertRaises(ValueError):
                    RELEASE.verify_wheel(destination, self.fixture_root)

    def test_verifier_rejects_extra_pth_package_cache_and_key_payloads(self):
        for extra in ("unexpected.pth", "other_package/__init__.py", "sage_sdk/client.py",
                      "sage_lantern/__pycache__/cached.pyc", "sage_lantern/agent.key", "../escape"):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "extra.whl"
                with zipfile.ZipFile(self.fixture_wheel) as source, zipfile.ZipFile(destination, "w") as target:
                    for name in source.namelist():
                        target.writestr(name, source.read(name))
                    target.writestr(extra, b"unreviewed")
                with self.assertRaisesRegex(ValueError, "unexpected release payload|SDK"):
                    RELEASE.verify_wheel(destination, self.fixture_root)

    def test_verifier_rejects_source_inventory_drift(self):
        added = self.fixture_root / "software/runtime/src/sage_lantern/added.py"
        try:
            added.write_text("pass\n")
            with self.assertRaisesRegex(ValueError, "source inventory differs"):
                RELEASE.verify_wheel(self.fixture_wheel, self.fixture_root)
        finally:
            added.unlink()

    def test_build_rejects_module_addition_or_removal_after_snapshot(self):
        for change in ("add", "remove", "modify"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                project = root / "software/runtime"
                package = project / "src/sage_lantern"
                package.mkdir(parents=True)
                (project / "pyproject.toml").write_text('[project]\nversion="0.2.0"\ndependencies=[]\n')
                (package / "__init__.py").write_text('__version__ = "0.2.0"\n')
                def mutate(*args, **kwargs):
                    if change == "add":
                        (package / "new_module.py").write_text("pass\n")
                    elif change == "remove":
                        (package / "__init__.py").unlink()
                    else:
                        (package / "__init__.py").write_text("pass\n")
                with patch.object(BUILDER.importlib.metadata, "version", side_effect=BUILDER.TOOLS.__getitem__), \
                        patch.object(BUILDER.subprocess, "run", side_effect=mutate):
                    with self.assertRaisesRegex(ValueError, "source changed" if change == "modify" else "module set changed"):
                        BUILDER.build(root / "output", root)

    def test_legacy_sdk_base_surface_never_implies_canonical_messaging(self):
        methods = {name: set(parameters) for name, parameters in RELEASE.SDK_APIS.items()}
        del methods["message_storage_status"]
        methods["message_send"].remove("require_encrypted_storage")
        with self.assertRaises(ValueError):
            RELEASE.verify_sdk_methods(methods)
        report = RELEASE.canonical_messaging_capability(methods)
        self.assertIs(report["available"], False)
        self.assertIs(report["verified"], False)
        self.assertEqual(report["missing_methods"], ["message_storage_status"])
        self.assertEqual(report["missing_parameters"], ["message_send.require_encrypted_storage"])
        methods["message_storage_status"] = set()
        methods["message_send"].add("require_encrypted_storage")
        report = RELEASE.canonical_messaging_capability(methods)
        self.assertIs(report["available"], False)
        self.assertEqual(report["reason"], "runtime_storage_and_auth_verification_required")

    def test_installed_import_gate_rejects_wrong_dependency_version(self):
        with patch.object(RELEASE.importlib, "import_module", return_value=SimpleNamespace(__version__="0.2.0")), \
                patch.object(RELEASE.importlib.metadata, "version", return_value="wrong"):
            with self.assertRaises(ValueError):
                RELEASE.verify_installed()

    def test_pyyaml_metadata_pin_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'missing-yaml.whl'
            with zipfile.ZipFile(self.fixture_wheel) as source, zipfile.ZipFile(destination, 'w') as target:
                for name in source.namelist():
                    raw = source.read(name)
                    if name.endswith('/METADATA'):
                        raw = raw.replace(b'Requires-Dist: PyYAML==6.0.2', b'')
                    target.writestr(name, raw)
            with self.assertRaisesRegex(ValueError, 'missing dependency pin'):
                RELEASE.verify_wheel(destination, self.fixture_root)

    def test_pyyaml_artifact_requires_exact_target_bytes_and_unique_version(self):
        reviewed = ROOT / 'software/bundle/python-wheels' / RELEASE.YAML_WHEEL
        raw = reviewed.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), RELEASE.YAML_SHA256)
        for fault in (None, 'missing', 'changed', 'foreign', 'duplicate', 'symlink'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary).resolve()
                destination = directory / RELEASE.YAML_WHEEL
                if fault == 'symlink':
                    destination.symlink_to(reviewed)
                elif fault != 'missing':
                    destination.write_bytes(raw + b'changed' if fault == 'changed' else raw)
                if fault == 'foreign':
                    destination.rename(directory / 'PyYAML-6.0.2-cp311-cp311-macosx_10_9_x86_64.whl')
                if fault == 'duplicate':
                    (directory / 'pyyaml-6.0.3-py3-none-any.whl').write_bytes(raw)
                if fault is None:
                    self.assertEqual(RELEASE.verify_yaml_wheel(directory), destination)
                else:
                    with self.assertRaises(ValueError):
                        RELEASE.verify_yaml_wheel(directory)

    def test_installed_yaml_rejects_missing_wrong_shadowed_or_system_package(self):
        for fault in (None, 'missing', 'version', 'import', 'shadow', 'system', 'parser'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                installed = root / 'yaml/__init__.py'
                package = SimpleNamespace(__version__='6.0.2', __file__=str(installed),
                    safe_load=lambda raw: {} if fault == 'parser' else {'enabled': True})
                if fault == 'shadow':
                    package.__file__ = str(root / 'shadow/yaml/__init__.py')
                distribution = SimpleNamespace(locate_file=lambda path: installed)
                def version(name):
                    self.assertEqual(name, 'PyYAML')
                    if fault == 'missing':
                        raise RELEASE.importlib.metadata.PackageNotFoundError(name)
                    return '6.0.3' if fault == 'version' else '6.0.2'
                def importing(name):
                    self.assertEqual(name, 'yaml')
                    if fault == 'import':
                        raise ModuleNotFoundError(name)
                    return package
                with patch.object(RELEASE.importlib.metadata, 'version', side_effect=version), \
                        patch.object(RELEASE.importlib.metadata, 'distribution', return_value=distribution), \
                        patch.object(RELEASE.importlib, 'import_module', side_effect=importing), \
                        patch.object(RELEASE.sys, 'prefix', str(root / 'venv' if fault == 'system' else root)):
                    if fault is None:
                        RELEASE.verify_yaml_installed()
                    else:
                        with self.assertRaises((ValueError, ModuleNotFoundError, RELEASE.importlib.metadata.PackageNotFoundError)):
                            RELEASE.verify_yaml_installed()

    def test_fresh_installer_checks_release_before_claim_and_after_pip(self):
        installer = (ROOT / "scripts/install-lantern-unit.sh").read_text()
        self.assertLess(installer.index('verify-runtime-release.py" --bundle'), installer.index("\nclaim_install_root\n"))
        self.assertLess(installer.index("sage-lantern-runtime==0.2.0"), installer.index('verify-runtime-release.py" --installed'))
        self.assertNotIn("sage-lantern-runtime==0.1.0", installer)
        self.assertIn("sage-agent-sdk==11.19.10+lantern.20260906", installer)
        self.assertLess(installer.index('verify-runtime-release.py" --binary-target'), installer.index("\nclaim_install_root\n"))
        self.assertNotIn('!= *"11.19.10"*', installer)

    def test_required_protected_mesh_modules_exist_and_guard_media_apis_required(self):
        current = {path.stem for path in (ROOT / "software/runtime/src/sage_lantern").glob("*.py")}
        self.assertLessEqual(RELEASE.REQUIRED, current)
        self.assertLessEqual({"conversation_workflow_store", "conversation_records", "protected_conversation_owner",
                              "private_media_store", "workflow_store", "public_workflow_store", "mesh_runtime",
                              "mesh_usb_opener", "mesh_serial_owner", "meshcore_contacts",
                              "mesh_daemon", "mesh_voice_ipc", "mesh_read_receipts", "mesh_read_root", "mesh_public_root", "mesh_gateway_root",
                              "storage_supervisor", "storage_supervisor_socket", "gateway_contact", "gateway_evidence",
                              "public_mesh_protocol", "public_mesh_provider", "public_gateway_bridge", "public_gateway_runtime", "public_gateway_composition",
                              "public_gateway_observation", "public_queue"}, RELEASE.REQUIRED)
        self.assertIn("guard", RELEASE.SDK_APIS["workflow_put"])
        self.assertIn("deployment_drain", RELEASE.REQUIRED)
        self.assertIn("trust_env", RELEASE.SDK_APIS["__init__"])
        self.assertEqual(RELEASE.SDK_APIS["private_media_get"], {"object_id"})
        self.assertEqual(RELEASE.SDK_APIS["private_media_put"], {"object_id", "jpeg"})

    def test_protected_module_missing_from_both_source_and_wheel_is_rejected(self):
        for module in ("workflow_store", "conversation_workflow_store", "private_media_store", "mesh_runtime", "deployment_drain",
                       "mesh_daemon", "mesh_voice_ipc", "mesh_read_receipts", "mesh_read_root", "mesh_public_root", "mesh_gateway_root",
                       "storage_supervisor", "storage_supervisor_socket", "gateway_contact", "gateway_evidence",
                       "public_mesh_protocol", "public_mesh_provider", "public_gateway_bridge", "public_gateway_runtime", "public_gateway_composition",
                       "public_gateway_observation", "public_queue"):
            path = self.fixture_root / f"software/runtime/src/sage_lantern/{module}.py"
            original = path.read_bytes()
            member = f"sage_lantern/{module}.py"
            try:
                path.unlink()
                with tempfile.TemporaryDirectory() as temporary:
                    wheel = Path(temporary) / "missing.whl"
                    with zipfile.ZipFile(self.fixture_wheel) as source, zipfile.ZipFile(wheel, "w") as target:
                        for name in source.namelist():
                            if name != member:
                                target.writestr(name, source.read(name))
                    with self.subTest(module=module), self.assertRaisesRegex(ValueError, "required module missing"):
                        RELEASE.verify_wheel(wheel, self.fixture_root)
            finally:
                path.write_bytes(original)

    def test_installed_modules_in_explicit_isolated_validation_environment(self):
        python = os.environ.get("LANTERN_RELEASE_PYTHON")
        if not python:
            self.skipTest("supply a separately provisioned validation venv to exercise installed imports")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONPATH", None)
        result = subprocess.run([python, str(ROOT / "scripts/verify-runtime-release.py"), "--installed"],
                                cwd=tempfile.gettempdir(), env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SDK signatures verified without client construction", result.stdout)


class BinaryPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.binary = self.root / "software/bundle/linux-arm64/bin/sage-gui"
        self.binary.parent.mkdir(parents=True)
        data = bytearray(64)
        data[:7] = b"\x7fELF\x02\x01\x01"
        data[18:20] = (183).to_bytes(2, "little")
        self.binary.write_bytes(bytes(data))
        self.binary.chmod(0o755)
        self.version = "lantern-local.reviewed-20260906"
        self.lock_path = self.root / "software/stack.lock.json"
        self.lock = dict(components={"sage": {"version": self.version}}, files=[dict(
            path=str(self.binary.relative_to(self.root)), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())])
        self.save_lock()
        self.result = subprocess.CompletedProcess([], 0, f"sage-gui {self.version} (commit source-abcd, built 2026-09-06T00:00:00Z, max-app-v30)\n", "")

    def save_lock(self):
        self.lock_path.write_text(json.dumps(self.lock))

    def test_exact_local_version_hash_and_isolated_version_command(self):
        with patch.object(RELEASE.subprocess, "run", return_value=self.result) as run:
            self.assertEqual(RELEASE.verify_target_binary(self.root), self.version)
        self.assertEqual(run.call_args.args[0], [str(self.binary), "version"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertEqual(run.call_args.kwargs["env"]["SAGE_HOME"], "/nonexistent")
        self.assertFalse((self.root / "opt").exists())

    def test_version_mismatch_prefix_suffix_and_missing_output_refused(self):
        for version in ("11.19.10", self.version + ".unreviewed", "prefix-" + self.version, ""):
            result = subprocess.CompletedProcess([], 0, self.result.stdout.replace(self.version, version), "")
            with self.subTest(version=version), patch.object(RELEASE.subprocess, "run", return_value=result), self.assertRaises(ValueError):
                RELEASE.verify_target_binary(self.root)

    def test_native_auto_vote_capability_suffix_preserves_exact_version_gate(self):
        output = self.result.stdout.replace('max-app-v30)', 'max-app-v30, auto-vote-v28)')
        with patch.object(RELEASE.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')):
            self.assertEqual(RELEASE.verify_target_binary(self.root), self.version)
        for malformed in (output.replace(self.version, self.version + '.wrong'),
                          output.replace('auto-vote-v28', 'auto-vote-vunknown'),
                          output.replace('auto-vote-v28)', 'auto-vote-v28, extra)'),
                          output.replace('max-app-v30, ', '')):
            with self.subTest(output=malformed), patch.object(RELEASE.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, malformed, '')), self.assertRaises(ValueError):
                RELEASE.verify_target_binary(self.root)

    def test_hash_mismatch_duplicate_lock_and_wrong_architecture_never_execute(self):
        original = self.binary.read_bytes()
        for mode in ("hash", "duplicate", "architecture"):
            self.binary.write_bytes(original)
            self.lock["files"] = [dict(path=str(self.binary.relative_to(self.root)), bytes=len(original), sha256=hashlib.sha256(original).hexdigest())]
            if mode == "hash":
                self.binary.write_bytes(original + b"tampered")
            elif mode == "duplicate":
                self.lock["files"] *= 2
            else:
                data = b"not-elf" + original[7:]
                self.binary.write_bytes(data)
                self.lock["files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
            self.save_lock()
            with self.subTest(mode=mode), patch.object(RELEASE.subprocess, "run") as run, self.assertRaises(ValueError):
                RELEASE.verify_target_binary(self.root)
            run.assert_not_called()

    def test_links_and_release_changes_are_rejected(self):
        link = self.binary.parent / "hardlink"
        os.link(self.binary, link)
        with patch.object(RELEASE.subprocess, "run") as run, self.assertRaises(ValueError):
            RELEASE.verify_target_binary(self.root)
        run.assert_not_called()
        link.unlink()
        self.binary.rename(link)
        self.binary.symlink_to(link)
        with patch.object(RELEASE.subprocess, "run") as run, self.assertRaises(ValueError):
            RELEASE.verify_target_binary(self.root)
        run.assert_not_called()
        self.binary.unlink()
        link.rename(self.binary)
        def changed(*args, **kwargs):
            self.binary.write_bytes(self.binary.read_bytes() + b"changed")
            return self.result
        with patch.object(RELEASE.subprocess, "run", side_effect=changed), self.assertRaisesRegex(ValueError, "release changed"):
            RELEASE.verify_target_binary(self.root)

    def test_failed_timeout_and_stderr_version_commands_deny(self):
        for result in (subprocess.CompletedProcess([], 1, self.result.stdout, ""),
                       subprocess.CompletedProcess([], 0, self.result.stdout, "unexpected diagnostic")):
            with patch.object(RELEASE.subprocess, "run", return_value=result), self.assertRaises(ValueError):
                RELEASE.verify_target_binary(self.root)
        with patch.object(RELEASE.subprocess, "run", side_effect=subprocess.TimeoutExpired("version", 5)), self.assertRaises(subprocess.TimeoutExpired):
            RELEASE.verify_target_binary(self.root)


if __name__ == "__main__":
    unittest.main()
