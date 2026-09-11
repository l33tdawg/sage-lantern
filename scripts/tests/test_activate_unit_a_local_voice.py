import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/activate-unit-a-local-voice.sh"
SERVICES = [
    "sage-lantern-voice-loop.service",
    "sage-lantern-runtime.service",
    "sage-lantern-personal-sage.service",
    "sage-lantern-llm.service",
]
EVIDENCE = (
    "UNIT_ID=unit-a\nIDENTITY_EVIDENCE_SHA256=identity-hash\n"
    "RECOVERY_EVIDENCE_SHA256=recovery-hash\nMODE=speech-to-screen\n"
)
MOCKS = r'''
record() { printf '%s\n' "$*" >> "$TEST_LOG"; }
uname() { echo aarch64; }
chown() { :; }
sleep() { :; }
python3() {
  if [[ $# == 4 ]]; then
    record unit-config-check
    command "$TEST_PYTHON" "$@"
  elif [[ $2 == unit-a ]]; then
    record metadata-check
    [[ ${FAIL_COMMAND:-} != metadata-check ]] || return 1
    command "$TEST_PYTHON" "$@"
  else
    record config-check
    [[ ${FAIL_COMMAND:-} != config-check ]]
  fi
}
sha256sum() {
  record "sha256sum $*"
  [[ ${FAIL_COMMAND:-} != evidence-check ]] || return 1
  if [[ $1 != --check ]]; then printf 'identity-hash  %s\n' "$1"; fi
}
systemctl() {
  record "systemctl $*"
  [[ "systemctl $*" != "${FAIL_COMMAND:-}" ]] || return 1
  local action=$1 service
  shift
  case "$action" in
    restart)
      for service in "$@"; do
        rm -f "$TEST_SERVICES/$service"
        if [[ -e "$TEST_MARKER" ]]; then
          printf active > "$TEST_SERVICES/$service"
        else
          record "condition-skipped $service"
        fi
      done
      ;;
    stop)
      for service in "$@"; do rm -f "$TEST_SERVICES/$service"; done
      ;;
    is-active)
      shift
      for service in "$@"; do
        [[ -f "$TEST_SERVICES/$service" ]] || return 3
      done
      ;;
  esac
}
curl() {
  record "curl $*"
  [[ -f "$TEST_SERVICES/sage-lantern-llm.service" ]] || return 7
  [[ -f "$TEST_SERVICES/sage-lantern-personal-sage.service" ]] || return 7
  [[ ${FAIL_COMMAND:-} != health ]]
}
timeout() {
  record "timeout $*"
  [[ "$1 $2 $3" == '--signal=TERM --kill-after=5 90' ]] || return 93
  [[ ${FAIL_COMMAND:-} != helper-timeout ]] || return 124
  [[ ${FAIL_COMMAND:-} != helper-killed ]] || return 137
  shift 3
  "$@"
}
runuser() {
  record "runuser $*"
  [[ -e "$TEST_MARKER" ]] || return 92
  [[ ! -e "$TEST_SERVICES/sage-lantern-runtime.service" ]] || return 94
  [[ ! -e "$TEST_SERVICES/sage-lantern-voice-loop.service" ]] || return 95
  [[ ${FAIL_COMMAND:-} != helper ]]
}
'''


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.log = self.root / "commands.log"
        self.state = self.root / "var/lib/sage-lantern"
        self.marker = self.state / "LOCAL_VOICE_READY"
        self.service_states = self.root / "services"
        self.service_states.mkdir()
        (self.service_states / "sage-lantern-ptt.service").write_text("active")
        self.evidence = self.root / "home/radxa/sage-commons/evidence/unit-a-provisioning"
        paths = [
            "etc/sage-lantern/unit.env",
            "etc/sage-lantern/lantern.toml",
            "var/lib/sage-lantern/identity/lantern-agent.key",
            "var/lib/sage-lantern/PTT_VERIFIED",
            "opt/sage-lantern/venv/bin/sage-lantern",
            "opt/sage-lantern/venv/bin/python",
            "opt/sage-lantern/bin/whisper-cli",
            "opt/sage-lantern/llama.cpp/llama-server",
            "usr/local/libexec/sage-lantern-verify-local-voice",
            "dev/snd/by-id/usb-SAGE_Commons_SAGE_Lantern_Audio_SAGE-LANTERN-A-AUDIO-00",
            "home/radxa/sage-commons/evidence/unit-a-provisioning/personal-public-identity.json",
            "home/radxa/sage-commons/evidence/unit-a-provisioning/recovery-test/RECOVERY_EVIDENCE_SHA256",
            "home/radxa/sage-commons/evidence/unit-a-provisioning/recovery-test/SHA256SUMS",
        ]
        for relative in paths:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("recovery-hash\n")
        (self.root / "etc/sage-lantern/unit.env").write_text(
            "UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n"
        )
        self.commons = self.state / "PROVISIONED"
        self.commons.write_text("independent evidence\n")
        source = SCRIPT.read_text().replace("${EUID}", "0")
        for prefix in ("/var/lib", "/home/radxa", "/etc/sage-lantern", "/opt/sage-lantern", "/usr/local/libexec", "/dev/snd"):
            source = source.replace(prefix, f"{self.root}{prefix}")
        self.script = self.root / "activation.sh"
        self.script.write_text(MOCKS + source)

    def activate(self, failure="", arguments=()):
        self.log.write_text("")
        commons_before = self.commons.read_bytes() if self.commons.exists() else None
        result = subprocess.run(
            ["bash", str(self.script), *arguments], capture_output=True, text=True,
            env={**os.environ, "TEST_LOG": str(self.log), "TEST_MARKER": str(self.marker), "TEST_SERVICES": str(self.service_states), "FAIL_COMMAND": failure, "TEST_PYTHON": sys.executable},
            timeout=10,
        )
        commons_after = self.commons.read_bytes() if self.commons.exists() else None
        self.assertEqual(commons_after, commons_before)
        self.assertEqual((self.evidence / "personal-public-identity.json").read_text(), "recovery-hash\n")
        self.assertFalse(list(self.state.glob(".LOCAL_VOICE_READY.*")))
        return result, self.log.read_text().splitlines()

    def assert_rollback(self, failure):
        self.marker.write_text(EVIDENCE)
        result, commands = self.activate(failure)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.marker.exists())
        for service in SERVICES:
            self.assertIn(f"systemctl stop {service}", commands)
            self.assertIn(f"systemctl disable {service}", commands)
        return commands

    def test_success_orders_verification_before_activation_and_is_idempotent(self):
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                result, commands = self.activate()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.marker.read_text(), EVIDENCE)
                self.assertIn("not full acceptance", result.stdout)
                self.assertFalse(any(command.startswith("condition-skipped ") for command in commands))
                self.assertEqual(commands[:2], ["metadata-check", "systemctl stop sage-lantern-voice-loop.service sage-lantern-runtime.service"])
                self.assertLess(commands.index("config-check"), commands.index("systemctl daemon-reload"))
                helper = next(command for command in commands if command.startswith("runuser "))
                self.assertEqual(helper, f"runuser -u sage-lantern -G audio -- {self.root}/opt/sage-lantern/venv/bin/python {self.root}/usr/local/libexec/sage-lantern-verify-local-voice --config {self.root}/etc/sage-lantern/lantern.toml")
                helper_index = commands.index(helper)
                self.assertEqual(commands[helper_index - 1], f"timeout --signal=TERM --kill-after=5 90 {helper}")
                health_indices = [index for index, command in enumerate(commands) if command.startswith("curl ")]
                self.assertEqual(len(health_indices), 2)
                self.assertLess(max(health_indices), helper_index)
                self.assertLess(helper_index, commands.index("systemctl restart sage-lantern-runtime.service"))
                self.assertLess(commands.index("systemctl is-active --quiet sage-lantern-runtime.service"), commands.index("systemctl restart sage-lantern-voice-loop.service"))
                for service in SERVICES + ["sage-lantern-ptt.service"]:
                    self.assertIn(f"systemctl is-active --quiet {service}", commands)

    def test_readiness_and_service_failures_roll_back(self):
        failures = [
            "helper", "helper-timeout", "helper-killed", "health", "config-check", "evidence-check",
            "systemctl stop sage-lantern-voice-loop.service sage-lantern-runtime.service",
            "systemctl daemon-reload",
            "systemctl restart sage-lantern-llm.service sage-lantern-personal-sage.service",
            "systemctl restart sage-lantern-runtime.service",
            "systemctl restart sage-lantern-voice-loop.service",
            "systemctl enable " + " ".join(SERVICES),
        ] + [f"systemctl is-active --quiet {service}" for service in SERVICES + ["sage-lantern-ptt.service"]]
        for failure in failures:
            with self.subTest(failure=failure):
                commands = self.assert_rollback(failure)
                if failure in {"helper", "helper-timeout", "helper-killed", "health", "config-check", "evidence-check"}:
                    self.assertNotIn("systemctl restart sage-lantern-voice-loop.service", commands)

    def assert_preflight_preserves_running_unit(self, *, arguments=(), failure=""):
        self.marker.write_text(EVIDENCE)
        for service in SERVICES:
            (self.service_states / service).write_text("active")
        before = {path.name: path.read_bytes() for path in self.service_states.iterdir()}
        result, commands = self.activate(failure, arguments)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.marker.read_text(), EVIDENCE)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.service_states.iterdir()})
        self.assertFalse(any(command.startswith("systemctl ") for command in commands))
        self.assertNotIn("rolled back", result.stderr)
        return result, commands

    def test_metadata_preflight_failure_does_not_roll_back(self):
        _, commands = self.assert_preflight_preserves_running_unit(failure="metadata-check")
        self.assertEqual(commands, ["metadata-check"])

    def test_actual_wrong_unit_metadata_does_not_roll_back(self):
        (self.root / "etc/sage-lantern/unit.env").write_text(
            "UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n"
        )
        result, commands = self.assert_preflight_preserves_running_unit()
        self.assertIn("Prepared unit metadata mismatch", result.stderr)
        self.assertEqual(commands, ["metadata-check"])

    def test_actual_invalid_supplied_config_does_not_roll_back(self):
        config = self.root / "invalid-unit-config.json"
        config.write_text('{}')
        result, commands = self.assert_preflight_preserves_running_unit(
            arguments=("--unit-id", "unit-b", "--unit-config", str(config)),
        )
        self.assertIn("Invalid per-unit voice activation configuration", result.stderr)
        self.assertEqual(commands, ["unit-config-check"])

    def test_invalid_arguments_do_not_roll_back(self):
        _, commands = self.assert_preflight_preserves_running_unit(arguments=("--invalid",))
        self.assertEqual(commands, [])

    def test_missing_bootstrap_marker_skips_core_and_fails_closed(self):
        source = self.script.read_text()
        bootstrap = 'mv "${candidate}" "${marker}"'
        self.assertIn(bootstrap, source)
        self.script.write_text(source.replace(bootstrap, 'rm -f "${marker}"'))
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.marker.write_text(EVIDENCE)
                result, commands = self.activate()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.marker.exists())
                for service in SERVICES[2:]:
                    self.assertIn(f"condition-skipped {service}", commands)
                self.assertFalse(any(command.startswith("runuser ") for command in commands))

    def test_missing_helper_rolls_back_existing_readiness(self):
        (self.root / "usr/local/libexec/sage-lantern-verify-local-voice").unlink()
        commands = self.assert_rollback("")
        self.assertFalse(any(command.startswith("runuser ") for command in commands))

    def test_fresh_activation_does_not_create_commons_marker(self):
        self.commons.unlink()
        for failure in ("helper", ""):
            with self.subTest(failure=failure):
                result, commands = self.activate(failure)
                self.assertEqual(result.returncode == 0, not failure, result.stderr)
                self.assertEqual(self.marker.exists(), not failure)
                self.assertFalse(self.commons.exists())
                if failure:
                    self.assertNotIn("systemctl restart sage-lantern-runtime.service", commands)

    def test_conflicting_evidence_is_not_overwritten(self):
        self.marker.write_text("conflicting evidence\n")
        result, commands = self.activate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence differs", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse(any(command.startswith("runuser ") for command in commands))

    def test_platform_guards_remain_before_service_operations(self):
        source = SCRIPT.read_text()
        self.assertLess(source.index("${EUID} -eq 0"), source.index("trap rollback EXIT"))
        self.assertLess(source.index("$(uname -m) == aarch64"), source.index("trap rollback EXIT"))
        self.assertNotIn("touch ", source)


if __name__ == "__main__":
    unittest.main()
