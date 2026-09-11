from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "smoke-test-lantern-unit.sh"
INSTALLER = SCRIPT.parent / "install-lantern-unit.sh"
RUNTIME_UNIT = SCRIPT.parents[1] / "software/systemd/sage-lantern-runtime.service"


class SmokeTestLanternUnitTests(unittest.TestCase):
    def run_smoke(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), "--root", str(root), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def add_file(root: Path, target: str, *, executable: bool = False) -> None:
        path = root / target.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"#!/bin/sh\n" if executable else b"fixture\n")
        if executable:
            path.chmod(0o755)

    def test_complete_staged_install_passes_without_executing_target_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for target in (
                "/var/lib/sage-lantern/PROVISIONED",
                "/var/lib/sage-lantern/identity/lantern-agent.key",
                "/opt/sage-lantern/models/ggml-base.bin",
                "/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf",
                "/opt/sage-lantern/models/en_US-lessac-medium.onnx",
                "/opt/sage-lantern/models/en_US-lessac-medium.onnx.json",
                "/etc/systemd/system/sage-lantern-llm.service",
                "/etc/systemd/system/sage-lantern-personal-sage.service",
                "/etc/systemd/system/sage-lantern-runtime.service",
            ):
                self.add_file(root, target)
            for target in (
                "/opt/sage-lantern/venv/bin/sage-lantern",
                "/opt/sage-lantern/venv/bin/python",
                "/opt/sage-lantern/bin/whisper-cli",
                "/opt/sage-lantern/bin/sage-gui",
                "/opt/sage-lantern/llama.cpp/llama-server",
            ):
                self.add_file(root, target, executable=True)

            config = root / "etc/sage-lantern/lantern.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(
                """
[runtime]
state_log = "/var/lib/sage-lantern/logs/state.jsonl"
output_dir = "/var/lib/sage-lantern/audio"
[stt]
binary = "/opt/sage-lantern/bin/whisper-cli"
model = "/opt/sage-lantern/models/ggml-base.bin"
[llm]
base_url = "http://127.0.0.1:8081"
model = "fixture"
[tts]
python = "/opt/sage-lantern/venv/bin/python"
model = "/opt/sage-lantern/models/en_US-lessac-medium.onnx"
[sage]
base_url = "http://127.0.0.1:8080"
identity_path = "/var/lib/sage-lantern/identity/lantern-agent.key"
""".strip()
                + "\n",
                encoding="utf-8",
            )

            result = self.run_smoke(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("0 failed", result.stdout)

    def test_empty_stage_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_smoke(Path(tmp))
            self.assertEqual(result.returncode, 1)
            self.assertIn("FAIL", result.stderr)

    def test_staged_root_rejects_execution_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_smoke(Path(tmp), "--require-services-active")
            self.assertEqual(result.returncode, 2)
            self.assertIn("cannot execute against a staged root", result.stderr)

    def test_installer_and_runtime_unit_use_the_same_smoke_command(self) -> None:
        command = "/usr/local/sbin/smoke-test-lantern-unit"
        self.assertIn(command, INSTALLER.read_text(encoding="utf-8"))
        self.assertIn(
            f"ExecStart={command} --local-voice-ready --require-services-active",
            RUNTIME_UNIT.read_text(encoding="utf-8"),
        )
        self.assertIn("--wait-health-seconds=60", RUNTIME_UNIT.read_text())
        self.assertIn("TimeoutStartSec=90", RUNTIME_UNIT.read_text())

    def test_health_wait_options_reject_staging_and_invalid_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            for arguments in (("--wait-health-seconds", "60"), ("--wait-health-seconds=60",)):
                result = self.run_smoke(Path(temporary), *arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn("cannot execute against a staged root", result.stderr)
            for value in ("-1", "61", "1.5", "bad", "060"):
                self.assertEqual(self.run_smoke(Path(temporary), "--wait-health-seconds", value).returncode, 2)


class HealthWaitTests(unittest.TestCase):
    def report(self, *, llama=False, sage=False, files=True):
        checks = {
            "llama_server": {"ok": llama, "detail": "HTTPError" if not llama else "ready"},
            "sage_node": {"ok": sage, "detail": "URLError" if not sage else "ready"},
            "whisper_binary": {"ok": files, "detail": "fixture"},
        }
        return subprocess.CompletedProcess([], 0 if llama and sage and files else 1, json.dumps(checks), "")

    def run_wait(self, reports, *, hangs=False):
        source = SCRIPT.read_text().split("<<'HEALTH_PY'\n", 1)[1].split("\nHEALTH_PY", 1)[0]
        clock = [0.0]
        attempts = []
        output, errors = io.StringIO(), io.StringIO()
        pending = iter(reports)
        latest = [None]

        def run(command, **kwargs):
            self.assertEqual(command, ["/mock/runtime", "health", "--config", "/mock/config"])
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], min(5, 60 - clock[0]))
            attempts.append(clock[0])
            if hangs:
                clock[0] += kwargs["timeout"]
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            clock[0] += min(0.1, kwargs["timeout"])
            latest[0] = next(pending, latest[0])
            return latest[0]

        with patch.object(sys, "argv", ["-", "/mock/runtime", "/mock/config", "60"]), patch.object(subprocess, "run", side_effect=run), patch.object(time, "monotonic", side_effect=lambda: clock[0]), patch.object(time, "sleep", side_effect=lambda duration: clock.__setitem__(0, clock[0] + duration)), redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as exit_result:
                exec(compile(source, str(SCRIPT), "exec"), {})
        self.assertLessEqual(clock[0], 60)
        return exit_result.exception.code, attempts, output.getvalue(), errors.getvalue()

    def test_boot_health_errors_retry_until_both_endpoints_pass(self):
        status, attempts, _, errors = self.run_wait([
            self.report(), self.report(llama=True), self.report(llama=True, sage=True),
        ])
        self.assertEqual(status, 0, errors)
        self.assertEqual(len(attempts), 3)

    def test_unhealthy_endpoints_hit_deadline_and_remain_nonzero(self):
        status, attempts, output, errors = self.run_wait([self.report()])
        self.assertEqual(status, 1)
        self.assertGreater(len(attempts), 1)
        self.assertIn("HTTPError", output)
        self.assertIn("URLError", output)
        self.assertIn("deadline exceeded", errors)

    def test_hung_health_attempts_are_killed_within_total_deadline(self):
        status, attempts, _, errors = self.run_wait([], hangs=True)
        self.assertEqual(status, 1)
        self.assertEqual(len(attempts), 10)
        self.assertIn("timed out", errors)

    def test_file_failure_is_not_retried_even_if_endpoints_are_ready(self):
        status, attempts, _, errors = self.run_wait([self.report(llama=True, sage=True, files=False)])
        self.assertEqual(status, 1)
        self.assertEqual(len(attempts), 1)
        self.assertIn("not retrying", errors)

    def test_invalid_health_report_fails_closed_without_retry(self):
        status, attempts, _, errors = self.run_wait([subprocess.CompletedProcess([], 1, "not JSON", "")])
        self.assertEqual(status, 1)
        self.assertEqual(len(attempts), 1)
        self.assertIn("invalid report", errors)

    def test_existing_static_failures_bypass_retry_branch(self):
        self.assertIn("wait_health_seconds > 0 && failures == 0", SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
