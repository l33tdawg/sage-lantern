# B private-policy correction — source review only

No device mutation, restart request, identity initialization, or audio admission
is authorized by this document. B's previously observed unlocked state is not a
fresh observation. Parent retains live-operation ownership.

## Default origin and durable correction

`prepare-b-identity-inputs.py` previously omitted subordinate federation policy
and `voter.required`. Native `DefaultConfig` initializes P2P enabled, two default
relays, forced-private routing, and optional voting. Loading the abbreviated
YAML therefore inherited those values. `node.go` starts the P2P substrate only
when both federation and P2P are enabled: this is **not evidence of exposure**.

The corrected Lantern template explicitly disables federation, P2P, relay
addresses, forced-private routing and quorum, and requires the enabled voter.
Generic SAGE defaults are not changed.

Native `omitempty` tags lose explicit false/empty policy during `SaveConfig`
and typed canonical rewrites. The next load inherits defaults again. The
review artifact `scripts/lantern/lantern-private-policy-roundtrip.patch` removes
omission for only P2P enabled, relay addresses and forced-private routing. Its
Go tests exercise save/load, chain-ID persistence, federation persistence, and
unchanged generic defaults. Apply to a new matched source/build, not an existing
release or a running A binary. Original matched `cmd/sage-gui/config.go` SHA256:
`87a6daa8bf4147517f4ac9feddf1f38ea94c214b5a29b80c6cfae3de5317f2f7`.

## Existing B is a separate transition

`scripts/lantern/prepare-b-private-policy.py` stages only a correction of B's
exact observed config SHA256
`dc1c2a230e427401bc0ba5714588e4f1725b6b4a49d3cef288f047133d0eea9b`.
It requires the independent B initialized receipt SHA256
`bb59112deef341ae869433a565acd7ec0815a081369bb61e684b315051897214`,
and checks B chain/root identifiers. A receipt cannot be substituted. It rejects
input hash drift, YAML aliases/duplicates, and credential-bearing policy. Only
the four reviewed subordinate-policy fields change. Identity, encryption,
media quotas, embeddings and historical commissioning receipts remain intact.

Future reviewed staging command, on B with both helper files pinned and private
staging ancestry verified (not executed):

```sh
/opt/sage-lantern/venv/bin/python -I -B /opt/REVIEWED/prepare-b-private-policy.py \
  --current-policy /var/lib/sage-lantern/sage-personal/config.yaml \
  --initialized-public /etc/sage-lantern/initialized-public.json \
  --output /var/lib/lantern-b-policy-UNIQUE
```

The output directory must not exist. `before.yaml` preserves exact bytes;
`candidate.yaml` and `review.json` are exclusive private files, fsynced. This
helper has no apply/restart interface. Partial staging is retained, not retried.
Do not export the baseline or candidate from the device.

## Reload admission still missing

A B-specific coordinator is now implemented for review in
`scripts/lantern/deploy-b-private-policy.py`, but **not admitted on B**. Do not use the A
restart helper's main, its intent, its native evidence or a systemctl native
restart. Before a live correction, the reviewed coordinator must:

1. Pin B boot, initialized identity, native PID/start/invocation, image and
   manifest, loopback listener, governance app27/no pending upgrade, current
   auth invocation/source, config and every service/drop-in.
2. Establish fresh-prebroker containment with genuine empty workflows and
   recursive cgroups, stopping UI then draining only the pinned auth lifetime.
   Require its actual closing/drained/local-revoked/confirmed receipt; inactive
   alone does not prove drainage. No fake prior lifetime or unlocked session.
3. Preserve baseline and binary in an exclusive durable transition journal;
   reverify pins immediately before atomic replacement. Candidate native must
   contain the round-trip fix and have independently matched release hashes.
4. Invoke a B-specific local-key signed native restart once. Unknown delivery
   retains the intent and inhibits automatic retry/rollback. Never stop or
   restart native through systemctl.
5. Verify changed dashboard boot, unchanged process start/PID/invocation,
   matched executable/listener/version, locked storage and persisted policy.
   Restore only exact owned temporary guards, then hand off owner unlock.

Matched rebuilt binaries are in
`/private/tmp/lantern-b-private-policy-native-20260907-{a,b}/`.
Both binary and manifest bytes match. Production source closure changes only
`cmd/sage-gui/config.go`; all dependencies, toolchain pins and build environment
match deployed `11f9…`. Binary SHA256:
`5b8542bdfbc508451f9fa90cd886e828bf301ab46ef659321897cf221ca0c072`.
Manifest SHA256:
`49d78fd38adc97aa60930b58a48aacb2b2f7b74ec4a199d45b5096d27fd2cb90`.
Version: `lantern-local.20260907-fresh-v27-private-policy`.

The coordinator's expected document has exact top-level fields `schema`
(`sage.lantern.b-private-transition.v1`), `unit_id`, `transition_id`, `boot_id`,
`native_boot`, `services`, `files`, `runtime_files`, `helpers`, `auth_reports`,
`before`, `after`, `scan_config`, and `storage_epoch`. All live values require a
new B observation; no sample success evidence or A process pins are supplied.

