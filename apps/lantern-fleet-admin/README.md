# SAGE Lantern Fleet Admin

Authenticated fleet operations interface for SAGE Lantern devices. The current implementation is a polished, responsive product shell backed by realistic sanitized Unit A fixtures and a durable D1 schema. It deliberately keeps remote mutations gated until the dedicated Lantern Device Agent, signed command/receipt contract, mTLS pairing flow, and server-side role assignments are approved.

## Product surfaces

- Foundation Fleet: inventory, clusters, provisioning, releases, evidence, incidents, and audit.
- Field Operations: assigned-cluster context for Torchbearers/Data Donkeys working offline or on intermittent links.
- Radio network: configured Heltec/MeshCore companions, nearby-node observations, signal, hops, region, antenna evidence, and topology.
- Federation: separate personal-local, Commons, fleet-management, and cloud-context lanes.
- AI settings: bounded experience/privacy controls with desired-versus-observed state.
- Commons sync: privacy-preserving proposal and query aggregates without proposal bodies.

## Authority boundary

Fleet roles never map automatically to personal SAGE roles. This application must never hold or reveal personal memories, Root credentials, proposer private keys, public-validator keys, recovery archives, raw audio, or reusable enrollment secrets. Every future mutation must be authorized server-side and re-checked by the Lantern.

## Local development

```sh
npm install
npm run dev
```

The application uses dispatch-owned ChatGPT sign-in. The local Sites runtime provides a development identity.

## Live enrollment console

`/live-enrollment` is the authenticated human side of the initial Fleet Authority
integration. It can create short-lived, single-use enrollment secrets, show a
device-claimed comparison code, and approve a matching code. Device claims,
certificate issuance, and signed observations do not pass through the browser.

The console fails closed until these production runtime values are configured in
Sites:

- `FLEET_AUTHORITY_URL` — the HTTPS Fleet Authority base URL.
- `FLEET_AUTHORITY_ADMIN_TOKEN` — secret bearer token used only by server routes.
- `FLEET_ROLE_ASSIGNMENTS_JSON` — explicit scoped assignments, for example
  `[{"authenticatedUserId":"<whoami user id>","role":"fleet_operator","scopeType":"global","scopeId":"*","expiresAt":null}]`.

An authenticated user can obtain only their own identifier from `/api/whoami`.
The client-side role preview never grants access. Enrollment mutations also
require same-origin requests, and the server forwards the authenticated user ID
to the authority for audit attribution.

## Unit A device-local deployment

The production service can run directly on Unit A for local screen testing. Its
systemd unit binds only to `127.0.0.1:3100` and enables the explicit
`LANTERN_EDGE_LOCAL=1` operator mode; the hosted Sites deployment continues to
require ChatGPT sign-in because that flag is absent there.

From the Unit A checkout, run:

```bash
sudo scripts/lantern/install-fleet-admin-local.sh
```

The installer enables the loopback status service, exact-serial radio/audio
udev links, recoverable GDM auto-login, screen-lock suppression, and the
self-restarting Chromium kiosk. Unit A then boots into the touch-only display at
`http://127.0.0.1:3100/device`; local `/` redirects there. The `/admin` maintenance
console is separate and read-only. Both routes return 404 in hosted mode;
the hosted `/` remains the authenticated Fleet dashboard.

For external-browser maintenance, establish an authenticated SSH tunnel from
your workstation (verify the device host key out of band on first connection):

```bash
ssh -N -T -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:3100:127.0.0.1:3100 \
  -L 127.0.0.1:3199:127.0.0.1:3199 radxa@sage-lantern-a.local
```

Then open `http://127.0.0.1:3100/admin` in that workstation's browser. Both
forwards are required: the browser reads live device status from port 3199.
SSH is the maintenance authentication boundary, not an application login.
Use a trusted workstation: forwarded status can contain private questions and
answers. Close the tunnel when finished; never use wildcard binds or `ssh -g`.
The configured operator label is not proof of an authenticated human identity.

Do not change either service to a LAN-wide bind until device-local
authentication and authorization are implemented.

### Touch voice and streamed answers

The dedicated screen has one compact answer toolbar: My question, an icon-only
blue Camera button and Ask. Answers scroll directly by touch; there are no
up/down controls on the main answer screen. Camera sits beside Ask, not inside the menu.
Menu opens a dismissible side panel for History and Device status. A native
SVG lantern mark identifies the header. Status replaces the old Radio shortcut; LoRa USB detection
is separate from link verification and no signal bars are fabricated. SAGE
service state is not a memory-query success measurement. Missing network,
battery, signal and synchronization telemetry is labelled explicitly.

