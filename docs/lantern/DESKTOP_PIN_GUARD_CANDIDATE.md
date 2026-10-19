# Native desktop PIN guard candidate — 2 October 2026

The trusted-peer adapter, mandatory GNOME guard, root-created launch authority
and explicit guarded sleep profile now have local candidate implementations.
**They are not installed on C, and D-022 remains open.** C's
running r17 session, apps, broker and models were left unchanged. Power, fresh
session, physical input, protected-storage and lifecycle gates remain open.

## Native adapter

`desktop-native-adapter.py` runs as root. A root operator or future qualified
launch supervisor explicitly enrolls one GNOME Shell PID and its actual logind
session. Enrollment is held in RAM; no user-written marker or incoming session
claim enrolls a peer. Each native Unix connection must have that exact
kernel-reported PID and UID. A pidfd, process start time, boot ID, exact executable
and arguments, the real `org.gnome.Shell@ubuntu.service` cgroup, active local
seat/session and selected launch environment fence reuse and replacement.

The root-owned policy pins the packaged executable/library and every mandatory
overlay file. Payloads are hashed at enrollment; metadata changes invalidate the
enrollment. Alternate injection flags, incomplete policies, writable paths,
symlinks and other peers refuse. The review policy uses C's observed broker UID
997; that is per-unit evidence, not a D/E/F provisioning default.

The adapter connects to the broker's separate root-peer Unix control surface.
It retains the broker guard and ticket in RAM and returns only an authorization
boolean to the compositor. Its one persistent native connection owns the grant;
another connection cannot replace it. EOF, pidfd death, session loss, relock and
mode change revoke/fence it, including EOF while PIN verification is running.
PIN checks share the existing persisted attempt budget. Native grants still
cannot authorize browser/private APIs, and public unlocked status cannot grant
desktop access.

These identity checks authenticate an explicitly enrolled process. They do not
attest all of its in-memory code or make a user-controlled launch chain trusted.
The new authority constructs the environment and enrolls its own child; alternate
session launch paths still need closure and qualification before production use. Do not auto-enroll a
PID from a user runtime file or a user-supplied D-Bus assertion.

## Mandatory compositor module

The source-bound builder patches C's exact GNOME 50.1 resources. It imports the
PIN guard directly from `main.js`, installs it before `layoutManager.init()` and
disables optional extension initialization. Patched `init.js` refuses a main
module without the mandatory marker. GNOME's native startup/modal paths that
restore normal shortcut handling now consult the guard synchronously.

The guard uses an opaque stage actor outside GNOME's animated UI group, a native
modal input grab, restricted session mode and a touch/keyboard numeric keypad.
Stock password dialogs and public unlock/deactivation requests cannot release
this independent guard. Only a fresh PIN result from the enrolled root adapter,
belonging to the current generation, releases it. A local 3.5-second deadline
conceals before the broker's six-second presence lease. Broker failure, stale
responses and mode-supervisor lease loss conceal and close the native transport.
Guard error handling contains no logout or application-close action.

`cf-mode-supervisor.py --require-native-pin` acknowledges native concealment
before rotation/browser replacement and refreshes a four-second mode lease.
The flag is opt-in for the future qualified lane and is absent from C's running
supervisor. Browser replacement still loses its RAM-only API token intentionally;
server-side conversation state stays independent.

The candidate currently requires native authentication in both modes, with the
companion's private API login remaining separate. Portrait presentation,
duplicate-login UX and mode behavior need the owner's panel acceptance; this
candidate is not a claim that those product flows are finished.

## Root-created startup candidate

`guarded-session-authority.py` runs in a dedicated root system unit. Its user
bridge can request only `start`; JSON cannot supply a PID, session, command,
environment or notification address. Kernel credentials, the exact `@ubuntu`
bridge cgroup/arguments and authoritative logind observations admit the request.
Root checks the complete source policy and qualified-power/fresh-session
activation gate, then creates one fixed Shell child with a constructed
environment, fresh NSS groups and no inherited descriptors. Enrollment binds
that child's pidfd, start time, root-service cgroup, parent PID, overlay and
private notification socket. It does not enroll the bridge's MainPID.

Only the enrolled child's kernel credentials can signal `READY=1` on the private
datagram socket. Root enrolls and starts the native adapter before relaying
readiness to the bridge; the bridge sends readiness to its own user manager.
Wrong peers, extra request fields, EOF and startup timeout cannot select an
alternate compositor or grant access. An existing compositor or Wayland socket
holds startup. A daemon lifetime allows one spawn attempt, with no automatic
adoption, replacement or retry after a spawn failure.

The candidate system unit uses `Restart=no`, `KillMode=process`, zero core limits
and `NoNewPrivileges`. Authority loss revokes the grant and leaves a live Shell
to conceal through its local lease. The bridge stays held after root EOF; the
candidate user-unit drop-in clears the stock `OnFailure` shutdown transaction.
These choices do not preserve Wayland applications when the compositor itself
crashes. Existing sockets and surviving processes require explicit recovery;
there is no automatic cleanup/restart of an unknown desktop.

