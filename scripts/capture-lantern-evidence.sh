#!/usr/bin/env bash

set -uo pipefail

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target_dir="${1:-evidence/lantern-${timestamp}}"

mkdir -p "${target_dir}"

run_if_available() {
  local output_file="$1"
  shift

  if command -v "$1" >/dev/null 2>&1; then
    "$@" >"${target_dir}/${output_file}" 2>&1 || true
  else
    printf 'Command unavailable: %s\n' "$1" >"${target_dir}/${output_file}"
  fi
}

{
  printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'hostname=%s\n' "$(hostname 2>/dev/null || printf unknown)"
  printf 'kernel=%s\n' "$(uname -srmo 2>/dev/null || printf unknown)"
  printf 'evidence_tool=capture-lantern-evidence.sh\n'
} >"${target_dir}/identity.txt"

{
  uname -a 2>/dev/null || true
  if [[ -r /etc/os-release ]]; then
    printf '\n'
    sed -n '1,80p' /etc/os-release
  fi
} >"${target_dir}/os.txt"

run_if_available storage.txt lsblk -o NAME,PATH,MODEL,SERIAL,SIZE,TYPE,FSTYPE,MOUNTPOINTS
run_if_available pci.txt lspci -nn
run_if_available usb.txt lsusb
run_if_available network-links.txt ip -brief link
run_if_available failed-services.txt systemctl --failed --no-pager

{
  if [[ -x /opt/sage-lantern/bin/sage-gui ]]; then
    /opt/sage-lantern/bin/sage-gui version 2>&1 || true
  elif command -v sage-gui >/dev/null 2>&1; then
    sage-gui version 2>&1 || true
  elif command -v sage >/dev/null 2>&1; then
    sage --version 2>&1 || true
  else
    printf 'SAGE command unavailable\n'
  fi
} >"${target_dir}/sage-version.txt"

{
  if [[ -x /opt/sage-lantern/venv/bin/sage-lantern ]]; then
    /opt/sage-lantern/venv/bin/sage-lantern --version 2>&1 || true
  else
    printf 'Lantern runtime unavailable\n'
  fi
  if [[ -f /var/lib/sage-lantern/inventory/unit-inventory.env ]]; then
    printf 'provisioning_inventory=present\n'
  else
    printf 'provisioning_inventory=absent\n'
  fi
  if [[ -f /var/lib/sage-lantern/PROVISIONED ]]; then
    printf 'provisioned_gate=present\n'
  else
    printf 'provisioned_gate=absent\n'
  fi
} >"${target_dir}/lantern-runtime.txt"

{
  shopt -s nullglob
  zone_count=0
  for zone in /sys/class/thermal/thermal_zone*; do
    zone_count=$((zone_count + 1))
    zone_name="$(basename "${zone}")"
    zone_type="$(cat "${zone}/type" 2>/dev/null || printf unknown)"
    zone_temp="$(cat "${zone}/temp" 2>/dev/null || printf unknown)"
    printf '%s type=%s raw_temp=%s\n' "${zone_name}" "${zone_type}" "${zone_temp}"
  done
  if (( zone_count == 0 )); then
    printf 'No thermal zones exposed\n'
  fi
} >"${target_dir}/thermals.txt"

{
  for path in /dev/serial/by-id /dev/serial/by-path /dev/snd /dev/input/by-id; do
    printf '[%s]\n' "${path}"
    if [[ -d "${path}" ]]; then
      ls -la "${path}" 2>&1 || true
    else
      printf 'Not present\n'
    fi
    printf '\n'
  done
} >"${target_dir}/stable-device-paths.txt"

{
  shopt -s nullglob
  supply_count=0
  for supply in /sys/class/power_supply/*; do
    supply_count=$((supply_count + 1))
    printf '[%s]\n' "$(basename "${supply}")"
    for field in type status online capacity voltage_now current_now power_now; do
      if [[ -r "${supply}/${field}" ]]; then
        printf '%s=%s\n' "${field}" "$(cat "${supply}/${field}")"
      fi
    done
    printf '\n'
  done
  if (( supply_count == 0 )); then
    printf 'No OS-visible power supplies\n'
  fi
} >"${target_dir}/power.txt"

(
  cd "${target_dir}" || exit 1
  if command -v sha256sum >/dev/null 2>&1; then
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS
  elif command -v shasum >/dev/null 2>&1; then
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 shasum -a 256 >SHA256SUMS
  else
    printf 'No SHA-256 utility available\n' >SHA256SUMS
  fi
)

printf 'Lantern evidence captured in %s\n' "${target_dir}"
