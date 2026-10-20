# C-native launch settlement integration

Status: source review blocker, 20 October 2026. Nothing is installed, enabled
or qualified on Unit C. The launch authority remains disabled.

The settlement owner is implemented, but connecting it directly around the
current Python `LaunchOnce` path would overstate what the authority can prove.
The safe increment is to harden the owner's durable-attempt boundary and record
the exact integration blockers before changing the live launch sequence.

## Current authority boundary

The candidate launch path is:

1. `scripts/lantern/guarded-session-authority.py` admits the root bridge.
2. `LaunchOnce.start()` calls `spawn_shell()`, which execs `gnome-shell`
   directly as UID 1000.
3. The authority opens a pidfd and waits for the exact child's `READY=1`
   credential datagram.
4. Seat and native-PIN publication follow only after those checks.

`LaunchOnce` has one-attempt semantics and never retries or terminates a
possibly released session. Those controls remain correct.

The disabled C loader has the stronger stopped-child boundary needed for
release settlement: `spawn_stopped`, exact-child verification, grant
publication, freeze checks and one release call. Its Linux `prepare` operation
still returns `EOPNOTSUPP`.

## Why settlement is not wired around the live exec

1. **The broker is still a source replay, not the fitted Linux broker.** The
   actual `loader.c` sequencing core stops the child, passes one descriptor and
   waits at `record_attempt`; the Linux backend still returns `EOPNOTSUPP` and
   no installed broker exists. `main` can now select the broker path only when
   the capture policy, socket and settlement root are all supplied together.
2. **The capture provider needs a root-owned installed dependency closure.**
   The authority can invoke the isolated capture consumer and validate its
   decision against protected policy bytes and current machine/boot. The
   consumer scripts and policy are not installed or qualified on C.
3. **Release and settlement still need one protected native transaction.**
   Publication and settlement have separate contracts. The native owner still
   needs a serialized anchor that records the failed or uncertain release and
   its settlement with rollback-resistant protection and a trusted owner
   identity.

Each item can hide or manufacture an obligation if ignored. None can be closed
by a timeout, a numeric PID, a fresh boot, a new generation or an unchanged
service file.

## Implemented source hardening

`SettlementOwner.track_release()` now returns an obligation only after
`record_attempt()` reports `attempted`. An unavailable journal, an unconfirmed
read-back, a conflicting record or an already-settled record raises
`SettlementRefused`. A caller cannot proceed merely because it has an
in-memory obligation.

The Linux probe now supports a persistent `child:<pid>:<starttime>` resource.
It distinguishes a running child, an unreaped zombie, an absent child and a
reused PID with a different start time. A live or unreaped process is not
terminal; PID reuse is treated as the original child being absent.

The durable journal now stores the canonical launch binding and exact resource
list. `FileJournalBackend.pending()` returns attempted records and
`SettlementOwner.recover()` reconstructs each obligation and probes it after a
trusted owner restart. Missing or unavailable recovery state returns
`uncertain`; it does not appear as an empty queue.

The C sequencing core now requires a `record_attempt()` operation after the
child is stopped, granted and frozen, immediately before the one release
callback. Failure or cancellation at that point fences the loader and cannot
release the compositor. The Linux backend returns `EOPNOTSUPP`, so the path
remains deliberately closed until a fitted settlement owner exists.

`launch_selection.py` now converts an eligible frozen-consumer decision into an
exact, non-authorizing launch selection. It rejects revoked decisions, reasons,
authority claims, changed policy bytes, wrong machine/boot identity, changed
capture/generation, malformed view digests and non-absolute unit paths. The
root authority refuses to spawn unless a fresh selection provider returns such
a value.

`loader_broker.py` now defines the stopped-child handoff client. It stages one
child, requires exactly one pidfd over `SCM_RIGHTS`, validates PID/start/cookie
identity, calls a durable-record callback, aborts a still-stopped child when
recording fails, and sends `release` only after the callback returns exactly
`True`. A compiled replay implements the server over the actual loader core.

`BrokerLaunchOnce` is now the authority alternative to `spawn_shell()`. It
binds the staged child to the current frozen selection, requires an injected
settlement recorder, preserves the staged pidfd on release uncertainty, and
keeps the daemon's one-attempt/no-restart behavior.

The broker authority also requires a startup recovery callback. It probes every
durable pre-release attempt before `prepare()` can spawn and stays held if any
result is uncertain. A compiled native replay now exercises the actual
`loader.c` core over the broker socket: the child stops, the descriptor is
passed, `record_attempt` waits for the client's release acknowledgment, and an
abort kills only the still-stopped child.

