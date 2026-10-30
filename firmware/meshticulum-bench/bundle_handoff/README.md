<!-- SPDX-License-Identifier: Apache-2.0 -->
# Finite bundle handoff harness

This Lantern-owned harness coordinates a single explicitly approved public
Meshticulum file between two endpoints. It retains one management process and
one SDK OS device owner per endpoint across parts, while each part receives
fresh authority, worker, journal, session and cipher state. Both previous part
sessions must drain, disarm and retire before advancement; the next receiver
must be ready before its sender starts.

These files began as a verbatim source copy of the frozen 30 October 2026 bundle
handoff harness. The tracked batch adapter and its tests now add the future
failure diagnostics described below. The tracked reservation estimator also
matches the SDK's exact `math.ceil(estimate * 1.10) + 1000` arithmetic, including
floating-point rounding, with packet-length coverage in `test_airtime.py`.
The frozen execution sources remain unchanged. Its preparation, staging and accounting helpers descend from
the earlier alpha.3 qualification harness. Source ancestry and inherited
`alpha3` schema names confer no authority from that historical campaign.
Historical scopes, journal debt, uncertain writes and incomplete historical
actual cost remain distinct and retained. This folder contains no configuration,
grant, secret, device receipt, payload or execution result. It has no automatic
entry point, default approved configuration or retry campaign.

## Exact SDK dependency

The original handoff candidate was public SDK commit
`541a3796454291b1d8cb573b5e5e60dd4fc4d1ac`, with candidate wheel SHA-256
`cab6694a2004916af169b929b55bc7e3acd5c0971f8af1210428ffbabf346f1d`.
The wheel metadata remains `meshticulum==1.0.0a3`; the version string alone does
not identify these newer source bytes or supply their physical qualification.
Keep the wheel, both checkout examples, all installed runtime files and the
per-host dependency inventory pinned separately.

This harness consumes the SDK's `radio_bundle.transfer_batch` API. Generic
crypto, radio workers, flow and byte-bundle verification remain in Meshticulum;
management, grant preparation, installation and device execution remain in
Lantern. The harness's exact candidate requirement is separate from Lantern's
other integration source lock; copying this directory does not update that lock
or qualify another application runtime.

The opt-in eight-frame and 26-phase timing adapter below targets public SDK
commit `badb99b2d48781434166cd1a195e964faa74c0f0`. Its locally built candidate
wheel is 300,787 bytes, SHA-256
`d3ec16c70ab07c66685f8bab32acf18bbc718e1ea104018a6d7b24162be564de`.
All 71 runtime Python files match that checkout. This identifies source inputs;
it supplies no hardware or throughput qualification and does not replace the
historical wheel identity above.

The R11 selective-repair candidate targets public SDK commit
`bb5b2bcb1f827ca7fe5a8c68f5f5b158c33cc24a`. Its separate wheel is 301,764 bytes,
SHA-256 `a7dd6ba38063781d20ecb424ec1dc7337eb2b70f2430db8611221a49b1257e74`;
all 75 runtime files, including 71 Python files, match that checkout. R11 retains
the current retry interval after a selective fast repair; ordinary timeout
repairs retain exponential backoff. The prior R10 eight-frame hardware sample
completed at 801.42 useful B/s across sender startup through authenticated
receipt. That result remains attributed to its original source and wheel. R11
hardware throughput remains unmeasured.

## Source inventory

| Files | Responsibility |
| --- | --- |
| `controller.py`, `campaign.py`, `common.py` | Explicit local preparation, admission estimates, immutable scopes and retained accounting |
| `file_run.py`, `transport.py` | One finite file coordinator, bounded management pipes, readiness and both-endpoint retirement barriers |
| `remote.py`, `remote_file_body.py` | Pinned remote setup, original remote clock binding, child supervision and terminal status |
| `sdk_entry.py`, `sdk_batch_entry.py` | SDK observation and bounded part/control adapter; final iterator exhaustion checks device closure |
| `small_fixture.py` | Signed public bundle fixture with explicit part geometry |
| `install_environment.py`, `verify_environment.py` | Isolated, pinned installed-wheel environment and runtime inventory verification |
| `test_file_run.py`, `test_sdk_batch_entry.py`, `test_airtime.py` | Local modeled coordinator, pipe, SDK lifecycle, refusal and exact airtime/reservation controls |

