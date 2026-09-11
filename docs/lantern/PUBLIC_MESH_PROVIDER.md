# Lantern public mesh provider (source-only, default off)

`PublicMeshProvider` transports an exact approved `PublicProposal`, not private
chat or ambient history. Root supplies an existing dedicated ordinary public
SDK identity, the private actor's real protected `WorkflowStore`, `PublicMeshPins`,
an exact retained-approval verifier, and a current gateway/radio-contact verifier.
The contact verifier must establish the operator-pinned gateway and recent usable
contact, not merely trust a self-announced card or the radio's cached directory.
There is no key creation, secret file loading, enrollment, automatic activation,
or new daemon. The existing serial owner and supervisor lease remain mandatory.

## Composition contract

- `PublicMeshPins(gateway_key, radio_key, chain_id, domain)` pins the existing
  protocol gateway signing key separately from its complete MeshCore radio key.
- `PublicMeshProvider(workflow, identity, pins, *, approval_check, contact_check,
  clock=..., timing=...)` has no external side effects during construction.
  `PublicMeshTiming` must come from trusted calibration; missing timing prevents
  admission. `preflight(proposal, receipt=...)` checks the complete serialized
  public content limit and the round-trip budget before signing/reservation.
- Before starting workers, root calls `runtime.attach_public_mesh(provider)`;
  the provider must use that runtime's exact store, not another untracked owner.
- An authenticated explicit action calls
  `runtime.public_begin(context, proposal, request_id=uuid, receipt=None)`.
  A receipt selects the protocol's signed GET poll instead of a POST submission.
  The existing context authority must explicitly authorize `public_begin`;
  the provider separately verifies exact retained public approval.
- `runtime.public_inspect(context, request_id)` returns `(PublicMeshProgress,
  result_or_none)`, requiring `public_inspect` authority. Progress has
  `request_id`, `state`, `next_fragment`, `fragment_count`.
- Existing supervised jobs batch at most four public fragments and recheck
  approval, contact and lifecycle authority per frame. Active public exchanges
  use a 10 ms polling interval; idle operation retains its configured interval.
  Receive jobs route public fragments before private inbox parsing. No HTTP
  public-mesh browser route or CommunityService capability is activated here.
- Root's public action grants, current contact proof, gateway fragment routing,
  and deployment settings remain integration gates, not default-true callbacks.

## Wire and persistence

`LP1:` is the dedicated public fragment discriminator. Its remaining bytes use
the existing `SL1:` fragment encoding; `public_fragments(sealed_wire)` produces
it. The gateway maps only that discriminator back for bounded reassembly, calls
the frozen `PublicGatewayBridge.handle(wire, author=pinned_author)`, and returns
`public_fragments(response)`. A prefix or radio sender address is never identity
proof. Both directions retain the author-preserving signed, sealed `LPM1.`
protocol; the gateway does not re-author the REST operation.

The provider signs REST only after contact checks. The <=120-second signed
request wire lives in memory, never in the durable queue. Protected CAS records
retain exact logical proposal, actor/chain/gateway binding, request UUID/digest,
expiry and progress. Each local row also has a signature binding its ID and
revision. A separate operation slot prevents a new nonce from resubmitting the
same operation after uncertainty. Private source/session IDs are not arguments
to the wire codec and are not added to the transmitted proposal.

Before RF, progress becomes `dispatching`. A confirmed serial response advances
one index, ending at `awaiting_response`; neither means canonical submission.
Timeout/unknown never retries. Restart cannot reconstruct a signed HTTP packet;
an unsent/partial request without its memory packet reports `unknown`, not a
fabricated durable RF queue. Expired packets leave the dispatch set. No automatic
re-signing, failover, deletion, or retry recovery is provided.

Responses are reassembled in eight bounded, signed, protected CAS slots, each
expiring after 120 seconds. Allocation searches for an existing matching assembly
or a free/expired slot; live assemblies are never evicted. Exhaustion fails
closed. Final verification binds gateway, author, request
UUID/digest, expiry and proposal. Nonce consumption and the exact result commit
in one CAS **before** returning any result. Duplicates do not re-consume or
re-expose it; authenticated inspection can read the stored result. Lost CAS
acknowledgments return unavailable and require reconciliation. Records use the
existing workflow quotas; there is no plaintext index or unbounded fragment log.

Gateway results always retain `trust_basis: pinned_gateway`,
`acceptance_available: false`, `consensus_verified: false`, and
`cache_eligible: false`. Even gateway-observed `committed` is not green acceptance
or a verified cache hit. Protected-store declarations in unit fixtures are
synthetic; serial/gateway tests do not prove physical RF or native activation.

## Timing acceptance

The budget reserves at least 30 seconds for gateway processing and 10 seconds
for safety, and must remain strictly below the 120-second authentication window.
The frame bound must cover both directions, actual four-frame bursts, firmware
queueing, empty receives, contact reads, protected-store calls and scheduling I/O.
It is an admission bound, not a transmitter pacing or airtime control setting.
Immediate synthetic acknowledgments do not establish sustainable RF throughput.
While a public exchange remains active, the dispatcher returns before scanning
or sending private queued traffic. A mixed-queue regression checks that private
work remains queued during the response wait. Native calibration still must
cover actual scheduling and I/O; do not activate from these unit-test results
alone.

Drain runs under the existing runtime fence/deadline, checks signed request rows
and refuses unknown dispatch; close discards all transient REST wires after join.