`launch_settlement_recorder.py` binds the staged child identity to the exact
capture selection and calls `SettlementOwner.track_release()` before the broker
may release. `guarded-session-authority.py` accepts an isolated capture policy,
broker socket and settlement root as one required configuration group; when
present it selects `BrokerLaunchOnce`, installs the recorder and runs startup
recovery before serving launch requests.

`settlement_anchor.py` adds an independently stored monotonic seal over the
complete recoverable journal and optional outstanding-release digest. It
detects changed, missing or reordered record sets and refuses generation
rollback in its CAS API. `SettlementOwner` verifies the seal before a new
attempt and advances it after each durable attempt or settlement; recovery
returns `uncertain` if the journal and anchor disagree. The store is still a
private file in source; it is not bound to a separate monotonic hardware owner.

The same source now carries an `OwnerIdentity` loaded from one mode-0600 owner
policy: machine ID, owner ID, executable digest and owner UID. The anchor value
includes the owner-policy digest, and an owner-bound `FileAnchorStore` refuses a
different owner even when the journal bytes are unchanged. The policy loader
checks the exact schema, file identity and expected UID; it does not establish
that the policy is installed from a trusted boot channel or that the owner
binary is currently running. Callers must load the policy from a protected path
and pass that identity through the native owner, never from bridge JSON.

An owner restart now converts a missing, malformed or counter-behind anchor
read into an absent in-memory seal. Recovery then returns
`settlement_anchor_mismatch` and further `track_release()` calls refuse; it
cannot treat the same failure as an empty journal and authorize a fresh launch.

`FileAnchorStore` also accepts a separate monotonic-counter port. The anchor
records the counter value and requires it to match on read; a create or
compare-exchange advances the counter before writing the next anchor. The
default `UnavailableCounter` refuses, so the source cannot accidentally treat
the file itself as a hardware rollback counter. A counter that is missing,
behind the anchor, or fails to advance returns `uncertain`/refuses the write.
`Tpm2NvCounter` supplies an absolute-command TPM2 NV-counter adapter using
`tpm2_nvread` and `tpm2_nvincrement`; it is tested with an injected runner but
has not been provisioned or exercised against a TPM. RPMB and other hardware
owners, NV-index attributes, provisioning and cold-power/rollback drills remain
open.

`native_bundle.py` defines the exact native bundle closure: required broker,
BPF object, policies and libexec modules; fixed modes; byte sizes; SHA-256
hashes; source commit; and rejection of missing, extra, changed or symlinked
files. It neither signs the manifest nor installs the bundle.
`scripts/lantern/stage-native-bundle.py` seals an already-reviewed tree with
that manifest once; it refuses a tree that already contains a manifest, so a
retry cannot silently replace the bundle identity. The stager does not build
the broker, object or policies and does not sign or install the result.

`scripts/lantern/assemble-native-bundle.py` composes an exact 24-file review
tree from a compiled broker, a compiled BPF object, a target-derived GNOME
overlay/library and explicitly supplied policy files. It validates the 13 LSM
sections and the manifest. The
review-only assembly evidence is
`docs/lantern/evidence/NATIVE_BUNDLE_ASSEMBLY_20261020.json`; it used synthetic
policy fixtures and builder-kernel BTF, so it is not a Unit C deployment
bundle.

A read-only Unit C check found kernel BTF `6.1.115-vendor-rk35xx` at
`9392c4db…f15c60` and no `/dev/tpm*` device. Building that exact BTF produced
a 13-hook object at `4f930d4c…427e39`; the corresponding review-only
24-file bundle manifest is `8cb6d887…b440`. The target capture policy is
absent, so that input remains synthetic; the overlay/library and native/guarded
policies are derived from read-only target bytes. The result still cannot be
installed without an authorized capture policy and boot-window review. Evidence:
`docs/lantern/evidence/NATIVE_BUNDLE_UNIT_C_BTF_20261020.json`.

`broker_agent.c` supplies the fitted-agent source around the Linux backend:
it loads digest-bound object/shell/pin inputs, accepts the one staged request,
and forwards `record_attempt` over the same broker socket. The Linux backend
now obtains the stopped child's PID/start-time identity before that callback;
an absent callback remains `EOPNOTSUPP`. The source was compiled successfully
as an ELF64 AArch64 broker in an isolated Linux ARM64 builder with libbpf and
OpenSSL. The Linux early-boot `prepare` still returns `EOPNOTSUPP`; the
binary is a required bundle member but has not been built, installed or
qualified on Unit C.

