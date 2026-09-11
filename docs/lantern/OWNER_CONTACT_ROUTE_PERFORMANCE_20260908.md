# Owner route auth fanout: future-source revision

No deployment, native-kind integration, voice edits or Curie A-vision frozen pin
changes. This changes only the future auth helper, root route and route tests.
Daemon/forwarder behavior and all durable WorkflowStore checks are retained.

## Measured fixed workload

Actual incoming HTTP on the same threaded synthetic auth server used for recursive
auth requests, real DeviceLock session, actual daemon dispatch over socketpair,
and the existing SDK/WorkflowStore synthetic native-storage transport. One explicit
preview followed by one confirmation issues revision 1 with identical TTL/scopes.
Counts exclude the two incoming browser requests, which are unchanged.

| Recursive auth HTTP calls | Before | After |
| --- | ---: | ---: |
| Issuance preview | 185 | 16 |
| Issuance confirmation | 495 | 40 |
| Total | **680** | **56** |

That is 624 fewer requests (91.8%). Baseline: 272 `/authorize`, 272 `/access-epoch`,
136 `/profile/get`. Revised: 56 `/owner-contact-snapshot`. These are call counts,
not board latency, TTFT or throughput claims. No device performance benchmark ran.
Already-issued export additionally measures 13 preview + 21 confirm requests;
exceptional abandonment reconciliation remains 24 + 177 because its durable
evidence/readback verification is intentionally retained. Tests enforce all four
operation budgets, rather than hiding the remaining reconciliation cost.

## Why this does not cache authority

- The helper-only POST endpoint returns token-authenticated epoch + profile in one
  response. Browser Origin/OPTIONS/GET are rejected; invalid/locked tokens fail.
  It samples and rechecks the access generation around validated profile loading.
  Profile IO is outside the auth gate, so locking cannot be stalled by filesystem
  IO. This is generation-fenced validation, not a claim of a transaction spanning
  independent future profile writes.
- Each access guard gets a new forwarder session/epoch acknowledgement, then one
  fresh root-authenticated snapshot and local physical/config/provider checks.
  The old redundant before/after five-request sequences are removed.
- Pure profile/binding readers within the synchronous core validation cycle use
  the already-observed data plus local lifecycle/config checks, rather than
  recursively initiating another complete auth exchange. They do not return or
  cache an authorization decision. Data are still compared against a fresh
  snapshot at each access guard and discarded from request context on completion.
- All access callbacks around native storage IO remain live. Tests instrument the
  actual SDK PUT boundary and prove new auth observation before each durable write
  and after successful writes. Lock/profile revocation after reservation prevents
  finalization/export and retains reserved evidence. Forwarder-detected lock can
  deny before an additional HTTP snapshot; no redundant request is required after
  positive denial. Export requires fresh approval and fresh final access checks.
- No elapsed-time cache, grace period, auth disabling, retry or stale-positive
  fallback. The existing consent-window limit is not an authorization cache.

## Frozen review

`/private/tmp/lantern-owner-contact-route-perf-review-20260908-v1` includes baseline
source/logs, final source, exact manifests, narrow patch, full test fixtures and
commands. Canonical native-kind integration remains separately coordinated.
Future auth helper: `a276b1a322bdeb1b3e9543232207a0b9d11cbdd44a5be511fb12bfb8bd7b1260`.
Root route: `8addd003b5977563f00b8710c42f4beac36678969dd7dc57d2fdde8a9e25a15a`.
Mixed old auth/new root fails closed (no compatibility bypass). Existing deployed
or frozen activation receipts/pins still describe their original artifacts; do
not repin them to pretend this source ran. Parent/Curie own any future activation.
