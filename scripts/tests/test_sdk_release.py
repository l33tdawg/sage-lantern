import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sdk_builder", ROOT / "scripts/build-sdk-release.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class SDKReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "upstream"
        self.package = self.source / "src/sage_sdk"
        self.package.mkdir(parents=True)
        (self.source / "pyproject.toml").write_text('''[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"
[project]
name = "sage-agent-sdk"
version = "11.19.10"
readme = "README.md"
[tool.setuptools.packages.find]
where = ["src"]
''')
        (self.source / "README.md").write_text("Synthetic SDK fixture, not production APIs.\n")
        (self.package / "__init__.py").write_text('__version__ = "11.19.10"\n')
        (self.package / "client.py").write_text("class SageClient:\n    pass\n")

    def test_staging_versions_and_hashes_do_not_modify_upstream(self):
        original = BUILDER.snapshot(self.source)
        staged = BUILDER.staged_payloads(original)
        self.assertEqual(original, BUILDER.snapshot(self.source))
        self.assertIn(BUILDER.VERSION.encode(), staged["pyproject.toml"])
        self.assertIn(BUILDER.VERSION.encode(), staged["src/sage_sdk/__init__.py"])
        self.assertEqual(staged["src/sage_sdk/client.py"], original["src/sage_sdk/client.py"])
        self.assertNotEqual(BUILDER.hashes(original), BUILDER.hashes(staged))

    def test_unsupported_version_and_backend_fail_closed(self):
        original = BUILDER.snapshot(self.source)
        for old, new in ((b'11.19.10', b'11.20.0'), (b'setuptools.build_meta', b'custom.backend')):
            with self.subTest(new=new):
                modified = dict(original)
                modified["pyproject.toml"] = modified["pyproject.toml"].replace(old, new)
                with self.assertRaises(ValueError):
                    BUILDER.staged_payloads(modified)

    def test_snapshot_excludes_non_source_and_rejects_links(self):
        (self.package / "identity.key").write_text("synthetic only")
        (self.package / "__pycache__").mkdir()
        (self.package / "__pycache__/cache.pyc").write_bytes(b"fake")
        self.assertEqual(len(BUILDER.snapshot(self.source)), 4)
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind):
                linked = self.package / "linked.py"
                target = self.source / "README.md"
                if link_kind == "symlink":
                    linked.symlink_to(target)
                else:
                    os.link(target, linked)
                try:
                    with self.assertRaises(ValueError):
                        BUILDER.snapshot(self.source)
                finally:
                    linked.unlink()

    def test_output_must_be_new_and_outside_source(self):
        for output in (self.source / "dist", self.root):
            with self.subTest(output=output), self.assertRaises(ValueError):
                BUILDER.build(self.source, output)

    def test_build_rejects_source_mutations(self):
        for change in ("add", "remove", "modify"):
            original = (self.package / "client.py").read_bytes()
            def mutate(*args, **kwargs):
                if change == "add":
                    (self.package / "new.py").write_text("pass\n")
                elif change == "remove":
                    (self.package / "client.py").unlink()
                else:
                    (self.package / "client.py").write_text("pass\n")
            try:
                with self.subTest(change=change), \
                        patch.object(BUILDER.COMMON.importlib.metadata, "version", side_effect=BUILDER.COMMON.TOOLS.__getitem__), \
                        patch.object(BUILDER.subprocess, "run", side_effect=mutate), \
                        self.assertRaisesRegex(ValueError, "source changed"):
                    BUILDER.build(self.source, self.root / "output")
            finally:
                (self.package / "client.py").write_bytes(original)
                (self.package / "new.py").unlink(missing_ok=True)

    def test_real_fixture_build_is_reproducible_and_payload_bounded(self):
        original = BUILDER.snapshot(self.source)
        first = BUILDER.build(self.source, self.root / "first")
        second = BUILDER.build(self.source, self.root / "second")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(original, BUILDER.snapshot(self.source))
        with zipfile.ZipFile(first) as archive:
            encoded = archive.read("sage_sdk/release-manifest.json")
            manifest = json.loads(encoded)
            self.assertEqual(manifest["source_sha256"], BUILDER.hashes(original))
            self.assertEqual(manifest["staged_sha256"], BUILDER.hashes(BUILDER.staged_payloads(original)))
        for extra in ("evil.pth", "other_package/__init__.py", "sage_sdk/key.pem", "sage_sdk/__pycache__/a.pyc"):
            with self.subTest(extra=extra):
                bad = self.root / "bad.whl"
                with zipfile.ZipFile(first) as source, zipfile.ZipFile(bad, "w") as target:
                    for name in source.namelist():
                        target.writestr(name, source.read(name))
                    target.writestr(extra, b"synthetic")
                with self.assertRaisesRegex(ValueError, "unexpected SDK wheel payload"):
                    BUILDER.verify_payload(bad, BUILDER.staged_payloads(original), encoded)


if __name__ == "__main__":
    unittest.main()
