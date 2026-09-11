# SAGE Fleet Authority

This is the isolated control-plane trust service for SAGE Lantern device management. It creates one-time physical-possession enrollments, issues a dedicated fleet mTLS client certificate, and accepts independently Ed25519-signed observations. It does **not** import, read, or reuse personal SAGE, federation, Commons proposer, Root, validator, or MeshCore identities. This slice intentionally contains no command API.

## Trust model

An admin bearer token may create and approve enrollments. Creation returns a 256-bit setup secret exactly once; only its SHA-256 digest is stored, and it is erased after successful completion. A device claim supplies that secret in JSON, a dedicated Ed25519 management public key, and a CSR made with a separate TLS key. Reuse of either identity is rejected across the fleet.

The device and operator compare a deterministic six-digit code. It is derived from canonical JSON containing the enrollment UUID, device ID, management fingerprint, complete CSR fingerprint, and random proof challenge. Approval requires the exact code. Completion requires an Ed25519 signature over the same identity-bearing proof transcript. The client certificate is returned once and cannot be fetched again.

The Fleet CA and fleet command-authority keys are generated independently in `FLEET_DATA_DIR`, mode `0700`; private keys are mode `0600`. The command-authority public key is returned for pinning, but this release does not issue commands.

Observation ingestion has two independent checks:

1. A trusted TLS terminator verifies the client certificate against the Fleet CA and supplies its SHA-256 DER fingerprint.
2. The service verifies the canonical observation and Ed25519 signature against the enrolled management identity.

The service must listen only on loopback or a private Unix socket. The TLS terminator must delete client-supplied identity headers before setting its own. Never expose the application port directly.

## Install and test

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Run locally only after setting a token of at least 32 characters:

```sh
export FLEET_ADMIN_TOKEN='replace-with-at-least-32-random-characters'
export FLEET_DATA_DIR="$PWD/.local-fleet-authority"
.venv/bin/sage-fleet-authority
```

The process binds `127.0.0.1:8091`. `GET /healthz` is the only unauthenticated operational endpoint. Port 8090 is reserved by Natter on the shared relay host.

## HTTP contract

Admin routes require:

```http
Authorization: Bearer <FLEET_ADMIN_TOKEN>
X-Fleet-Operator-Id: <stable operator identifier>
```

`X-Fleet-Operator-Id` is recorded only after bearer authentication succeeds. It must match `[A-Za-z0-9][A-Za-z0-9@._:-]{0,127}` and defaults to `admin-token` for non-interactive clients.

### Enrollment

`POST /v1/admin/enrollments`

```json
{"device_id":"unit-a","expires_in_seconds":900}
```

Returns HTTP 201. `enrollment_id` is a canonical lowercase UUID. `setup_secret` exists only in this response.

```json
{"enrollment_id":"...","device_id":"unit-a","setup_secret":"...","expires_at":"...Z","status":"pending"}
```

`GET /v1/admin/enrollments?limit=100` and `GET /v1/admin/enrollments/{id}` return sanitized records. They never return the secret, public key, CSR, challenge, or proof.

`POST /v1/enrollments/{id}/claim`

```json
{
  "setup_secret":"...",
  "management_public_key":"<canonical base64, 32 raw Ed25519 bytes>",
  "management_fingerprint":"sha256:<64 lowercase hex>",
  "tls_csr_pem":"-----BEGIN CERTIFICATE REQUEST-----\n..."
}
```

The setup secret is a JSON field, not an authorization credential. The response contains `comparison_code` (exactly six digits), `proof_challenge`, and `command_authority` (`algorithm`, raw-base64 `public_key`, and fingerprint). A setup secret is consumed by the first successful claim.

`tls_csr_fingerprint` is `sha256:<lowerhex>` over the complete CSR DER. `tls_key_fingerprint`, used independently for key uniqueness, is SHA-256 over SubjectPublicKeyInfo DER.

`POST /v1/admin/enrollments/{id}/approve`

```json
{"comparison_code":"012345"}
```

Approval works only while a claimed enrollment is unexpired and the operator-provided code exactly matches.

`POST /v1/enrollments/{id}/complete`

```json
{"proof_signature":"<canonical base64 Ed25519 signature>"}
```

The signature covers this UTF-8 canonical JSON (sorted keys, no whitespace):

```json
{"challenge":"<proof_challenge>","device_id":"unit-a","enrollment_id":"<uuid>","management_fingerprint":"sha256:<hex>","schema":"sage.fleet.enrollment.proof.v1","tls_csr_fingerprint":"sha256:<hex>"}
```

The one successful response contains `client_certificate_pem`, `ca_certificate_pem`, `certificate_fingerprint`, and the command-authority pin. The TLS private key never leaves the device. A retry after issuance returns HTTP 409 and no certificate.

### Observations

`POST /v1/device/observations` accepts the existing `sage.lantern.management.observation.v1` signed envelope. The trusted proxy must supply:

```http
X-Fleet-Client-Cert-Verified: SUCCESS
X-Fleet-Client-Cert-Fingerprint: sha256:<64 lowercase hex over certificate DER>
```

The verifier enforces exact schemas, device and fingerprint binding, canonical state hash and signature, safe integer-only telemetry, 64 KiB state limit, timezone-aware times, a maximum 24-hour freshness window, five-minute future skew, and current freshness. SQLite `BEGIN IMMEDIATE` covers both the last-sequence check and observation insert. An exact retry of an already accepted canonical signed envelope returns the original 202 acknowledgement and `ingested_at`, even after the envelope's freshness TTL, because the stored envelope hash proves it passed validation originally. A different envelope reusing that sequence returns 409. This makes a lost acknowledgement safely retryable without weakening replay protection. Success is HTTP 202:

```json
{"accepted":true,"device_id":"unit-a","sequence":1,"state_hash":"...","ingested_at":"...Z"}
```

`GET /v1/admin/observations?device_id=unit-a&limit=100` returns sanitized observations without signatures. `GET /v1/admin/audit-events?limit=100` returns audit metadata; audit details never contain bearer/setup secrets, keys, CSRs, proofs, signatures, certificates, or raw observation state.

## Production service examples

Copy and edit [`examples/fleet-authority.env`](examples/fleet-authority.env) and [`examples/sage-fleet-authority.service`](examples/sage-fleet-authority.service). Create a dedicated unprivileged `sage-fleet-authority` account and an empty `/var/lib/sage-fleet-authority` owned only by it. Do not point `FLEET_DATA_DIR` at any SAGE home or relay directory.

Back up the SQLite database, Fleet CA files, and command-authority key together with filesystem-level encryption and restricted recovery access. WAL mode is enabled; use SQLite's backup API or briefly stop the service for a file-level backup. Loss of the CA or command key requires an explicit fleet re-enrollment plan.
