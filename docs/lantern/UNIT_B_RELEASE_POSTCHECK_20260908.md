# B retained-inhibitor reconciliation and independent postcheck

## Actual result

The single reviewed reconciliation exited **0** and returned `released:true`,
`physical_verified:false`, `reboot_verified:false`. No audio, native, auth or
display restart was requested. Original failed-release evidence remains intact.

Attempt directory:
`/var/lib/sage-lantern-b-failed-inhibitor-reconciliation/5e71e13b-0fd2-46a9-b7ab-87e4df23e906`

| Artifact | SHA256 |
| --- | --- |
| `expected.json` | `7ea8670b8447436703ff02726bfd0b022bab91e7619a9f0f8f792c13eaa1b142` |
| `complete.json` | `1c064232d0e60e64fd224805938033cff563acb8b3137e2b341852332269cca4` |
| `owner.json` | `4d93de9abcfeb02ff7437f3f26501a7135758fd80939c415e32646afc2cca260` |
| `release-authorized.json` | `c90e29147854c40f36cf73b0bfda88f7c9f5171d3c4e1e73c2bb61f5a16c4c3d` |

`/opt/lantern-b-failed-inhibitor-review-20260908/apply.stdout` SHA256:
`6a98e9fedb8920f586046b5034ea3be63be410107c1d5d994aabb5a32a3a2ee7`.

## Independent read-only checks after completion

- Completion hash/schema verified. Receipt root-owned 0600; attempt directory
  root-owned 0700; ancestors root-owned, nonsymlink and not group/world writable.
- Both the retired and replacement holder/wrapper processes were absent.
  Their exact owned inhibitor rows were absent.
- All four intended `/run/systemd/system` provisioning masks were absent.
- Unmodified `SuspendSystem.verify_sleep_idle().confirmed` was true.
- `audio_readiness.operational_ready()` was true.
- All six pinned service manager states, PID/start identities and executable
  hashes matched the pre-apply expected document; restart counts remained zero.

| Service | Main PID |
| --- | --- |
| personal-sage | 9348 |
| device-auth | 62591 |
| llm | 159624 |
| mesh-runtime | 160724 |
| voice-loop | 160767 |
| display-power | 279119 |

The first postcheck wrapper exited 1 only because its final reporting command
used `check_output(systemctl is-enabled display-power)`, for which disabled is
exit 1. All preceding checks had passed. A separate read-only `systemctl show`
check then exited 0 and confirmed display **active/disabled**, no drop-ins and
no pending reload. This did not trigger an apply retry or service action.

## Boot closure verified without reboot

Startup service is **enabled/inactive**, PID 0, no drop-ins or pending reload.
Installed startup config validation and every referenced pin passed:

| Component | SHA256 |
| --- | --- |
| coordinator | `57d7fba9f191781011ef48dc62886cc6ec677e4b300d66a1d1e40e5da9033b08` |
| adapter | `4b9bb038effea9d538cdf5e5daa3503c7aead97158290c322a782f5a377ae7d2` |
| recovery helper | `5f96803ce6a8fc5014f77dff8bff7e4d0d0a94ac70a7eece16f27331cfcdba6a` |
| collector | `40a4dd7003acc5430b4f82db90bc31500f3320284641fcb6d72a50bcb06b93cf` |
| R7 wheel | `6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440` |
| baseline | `5d678ca3762fff70b921aceb0dc64232393a30179cb4ccbc789a604b97c1c2d2` |
| policy | `bf9621cd97750489b60057d274c8074c7aa2c840ca36bd3296c69791cff855f8` |
| initial lifetime | `dae23ef9921356133114ba5f3e51587a1bdfe6a3e8dd27f2c7646995b0241604` |

B boot remains `af128139-eb7d-4984-b152-8402997c9f83`. The startup service was not
started in this boot. No reboot was performed or accepted.

## Remaining acceptance

Owner screen-off → touch-wake → PIN test is still pending. Display boot enablement
is deliberately off until that acceptance. Deep sleep and reboot acceptance are
separate. Current ordinary-idle readiness is not physical suspend/wake evidence.
The unrelated world-writable vendor camera unit remains unchanged; see
`UNIT_B_DISPLAY_COLLECTION_20260908.md` for the security finding.