`scripts/lantern/build-native-bpf.sh OUTPUT [BTF]` builds the disabled
`desktop_guard.bpf.o` from a supplied kernel BTF file. The isolated ARM64
builder produced a deterministic object with all thirteen LSM sections when
run twice from the same inputs. That object came from the builder kernel's BTF,
not Unit C's vendor BTF; it is a source-build check, not verifier, load or
target-kernel qualification.

Run the Linux-host build with
`scripts/lantern/build-native-broker.sh OUTPUT`, or use
`scripts/lantern/build-native-broker-in-container.sh OUTPUT` from the
review workstation. The container path uses the pinned bootstrap image and a
read-only source mount; it does not contact a unit. The package set is part of
the builder image, and the build output is not a kernel or session acceptance
result. On a root Linux builder, `scripts/tests/test_native_broker_build.py`
also compiles and runs the disposable stopped-child replay; it verifies the
actual `/proc` start-time identity and kills the child before release or exec.

`early_boot_admission.py` adds the source early-boot gate: current boot ID,
initial UID mapping, protected policy generation and absence of any UID-1000
process. The authority retains the admission receipt and refuses launch if the
boot changes. The source gate is not yet invoked before every UID-1000 process
by the native boot chain.

`software/systemd/sage-lantern-early-boot-admission.service` is the source
boot-order candidate. It runs the gate as a oneshot root unit before
`display-manager.service`, `getty.target`, `ssh.service` and `ssh.socket`, and
writes a private receipt under `/var/lib/sage-lantern-early-boot`. The guarded
session unit requires that gate and waits for it. When the broker configuration
is selected, the authority now requires `--early-boot-receipt` and re-reads the
receipt at launch preparation. This is still an uninstalled source unit: the
actual systemd graph, service enablement, all alternate login paths and the
first-UID-1000 boundary must be measured on the target.

The read-only Unit C check on 20 October found the native admission and guarded
session units absent, while `graphical.target`, active GDM and active SSH were
present. This is an explicit unbootstrapped target state, not an enforcement
result; see
`docs/lantern/evidence/NATIVE_BOOT_CHAIN_UNIT_C_20261020.json`.

Focused controls in `scripts/tests/test_settlement_owner.py` cover:

- track and settle with the durable file journal;
- missing resources;
- an uncertain durable attempt;
- an already-settled attempt;
- running, zombie, absent and PID-reused child observations.
- durable attempt recovery through a reopened file journal;
- unavailable recovery state remaining uncertain.
- an actual stopped-child loader replay proving `record_attempt` precedes
  release and a callback refusal prevents release.
- strict frozen-capture selection binding and authority refusal without it.
- strict loader-broker framing, pidfd handoff and record-before-release order.
- an authority broker path that cannot be constructed without a settlement
  recorder and preserves the staged pidfd on uncertainty.
- a clean-recovery gate before broker-backed launch and a compiled native
  stopped-child/handoff/release replay.
- an independent journal anchor that detects record-set changes, release
  identity changes and generation rollback at its CAS boundary.
- owner integration that verifies the anchor before attempts and advances it
  only after durable attempt or settlement transitions.
- owner-policy loading and anchor binding that refuses a substituted owner
  identity at the same generation and journal digest.
- counter-bound anchor transitions that refuse a missing, rolled-back or
  non-advancing monotonic counter.
- restart recovery that keeps a counter-ahead/anchor-behind interruption
  uncertain and blocks every subsequent release attempt.
- an exact native-bundle manifest/verification source contract with a fixed
  required file set and metadata closure.
- an early-boot admission receipt requiring a clean UID-1000 namespace for the
  current boot.
- a source oneshot unit ordered before display-manager, getty and SSH, plus a
  protected receipt re-read by the broker-backed authority.

## Required implementation order

1. Turn the compiled replay broker into the fitted Linux broker and install its
   fixed policy, socket and sidecar assets as one reviewed closure.
2. Install and enforce the early-boot admission barrier before GDM or any
   other UID-1000 process reaches the broker.
3. Turn the isolated capture consumer and fixed policy into a root-owned
   installed dependency closure; never accept capture ID, generation or policy
   digest from bridge JSON or user-writable state.
4. Serialize the release-attempt anchor and settlement journal through the
   native owner. Preserve an unresolved attempt across supervisor crash, boot,
   cancellation and protection-store rollback.
5. Bind the integrated anchor to a separate protected monotonic store and
   trusted owner identity. A same-store file alone is not rollback resistance.
   The owner identity source binding is now prepared; the hardware counter and
   installed policy channel remain open.
6. Replay failures before and after each durable write, after child exit, at
   release-callback return, during supervisor restart and with stale or reused
   child identities.

The loader's current source-only and uninstalled status remains unchanged. This
document is an implementation-or-blocker handoff, not launch, session, privacy,
power or physical acceptance.
