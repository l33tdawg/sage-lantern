# A auth-maintenance evidence for independent review

Read-only observation on boot `2701621b-86ee-4302-8a90-98f70cff9046`.
No receipt, service, fence or private ledger was modified for this inventory.

## Corrected count and meaning

There are 12 receipts: **8 historical unconfirmed**, 3 historical confirmed,
and 1 currently active/unconfirmed. The earlier aggregate of 9 unconfirmed
included the active instance; it did not mean 9 historical failures.

Active auth invocation: `1c9a8012602a440da505103180fd084b`.
Its receipt hash at observation was
`f4a17f0a1dfe83a5f1b758f712ca276c66113d9d7f97d358cdaad2857553c770`.
This receipt must be freshly observed after any actual stop, not treated as
immutable or adopted as historical cleanup evidence.

All eight historical unconfirmed files have `closing=false`, `drained=false`,
`local_revoked=false`, `upstream_cleanup="unknown"`. These are the initial
receipt values emitted by `DrainReport.__init__`, not proof that operations
remain running. Conversely, old process exit does not upgrade them to a drain.
The inventory alone cannot establish why final report publication was absent.

Directory: `/var/lib/sage-lantern/device-lock/drain/`.

| Historical filename | SHA256 |
| --- | --- |
| `1d3daa084de74bd0bf3116727017a1be.json` | `769e13a576f6ac87d5a864a1b9c63f6611406784c1e242a1fd36b52073e6400d` |
| `4f723dbfd037428087d402ab7c6da7b4.json` | `779d2fb35e75674b5384e166bc6d39ca079f82700ba18fa2ebc7768939110b5e` |
| `8074b590c6d24e298381d6ebe9d2b9fc.json` | `57bebd51772227890d9b8c8347d23de23f583b7d66b3923b4e33fa89a4209664` |
| `9e0832e50e004fef9c2158eac6b76825.json` | `8de8e56be8b1d44a14b42195ee9a1879d707eb73375f199b5d4d93927e808f25` |
| `a083fd0809cd4a24b03b31f82240cf8d.json` | `06df41e0bdcba2888666e52a59d604533aecb53389913117f39dce84c0c0ff4e` |
| `a3e8ed08755445e2878451904ce47dc9.json` | `36d77a20f05e0c85b1bfbaef9e5c8460aa5a05d45bbb8d0bbb3494da82936e5b` |
| `ddd073e0a06f45fea0cd28858e368a85.json` | `f109390234cdfd7b239d2099210b8b4b0a002c47a360f6bf75362a344b224c8c` |
| `f2d44df3ec314a89912ce0a8049534ef.json` | `fbc2b0e2e51c4cb56fee4e2d11e7e72729cc4b8a02b9741d53314aa1709cc688` |

## Existing evidence does not cover the whole set

`/var/lib/sage-lantern-cold-native-update/capability-supersession.json`
SHA256 `2a2cb07b3f00f6ecb277eb2bbeebb17f3fd0022ff799431935aa6d763546b075`.
Its boot is `7fc2a334-26e4-48e8-b585-42e1072c4346`; its unknown-receipt map
contains only `8074b590...` and `9e0832e5...`, with the hashes above.
It explicitly retains historical request completion as unknown. It does not
cover the six later historical receipts, and its original live-proof validator
requires the bound boot/native process. It cannot simply be revalidated against
today's live process or used as an all-receipts exemption.

## Exact code gates and reviewer decision

- `scripts/lantern/unit-a-device-auth.py`: `DrainReport.__init__` writes the
  exclusive initial false/unknown receipt; `main` calls `report.finish` only
  after `server.server_close` returns.
- `software/runtime/src/sage_lantern/storage_supervisor.py`:
  `RootVoiceBackend.drain_prerequisites`, lines 145–168, requires every receipt
  to be confirmed, after verifying the real sleep fence/guards and inactive auth.
- `scripts/lantern/lantern-idle-suspend.py`: `prior_auth_drained`, lines 126–140,
  excludes the current invocation but rejects any other unconfirmed receipt.
- `scripts/lantern/auth-capability-supersession.py`: `validate`, lines 128–148,
  is scoped capability invalidation, never historical drain or mutation authority.

Lovelace review requested: define a separately supported current-maintenance
admission that preserves the exact old unknown receipts and verifies current
auth closure, producer exclusion, native capability lineage and outstanding
work. Do not delete/archive receipts merely to make the legacy all-confirmed
predicate pass, and do not broaden the two-receipt supersession binding.
Until that contract is reviewed, no stop/drain operation is being submitted:
the installed audio remains functional and baseline observation continues.

## Follow-up live cgroup and coverage check

All four canonical cgroups existed with `cgroup.events populated=1`, one PID
each, no child cgroup, and no pending unit job; repeated unit snapshots matched:

| Unit suffix | PID | Invocation |
| --- | ---: | --- |
| `device-auth.service` | 1275 | `1c9a8012602a440da505103180fd084b` |
| `voice-loop.service` | 49210 | `0f6f563931b64726b104db7471c219b5` |
| `mesh-runtime.service` | 49153 | `139312152ccd4bb0a91a67822295115d` |
| `llm.service` | 47458 | `7239f4283524450c93c5b07ad000282a` |

Full unit names use prefix `sage-lantern-`; cgroups are under
`/sys/fs/cgroup/system.slice/<full-unit-name>`. This is active occupancy evidence,
not containment. Exact hash comparison against supersession confirmed coverage
only for the two named historical receipts. The other six historical receipts
and current receipt were absent from that evidence map; no mismatched covered
hash was silently accepted.

The follow-up signed double-scan attempt refused with `ACCESS_AVAILABLE False`
before `load_identity`. No key was loaded and no signed scan was performed.
Fresh owner unlock is required for the requested stable signed reconciliation;
prior successful scans are not substituted for a current proof.
