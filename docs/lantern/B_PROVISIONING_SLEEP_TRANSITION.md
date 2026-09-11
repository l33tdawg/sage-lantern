# B provisioning sleep-mask transition — review candidate

2026-09-08. Source/tests only; **no B actions and no deployment authorization**.
Owner: this helper, guard-handoff completion verifier, their tests/docs. Mendel
retains coordinator/recovery ownership; Lovelace reviews through parent.

## Actual route, without changing R7

`transition-b-provisioning-sleep.py` replaces exactly these runtime symlinks'
inhibition with one verified, owned logind `sleep`/`block` inhibitor:
`sleep.target`, `suspend.target`, `hibernate.target`, `hybrid-sleep.target`, all
under `/run/systemd/system`, each literally pointing to `/dev/null`.
No generic `unmask`, `revert`, service start/stop, native rewrite, capture or
readiness fabrication. No further R7 adapters.

Original-success admission is **completed locked policy containment,
before original policy release**: native healthy and pinned; native/cold/auth/UI
guards effective; auth/UI/cold inactive and physically empty; idle explicitly
absent; original auth drainage checked. All systemd jobs must be absent,
logind must not be preparing sleep/shutdown, and no sleep-service PID/control
PID/cgroup or known sleep/idle helper process may be present. Unknown reads
refuse. Four exact masks, canonical vendor file hashes, manager UnitPath and
the unchanged other-inhibitor inventory are checked before each removal.
The explicitly recovered-release admission below instead requires retained
native/cold guards, the actual recovery auth process and independently inactive
UI. It accepts a legitimate current owner unlock only when native/auth lock
states agree as booleans; it does not reinterpret the historical locked receipt.

1. Inspect, then explicitly apply this transition under retained policy guards.
2. It exclusively claims root0700
   `/var/lib/sage-lantern-b-provisioning-sleep` before acquiring inhibition.
   The installed pinned `systemd-inhibit --what=sleep --mode=block` runs only
   this helper's internal resident holder, never an operator-supplied command.
3. Verify logind's actual `ListInhibitors` row against the exact wrapper PID,
   UID0, purpose and mode, plus wrapper/holder start ticks, executable hashes,
   argv and holder-parent relationship. Wrapper reparenting after CLI exit is
   allowed; PID reuse or holder reparenting is not. Existing inhibitors are
   neither removed nor appropriated.
4. Archive **all four actual symlinks** and their original metadata, then
   journal/unlink/reload/check each exact path. The inhibitor is checked before
   and after each step. Final verification calls ordinary R7 `_sleep_idle`.
5. The parent follows the unchanged policy release/unlock → mesh containment →
   guard handoff → mesh install/release → audio configuration and explicitly
   consented capture → reviewed model start/`ColdBrokerBootstrap` → actual first
   lifetime verification → startup installation/enablement → native guard
   handoff. Ordinary R7 sees unmasked targets throughout. Use `--status` at
   stage boundaries; the resident owner also watches its inhibitor identity.
6. Inspect and separately approve inhibitor release only after the exact
   completed native guard handoff. Its new **read-only** verifier reconstructs
   and rechecks real commissioning, active lifetime, installed startup ownership
   and unchanged B native identity. Candidate files alone do not qualify.
   A root-peer socket request is journaled once; holder independently rechecks
   completion and ownership. CLI reports released only after logind shows the
   owned inhibitor absent, not merely after a request or acknowledgement.

## Failure limits

No expiry automatically releases inhibition. Each request/subprocess is
bounded; the resident holder intentionally persists until reviewed release.
Unknown removal, reload or release gets **no retry/resume**. Inhibitor ownership
loss or a termination signal triggers at most one fail-closed restoration of
the four masks, without overwriting conflicting paths. It releases/exits only
if remasking is physically verified; unknown restoration retains any remaining
inhibitor and requires manual reconciliation. Unexpected socket/server failure
also cannot silently exit past this fallback. No automatic sleep/resume occurs.

