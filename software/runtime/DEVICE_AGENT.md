# Lantern Device Agent

This runtime contains the outbound-only Unit A management client. It has no
inbound listener and does not fetch or execute commands. Its Ed25519 observation
identity and its TLS client key are generated separately on the Lantern and are
not SAGE, proposer, validator, radio, or recovery identities.

## Enrollment

Use an owner-only state directory and place the single-use setup secret in a
temporary owner-readable file. Passing the secret as an argument is deliberately
unsupported because command arguments are commonly visible to other processes.

```sh
sage-lantern-device-agent enroll-claim \
  --state-root /var/lib/sage-lantern-device \
  --authority-url https://fleet-api.sage.delivery \
  --enrollment-id 11111111-2222-4333-8444-555555555555 \
  --device-id lantern-a \
  --setup-secret-file /run/sage-lantern/fleet-setup-secret
```

The command generates the management Ed25519 key and a separate P-256 TLS key
and CSR locally. It uses server-authenticated HTTPS, consumes the setup secret,
and prints the six-digit comparison code. Compare that code with Fleet Admin and
have an authorized operator approve it. The setup secret is never written into
Device Agent state.

After approval, complete proof of possession:

```sh
sage-lantern-device-agent enroll-complete \
  --state-root /var/lib/sage-lantern-device
```

The proof signs the authority challenge together with the enrollment ID,
expected device ID, management fingerprint, and CSR fingerprint. Completion
accepts the certificate only if it matches the locally retained TLS key and the
returned certificate chain, and only if the command-authority pin is unchanged
from the claim. The issued endpoint/certificate/CA are saved in
`device-agent.json`; the command-authority public key is independently pinned in
`command-authority.json` for the later command slice.

For a private/bootstrap CA, pass `--bootstrap-ca /path/to/ca.pem` to both
enrollment commands. Production `fleet-api.sage.delivery` should use normal
public Web PKI and does not need that option. The returned Fleet CA verifies the
issued client certificate; it must not replace the operating-system trust store
used to verify the public HTTPS server.

## Outbound observations

A local producer writes sanitized JSON using only the supported top-level
status sections: `status`, `services`, `software`, `hardware`, `resources`,
`mesh`, `personal_sage`, `commons`, `queues`, and `alerts`. The adapter rejects
credential-like keys and does not inspect SAGE databases or launch a collector
command.

```sh
sage-lantern-device-agent run \
  --state-root /var/lib/sage-lantern-device \
  --status-file /run/sage-lantern/sanitized-status.json \
  --interval-seconds 60 \
  --freshness-seconds 300 \
  --clock-confidence ntp-synchronized
```

`observations.sqlite3` allocates every sequence transactionally before network
delivery and retains the exact signed payload until its matching acknowledgement
arrives. Failures use persisted bounded exponential backoff. After a restart the
same payload and sequence are retried; an observation that expires while offline
is discarded with its sequence permanently burned before fresh state receives a
new sequence. Use `--once` for a supervised one-cycle smoke test.

The state directory, TLS key/CSR, issued material, enrollment transcript, pins,
and SQLite outbox are written owner-only. The management public key is the only
world-readable identity artifact.
