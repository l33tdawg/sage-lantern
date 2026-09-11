import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/assemble-protected-config.py"
SPEC = importlib.util.spec_from_file_location("protected_assembler", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.staging = self.root / "stage"
        self.staging.mkdir(mode=0o700)
        voice = {"algorithm": "ed25519", "public_key_hex": "12" * 32}
        fingerprint = "sha256:" + hashlib.sha256(json.dumps(voice, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.identity = {"schema": "sage.lantern.personal-public-identity.v1", "personal_chain_id": "local-private-913", "personal_genesis_fingerprint": "sha256:" + "a" * 64, "voice_identity": voice, "voice_identity_fingerprint": fingerprint}
        self.inventory = "\n".join(["INVENTORY_SCHEMA=sage-lantern-identity-v1", "UNIT_ID=unit-a", "PERSONAL_CHAIN_ID=local-private-913", "PERSONAL_GENESIS_FINGERPRINT=sha256:" + "a" * 64, "VOICE_IDENTITY_FINGERPRINT=" + fingerprint]) + "\n"
        self.args = argparse.Namespace(unit_id="unit-a", operator_approved=True, chain_id="local-private-913", staging_root=str(self.staging), actor_bytes=1048576, node_bytes=2097152, actor_objects=10, node_objects=20, min_free_bytes=4194304, sage_manifest=None, sage_binary=None, runtime_wheel=None, sdk_wheel=None)
        for key, content in {
            "sage_yaml": "# preserve this\nnetwork:\n  domain: personal\nunknown: [one, two]\n",
            "runtime_toml": '# preserve this too\n[sage]\nbase_url = "http://127.0.0.1:8080"\nidentity_path = "/never/read/operator.key"\ndomain = "personal"\nunknown = [1, 2]\n[extra]\nsetting = "keep"\n',
            "inventory": self.inventory,
            "public_identity": json.dumps(self.identity),
        }.items():
            path = self.root / key
            path.write_text(content)
            setattr(self.args, key, str(path))

    def reject(self):
        with self.assertRaises((ValueError, OSError, yaml.YAMLError, TypeError)):
            MODULE.assemble(self.args)
        self.assertFalse((self.staging / self.args.unit_id).exists())

    def test_disabled_preserves_config_permissions_and_inputs(self):
        originals = {key: Path(getattr(self.args, key)).read_bytes() for key in ("sage_yaml", "runtime_toml", "inventory", "public_identity")}
        plan = MODULE.assemble(self.args)
        output = self.staging / "unit-a"
        self.assertFalse(plan["provisioned"])
        self.assertFalse(plan["applied"])
        self.assertFalse(plan["chain_binding_verified"])
        self.assertTrue(plan["activation_required"])
        self.assertFalse(any(plan["artifact_checks"].values()))
        sage = yaml.safe_load((output / "sage.candidate.yaml").read_text())
        self.assertFalse(sage.pop("private_media")["enabled"])
        self.assertEqual(sage, yaml.safe_load(originals["sage_yaml"]))
        runtime = tomllib.loads((output / "lantern.candidate.toml").read_text())
        self.assertFalse(runtime["sage"].pop("protected_storage"))
        self.assertEqual(runtime["sage"].pop("chain_id"), self.args.chain_id)
        self.assertEqual(runtime, tomllib.loads(originals["runtime_toml"].decode()))
        for key, value in originals.items():
            self.assertEqual(Path(getattr(self.args, key)).read_bytes(), value)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        for path in output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(set(path.name for path in output.iterdir()), {"sage.candidate.yaml", "lantern.candidate.toml", "plan.json"})
        with self.assertRaises(FileExistsError):
            MODULE.assemble(self.args)

    def test_cli(self):
        command = [sys.executable, str(SCRIPT), "--operator-approved"]
        for key, value in vars(self.args).items():
            if key != "operator_approved" and value is not None:
                command.extend(["--" + key.replace("_", "-"), str(value)])
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["mode"], "disabled-candidate")

    def test_unit_b_cannot_use_unit_a(self):
        self.args.unit_id = "unit-b"
        self.reject()

    def test_absent_approval(self):
        self.args.operator_approved = False
        self.reject()

    def test_chain_rejections(self):
        for chain in ("", "unit-a", "YOUR-chain", "example-chain", "personal", "different", "x" * 129):
            with self.subTest(chain=chain):
                self.args.chain_id = chain
                self.reject()

    def test_forged_public_fingerprint(self):
        self.identity["voice_identity"]["public_key_hex"] = "34" * 32
        Path(self.args.public_identity).write_text(json.dumps(self.identity))
        self.reject()

    def test_missing_binding(self):
        Path(self.args.inventory).write_text("UNIT_ID=unit-a\n")
        self.reject()

    def test_duplicate_inventory_and_identity(self):
        Path(self.args.inventory).write_text(self.inventory + "UNIT_ID=unit-a\n")
        self.reject()
        Path(self.args.inventory).write_text(self.inventory)
        Path(self.args.public_identity).write_text('{"schema":"one","schema":"two"}')
        self.reject()

    def test_yaml_rejections(self):
        for raw in ("foo: 1\nfoo: 2\n", "private_media:\n  enabled: true\n", "private_media:\n  actor_bytes: 9\n", "nested:\n  key: 1\n  key: 2\n", "foo: &same [1]\nbar: *same\n", "password: sensitive\n"):
            with self.subTest(raw=raw):
                Path(self.args.sage_yaml).write_text(raw)
                self.reject()

    def test_toml_rejections(self):
        original = Path(self.args.runtime_toml).read_text()
        for raw in (original + "\n[sage]\n", original.replace("[sage]", "[sage]\nprotected_storage=true"), original.replace("[sage]", '[sage]\nchain_id="other"'), original.replace("127.0.0.1", "remote.example"), original.replace('domain = "personal"', 'domain = ""'), original.replace("[sage]", '[sage]\ntoken="sensitive"')):
            with self.subTest(raw=raw):
                Path(self.args.runtime_toml).write_text(raw)
                self.reject()

    def test_existing_disabled_media_merge(self):
        Path(self.args.sage_yaml).write_text("before: 1\nprivate_media:\n  enabled: false\nafter: 2\n")
        MODULE.assemble(self.args)
        document = yaml.safe_load((self.staging / "unit-a/sage.candidate.yaml").read_text())
        self.assertEqual(document["after"], 2)
        self.assertEqual(document["private_media"]["node_objects"], 20)

    def test_invalid_quotas(self):
        for key, value in (("actor_bytes", 0), ("actor_objects", True), ("node_bytes", 1), ("node_objects", 1), ("min_free_bytes", 2**63-1)):
            with self.subTest(key=key):
                prior = getattr(self.args, key)
                setattr(self.args, key, value)
                self.reject()
                setattr(self.args, key, prior)

    def test_linked_inputs(self):
        original = Path(self.args.sage_yaml)
        link = self.root / "link"
        link.symlink_to(original)
        self.args.sage_yaml = str(link)
        self.reject()
        link.unlink()
        os.link(original, link)
        self.reject()

    def test_private_input_path_rejected(self):
        self.args.sage_yaml = str(self.root / "operator.key")
        self.reject()

    def test_staging_permissions(self):
        self.staging.chmod(0o755)
        self.reject()

    def test_input_change_rejected(self):
        def change(_args):
            Path(self.args.inventory).write_text("changed\n")
            return {}
        with patch.object(MODULE, "capabilities", side_effect=change):
            self.reject()

    def test_unsupported_manifest(self):
        manifest = self.root / "manifest.json"
        self.args.sage_manifest = str(manifest)
        self.args.sage_binary = str(self.root / "binary")
        for value in ({"verified": True}, {"schema": "sage.lantern.binary-staging.v1", "source_sha256": {}}):
            manifest.write_text(json.dumps(value))
            self.reject()

    def test_runtime_verifier_reused(self):
        wheel = self.root / "runtime.whl"
        wheel.write_bytes(b"test")
        self.args.runtime_wheel = str(wheel)
        with patch.object(MODULE, "helper") as helper:
            plan = MODULE.assemble(self.args)
        helper.return_value.verify_wheel.assert_called_once_with(wheel)
        self.assertTrue(plan["artifact_checks"]["runtime_wheel_checked"])
        self.assertFalse(plan["backend_verified"])

    def test_binary_hash_and_architecture_checks_not_attestation(self):
        binary = self.root / "binary"
        header = bytearray(64)
        header[:7] = b"\x7fELF\x02\x01\x01"
        header[18:20] = (183).to_bytes(2, "little")
        binary.write_bytes(header)
        manifest = self.root / "manifest.json"
        document = {"schema": "sage.lantern.binary-staging.v1", "binary_sha256": hashlib.sha256(header).hexdigest(), "source_sha256": {name: "a" * 64 for name in ("cmd/sage-gui/private_media_config.go", "api/rest/private_media_handler.go", "api/rest/workflow_journal_handler.go", "internal/store/workflow_journal.go")}}
        manifest.write_text(json.dumps(document))
        self.args.sage_manifest, self.args.sage_binary = str(manifest), str(binary)
        with patch.object(sys, "dont_write_bytecode", True):
            plan = MODULE.assemble(self.args)
        self.assertTrue(plan["artifact_checks"]["sage_binary_inputs_checked"])
        self.assertFalse(plan["backend_verified"])
        self.assertFalse(plan["chain_binding_verified"])

    def test_bad_binary_rejected(self):
        binary = self.root / "binary"
        binary.write_bytes(b"invalid")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema": "sage.lantern.binary-staging.v1", "binary_sha256": "b" * 64, "source_sha256": {name: "a" * 64 for name in ("cmd/sage-gui/private_media_config.go", "api/rest/private_media_handler.go", "api/rest/workflow_journal_handler.go", "internal/store/workflow_journal.go")}}))
        self.args.sage_manifest, self.args.sage_binary = str(manifest), str(binary)
        with patch.object(sys, "dont_write_bytecode", True):
            self.reject()

    def test_unit_b_explicit_binding(self):
        self.args.unit_id = "unit-b"
        self.args.chain_id = "local-private-728"
        Path(self.args.inventory).write_text(self.inventory.replace("unit-a", "unit-b").replace("local-private-913", self.args.chain_id))
        self.identity["personal_chain_id"] = self.args.chain_id
        Path(self.args.public_identity).write_text(json.dumps(self.identity))
        plan = MODULE.assemble(self.args)
        self.assertEqual(plan["unit_id"], "unit-b")
        self.assertEqual(plan["chain_id"], "local-private-728")

    def test_cli_does_not_echo_parser_input(self):
        Path(self.args.sage_yaml).write_text("password: [DO-NOT-ECHO\n")
        command = [sys.executable, str(SCRIPT), "--operator-approved"]
        for key, value in vars(self.args).items():
            if key != "operator_approved" and value is not None:
                command.extend(["--" + key.replace("_", "-"), str(value)])
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("DO-NOT-ECHO", result.stdout + result.stderr)
        self.assertFalse((self.staging / "unit-a").exists())


if __name__ == "__main__":
    unittest.main()