Logind inhibition is **not** equivalent to masking against privileged direct
`systemctl start suspend.target` or kernel power writes. Maintain reviewed
producer containment, absent idle and exclusive operator control throughout.
SIGKILL, logind death, host failure or hostile root can defeat process-held
inhibition; monitoring/remasking is best effort, not an atomic kernel fence or
a guarantee of uninterrupted inhibition. Neither journal nor process existence
alone proves the lock. Release is not device sleep acceptance or RF silence.

Official protocol: [systemd-inhibit manual](https://raw.githubusercontent.com/systemd/systemd/v252/man/systemd-inhibit.xml)
describes acquisition before the child and release after it;
[systemd implementation](https://raw.githubusercontent.com/systemd/systemd/main/src/login/inhibit.c)
shows the retained Inhibit FD, parent/child ownership and `ListInhibitors`
`(what, who, why, mode, uid, pid)` fields. Target acceptance must confirm the
pinned installed executable/protocol rather than infer it from upstream alone.

## Expected input and commands

### Compatibility failure: separate reconciliation, never retry the old attempt

The first attempt failed before holder receipt: parent separately probed that
installed systemd 252.39 rejects `--no-ask-password`. That probe is not an original
error receipt. The corrected wrapper removes only that flag, retaining root,
DEVNULL stdin, exact holder argv and PID/image/logind ownership checks.
Run the new helper with ordinary expected/hash arguments plus `--executable-check`
before any UI stop or attempt claim. This executes only pinned target
`/usr/bin/systemd-inhibit --help` and `--version`, with five-second timeouts and
integrity checks before/after. Local tests simulate that contract; target execution
is still required. No host binary's help output substitutes for target validation.

The original `/var/lib/sage-lantern-b-provisioning-sleep` stays untouched.
Reconciliation input has exact keys `schema: sage.lantern.b-sleep-failed-option.v1`,
`boot_id` (actual current boot), and `files` (the exact three `FAILED_FILES` hashes
in the helper). Supply `--reconcile-expected PATH --reconcile-sha256 SHA` for
read-only inspection, then separately approve `--apply`. It exclusively claims
`/var/lib/sage-lantern-b-provisioning-sleep-failed-option`, copies original evidence,
records all four unchanged mask identities, and requires current containment,
no owned logind inhibitor row, absent PID81168, and no matching wrapper/holder
processes. Missing owner receipts alone are never sufficient. Unknown outcome
does not authorize retry or cleanup. No masks/services are changed by reconciliation.

After reviewed reconciliation completion, a separately approved normal `--apply`
claims `/var/lib/sage-lantern-b-provisioning-sleep-compatible-v2`, never the old
attempt. It rechecks archived/original evidence and mask identities before ordinary
admission. Update expected `self_sha256` only; retain original input bindings.
No automatic continuation is performed. Fresh owned-inhibitor absence was not in
parent's supplied observation and must pass the executable inspection on B.
Parent subsequently supplied zero owned-name/PID81168 inhibitor matches. The
helper still rechecks this live, and pins the actual installed executable SHA,
842-byte LANG=C help SHA, and original four dev24/inode2965–2968 mask identities.
The help/probe evidence is not substituted for an original failure receipt.
Candidate helper SHA: `56ee0cc1616a022afd919676cdff6805a8a705d5d2e58da0de0a568d460aa4a3`.
Combined local tests: 264 passed; reconciliation still requires independent review
and actual target read-only inspection before authorization.

Root-owned JSON exact keys:

```
schema: sage.lantern.b-provisioning-sleep.v1
boot_id: actual current nonzero boot UUID
self_sha256: reviewed transition helper SHA
handoff: {path: staged handoff-b-policy-guards.py, sha256: reviewed hash below}
coordinator: {path: original approved v5 coordinator, sha256: 33a8963d…}
policy_expected: {path: unchanged original policy expected.json, sha256: actual hash}
systemd_inhibit_sha256: actual /usr/bin/systemd-inhibit executable SHA
python_sha256: actual installed venv Python executable-image SHA
busctl_sha256: actual /usr/bin/busctl executable SHA
vendor_targets: four target-name → {path: /usr/lib/systemd/system/NAME, sha256: reviewed SHA}
unit_path: exact systemctl show --property=UnitPath output, including newline
other_inhibitors_sha256: SHA of sorted ListInhibitors rows encoded by helper.encoded
mask_origin: parent-provisioning-only-v1
```

Canonical vendor pin paths are `/usr/lib/...`; effective `/lib/...` fragments
are accepted only when resolving to that identical pinned file. Unknown
overrides/drop-ins refuse. Pin values above require real inspection, not invented
placeholders; no target discovery/download/install is executed here.

```sh
/opt/sage-lantern/venv/bin/python -I -B /ROOT_STAGE/transition-b-provisioning-sleep.py \
  --expected /ROOT_STAGE/sleep-expected.json --expected-sha256 REVIEWED_SHA
```

Append `--apply` only after approval. The same expected/hash with `--status`
checks ongoing ownership without mutation. `--holder` is internal, requiring
the exclusive stored attempt and actual pinned inhibitor parent; never invoke
it as an operator shortcut.

Release JSON exact keys: `schema: sage.lantern.b-provisioning-sleep-release.v1`,
`transition_sha256` (original expected bytes), `native_expected` (pin),
`native_complete_sha256` (actual native handoff completion hash).
Use the same base invocation plus `--release-expected /ROOT_STAGE/release.json
--release-sha256 REVIEWED_SHA` for inspection; append `--apply` once only after
independent approval. The helper's successful release receipt explicitly says
`sleep_acceptance_verified: false`.

## Separate failed-release recovery: implemented consumer, fresh pins required

Parent reports actual recovery-v5 success at attempt
`e2e9dbc6-dcb9-4964-8ffc-3cb063705b64`; the consumer hard-pins that attempt and
completion SHA `a088388d91419dad7de68a286985f92b0b5499ec10c41aa38e4e485f12fd15ca`.
Local approved recovery/coordinator source hashes were independently checked;
this session did not read B or independently observe its success.
Both failed recovery directories (`8c53b540-695e-4661-9599-30272e4b0c94` and
`e30159af-a38c-47ad-80d2-ef19f9882453`) retain exact six-file inventories and
hashes, checked before and after admission. All four historical auth receipts
remain immutable. Neither failed recovery may acquire `complete.json`.
Parent supplied successful `expected.json` SHA and installed source paths in
`/private/tmp/lantern-b-success-handoff-public-20260908.json`; the exact inputs
are below. No secret/auth token is needed. The pinned completion binds that hash.
Deployment remains review-gated. The reported live UI must separately become
inactive before admission; this helper does not stop it.

Parent reports original release failed. The original-success branch still
refuses that state. The new optional `recovery_evidence` branch explicitly
consumes the approved recovery-v5 schema, without executing recovery code.
**Actual pins below come from parent-collected public metadata, not invented evidence.**
Consumer integration remains subject to Lovelace/parent review before deployment.

- Approved recovery source: `a66eae8197e76fa4d7d8a06849e65a783c1564a299b406bcda1682b5d2c930ea`.
- Recovery observer coordinator: `c04ad4651381f295a179e354f40bb1e3f28d663d3c7ebd16ff0b75631fa23567`.
- Separate completion: `/var/lib/lantern-b-release-recovery-ATTEMPT/complete.json`,
  schema `sage.lantern.b-release-recovery-complete.v1`.

Implemented checks:
1. Explicit recovered-release evidence, not an alias for original success.
   Pin recovery code, its expected document and completion independently from
   the untouched original policy expectation/deployed-v5 hash.
2. Verify original failed-journal SHA/final failure phase, original native
   completion SHA and absence of original `released.json`. Bind recovery
   attempt path, expected SHA, boot/native boot, retained guards, new auth
   receipt and `fresh_services`. Require `historical_cleanup: unclaimed`,
   `audio_started: false`, `owner_unlock_required: true`; never invent drainage.
3. Mask admission then verifies retained native/cold guards and actual current
   recovery auth identity. UI must be independently contained/inactive before
   admitting the next provisioning phase; a historically active UI receipt
   is not evidence that it is stopped. Keep all existing no-idle/job/unknown
   producer gates. No new auth/UI stop/start is implied by receipt consumption.
4. Pass that same explicit recovery authority through mesh/native handoff and
   final inhibitor release; recheck actual lifetime separately. Do not call
   recovery `backend_for()` on completed recovery: it intentionally refuses an
   existing attempt. The consumer is read-only and never replays recovery.
5. Tests cover mismatched attempts, stale PID/boot, substituted original code, missing
   recovery completion, historical-cleanup inflation and forged original
   success. The original v5 coordinator binding remains `33a8963d…`, not `c04ad465…`.

Add the **identical** optional object to the sleep-transition expected document
and both mesh/native guard-handoff expected documents:

```json
"recovery_evidence": {
  "schema": "sage.lantern.b-recovered-release-evidence.v1",
  "recovery": {"path": "/opt/lantern-b-release-preexec-v5/recover-b-policy-release.py", "sha256": "a66eae8197e76fa4d7d8a06849e65a783c1564a299b406bcda1682b5d2c930ea"},
  "expected": {"path": "/var/lib/lantern-b-release-recovery-e2e9dbc6-dcb9-4964-8ffc-3cb063705b64/expected.json", "sha256": "1eeaddf792dba222045bb2e615b92c89ebedd74cca4653bd2a9b5f63095ccb2a"},
  "complete": {"path": "/var/lib/lantern-b-release-recovery-e2e9dbc6-dcb9-4964-8ffc-3cb063705b64/complete.json", "sha256": "a088388d91419dad7de68a286985f92b0b5499ec10c41aa38e4e485f12fd15ca"}
}
```

The recovery expected document separately binds observer coordinator `c04ad465…`.
Its installed path is `/opt/lantern-b-release-preexec-v5/deploy-b-private-policy.py`.
Later public storage observation reports unlocked, same boot/native boot,
current auth receipt SHA `6539396791ffa6c5f61dcc5caf7a3fc172afc5be0ab7d6bd21441fd5d98c0ca2`,
and storage epoch `7fa46fe0400977c91e93f5cf2b38a0e2ec5b96dc1a4eb1a7f81eb728c89774ea:2`.
This is observation, not perpetual authorization: current state must be checked
again before mutation. Audio inspection succeeded but capture remains unverified.
Its attempt UUID derives both canonical state paths; the consumer also checks
the exact successful recovery journal, archived guards/auth receipt, absence of
error/failure artifacts, and unchanged historical receipt hashes. No original
`released.json` is allowed. Mask admission/current mesh checks require the new
auth PID/start/invocation/image and current receipt to match. Later lifetime
checks treat recovery as historical authority and independently verify active
commissioning/startup. Final inhibitor release requires the identical recovery
evidence to survive that entire chain. UI stop/unlock are separate owner actions;
this consumer performs neither and does not claim historical drainage.

## Review evidence

Current transition SHA: `4caed65883e70906d7f16c2efdeb7f8cb61405f1e578add117223d5727b43fd4`.
Handoff with read-only completed-native verifier:
`3ac5ae9b542620176190511ab733b9dbed4affe9a7e1548b640298732d193930`.
61 recovery-consumer tests, 63 transition tests and prior handoff/mesh/lifetime
tests: **252 passed**.
Tests use temporary masks/archives and simulated Linux/logind/systemd/process
observations; no live inhibitor or hardware was exercised. Because Mendel's
working coordinator has advanced, tests explicitly load and verify the approved
v5 archive bytes rather than silently treating new code as the original:

```sh
LANTERN_B_POLICY_REVIEW_ARCHIVE=/private/tmp/lantern-b-policy-mask-review-20260908-v5.tar.gz \
PYTHONPATH=software/runtime/src PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q -p no:cacheprovider \
  scripts/tests/test_transition_b_provisioning_sleep.py scripts/tests/test_handoff_b_policy_guards.py \
  scripts/tests/test_b_recovered_release_consumer.py \
  scripts/tests/test_handoff_first_audio_lifetime.py scripts/tests/test_install_prepared_fresh_mesh.py
```

Next review: Lovelace reviews the exact mask/inhibitor core and this read-only
recovery-consumer delta; parent freshly collects the actual recovery receipt pins
before target inspection. Neither source review replaces device
acceptance, and neither authorizes unmasking B now.
