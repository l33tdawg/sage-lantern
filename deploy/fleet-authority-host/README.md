# Fleet Authority host migration

These are reviewed deployment inputs for adding `fleet-api.sage.delivery` to
the existing SAGE relay host without changing TURN or Natter. They are not an
auto-deployer. The migration is deliberately staged because the current call
relay owns public TCP 80/443 and its own `call.sage.delivery` ACME lifecycle.

## Resulting listener layout

| Listener | Owner | Purpose |
| --- | --- | --- |
| public TCP 80 | nginx HTTP | call HTTP/ACME forwarding; fleet ACME and redirect |
| public TCP 443 | nginx stream | SNI routing without terminating call TLS |
| `127.0.0.1:8080` | call relay | call HTTP and its ACME challenge handler |
| `127.0.0.1:8444` | call relay | call TLS/WebSocket/long-poll traffic |
| `127.0.0.1:9443` | nginx HTTP/TLS | fleet TLS and device-client-certificate validation |
| `127.0.0.1:8090` | Natter | existing loopback listener; untouched |
| `127.0.0.1:8091` | Fleet Authority | plain HTTP, unreachable directly off-host |

TCP/UDP 3478 (TURN) and TCP/UDP 4001 (Natter) are out of scope and must remain
unchanged. Unknown HTTP hosts return 444; unknown TLS SNI is sent to a closed
loopback port.

## Trust boundaries

- `call.sage.delivery` is TLS passthrough. The Go relay continues to obtain and
  serve its own public certificate.
- nginx terminates `fleet-api.sage.delivery` with a Let's Encrypt certificate.
- nginx requests a device certificate signed by the private Fleet Device CA.
  It requires successful verification only for `/v1/device/*`.
- `/v1/enrollments/{id}/claim` and `/v1/enrollments/{id}/complete` cannot use
  mTLS because the client certificate is issued by completion. The authority's
  short-lived setup-secret and proof-of-possession checks protect these routes.
- `/v1/admin/*` has no device-mTLS requirement. The authority must validate its
  admin bearer token, and the Sites application must keep that token server-side.
- `/healthz` is intentionally unauthenticated and must reveal no internals.
- nginx erases public `X-Fleet-Client-Cert-*` identity headers. On verified
  device requests it sets only `X-Fleet-Client-Cert-Verified: SUCCESS` and a
  `sha256:<lowerhex>` DER-certificate fingerprint.

The standard nginx `$ssl_client_fingerprint` is SHA-1 on common builds, so this
configuration does **not** use it. The small njs helper hashes the canonical DER
certificate with SHA-256. `libnginx-mod-http-js` is therefore a required package.

## Prerequisites and preflight

1. Confirm `fleet-api.sage.delivery` has an unproxied A record for the host and
   resolves globally. Cloudflare orange-cloud proxying would terminate TLS and
   prevent device-certificate authentication.
2. Capture and back up the live unit and nginx state:

   ```sh
   systemctl cat sage-call-relay.service
   systemctl show sage-call-relay.service -p ExecStart
   ss -lntup
   nft list ruleset
   ```

3. Install `nginx`, `libnginx-mod-stream`, `libnginx-mod-http-js`, and certbot.
   Verify both dynamic-module load files exist in `/etc/nginx/modules-enabled/`.
4. Copy this tree's `nginx.conf`, `conf.d`, `stream-conf.d`, `snippets`, and
   `njs` files to their matching `/etc/nginx` paths. Initially install the
   stream file as `10-sage-sni.conf` from
   `10-sage-sni-bootstrap.conf`; do **not** install both map files.
5. Copy the call-relay drop-in template to a scratch path. Replace its example
   `ExecStart` with the live unit's complete command, changing only `-http` to
   `127.0.0.1:8080` and `-https` to `127.0.0.1:8444`.
6. Keep the fleet TLS config out of `/etc/nginx/conf.d` until its public
   certificate and `/var/lib/sage-fleet-authority/fleet-ca.crt` both exist.
7. Run the repository checks and then `nginx -t` on-host. A successful static
   check is not a substitute for `nginx -t` with the installed modules/certs.

