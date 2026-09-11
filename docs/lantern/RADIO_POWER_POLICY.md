# Unit A Radio and Screen Power Policy

Status: read-only repository audit and proposed acceptance contract, 2026-09-05.
Only this document is added. No live radio queries, transmissions, key changes,
USB controls, screen controls, OS suspend, installation or firmware changes.
Scope: existing Unit A master. Hardware wake and two-node RF tests remain pending.

Updated owner requirement: the intended default is a duty-cycled Lantern that
periodically wakes to poll/announce/sync, then turns its receiver off between
windows. This overrides the earlier recommendation to leave the initial mode
unspecified. It is a requested product default, not an implemented capability
or evidence of power savings.

## 1. Actual support found in the repository

| Source | Established evidence | Not established |
| --- | --- | --- |
| `evidence/unit-a-radio/meshcore-radio-public.json:5` | Dated evidence identifies Heltec V4.3 OLED and USB companion firmware v1.17.1-d929643. | Not a current hardware interrogation or a firmware command specification. |
| `evidence/unit-a-radio/meshcore-radio-public.json:39` | Companion protocol passed and settings survived reboot in the recorded test; two-node RF exchange is explicitly pending. | No measured idle/sleep current, receiver state, sleep/wake command, transfer drain, or RF delivery guarantee. |
| `evidence/unit-a-radio/meshcore-radio-public.json:30` | Recorded profile has `repeat:false`. | Must not claim this companion currently provides autonomous mesh repeating. Receiving messages via relays and acting as a repeater are different roles. No traffic does not prove the receiver is unused. |
| `evidence/unit-a-radio/70-sage-lantern-radio.rules:1` | Exact USB device identity maps to `/dev/sage-lantern-radio`, mode 0660, group `sage-lantern`. | A stable serial alias is not a switchable power rail or a power-control API. |
| `scripts/lantern/unit-a-status-server.py:243` | `_radio()` checks device-path existence and reads recorded identity/profile metadata. | Does not query firmware, TX/RX busy state, RF reachability, power consumption or current sleep state. Missing USB is not proof of power-off. |
| `docs/lantern/SOFTWARE_STACK.md:13` | MeshCore serial transport remains an adapter gap in the documented scaffold. | No deployed transport/power-controller readiness follows from this description. |
| `docs/lantern/MESHCORE_HOST_PROTOCOL_TEST_PLAN.md:17` | Enumeration/reconnect and payload/ACK measurements are planned. | No verified host framing, power opcode, wake handshake, receipt-drain timing or maximum radio payload supplied here. |
| `scripts/lantern/sage-lantern-kiosk:7`, `scripts/lantern/sage-lantern-session:6` | Both startup paths issue `xset s off`, `xset -dpms`, and `xset s noblank`. | A dark UI or “Tap to wake” is not proof of panel/backlight power saving. These source settings are a concrete obstacle to automatic DPMS, not proof of current native state. |

Search of runtime source and Lantern scripts found no MeshCore sleep/standby/
power-off command implementation or verified USB-port power-switch integration.
The repository firmware tree contains the separate audio bridge, not the radio
firmware source required to audit its sleep implementation. ESP sleep symbols in
audio-bridge SDK/build artifacts are not Heltec radio support.

**Supported power command: none established by this audit.** Do not invent an
opcode, expose an arbitrary serial command, or infer support from the SX1262/
ESP32 chip family. Closing a serial connection, lowering transmit power, stopping
host sends, USB unbinding/autosuspend, and removing board power are distinct
operations; none is verified here to disable the receiver or save a measured
amount of energy. Any external/secondary supply and shared USB power topology
must be checked before claiming a rail switch turns this board off.

## 2. Proposed explicit settings

Settings are policy requests, not statements of observed hardware state. Changes
require live owner authentication and generation fencing. A narrow trusted
controller owns hardware actions; browser code never supplies shell commands,
USB paths, GPIOs or firmware bytes. No new control endpoint is defined here.

