"""Review artifacts preserve disabled flags and exact payload identities."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("stage_desktop_loader", ROOT / "scripts/lantern/stage-desktop-loader.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)

class StageDesktopLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="desktop-loader-stage-")
        self.directory = Path(self.temp.name)
        self.build = self.directory / "build"
        self.build.mkdir()
        for name in stage.BUILD:
            (self.build / name).write_bytes(("synthetic staging payload " + name).encode())
        self.bundle = self.directory / "bundle"
        self.manifest = stage.stage(self.build, self.bundle)

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, value):
        (self.bundle / "manifest.json").write_text(json.dumps(value))

    def test_frozen_payloads_and_no_overwrite(self):
        self.assertEqual(stage.verify(self.bundle), len(self.manifest["files"]))
        self.assertTrue(all(self.manifest[flag] is False for flag in stage.FLAGS))
        with self.assertRaises(FileExistsError):
            stage.stage(self.build, self.bundle)

    def test_qualification_flags_are_strict_false(self):
        for flag in stage.FLAGS:
            for value in (True, 0, "false", None):
                with self.subTest(flag=flag, value=value):
                    changed = dict(self.manifest, **{flag: value})
                    self.write_manifest(changed)
                    with self.assertRaises(ValueError):
                        stage.verify(self.bundle)
        self.write_manifest(self.manifest)

    def test_payload_modification_refused(self):
        payload = self.bundle / next(iter(self.manifest["files"]))
        payload.write_bytes(b"altered")
        with self.assertRaises(ValueError):
            stage.verify(self.bundle)

    def test_extra_file_or_directory_refused(self):
        extra = self.bundle / "unexpected"
        extra.write_text("extra")
        with self.assertRaises(ValueError):
            stage.verify(self.bundle)
        extra.unlink(); extra.mkdir()
        with self.assertRaises(ValueError):
            stage.verify(self.bundle)

    def test_missing_expected_payload_cannot_be_laundered(self):
        value = dict(self.manifest)
        value["files"] = dict(value["files"])
        name = next(iter(value["files"]))
        del value["files"][name]
        (self.bundle / name).unlink()
        self.write_manifest(value)
        with self.assertRaises(ValueError):
            stage.verify(self.bundle)

    def test_symlink_payload_refused_even_with_matching_bytes(self):
        name = next(iter(self.manifest["files"]))
        payload = self.bundle / name
        outside = self.directory / "outside"
        shutil.copyfile(payload, outside)
        payload.unlink(); payload.symlink_to(outside)
        with self.assertRaises(ValueError):
            stage.verify(self.bundle)

    def test_duplicate_manifest_keys_refused(self):
        body = json.dumps(self.manifest)
        (self.bundle / "manifest.json").write_text('{"deployable":true,' + body[1:])
        with self.assertRaises(ValueError):
            stage.verify(self.bundle)

    def test_boundary_and_predecessor_refused(self):
        for patch in ({"uid_boundary": True}, {"uid_boundary": 999},
                      {"live_changes": ["installed"]}, {"native_prepare": "enabled"},
                      {"predecessor_manifest_sha256": "0" * 64}):
            with self.subTest(patch=patch):
                self.write_manifest(dict(self.manifest, **patch))
                with self.assertRaises(ValueError):
                    stage.verify(self.bundle)

if __name__ == "__main__":
    unittest.main()
