# Desktop launch closure and seat review — 2 October 2026

**Alternate launch closure and D-022 remain open.** This successor adds a local
seat-binding check and a read-only launch inventory. C's running r17 session is
unchanged. The original
[candidate handoff](DESKTOP_PIN_GUARD_CANDIDATE.md),
[NEXT_SESSION.md](NEXT_SESSION.md) and 138-payload root-launch review bundle
remain frozen and hash-verified. New evidence and a disabled 139-payload bundle
are in [the successor record](../../work/c-desktop-launch-closure-20261002/README.md).

## Findings on C

The bounded read-only inventory records GNOME 50.1, Mutter
`50.1-0ubuntu2.2` and systemd `259.5-0ubuntu3.4`.
It found:

- GNOME and Ubuntu Wayland session selectors launching stock `gnome-session`.
- A legacy SAGE Lantern X11 selector. Its presence is recorded; whether GDM
  supports/selects it on this image has not been exercised.
- User-manager lookup paths under `/home/sage` and `/run/user/1000`, including
  control, transient and generator locations. The user can write their parents.
  A missing leaf or a root-owned leaf under a replaceable user parent does not
  establish closure.
- Writable user autostart/configuration parents and an `unconfined` running
  Shell security label. This observation does not establish a restriction on
  unmanaged compositor launches, device access or code injection.
- Stock Shell PID 1790 owns logind controller `:1.37` for session 3, and holds
  the registered DRM card and evdev descriptors. This is evidence about the
  existing stock process, **not acceptance of the new root-created child**.

The inventory reads only fixed launch files, public service/process identities,
selected saved-session fields, path permissions and DRM/evdev descriptor metadata.
It does not read owner application content, arbitrary environment variables,
credentials or private descriptor targets. No device is opened and no seat
control method is invoked.

## Seat-binding candidate change

Mutter 50.1's [launcher source](https://github.com/GNOME/mutter/blob/50.1/src/backends/meta-launcher.c)
supports an explicit `XDG_SESSION_ID`, and requests control without force.
[logind's interface](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html)
permits the session UID or root to request control. Therefore membership in the
session cgroup is not itself the deciding seat-access test. The reviewed
upstream sources are retained with hashes; they do not attest Ubuntu's complete
patched source or qualify the candidate on C.

The new `desktop_seat.py` supplements the existing exact-child pidfd/process
anchor. After an exact-child `READY=1`, root reads the root-owned saved logind
session record, resolves its unique controller name through the system bus to
the kernel-reported PID, and checks that the child's DRM/evdev character
descriptors belong to that controller's registered devices. It double-checks
state and controller identity around the observation. UID, leader, active local
Wayland session and seat must match; wrong/missing controllers, malformed
responses, changed state and absent/unregistered devices refuse.

The authority publishes native PIN access only after this check. Continued
controller loss invalidates authority and follows the existing grant-revocation
and concealment path. Hold during socket construction/publication cannot restart
native authority. No failure path terminates or replaces Shell or applications.
The guard may retry its connection while concealed until the adapter is ready.

The saved-session format and device requirements are specific to the reviewed
systemd 259/DRM/evdev lane. Real child startup, event handling, latency, hotplug,
suspend/wake and first-frame concealment are unqualified. Holding descriptors
does not prove touch coverage or that input is blocked behind the guard.

## Closure work required before production

[GNOME session configuration](https://gnome.pages.gitlab.gnome.org/gnome-session/re01.html)
and [systemd user-unit lookup](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html)
include user-controlled locations. Replacing one `@ubuntu` command and selecting
one GDM entry cannot alone exclude alternate paths. Source review also shows
that logind's seat-controller admission is based on the session UID; the native
broker's exact-PID enrollment does not restrict that separate interface.

A successor must establish all of the following as an enforced boundary:

1. A root-controlled graphical session selection and launch chain, covering
   stock GNOME/Ubuntu, legacy X11 and failure fallback paths. Preserve GDM's
   separate greeter and independent root key-only SSH management.
2. Session and user-manager lookup that cannot be replaced through writable
   parents, home/runtime units, transient jobs, generators, manager environment
   changes or user autostart. An absent override file is insufficient.
3. Restrictions on unmanaged compositor execution, logind device/control access,
   direct DRM/input access and code injection, including replacement after
   compositor failure. Qualify the isolation/confinement mechanism on this
   image; an `unconfined` label or configuration-only receipt cannot satisfy it.
4. A source-bound complete release and actual root-created child startup with
   the new controller/descriptor check, without adopting PID 1790 or forcing
   control away from the current session.
5. Qualified power, an owner-authorized fresh-session window, physical concealment
   and keyboard/touch/shortcuts, failure and mode transitions, guarded sleep and
   app/conversation preservation. Compositor death still cannot promise Wayland
   client preservation. D/E/F remain held until C acceptance.

The read-only review script always keeps candidate-seat, alternate-launch and
desktop acceptance false, even if supplied an empty inventory or claimed
qualification flags. It is evidence review, not an activation authority.

## Verification

The final local regression suite passes **293 Python tests plus 114 subtests**;
all **19 JavaScript tests** pass. This is the named successor suite, not a claim
to rerun every test in the earlier 310-test aggregate. The first broad run hit
sandbox socket-bind denials; the same suite passes outside that restriction.
Three final cases cover cancellation during native publication.

The 139-payload successor and 138-payload predecessor pass bundle verification.
All 26 retained predecessor source/handoff hashes checked here match their
frozen record. Final read-back matches the predecessor's boot ID, Shell
PID/start/invocation, session, packaged hashes and all seven tracked service
identities. No native guard socket exists on C. No deployment, login change,
restart, unlock, inference, capture, suspend or reboot occurred.