| Mode | Intended behavior | Honest UI / availability |
| --- | --- | --- |
| Reachable | Keep a verified receiver available; host outbound traffic occurs only under existing consent and queue policy. Screen-off does not independently turn the radio off. | “Radio on; reception enabled” only with fresh driver evidence. “Reachable” is a selected intent, not proof that any peer is in range or can deliver. |
| Battery saver — requested default | Periodically wake, poll the designated awake relay, send a bounded announcement if needed, and exchange incremental sync traffic; safely drain and disable reception between windows. Explicit Send/Sync requests an immediate wake. | Show “Scheduled reception” and next attempt only after sleep/wake and relay protocol validation. Until then show “Duty-cycle requested; radio power control unsupported”; do not silently claim that windows or power-off are operating. |
| Radio off | After safe quiescence, disable TX and RX using a verified command or dedicated power control; preserve pending work. Remain off across reboot until owner changes policy, subject to verified boot enforcement. | “Cannot send or receive mesh messages, including messages delivered through relays.” Until supported, offer an unavailable explanation rather than pretending a host pause is radio-off. |

The owner has selected Battery saver as the intended default. This document
does not activate it on the existing master or authorize announcements/new
publications. First activation must disclose delayed delivery and the selected
schedule; transport enrollment and publication consent remain separate gates.
Retain explicit Reachable and Radio off overrides. In Radio off, Send/Sync must
ask permission to enable the radio rather than overriding the setting silently.

Keep requested mode, observed radio state and queue state separate. Proposed
observed states: `unknown`, `on`, `quiescing`, `sleeping`, `off`, `fault`.
After command timeout, disconnect or stale evidence, report `unknown`/`fault`,
not the requested state. Unsupported controls remain disabled. Preserve the
requested policy on errors and reboot; do not auto-enable off mode for retries.

“Unused” means policy permits loss of reception AND no active send, receive,
fragment assembly, receipt exchange, queued firmware TX, or imminent negotiated
receive obligation. An empty outbound queue, no recent TX, screen lock or no
visible neighbors alone does not meet that definition. In Reachable mode,
listening is intentional use even during silence.

## 3. Safe power-down transaction

1. Persist the requested mode/revision, then fence new transport work under the
   single controller's transition lock. Resolve setting races by revision;
   stale callers cannot turn the radio back on.
2. Stop admitting new transfers. Drain the current TX and bounded receive/
   receipt work using actual firmware/transport completion evidence. A completed
   serial write does not establish completion of over-the-air transmission.
3. Persist exact queued payloads, transfer IDs, duplicate/reassembly state and
   received receipts before changing power. Already-dispatched outcomes without
   an authoritative receipt remain unknown/reconcile-only, never delivered or
   unsent merely because the device will sleep. Preserve private, public and
   transport queues as separate authority domains.
4. At a verified safe point, issue only the supported allowlisted power action.
   No routine mode transition may cut power mid-transmit or receipt. If the
   deadline expires without safe-idle evidence, defer the power change and show
   why; do not guess that the hardware finished. Bounded current receipt handling
   must not allow endless unsolicited traffic to keep extending the drain window.
5. Verify resulting state, then publish success. An unknown command result
   requires reconciliation, not blind power cycling, reflashing or identity reset.
6. On enable/wake, verify exact device identity, protocol/version, preserved
   configuration and receiver readiness before resuming eligible work. Reconcile
   uncertain transfers and deduplicate receipts before considering resend.

Every duty-cycle wake has a bounded work budget: connection/poll time, encoded
bytes, attempted transfers and total awake time. Before starting each operation,
require enough remaining budget for its defined TX/receipt window. Do not admit
new work merely because the queue is nonempty; persist unfinished work for the
next cycle. Explicit Send/Sync may begin an immediate bounded window, not an
unlimited drain. Coalesce repeated button requests and apply a retry cooldown.
Do not extend the awake deadline on every received packet. The budget is an
admission limit, not permission to cut power mid-transmit; if firmware idle cannot
be verified, remain visibly degraded/on rather than claim a completed sleep.