- `services`: all seven names in `SERVICES`, each with actual `pid`,
  `start_ticks`, `invocation`, `image_sha256`; model/broker/voice must be inactive.
  Only fresh B idle may instead explicitly carry `absent: true` with integer
  PID/start zero and empty invocation/image. This is not a loaded-empty pin.
  It requires live `LoadState=not-found`, inactive state, no job, PID, invocation,
  fragment, drop-ins, control group, transient unit or pending daemon reload.
  Every manager `UnitPath` is checked for unit/drop-in absence, including runtime
  and generator paths; even an empty canonical idle cgroup is refused. All
  processes are checked for idle cgroup membership or idle helper command lines.
  Paths and manager properties are rechecked after scanning; unreadable state
  fails closed. A loaded idle unit retains the original file/process/guard path.
  No idle unit or idle guard is created, stopped or represented as retained when
  the explicit absent path is selected. Any later appearance refuses transition.
- `files`: root-owned public files and SHA256, including every service fragment
  and original drop-in, coordinator itself, auth script, unit metadata and
  initialized receipt. Do not put config/key files or installed native here.
- `runtime_files`: exact installed R7 package `*.py` path/hash map. Extra or
  absent Python source refuses admission; runtime is not replaced.
- `helpers`: exactly the seven sibling filenames in `HELPERS`, each with root
  staged `path` and `sha256`. All pins are checked before loading utilities.
- `auth_reports`: actual current/historical receipt filename/hash map. Preserve
  all historical bytes without asserting their old outcomes are confirmed.
- `before`/`after`: `binary`, `manifest`, `binary_sha256`, `manifest_sha256`,
  `inventory_sha256`, `version`. Both binaries must be separately root-staged;
  `before.binary` is a verified copy of `11f9…`, not the installed pathname.
- `scan_config`: independently prepared B private mesh-shaped configuration
  with no peers/messaging/dispatch enabled. It is not installed or enrolled.
  `storage_epoch` is the real signed probe's string epoch, not an invented ID.
  Admission pins endpoint exactly `http://127.0.0.1:8080`, B's chain and ordinary
  agent ID, ordinary key pathname, and integer UID994. Alternate loopback stores,
  root identities, different key paths and mismatched UIDs refuse admission.

After review, run default `--expected PATH --expected-sha256 SHA` inspection,
then the same command with `--apply`. Inspection does not claim quiescence;
apply creates guards, stops UI/idle, scans twice, then stops pinned auth and
requires genuine confirmed drain before replacement. `--apply` success remains
locked with guards retained. A separate same-pins `--release` validates actual
completion, archives/removes only auth/UI guards and starts auth/UI locked for
the owner. Model/broker/voice/idle guards and native `Restart=no` remain; their
later transfer belongs to reviewed mesh/audio/bootstrap commissioning, not
this release. Final release rechecks all cold cgroups/effective guards and the
same native executable/listener/healthy locked boot. A changed boot must be a
canonical, nonzero UUID, never a missing field that merely differs from old.
If partial release/start fails, bounded cleanup independently attempts guard
restoration, daemon reload, pinned UI/auth stops, then physical containment and
native health verification. Log failures cannot skip cleanup. Cleanup never
claims a confirmed auth drain; failed verification is explicitly unverified.
`--request-child` is an internal one-shot stage, never an operator
shortcut: it requires retained swapped intent, all guards and confirmed drain.
Unknown apply or release is manual reconciliation, never an automatic retry.
Audio commissioning remains blocked until review and real owner unlock; no
broker/voice start is implemented in this coordinator.

Trust scope: root-controlled staging and system files, cooperative service UID,
not hostile root or same-UID compromise. The staging-only helper walks parent
descriptors, checks owners, exclusively creates its directory and fsyncs its
parent. No baseline/config/key export is part of this handoff.

## Test evidence

- 81 Python tests pass for initialization, pinned correction and coordinator,
  including partial guard writes, unknown drainage and ambiguous restart with
  real temporary-file preservation. Kernel/API observations in coordinator
  orchestration tests are simulated, not device acceptance.
- Direct scan-binding and health validators reject wrong local stores, identity
  and UID substitutions, missing/malformed/zero/unchanged boot IDs. Service-gate
  tests reject lost cold guards and nonempty cold cgroups; release tests cover
  auth/UI startup failures and final-check failure with cold fences retained.
- Absent-idle tests cover explicit schema, wrong unit/PID/invocation, loaded or
  masked states, runtime units/drop-ins, cgroups, helper processes, unreadable
  state, manager/job races, and absence of idle stop during apply. B static pins
  `93a5c286…` remain unchanged; no synthetic storage epoch was added.
- Both new Go tests pass on isolated matched Linux source with the patch.
- The unchanged native tags fail the new round-trip test after `SaveConfig`,
  reproducing the default-resurrection defect.
- Host macOS full-package compilation is unsupported by this Linux-only source
  closure; Linux tests ran in cached offline Docker without device access.
- Strengthened native test clears environment overrides, preserves synthetic
  identity/encryption fields, and toggles federation true then false so both
  persistence calls actually marshal. Config/resident-vault selections passed
  in `cmd/sage-gui`, `internal/store`, `internal/vault`, and `web`.