Apart from those tracked diagnostic and reservation changes, copied Python sources remain unchanged so their hashes can be
compared directly with a frozen prepared run. New preparation must pin the
updated batch adapter and estimator independently; they cannot inherit the old run's artifact
identity. Run preparation copies the runtime harness into its own new directory
and records each source hash.
Execution must use that prepared copy; editing the tracked sources cannot
silently alter an already prepared operation.

## Prepare, stage, issue and run separately

A new operator-supplied configuration must pin a clean exact SDK checkout,
candidate wheel, compact firmware-source identity, dependency lock, endpoint
identities, protected management channel, fresh radio/clock observations and
retained historical evidence. It must also define the finite original window,
ordered parts, role-specific operation limits and full file allowances. The
configuration schema is checked in `controller.prepare`; no live template is
included here. A fresh preparation fails if its pinned inputs, native headroom,
full reservations or remaining ACK-grace budget do not fit.

The following shows the action boundaries, using paths supplied by a separately
reviewed operation. `SOURCE_TEST_PYTHON` names an isolated interpreter with the
candidate dependencies; `MESHTICULUM_SOURCE` names its clean pinned source.
Preparing a source artifact does not authorize the later actions.

```sh
PYTHONPATH="$MESHTICULUM_SOURCE/src" "$SOURCE_TEST_PYTHON" \
  firmware/meshticulum-bench/bundle_handoff/controller.py prepare \
  --config "$APPROVED_CONFIG" --output "$NEW_RUN_DIRECTORY"
```

`prepare` is local. By default it creates fresh random useful bytes. An optional
`useful_input` object containing exactly `path` and `sha256` can select a pinned
regular file of exactly `useful_bytes`, from 1 to 1,000,000 bytes, for a matched
workload. Explicit null, aliases, changed files and hash or size mismatches are
refused. Preparation always creates a fresh signed pack identity and key;
issuance still creates fresh sessions and grants. Reusing useful bytes carries
no authority or performance evidence from the earlier operation. Preparation
verifies exact response geometry, pins source/artifacts and freezes the run directory.
It does not install anything, claim a grant or open hardware.

```sh
"$SOURCE_TEST_PYTHON" "$NEW_RUN_DIRECTORY/controller.py" stage \
  --password-stdin < "$PROTECTED_PASSWORD_FILE"
```

`stage` is a separate bounded remote installation into the prepared private
runtime directory. It verifies the normal isolated wheel environment and exact
runtime inventory; it does not ARM or transmit. Credentials enter through stdin,
never a command argument or copied configuration. Keep operational inputs and
outputs outside the source tree and protected from other users.

```sh
"$SOURCE_TEST_PYTHON" "$NEW_RUN_DIRECTORY/controller.py" issue
```

`issue` checks the prepared and staged records, original campaign clock, native
headroom and full reservation binding before creating fresh local grants and a
scope. Read the exact resulting scope digest for the next action. A live earlier
scope or an already consumed scope is not reusable. Issuing is not radio
execution and does not start the following command automatically.

```sh
"$SOURCE_TEST_PYTHON" "$NEW_RUN_DIRECTORY/controller.py" run \
  --scope-sha256 "$ISSUED_SCOPE_SHA256" \
  --password-stdin < "$PROTECTED_PASSWORD_FILE"
```

`run` consumes that exact scope once and may ARM/transmit. It creates no successor
scope, retries no failed campaign and resets no device or debt. Reliable-flow
retransmissions remain possible only within the original per-part authority and
budgets. A missing, failed, fenced or uncertain endpoint stops progression.
Full file allowances remain reserved; actual paid journal spend is recorded
separately. Original epoch and monotonic bounds both remain in force, including
across management messages and part transitions. Wall rollback, re-entry or a
new process cannot extend the file window.

## Frozen SF5 plan and its limits

The frozen source selects LoRa **SF5/BW500 kHz, CR4/5, preamble 12**, at
921.375 MHz, sync word 0x42 and requested chip power 0 dBm. It is the fastest
LoRa profile supported by the selected public SDK and recorded firmware source.
It still requires exact current waveform/power admission. The unchanged queue
policy is depth four, 5 ms inter-packet RX and 100 ms reverse listening; USB
write pacing remains 64 bytes/2 ms.

