# Lantern coordinated public LoRa/IP pilot

This runbook belongs to **Lantern**, which depends on the separate [Meshticulum library](https://github.com/l33tdawg/meshticulum/tree/a89c1ca6fdf1865d61367cf3a8d59ac1ab50cb39). Native Linux jobs, private application policy and fleet management are implemented in [`software/meshticulum-integration`](../../software/meshticulum-integration), under `lantern_meshticulum.bench` and `lantern_meshticulum.ip`. Runtime provisioning/deployment tools live in [`firmware/meshticulum-bench`](../../firmware/meshticulum-bench). Meshticulum contains the generic transport/resource primitives and imports no Lantern implementation.

The [generic TCP worker](https://github.com/l33tdawg/meshticulum/blob/a89c1ca6fdf1865d61367cf3a8d59ac1ab50cb39/src/meshticulum/ip/worker.py), [radio worker](https://github.com/l33tdawg/meshticulum/blob/a89c1ca6fdf1865d61367cf3a8d59ac1ab50cb39/src/meshticulum/bench/serial_worker.py) and [MR1 codec](https://github.com/l33tdawg/meshticulum/blob/a89c1ca6fdf1865d61367cf3a8d59ac1ab50cb39/src/meshticulum/resources/resource.py) supply carrier and verification boundaries. This document grants no device window, private-radio permission, SAGE admission or production activation.

The native public programme now has both a recorded C→A LoRa→B IP request/reply and a later successful direct C↔B failure/resume campaign. That later campaign kept the first verified piece through TCP loss, admitted fresh LoRa request/reply for the missing piece, sampled a currently faster IP path and verified a fourth piece after returning to IP—all within one original expiry. The earlier 90-second lease refusal remains a separate failed campaign. Our performance qualification, sourced published-comparison review, private native ownership/consent, private transport and release gates remain open. Historical execution, source preparation and read-only preflight establish no fresh RF handoff.

The [public parent accounting contract](MESHTICULUM_PUBLIC_PARENT_CAPS.md) records the next launch prerequisite: stable handoff-wide prior-debt binding, full-pair prepayment and independent native one-use claims. Its standalone accounting preparation does not yet wire or qualify controller/provisioner/radio/IP enforcement. New performance preparation remains held on that integration and current native facts; old campaign IDs, grants and handbacks supply no replacement authority.

Before radio activation, obtain a fresh finite device handoff and inspect current hardware, firmware, boots, exclusive ownership, power/region/antenna records and service lifetimes. Confirm the native authentication needed on every participating host. Historical releases and benchmark pins are location hints. A report of no visible radio holders is one observation; the actual worker must still acquire its exclusive owner.

Use synthetic signed public resources and independent adjacent-link keys. Private SAGE providers remain disabled. Root privileges, an artifact hash, successful preparation and a current dispatch lease establish different facts.

## Recorded successful native resume and IP return

The [2026-10-10 successful resume evidence](evidence/meshticulum-native-resume-20261010.json), schema `lantern.meshticulum.native-public-resume-qualification.v1`, records a fresh campaign in the **11:20–11:35 UTC** device window. Its execution identities are:

| Identity | Recorded pin |
| --- | --- |
| Meshticulum core | [`a89c1ca6fdf1`](https://github.com/l33tdawg/meshticulum/commit/a89c1ca6fdf1865d61367cf3a8d59ac1ab50cb39) |
| Lantern runtime code | [`379025e17761`](https://github.com/l33tdawg/sage-lantern/commit/379025e177619ee3ffd51ef8d5ae0b2bc27dd54e) |
| Lantern documentation head at execution | [`7d2b45e63e5e`](https://github.com/l33tdawg/sage-lantern/commit/7d2b45e63e5eb92ba058af7dfc736472ceb601cc) |
| Host runtime SHA-256 | `856570c771f1377630c8456774a33f55660fe787af9336f2164d55ae9d10561f` |
| Bundle manifest SHA-256 | `15f64943b82546e1c338546dd4fbd9318fd69e945a1064687586e5a00abfc819` |

One original **160-second resource expiry** covered the complete sequence:

1. C's first TCP request/reply produced an independently publisher-verified **977-byte piece** from B's configured public store.
2. The next reply was interrupted after **512 received stream bytes**. Its receive journal fenced; the partial reply was neither durable nor cached. C rechecked and retained the first verified piece.
3. Fresh, independently admitted **C↔B LoRa request/reply** recovered the missing second piece. C verified the original request binding, signed manifest/publisher and complete chunk before accepting it. Old sessions and their debt were not reused or refunded.
4. A fresh IP sample on the third piece was locally faster than the completed LoRa transaction. The controller returned to IP for the fourth piece and verified that piece independently.

The four distinct **977-byte pieces totalled 3,908 useful verified bytes**. All four publisher-verification receipts retained the same original expiry and occurred before it; the expiry was not extended. The first piece remained reusable and was not retransferred. This was **10 sessions and 20 terminal, retired native children**, with fixed lease sizing and owned management reuse actually executed at the pins above.

The resumed LoRa piece took **54.349727 seconds** from original request to independent verification; the fresh IP sample took **9.529958 seconds**. These are native service transaction timings including controller/SSH/preparation and final-radio-ACK intervals. They support this campaign's measured path decision, not sustained carrier capacity, one-way latency, a general speedup from SSH reuse or a matched MeshCore/Reticulum benchmark.

The final read-only C/A/B audit found **10 retired C children, 10 retired B children and zero A children**, no visible radio holders and persistent debt matching terminal receipts. Radio children were disarmed and their attempt journals unfenced. **33 charged radio attempts** retained **2,233,231 microseconds of calculated airtime reservation**; actual RF airtime, energy, EIRP and kernel retransmission bytes remain unavailable. Production service lifetimes were unchanged; all **three owned management masters were terminal**. C's handback was sent at **11:28:51 UTC**, inside that window; its authority is not reusable.

Existing MCU firmware remained in place: **no firmware flash and no private production enablement**. The result does not prove full deployed-firmware/source equivalence or private owner/consent and transport qualification. Our performance qualification, published-comparison review, distributed-artifact notice/source/relinking closure, clean public history and public-alpha/release requirements remain open.

## Historical native mixed path and failed resume attempt

The [2026-10-10 native mixed evidence](evidence/meshticulum-native-mixed-20261010.json), schema `lantern.meshticulum.native-mixed-public-qualification.v1`, records the executed milestone and the subsequent failed resume campaign. The qualified code/artifact identities remain Meshticulum [`6605ba714ce4`](https://github.com/l33tdawg/meshticulum/commit/6605ba714ce414445673b40bfcfe93fe467b86aa), Lantern [`a41851a43010`](https://github.com/l33tdawg/sage-lantern/commit/a41851a4301008bd840a8508187a59a150eeaa0e), and runtime SHA-256 `d3194a92917056818dc58b6c7fce4f1320b37c4d6a16acaa9bc7512f593480a9`.

The mixed request/reply used **four independent sessions and eight terminal, retired native children**. A forwarded the bytes actually received from C to B; B served the received request from its verified public store; A returned the response actually received over IP to C over LoRa. C's independent original-request, publisher/manifest and whole-chunk checks accepted **977 public bytes**. This is a finite public-piece mixed-path result, not a complete pack, sustained RF capacity or matched upstream benchmark.

The transaction took **89.067811 seconds**, including SSH/controller gaps and final-radio-receipt intervals. Its **21 charged radio attempts** reserved **1,174,235 microseconds of calculated airtime** across DATA/ACK attempts; all radio children reported disarm and all attempt journals were unfenced. The reservation is an accounting estimate, not measured RF airtime. The one small transaction's elapsed time is not a link-capacity or matched-performance measurement.

The **separate fresh resume campaign** first verified a direct TCP piece, then interrupted the next reply after **512 received stream bytes**. The first verified piece survived; the incomplete reply did not enter the verified cache. Fresh LoRa admission was attempted under that campaign's original **160-second expiry**, but B refused the new radio receiver **before ARM/TX** with `HardwareError:radio_authority_expired`: the fixed **90-second lease** could not fit about **85 seconds remaining**. The failed receiver made zero radio attempts. C's paired child was prepared but never started. Preserve this deadline-protecting refusal as a failed resume outcome; the missing piece was not resumed over LoRa and faster-IP return was not qualified.

Final read-only native audits covered **18 retired children**, **17 started processes confirmed terminal**, and the one unused C child across these two campaigns. No visible radio-device holders remained; persistent debt and production service lifetimes were unchanged. These observations accompany the terminal/disarm/retirement evidence, rather than replacing worker ownership checks. C's handback was sent before **10:24 UTC on 2026-10-10**; that completed window grants no future dispatch authority.

The first B audit comparison included management-envelope metadata absent from the native result file. A second read-only comparison normalized those two metadata fields; both audit attempts remain retained. The audits changed no grants or databases and extended no deadlines.

At that earlier checkpoint, the lease-sizing correction was **Lantern source only**, with **22 focused source controls passed** and a separately recorded integration run. It clips new leases to remaining original resource time minus a **15-second native setup reserve**, preserves waveform/power/attempt limits and original expiry, and refuses a duration below the admitted minimum before preparation. The later successful resume campaign above executed the correction at its new code/runtime pins. It does not overwrite this failed campaign or renew its spent authority; another run still needs a fresh finite window and original deadlines/debt preserved.

Private native owner/consent and protected application admission, our performance qualification, review against published MeshCore/Reticulum figures, actual RF airtime/energy/EIRP, license/source/relinking review, clean public history and release gates remain open. Keep failed and unavailable measurements alongside the successful piece.

## Recorded native TCP prerequisite

The [2026-10-10 native evidence](evidence/meshticulum-native-tcp-20261010.json), schema `lantern.meshticulum.native-public-tcp-qualification.v1`, records this finite public scope:

| Identity | Qualified execution pin |
| --- | --- |
| Meshticulum core | [`6605ba714ce4`](https://github.com/l33tdawg/meshticulum/commit/6605ba714ce414445673b40bfcfe93fe467b86aa) |
| Lantern integration | [`a41851a43010`](https://github.com/l33tdawg/sage-lantern/commit/a41851a4301008bd840a8508187a59a150eeaa0e) |
| Host runtime SHA-256 | `d3194a92917056818dc58b6c7fce4f1320b37c4d6a16acaa9bc7512f593480a9` |
| Bundle manifest SHA-256 | `5805dec36183d469c484f45e24cf5491032758d297e4f801ef517d09015d85df` |

C sent an authenticated MR1 request to B. B prepared its reply from the actual received request and configured verified public store; C independently checked the original request binding, signed manifest/publisher and complete chunk before caching **977 useful verified bytes**. This is public-resource acceptance, with private admission and SAGE import both false.

A second request/reply used separate child sessions under the same original **160-second resource expiry**. C's admitted response socket was interrupted after **512 received stream bytes**, including transport framing. Both response peers reported failure; C's receive journal was fenced, the partial object was neither durable nor cached, and the earlier verified piece remained unchanged. Failed stream-byte reservations stayed charged.

The final campaign comprised **four TCP sessions and eight native children**. Actual pidfd-backed receiver readiness and stable native service lifetimes were checked. All eight children were retired, preserving their journals and debt. A later read-only audit, after expiry, checked all twelve owned run directories across this campaign and the prior failed campaign: retirement markers, unchanged grants, persisted byte debt and wire-session retirement matched the terminal receipts. These observations are tied to this execution and its original deadlines; they are not reusable authority for another run.

The single request-to-publisher-verification interval was **19.443863 seconds**, measured on C and including SSH/controller gaps. Its arithmetic rate, about **50.247 B/s**, describes this tiny-piece service transaction. It is not TCP link capacity, RF goodput, one-way latency or a matched benchmark. Kernel retransmission bytes, measured RF airtime, energy and EIRP remain unavailable.

In that earlier TCP-only campaign, C's SPI/radio was never opened or armed. B's runtime inspection queried only disarmed HELLO/STATUS; its record contains zero RF ARM and TX commands. That prerequisite alone does not qualify a mixed route or radio resume; the later evidence above supplies those scoped public results. Our performance qualification, published-comparison review, private native ownership/consent and transport, and public-alpha requirements remain open.

Two earlier attempts remain in the evidence rather than being counted as this campaign's success: C's legacy state directory had mode `0755` and was refused; a later reply setup timed out because short-circuit polling starved the sender's sudo prompt. The controller now polls every owned handle, including a sender waiting for authentication. Those failed attempts retain their own outcomes and context; they are not erased or folded into the final common expiry.

## Recorded backend fixture compatibility

Separate [encrypted-backend evidence](evidence/meshticulum-encrypted-backend-20261010.json) records eight canonical-adapter checks using SDK `11.19.10+lantern.20260906` against real temporary REST/SQLite/vault fixtures, locally and on B Linux/arm64. B's isolated dependency target left its existing environment and service lifetimes unchanged. The [integration guide](../../software/meshticulum-integration/README.md#recorded-encrypted-backend-compatibility) links the exercises and their exact scope.

Those checks include reconciliation after losing a client return following a real committed HTTP PUT; no real network disconnect was injected. Policy/lease and capture boot identity were modeled fixture inputs under temporary Go governance. No production stores, C or RF operation were involved in those fixture checks. The result establishes backend compatibility only: it does not enable a private provider, qualify private transport or supply the separate public resume/route-return evidence. Production lifecycle and release gates remain open.

## Prepared source stages

| Component | Current responsibility |
| --- | --- |
| [`pilot_context.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_context.py) | Create or recheck the immutable original host-local resource context |
| [`pilot_provision.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_provision.py) | Prepare one finite public child, with payload provenance and the original native deadline; opens no transport |
| [`pilot_grant_bootstrap.py`](../../firmware/meshticulum-bench/pilot_grant_bootstrap.py) | Run preparation as isolated Linux root after package, manifest, source-file and bundle permission checks; obtain current host facts and clocks locally |
| [`pilot_resources.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_resources.py) | Lease local authenticated quarantine, serve a selected public chunk from a verified store, or independently verify a complete reply before caching |
| [`pilot_resource_node.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_resource_node.py) | Verify against the original sent request and the recipient's own campaign, manifest and trust material; opens no socket or radio |
| [`pilot_job.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_job.py) | Record the admitted public process, recheck its actual Linux process identity through a pidfd, and stop only that held handle |
| [`pilot_controller.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_controller.py) | Prepare and sequence the four public hops through an explicitly supplied management operator, preserving source provenance, original deadlines and cleanup evidence |
| [`pilot_ssh.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_ssh.py) | Execute bounded pinned SSH management, preserving actual child handles and terminal-only sudo credentials |
| [`pilot_seed.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_seed.py) | Verify and install the signed public source at B or manifest/trust material at C; recipient chunks are refused |
| [`run_public_pilot.py`](../../firmware/meshticulum-bench/run_public_pilot.py) | Run one explicitly confirmed finite mixed request/reply pilot and retain its outcome and cleanup metadata |
| [`bench_node.py`](../../firmware/meshticulum-bench/bench_node.py) | Hash-verified native actions for finite radio/TCP runs, `pilot-verify` and explicit child revocation |

The provisioner creates protected, create-only child files, including `grant.json`, `secret.bin`, `active`, `provision.json` and a sender body only when applicable. It does not claim the child, open a listener, arm a radio, start a transfer or create a worker journal. Receivers receive no management-supplied payload. The public runtime manifest keeps private services disabled.

The controller must preserve the actual process handles and evidence for these stages and enforce the finite device window. A set of successfully prepared children is not an executed or qualified route.

The `Controller` implements the four-hop request/reply sequence. Its management
operator supplies real child handles, native status, preparation, collection,
recipient verification and retirement. Native mixed execution, the earlier
failed resume and the later successful resume/IP return each have separately
pinned evidence above. Modeled controller tests remain separate source controls
rather than proof of another device route.

Public jobs create `job.json` after admission and payload validation. Radio jobs
publish `radio-ready.json` only after ARM and flow startup; TCP receivers retain
their explicit listening receipt. The controller waits for both a live native
process handle and its bound readiness record before starting the sender. It
requires terminal native processes, matching result/process/context bindings,
durable receiver bytes and radio disarm with an unfenced journal before the next
hop. The 20-second final-radio-receipt interval remains part of elapsed time.

`pilot-status` and `pilot-stop` recheck the actual boot, PID, process start ticks
and command-line digest. Stop signals the held pidfd, preventing a later PID
reuse from receiving the signal. An absent job record is unknown; a terminated
process proves neither radio disarm nor safe handoff. `pilot-retire` touches only
the matching native-provisioned public child and leaves its debt in place. These
cleanup actions remain available after a service-role change, so the pilot can
stop its own recorded process without affecting the service that appeared.

A management error during preparation is uncertain until checked independently:
the native grant may have persisted before its reply was lost. Controller
cleanup therefore attempts retirement for every attempted child, including one
whose preparation did not return success. It retains retirement/stop errors and
never upgrades those errors to a clean handoff.

## Source inputs and runtime preparation

Install the separately reviewed Meshticulum package and Lantern integration in the host environment before inspecting or building the pilot tooling. The integration package declares the Meshticulum dependency; it does not carry a duplicate core implementation.

The Lantern builder takes an explicit Meshticulum source checkout:

```sh
python firmware/meshticulum-bench/build_runtime.py \
  --meshticulum-root /path/to/meshticulum \
  --output-dir build/meshticulum-runtime
```

[`source_core.py`](../../firmware/meshticulum-bench/source_core.py) checks that input against the integration's [`meshticulum-source-lock.json`](../../software/meshticulum-integration/meshticulum-source-lock.json). The lock covers the generic core Python/JSON sources, raw `.cpp`/`.hpp`/`.c`/`.dts` firmware inputs, LICENSE and NOTICE. Preserve the exact core and Lantern commit identities with the bundle and validation records. A matching source lock or successful archive build does not establish native ownership, qualify a board, install the runtime or authorize dispatch. Private services remain disabled in the public bundle.

## Private-state precheck and immutable staging

Before native preparation or installation, inspect the existing pilot state. The root-owned `/var/lib/meshticulum-bench` directory and its protected private subdirectories must satisfy the exact `0700` checks; native grant files use the private file rules. Parent paths must be root-owned directories without group/other write permission. A legacy `0755` directory is refused rather than silently accepted as private. Any authorized state preparation must preserve existing contexts, grants, receipts and journals.

The management staging directory is separately owned by the SSH account and checked as `0700`. A staged package is `0600`, regular, single-link and opened without following a symlink. An existing digest-named staging file may be reused only after verifying identical length and **identical bytes** under those protections; different bytes or unsafe ownership/modes are refused. This avoids replacing a staged immutable input during repeated preparation.

Staging reuse does not reuse native authority. The provisioner still creates a new native child directory and grant exactly once; an existing child/session is refused. It cannot renew the original resource deadline, reset replay state or refund prior debt. See [`pilot_ssh.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_ssh.py), [`runtime_bootstrap.py`](../../firmware/meshticulum-bench/runtime_bootstrap.py) and [`pilot_provision.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_provision.py).

## Concrete management operator

The runner consumes a protected `meshticulum.finite-ssh-pilot.v1` config. It binds
the runtime archive, manifest and two bootstrap hashes; a finite handoff end;
an explicit TCP port range; and exactly A, B and C. Each target supplies its
existing SSH argv, Python path, sudo mode, current host boot and literal IP
address. Host checking and batch SSH authentication are mandatory; duplicate
or unsupported SSH overrides are refused. The caller must independently obtain
the current device handoff. A config or confirmation flag alone is not RF
authority.

Use `--help` to inspect the complete interface:

```sh
python firmware/meshticulum-bench/run_public_pilot.py --help
```

`--ask-sudo` reads each required password in the local terminal, keeps it in
memory, sends it only once to that host's recognized sudo prompt and clears it
on exit. Repeated prompts, echoed credentials, excess output and command timeouts
refuse the operation. Grant packages travel on a separate channel without a
PTY; only their digest and private staging path appear in management arguments.

### Opt-in owned management connections

Adding **`--reuse-management-connections`** to the reviewed finite invocation explicitly opens one locally owned OpenSSH `ControlMaster` for each configured host. The CLI does this before optional runtime installation, seed preparation and the original resource clock. Imports and `SSHOperator` construction open no connections; reuse is not the default. Connection setup still consumes the existing finite handoff window and preserves pinned host-key checking and SSH authentication requirements.

[`pilot_connections.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_connections.py) creates a short private Unix-socket directory and holds its directory identity. Reused commands check the owned master process's liveness and the original directory/socket ownership, permissions and device/inode bindings. Loss or replacement refuses new work. Each reused client sets `ProxyCommand=/usr/bin/false`, so a missing control socket cannot silently create a fallback TCP connection. The mode configures no global/shared `ControlPath` or persistent master: `ControlPersist=no`, with the jobs owned by this CLI invocation.

After control loss, only explicitly requested **native stop and retirement** may use a fresh pinned SSH connection for containment. That recovery is recorded in management events and still relies on the native boot/run/pidfd checks. Provisioning, installation, data dispatch, normal status/collection and recipient verification do not use this recovery exception to continue a failed path.

The CLI's `finally` path retires its native work, reaps management clients, then closes and reaps the owned masters before removing their sockets/directory. Client, master or native cleanup failure makes the result incomplete; removing a socket alone does not prove that its process is terminal. Keep cleanup errors and recovery events in the outcome.

Original resource/handoff expiry, create-only native grants, stdin-only non-PTY package staging, in-memory sudo handling, private-provider refusal and prior radio/stream-byte debt remain unchanged. Reuse supplies no native permission and renews no deadline.

Source controls include local process/socket lifecycle and actual local OpenSSH refusal of fallback TCP when a control socket is missing. The successful native resume campaign above exercised authenticated management reuse on its configured hosts and confirmed all three owned masters terminal. Native control-loss recovery and general latency effects are not established by that normal-use case; record setup/cleanup costs separately in future qualification. No isolated speed claim follows from this option, and the earlier TCP/mixed milestones remain bound to their historical pins. See [`pilot_ssh.py`](../../software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_ssh.py) and the [CLI](../../firmware/meshticulum-bench/run_public_pilot.py).

`--install-runtime` is an explicit optional step. The installer checks the pinned
archive and current host boot before writing, then inspects A/B firmware status.
C's installation inspection checks its OS role and boot without opening SPI;
actual C radio state still requires the separately admitted worker. Installation
is an action that belongs inside the coordinated window. No
firmware flash or production-service commissioning is implied.

Before the resource clock starts, the runner installs a locally verified public
source pack at B and only its signed manifest and trust root at C. It refuses
management-supplied recipient chunks. The native bootstrap rechecks actual
service lifetimes after preparation or seed installation. Each new logical
resource then gets one original context; no later route extends its deadline.

The result retains per-hop outcomes, failed/uncertain management, retirement and
stop evidence, and before/after service lifetimes. The runner reports failure
when transfer, lifecycle or retirement checks fail. Unknown physical airtime,
energy and EIRP remain null. It is a pilot tool, not a matched throughput
benchmark or private SAGE activation.

## Original resource permission

The native provisioner creates one `original_context` per host, campaign and logical resource before the first child admission. It records the actual unit and boot, independently observed epoch/monotonic clocks, resource manifest identity, original epoch expiry and original local monotonic deadline. `context.json` is immutable and create-only; another host, boot, resource or expiry cannot rebind it.

The initial context permits at most a 180-second remaining resource window. Each child must remain within its original epoch and monotonic bounds; the current provisioner also caps the grant's issued-to-expiry span at 240 seconds. Preparation cannot install an operator-chosen replacement monotonic deadline. A later wall-clock change, route generation or child grant cannot renew the original resource window.

All IP children require `application_deadline_monotonic_us`. Public radio service grants can carry the same host-local field at the plan's top level. It may only tighten the epoch-derived deadline and is excluded from the shared peer wire context, because hosts have different monotonic clocks. Peer/session/generation/object/service/epoch bindings remain shared. Legacy finite grants do not by themselves prove whole-resource deadline preservation across later admissions.

Relay preparation takes the earlier of the host's original context deadline and the source quarantine's original deadline, while requiring the same application epoch expiry. The context grants no socket, RF, identity or private-store authority. Retain old journals and contexts through failures, cancellation and fresh admissions; new children cannot refund earlier debt or reuse retired nonce/replay state.

## Payload provenance and revocation

There are three sender preparation modes, each recorded in `provision.json`:

- **Original public request fixture:** a synthetic initial sender may receive an explicit management-provisioned MR1 request that matches its admitted size, digest and shape. An MR1 response supplied this way is refused, even if its size and hash match a grant.
- **Local relay:** the body must come from this host's prior authenticated `receiver.bin`, referenced by its run identifier. A simultaneous operator replacement body is refused. The source must be a completed, durable, public receive with matching unit, current boot, format, digest and unexpired original deadlines.
- **Native public response:** `served_public_chunk` references the actual local MR1 request capture. The responder decodes those received bytes and uses a `BlockServer` backed by its configured `VerifiedCache`, pinned manifest and publisher trust. It prepares the resulting response only if its exact bytes match the separately admitted response descriptor. A simultaneous operator replacement body is refused.

The native response mode serves only a chunk from the selected public pack. The pilot controller must choose that mode for the resource server and the local-relay mode for bridge forwarding. Response preparation requires a locally received native source; it cannot substitute an operator-provided reply for the request→store→response path.

`captured()` holds the source run's shared `active` lock while reading and using quarantine. It checks the marker and active-file identity before use, then rechecks the capture, digest and original deadlines before releasing the lease. Retirement or substitution refuses that use. During dispatch, each fresh child holds its own current authority/revocation and worker leases. The controller must retire affected children explicitly; a source lease held during preparation does not imply automatic cascading revocation of every later child.

Protected native receipts provide local provenance. Quarantine remains transport-authenticated bytes, with `publisher_verified` false, until the recipient's independent checks pass. Root access or a matching body hash does not replace publisher verification.

## One radio on each device

Run radio stages sequentially through the existing exclusive worker. Request and reply must not open concurrent physical workers on the same radio. The current finite radio receiver waits 20 seconds after first flow completion so it can repeat a lost final receipt, while its original authority remains current. Allow that margin inside the resource window. Its settled exit and current owner release must precede the next radio stage on that device.

Include receiver grace, operator/controller gaps and setup in whole-resource latency. They are not PHY airtime. Disarm, wire retirement and retained worker debt remain separate evidence.

For C→A LoRa→B IP:

1. C sends a bounded MR1 request to A through fresh radio admission. A durably quarantines the exact authenticated bytes; request/ACK costs remain charged.
2. A provisions its fresh A→B TCP sender from that local request quarantine. Do not copy a substitute request into A through management. B receives and quarantines the actual TCP bytes.
3. B prepares the response from the received request and its configured verified public store, then sends those exact bytes through separate B→A TCP admission under the same original resource expiry.
4. A provisions fresh A→C radio admission from the response actually received over TCP. Management must not install a substitute reply at A or C.
5. C runs independent MR1 request-binding and publisher/chunk verification before committing the piece. The native verifier reads C's original completed sent request, rather than accepting a newly supplied request body.

Every direction needs independent child sessions, adjacent-link keys and current admission. Record the complete peer bindings, original deadlines, sender authenticated receipt, receiver durable bytes, each worker's costs and cleanup separately. TCP `KERNEL_ACCEPTED` is stream-write evidence; it cannot settle RF debt, prove peer receipt or establish publisher trust.

## Independent recipient verification

The initial verifier handles one **whole selected chunk**, starting at offset zero. It checks the MR1 response's magic, request digest binding, success status, offset, complete length and requested size. It then verifies the recipient's independently pinned manifest digest and publisher signature, finds the named chunk in that manifest, validates its content and pack rules, and checks original expiry and the earliest campaign, request or response monotonic deadline before cache insertion.

Prepare signed public metadata and trust material independently at the recipient; receiving a response does not enroll its publisher. This pilot begins with that metadata already provisioned. It does not claim discovery or a complete pack transfer.

The radio child object ceiling is 16,384 bytes, including the 28-byte MR1 response header. Select a complete chunk no larger than 16,356 bytes for the initial radio response. This verifier does not merge arbitrary partial replies into a chunk. Chunk reuse is allowed only after revalidation; an existing piece must match the verified bytes exactly.

The native `pilot-verify` action holds the original sent request's `active` lease and checks its receipt schema, unit, boot, completed send, MR1 format, common epoch expiry and exact descriptor bytes. Its original monotonic deadline must still be live and no later than the immutable campaign deadline; that tighter bound is passed into the chunk verifier. The action rechecks the active-file identity, marker, deadlines, receipt, plan and request bytes before recording `verified-piece.json`. An uncertain or changed request cannot produce that success receipt. The receipt establishes neither private admission nor a SAGE import.

## Failure, resume and route return

`run_public_pilot.py --mode resume` selects four distinct signed chunks under one
original resource campaign. It first verifies a direct IP piece, then explicitly
interrupts the next public IP response with `--loss-rx-bytes` (512 by default).
Only the admitted receiving test socket is closed, after the bounded number of
stream bytes have been charged. Its receive journal is fenced, the old sessions
are retired and the incomplete object is never quarantined or publisher-accepted.
This hook is root/operator-selected, receiver-only and limited to public MR1
responses; a peer cannot request it and private formats are refused.

The native TCP prerequisite proved first-piece verification, bounded loss and
an unchanged cache. The earlier failed resume then stopped at B's overlong lease
before ARM/TX. A later fresh campaign completed radio resume and measured IP
return before its own original expiry, as recorded above. The controller rechecks
C's signed cache and uses fresh direct LoRa request/response admission for the
missing piece within that deadline. The already
verified piece remains present and is not retransferred. It samples a fresh IP
generation on the third piece and sends the fourth over IP only if the new
verified rate exceeds the observed LoRa rate. A slower IP sample remains a
reported result and leaves faster-IP return unqualified.

Timing uses the original sent request's start and publisher-verification receipt
on C's own native monotonic clock. It includes management/controller gaps,
request/reply, cleanup policy and verification; it is whole-piece service timing,
not raw PHY capacity or one-way latency. The successful faster-IP return path
uses ten independent child sessions; a slower-IP outcome stops after eight. They
keep the same original application expiry, retain their journals and cannot refund
the failed IP attempt.

For a separate resource transfer, admit a direct C↔B IP route and verify a first piece. Cause a bounded loss of that test connection, retain its original reservations and verify that an incomplete piece has not entered the cache. Do not change production network configuration to inject the failure.

Admit a fresh C↔B LoRa route under the original host contexts, resume using already verified pieces and verify the next completed piece. Then admit a new IP route generation. Obtain a new verified timing sample; old-generation measurements and successful socket writes do not establish its current advantage. Return to IP only if its current verified predicted cost is lower. If it is slower, retain that result and leave the faster-IP requirement open.

These route changes now have the finite native public evidence above. Other conditions, workloads and private application paths still need their own qualification. Prepared resource helpers do not migrate live wire sessions, authorize routes or clear retained debt.

## Evidence and cleanup

The controller must enforce the finite handoff end time, preserve raw failures and interrupted writes, poll actual process handles, and stop/disarm/retire its own jobs before returning a device. Preserve production model/voice/UI services, SAGE/SDK identities and protected stores. A state file alone proves neither process liveness nor safe writer handoff.

Keep provisioning receipts, native run receipts, quarantined bytes, verification receipts and cleanup outcomes distinct. An expired or failed run may leave durable evidence; do not delete it and reprovision the same session as if no attempt occurred. A new admission retains the original resource deadline and the prior DATA/ACK/retry reservations.

Collect useful verified bytes and full request→verification elapsed time, plus per-hop timing, stream-byte counts, radio driver completion and uncertainty. Leave unmeasured RF airtime, energy, EIRP and kernel retransmission measurements null. Synthetic and loopback timings are source controls rather than physical capacity measurements.

## Published comparison review

The owner's comparative scope uses **published MeshCore and Reticulum figures**, not new campaigns that we run on those stacks. Our target remains faster useful encrypted transfers and stronger encryption/security than both, supported by explicit performance evidence and security properties. Meshticulum's own native and sustained-performance qualification remains required.

For every cited reference figure, retain its primary source, date/version, hardware, modem settings, payload/encryption, topology, completion boundary, units and loss/retry scope. Review compatible conditions against our recorded measurements; leave missing or incomparable samples unavailable. For security, compare documented threat models and properties such as sender/recipient authentication, key lifecycle/forward secrecy, replay, consent/authorization and metadata exposure. Do not infer superiority from a primitive name, a key size or a faster PHY setting.

See [the current core comparison](https://github.com/l33tdawg/meshticulum/blob/f6def31e3ce6f107f55a3a0ca14c71186ce3bc7d/docs/COMPARISON.md). The retained radio results and 1–1.5 kB/s engineering target remain documented in their [historical performance record](https://github.com/l33tdawg/meshticulum/blob/6605ba714ce414445673b40bfcfe93fe467b86aa/docs/PERFORMANCE.md). Historical evidence keeps its original pins, figures and comparison fields; this scope correction creates neither a new benchmark result nor a security-superiority claim.
