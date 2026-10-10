<!-- SPDX-License-Identifier: Apache-2.0 -->
# Public debt preflight

Status: draft source contract. Version: 0.1. Owner: SAGE Foundation / Lantern maintainers. Updated: 2026-10-11. Scope: bounded observation of retained public bench metadata. This extends the [parent accounting contract](MESHTICULUM_PUBLIC_PARENT_CAPS.md) and supports [SYS-003, SYS-006 and SYS-011](../REQUIREMENTS_TRACEABILITY.md). It grants no device window, enrollment, start, refund or release approval.

Parent enrollment needs independently observed prior debt. A journal constructor is unsuitable for inspection: AttemptJournal and IpJournal can recover pending reservations, update fences or create ownership state; authority, SessionStore, NativeCaps and original_context constructors can also mutate native state. Existing cache-status uses original_context and is excluded from strictly read-only debt collection.

The new collector reads only explicit public grant/active/provision/job/result and attempt/byte/wire metadata in a fixed run allowlist. It never reads secret.bin, sender.bin, receiver.bin, private SAGE stores or model/evaluation files. Root-owned modes, single-link files, no symlink traversal, held descriptor identity, existing nonblocking owner locks, bounded input sizes and before/after hashes guard the reads. Missing, changed, busy or unsupported data stays unavailable.

SQLite parses a bounded held-file image in query-only memory. No SQLite connection opens the native database path, and no recovery or checkpoint is requested. All native WAL, SHM and rollback sidecars refuse a complete row observation. A closed WAL database can retain header flags; only the isolated parser image changes those flags. Native bytes and hashes stay unchanged. This parser is not a consistent reader for an active WAL database.

## Scope and launcher

The verified public runtime exposes `pilot-debt-snapshot <unit>` with required `--scope-file`, `--scope-sha256`, `--expected-boot` and `--observation-end-epoch-us`. The state root stays `/var/lib/meshticulum-bench`. The scope file declares schema `meshticulum.public-debt-scope.v1`, unit/current boot, declared runtime hash, exact manifest hash, fixed observation expiry and the explicit run allowlist. Each run carries its original boot, transport, raw grant-file hash and independently supplied public grant/attempt binding digests. Null binding denotes unavailable provenance, never zero debt.

`grant_sha256` hashes exact grant.json bytes. Despite their field names, `grant_context_sha256` and `attempt_binding_sha256` contain the existing 32-byte digest encoded as lowercase hex; they do not hash the digest a second time. Radio attempt binding additionally depends on MCU/host/profile identity and must be independently supplied. An IP attempt binding matches its public grant context.

The launcher verifies root-owned runtime files against the full manifest, observes native facts before/after, checks the unit/boot and the exact fixed observation expiry, and opens no serial, HAT, socket or radio worker. The installed directory records only a runtime hash prefix: the archive hash remains declared/unobserved, while the full manifest and listed payload hashes are observed. A scope file or hash is a read-selection contract, not approval or provenance authority.

## Accounting and limits

Count every radio reservation row and its estimated microseconds, including RESERVED, UNKNOWN, REFUSED and NOT_SENT. The owner request sequence is not a row count. For IP, retain every outgoing attempt's full reserved bytes and frame count; RX debt includes settled, pending and unknown receive bytes. Keep nonce/counter state, fences, original deadlines, row identities and admitted caps separately. Never treat elapsed time, revocation, a missing process or a smaller actual result as a refund.

The first collector observes scoped legacy journal rows and safe parent references. It does not reconcile authoritative parent prepayments, their prior lineage or overlaps, complete all historical inventory, prove current terminal ownership, or collect physical/MCU facts. `complete_global_debt` and `eligible_for_enrollment` therefore remain false even when every scoped row is readable. Known scoped actual totals are diagnostic; they cannot replace already paid full caps or justify enrollment. A missing admitted cap or binding remains null/unavailable.

The historical review identifies 38 child snapshots, 37 complete cost rows and one unresolved C child. That allowlist does not prove current inventory completeness. A fresh finite observation handoff must confirm current writers, boot/service lifetimes, scope and source pins. First-phase commands may read existing pilot-facts, pilot-status with stop=False, and public pilot-result metadata. They exclude cache-status, constructors, device opens, signals to old jobs, ARM/TX/DISARM, reset/flash, deployment and service changes.

A/B HELLO/STATUS requires a separate reviewed finite device-ownership scope. Existing inspect opens/configures serial and may create ownership locks, so it is not part of strict read-only collection. C has no MCU counter provider; opening LinuxHAT cannot manufacture one. Holder/process absence cannot establish physical quiescence. Current complete debt, MCU overlap, C containment and independent enrollment approval remain open. Comparisons and physical performance qualification retain their existing requirements.

## Source verification

The reviewed collector has 31 focused metadata/SQLite controls and nine launcher controls. The final integration suite passed 986 tests with one expected skip on 284 unchanged Python inputs. Root identity and native facts in these fixtures are explicitly modeled. These checks establish source behavior and file preservation; they do not qualify current native debt, enrollment, device ownership or physical performance.

The [parent accounting reader](MESHTICULUM_PARENT_DEBT.md) adds separately scoped controller/native full-cap and legacy identity relations. It retains opaque prior lineage and global/enrollment limits; its observation creates no new authority.
