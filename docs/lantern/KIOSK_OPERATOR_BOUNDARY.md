# Kiosk account and privileged administration

The autologin `sage` account must not be a privileged administrator. Its
compositor, browser and later docked apps are user-facing processes. Privileged
maintenance uses the separate owner-authorized root SSH management lane.

`scripts/lantern/configure-kiosk-privileges.py` narrows this role on each
commissioned C–F unit. It does not implement or prove the whole docked-desktop
login required by [D-022](DECISIONS.md#d-022--docked-desktop-requires-a-login).

## What the tool changes

- Remove `disk`, `sudo`, `docker`, `adm` and `systemd-journal` memberships from
  `sage` when present. Preserve all other memberships, including the reviewed
  input, graphics and audio dependencies.
- Install a root-owned, mode 0440 sudoers rule denying every command for
  `sage`. This also closes sudo admission for existing processes whose cached
  groups still include `sudo`.
- Remove the exact existing Armbian PSD exception only if its referenced
  `/usr/bin/psd-overlay-helper` is absent. A different rule or a present/linked
  helper refuses application so its dependency can be reviewed.
- Preserve previous rule bytes/modes and the removed group list in a root-only
  backup receipt before changing membership. Validate sudoers syntax before
  and after installing the deny rule; verify membership and effective sudo
  denial by read-back.

The tool leaves passwords, owner PINs, keys, authorized-key files, private SAGE
state, GDM, services and existing sessions untouched. It requires a direct root
SSH session for `--apply`; running it from the kiosk's sudo session is refused.
Verify an independent fresh management login before applying it and again after
it completes. Do not remove administration access on the basis of a planned
enrollment or an already-open multiplexed connection.

```sh
python3 scripts/lantern/configure-kiosk-privileges.py --plan
sudo python3 scripts/lantern/configure-kiosk-privileges.py --check
# Apply from the verified direct root SSH connection:
python3 scripts/lantern/configure-kiosk-privileges.py --apply \
  --backup-dir /var/backups/sage-lantern-kiosk-privileges-YYYYMMDDTHHMMSSZ
python3 scripts/lantern/configure-kiosk-privileges.py --check
```

Use a new backup directory. Exit 0 means the account/rule configuration checks
pass, exit 1 means they fail, and exit 2 means the operation refused or could not
finish. Inspect the backup receipt after a partial apply. Do not reuse the
backup directory or infer success from its presence.

The check reports selected NSS memberships and counts of running `sage`
processes whose `/proc` metadata still lists privileged groups. It does not
read command lines, environments, keys or owner content. Its sudo probe queries
permission for `/usr/bin/true`; it never executes that privileged command.
Existing graphical processes need replacement before their cached groups are
gone. Keep that transition in the qualified-power and owner panel test window.

Rollback restores the saved sudo rules with their original modes, removes only
the deny rule if it previously did not exist, and adds back only the groups
recorded as removed. Validate sudoers and recheck the separate root management
lane. Do not replace the full current group list or overwrite unrelated changes.
Restoring these privileges is an explicit operator decision, since it broadens
the kiosk account's authority again.

## Whole-desktop authentication remains a separate gate

Read-only inspection on 1 October 2026 confirms that C uses GNOME Shell 50.1
under the autologin `sage` account. The mode supervisor is a session autostart,
not a system service. It currently changes the browser/window and panel
orientation without testing the device lock. The full Ubuntu desktop runs
underneath the portrait browser. Relocking only the companion app therefore
does not enforce D-022 across terminal, Files or the desktop shell.

The current GNOME lock settings deliberately avoid stranding the panel behind
an operator password. A complete successor needs a device-PIN authentication
path at the compositor/session boundary, with these acceptance requirements:

1. A portrait-to-docked switch requires a new SAGE/Lantern login. Connecting
   input devices or losing a browser process cannot grant desktop access.
2. Terminal, Files, overview, shortcuts and alternate launch paths remain
   inaccessible until that authentication succeeds. A browser window, an
   unlocked status observation or a user-writable readiness marker is not a
   whole-session authorization mechanism.
3. Relock, broker failure, stale lease and guard restart must conceal the desktop
   and deny input without discarding unknown work. Preserve running apps behind
   the PIN lock by default; signing out/closing apps requires the owner's
   explicit choice. Running Lantern conversation state must survive switches.
4. Tokens and grants remain in RAM, with explicit lifetime and ownership. Do not
   add browser-persisted credentials to bridge a mode change.
5. Verify GNOME 50 session startup and failure behavior on the installed image.
   Session files alone do not establish which services or fallback shell run.
   GNOME 50 uses systemd session targets and can suppress autostarts for kiosk
   sessions; see [GNOME session documentation](https://gnome.pages.gitlab.gnome.org/gnome-session/re01.html).
6. Qualify the actual panel and keyboard boundary, lock/unlock, mode switching,
   process/session replacement and management access. This requires the guarded
   fresh-session window; stock-shell presence and synthetic tests do not close
   that gate.

Do not claim desktop isolation from group membership removal or from a
fullscreen Chromium flag. Retain the separate browser/image provenance,
protected-storage, physical lifecycle and deferred PIN-recovery requirements.

Current per-unit evidence belongs under `work/`; use
[NEXT_SESSION](NEXT_SESSION.md) for C's installed baseline and open gates.

## C result — 1 October 2026

Applied and independently checked from source hash
`8ee7e1dbcdf02b7c571e4884dbb4784336554a16bf20d2dbb1e618038357b451`.
`sage` no longer has `disk`, `sudo`, `docker` or `systemd-journal` in NSS.
`adm` was already absent. All 12 other memberships were preserved. The explicit
sudo deny rule passes syntax validation and the effective permission probe
denies. The reviewed obsolete PSD rule was backed up and removed; its helper
was absent.

A new root login using only the existing management key succeeded both before
and after application, with SSH agent and multiplexing disabled. Passwords,
PINs, authorized keys and private state were not changed. The boot ID and the
main PIDs, invocation IDs and active states of the same seven Lantern/model/
desktop units match their pre-application values. No session/service restart,
unlock, inference, capture, suspend or reboot was performed.

Read-back still finds 85 of 86 observed `sage` processes carrying cached
privileged groups. The explicit sudo denial is active, but cached access through
other groups still needs a fresh-session qualification. **The full desktop PIN
boundary is not implemented or accepted by this change.**

The backup receipt is on C at
`/var/backups/sage-lantern-kiosk-privileges-20261001T121813Z/receipt.json`.
The root-only staged helper is
`/var/lib/lantern-work/kiosk-privileges-20261001/configure-kiosk-privileges.py`.
Evidence: [work/c-kiosk-privileges-20261001](../../work/c-kiosk-privileges-20261001/README.md).
Twelve focused privilege tests, 14 crash-policy tests and 13 appliance-manifest
tests pass, plus 37 manifest subtests.

## Desktop authentication backend candidate — 1 October 2026

The local source now includes a separate native-desktop grant in
`sage_lantern.desktop_grant`, integrated into the existing auth broker. It
shares the existing SAGE PIN verifier, persisted attempt limit, upstream
serialization and cancellation fences. It issues a separate RAM-only ticket;
neither that ticket nor the active desktop grant authorizes private browser
APIs. Browser authentication and the public unlocked status cannot activate it.

The optional `--desktop-socket` adds a bounded Unix control surface. The
broker accepts only kernel-reported root peers, in a private runtime directory,
and exposes no new HTTP routes. This is the broker-facing half of a future
trusted adapter. The adapter must obtain and verify the native compositor's
actual session and process identity before supplying its binding; the broker
does not independently prove that identity from the supplied binding string.
At the time of this backend milestone, that adapter and the native PIN
dialog/guard were unimplemented. They now have local candidates described below;
neither milestone establishes an installed desktop boundary.

An inactive ticket expires after 30 seconds. Active access needs a six-second
presence lease plus explicit activity within the existing idle policy. Polling
and repeated activation extend neither deadline. Expired grants cannot be
revived; mode changes, relock, guard replacement and broker shutdown fence
pending verification. Private broker storage/clock failures revoke the native
grant. Pending or active desktop grants make the existing sleep-readiness
check refuse. Tokens and PINs are not logged or persisted by the new surface.

Synthetic local verification passes **330 tests plus 287 subtests**, including
real temporary Unix/TCP sockets, kernel-credential fixtures, shared rate limits,
mode changes, cancellation races, worker overload and the existing vault,
profile and release checks. These checks do not exercise C's compositor or
physical input paths. Frozen source hashes, results and startup evidence are
under `work/c-desktop-pin-candidate-20261001/`.

Read-only inspection confirms C's live shell is
`org.gnome.Shell@ubuntu.service`, using the packaged
`/usr/lib/systemd/user/org.gnome.Shell@.service` template and
`/usr/bin/gnome-shell --mode=%i`. Its Wayland service has `Restart=no` and an
on-failure session shutdown path; its failure handling can also disable
extensions. A mandatory guard cannot rely on an optional extension remaining
enabled, or on an assumed `@wayland` instance. Implement and qualify startup
concealment, actual peer binding and failure behavior on this installed stack.

**This candidate is local and is not a deployable desktop boundary.** No new
socket, PIN guard, PAM rule, session mode or successor release is installed on C.
The installed r17 receipt remains historical evidence. Do not enable the new
control socket by itself as a substitute for D-022. The owner reconfirmed that
the power adapter is still pending; the guarded fresh-session and physical
acceptance window remains open. Preserve running apps behind the lock by
default, and do not sign out to install this preparation.

## Native adapter and mandatory guard candidate — 1 October 2026

The root-enrolled exact-peer adapter, mandatory GNOME resource module, checked
launcher and guarded mode-supervisor lane are now implemented locally. Their
protocol and race checks pass; the real C compositor/input and root launch chain
remain unqualified. C's boot, Shell invocation and seven tracked service
identities are unchanged, and no new guard socket is installed.

See [native guard candidate](DESKTOP_PIN_GUARD_CANDIDATE.md) and
[frozen evidence](../../work/c-desktop-native-guard-20261001/README.md). Next work
is root-controlled session selection/enrollment and a matched successor, then
startup/failure, sleep integration, portrait UX and physical input acceptance
in the qualified-power fresh-session window. The current Wayland Shell's
on-failure shutdown path still needs an explicit application-preservation
resolution. **Whole-desktop lock and D-022 remain open.**

## Mode-supervisor source repair — 9 October 2026

The independent review of 6 October held the docked-desktop gate on seven groups
(D022-F01…F07). All seven are repaired in source: the property query carries one
`-p` per property; the confirmed session is fenced by its whole observed
identity — its active state, its leader and that leader's own process start time
— and the eligible session is re-selected after the lock and again after the
rotation; the browser-restart carve-out requires the same lifetime; an entry
that waited re-reads the operator's intent and cancels if it no longer resolves
to desktop; a refusal recovers a real kiosk and records the browser that
actually exists; an unusable `operator_uid`/`operator_seat` keeps the validated
default instead of becoming a wildcard; and the sleep lane preserves any shield
it did not create (see the D-018×D-022 amendment in
[DECISIONS.md](DECISIONS.md)).

This is source, tests and documentation only. Nothing is installed on C, no
session, configuration or credential was touched, and the qualified-power
fresh-session window, the operator panel acceptance and every native-loader gate
remain exactly as they were. **D-022 remains open.**
