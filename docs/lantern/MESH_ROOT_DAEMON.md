# Dedicated root mesh runtime — source integration, not deployed

Voice and browser remain unprivileged. `sage_lantern.mesh_daemon --serve`
is the dedicated root owner of USB, the serial scheduler, MeshRuntime and
the cooperating voice/vault lifecycle. No radio open occurs at import or
daemon launch: first authenticated mesh access performs bounded protected
journal reconciliation before the single approved initial open.

## Trusted installation inputs

The only daemon configuration path is `/etc/sage-lantern/mesh-runtime.json`:
root-owned, regular, single-link, 0600, bounded 16 KiB, real trusted ancestors,
strict JSON without duplicate fields. No socket operation accepts configuration,
file paths, commands, arbitrary SDK methods or keys.

The exact required fields are:

| Field | Meaning |
|---|---|
| `schema` | `sage.lantern.mesh-runtime.v1` |
| `unit_id` | Actual prepared unit, e.g. `unit-a` or `unit-b` |
| `voice_uid` | Actual non-root `sage-lantern` service UID |
| `endpoint`, `chain_id` | Operator-pinned local SAGE endpoint and chain |
| `identity_file`, `agent_id` | Existing ordinary identity below `/var/lib/sage-lantern/identity/`, and its independently verified public ID; never a Root identity |
| `receiver_slot` | Existing fixed canonical nonzero UUID; never replace an unresolved slot |
| `radio` | Exact `stable_path`, `serial`, `vendor_id`, `product_id`, `public_key` from verified hardware inventory |
| `peers` | List of independently approved `{chain_id,agent_id,radio_public_key}` triples |
| `approve_initial_open`, `acknowledge_reset` | Both explicit `true`; approve possible electrical reset/buffer loss, not fictitious no-reset behavior |
| `messaging_enabled`, `receive_while_locked` | Explicit background receive consent |
| `dispatch_enabled`, `dispatch_while_locked` | Separate explicit consent to continue already approved queued sends |

All policy fields require exact booleans. No default PIN, keys, enrollment,
contacts or trust bindings are manufactured. Peer triples do not create contact
cards or populate firmware contacts. The protected registry must already hold
current signed self-consent cards, and the radio must independently know the
corresponding full keys. These remain provisioning prerequisites.

The existing key is opened using no-follow directory descriptors, checked as a
0600 single-link 32-byte regular file and matched to `agent_id` before any SDK
call. A Linux sealed memfd holds the same key for repeated SDK factory loads;
there is no new key or filesystem copy. The root journal is deliberately outside
the service-owned home: `/var/lib/sage-lantern-mesh-supervisor`, root 0700.

## Service confinement and activation ordering

Stage the matching wheel, voice script, suspend helper, dedicated root unit and
`40-mesh-broker.conf` voice drop-in together. The root unit has a closed device
policy, loopback-only IP policy, read-only system/home protection, bounded
capabilities and only root control/journal directories writable. It does not
restart automatically. Voice stays `User=sage-lantern` and has `Restart=no` plus
a root-owned start condition. The transient start file is **only a systemd start
condition**, never a storage lease or proof of drain.

The voice runtime directory is optional in the daemon's `ReadOnlyPaths`: on
cold boot it does not exist until the supervised voice service starts. The
daemon must not depend on starting voice first to create it. `ProtectSystem=strict`
remains enabled; required identity/configuration and drain directories must
already be provisioned. Native cold-boot namespace verification remains required.

`python -m sage_lantern.mesh_daemon --print-device-policy` validates the fixed
operator manifest and emits the exact single-radio DeviceAllow drop-in, without
opening USB or loading the key. After independent review, that output belongs at
`/etc/systemd/system/sage-lantern-mesh-runtime.service.d/20-radio.conf`.
No wildcard tty permissions. Re-enumeration requires explicit review, not port
discovery or an automatic open retry.

Before first activation, stop existing voice/radio producers with their real
drain proofs, preserve existing protected journals, disable independent voice
boot starts and verify the per-unit systemd device/condition properties. Do not
start this alongside an existing radio owner. No installer or live activation
is performed by this change.

The daemon waits for actual encrypted SAGE storage availability, then starts the
voice service through its guarded fixed systemctl path. Voice selects the IPC
facade only with `SAGE_LANTERN_MESH_BROKER=1`; missing/unavailable root runtime
never falls back to in-process serial or plaintext storage.

## IPC and lifecycle

Socket: `/run/sage-lantern-mesh-control/broker.sock`, in a root-owned directory.
Every connection uses Linux SO_PEERCRED. Voice must match the supervised PID,
UID, start ticks, boot and systemd invocation; subsequent calls additionally
bind the current broker ID. The client verifies root as its peer.

The only voice methods are `ready`, `contacts`, `list_messages`, `open_message`,
`queue`, `close`. Responses remain typed provider values. Frames are length
prefixed and bounded at 64 KiB; mutation requests are bounded at 8 KiB. During
live operations root challenges the original voice authorization callback and
independently checks the device access epoch. Disconnect, lock/reunlock,
process replacement, stale broker, malformed responses and timeouts deny access.
There are no automatic mutation retries. Queue success is only durable queueing,
not RF delivery or user read. No automatic delivery/read receipt is introduced.

Root-only control methods are `drain`, `resume`, `close-vault`, bound to a
transition UUID. The existing suspend helper retains its fence and positively
drains auth first, then calls this broker instead of stopping voice directly.
The broker keeps servicing storage callbacks while voice stops. It requires
the exact bound voice receipt, verified empty voice process tree, and positive
join of its own mesh runtime before acknowledging drain. An admitted capture
can persist after user lock while its owned vault-generation lease remains
valid; that lease never grants fresh reads, RF or user authorization.

After correlated resume/verified abort, the existing helper starts auth fresh
locked and requests supervised voice restart. Broker identity rotates and a
fresh lazy hardware runtime is constructed. Old IPC clients cannot regain
authority. Existing camera, kiosk, model-idle and suspend proof gates remain.

`close-vault` alone runs the fixed stop of
`sage-lantern-personal-sage.service`, after positive drain and fresh fence/auth
checks, then verifies inactive state and an empty vault process tree. Normal
suspend need not stop SAGE or the warm model. Any uncertain stop/join retains
the fence; timeout never substitutes for cleanup.

## Recovery and remaining native acceptance

Broker death, an existing journal/socket/start grant, unknown capture/dispatch,
changed vault generation or failed initial open blocks automatic recovery.
Never delete these artifacts to retry blindly. Reconcile protected records and
process/device ownership through a separately reviewed operator recovery flow.
Unattended crash/reboot recovery is not implemented.

Cooperating management must use this close gate. Independent root commands,
kernel failure, malicious same-credential writers and pre-existing external
serial handles are outside its enforcement boundary.

Synthetic tests exercise real Unix socket framing, provider/SDK protected
workflows and synthetic serial, plus root control ordering and rejection paths.
Linux kernel peer credentials, memfd seals, effective systemd confinement,
per-unit initial-open electrical effects, current peer enrollment and physical
two-unit RF operation still require native review/testing before deployment.
