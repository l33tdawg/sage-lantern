# Cold Unit A auth capability supersession

This is a separate recovery observation, not a drain receipt, activation grant,
or proof that historical requests completed. It does not alter the idle/sleep
consumer or runtime. Unknown original receipts remain byte-for-byte unchanged.

## Frozen producer/consumer contract v1

Shared validator: `scripts/lantern/auth-capability-supersession.py`.
Curie owns the producer in `update-fresh-native.py`; the producer must retain its
durable start inhibition and independently capture the before-state before any
stop or swap. A signed restart response is only acceptance, not completion.

Evidence has exactly these fields:

| Field | Value |
| --- | --- |
| `schema` | `sage.lantern.auth-capability-supersession.v1` |
| `unit_id` | `unit-a` |
| `boot_id` | Exact Linux boot UUID, unchanged throughout |
| `root_id`, `chain_id` | Reviewed installed public identity/chain |
| `initialized_sha256` | Exact immutable initialized-public receipt hash |
| `native_before`, `native_after` | Each exactly `binary_sha256`, `manifest_sha256`, `inventory_sha256`, `version`; inventory is the native manifest's inventory hash, not its source-file map |
| `sage_before`, `sage_after` | Each exactly `pid`, `start_ticks`, `invocation`, `uid`, `application_boot_id` |
| `current_auth` | Exactly `pid`, `start_ticks`, `instance_id`, `uid`, captured before stopping auth |
| `unknown_receipts` | Object mapping each prior invocation ID (without `.json`) to its original byte SHA256 |
| `current_auth_report_sha256` | Hash of the actual completed current-auth drain receipt |
| `capability_invalidated` | Literal `true` |
| `historical_request_completion` | Literal `unknown` |

Both image records and all identity fields must match a separately pinned
transition binding, not context copied from untrusted evidence. Source versions
are manifest versions. Same-binary recovery is representable. This consumer
supports the reviewed exec-in-place path: PID/start ticks/invocation/UID stay
identical, while the application BootID must change. A systemd replacement with
a different PID is a different recovery contract and is rejected.

The binding is exactly `BINDING_FIELDS` exported by the shared module: all fields
above except `schema`, `sage_after`, `current_auth_report_sha256`, and the two
claim fields. No extra fields, credentials, cookie values, or private keys belong
in either document. The documents contain only public identities, hashes and
process metadata. There is no automatic rollback claim.

## Recovery entry is not normal preflight

`preflight-fresh-native-update.py --recovery-entry --expected PATH
--expected-sha256 SHA` retains all ordinary observation gates but permits exact
startup-shaped prior receipts (all three flags false, cleanup unknown). Malformed
or partially drained receipts are not admitted by this narrow variant.

The result has `recovery_entry_verified=true`, `preflight_verified=false`,
`mutation_authorized=false`, `current_auth_drain_required=true`, and the exact
unknown receipt map. It is only an input to a separately authorized contained
recovery. Default preflight still requires confirmed prior receipts.

The driver must still stop the current auth invocation, independently verify its
empty cgroup, and require its real receipt to have closing/drained/local_revoked
all true and upstream_cleanup confirmed. An old unknown receipt cannot excuse
a failed current drain. No force-kill or timeout becomes confirmed cleanup.

## Read-only consumption while containment remains

```python
observer.read_supersession(
    evidence_path, evidence_sha256,
    helper_sha256=reviewed_shared_helper_sha256,
    binding=pinned_transition_binding,
    manifest_path=reviewed_new_native_manifest_path,
)
```

The installed sibling helper and evidence must be root-controlled and hash
pinned. The reader verifies actual installed binary/manifest, public identity,
unchanged Linux boot, the new health BootID/version and locked encryption state,
the running native inode/process identity and uniquely owned IPv4 loopback 8080
listener before/after health, retained update guard, and inactive
empty auth/producer cgroups. A genuinely absent idle unit is allowed only via
the existing absent-unit/canonical-empty-cgroup checks. All remaining receipts
must be either exact bound unknowns or confirmed receipts, including the drained
current invocation. It repeats the live observations and rereads the evidence
under the observer's aggregate deadline. It performs no writes or activation.

Success returns only capability invalidation, historical completion unknown,
mutation unauthorized, and receipts not rewritten. This is not an enduring lease:
the caller must retain serialization and guards through its next transition.
Future active-auth, idle, or runtime consumers need separately reviewed
integration; this reader deliberately refuses after auth has restarted.
