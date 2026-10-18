import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("appliance_manifest", ROOT / "scripts/appliance-release-manifest.py")
MANIFEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANIFEST)
REAL_MATCHED = MANIFEST.matched


class ApplianceManifestTests(unittest.TestCase):
    def test_public_example_requires_exact_file_bytes_and_path(self):
        data = b'-----BEGIN EC PRIVATE KEY-----\n' + b'A' * 64 + b'\n-----END EC PRIVATE KEY-----'
        relative = 'node_modules/miniflare/dist/src/index.js'
        path = self.stage / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        path.chmod(0o644)
        with patch.object(MANIFEST, 'PUBLIC_EXAMPLE_FILES', {relative: hashlib.sha256(data).hexdigest()}):
            self.assertEqual(MANIFEST.file_hash(path)[0]['sha256'], hashlib.sha256(data).hexdigest())
            for changed in (data + b'\n', data.replace(b'A' * 64, b'B' * 64), data + data):
                path.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, 'unreviewed dependency'):
                    MANIFEST.file_hash(path)
            other = self.stage / 'other.js'
            other.write_bytes(data)
            other.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'private-key'):
                MANIFEST.file_hash(other)

    def test_escaped_private_key_literals_are_also_rejected(self):
        path = self.stage / 'embedded.js'
        for newline in (b'\n', b'\r\n', b'\\n', b'\\r\\n'):
            with self.subTest(newline=newline):
                path.write_bytes(b'-----BEGIN RSA PRIVATE KEY-----' + newline + b'A' * 64)
                path.chmod(0o644)
                with self.assertRaisesRegex(ValueError, 'private-key'):
                    MANIFEST.file_hash(path)

    def test_exact_packaged_queue_code_is_not_device_state(self):
        base = "node_modules/miniflare/dist/src/workers/queues"
        for prefix in ("", "apps/lantern-fleet-admin/"):
            for suffix in ("", "/broker.worker.js", "/broker.worker.js.map"):
                name = prefix + base + suffix
                self.assertEqual(MANIFEST.safe_name(name), name)
            for suffix in ("/history.json", "/messages.json", "/agent.key", "/other.js", "/.env", "/state.db"):
                with self.subTest(prefix=prefix, suffix=suffix), self.assertRaises(ValueError):
                    MANIFEST.safe_name(prefix + base + suffix)
        for name in ("queues/broker.worker.js", "photos/" + base + "/broker.worker.js",
                     "other/" + base + "/broker.worker.js"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                MANIFEST.safe_name(name)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.stage = self.directory / "stage"
        self.stage.mkdir(mode=0o755)
        self.selection = dict(schema=MANIFEST.SELECTION_SCHEMA, release_id="fixture-1",
                              components=dict(sage="lantern-local.fixture", sdk="11.19.10+lantern.20260906", runtime="0.2.0"),
                              files=[])
        for role in sorted(MANIFEST.REQUIRED):
            name = role + ".fixture"
            (self.stage / name).write_bytes(b"synthetic release bytes\n")
            (self.stage / name).chmod(0o644)
            self.selection["files"].append(dict(path=name, role=role))
        self.recipe = self.directory / "selection.json"
        self.recipe.write_bytes(MANIFEST.canonical(self.selection))
        self.output = self.directory / "manifest.json"
        matcher = patch.object(MANIFEST, "matched")
        self.match = matcher.start()
        self.addCleanup(matcher.stop)

    def build(self):
        return MANIFEST.build(self.stage, self.recipe, self.output)

    def test_reproducible_manifest_and_verify_never_claim_readiness(self):
        first = self.build()
        second = self.directory / "second.json"
        MANIFEST.build(self.stage, self.recipe, second)
        self.assertEqual(self.output.read_bytes(), second.read_bytes())
        self.assertEqual(MANIFEST.verify(self.stage, self.output), first)
        for field in ("deployment_ready", "runtime_acceptance_verified", "signed_authenticity"):
            self.assertIs(first[field], False)
        self.assertGreaterEqual(self.match.call_count, 3)

    def test_extra_missing_and_empty_directory_rejected(self):
        for mode in ("extra", "missing", "empty"):
            with self.subTest(mode=mode):
                extra = self.stage / "extra.txt"
                missing = self.stage / self.selection["files"][0]["path"]
                if mode == "extra":
                    extra.write_text("unexpected")
                elif mode == "missing":
                    missing.unlink()
                else:
                    extra.mkdir()
                try:
                    with self.assertRaises(ValueError):
                        self.build()
                    self.assertFalse(self.output.exists())
                finally:
                    if extra.is_dir():
                        extra.rmdir()
                    elif extra.exists():
                        extra.unlink()
                    if not missing.exists():
                        missing.write_bytes(b"synthetic release bytes\n")

    def test_metadata_is_read_through_validated_descriptor(self):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unchecked reopen")):
            MANIFEST.build(self.stage, self.recipe, self.output)
            MANIFEST.verify(self.stage, self.output)

    def test_identical_metadata_replacement_is_rejected(self):
        original = MANIFEST.inventory
        content = self.recipe.read_bytes()
        def replace_after_inventory(*args):
            result = original(*args)
            replacement = self.directory / "replacement.json"
            replacement.write_bytes(content)
            replacement.chmod(0o644)
            replacement.replace(self.recipe)
            return result
        with patch.object(MANIFEST, "inventory", side_effect=replace_after_inventory), self.assertRaises(ValueError):
            MANIFEST.build(self.stage, self.recipe, self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_hardlink_and_permissions_rejected(self):
        target = self.stage / self.selection["files"][0]["path"]
        for mode in ("symlink", "hardlink", "permissions"):
            with self.subTest(mode=mode):
                outside = self.directory / "outside"
                if mode == "symlink":
                    target.rename(outside)
                    target.symlink_to(outside)
                elif mode == "hardlink":
                    os.link(target, outside)
                else:
                    target.chmod(0o666)
                try:
                    with self.assertRaises(ValueError):
                        self.build()
                finally:
                    if mode == "symlink":
                        target.unlink()
                        outside.rename(target)
                    elif mode == "hardlink":
                        outside.unlink()
                    target.chmod(0o644)

    def test_paths_state_and_key_material_rejected_even_if_selected(self):
        for name in ("../escape", "/absolute", "secrets/key.json", "identity/agent.json", "a.pth", "key.pem", "history.json", ".env.local", "__pycache__/x.pyc"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                MANIFEST.safe_name(name)
        target = self.stage / self.selection["files"][0]["path"]
        target.write_bytes(b"-----BEGIN PRIVATE KEY-----\n" + b"A" * 40 + b"\n")
        with self.assertRaisesRegex(ValueError, "private-key"):
            self.build()

    def test_source_mutation_addition_and_selection_edits_during_match_rejected(self):
        for mode in ("edit", "add", "selection"):
            with self.subTest(mode=mode):
                original = self.recipe.read_bytes()
                target = self.stage / self.selection["files"][0]["path"]
                saved = target.read_bytes()
                added = self.stage / "added"
                def mutate(*args):
                    if mode == "edit":
                        target.write_bytes(saved + b"changed")
                    elif mode == "add":
                        added.write_text("added")
                    else:
                        self.recipe.write_bytes(original + b" ")
                self.match.side_effect = mutate
                try:
                    with self.assertRaises(ValueError):
                        self.build()
                    self.assertFalse(self.output.exists())
                finally:
                    self.recipe.write_bytes(original)
                    target.write_bytes(saved)
                    added.unlink(missing_ok=True)

    def test_artifact_mismatch_denies_manifest_and_readiness_tampering_denies_verify(self):
        self.match.side_effect = ValueError("artifact mismatch")
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())
        self.match.side_effect = None
        result = self.build()
        result["deployment_ready"] = True
        self.output.write_bytes(MANIFEST.canonical(result))
        with self.assertRaises(ValueError):
            MANIFEST.verify(self.stage, self.output)

    def test_missing_roles_duplicate_json_and_output_overwrite_rejected(self):
        bad = copy.deepcopy(self.selection)
        bad["files"] = [entry for entry in bad["files"] if entry["role"] != "dropin"]
        with self.assertRaises(ValueError):
            MANIFEST.selection(bad)
        with self.assertRaises(ValueError):
            MANIFEST.document(b'{"duplicate":1,"duplicate":2}')
        self.output.write_text("preserve")
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(self.output.read_text(), "preserve")

    def test_actual_component_matchers_on_synthetic_wheels_no_build_or_execution(self):
        runtime = MANIFEST.helper("verify-runtime-release")
        sdk = MANIFEST.helper("build-sdk-release")
        selected = dict(components=self.selection["components"], files=[])
        def write(name, data, role=None):
            path = self.stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if role:
                selected["files"].append(dict(path=name, role=role))
            return path
        dependencies = ["PyNaCl==1.6.2", "cryptography==46.0.3", "sage-agent-sdk==" + sdk.VERSION, "PyYAML==6.0.2"]
        sources = {"pyproject.toml": b"synthetic pyproject bytes"}
        for module in runtime.REQUIRED | {"__init__"}:
            sources[f"src/sage_lantern/{module}.py"] = b"pass\n"
        for name, data in sources.items():
            write("software/runtime/" + name, data)
        wheel = write("runtime.whl", b"", "runtime_wheel")
        manifest = dict(schema="sage.lantern.runtime-release.v1", version=runtime.VERSION,
                        source_sha256={name: hashlib.sha256(data).hexdigest() for name, data in sources.items()})
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, data in sources.items():
                if name.startswith("src/"):
                    archive.writestr(name.removeprefix("src/"), data)
            archive.writestr("sage_lantern/release-manifest.json", json.dumps(manifest))
            for name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD"):
                archive.writestr("sage_lantern_runtime-0.2.0.dist-info/" + name,
                                 "\n".join("Requires-Dist: " + dependency for dependency in dependencies) if name == "METADATA" else "")
        write("sources/sage-sdk/pyproject.toml", b'[build-system]\nrequires = ["setuptools>=68.0", "wheel"]\nbuild-backend = "setuptools.build_meta"\n[project]\nname = "sage-agent-sdk"\nversion = "11.19.10"\n')
        write("sources/sage-sdk/README.md", b"Synthetic SDK\n")
        write("sources/sage-sdk/src/sage_sdk/__init__.py", b'__version__ = "11.19.10"\n')
        client = "class SageClient:\n" + "".join("    def " + name + "(self" + "".join(", " + arg + "=None" for arg in sorted(parameters)) + "):\n        pass\n"
                                                for name, parameters in runtime.SDK_APIS.items())
        write("sources/sage-sdk/src/sage_sdk/client.py", client.encode())
        original = sdk.snapshot(self.stage / "sources/sage-sdk")
        payloads = sdk.staged_payloads(original)
        sdk_manifest = dict(schema="sage.lantern.sdk-release.v1", version=sdk.VERSION,
                            source_sha256=sdk.hashes(original), staged_sha256=sdk.hashes(payloads))
        sdk_wheel = write("sdk.whl", b"", "sdk_wheel")
        with zipfile.ZipFile(sdk_wheel, "w") as archive:
            for name, data in payloads.items():
                if name.startswith("src/"):
                    archive.writestr(name.removeprefix("src/"), data)
            archive.writestr("sage_sdk/release-manifest.json", json.dumps(sdk_manifest))
            for name in ("METADATA", "WHEEL", "top_level.txt", "RECORD"):
                archive.writestr("sage_agent_sdk-" + sdk.VERSION + ".dist-info/" + name,
                                 "Name: sage-agent-sdk\nVersion: " + sdk.VERSION + "\n" if name == "METADATA" else "")
        binary = bytearray(64)
        binary[:7] = b"\x7fELF\x02\x01\x01"
        binary[18:20] = (183).to_bytes(2, "little")
        write("sage-gui", bytes(binary), "sage_binary")
        inventory = dict(source_sha256={"main.go": "a" * 64}, dependency_input_sha256={"dependency.go": "b" * 64}, modules={"sage": {"Version": "fixture"}})
        binary_manifest = dict(inventory, schema="sage.lantern.binary-staging.v1", version=selected["components"]["sage"],
            toolchain_sha256={"go": "c" * 64}, binary_sha256=hashlib.sha256(binary).hexdigest(),
            inventory_sha256=hashlib.sha256(MANIFEST.canonical(inventory)).hexdigest())
        write("sage-manifest.json", json.dumps(binary_manifest).encode(), "sage_manifest")
        for name in ("dist/server/index.js", "package.json", "package-lock.json", "node_modules/example/index.js"):
            write("apps/lantern-fleet-admin/" + name, b"synthetic", "ui")
        REAL_MATCHED(self.stage, selected)
        write("sources/sage-sdk/src/sage_sdk/client.py", b"changed")
        with self.assertRaisesRegex(ValueError, "SDK source manifest mismatch"):
            REAL_MATCHED(self.stage, selected)


if __name__ == "__main__":
    unittest.main()
