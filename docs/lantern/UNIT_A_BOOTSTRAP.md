# Unit A OS and Runtime Bootstrap

**Target:** Radxa ROCK 5B+, already booted into Debian Bookworm (12) ARM64

This runbook prepares a fresh Unit A operating system for the sterile Lantern
runtime. It does not create a bootable image, flash an NVMe drive, modify a
bootloader or partition a disk.

## 1. Install the operating system outside this repository

Use a Radxa-supported Debian Bookworm ARM64 image and the corresponding Radxa
instructions to boot the board and, if desired, install or migrate the OS to
NVMe. Image selection, boot media creation, SPI/bootloader changes and NVMe
partitioning are vendor-platform operations and remain outside the Lantern
scripts.

Before continuing, boot Unit A from its intended storage and confirm the root
filesystem is on the expected device. Do not infer that from the presence of an
NVMe drive:

```bash
cat /etc/os-release
uname -m
findmnt -no SOURCE,FSTYPE,SIZE /
lsblk -o NAME,PATH,MODEL,SERIAL,SIZE,TYPE,FSTYPE,MOUNTPOINTS
```

The expected architecture is `aarch64`; the OS must identify as Debian
Bookworm/12 (or a Debian-derived Bookworm image). Record the image name,
download checksum, bootloader version, storage serial and the output above in
the Unit A evidence package.

## 2. Transfer the complete sterile repository

Copy this repository to Unit A without adding any identity directories, keys,
tokens, live SAGE homes or radio credentials. Keep `software/stack.lock.json`
and all cached artifacts together; the runtime installation is designed to
work from the local bundle after OS prerequisites are present.

From the repository root on Unit A, preview or inspect the prerequisite state:

```bash
scripts/bootstrap-lantern-os.sh --dry-run
scripts/bootstrap-lantern-os.sh --check
```

The default mode is `--check` and makes no changes. To install only missing
packages from the OS-configured apt repositories:

```bash
sudo scripts/bootstrap-lantern-os.sh --install
```

The command is idempotent. It does not change apt sources, hostname, users,
networking, storage, boot configuration, identities or service enablement.
`apt-get update` requires network access; use `--skip-update` only when the
existing package indexes are known to be current or a local apt mirror is in
use.

Installed prerequisites cover Python 3.11 virtual environments, the native
whisper.cpp build, ARM64 runtime libraries, ALSA capture/playback inspection,
and USB/PCI enumeration.

## 3. Verify and install the sterile Lantern runtime

Run the locked-artifact verification as the normal operator, then the existing
runtime installer as root:

```bash
python3 scripts/verify-lantern-bundle.py
sudo scripts/install-lantern-unit.sh
```

The installer places the runtime under `/opt/sage-lantern`, state under
`/var/lib/sage-lantern`, and configuration under `/etc/sage-lantern`. It leaves
services disabled and does not create Unit A identity material.

## 4. Capture the pre-provisioning baseline

While the runtime is still sterile, capture the board and peripheral baseline:

```bash
scripts/capture-lantern-evidence.sh evidence/unit-a-pre-provision
```

Review at least the OS, storage, thermal, PCI, USB, failed-service and stable
device-path reports. A missing or unexpected peripheral is a bring-up finding;
do not compensate by hard-coding a transient `/dev` path.

## 5. Prepare the Unit A identity boundary

Do not create `/var/lib/sage-lantern/PROVISIONED` or enable the SAGE/LLM
services until Unit A has been provisioned with unique identities and its
public fingerprints and recovery evidence have been checked. Never clone a
provisioned Unit A filesystem to Unit B. Clone only sterile OS/runtime state,
then provision Unit B independently.

Record the immutable unit name and create the external-identity requirements:

```bash
sudo scripts/provision-lantern-unit.sh prepare --unit-id unit-a
```

This step creates no key. Generate the personal SAGE chain/node material,
ordinary voice-agent identity, Commons proposer identity, TLS/federation
identity and Heltec radio identity using their owning systems on Unit A. The
script deliberately cannot import or copy those secrets.

The ordinary voice-agent key consumed by `lantern.toml` uses the SAGE Python
SDK's 32-byte Ed25519 seed format. Generate it once on Unit A, refusing to
overwrite an existing key:

```bash
sudo -u sage-lantern /opt/sage-lantern/venv/bin/python -c \
  'from pathlib import Path; from sage_sdk import AgentIdentity; p=Path("/var/lib/sage-lantern/identity/lantern-agent.key"); assert not p.exists(), f"refusing overwrite: {p}"; i=AgentIdentity.generate(); i.to_file(p); p.chmod(0o600); print(i.agent_id)'
```

The printed value is public. Preserve it in the private build record. Start the
personal SAGE binary manually for its first-run/onboarding ceremony while the
systemd gate remains closed, then stop it before fingerprinting its public
identity artifacts:

```bash
sudo -u sage-lantern env HOME=/var/lib/sage-lantern \
  SAGE_HOME=/var/lib/sage-lantern/sage-personal \
  /opt/sage-lantern/bin/sage-gui serve
```

