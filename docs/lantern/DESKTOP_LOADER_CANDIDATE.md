# Desktop root-loader source candidate — 2 October 2026

**Disabled and not deployable. C is unchanged. Whole-desktop lock, alternate
launch closure, exact-child seat access and physical acceptance remain open.**

This extends the frozen `DESKTOP_CONFINEMENT_CANDIDATE.md` source preparation
without modifying its bundle, hooks, launch authority or predecessor handoffs.
New implementation is in `software/security/desktop_loader_candidate/`.

## What was prepared

The native sequencing core enforces this order:

1. Authenticated root preflight; load a digest-bound object and fresh map.
2. Attach and pin exactly thirteen hooks; verify unique link/program IDs and
   the expected map relationships against this loaded object.
3. Permit one newly created child, held before its first compositor exec.
4. Update task storage with the child's own pidfd and exact executable grant;
   read back the grant, freeze the map, prove a same-value write is denied,
   then recheck identity, pins and stopped state before release.
5. Fence on error, cancellation or subsequent attestation failure. Retain pins
   when descriptors are closed. Never adopt a PID, restart an attempt, remove
   predecessor pins or kill a potentially running session after uncertain release.

The Linux backend parses the exact program/map shape and loads from bytes
whose SHA256 has already matched the policy. It verifies root-controlled file
ancestry through directory FDs, pins under a private root bpffs directory, and
binds reopened pins to kernel IDs rather than accepting claims from a file.
The compositor FD is bound to its digest and executable inode/device. Kernel
device encoding is constructed from major/minor, not a cast of libc `st_dev`.

Task-storage userspace keys are pidfds in the
[Linux 6.1 implementation](https://raw.githubusercontent.com/torvalds/linux/v6.1/kernel/bpf/bpf_task_storage.c).
The candidate checks the generic userspace freeze behavior described by the
[Linux 6.1 BPF syscall implementation](https://raw.githubusercontent.com/torvalds/linux/v6.1/kernel/bpf/syscall.c).
The FD-backed LSM link destructor closes its FD; explicit unpin is a separate
operation in [libbpf 1.1.2](https://raw.githubusercontent.com/libbpf/libbpf/v1.1.2/src/libbpf.c).
These primary source readings guide the candidate; they do not attest C's
vendor kernel behavior.

## Validation and limits

The ARM64 source build uses GCC's `-Wall -Wextra -Werror` and libbpf 1.1.2.
Native fault replays run under address/undefined-behavior sanitizers. They
exercise actual sequencing code with failures/cancellation at every operation,
duplicate/mismatched identities, malformed grants, repeat attempts, and sticky
release uncertainty. Backend checks cover filesystem ancestry/symlinks,
digest changes, object shape and expected per-program map references. A
capless, network-disabled container creates one disposable root child, observes
its stop through its pidfd, rejects mismatched child identity, and aborts it
before any UID change or exec. No BPF attachment or real compositor is tested.

The provided inspection executable has no load or launch command. Its Linux
`prepare` callback **always refuses with EOPNOTSUPP**. The source backend's
low-level functions are not an authorized runtime service. Current sequencing
validation is not proof that a future daemon survives every crash or that any
stock session launch path is already gated.

## Integration that remains required

- Build and review the root early-boot admission barrier. All thirteen hooks
  must be attached before **any UID 1000 process** can start. GDM, PAM, SSH,
  user manager and every alternate entry path must obey the same barrier.
  A partial load must leave admission closed; merely ordering one systemd unit
  before GDM does not prove every entry path is closed.
- Bind the native root supervisor, policy file/digest, object, kernel/BTF,
  executable and overlay/library/config assets under one trusted ancestry and
  immutable launch contract. Supply a fixed validated environment. Recheck
  executable and asset metadata through the release boundary.
- Use a single-threaded native supervisor for fork. Keep cancellation and the
  release syscall in one serialized admission loop; do not insert a Python
  `preexec_fn` into the existing threaded authority. The interface does not
  promise thread safety. Review late-disconnect, supervisor death, startup
  timeout, partial-pinning and held-child cleanup with the actual boot gate.
- Connect the new child to the existing native guard only after its exact
  parent/cgroup/environment/session are verified. `READY=1` still does not
  prove logind controller or DRM/evdev access. The existing `spawn_shell`
  immediate-exec path must not be silently used for this candidate.
- Review Ember's GNOME evidence revision and disabled plan: complete ancestors,
  required-unit resolution, extension/D-Bus inventory completeness, manager
  environment, search-path precedence and compositor fork/reexec compatibility.
- Qualify the vendor verifier, attach behavior, map freeze, pinned lifetime,
  descriptor donations, namespace attempts and real deny/allow probes only
  after qualified power and an owner-approved fresh-session window. Existing
  mappings cannot be retroactively revoked. Never load into C's current session.
- Qualify first-frame concealment, kernel input coverage, exact-child seat,
  portrait usability, lock/unlock and sleep/wake with application preservation.
  C must pass before D/E/F replication; final DSI acceptance remains separate.

No activation file, enabled service, installer or device write is supplied.
