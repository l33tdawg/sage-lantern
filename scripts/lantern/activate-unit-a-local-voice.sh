#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: activate-unit-a-local-voice.sh [--unit-id unit-a|unit-b|unit-c|unit-d|unit-e|unit-f] [--unit-config FILE] [--dry-run]
Default unit-a preserves legacy paths. Units B–F require --unit-config.
Config is public JSON, never sourced: schema="sage-lantern-voice-activation-v1",
unit_id, prepared_metadata, inventory, stable_usb_audio, identity_evidence,
recovery_dir. Metadata/inventory use provision-lantern-unit.sh's existing schemas.
Evidence must be under the matching unit-a-provisioning through unit-f-provisioning path component.
Dry-run validates supplied public evidence only; no writes, service commands,
key reads, hardware probes or recovery execution. It is NOT readiness acceptance.
Staged metadata/inventory paths are allowed only in dry-run. Actual activation
requires /etc/sage-lantern/unit.env and the installed public inventory.
Operator must independently establish B provenance; fingerprints are attestations,
not proof of key generation or uniqueness against another device.
EOF
}
die() { printf '%s\n' "$*" >&2; exit 2; }
unit_id=unit-a
unit_config=''
dry_run=0
seen_unit=0
seen_config=0
while (($#)); do
  case "$1" in
    --help) (($# == 1)) || die 'Help takes no additional arguments.'; usage; exit 0 ;;
    --dry-run) ((dry_run == 0)) || die 'Duplicate dry-run.'; dry_run=1; shift ;;
    --unit-id)
      (($# >= 2 && seen_unit == 0)) || die 'Missing or duplicate unit-id.'
      unit_id=$2; seen_unit=1; shift 2 ;;
    --unit-config)
      (($# >= 2 && seen_config == 0)) || die 'Missing or duplicate unit-config.'
      [[ -n $2 && $2 != -* ]] || die 'Invalid unit-config.'
      unit_config=$2; seen_config=1; shift 2 ;;
    *) die 'Unknown activation argument.' ;;
  esac
done
[[ ${unit_id} =~ ^unit-[a-f]$ ]] || die 'Unsupported unit-id.'
[[ ${unit_id} == unit-a || -n ${unit_config} ]] || die 'Non-A units require independently prepared per-unit configuration and evidence.'

readonly state_root=/var/lib/sage-lantern
readonly marker=${state_root}/LOCAL_VOICE_READY
identity_evidence=/home/radxa/sage-commons/evidence/unit-a-provisioning/personal-public-identity.json
recovery_dir=/home/radxa/sage-commons/evidence/unit-a-provisioning/recovery-test
stable_usb_audio=/dev/snd/by-id/usb-SAGE_Commons_SAGE_Lantern_Audio_SAGE-LANTERN-A-AUDIO-00
if [[ -n ${unit_config} ]]; then
  validated=$(python3 - "${unit_config}" "${unit_id}" "${dry_run}" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

def require(condition):
    if not condition:
        raise ValueError

def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result

def safe_path(value):
    require(type(value) is str and re.fullmatch(r"/[A-Za-z0-9_./-]+", value))
    path = Path(value)
    require(".." not in path.parts)
    return path

def public_bytes(path):
    for parent in (*reversed(path.parents), path):
        require(not parent.is_symlink())
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 65536)
        result = os.read(descriptor, 65537)
        require(len(result) <= 65536)
        return result
    finally:
        os.close(descriptor)

def env(path):
    pairs = []
    for line in public_bytes(path).decode().splitlines():
        key, separator, value = line.partition("=")
        require(separator and re.fullmatch(r"[A-Z_]+", key))
        pairs.append((key, value))
    return unique(pairs)

try:
    config_path, unit, dry = sys.argv[1:]
    config = json.loads(public_bytes(Path(config_path).absolute()), object_pairs_hook=unique)
    require(type(config) is dict and set(config) == {
        "schema", "unit_id", "prepared_metadata", "inventory", "stable_usb_audio",
        "identity_evidence", "recovery_dir",
    })
    require(config["schema"] == "sage-lantern-voice-activation-v1" and config["unit_id"] == unit)
    paths = {key: safe_path(config[key]) for key in (
        "prepared_metadata", "inventory", "identity_evidence", "recovery_dir", "stable_usb_audio",
    )}
    require(paths["prepared_metadata"].name == "unit.env")
    require(paths["inventory"].name == "unit-inventory.env")
    require(paths["identity_evidence"].name == "personal-public-identity.json")
    for key in ("identity_evidence", "recovery_dir"):
        require(f"{unit}-provisioning" in paths[key].parts)
    if dry == "0":
        require(str(paths["prepared_metadata"]) == "/etc/sage-lantern/unit.env")
        require(str(paths["inventory"]) == "/var/lib/sage-lantern/inventory/unit-inventory.env")
    audio = str(paths["stable_usb_audio"])
    require(re.fullmatch(r"/dev/snd/by-id/usb-[A-Za-z0-9_-]+", audio))
    require(unit == "unit-a" or "SAGE-LANTERN-A-AUDIO" not in audio)
    metadata = env(paths["prepared_metadata"])
    require(metadata == {"UNIT_ID": unit, "PROVISIONING_SCHEMA": "sage-lantern-unit-v1"})
    inventory = env(paths["inventory"])
    fingerprints = ["PERSONAL_GENESIS", "COMET_NODE", "PERSONAL_VALIDATOR", "VOICE_IDENTITY",
                    "PUBLIC_PROPOSER", "TLS_FEDERATION", "RADIO_IDENTITY", "RECOVERY_EVIDENCE"]
    attestations = ["IDENTITY_UNIQUENESS", "RECOVERY_TEST", "NO_CLONED_SECRETS", "NO_PUBLIC_VALIDATOR_KEY"]
    require(set(inventory) == {"UNIT_ID", "INVENTORY_SCHEMA", "PERSONAL_CHAIN_ID"}
            | {name + "_FINGERPRINT" for name in fingerprints}
            | {name + "_ATTESTED" for name in attestations})
    require(inventory["UNIT_ID"] == unit and inventory["INVENTORY_SCHEMA"] == "sage-lantern-identity-v1")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", inventory["PERSONAL_CHAIN_ID"]))
    for name in fingerprints:
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", inventory[name + "_FINGERPRINT"]))
    require(len({inventory[name + "_FINGERPRINT"] for name in fingerprints[1:-1]}) == 6)
    require(all(inventory[name + "_ATTESTED"] == "PASS" for name in attestations))
    identity = json.loads(public_bytes(paths["identity_evidence"]), object_pairs_hook=unique)
    require(type(identity) is dict and identity.get("schema") == "sage.lantern.personal-public-identity.v1")
    for name in ("personal_chain_id", "personal_genesis_fingerprint", "comet_node_fingerprint",
                 "personal_validator_fingerprint", "voice_identity_fingerprint", "tls_federation_fingerprint"):
        require(identity[name] == inventory[name.upper()])
    recovery = public_bytes(paths["recovery_dir"] / "RECOVERY_EVIDENCE_SHA256").decode()
    require(re.fullmatch(r"[0-9a-f]{64}  recovery-evidence-bundle\n?", recovery))
    manifest = public_bytes(paths["recovery_dir"] / "SHA256SUMS")
    names = set()
    for line in manifest.decode().splitlines():
        match = re.fullmatch(r"[0-9a-f]{64}  ([a-zA-Z0-9.-]+)", line)
        require(match and match[1] not in names)
        names.add(match[1])
    require(names == {"backup-manifest.json", "identity-before.json", "identity-after.json",
                      "private-backup.sha256", "upgrade-preflight.txt"})
    digest = hashlib.sha256(manifest).hexdigest()
    require(recovery[:64] == digest and inventory["RECOVERY_EVIDENCE_FINGERPRINT"] == "sha256:" + digest)
    print(paths["identity_evidence"])
    print(paths["recovery_dir"])
    print(audio)
except (OSError, ValueError, TypeError, KeyError):
    raise SystemExit("Invalid per-unit voice activation configuration or public evidence.")
PY
  ) || exit 2
  { IFS= read -r identity_evidence; IFS= read -r recovery_dir; IFS= read -r stable_usb_audio; } <<<"${validated}"
fi
if ((dry_run)); then
  printf 'DRY RUN: %s local voice activation; no target changes.\n' "${unit_id}"
  printf 'identity_evidence=%s\nrecovery_dir=%s\nstable_usb_audio=%s\n' "${identity_evidence}" "${recovery_dir}" "${stable_usb_audio}"
  echo 'Pending target gates: prepared identity, hardware/PTT, recovery checks, TTS configuration, dependencies and service verification.'
  exit 0
fi
[[ ${EUID} -eq 0 ]] || die 'Run as root on the selected unit.'
[[ $(uname -m) == aarch64 ]] || die 'Local voice requires aarch64.'
python3 - "${unit_id}" <<'PY'
from pathlib import Path
import sys
expected = f"UNIT_ID={sys.argv[1]}\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n"
if Path("/etc/sage-lantern/unit.env").read_text() != expected:
    raise SystemExit("Prepared unit metadata mismatch.")
PY

candidate=''
activated=0
services=(
  sage-lantern-voice-loop.service
  sage-lantern-runtime.service
  sage-lantern-personal-sage.service
  sage-lantern-llm.service
)
rollback() {
  local result=$?
  trap - EXIT
  if ((activated == 0)); then
    rm -f "${marker}" || true
    for service in "${services[@]}"; do
      systemctl stop "${service}" || echo "Rollback could not stop ${service}." >&2
      systemctl disable "${service}" || echo "Rollback could not disable ${service}." >&2
    done
    echo 'Local voice activation failed; LOCAL_VOICE_READY was rolled back.' >&2
    ((result != 0)) || result=1
  fi
  if [[ -n ${candidate} ]]; then
    rm -f "${candidate}" || true
  fi
  exit "${result}"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

systemctl stop sage-lantern-voice-loop.service sage-lantern-runtime.service

required=(
  /etc/sage-lantern/unit.env
  /etc/sage-lantern/lantern.toml
  "${state_root}/identity/lantern-agent.key"
  "${state_root}/PTT_VERIFIED"
  "${identity_evidence}"
  "${recovery_dir}/RECOVERY_EVIDENCE_SHA256"
  /opt/sage-lantern/venv/bin/sage-lantern
  /opt/sage-lantern/venv/bin/python
  /usr/local/libexec/sage-lantern-verify-local-voice
  /opt/sage-lantern/bin/whisper-cli
  /opt/sage-lantern/llama.cpp/llama-server
)
for path in "${required[@]}"; do
  [[ -f ${path} ]] || { echo "Missing local voice prerequisite: ${path}" >&2; exit 1; }
done
[[ -e ${stable_usb_audio} ]] || {
  echo 'Configured stable USB audio identity is missing.' >&2
  exit 1
}

(cd "${recovery_dir}" && sha256sum --check --quiet SHA256SUMS)
python3 - /etc/sage-lantern/lantern.toml <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as stream:
    config = tomllib.load(stream)
if config.get("tts", {}).get("enabled", True) is not False:
    raise SystemExit("tts.enabled must be false for the screen-answer prototype")
PY

identity_sha=$(sha256sum "${identity_evidence}" | awk '{print $1}')
recovery_sha=$(awk 'NF {print $1; exit}' "${recovery_dir}/RECOVERY_EVIDENCE_SHA256")
candidate=$(mktemp "${state_root}/.LOCAL_VOICE_READY.XXXXXX")
printf 'UNIT_ID=%s\nIDENTITY_EVIDENCE_SHA256=%s\nRECOVERY_EVIDENCE_SHA256=%s\nMODE=speech-to-screen\n' \
  "${unit_id}" "${identity_sha}" "${recovery_sha}" >"${candidate}"
chmod 0640 "${candidate}"
chown root:sage-lantern "${candidate}"
if [[ -f ${marker} ]] && ! cmp -s "${candidate}" "${marker}"; then
  rm -f "${candidate}"
  echo 'Existing LOCAL_VOICE_READY evidence differs; refusing overwrite.' >&2
  exit 1
fi
mv "${candidate}" "${marker}"
echo 'LOCAL_VOICE_READY temporarily permits core bootstrap; it is not full acceptance until verification and service checks succeed.'

systemctl daemon-reload
systemctl restart sage-lantern-llm.service sage-lantern-personal-sage.service

ready=0
for _ in {1..180}; do
  if curl --fail --silent --max-time 2 http://127.0.0.1:8080/health >/dev/null \
      && curl --fail --silent --max-time 2 http://127.0.0.1:8081/health >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if ((ready == 0)); then
  echo 'Local core failed readiness.' >&2
  exit 1
fi

for service in sage-lantern-llm.service sage-lantern-personal-sage.service; do
  systemctl is-active --quiet "${service}"
done
timeout --signal=TERM --kill-after=5 90 runuser -u sage-lantern -G audio -- \
  /opt/sage-lantern/venv/bin/python /usr/local/libexec/sage-lantern-verify-local-voice \
  --config /etc/sage-lantern/lantern.toml

systemctl restart sage-lantern-runtime.service
systemctl is-active --quiet sage-lantern-runtime.service
systemctl restart sage-lantern-voice-loop.service
for service in "${services[@]}" sage-lantern-ptt.service; do
  systemctl is-active --quiet "${service}"
done
systemctl enable "${services[@]}"
activated=1
printf '%s private speech-to-screen loop is active. Commons PROVISIONED remains independent.\n' "${unit_id}"