Use the local CEREBRUM UI on `http://127.0.0.1:8080/ui/` to finish the private
personal-node setup. This is a local initialization run, not permission to add
a Commons validator key. Record only public fingerprints in the provisioning
inventory; keep recovery evidence and all private material out of the repo.

## Private voice enrollment and acceptance

A key file, an agent-directory entry marked `active`, and HTTP `/health` are
not evidence of approved enrollment. The app-v23+ embedding boundary requires
an active, coherent committed enrollment. Keep the ordinary voice identity
separate from the local operator and from the public Commons proposer.

After installing the local helpers, start the private node for its onboarding
ceremony as described above. With explicit operator approval, enroll the
existing voice key on that node only:

```bash
sudo -u sage-lantern /opt/sage-lantern/venv/bin/python \
  /usr/local/libexec/sage-lantern-approve-local-voice \
  --config /etc/sage-lantern/lantern.toml \
  --sage-home /var/lib/sage-lantern/sage-personal \
  --operator-key /var/lib/sage-lantern/sage-personal/agent.key \
  --name lantern-unit-a-voice --clearance 1 --approve
```

Review the configured private home domain and clearance before approval on
each unit. This assigns Member/Companion, not Admin. The helper requires the
exact current local operator, refuses remote endpoints and mismatched existing
policy, and retains a permission-restricted same-device copy of the existing
voice key in SAGE's supported consent-key inventory. It does not generate or
replace identity material. Include both paths in protected device backup;
never clone either key onto another unit. This is not Commons enrollment.

With the voice loop stopped, run the real dependency checks as the same account
with the same audio access used by the service:

```bash
sudo runuser -u sage-lantern -G audio -- \
  /opt/sage-lantern/venv/bin/python \
  /usr/local/libexec/sage-lantern-verify-local-voice \
  --config /etc/sage-lantern/lantern.toml
```

This performs signed embedding and hybrid retrieval plus a one-second live
microphone capture/stop; temporary audio is deleted and no memory content is
printed. Do not run it while a user is recording. Static context cannot satisfy
this gate. Activation must fail closed if these checks fail; do not work around
403 with operator credentials in the voice process or mark it ready based on
USB enumeration alone.

After activation, five physical hold/speak/release sessions must produce
transcripts and answers. Repeat after reboot and after audio reconnection.
These checks remain distinct from the automated capture-only test, public
provisioning, and the two-device acceptance demonstration.

## 6. Verify provisioning and open the service gate

Every fingerprint is lowercase `sha256:` followed by 64 hexadecimal
characters and must be computed from a public artifact or evidence bundle—not
from a private key file. Review the exact required arguments first:

```bash
scripts/provision-lantern-unit.sh --help
```

Then run `verify` with Unit A's personal genesis, Comet node, personal
validator, ordinary voice, Commons proposer, TLS/federation, radio and recovery
evidence fingerprints, plus all four explicit attestations. A dry run validates
the complete command without writing:

```bash
sudo scripts/provision-lantern-unit.sh verify --unit-id unit-a \
  --personal-chain-id PERSONAL_CHAIN_ID \
  --personal-genesis-fingerprint sha256:... \
  --comet-node-fingerprint sha256:... \
  --personal-validator-fingerprint sha256:... \
  --voice-identity-fingerprint sha256:... \
  --public-proposer-fingerprint sha256:... \
  --tls-federation-fingerprint sha256:... \
  --radio-identity-fingerprint sha256:... \
  --recovery-evidence-fingerprint sha256:... \
  --attest-identity-unique --attest-recovery-tested \
  --attest-no-cloned-secrets --attest-no-public-validator-key \
  --dry-run
```

Remove `--dry-run` only after checking the values. The script writes the public
inventory atomically and writes `PROVISIONED` last. It refuses to overwrite a
different unit identity, inventory or provisioning marker.

## 7. Start and smoke-test the installed stack

After verified provisioning, enable the two long-running local services and
the one-shot runtime readiness gate:

```bash
sudo systemctl enable --now sage-lantern-llm.service
sudo systemctl enable --now sage-lantern-personal-sage.service
sudo systemctl enable --now sage-lantern-runtime.service
```

The runtime unit is deliberately not a microphone/PTT daemon. It proves that
the installed files, configuration, services and local health endpoints are
ready for the existing prerecorded-WAV CLI. Re-run the check directly with:

```bash
sudo /usr/local/sbin/smoke-test-lantern-unit --require-services-active
sudo /usr/local/sbin/smoke-test-lantern-unit \
  --require-services-active --audio /path/to/known-input.wav
```

The second command must produce a non-empty response WAV under
`/var/lib/sage-lantern/audio/`. Live ALSA capture/playback, PTT, touch, encoder
and MeshCore remain later hardware integration gates.

## 8. Capture the post-install evidence

```bash
scripts/capture-lantern-evidence.sh evidence/unit-a-bootstrap
```

Review the bundle before sharing it. It reports whether the public provisioning
inventory and gate exist, but never copies their private source material.

Successful completion of this runbook means the OS and sterile runtime are
installed, uniquely provisioned, smoke-tested and evidenced. It does not mean
live audio, controls, touch, MeshCore, thermal load or the complete acceptance
loop have passed hardware acceptance.
