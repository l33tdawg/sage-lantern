# Disabled desktop loader and stopped-child handoff

Source candidate only. The Linux `prepare` operation always returns
`EOPNOTSUPP`. The only provided executable parses a BPF object; it has no
attach, activation or session-launch command. Nothing here is installed on C.

`loader.c` is the actual sequencing core exercised by fault injection. It
requires all thirteen hook identities and the single task-storage map to be
pinned and positively attested before it permits one child creation attempt.
It writes that child's grant using its pidfd, reads it back, freezes the map,
checks that a same-value userspace write is denied, then rechecks the stopped
child and all pins, durably records the settlement attempt through
`record_attempt`, and only then requests release. The Linux backend invokes
the broker's injected record callback only after it has obtained the child's
PID/start-time identity; an absent callback still refuses with
`EOPNOTSUPP`. The fitted broker source supplies that callback, while
`prepare()` continues to return `EOPNOTSUPP`, so no unfitted owner can release
the child. Failure fences admission and retains pins. Release uncertainty is
sticky: repeated cancellation never turns it into permission to terminate a
potentially running session.

`linux_backend.c` supplies the libbpf/OS source implementation. It loads from
digest-checked bytes, accepts the exact hook/map shape, rejects existing pins,
and compares reopened root-owned pins with the map and loaded program/link IDs.
The three credential hooks have no grant-map reference; the other ten must
reference the exact fresh map. Filesystem checks walk trusted ancestor directory
descriptors. The compositor executable is held by FD and checked against its
digest and kernel device/inode identity. Userspace `st_dev` is decoded through
major/minor before constructing the kernel's device encoding.

The single-threaded child helper stops while still root, before UID drop and
the first compositor exec. It uses pidfd-based signals and wait operations,
fixed argv, an explicitly supplied environment, no supplementary groups, no
core dumps or new privileges, and descriptor-based exec. The wait is bounded.
Current `prepare` refusal means this path has no live production caller.

Run the portable replay from the repository root:

```
python3 -m pytest -q scripts/tests/test_desktop_loader_candidate.py
```

The source build has two entry points:

```
scripts/lantern/build-native-broker.sh OUTPUT
scripts/lantern/build-native-broker-in-container.sh OUTPUT
```

The first is the Linux-host build. On macOS or another non-Linux host the
second uses the pinned ARM64 builder in
`scripts/lantern/native-broker-builder.Dockerfile`, runs the compile with
network access disabled, and copies the resulting binary to `OUTPUT`. The
builder installs the declared libbpf, libelf, zlib, OpenSSL and C toolchain
packages. It is a source-build check, not a C device measurement and not a
claim that the kernel verifier accepted any program.

The previous isolated ARM64 replay and compiler definition remain under
`work/c-desktop-loader-20261002/build/` in the full evidence checkout. Its
disposable stopped child is killed before UID drop or exec. No compositor,
user session or BPF policy is launched by these checks.

Build the disabled BPF object separately on Linux with
`scripts/lantern/build-native-bpf.sh OUTPUT BTF`. The script requires
`bpftool`, Clang and LLVM, generates `vmlinux.h` from the supplied BTF, and
produces the object without loading or attaching it. Use Unit C's exact BTF
for target qualification; a host-builder object is not a vendor-kernel result.

The root early-boot session admission barrier now has a source oneshot unit
ordered before display-manager, getty and SSH, and a protected receipt that the
broker-backed authority re-reads. Its actual boot graph, every alternate entry
path and installed service state remain unqualified. Qualified
asset/environment and GNOME plan, authenticated activation policy, native
broker/seat integration, vendor kernel load/deny tests and physical acceptance
also remain open. Read `docs/lantern/DESKTOP_LOADER_CANDIDATE.md` before any
integration. There is no installer, enabled unit or activation marker in this
candidate.

## Root-held GNOME evidence reader

`frozen_gnome_consumer.py` adds a disabled, library-only capture consumer.
It walks root-owned ancestry through directory descriptors, holds exact bytes,
rejects writable ancestors/symlinks/hardlinks/special files, and binds the capture
to a root-owned policy and current authority record. Machine ID, boot ID,
capture ID and generation must agree. It checks this consumer and the Python
executable against policy digests and accepts only the three fixed source hashes
of the independently reviewed GNOME contract v3. Those analysis/contract modules
execute from held bytes; no second closure predicate or caller view is accepted.
File identity and bytes are checked again before a positive result.

The entry requires root on Linux with an isolated interpreter and no site loading
(`-I -S`); it rejects a translated UID mapping. The local root depot is its trust
boundary. It does not authenticate a remote producer, create that depot, collect
observations, maintain monotonic generation state or qualify the interpreter's
complete dependency graph. The native supervisor still needs to authenticate
and pin the consumer before loading it, retain/recheck its authority through
release, and enforce all other native admission requirements.

Every returned decision has `runtime_authorized=False`. Existing native
`prepare` remains `EOPNOTSUPP`; this Python library is not wired to it. No command,
service, native helper launch, device probe or activation is supplied. The old
review bundle stays unchanged. Current source and disposable Linux test evidence
are under `work/rook-gnome-root-consumer-20261003/`.

## Mandatory current-capture record

