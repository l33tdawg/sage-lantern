# Public mesh root/IPC composition — staged, not activated

The existing daemon optionally consumes `public_mesh` in its root-owned
`mesh-runtime.json`. Omit the field to disable public IPC; `null` or partial
configuration is invalid. No user/HTTP caller supplies configuration or keys.
The exact object fields are:

- `identity_file`: existing ordinary public key file below the existing identity
  directory, different from the private identity file.
- `author`: independently enrolled ordinary public actor, different from the
  private storage actor. File loading uses the existing no-follow/key-pinning
  loader; no key generation or enrollment occurs.
- `chain_id`, `domain`: explicit public destination.
- `gateway_key`: full independently approved protocol gateway signing key.
- `radio_key`: separately pinned full MeshCore gateway radio key.
- `storage_actor`, `storage_chain`: explicit authorization to persist public
  workflow metadata in this exact private actor/chain. Both must match the
  daemon's already guarded protected storage, not a second untracked client.
- `timing`: exact `frame_seconds`, `gateway_seconds`, `safety_seconds` object
  validated by `PublicMeshTiming`. Supply independently measured/conservative
  link bounds; there is no synthetic timing default for production.

The preparation helper additionally requires the configured public author to
match the verified inventory's PUBLIC_PROPOSER_FINGERPRINT (SHA256 raw public
key). This consistency check is not proof of enrollment or public capabilities;
the operator and gateway still verify the ordinary identity's actual privileges.

## Root lifetime

Before USB open, root loads/pins the independent public identity and verifies
existing public mesh records through the provider's drain scan. Unknown
dispatch blocks startup; nothing is erased or resubmitted. After the private
runtime factory returns, root attaches public handling to its exact WorkflowStore
before starting background workers. All jobs use the existing scheduler,
process/epoch authorization and storage lifecycle lease. No extra thread owns
serial and no public HTTP packet is persisted by this composition.

`HardwareRuntime(..., public_contact_check=callback)` accepts a trusted root
integration callback receiving exact `PublicMeshPins`. Without an injected
callback, root uses `GatewayContactProbe.current`: signed nonce replies bound
to the public author, gateway, radio key, chain, domain and bounded lifetime.
`public_ready` can durably arm this probe. The existing owner scheduler sends
bounded `LC1:` bursts; replies pass through the durable receiver before the
probe becomes current. Gateway runtime routing verifies the signed probe and
queues a signed reply in protected CAS slots without issuing REST requests.
Arming or serial acceptance alone cannot authorize submissions. Unknown writes
or dispatches deny clean drain; proof expiry never authorizes automatic retry.

The provider supports request-bound contact admission through
`contact_admission=probe`. Admission requires a currently verified LC1 response
and binds an opaque in-memory token to the exact request, proposal, destination,
digest and deadline. The admitted exchange may finish after the short contact
freshness window, but never after its original deadline; authorization and
configuration checks remain live. Restart does not reconstruct this authority,
and a new request requires fresh contact. The root composition must explicitly
wire this interface; standalone provider tests do not prove that wiring.

Before signing or dispatch, root resolves the exact retained source approval
through `PublicWorkflowStore`, verifies its storage-actor signature and exact
proposal binding, and refuses cancelled, missing, unsigned or altered approvals.
Reservation and an archived result do not revoke signed consent. An exact
content-bound recorded receipt may authorize status polling, not submission
retry. Mesh provider slots own mesh idempotency; cross-transport fallback/retry
is not authorized.

## Voice facade contract for Hooke

Root also implements `public_ready`, `public_prepare`, `public_queue`,
`public_list`, and `public_sources` for `CommunityPublicMeshFacade`. Queueing
persists a signed root intent before source approval and provider admission;
repeated intents never implicitly resubmit. Lists report queued/pending/unknown,
not authoritative acceptance.

`MeshVoiceFacade.public_begin(proposal, *, request_id, receipt=None,
access_check, on_submit) -> PublicMeshProgress`

`MeshVoiceFacade.public_inspect(request_id, *, access_check)
-> (PublicMeshProgress, result_or_none)`

These use existing bounded Unix IPC, exact supervised peer credentials, broker
binding, device epoch and repeated original-request guard challenges. Begin
sends exact `{proposal, request_id, receipt}`; inspect sends `{request_id}`.
No signer, path, endpoint, policy, approval boolean, or source history is sent.
`on_submit` conservatively marks possible submission before IPC transmission;
no automatic replay follows a lost reply. A poll receipt selects the provider's
signed GET operation, not a new proposal submission.

Progress is queued/dispatching/awaiting_response/consumed/unknown, not delivery
or acceptance. Results retain `trust_basis=pinned_gateway` with
`acceptance_available`, `consensus_verified`, `cache_eligible` all exact false.
Even gateway-observed committed is not green acceptance or canonical cache data.

## Tests and remaining integration

The synthetic integration test goes through actual facade/socket/root dispatch,
real protected SDK MockTransport records, retained signed approval, and the real
background scheduler with measured 100ms synthetic send/receive delays. It does
not manually call dispatch per fragment or alter polling/pacing constants. The
gateway uses the actual bridge/protocol; no RF/native timing proof is claimed.

Activation still requires an explicitly configured, operating gateway role,
measured radio timing, public identity enrollment verification, matching runtime
and SDK builds, and native confinement/radio acceptance. Synthetic client and
gateway owner-loop tests exercise signed probe exchange and clean drain; they
are not native RF reachability or timing measurements. No deployment occurred.
