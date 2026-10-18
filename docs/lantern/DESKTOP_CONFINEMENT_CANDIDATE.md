# C desktop confinement source candidate — 2 October 2026

**Disabled, compiled source candidate. C's running r17 session is unchanged.**
The whole-desktop lock, alternate-launch closure, root-created child seat access,
and physical sleep/wake acceptance remain open. Power qualification and an
owner-approved fresh-session window remain prerequisites for testing on C.

This follows the frozen [launch and seat review](DESKTOP_LAUNCH_CLOSURE_REVIEW.md).
It does not replace the [original review bundle and handoff](DESKTOP_PIN_GUARD_CANDIDATE.md).
The new evidence is under `work/c-desktop-confinement-20261002/`.

## Why device-path policy is insufficient

C reports kernel `6.1.115-vendor-rk35xx`, active AppArmor and BPF LSMs,
`CONFIG_BPF_LSM=y`, `CONFIG_DEBUG_INFO_BTF=y`, and an 8,025,493-byte kernel BTF
file. AppArmor exposes no D-Bus mediation feature, and its policy parser is
absent. These are bounded read-only observations, not an enforcement test.

Upstream Linux 6.1's AppArmor file-permission implementation accepts a handle
whose file label is unconfined. Its receive hook calls that implementation.
Consequently a path-denial profile cannot be assumed to reject a handle
delegated by an unconfined logind process. The vendor kernel's exact patched
implementation has not been attested. The candidate therefore does not rely
on AppArmor path denials for descriptor-transfer closure.
([file permission source](https://github.com/torvalds/linux/blob/v6.1/security/apparmor/file.c#L560),
[receive hook](https://github.com/torvalds/linux/blob/v6.1/security/apparmor/lsm.c#L465))

## Implemented source boundary

`software/security/desktop_guard.bpf.c` contains thirteen BPF LSM programs.
The device rules inspect character-device major/minor numbers, including
aliases, at open, receive, read/write permission, ioctl, mmap and mprotect.
An application at kernel UID 1000 receives no primary DRM, input, framebuffer
or console access. Render nodes, ordinary devices, serial and NPU nodes remain
outside these seat rules. Raw memory/ports and virtual input are forbidden
even for the enrolled compositor.

The exception uses the leader's **kernel task storage**, populated through its
pidfd by a future root launcher. Another process gets no exception even if a
numeric PID is reused; compositor threads share the leader's storage. The
grant also checks the running executable's filesystem device and inode. No
caller-supplied PID, profile name, environment or JSON claim grants access.
The kernel task-storage implementation resolves userspace map keys through
`pidfd_get_pid` and frees storage with the task.
([kernel implementation](https://github.com/torvalds/linux/blob/v6.1/kernel/bpf/bpf_task_storage.c#L142))

The other hooks reject same-user tracing and signals against the enrolled
task, capability use, credential changes that escape UID 1000, and BPF syscalls
including map operations through an existing descriptor. An enrolled Shell
cannot reexec or create a separate process that inherits its mappings and
descriptors. Threads remain allowed. Root management remains independent.
Every hook preserves a prior LSM denial.

**The fork restriction is an unqualified compatibility constraint.** GNOME
must launch applications through the user manager, and all compositor helper
paths need real testing. This candidate must not be installed as a stock Shell
drop-in. BPF LSM programs require kernel load and attachment; a successful C
compilation does not establish enforcement.
([BPF LSM documentation](https://docs.kernel.org/bpf/prog_lsm.html))

## Local validation and frozen artifacts

The actual hook bodies passed **1,193 userspace decision checks**, covering
transferred/inherited device handles, render-node boundaries, mixed kernel
credentials, PID reuse, missing grants, executable mismatch, tracing/signals,
fork/reexec, saved sender credentials and prior-denial preservation.
This replay substitutes kernel input access; it cannot test the verifier,
real syscall mediation, namespace behavior, logind or the compositor.

The source compiles with warnings treated as errors against C's copied BTF
metadata using Debian clang 14 and libbpf 1.1.2 in a disposable local container.
`bpftool gen skeleton` successfully parses the object. No BPF program was loaded
or attached in Docker or on C. All thirteen hook prototypes occur in C's BTF
with the expected argument counts. The relevant suite passed **305 Python
tests, 145 subtests and 19 JavaScript tests**. The bundle stager checks ELF structure, all
thirteen hook sections and CO-RE metadata, binds payloads with SHA-256, and
rejects asserted live qualification or unlisted/changed payloads.

The review bundle contains source, object, tests and evidence. It supplies no
loader, enabled unit, session selector, activation marker or installer.
All live-qualification and acceptance flags are explicitly false.

## Required next work

1. Build the root loader and launch handoff. Attach all thirteen hooks before
   any UID 1000 session or application exists. Fail closed on partial load.
   Pin and verify links/maps under a root-owned boundary so a daemon exit does
   not detach protection. Bind object, link and map identities to policy.
2. Hold a newly root-created child before its first Shell exec. Use its pidfd
   to write one task-storage grant, verify it, freeze userspace map writes,
   then release the fixed executable. Preserve cancellation and no-adoption
   rules. Do not derive nanosecond task identity from rounded `/proc` times.
3. Close GDM selection, user-manager precedence/environment and GNOME search
   paths; review trusted Shell D-Bus and configuration injection paths.
   A kernel device exception does not prove Shell code integrity.
4. After power qualification and fresh-session authorization, qualify vendor
   verifier/load behavior and real deny/allow probes, including unconfined
   descriptor donors and namespace attempts. Existing mappings cannot be
   retroactively revoked; loading into C's current session is prohibited.
5. Qualify the exact child's logind controller and device access, first-frame
   concealment, kernel input coverage, portrait UX, sleep/wake and application
   preservation. D/E/F remain held until C acceptance.