New input configurations can explicitly set `train_depth: 8` for the negotiated
scalar eight-frame source candidate. Omission preserves the exact four-frame
policy. Eight selects the public SDK's exact v2 policy, with `max_depth` and
`train_frames` both eight; preparation checks identical SDK policy bytes, and
the original scope and every part grant must bind that same selection. A
baseline input cannot select eight. Partial policies, changed guards and
four/eight scope or grant mismatches are refused. This source option requires
matching new firmware capability and fresh build, install and RF qualification;
it does not widen historical grants or certify a throughput result. Hold times,
USB pacing, per-boot caps and all retained debt remain unchanged.

A separate source option combines `comparison_mode: "queued"`, explicit
`train_depth: 8` and `flow_window_frames: 32`. It selects the exact v3 policy:
32 logical outstanding/reassembly slots with an eight-frame physical train.
The SDK owner binds a 6,656-byte window (32 × the admitted 208-byte frame bound).
The native queue, eight-frame ACK turn, 5 ms/100 ms receive guards, original
deadlines and operation allowances stay unchanged. Omission retains the prior
v1/v2 policy bytes; null, other widths, missing explicit train eight and baseline
selection are refused. Preparation requires the exact pinned SDK to expose
`policy_for_flow_window(32)` and return identical bytes. An older SDK still
supports the unchanged default selections but refuses v3. The prepared record,
original scope and every part grant bind the selected policy; a v2/v3 mismatch
cannot widen it. This source option requires its own candidate pins and finite
qualification and makes no hardware throughput claim.

The associated cold plan is **50,000 useful bytes, five 10,000-byte parts,
A→B, order 4, 2, 0, 3, 1**, with host profiling enabled. It proposes a fresh
180-second original file window and 60-second per-part radio leases. Sender
operation caps are **84 attempts / 4,000,000 estimated airtime µs per part**,
with **420 / 20,000,000 µs** reserved for the whole file. Receiver caps are
**28 / 1,000,000 µs per part**, with **140 / 5,000,000 µs** reserved.
Grant/cipher frame bounds and frozen scheduler ledgers remain independent.

Historical native counters of A 300 and B 871 attempts would leave B only
13 attempts after the full receiver allocation against the 1,024-attempt cap.
Fresh preflight must establish the actual same-boot counters and all old debt;
these historical numbers are not permission or a promise that this plan still
fits. No retry, reboot, reset or automatic allowance increase follows a refusal.
The native 300,000,000 µs airtime ceiling also remains cumulative.

The source model gives 56 DATA frames per part. Its ideal guarded useful rate
is about 2.821 decimal kB/s, before host/setup delays, underfilled queues or
retries. This is not a measured result. The earlier alpha.3 100,000-byte run
used B→A, SF7, larger parts and separate children; this new waveform, direction,
workload and lifecycle cannot form a paired speedup claim against it.

The 50 kB trial exhausted the receiver's 28-ACK cap before completing a part.
A separate 5 kB trial with a 20-ACK cap completed one part, then exhausted its
next receiver cap before the final DATA arrived. A nine-DATA source regression
in Meshticulum reproduces that starvation under injected loss: the receiver
verifies the bytes but cannot send its final ACK. The same faults complete
with the original 64-ACK allowance. A cold arithmetic fit does not establish
loss-recovery headroom; retain every failed trial and reserve the complete
chosen allowance before issuing new authority.

The subsequent 5 kB SF5 B→A run used 64 receiver ACK attempts per part and
completed all five parts in order 4, 2, 0, 3, 1. Its four receipt-to-next-entry
gaps totalled 995,923 µs; receipt-to-next-DATA gaps totalled 2,377,247 µs.
Eight DATA retries stretched sender startup-to-final-receipt to 37.339657 s,
or 0.133906 useful kB/s. This small result establishes neither 2–3 kB/s nor
the larger file's recovery. The public SDK's dated benchmark record retains
the exact candidate, all three trials and their measurement limits.

For handoff, retain same-sender monotonic receipt-to-next-part-entry and
receipt-to-next-first-DATA boundaries. Also retain whole-file startup through
the final authenticated receipt, publisher/whole-file verification, and original
scope issue through both supervisors closed. Host phase aggregates are inclusive
and overlap; do not sum them into latency. The goal of under one second total
across four handoffs remains a measurement target, not a source-test result.

## Future failure diagnostics

