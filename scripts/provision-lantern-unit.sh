#!/usr/bin/env bash

set -euo pipefail

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  provision-lantern-unit.sh prepare --unit-id ID [--staging-root DIR] [--dry-run]
  provision-lantern-unit.sh verify --unit-id ID [verification options] [--staging-root DIR] [--dry-run]

This script never creates, imports, or copies private keys. Generate every SAGE,
Commons, TLS/federation, and radio identity with its owning system on the final
unit. Only public SHA-256 fingerprints and operator attestations are inventoried.

Verification options (all required for "verify"):
  --personal-chain-id ID
  --personal-genesis-fingerprint sha256:HEX
  --comet-node-fingerprint sha256:HEX
  --personal-validator-fingerprint sha256:HEX
  --voice-identity-fingerprint sha256:HEX
  --public-proposer-fingerprint sha256:HEX
  --tls-federation-fingerprint sha256:HEX
  --radio-identity-fingerprint sha256:HEX
  --recovery-evidence-fingerprint sha256:HEX
  --attest-identity-unique
  --attest-recovery-tested
  --attest-no-cloned-secrets
  --attest-no-public-validator-key

Without --staging-root, this must run as root on Linux aarch64 and requires the
sage-lantern service account. A staging root permits safe tests on other hosts.
--dry-run validates all inputs and reports intended writes without changing disk.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

log() {
  printf '%s\n' "$*"
}

valid_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

valid_fingerprint() {
  [[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]]
}

action="${1:-}"
case "${action}" in
  prepare|verify) shift ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; die 'first argument must be prepare or verify' ;;
esac

unit_id=''
staging_root=''
dry_run=0
personal_chain_id=''
personal_genesis_fingerprint=''
comet_node_fingerprint=''
personal_validator_fingerprint=''
voice_identity_fingerprint=''
public_proposer_fingerprint=''
tls_federation_fingerprint=''
radio_identity_fingerprint=''
recovery_evidence_fingerprint=''
attest_identity_unique=0
attest_recovery_tested=0
attest_no_cloned_secrets=0
attest_no_public_validator_key=0

while (($#)); do
  case "$1" in
    --unit-id|--staging-root|--personal-chain-id|--personal-genesis-fingerprint|--comet-node-fingerprint|--personal-validator-fingerprint|--voice-identity-fingerprint|--public-proposer-fingerprint|--tls-federation-fingerprint|--radio-identity-fingerprint|--recovery-evidence-fingerprint)
      (($# >= 2)) || die "missing value for $1"
      option="${1#--}"
      option="${option//-/_}"
      printf -v "${option}" '%s' "$2"
      shift 2
      ;;
    --attest-identity-unique) attest_identity_unique=1; shift ;;
    --attest-recovery-tested) attest_recovery_tested=1; shift ;;
    --attest-no-cloned-secrets) attest_no_cloned_secrets=1; shift ;;
    --attest-no-public-validator-key) attest_no_public_validator_key=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "${unit_id}" ]] || die '--unit-id is required'
valid_id "${unit_id}" || die '--unit-id must be 1-64 safe characters (letters, digits, dot, underscore, hyphen)'

