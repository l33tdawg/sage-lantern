import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("sage_builder", Path(__file__).resolve().parents[1] / "build-sage-release.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class SageReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()

    def write(self, name, data=b"synthetic"):
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def test_selected_untracked_go_embed_and_module_inputs(self):
        for name in ("go.mod", "go.sum", "cmd/sage-gui/main.go", "cmd/sage-gui/new_untracked.go",
                     "cmd/sage-gui/assets/index.html", ".sage/identity.key", "cmd/sage-gui/ignored_test.go"):
            self.write(name)
        packages = [{"ImportPath": "fixture/cmd/sage-gui", "Dir": str(self.source / "cmd/sage-gui"),
                     "GoFiles": ["main.go", "new_untracked.go"], "EmbedFiles": ["assets/index.html"]}]
        with patch.object(BUILDER, "run", return_value=json.dumps(packages[0])):
            snapshot = BUILDER.snapshot(self.source, "go", {})
        self.assertEqual(set(snapshot[0]), {"go.mod", "go.sum", "cmd/sage-gui/main.go",
                                          "cmd/sage-gui/new_untracked.go", "cmd/sage-gui/assets/index.html"})
        before = BUILDER.source_manifest(snapshot)
        snapshot[0]["cmd/sage-gui/new_untracked.go"] = b"changed"
        self.assertNotEqual(before, BUILDER.source_manifest(snapshot))

    def test_private_paths_and_traversal_rejected(self):
        for name in ("../secret", "/etc/file", ".sage/config.json", "assets/private.key", ".git/config", "state.db"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                BUILDER.relative_input(name)

    def test_linked_inputs_rejected(self):
        target = self.write("target.go")
        for hardlink in (False, True):
            linked = self.source / "linked.go"
            if hardlink:
                os.link(target, linked)
            else:
                linked.symlink_to(target)
            with self.assertRaises(ValueError):
                BUILDER.source_bytes(linked)
            linked.unlink()

    def test_environment_removes_ambient_build_settings(self):
        with patch.dict(os.environ, {"GOFLAGS": "-overlay=secret", "GOPROXY": "https://invalid",
                                     "GOTOOLCHAIN": "auto", "SAGE_HOME": "/private/state"}):
            env = BUILDER.environment()
        self.assertEqual(env["GOPROXY"], "off")
        self.assertEqual(env["GOTOOLCHAIN"], "local")
        self.assertEqual(env["GOFLAGS"], "")
        self.assertEqual(env["CGO_ENABLED"], "0")
        self.assertNotIn("SAGE_HOME", env)

    def test_elf_requires_arm64_little_endian(self):
        header = bytearray(64)
        header[:7] = b"\x7fELF\x02\x01\x01"
        header[18:20] = (183).to_bytes(2, "little")
        binary = self.write("binary", header)
        BUILDER.verify_elf(binary)
        for offset, value in ((0, 0), (4, 1), (5, 2), (18, 62)):
            modified = bytearray(header)
            modified[offset] = value
            binary.write_bytes(modified)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                BUILDER.verify_elf(binary)

    def test_output_and_build_id_validation_before_build(self):
        for output, build_id in ((self.source / "out", "valid"), (self.root, "valid"),
                                 (self.root / "new", "bad -X injected")):
            with self.subTest(build_id=build_id), self.assertRaises(ValueError):
                BUILDER.build(self.source, output, build_id, 1788652800)

    def test_multiple_package_json_and_invalid_graph(self):
        self.assertEqual(list(BUILDER.json_stream(' {"one":1}\n {"two":2}\n')), [{"one": 1}, {"two": 2}])
        for payload in ("", json.dumps({"Error": {"Err": "missing offline module"}})):
            self.write("go.mod")
            self.write("go.sum")
            with patch.object(BUILDER, "run", return_value=payload), self.assertRaises(ValueError):
                BUILDER.snapshot(self.source, "go", {})

    def test_source_drift_never_publishes_output(self):
        tool_directory = self.root / "tools"
        tool_directory.mkdir()
        for name in ("go", "compile", "asm", "link"):
            (tool_directory / name).write_bytes(b"synthetic tool")
        original = ({"go.mod": b"module fixture", "cmd/sage-gui/main.go": b"package main"}, {}, {})
        changed = ({**original[0], "cmd/sage-gui/new.go": b"package main"}, {}, {})

        def fake_run(command, cwd, env, timeout=120):
            if command[1] == "env":
                return json.dumps({"GOROOT": str(tool_directory), "GOTOOLDIR": str(tool_directory), "GOVERSION": "synthetic", "GOTELEMETRY": "off"})
            if command[1] == "build":
                binary = cwd.parent / "artifact/sage-gui"
                header = bytearray(64)
                header[:7] = b"\x7fELF\x02\x01\x01"
                header[18:20] = (183).to_bytes(2, "little")
                inventory_hash = BUILDER.digest(BUILDER.encoded(BUILDER.source_manifest(original)))
                binary.write_bytes(header + b"lantern-local.test source-" + inventory_hash[:16].encode() + b" 2026-09-06T00:00:00Z")
                return ""
            return "GOOS=linux GOARCH=arm64 CGO_ENABLED=0 lantern-local.test ./cmd/sage-gui"

        output = self.root / "output"
        with patch.object(BUILDER.shutil, "which", return_value=str(tool_directory / "go")), \
                patch.object(BUILDER, "run", side_effect=fake_run), \
                patch.object(BUILDER, "snapshot", side_effect=[original, original, changed]), \
                self.assertRaisesRegex(ValueError, "changed during build"):
            BUILDER.build(self.source, output, "test", 1788652800)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