After the SDK iterator and session cleanup unwind, a failed batch makes a
best-effort local write of `SDK_BATCH_DIAGNOSTICS.json`, even if its parent has
stopped reading the result pipe. This is a new tracked-source facility, not a
file produced by any RF execution using the older frozen adapter (SHA-256
`70436b7d3e5cddd9cfddae3a7358a21d5a5f05360c81cb9888e70e49d60f8c45`).
These diagnostics have not been used in that RF artifact. They open no hardware, retry no
transfer, renew no authority and do not alter the original failure exit status.

The adapter retains the original private directory identity before SDK work.
Diagnostic publication requires the same effective-user-owned `0700` directory,
rejects symlink path components and creates a new `0600` regular file using
`O_EXCL` and `O_NOFOLLOW`. It checks the file inode and single link, then syncs
the file and directory. An existing file, symlink or hardlink is never replaced.
Failed cleanup may leave an incomplete diagnostic entry; its presence alone
does not establish a durable complete report or transport cleanup.

The fixed schema is `meshticulum.sdk-batch-diagnostics.v1`. It binds the original
scope hash and endpoint unit and retains bounded error types, part boundaries,
numeric flow/journal totals and host phase aggregates. It excludes argument
vectors, runtime paths, packet bodies, credentials and raw event traces. Only
an exact SDK `WireError` may contribute a refusal code, restricted to 1–100
lowercase letters, digits or underscores. Arbitrary exception messages are
never copied. Error trees are limited to 64 nodes and the JSON to 1 MiB.

Diagnostic work has a separate 100 ms monotonic budget and interrupting alarm;
it refuses to replace an existing alarm. It makes no new filesystem calls after
that budget expires except best-effort descriptor closure. OS scheduling or
uninterruptible kernel I/O cannot supply a hard real-time guarantee. Publication,
fsync, pipe and cleanup faults all preserve the original failure status. A
missing report means diagnostics were unavailable; it is never zero debt or
successful cleanup. Original journals and endpoint closure evidence remain the
accounting authority.

The current source additionally retains fixed ACK-effect and completed-ACK
snapshot counters from the SDK flow report. Its `ack_events_retained_tail`
contains at most 64 ACK metadata rows selected from the SDK's 512-event tail:
local host timestamp, fixed effect/snapshot/wait labels, canonical ACK integers,
retired-frame delta and local burst occupancy. It excludes packet hashes,
request IDs, payloads and arbitrary reason strings. Older or missing ACK fields
remain absent; the tail is not a complete event count. Ordinary endpoint results
already retain their generic flow snapshots. This adds no timing collector or
clock reads and does not change the radio/session path. Cross-endpoint host and
MCU timestamps still cannot prove a collision without a valid common clock
basis or independent RF observation.

`final_ACK_reannouncement_budget_unavailable` records a conservative skip of the
optional final-ACK repeat when attempt, airtime, ledger or retained-handle
headroom is unavailable. The skip spends no new attempt and supplies no extra
authority. The failure projection now retains this fixed counter too. That
projection-only addition was made after the `run-00-recovery` harness was
frozen; its exact adapter hash and source copy remain unchanged. Ordinary full
flow snapshots already retain the counter, so the frozen run's reports need no
retroactive rewriting.

## Source controls

From the Lantern root, with the exact public SDK source available:

```sh
PYTHONPATH="$MESHTICULUM_SOURCE/src" "$SOURCE_TEST_PYTHON" -m pytest -q \
  firmware/meshticulum-bench/bundle_handoff/test_file_run.py \
  firmware/meshticulum-bench/bundle_handoff/test_sdk_batch_entry.py \
  firmware/meshticulum-bench/bundle_handoff/test_airtime.py
```

These tests use synthetic authorities, temporary files and local Python pipe
children. They do not SSH, open devices or establish radio delivery. They cover
mode/window bindings, role limits, debt completeness, ordered coordination,
control faults, finite process cleanup and iterator closure. Physical evidence
must remain a separate artifact tied to exact installed bytes and a consumed
finite scope; source test success cannot replace it.


The failure projection also retains fixed ACK-effect counters and at most 64
ACK metadata rows from the SDK's last 512 events. It excludes packet hashes,
request identifiers, payloads and arbitrary error text. Optional scalar
`fast_retry_offers` and `fast_retransmits` distinguish logical offers from actual
handover when supplied by the reviewed SDK revision; neither proves receipt.
A missing field stays absent. New preparation pins this updated adapter. The
first 3 kB ACK-policy trial used its earlier frozen copy, before the budget-skip
and selective-retry counter projection additions; full success snapshots already
retained their own SDK fields. The later 2 kB trial used this exact batch adapter, with its own prepared artifact record.