if [[ -n "${staging_root}" ]]; then
  [[ "${staging_root}" == /* ]] || die '--staging-root must be an absolute path'
  [[ "${staging_root}" != / ]] || die '--staging-root must not be /'
  state_root="${staging_root%/}/var/lib/sage-lantern"
  config_root="${staging_root%/}/etc/sage-lantern"
  target_mode=0
else
  [[ "${EUID}" -eq 0 ]] || die 'run as root on the target Lantern'
  [[ "$(uname -s)" == Linux ]] || die 'target provisioning requires Linux'
  [[ "$(uname -m)" == aarch64 ]] || die "target provisioning requires aarch64; found $(uname -m)"
  id sage-lantern >/dev/null 2>&1 || die 'sage-lantern service account is missing; run install-lantern-unit.sh first'
  state_root='/var/lib/sage-lantern'
  config_root='/etc/sage-lantern'
  target_mode=1
fi

metadata_file="${config_root}/unit.env"
requirements_file="${state_root}/identity/EXTERNAL_IDENTITY_REQUIREMENTS.txt"
inventory_file="${state_root}/inventory/unit-inventory.env"
provisioned_file="${state_root}/PROVISIONED"
profile_root="${staging_root%/}/etc/sage"
profile_file="${profile_root}/provisioning-profile.yaml"
profile_template="${source_root}/software/config/sage-provisioning-profile.example.yaml"
[[ -f "${profile_template}" ]] || die 'reviewed SAGE provisioning profile template is missing'

if [[ -f "${metadata_file}" ]]; then
  recorded_unit="$(sed -n 's/^UNIT_ID=//p' "${metadata_file}")"
  [[ "${recorded_unit}" == "${unit_id}" ]] || die "unit is already prepared as ${recorded_unit:-unknown}; refusing ${unit_id}"
fi

if [[ "${action}" == verify ]]; then
  [[ -f "${metadata_file}" ]] || die 'unit is not prepared; run prepare first'

  valid_id "${personal_chain_id}" || die '--personal-chain-id is missing or invalid'
  for name in personal_genesis_fingerprint comet_node_fingerprint personal_validator_fingerprint voice_identity_fingerprint public_proposer_fingerprint tls_federation_fingerprint radio_identity_fingerprint recovery_evidence_fingerprint; do
    value="${!name}"
    valid_fingerprint "${value}" || die "--${name//_/-} must be sha256: followed by 64 lowercase hex characters"
  done

  ((attest_identity_unique == 1)) || die '--attest-identity-unique is required'
  ((attest_recovery_tested == 1)) || die '--attest-recovery-tested is required'
  ((attest_no_cloned_secrets == 1)) || die '--attest-no-cloned-secrets is required'
  ((attest_no_public_validator_key == 1)) || die '--attest-no-public-validator-key is required'

  identity_fingerprints=(
    "${comet_node_fingerprint}"
    "${personal_validator_fingerprint}"
    "${voice_identity_fingerprint}"
    "${public_proposer_fingerprint}"
    "${tls_federation_fingerprint}"
    "${radio_identity_fingerprint}"
  )
  for ((i = 0; i < ${#identity_fingerprints[@]}; i++)); do
    for ((j = i + 1; j < ${#identity_fingerprints[@]}; j++)); do
      [[ "${identity_fingerprints[i]}" != "${identity_fingerprints[j]}" ]] || die 'identity fingerprints must be distinct'
    done
  done
fi

if ((dry_run == 1)); then
  log "DRY RUN: ${action} unit ${unit_id} under ${staging_root:-/}"
  log "DRY RUN: would write ${profile_file}, ${metadata_file} and ${requirements_file}"
  if [[ "${action}" == verify ]]; then
    log "DRY RUN: verification passed; would write ${inventory_file}, then ${provisioned_file} last"
  fi
  exit 0
fi

if ((target_mode == 1)); then
  install -d -o root -g sage-lantern -m 0750 "${config_root}"
  [[ ! -L "${profile_root}" ]] || die 'existing /etc/sage is a symlink; refusing to install policy'
  install -d -o root -g root -m 0755 "${profile_root}"
  # The runtime and first-run SAGE ceremony execute as sage-lantern. Keep its
  # private state tree service-owned; root-owned mode 0700 would lock the
  # service account out of its own home after `prepare`.
  install -d -o sage-lantern -g sage-lantern -m 0700 "${state_root}" "${state_root}/identity" "${state_root}/inventory"
else
  install -d -m 0750 "${config_root}"
  [[ ! -L "${profile_root}" ]] || die 'existing /etc/sage is a symlink; refusing to install policy'
  install -d -m 0755 "${profile_root}"
  install -d -m 0700 "${state_root}" "${state_root}/identity" "${state_root}/inventory"
fi

if [[ -L "${profile_file}" ]]; then
  die 'existing SAGE provisioning profile is a symlink; refusing to replace it'
fi
if [[ -e "${profile_file}" ]]; then
  cmp -s "${profile_template}" "${profile_file}" || die 'existing SAGE provisioning profile differs; refusing to overwrite it'
else
  profile_tmp="$(mktemp "${profile_root}/.provisioning-profile.XXXXXX")"
  cp "${profile_template}" "${profile_tmp}"
  chmod 0600 "${profile_tmp}"
  ((target_mode == 0)) || chown root:root "${profile_tmp}"
  mv "${profile_tmp}" "${profile_file}"
fi
chmod 0600 "${profile_file}"
((target_mode == 0)) || chown root:root "${profile_file}"

if [[ ! -f "${metadata_file}" ]]; then
  metadata_tmp="$(mktemp "${config_root}/.unit.env.XXXXXX")"
  printf 'UNIT_ID=%s\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n' "${unit_id}" >"${metadata_tmp}"
  chmod 0640 "${metadata_tmp}"
  ((target_mode == 0)) || chown root:sage-lantern "${metadata_tmp}"
  mv "${metadata_tmp}" "${metadata_file}"
fi

if [[ ! -f "${requirements_file}" ]]; then
  requirements_tmp="$(mktemp "${state_root}/identity/.requirements.XXXXXX")"
  cat >"${requirements_tmp}" <<EOF
Unit: ${unit_id}

Generate identities externally, on their owning final systems. Do not place a
public Commons validator key on this Lantern. Do not clone a live SAGE_HOME,
Root/Admin/recovery material, ordinary-agent keys, proposer keys, TLS/federation
keys, receive tokens, journals, queues, or radio identity from another unit.

Required public inventory: personal chain/genesis, Comet node, personal validator,
ordinary voice agent, Commons Member proposer, TLS/federation, and radio identity.
The provisioner records fingerprints only; it never handles private key material.
EOF
  chmod 0600 "${requirements_tmp}"
  ((target_mode == 0)) || chown root:sage-lantern "${requirements_tmp}"
  mv "${requirements_tmp}" "${requirements_file}"
fi

if [[ "${action}" == prepare ]]; then
  log "Prepared unit ${unit_id}; no identity or secret was created."
  log "Generate identities externally, then run verify with all required public fingerprints and attestations."
  exit 0
fi

inventory_tmp="$(mktemp "${state_root}/inventory/.unit-inventory.XXXXXX")"
cat >"${inventory_tmp}" <<EOF
INVENTORY_SCHEMA=sage-lantern-identity-v1
UNIT_ID=${unit_id}
PERSONAL_CHAIN_ID=${personal_chain_id}
PERSONAL_GENESIS_FINGERPRINT=${personal_genesis_fingerprint}
COMET_NODE_FINGERPRINT=${comet_node_fingerprint}
PERSONAL_VALIDATOR_FINGERPRINT=${personal_validator_fingerprint}
VOICE_IDENTITY_FINGERPRINT=${voice_identity_fingerprint}
PUBLIC_PROPOSER_FINGERPRINT=${public_proposer_fingerprint}
TLS_FEDERATION_FINGERPRINT=${tls_federation_fingerprint}
RADIO_IDENTITY_FINGERPRINT=${radio_identity_fingerprint}
RECOVERY_EVIDENCE_FINGERPRINT=${recovery_evidence_fingerprint}
IDENTITY_UNIQUENESS_ATTESTED=PASS
RECOVERY_TEST_ATTESTED=PASS
NO_CLONED_SECRETS_ATTESTED=PASS
NO_PUBLIC_VALIDATOR_KEY_ATTESTED=PASS
EOF
chmod 0640 "${inventory_tmp}"
((target_mode == 0)) || chown root:sage-lantern "${inventory_tmp}"

if [[ -f "${inventory_file}" ]] && ! cmp -s "${inventory_tmp}" "${inventory_file}"; then
  rm -f "${inventory_tmp}"
  die 'existing identity inventory differs; refusing to overwrite it'
fi
if [[ ! -f "${inventory_file}" ]]; then
  mv "${inventory_tmp}" "${inventory_file}"
else
  rm -f "${inventory_tmp}"
fi

if command -v sha256sum >/dev/null 2>&1; then
  inventory_digest="$(sha256sum "${inventory_file}" | awk '{print $1}')"
else
  inventory_digest="$(shasum -a 256 "${inventory_file}" | awk '{print $1}')"
fi

provisioned_tmp="$(mktemp "${state_root}/.PROVISIONED.XXXXXX")"
printf 'UNIT_ID=%s\nINVENTORY_SHA256=%s\n' "${unit_id}" "${inventory_digest}" >"${provisioned_tmp}"
chmod 0640 "${provisioned_tmp}"
((target_mode == 0)) || chown root:sage-lantern "${provisioned_tmp}"

if [[ -f "${provisioned_file}" ]] && ! cmp -s "${provisioned_tmp}" "${provisioned_file}"; then
  rm -f "${provisioned_tmp}"
  die 'existing PROVISIONED marker differs; refusing to overwrite it'
fi
if [[ ! -f "${provisioned_file}" ]]; then
  mv "${provisioned_tmp}" "${provisioned_file}"
else
  rm -f "${provisioned_tmp}"
fi

log "Provisioning verification passed for ${unit_id}."
log "Recorded public inventory ${inventory_digest}; PROVISIONED was written last."