**This launch authority does not yet establish a trusted session selector.**
Root-controlled GDM selection, user session/unit/autostart overrides, alternate
compositor launches and process isolation still need a concrete deployment
boundary. Logind/DRM seat access from a root-created child in a system-service
cgroup is also unqualified. If that launch cannot obtain the seat, this candidate
holds; it has no stock-Shell fallback. Do not deploy the review drop-ins alone.

## Guarded sleep candidate

The root-owned profile `wayland-mutter-native-pin` explicitly selects
`NativePinShield`; the installed `wayland-mutter` behavior remains separate.
`PrepareSleep` locks/disconnects and fences an in-flight PIN. `Wake` shows the PIN
actor and requires a fresh grant. Neither operation releases modal input or
uses public screen-shield deactivation as authentication. The guarded console
fence admits only the supervisor with the exact `--require-native-pin` argument.
Missing acknowledgments or an unlocked wake state refuse without a stock-shield
fallback. These operations report `concealed`/`restored_guarded`, not physical
HDMI power-off. Actual suspend/wake and display-power acceptance remain open.

## Review bundle and remaining gates

[GLib's overlay API](https://docs.gtk.org/gio/struct.Resource.html#overlays)
falls back to built-in resources when an overlay file is missing. Therefore the
candidate launcher verifies every root-owned payload before exec and refuses
missing files, drift or injected launch flags. A checked overlay alone still
does not prove which session/launcher will execute. GNOME uses systemd session
management; see the [GNOME session manual](https://gnome.pages.gitlab.gnome.org/gnome-session/re01.html).

The 2 October matched review snapshot is local under
`work/c-desktop-root-launch-20261002/review-bundle/`: 138 hashed payloads include
the broker, full runtime source snapshot, authority/adapter/bridge, mandatory
overlay, supervisor, sleep controller and candidate policies/units/autostart.
It is review-only: `deployable`, `installed_on_c` and `desktop_pin_accepted` are
false. It has no activation file, enabled links, GDM selector or installer.
The snapshot includes pre-existing local runtime changes; their complete release
integration against r17 remains a required gate, not an implied qualification.
Earlier evidence remains under `work/c-desktop-native-guard-20261001/`.
There is no active installer,
PAM edit, live Shell patch, socket enablement or session restart in this work.
The existing r17 installation receipt remains historical evidence.

Before a rollout:

1. Qualify C's supply and arrange the owner-approved guarded fresh-session
   window. Preserve unknown work behind the lock; do not sign out automatically.
2. Review the matched snapshot, finish root-controlled session selection and
   alternate-launch closure, and qualify child enrollment/seat access with no
   unguarded fallback.
   Use the actual `@ubuntu` unit. Current Wayland Shell has `Restart=no` and an
   on-failure session-shutdown path; stock compositor death does not establish
   application preservation. Resolve and test this limitation explicitly.
3. Qualify missing/changed payloads, first frame, startup timeout, modal failure,
   broker failure/restart, adapter death/EOF, stale leases, mode-supervisor loss,
   relock, guard replacement and management access. Local/mock checks do not
   establish actual concealment or kernel input coverage.
4. Verify touch keypad, keyboard, terminal/Files/overview/shortcuts, dock/undock,
   browser loss, portrait flow and conversation/app preservation. The modal and
   shortcut fence must be tested against C's actual compositor and physical
   devices. Repeat product display checks on final DSI hardware.
5. Qualify the explicit guarded sleep profile: the native PIN guard refuses public
   `unlock-session`/shield deactivation as an authorization path. Existing sleep
   checks and all protected-storage/browser/privacy/fresh-group gates remain open.

Read-only checks on 2 October found the same boot `10b61161-4469-43de-9fc3-d947ee7147fb`, Shell
PID 1790/invocation `f601d68d6575410cad78b0bb32501909`, active local Wayland
session 3, and unchanged identities for the same seven tracked services. No new
guard socket exists on C. Exact packaged Shell/library hashes are in the
evidence, with no owner content, PINs, tickets or keys.

Local verification passes **310 distinct Python tests plus 108 subtests and 19
JavaScript tests**, with syntax and 138 frozen-payload hash checks. The broad
306-test run passed; after a shutdown-race fix, the focused 51-test run passed,
including four additional cases. One existing SDK Pydantic field-name warning
and Node module-type warning remain. A whole-tree whitespace check also reports
a pre-existing extra blank line in `test_unit_a_status_server.py`; this work did
not edit it. The tests exercise real temporary Unix streams with synthetic peer/pidfd
fixtures, actual broker cancellation/rate-limit behavior, the actual guard's
asynchronous methods with mocked compositor objects, its pure lease state, and
the source-bound builder/launcher. They do not execute C's compositor or enroll
its real running PID. The launch/readiness tests use synthetic Linux credentials
and process metadata on macOS; they do not establish real GNOME seat access.
See the frozen [successor evidence](../../work/c-desktop-root-launch-20261002/README.md).
