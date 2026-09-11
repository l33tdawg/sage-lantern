import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "workflow_interop", Path(__file__).resolve().parents[1] / "lantern/run-workflow-interop.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class WorkflowInteropHarnessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name).resolve()
        fixture = self.source / "api/rest/workflow_journal_handler_test.go"
        fixture.parent.mkdir(parents=True)
        fixture.write_text("synthetic fixture")
        (fixture.parent / "private_media_handler_test.go").write_text("synthetic media fixture")
        self.arguments = ["run-workflow-interop.py", "--sage-source", str(self.source)]

    def test_temporary_overlay_and_isolated_environment(self):
        overlays = []

        def run(command, **options):
            self.assertEqual(command[:3], ["go", "test", "-overlay"])
            self.assertIn("^TestLanternWorkflowInterop$", command)
            self.assertTrue(options["check"])
            self.assertEqual(options["timeout"], 240)
            self.assertEqual(options["cwd"], self.source)
            environment = options["env"]
            self.assertNotIn("SAGE_KEY", environment)
            self.assertNotIn("LANTERN_ENDPOINT", environment)
            for name in ("GOPROXY", "GOSUMDB", "GOENV"):
                self.assertEqual(environment[name], "off")
            self.assertEqual(environment["GOFLAGS"], "")
            self.assertEqual(environment["GOTOOLCHAIN"], "local")
            self.assertEqual(environment["LANTERN_INTEROP_PYTHON"], HARNESS.sys.executable)
            overlay = Path(command[3])
            overlays.append(overlay)
            replacements = json.loads(overlay.read_text())["Replace"]
            virtual = self.source / "api/rest/lantern_workflow_interop_test.go"
            self.assertEqual(list(replacements), [str(virtual)])
            self.assertFalse(virtual.exists())
            self.assertTrue(Path(replacements[str(virtual)]).is_file())
            return subprocess.CompletedProcess(command, 0)

        with patch.object(HARNESS.sys, "argv", self.arguments), patch.object(HARNESS.subprocess, "run", side_effect=run), \
                patch.dict(HARNESS.os.environ, {"SAGE_KEY": "synthetic", "LANTERN_ENDPOINT": "synthetic",
                                               "GOFLAGS": "-race", "GOTOOLCHAIN": "auto"}):
            HARNESS.main()
        self.assertEqual(len(overlays), 1)
        self.assertFalse(overlays[0].exists())

    def test_child_failure_propagates(self):
        failure = subprocess.CalledProcessError(1, ["go", "test"])
        with patch.object(HARNESS.sys, "argv", self.arguments), \
                patch.object(HARNESS.subprocess, "run", side_effect=failure), \
                self.assertRaises(subprocess.CalledProcessError):
            HARNESS.main()

    def test_missing_matching_fixture_fails_before_execution(self):
        (self.source / "api/rest/workflow_journal_handler_test.go").unlink()
        with patch.object(HARNESS.sys, "argv", self.arguments), \
                patch.object(HARNESS.subprocess, "run") as run, self.assertRaises(ValueError):
            HARNESS.main()
        run.assert_not_called()