The next source increment additionally requires
`/var/lib/sage-lantern-desktop/gnome-capture/current.json`, selected by the
bootstrap/store rather than the policy caller. Its exact canonical schema binds
active/revoked state, machine, boot, capture identity, generation and the exact
policy path and digest. The record is held through the same trusted ancestry
checks and its identity/bytes are checked again last. Missing, malformed,
revoked, replaced or mismatching records refuse eligibility. The public
`review_capture(policy_path)` signature is unchanged; the mandatory protected
record is an added input requirement. The record is never created by this reader.

This is current-record matching, not an implementation of durable monotonic
publication or protection against rollback of the entire trusted store. Producer
truth/permissions/update serialization, crash durability, bootstrap authentication
and binding through native release remain open. A result is an observation at the
final recheck, not a lease; its descriptors close and its authority must never be
cached for launch. Native `prepare` still refuses. Source/replay evidence for this
increment is `work/rook-gnome-current-generation-20261003/`.

## Disabled publication and revocation prototype

`capture_publication_prototype.py` models sequencing over explicit backend ports.
The default backend refuses every operation. It supplies no storage adapter,
provisioning, authenticated producer, command, service or native integration.
Every returned observation and outcome has `runtime_authorized=False`.

An independently protected anchor is reserved before publication, so an advanced
pending generation fences the earlier record. Revocation advances that anchor
before withdrawing depot bytes. One backend transaction must serialize all
clients through exact-child release; a request selector is never permission.
A durable attempt marker precedes the release callback and blocks retry even
when its outcome is unknown. No operation unpins or kills a child. Missing or
malformed anchors refuse instead of initializing generation zero.

The portable tests use a deliberately synthetic in-memory anchor and thread gate.
They exercise 48 mutation-boundary interruption cases, stale bindings, receipt
failures and concurrent clients. Coordinator replacement tests persisted model
state; it does not simulate a disk, power loss or a real authority restart. A
whole-store-plus-anchor rollback control still accepts old state, identifying
the independent backend qualification that remains open. A crash before any
reservation has persisted cannot manufacture a durable fence; supervisor loss
and recovery admission require the separate qualified native owner and barrier.

Run the source protocol checks with:

```
python3 -m pytest -q scripts/tests/test_capture_publication_prototype.py
```

The existing reader and Linux backend are unchanged, and `prepare` still returns
`EOPNOTSUPP`. Full contract-v3 truth, producer/dependency authentication, complete
bound staging, anchor durability/provisioning, cross-process serialization,
bootstrap recovery and stopped-child attestation remain prerequisites. Evidence
and the next backend requirements are in
`work/rook-gnome-publication-prototype-20261005/`.

## Outstanding launch obligation across captures

A later adversarial self-review found that the original attempt marker was
terminal only within one generation: republishing or revoking replaced its
launch identity. The repaired model carries an exact `ReleaseObligation` in the
independent anchor across publication, revocation, interrupted updates and boot
changes. CAS rejects dropping, replacing or rebinding that obligation. Every
further release refuses while it exists, including a new selector or launch UUID.
Observed callback completion does not prove that the child exited.

There is no settlement or reset port in this prototype. A real native owner
must separately qualify durable lifecycle settlement and protection retention;
fresh publication cannot supply that evidence. All runtime flags remain false.
The separate 51-case instrument retains the original 3 green/48 red controls and
the repaired 51 green/0 red result. The unchanged 31-method/174-subcase suite and
new three-method/20-subcase obligation checks pass. Backend research, the bounded
read-only inventory collector and the unresolved hardware qualification are
recorded in `work/rook-gnome-anchor-review-20261005/`.

## Unconfirmed anchor readback after a committed update

After an exact successful CAS receipt, a missing, malformed or unavailable
anchor confirmation now returns `uncertain/anchor_readback_unconfirmed`.
Previously these failures returned `refused` even when the reservation,
activation, revocation or launch-attempt marker had persisted. A valid but
different readback still returns `uncertain/anchor_readback_mismatch`.
Initial invalid or unavailable state and genuine compare conflicts still refuse.

The correction changes outcome reporting, not stored transitions or release
eligibility. Recovery reads fresh state; an outstanding launch obligation still
blocks another release. Evidence is in
`work/rook-c-publication-review-20261006/`. The backend remains unavailable by
default, all runtime flags remain false and native `prepare` remains disabled.

## Terminal confirmation and cleanup outcomes

Once activation or withdrawal has completed, failed final confirmation returns
an uncertain outcome without a selected capture. Transaction cleanup cannot
downgrade a completed operation or an existing uncertain outcome to a refusal.
Adapters must propagate operation exceptions; if an adapter suppresses one, the
coordinator defensively returns uncertainty instead of an absent result. This
does not qualify the broken adapter.

Known refusals before activation or before the child-release callback still
refuse. The phase audit preserves the exact state, callback trace and release
count for every paired case. Evidence and the complete outcome map are in
`work/rook-c-terminal-confirmation-20261006/`.

## Cancellation and repeated native cleanup

The native sequencing core now rechecks its phase immediately after each
callback. A callback that synchronously holds the loader prevents every later
ordinary callback in that operation. The Linux backend also retains the caller's
release-attempt hint, so later holds and close cannot restore permission to kill
a possibly released child. Cleanup of a child with no release attempt remains
available. Exact source and inert replay evidence are in
`work/rook-c-native-review-20261006/`; live kernel and session qualification remain
open, and the Linux `prepare` refusal is unchanged.
