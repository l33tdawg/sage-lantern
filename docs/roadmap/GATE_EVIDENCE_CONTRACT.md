# Gate evidence contract

**Status:** CANDIDATE normative  
**Applies to:** G0–G6  
**Canonical JSON:** RFC 8785 JSON Canonicalization Scheme (JCS)  
**Digest:** SHA-256

## 1. Files and self-reference boundary

Each gate attempt publishes two immutable files plus referenced evidence:

1. `evidence/gates/<gate>/<attempt-id>/gate-manifest.json` — the unsigned decision/evidence payload.
2. `evidence/gates/<gate>/<attempt-id>/gate-manifest.attestations.json` — detached digest and approval signatures.

`gate-manifest.json` contains neither its own digest nor approval signature values. `gate-manifest.attestations.json` is not part of the manifest digest. This eliminates self-hashing and circular-signature ambiguity.

## 2. Unsigned manifest payload

The complete `gate-manifest.json` object is the unsigned signing payload:

```json
{
  "schema": "sage.commons.gate-manifest.v1",
  "manifest_id": "stable-random-or-content-independent-id",
  "gate": "G0",
  "attempt": 1,
  "decision": "PASS",
  "baseline": {
    "master_prd_version": "0.1.0",
    "requirements_register_sha256": "lowercase-hex",
    "sage_release": "v11.18.4",
    "sage_source": "immutable-ref",
    "consensus_app_version": 26
  },
  "requirements": [
    {
      "id": "SYS-001",
      "result": "PASS",
      "threshold": "frozen machine/human-readable criterion",
      "measured": "value/result",
      "evidence": [
        {"uri": "relative-or-content-addressed", "sha256": "lowercase-hex"}
      ]
    }
  ],
  "waivers": [],
  "supersedes": [
    {
      "manifest_id": "prior-id",
      "manifest_sha256": "prior-lowercase-hex",
      "uri": "../prior-attempt/gate-manifest.json"
    }
  ],
  "created_at": "RFC3339-UTC"
}
```

For schema v1:

- `decision` is exactly `PASS`, `CONDITIONAL_PASS` or `FAIL`.
- requirement `result` is exactly `PASS`, `FAIL`, `BLOCKED` or `WAIVED`.
- timestamps are RFC 3339 UTC using `Z`.
- arrays whose order has product meaning retain that order; producers MUST sort `requirements` by UTF-8 requirement ID, `evidence` by URI then digest, `waivers` by waiver ID and `supersedes` by manifest digest before RFC 8785 canonicalization.
- duplicate object keys, non-I-JSON values and unknown top-level fields fail schema-v1 verification.

## 3. Canonicalization and digest

The producer MUST:

1. parse `gate-manifest.json` as I-JSON and validate schema v1;
2. apply the required array ordering above;
3. serialize the entire object with RFC 8785 JCS;
4. encode that canonical text as UTF-8 with no BOM or trailing newline;
5. compute `SHA-256(canonical_utf8_bytes)`; and
6. encode the 32-byte result as 64 lowercase hexadecimal characters named `manifest_sha256` in the detached sidecar.

Verifiers repeat those exact steps and reject a digest mismatch. They MUST NOT hash the original pretty-printed file bytes and MUST NOT add/remove fields before canonicalization.

## 4. Detached attestations

Example `gate-manifest.attestations.json`:

```json
{
  "schema": "sage.commons.gate-attestations.v1",
  "manifest_id": "same-id-as-manifest",
  "manifest_sha256": "64-lowercase-hex",
  "approvals": [
    {
      "role": "accountable-role",
      "identity": "qualified-identity",
      "decision": "APPROVE",
      "algorithm": "Ed25519",
      "key_id": "stable-key-id",
      "verification_method": "did:key-or-versioned-key-registry-uri",
      "signature_base64url": "unpadded-base64url"
    }
  ],
  "created_at": "RFC3339-UTC"
}
```

Each approval signature covers these exact UTF-8 bytes, including both line feeds and no bytes after the final line feed:

```text
SAGE-COMMONS-GATE-MANIFEST-V1
sha256:<manifest_sha256>
```

`decision` in an approval is exactly `APPROVE` or `REJECT`. Schema v1 permits Ed25519 only; adding another algorithm requires a schema version and verification specification. `key_id` identifies the exact key version. `verification_method` resolves to the public key and its validity/revocation evidence at signing time. Signatures use RFC 4648 base64url without `=` padding.

The sidecar itself is serialized with RFC 8785 for storage/interchange, but its bytes are not recursively signed by these approvals. Multiple signatures independently cover the same domain-separated manifest-digest payload.

## 5. Verification algorithm

A verifier MUST:

1. validate both schemas and matching `manifest_id`;
2. canonicalize and hash the unsigned manifest exactly as §3;
3. compare the computed digest to detached `manifest_sha256` using all 32 bytes;
4. rebuild the exact domain-separated signing payload from §4;
5. resolve each `verification_method` and confirm `key_id`, algorithm, validity, role qualification and revocation state at signing time;
6. verify each detached Ed25519 signature;
7. apply the gate's required role/quorum policy; and
8. verify every evidence/supersedes digest and waiver validity before accepting the gate decision.

Unknown algorithms, keys, roles, fields, schemas or unverifiable superseded manifests fail closed.

## 6. Evidence, reruns and waivers

Thresholds and study samples are frozen before the run. Evidence records exact hardware, software, configuration, time, test/operator identity and sanitization. A rerun creates a new attempt directory and names the prior manifest in `supersedes`; it never overwrites prior evidence.

A waiver contains `waiver_id`, requirement/evidence gap, reason, residual risk, compensating control, accountable owner, exact deployments/capabilities affected, issue/RFC, issued/expiry time and rollback. Expired waivers turn the affected gate/capability to `FAIL` until resolved. Non-waivable invariants are listed in the roadmap.

Gate signatures attest only the exact manifest digest and decision process. They do not prove unrelated claims or substitute for installed-SAGE, pack, device-execution, backup, state-sync or consensus evidence.
