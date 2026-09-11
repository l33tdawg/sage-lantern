# Explicit gateway daemon composition — source only

No installation, enablement, key provisioning or native RF acceptance is implied.
The canonical installer/stager now includes `sage-lantern-gateway-evidence.service`.
Its runtime module is `sage_lantern.gateway_evidence`; the daemon composition is
`sage_lantern.mesh_gateway_root`. The mesh service pulls in the evidence helper,
but neither service is automatically enabled by these source changes.

## Root manifest

`mesh-runtime.json` may contain **one** of `public_mesh` and `public_gateway`.
Gateway mode is an explicit dedicated radio role, not implicit public activation
of a private identity. Its exact `public_gateway` fields are:

- `identity_file`: an existing independently provisioned ordinary gateway signing
  key under the identity directory, different from the private storage key.
- `role`: the frozen `RootPublicGateway` configuration: schema, role, enabled,
  endpoint, chain_id, domain, gateway_key, storage_actor, storage_chain, pins,
  timing, operation_seconds, lease_seconds, poll_interval.
- `backend_pin`: exact `pid`, `start_ticks`, `boot_id`, `listener_inode` of an
  independently verified public backend. The operator attests that this exact
  process/listener serves the configured endpoint and chain. This code does not
  discover chain identity from a health response or fabricate that attestation.
- `calibration`: schema `sage.lantern.gateway-calibration.v1`, local `radio_key`,
  `role_digest` (SHA256 of protocol canonical JSON of the entire role), and integer
  `issued_at`/`expires_at`. The operator must supply measured conservative timing;
  this manifest is not a power, reachability or latency measurement.

Public endpoint/chain and gateway key must differ from the private endpoint/chain
and signer. Storage actor/chain must match the daemon's existing protected client.
Pins map independently approved full radio keys to ordinary public authors;
unique short prefixes are required but do not authenticate authors. The bridge
still verifies signed requests and the public backend's member/clearance-zero
profile before REST mutation. No private Root credential is used for public REST.

Gateway mode requires messaging/dispatch enabled and both locked policies false.
Operation duration is at most 50 seconds; lease duration is operation+5 through
55 seconds. Effective systemd `TimeoutStopUSec` must be 65–120 seconds, checked
via busctl before admission. The shipped mesh unit uses 75 seconds; gateway
shutdown and root drain requests use 60 seconds. Unsupported larger role budgets
fail closed rather than silently inheriting the old 30-second capture lease.

## Startup, grants and uncertain state

Before USB open/reset, daemon startup validates evidence, loads the pinned key
with the existing no-follow loader, and verifies protected gateway journal
signatures, binding, schema and progress. Unknown forwarding/dispatch or corrupt
rows block opening. Clean queued progress is not an automatic retry grant.
The actual `RootPublicGateway` then attaches to the same existing scheduler and
durable receiver. It handles LP1 and LC1; there is no second radio owner.

Each gateway background lease captures the exact non-null access epoch on its
owner thread. Both backend/user checks and RF checks require that same epoch:
lock followed by another unlock cannot authorize the older grant. The separate
capture-storage lease remains bound to supervisor object/broker and private vault
generation through admitted persistence and join. It neither grants new user
reads nor extends its monotonic deadline. Uncertain work retains dependencies.

Root public proposal composition passes its actual probe as `contact_admission`
to `PublicMeshProvider`. A fresh signed contact reply admits one exact bounded
exchange under the provider's contract; a callback returning true is not a
replacement probe admission. Public results still do not grant consensus-green
acceptance or canonical cache eligibility.

## Privileged evidence helper

The root-only, mode-0600 Unix socket accepts only a nonce and expected manifest
digest. The helper reads the fixed root-owned manifest; callers cannot select a
PID, path, command, endpoint or key. It checks the pinned process incarnation,
loopback listening inode and that process's descriptor, then rechecks listener
and descriptor before responding. A fresh helper response is required for each
check. Missing helper, wrong peer/digest, changed configuration or timeout denies.

`Type=notify` publishes readiness only after the listener is bound/listening.
Client and server enforce total monotonic request deadlines, not merely a fresh
socket timeout for each byte. Replies arriving after the deadline cannot grant
authority. Native SO_PEERCRED, procfs/LSM permissions and systemd confinement
still require Linux acceptance testing.

Only the helper has CAP_SYS_PTRACE alongside CAP_DAC_READ_SEARCH; the radio/SDK
daemon does not gain ptrace. The helper has no network/radio devices, sees config
read-only, masks the private data tree, and blocks ptrace/process_vm syscalls.
**It remains a privileged trust component:** procfs capability checks and process
memory exposure mean these restrictions do not prove it incapable of accessing
secrets if compromised. Its code deliberately inspects metadata only.

Backend restarts, changed listener inode, new boot, changed pins or expired
calibration require new independently verified operator metadata. No automatic
repinning, enrollment, key creation, or production default values are supplied.