A receipt timeout can end a transport attempt without proving non-delivery.
Radio-off cannot promise receipt arrival while the receiver is disabled. The
protocol must define a bounded acknowledgement window and later reconciliation;
power-down may proceed after that window only with verified hardware idle and
the uncertain transfer durably recorded. Unexpected physical power loss is a
recovery test, not an authorized normal transition. Critical-battery emergency
cutoff requires a separate explicit safety policy and honest interrupted state.

## 4. Scheduled reachability is a missing protocol

Disabled reception means this node cannot receive messages from peers/relays
during that interval. A local wake timer alone does not tell senders when to
deliver or require them to retain messages. Do not describe periodic radio
sleep as always reachable, or assume MeshCore firmware negotiates it.

The default requires a separately provisioned, awake store-and-forward relay or
gateway. Relay nodes have a distinct always-on role and power/connectivity
budget; a sleeping Lantern must not be advertised as an always-available relay.
Current Unit A evidence records `repeat:false`, not a deployed relay service.
No such relay is provisioned or authorized by this document. Without one, peers
can miss each other's receive windows and queued work must remain pending.

Use asynchronous delivery: a relay custody ACK means buffered at that relay,
not received by the destination, ingested by SAGE or canonically committed.
Persist sender identity/message ID, immutable digest, expiry and per-stage
receipts. Lost wake windows, announcements and ACKs require bounded backoff,
retry/reconciliation and durable receiver deduplication. A timeout never proves
non-delivery. Relay storage quotas, retention and TTL expiry must be visible;
there is no guaranteed eventual delivery if relay retention or connectivity ends.

Before scheduled reception can be enabled, specify and test: authenticated
schedule/version announcements; clock basis and drift guard; listen duration
and startup budget; expiry/offline behavior; peer/gateway buffering quotas and
message TTL; acknowledgement/retry/deduplication; missed-window recovery; and
fair airtime budgets. Avoid publishing private activity schedules unnecessarily.
An untrusted peer must not keep the unit awake indefinitely or alter owner policy.

Do not align every device on a common timer edge. Specify bounded randomized
jitter/backoff to reduce synchronized announcements and collision retries,
while allowing the announced schedule and clock-drift guard to cover the actual
listen window. An awake polling relay can avoid dependence on peer-to-peer
window alignment. Measure cold-start, clock drift, missed wake and collision
behavior before selecting guard widths or advertising a delivery latency bound.

If peers do not support the agreed protocol, offer continuous reception or an
explicitly unavailable/off mode. Do not auto-sleep a receiver based on TX silence.

### Incremental sync, not a full-blockchain promise

Each wake may request a small manifest/checkpoint summary, then missing bounded
bundles after the last durably verified cursor. The product must name the chain,
domain and covered range. Verify supported checkpoint/bundle provenance, content
digests, ordering and authoritative status using the approved SAGE boundary;
relay signatures or a higher claimed height alone cannot establish canonical
trust. Do not invent an upstream checkpoint API. Missing supported proofs keeps
this sync mode disabled rather than treating relayed JSON as chain state.

Persist partial bundles separately; advance the cursor only after complete
verification and atomic installation. Resume missing chunks in later windows,
reject rollback/conflicting checkpoints, and process challenges/deprecations as
well as new answers. Gaps require bounded recovery over an approved transport,
not an automatic full-chain download or silently trusted snapshot. Label scope,
last verified checkpoint/time and stale state. A partial public-cache update is
not full personal-ledger replication, consensus participation or proof that the
entire blockchain is synchronized. Photos remain outside initial LoRa payloads.

### Interval decision still open

Proposed starting point for a later authorized experiment: one wake attempt
every **5 minutes**, with a **30-second maximum planned awake window**, plus
immediate bounded wake for explicit Send/Sync. These are provisional UX/latency
targets, not measured radio timings, legal airtime limits or a power prediction.
The operation-admission rule still forbids cutting an active TX to meet 30 seconds.
Measure startup/poll cost, encoding overhead and receipt timing first; if they
cannot fit, revise the protocol or window rather than report false success.
No interval is activated by writing this document. Owner confirmation and relay/
driver evidence are required before selecting these values as deployed defaults.