## Staged migration

Use a second root SSH session for recovery and schedule a short call-relay
maintenance window.

1. Install the verified call-relay override at
   `/etc/systemd/system/sage-call-relay.service.d/listeners.conf`.
2. Run `systemctl daemon-reload`, restart the call relay, and verify it listens
   only on loopback 8080/8444. If it does not, roll back before starting nginx.
3. Enable the HTTP config and bootstrap SNI config. Run `nginx -t`, then start
   nginx. Verify existing call HTTPS and WebSocket/long-poll behavior through
   `call.sage.delivery`; also verify its ACME challenge path still reaches the
   relay.
4. Create `/var/lib/letsencrypt/.well-known/acme-challenge`, then obtain the
   fleet public certificate with certbot's webroot mode rooted at
   `/var/lib/letsencrypt`.
5. Start the Fleet Authority on its packaged `127.0.0.1:8091` listener. Confirm
   it is absent from `ss -lnt` public-address and wildcard listeners, then apply
   its network-sandbox systemd drop-in.
6. Install `20-fleet-tls.conf`. Atomically replace the bootstrap stream config
   with `10-sage-sni.conf`, run `nginx -t`, and reload nginx.
7. Run the acceptance checks below before enabling a device daemon or connecting
   the hosted admin application.

Do not expose 8080, 8090, 8091, 8444, or 9443 in the firewall. Preserve the existing
22/tcp, 3478/tcp+udp, and 4001/tcp+udp policy while adding/retaining 80/tcp and
443/tcp.

## Acceptance checks

- `curl https://call.sage.delivery/...` and the real call client still work.
- `curl https://fleet-api.sage.delivery/healthz` succeeds without a client cert.
- a request to `/v1/device/observations` without a certificate returns 403 at
  nginx; an arbitrary self-signed client certificate also fails verification.
- a Fleet-CA-issued device certificate reaches the authority with exact
  `Verified=SUCCESS` and SHA-256 certificate fingerprint headers.
- public callers cannot forge either certificate header on enrollment, admin,
  health, or device routes.
- an admin request without or with an invalid bearer receives 401/403 from the
  authority; nginx must not implement or log that secret.
- duplicate or stale device sequence numbers remain rejected by the authority.
- public probes to 8080, 8090, 8091, 8444, and 9443 fail.
- TURN and Natter smoke tests and socket ownership match the preflight capture.

Because the outer stream proxy does not use PROXY protocol (the call relay was
not shown to support it), TLS backends see the proxy as their network peer. Do
not use source IP as device identity or authorization. The certificate
fingerprint and signed observation identity are authoritative.

## Certificate lifecycle

Public TLS renewal uses certbot's webroot and a deploy hook that runs
`nginx -t && systemctl reload nginx`. Test renewal before handoff. Keep the
Fleet CA private key readable only by the authority service account and backed
up encrypted; nginx needs only `fleet-ca.crt`.

Device certificates are short-lived credentials. Re-enroll or implement a
proof-bound renewal before expiry. For CA rotation, create a temporary CA bundle
containing old and new public CA certificates, reload nginx, move devices to the
new CA, then remove the old CA only after every old certificate is expired or
explicitly retired. Immediate per-device revocation must also be enforced by
the authority's active-device/certificate lookup; mTLS chain validity alone is
not sufficient. Do not enable remote commands until renewal, revocation, and CA
backup/restore have been exercised.

## Rollback

1. Stop nginx so it releases public 80/443.
2. Remove or move aside the call-relay listener drop-in, run
   `systemctl daemon-reload`, and restart the call relay on its original public
   80/443 listeners.
3. Verify call service and TURN/Natter. Leave the Fleet Authority stopped or
   loopback-only. DNS for `fleet-api` may remain but must fail closed.
4. Restore backed-up nginx/unit files if needed. Do not delete certificates,
   authority state, or CA keys during an incident; retain them for diagnosis.

The ordering matters: starting the public-listener call relay before stopping
nginx creates a port collision; stopping nginx first creates a brief, bounded
call outage but makes rollback deterministic.
