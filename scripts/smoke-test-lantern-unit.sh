#!/usr/bin/env bash

# Verify a provisioned SAGE Lantern installation. With --root, checks are
# resolved below a staging tree and no binaries, services, or endpoints run.
set -uo pipefail

root="/"
config="/etc/sage-lantern/lantern.toml"
audio=""
require_services=false
wait_health_seconds=0
gate=/var/lib/sage-lantern/PROVISIONED
gate_label='provisioning gate'

usage() {
  cat <<'EOF'
Usage: smoke-test-lantern-unit.sh [OPTIONS]

Options:
  --root DIR                  Inspect a staged filesystem without executing it
  --config PATH               Target-side config path (default: /etc/sage-lantern/lantern.toml)
  --audio WAV                 Run one prerecorded WAV through the voice CLI
  --require-services-active   Require the LLM and personal SAGE units to be active
  --local-voice-ready         Validate the private LOCAL_VOICE_READY gate instead of Commons provisioning
  --wait-health-seconds N     Retry only transient local health failures for up to N seconds (0-60)
  -h, --help                  Show this help

--audio, --require-services-active, and health waiting are valid only with --root /.
EOF
}

while (($#)); do
  case "$1" in
    --root)
      [[ $# -ge 2 ]] || { printf '%s\n' 'ERROR: --root requires a directory' >&2; exit 2; }
      root="$2"
      shift 2
      ;;
    --config)
      [[ $# -ge 2 ]] || { printf '%s\n' 'ERROR: --config requires a path' >&2; exit 2; }
      config="$2"
      shift 2
      ;;
    --audio)
      [[ $# -ge 2 ]] || { printf '%s\n' 'ERROR: --audio requires a WAV path' >&2; exit 2; }
      audio="$2"
      shift 2
      ;;
    --require-services-active)
      require_services=true
      shift
      ;;
    --wait-health-seconds)
      [[ $# -ge 2 ]] || { printf '%s\n' 'ERROR: --wait-health-seconds requires seconds' >&2; exit 2; }
      wait_health_seconds="$2"
      shift 2
      ;;
    --wait-health-seconds=*)
      wait_health_seconds="${1#*=}"
      shift
      ;;
    --local-voice-ready)
      gate=/var/lib/sage-lantern/LOCAL_VOICE_READY
      gate_label='local voice gate'
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'ERROR: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "$wait_health_seconds" =~ ^(0|[1-9][0-9]?)$ ]] || ((wait_health_seconds > 60)); then
  printf '%s\n' 'ERROR: --wait-health-seconds must be an integer from 0 to 60' >&2
  exit 2
fi
[[ -d "$root" ]] || { printf 'ERROR: root is not a directory: %s\n' "$root" >&2; exit 2; }
root="${root%/}"
[[ -n "$root" ]] || root="/"
if [[ "$root" != "/" && ( -n "$audio" || "$require_services" == true || "$wait_health_seconds" != 0 ) ]]; then
  printf '%s\n' 'ERROR: --audio, --require-services-active, and health waiting cannot execute against a staged root' >&2
  exit 2
fi

target_path() {
  local path="$1"
  if [[ "$root" == "/" ]]; then
    printf '%s' "$path"
  elif [[ "$path" == /* ]]; then
    printf '%s%s' "$root" "$path"
  else
    printf '%s/%s' "$root" "$path"
  fi
}

failures=0
passes=0
pass() { printf 'PASS  %s\n' "$1"; passes=$((passes + 1)); }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

check_file() {
  local label="$1" path="$2" mode="${3:-file}" host_path
  host_path="$(target_path "$path")"
  if [[ "$mode" == executable ]]; then
    [[ -f "$host_path" && -x "$host_path" ]] && pass "$label: $path" || fail "$label missing or not executable: $path"
  else
    [[ -f "$host_path" ]] && pass "$label: $path" || fail "$label missing: $path"
  fi
}

check_file "$gate_label" "$gate"
check_file 'Lantern identity' /var/lib/sage-lantern/identity/lantern-agent.key
check_file 'runtime CLI' /opt/sage-lantern/venv/bin/sage-lantern executable
check_file 'Whisper CLI' /opt/sage-lantern/bin/whisper-cli executable
check_file 'SAGE node' /opt/sage-lantern/bin/sage-gui executable
check_file 'llama.cpp server' /opt/sage-lantern/llama.cpp/llama-server executable
check_file 'Whisper model' /opt/sage-lantern/models/ggml-base.bin
check_file 'LLM model' /opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf
check_file 'Piper model' /opt/sage-lantern/models/en_US-lessac-medium.onnx
check_file 'Piper model config' /opt/sage-lantern/models/en_US-lessac-medium.onnx.json
check_file 'LLM systemd unit' /etc/systemd/system/sage-lantern-llm.service
check_file 'personal SAGE systemd unit' /etc/systemd/system/sage-lantern-personal-sage.service
check_file 'runtime readiness unit' /etc/systemd/system/sage-lantern-runtime.service

config_host="$(target_path "$config")"
if [[ -f "$config_host" ]]; then
  pass "runtime config: $config"
  if config_error="$(python3 - "$config_host" "$root" <<'PY'
import pathlib
import sys
import tomllib

config_path = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
with config_path.open("rb") as handle:
    config = tomllib.load(handle)

tts_enabled = bool(config.get("tts", {}).get("enabled", True))
required = {
    "runtime.state_log": ("runtime", "state_log", False),
    "runtime.output_dir": ("runtime", "output_dir", False),
    "stt.binary": ("stt", "binary", True),
    "stt.model": ("stt", "model", True),
    "llm.base_url": ("llm", "base_url", False),
    "llm.model": ("llm", "model", False),
    "sage.base_url": ("sage", "base_url", False),
    "sage.identity_path": ("sage", "identity_path", True),
}
if tts_enabled:
    required.update({
        "tts.python": ("tts", "python", True),
        "tts.model": ("tts", "model", True),
    })
errors = []
for label, (section, key, is_file) in required.items():
    value = config.get(section, {}).get(key)
    if value in (None, ""):
        errors.append(f"missing {label}")
        continue
    if is_file:
        target = pathlib.Path(value)
        if not target.is_absolute():
            target = config_path.parent / target
        elif root != pathlib.Path("/"):
            target = root / target.relative_to("/")
        if not target.is_file():
            errors.append(f"{label} target missing: {value}")
if errors:
    print("; ".join(errors))
    raise SystemExit(1)
PY
  )"; then
    pass 'configuration schema and file references'
  else
    fail "configuration invalid: $config_error"
  fi
else
  fail "runtime config missing: $config"
fi

if [[ "$root" == "/" && "$require_services" == true ]]; then
  for unit in sage-lantern-llm.service sage-lantern-personal-sage.service; do
    if systemctl is-active --quiet "$unit"; then
      pass "service active: $unit"
    else
      fail "service inactive: $unit"
    fi
  done
fi

runtime_cli="$(target_path /opt/sage-lantern/venv/bin/sage-lantern)"
wait_for_health() {
  python3 - "$runtime_cli" "$config_host" "$wait_health_seconds" <<'HEALTH_PY'
import json
import subprocess
import sys
import time

deadline = time.monotonic() + int(sys.argv[3])
endpoints = {"llama_server", "sage_node"}
last_output = ""
last_error = ""
while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        break
    try:
        result = subprocess.run(
            [sys.argv[1], "health", "--config", sys.argv[2]],
            capture_output=True, text=True, timeout=min(5.0, remaining),
        )
    except subprocess.TimeoutExpired:
        last_error = "Runtime health attempt timed out."
    except OSError:
        print("Runtime health command could not start.", file=sys.stderr)
        raise SystemExit(1)
    else:
        last_output, last_error = result.stdout, result.stderr
        try:
            checks = json.loads(result.stdout)
            if not isinstance(checks, dict) or not endpoints.issubset(checks):
                raise ValueError()
            if any(not isinstance(check, dict) or type(check.get("ok")) is not bool for check in checks.values()):
                raise ValueError()
        except (ValueError, TypeError):
            print("Runtime health returned an invalid report.", file=sys.stderr)
            raise SystemExit(1)
        failed = {name for name, check in checks.items() if not check["ok"]}
        if not failed and result.returncode == 0:
            print(result.stdout, end="")
            raise SystemExit(0)
        if result.returncode != 1 or not failed or failed - endpoints:
            print(result.stdout, end="")
            print("Runtime file/configuration check failed; not retrying.", file=sys.stderr)
            raise SystemExit(1)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        break
    time.sleep(min(1.0, remaining))
print(last_output, end="")
if last_error:
    print(last_error, file=sys.stderr)
print("Local health readiness deadline exceeded.", file=sys.stderr)
raise SystemExit(1)
HEALTH_PY
}

if [[ "$root" == "/" && -x "$runtime_cli" && -f "$config_host" ]]; then
  health_ready=false
  if ((wait_health_seconds > 0 && failures == 0)); then
    wait_for_health && health_ready=true
  else
    "$runtime_cli" health --config "$config_host" && health_ready=true
  fi
  if [[ "$health_ready" == true ]]; then
    pass 'runtime files and local health endpoints'
  else
    fail 'runtime files or local health endpoints unavailable'
  fi
fi

if [[ -n "$audio" ]]; then
  if [[ ! -f "$audio" ]]; then
    fail "input WAV missing: $audio"
  elif result="$($runtime_cli voice --config "$config_host" --audio "$audio")"; then
    printf '%s\n' "$result"
    pass "prerecorded-WAV voice loop: $audio"
  else
    fail "prerecorded-WAV voice loop failed: $audio"
  fi
fi

printf '\nLantern smoke test: %d passed, %d failed.\n' "$passes" "$failures"
((failures == 0))