The selective-recovery trial used SDK commit
`5431846ee47a702a07994c1b3456c7da01776b20`, unpublished wheel SHA-256
`d8ec8d20f59e783d9f7be4a69bb70a4d7b7ed7ccebe0b05ff1fce89a3ddd6438`,
and batch-adapter SHA-256
`a37f69ae50b4e2fd1681ad210b628624b09f3d68c198ff87793f86afb626e3b5`.
It completed one 2,000-byte file in 2.554067 seconds from sender process launch
to authenticated receipt, with one selective retry submitted 16.725 ms after
SACK proof. All 72 runtime files per isolated environment matched the candidate;
both endpoints exited zero and closed owned hardware. All 22 physical attempts
and paid debt reconcile. This is one small file with one repair, not a paired
profile speed comparison or sustained capacity result. The public
[recovery record](https://github.com/l33tdawg/meshticulum/tree/main/benchmarks/2026-10-30-sf5-recovery)
retains exact boundaries, the earlier 3 kB ACK-policy trial and source controls.
The published alpha.3 artifacts and other Lantern integration locks are unchanged.

The logical-window32 candidate targets SDK commit
`e43320ec3767c7aaabe64cf46af5116a2f2993cc`. Its separate wheel is 302,151 bytes,
SHA-256 `83cba2892003df14eca5ec53cfd69b0cfed5711ba320183743b486d890246fd0`;
all 75 runtime files, including 71 Python files, match that checkout. This
identifies the source inputs for the first window32 hardware sample: the same
23,900 useful bytes completed at 1,276.92 B/s in 18.716954 seconds including
sender startup. All attempts reconciled and both endpoints closed. Loss positions
and ACK losses differed from the earlier policy; repeated capacity and the
3,000 B/s goal remain unqualified.
The matched R11 eight-frame sample completed at 1,053.28 useful B/s including
startup with different loss from the earlier sample. Neither reached 3,000 B/s.

## Explicit selective-repair fill candidate

A new input can set `selective_repair_fill: true` only together with queued
mode, explicit `train_depth: 8` and `flow_window_frames: 32`. It selects the
SDK's exact v4 policy through `policy_for_repair_fill()`. Missing support or
changed policy bytes refuse preparation; false, null and coerced values are
invalid. Omitting the field preserves all existing policy selections.

The SDK candidate can fill the remaining positions of an eight-frame train
with new DATA after one selective repair when current canonical credit permits.
The original scope and every part grant bind that option. This adds no retry
opportunity, credit or attempt/airtime allowance, and retains native listening
floors, finite deadlines and debt. The earlier v3/window32 sample measured
1,276.92 useful B/s; the first v4 sample is recorded below.

The initial source-only repair-fill candidate used exact SDK commit
`530fe62df03c3d235f04c892b23966100d659f51`, with a 302,742-byte wheel,
SHA-256 `b3cef9b777a5757aedb30539e01a78a271e9931bf32780cfa24ecccf78290f9a`.
Review found that a final new DATA handover could cross another frame's retry
deadline and admit that retry into a partial repair train. That candidate was
not run on hardware; its source and wheel remain retained.

The corrected candidate is SDK commit
`79cbcd7e3b6c892fc2c3372d5aa474fe58419d02`, with a 302,855-byte wheel,
SHA-256 `7c043840a72f1912f5caf8f1d869c3086fe3d0a8cacc5974ec5dae8123e274e7`.
All 75 runtime files, including 71 Python files, match that clean checkout.
An active partial repair train now takes the existing reverse hold after its
final new DATA. Independent direct and deferred handover controls reproduce
the old defect and pass with the correction. The source model and tests do not
establish physical performance for v4.

The first matched v4 hardware sample then completed the same 23,900 useful bytes
in 19.537686 seconds from sender process startup through authenticated receipt,
or 1,223.28 useful B/s. All nine recorded repair-fill turns carried a selective
repair followed by seven new DATA frames. This sample needed 13 DATA retries,
versus 10 previously, with different loss positions; it does not isolate a
causal speed change. Both endpoints drained and closed, all paid attempts
reconciled, and the original scope expired. The 3,000 B/s goal remains open.