## 5. Screen-off and OS sleep are separate

Recommended independent display policy: retain 120-second authenticated idle
and six-second browser presence; after confirmed lock and the agreed 30-second
locked-screen delay, request verified panel/backlight power saving. A wake input
only reveals the PIN UI and must not also trigger PTT, activation or sending.
Actual interaction, not polling or receipt traffic, resets display/user-idle time.

Parent should resolve both launcher DPMS overrides before evaluating display
idle. Verify actual backlight/panel state and input wake on Unit A; black pixels,
a UI overlay, or a successful control command alone are not energy measurements.
Do not disable touch power without a tested alternative wake source.

Display blanking must follow auth revocation/output cancellation. Trusted system
revocation must increment both broker and device generations; public `/lock`
alone is not a suspend barrier. Relevant primitives are
`scripts/lantern/unit-a-device-auth.py:295` (`AuthBroker.shutdown`) and
`software/runtime/src/sage_lantern/device_lock.py:434` (`DeviceLock.lock`). The
former still needs an admission gate preventing new unlocks during preparation.

The requested five-minute OS suspend remains separate and unimplemented by this
policy. It must coordinate radio drain, auth/cookie cleanup and hardware gates;
explicitly revoke again before reopening admission after wake. Do not depend on
monotonic idle expiry across suspend. Neither USB power retention nor autonomous
radio buffering while the host sleeps is established, so host suspend cannot
be advertised as preserving mesh reachability without dedicated evidence.

## 6. Acceptance plan — not executed

| Test | Required evidence |
| --- | --- |
| Driver capability | Exact firmware source/protocol mapping for sleep/wake or verified dedicated port switch; no guessed command; unsupported revision fails closed. |
| Energy and wake | Measure receiving, TX, sleeping, off and display-off power; verify supply topology, reconnect timing and wake inputs. No numerical saving claimed beforehand. |
| Reachable silence | Long interval without TX does not disable RX; distinguish policy intent from proven peer reachability. |
| Transition race | Change mode during serial write, RF TX, fragment receive and receipt commit; no mid-operation power cut or false receipt. Deadline failure leaves visible deferred state. |
| Crash/reboot | Persist mode, pending payload/ID and unknown dispatch before power changes; restart preserves identities, deduplication and no-false-delivery semantics. |
| Off enforcement | An off request cannot be overridden by retry workers, screen wake, stale UI or boot races. If boot off cannot be guaranteed, feature remains unsupported. |
| Scheduled windows | Simulate drift, missing peer support, dropped announcements, missed windows, TTL expiry and bounded store-and-forward. Unsupported peers get no false reachability promise. |
| Awake budget / explicit wake | Send/Sync wakes promptly in Battery saver, coalesces repeated requests, respects bounded work and safe drain; Radio off still requires explicit enable approval. Backlog cannot keep the radio on indefinitely. |
| Relay custody / collisions | Lost custody/delivery ACKs and colliding announcements cause bounded jittered retries without duplicate logical delivery. Sleeping Lanterns are not counted as always-on relays. |
| Incremental verification | Interrupted bundle, forged checkpoint, rollback, missing range and changed canonical status do not advance the verified cursor or yield falsely current public answers. No implicit full-chain or photo download. |
| Receiver-disabled loss | Demonstrate that messages/relays are unavailable during off intervals; show buffered delivery only where separately verified. |
| Photo/resource pressure | Large photo queue does not force radio on or monopolize airtime; preserve separate photo-pending status and PTT priority per `SHARED_PHOTO_TRANSPORT.md`. |
| Screen and auth | Confirm physical screen-off, PIN-only wake, revoked pending/active tokens, no restored stream/audio and no pass-through wake gesture. |
| Faults | USB loss, command timeout, corrupt queue, full disk and continuous unsolicited frames cause bounded recovery, never guessed power state or queue erasure. |

Parent handoff: no radio power command is verified in this repository; implement
and validate the capability boundary before exposing Off or scheduled sleep as
working settings. Screen DPMS overrides are a concrete source finding to address
separately. All live/hardware tests need explicit follow-up authorization.