History is device-local, opt-in and off by default. Enabling it warns that
anyone using the device can read saved questions and answers. Only future
completed answers are eligible, with up to 100 entries retained for 30 days
within the backend byte limit. Entries can be reopened or deleted; clear-all
and disabling require confirmation, and disabling also removes saved history.
This does not erase the current answer from the main screen, securely erase
storage media, or modify SAGE. No audio/photos or public-network copies are
created by history. The normal admin tunnel does not expose history controls.

On the device, tap Ask, speak, then tap Send. Cancel discards the recording;
the physical hold-to-talk button remains available. Both inputs share one
recording/processing owner. Touch capture uses a four-second renewable lease:
closing or hiding the page, losing its connection, or reaching the 30-second
recording limit cancels rather than submits. Send starts processing; there is
no inference-cancellation control yet. No always-listening mode is enabled.

The voice controller binds only to `127.0.0.1:3197`, checks exact Host/Origin
and a custom header, and uses per-recording credentials kept only in memory.
The normal external-browser admin tunnel deliberately does not forward this
control port. It is a trusted local-session boundary, not human authentication.

Real model deltas flow through runtime `voice --events` and the voice status
file. The controller batches the first three complete words and subsequent
five-word groups, also flushing at sentence endings or after about 1.2 seconds
of pending text (80 characters for scripts without spaces). Final text is
published on successful completion without waiting for another group. These
are presentation bounds, not faster model generation; existing first-token
timings are measured upstream of this batching.
The screen reads partial answers while processing, with a 700ms status
poll interval, and distinguishes completion from partial/error output. The
admin view exposes first-text and stage timings after completion. Model first
text excludes speech recognition; first text including recognition still
excludes microphone hold time and browser rendering. No conversation-memory
writes or public sharing are enabled by streaming.

### Optional Unit A camera preview

Install the isolated preview service with
`sudo bash scripts/lantern/install-camera-local.sh` from the repository root.
It requires the existing system `python3` and `v4l2-ctl`; no model downloads or
package installs occur. The Unit A service defaults to the recorded C270 stable
device path; another unit needs an explicit reviewed `--device` override.

On the touchscreen choose Camera to explicitly start the live viewfinder, then
Take photo to freeze the displayed frame and stop streaming. Retake reopens the
viewfinder; Back discards the photo and stops capture. Leaving the screen,
starting a voice session, or hiding the browser stops the preview; it also has
a 60-second server limit and does not silently restart. Photo pixels stay in
browser memory, not SAGE or cloud storage. Live frames use no temporary files;
the older single-capture endpoint still cleans its temporary file. Photo answers
and Use photo are not enabled until a compatible local vision path is validated.

The service binds only to `127.0.0.1:3198`. Capture requires the exact frontend
Origin, a custom request header and an empty POST; GET never captures. Live
preview uses POST `/preview` with `X-Lantern-Camera: preview`, returning bounded
big-endian length-prefixed JPEG frames at requested 1280×720 / 15 fps. Capture
stops on disconnect or timeout, and shares the single camera lock. This is
a local trusted-session boundary, not remote application authentication. Do not
expose it on the LAN. External-browser camera testing additionally requires the
SSH forward `-L 127.0.0.1:3198:127.0.0.1:3198`; the usual two-port admin tunnel
does not include camera access.

The camera service has video access but no SAGE credentials. Its voice-state
guard is best-effort when that file is readable; UI coordination is not a
backend guarantee that PTT and camera cannot overlap. A central session owner
is required before integrating photos and soft voice controls.

The outbound management deployment uses a dedicated `sage-lantern-device`
account. `sage-lantern-observation-status.timer` refreshes an allow-listed local
status file every 30 seconds; `sage-lantern-device-agent.service` signs that
state with the separate fleet management key and sends it outbound over mTLS
every 60 seconds. The agent has no inbound listener, and this release contains
no remote command execution.

## Production integration status

The Device Agent protocol core is implemented in `software/runtime/src/sage_lantern/management.py`: dedicated on-device management identity, signed freshness-bounded observations, signed expiry-bounded commands, expected-state checks, durable replay protection, an action allow-list, and device-signed receipts. The outbound-only mTLS observation adapter is in `management_transport.py`, while `lib/observation-contract.ts` provides authority-side signature, identity, freshness, state-hash, and sequence verification. See `docs/lantern/DEVICE_AGENT_MANAGEMENT_CONTRACT.md`.

The separate authority and its hostname-based TLS/mTLS ingress are deployed at
`fleet-api.sage.delivery`, and `/live-enrollment` uses server-side operator
authorization. The main dashboard remains explicitly fixture-backed until Unit
A completes physical-possession enrollment and its first signed observation is
accepted. Remote commands and concrete action handlers remain gated follow-on
work.

## Validation

```sh
npm run lint
npm run db:generate
npm run build
```

The generated D1 migration is in `drizzle/`. R2 is reserved for sanitized evidence objects; D1 stores only evidence metadata and hashes.
