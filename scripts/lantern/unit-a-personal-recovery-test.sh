#!/usr/bin/env bash
set -euo pipefail

[[ ${EUID} -eq 0 ]] || {
  echo 'run as root' >&2
  exit 1
}

sage_home=/var/lib/sage-lantern/sage-personal
sage_bin=/opt/sage-lantern/bin/sage-gui
capture_bin=/opt/sage-lantern/tools/capture-lantern-public-identity.py
voice_key=/var/lib/sage-lantern/identity/lantern-agent.key
private_dir=/var/lib/sage-lantern/recovery-test-private
archive=${private_dir}/sage-full-unit-a.tar.gz
evidence_dir=/home/radxa/sage-commons/evidence/unit-a-provisioning/recovery-test

[[ -x ${sage_bin} ]] || { echo "missing ${sage_bin}" >&2; exit 1; }
[[ -x ${capture_bin} ]] || { echo "missing ${capture_bin}" >&2; exit 1; }
[[ -f ${voice_key} ]] || { echo "missing ${voice_key}" >&2; exit 1; }
[[ ! -e ${archive} ]] || { echo "refusing existing ${archive}" >&2; exit 1; }
[[ ! -e ${evidence_dir} ]] || { echo "refusing existing ${evidence_dir}" >&2; exit 1; }

mapfile -t sage_pids < <(pgrep -u sage-lantern -f '^/opt/sage-lantern/bin/sage-gui serve$' || true)
if ((${#sage_pids[@]})); then
  echo "Stopping personal SAGE gracefully: ${sage_pids[*]}"
  kill -TERM "${sage_pids[@]}"
  for _ in {1..30}; do
    pgrep -u sage-lantern -f '^/opt/sage-lantern/bin/sage-gui serve$' >/dev/null || break
    sleep 1
  done
fi
if pgrep -u sage-lantern -f '^/opt/sage-lantern/bin/sage-gui serve$' >/dev/null; then
  echo 'personal SAGE did not stop within 30 seconds; refusing backup' >&2
  exit 1
fi

install -d -o sage-lantern -g sage-lantern -m 0700 "${private_dir}"
install -d -o radxa -g radxa -m 0750 "${evidence_dir}"

sudo -u sage-lantern env HOME=/var/lib/sage-lantern SAGE_HOME="${sage_home}" \
  /opt/sage-lantern/venv/bin/python "${capture_bin}" \
  --sage-home "${sage_home}" --voice-key "${voice_key}" \
  >"${evidence_dir}/identity-before.json"

echo 'Creating complete stopped-node backup...'
sudo -u sage-lantern env HOME=/var/lib/sage-lantern SAGE_HOME="${sage_home}" \
  "${sage_bin}" backup --full --out "${archive}"
sha256sum "${archive}" | sed "s#${archive}#sage-full-unit-a.tar.gz#" \
  >"${evidence_dir}/private-backup.sha256"
tar -xOf "${archive}" sage-backup-manifest.json \
  >"${evidence_dir}/backup-manifest.json"

echo 'Restoring the complete backup atomically over the stopped node...'
sudo -u sage-lantern env HOME=/var/lib/sage-lantern SAGE_HOME="${sage_home}" \
  "${sage_bin}" restore --from "${archive}" --force

mapfile -t aside_paths < <(compgen -G "${sage_home}.pre-restore-*" || true)
if ((${#aside_paths[@]} != 1)); then
  echo "expected exactly one moved-aside original tree, found ${#aside_paths[@]}" >&2
  exit 1
fi
aside_path=${aside_paths[0]}
[[ -n ${aside_path} && ${aside_path} == "${sage_home}.pre-restore-"* ]] || {
  echo 'refusing unexpected moved-aside tree path' >&2
  exit 1
}

sudo -u sage-lantern env HOME=/var/lib/sage-lantern SAGE_HOME="${sage_home}" \
  /opt/sage-lantern/venv/bin/python "${capture_bin}" \
  --sage-home "${sage_home}" --voice-key "${voice_key}" \
  >"${evidence_dir}/identity-after.json"
cmp "${evidence_dir}/identity-before.json" "${evidence_dir}/identity-after.json"

sudo -u sage-lantern env HOME=/var/lib/sage-lantern SAGE_HOME="${sage_home}" \
  "${sage_bin}" upgrade preflight >"${evidence_dir}/upgrade-preflight.txt"

(
  cd "${evidence_dir}"
  sha256sum backup-manifest.json identity-before.json identity-after.json \
    private-backup.sha256 upgrade-preflight.txt >SHA256SUMS
  sha256sum SHA256SUMS | sed 's/  SHA256SUMS$/  recovery-evidence-bundle/' \
    >RECOVERY_EVIDENCE_SHA256
)
chown -R radxa:radxa "${evidence_dir}"
find "${evidence_dir}" -type f -exec chmod 0640 {} +

# These are secret-bearing duplicates created only for this proof. Keep neither
# once the restored live tree has passed identity comparison and preflight.
rm -rf -- "${aside_path}" "${private_dir}"

echo
echo 'Recovery test passed; restored identity is byte-for-byte equivalent publicly.'
cat "${evidence_dir}/RECOVERY_EVIDENCE_SHA256"
echo 'Personal SAGE remains stopped. No duplicate secret-bearing tree was retained.'
