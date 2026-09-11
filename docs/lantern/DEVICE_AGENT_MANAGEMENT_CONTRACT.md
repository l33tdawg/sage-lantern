# Lantern Device Agent management contract

**Status:** Implemented protocol core; transport integration pending  
**Version:** v1  
**Authority plane:** Dedicated fleet device management

## Boundary

The Device Agent uses a locally generated Ed25519 identity that is distinct from the personal SAGE, Commons proposer, MeshCore radio, and TLS federation identities. Its private key stays on the Lantern. The contract does not expose a shell, personal memories, CEREBRUM Root operations, validator operations, or arbitrary service commands.

## Implemented contract

- Observations contain a device ID, monotonic sequence, observation time, explicit freshness expiry, clock-confidence label, sanitized state, and canonical state hash.
- The Device Agent signs the full canonical observation with its dedicated management key.
- Commands are signed by a pinned fleet authority and target exactly one device.
- Every command carries issue/expiry times, a monotonic sequence, an expected prior-state hash, a desired revision, one allow-listed declarative action, and bounded JSON parameters.
- The Lantern checks schema, target, time window, signature, expected state, installed local handler, and replay state before execution.
- Acceptance is persisted before execution so a restart cannot silently run the same mutation twice.
- Every accepted command produces a device-signed success or failure receipt. Failure receipts expose a bounded reason code, not exception text or private diagnostic content.

Allowed v1 actions are `apply_ai_preferences`, `apply_device_profile`, `collect_sanitized_evidence`, `run_diagnostic`, and `stage_release`. Each action still requires an explicitly installed local handler; allow-list membership alone does not authorize execution.

## Local protocol exercise

```sh
sage-lantern-device-agent init-identity \
  --identity-root /var/lib/sage-lantern/management-identity

sage-lantern-device-agent observe \
  --identity-root /var/lib/sage-lantern/management-identity \
  --device-id lantern-a \
  --sequence 1 \
  --state /run/sage-lantern/sanitized-observation.json \
  --freshness-seconds 300 \
  --clock-confidence ntp-synchronized
```

The output is a signed protocol artifact. It is not evidence that a control plane received it or that a real device is online.

## Outbound transport and ingest verification

`sage_lantern.management_transport.MTLSObservationClient` sends a signed observation to one configured HTTPS ingest path using a dedicated client certificate. It uses normal CA and hostname verification, refuses credentials/query/fragment data in the endpoint, follows no redirects, bounds response size, and requires a `202` acknowledgement that exactly echoes the device ID, sequence, and state hash. The TLS certificate authenticates the transport session; the Ed25519 signature independently authenticates the observation artifact.

`apps/lantern-fleet-admin/lib/observation-contract.ts` is the reusable authority-side verifier. Before an observation can be persisted it checks the registered device ID, registered Ed25519 fingerprint/public key, exact schema, canonical state hash, signature, safe integer-only telemetry contract, explicit freshness window, future clock skew, and monotonic sequence. A Python-generated golden vector is verified by the TypeScript test suite to keep both canonical encodings aligned.

The verifier is intentionally not exposed as a Sites route. The separate fleet authority must terminate device mTLS, load the registered identity and last committed sequence, invoke the verifier, and atomically insert the observation with its new sequence. The signed-in operator UI remains a consumer of that plane, not the device trust endpoint.

## Next transport slice

The next slice adds physical-possession enrollment and authority deployment. Enrollment must pin the fleet command-authority key separately from the client TLS certificate, use a single-use setup secret and comparison code, reject duplicate device-management identities, and leave personal SAGE trust untouched. The authority must commit identity lookup, sequence comparison, and `observed_states` insertion atomically; delivery alone must never mark an operation successful without a valid signed receipt.
