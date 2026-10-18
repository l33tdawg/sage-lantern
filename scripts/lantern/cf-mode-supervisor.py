#!/usr/bin/env python3
"""Portrait app mode / docked desktop mode for C-F; not a session switch.

Portrait (default): the Lantern console fullscreen through the installed
kiosk launcher. Docked: a USB keyboard attached (mouse optional), or an explicit override,
closes the kiosk, rotates the panel for side use, and serves the same console
as an ordinary window on the GNOME desktop. One browser at a time; the app
server, conversation state and device lock are untouched by a mode change.

D-022 requires a fresh operator login on entering the docked desktop. That is a
gate, not a notification: the session is locked and the lock is *proven* before
the panel is rotated, the kiosk stopped or a desktop browser started. If the
gate cannot be confirmed - no matching session, an ambiguous one, a refused
request, a lock that never reports itself - the desktop is refused outright and
the unit stays on the appliance. Exit status is not evidence: on C
`loginctl lock-session` returned 0 while doing nothing at all.

The device unlock token lives only in browser memory, so a mode change
intentionally re-locks the UI. The candidate --require-native-pin lane also
requires compositor concealment before a transition and keeps a mode lease.
It is not enabled in C's running r17 session.

The confirmed session is fenced by its whole observed identity, not by a name:
the fence carries whether the session is still the active one and the leader
process's own start time, so a replacement session that reuses a session id and
even a recycled leader pid cannot inherit this gate. The fence is re-read after
the lock and again after the rotation, because a confirmation is not an atomic
barrier over the compositor. A refused entry leaves a real appliance kiosk
running, and the state file records the browser that is actually there.
"""
import json
import argparse
import os
from pathlib import Path
import fcntl
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

CONFIG = Path('/etc/sage-lantern/display-mode.json')
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
OVERRIDE = RUNTIME / 'sage-lantern-mode.override'
STATE = RUNTIME / 'sage-lantern-mode.state'
OUTPUT_MODE = '/usr/local/libexec/sage-lantern-output-mode'
KIOSK = '/usr/local/bin/sage-lantern-kiosk'
CONSOLE_URL = 'http://127.0.0.1:3100/device'
PROFILE = '/home/sage/.config/sage-lantern/unit-c/chromium'
# Where a process reports its own name. A module constant so the owner probe can
# point it at a synthetic tree: the unit is the only place /proc exists, and a
# branch that cannot be exercised off the unit is a branch nobody has tested.
PROC = '/proc'
# The name this machine writes into a profile lock, read once at import for the
# same reason as PROC: the probe needs to simulate a hostname it is not running
# on, and patching os.uname() would reach every other caller of os in the
# process. os.uname() is POSIX; the fallback keeps the import working elsewhere.
try:
    NODENAME = os.uname().nodename
except AttributeError:  # pragma: no cover - POSIX only in practice
    NODENAME = 'localhost'
DEFAULTS = {'kiosk_transform': 'normal', 'desktop_transform': 'rotate-90', 'panel_scale': '1.0'}
# The session this supervisor may gate. Kept out of DEFAULTS so the reviewed
# display-mode contract - transforms and panel scale - keeps its exact shape.
BINDING_DEFAULTS = {'operator_uid': '1000', 'operator_seat': 'seat0'}
PANEL_SCALE = 1.0
# The D-022 gate is bounded by polls rather than by a wall clock, so an inert
# control can run the whole confirmation loop without a simulated clock. Twenty
# polls at a quarter second is the five seconds a desktop entry may take.
CONFIRM_POLLS = 20
CONFIRM_POLL_SECONDS = 0.25
RE_REQUEST_EVERY = 4
REFUSAL_BACKOFF_MAX = 30
# The installed launcher is Wayland-native as of this release
# (--ozone-platform=wayland); it still sizes the window with xrandr, which is a
# no-op outside X. Portrait mode here is Wayland-native either way.
KIOSK_FLAGS = ('--kiosk', '--ozone-platform=wayland', '--no-first-run', '--no-default-browser-check',
               '--noerrdialogs', '--disable-session-crashed-bubble', '--disable-dev-tools',
               '--disable-extensions', '--disable-features=Translate,MediaRouter,PrintPreview',
               '--disable-pinch', '--overscroll-history-navigation=0', '--password-store=basic')


def log(message):
    stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    print(f'{stamp} {message}', flush=True)


def config():
    values = dict(DEFAULTS)
    try:
        loaded = json.loads(CONFIG.read_text())
        if isinstance(loaded, dict):
            for key in DEFAULTS:
                if isinstance(loaded.get(key), str):
                    values[key] = loaded[key]
            scale = loaded.get('panel_scale')
            if isinstance(scale, (int, float)) and not isinstance(scale, bool):
                values['panel_scale'] = repr(float(scale))
    except (OSError, ValueError):
        pass
    return values


def panel_scale(values):
    """The reviewed panel scale; anything unparseable falls back to 1.0.

    The 720x1560 panel must be at 1.0: this Chromium build renders at 1x, so
    GNOME fractional scaling crops the console on both edges.
    """
    try:
        scale = float(values['panel_scale'])
    except (KeyError, TypeError, ValueError):
        return PANEL_SCALE
    return scale if 0.5 <= scale <= 4.0 else PANEL_SCALE


def binding_values():
    """The operator account and seat a desktop entry is allowed to gate.

    A configured value that cannot be a real account or seat is not a wildcard:
    it falls back to the reviewed default, so a blank, whitespace or malformed
    setting can never widen the gate to whoever happens to be logged in.
    `operator_uid` must be a positive numeric account id and `operator_seat` a
    non-empty seat name; an absent or unreadable file keeps both defaults.
    """
    values = dict(BINDING_DEFAULTS)
    try:
        loaded = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return values
    if not isinstance(loaded, dict):
        return values
    uid = loaded.get('operator_uid')
    if isinstance(uid, str) and uid.strip().isdigit() and int(uid.strip()) > 0:
        values['operator_uid'] = uid.strip()
    seat = loaded.get('operator_seat')
    if isinstance(seat, str) and seat.strip() not in ('', '-'):
        values['operator_seat'] = seat.strip()
    return values


def by_id(pattern):
    return sorted(Path('/dev/input/by-id').glob(pattern))


def inputs():
    """Real keyboards and pointers only; the direct-input panel is not a mouse."""
    keyboard = bool(by_id('usb-*-event-kbd')) or bool(by_id('bluetooth-*-event-kbd'))
    mouse = (bool(by_id('usb-*-event-mouse')) or bool(by_id('bluetooth-*-event-mouse'))
             or bool(by_id('usb-*-event-mouse-*')) or bool(by_id('bluetooth-*-event-mouse-*')))
    return keyboard, mouse


def wanted_mode(values, keyboard, mouse):
    """Resolve the mode. A keyboard alone is the owner's docked trigger."""
    override = override_setting()
    if override in {'desktop', 'kiosk'}:
        return override, 'override'
    if override not in {'', 'auto'}:
        return ('desktop' if keyboard else 'kiosk'), 'invalid-override'
    return ('desktop' if keyboard else 'kiosk'), 'auto'


def override_setting():
    """The operator's explicit override exactly as written, or '' for none.

    Read on its own so a transition already in flight can ask whether the
    operator has since asked for something else, without re-deriving the whole
    resolved mode from a second input scan.
    """
    try:
        return OVERRIDE.read_text().strip()
    except OSError:
        return ''


class Browser:
    def __init__(self):
        self.process = None
        self.mode = None
        self.started_at = 0.0
        self.failures = 0

    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        if self.process is None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=8)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process = None
        self.mode = None

    def start(self, mode, transform):
        self.clear_stale_singleton()
        if mode == 'kiosk':
            command = ['/usr/bin/chromium', *KIOSK_FLAGS, f'--user-data-dir={PROFILE}', CONSOLE_URL]
        else:
            command = ['/usr/bin/chromium', '--ozone-platform=wayland', '--new-window',
                       '--window-size=1280,700', '--no-first-run', '--no-default-browser-check',
                       '--noerrdialogs', '--disable-session-crashed-bubble', '--disable-dev-tools',
                       '--password-store=basic', f'--user-data-dir={PROFILE}', CONSOLE_URL]
        environment = dict(os.environ)
        environment['XDG_RUNTIME_DIR'] = str(RUNTIME)
        environment.setdefault('HOME', str(Path.home()))
        environment.setdefault('DISPLAY', ':0')  # session XWayland; the browsers below request Wayland directly
        environment.setdefault('WAYLAND_DISPLAY', 'wayland-0')
        environment.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={RUNTIME}/bus')
        self.process = subprocess.Popen(command, start_new_session=True, env=environment,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.mode = mode
        self.started_at = time.monotonic()
        log(f'started {mode} browser pid={self.process.pid} transform={transform}')

    def note_exit(self):
        """Back off when a browser exits immediately (for example a held profile)."""
        if self.process is not None and time.monotonic() - self.started_at < 5:
            self.failures += 1
        return min(2 * max(self.failures, 1), 20)

    def clear_stale_singleton(self):
        """Drop singleton symlinks, but only once nothing live owns the profile.

        Called only from start(), which the supervisor reaches when *this*
        object holds no browser. That is not the same as no browser running:
        `running()` reads this object's own Popen handle, so an initial or
        recreated Browser has process=None while a live chromium still holds the
        profile. Deleting the three names underneath that owner is how a second
        browser gets admitted onto a profile the first one is still using.

        The lock itself names its owner, so the owner is established from the
        lock rather than assumed: `SingletonLock` is a symlink to
        `hostname-pid`, and chromium only clears it on a clean exit. A lock that
        names a dead process, or one this machine never issued, is the case this
        method exists for, and it still clears. Anything the method cannot
        resolve is treated as live and left alone — a held profile shows a
        browser that will not start, while a cleared profile under a live owner
        corrupts a running session, and only one of those is recoverable.

        Without this repair a browser that dies uncleanly leaves the profile
        held: every later launch refuses to start and note_exit() can only back
        off forever, so the unit stays at the greeter instead of the appliance.
        The bash launcher beside this already clears the same three names; this
        supervisor spawns chromium directly and so never got that repair.
        """
        owner = self.lock_owner_pid()
        if owner is not None and self.owner_is_live(owner):
            log(f'profile is held by live browser pid={owner}; leaving its locks alone')
            return
        for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
            path = Path(PROFILE) / name
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                log(f'could not clear held profile {path}: {error}')
                continue
            log(f'cleared stale {name} left by a dead browser')

    def lock_owner_pid(self):
        """The pid the profile's own lock names, or None when there is no lock.

        Chromium writes `SingletonLock` as a symlink whose target is
        `hostname-pid`. A target this machine did not issue — a different
        hostname, or bytes that are not a lock at all — cannot belong to a live
        chromium here, so it is stale by construction. Only the hostname this
        process would write counts as ours: comparing it is what keeps a lock
        from an earlier boot, or another machine sharing the profile directory,
        from being read as a live owner forever.

        The split is on the *last* hyphen from the front — the target must start
        with this machine's own hostname and a separator — because appliance
        hostnames contain hyphens themselves (`lantern-c-4711`). Splitting on the
        first hyphen instead would read `lantern` as the host and discard a lock
        this machine really issued, which is the failure this repair exists to
        prevent.
        """
        try:
            target = os.readlink(Path(PROFILE) / 'SingletonLock')
        except OSError:
            return None
        prefix = f'{NODENAME}-'
        if not target.startswith(prefix):
            return None
        pid = target[len(prefix):]
        if not pid.isdigit() or int(pid) <= 0:
            return None
        return int(pid)

    def owner_is_live(self, pid):
        """Whether that pid is a chromium still entitled to hold the profile.

        Liveness alone is not enough. Pids are recycled, so a live process
        wearing the lock's number may be something else entirely, and treating
        that as the owner would leave the profile held for good — the greeter
        this repair exists to prevent. The name is what distinguishes them.

        Unreadable details count as live: a process this account cannot inspect
        might be the browser, and refusing to clear only costs a launch that
        still refuses, which note_exit() already reports.
        """
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        try:
            name = (Path(PROC) / str(pid) / 'comm').read_text().strip().lower()
        except OSError:
            return True
        if not name:
            return True
        return 'chromium' in name or 'chrome' in name


def apply_transform(name, scale):
    result = subprocess.run([OUTPUT_MODE, name, '--scale', f'{scale:g}'],
                            capture_output=True, text=True, timeout=20)
    log(f'transform {name} scale={scale:g}: rc={result.returncode} '
        f'{result.stdout.strip()} {result.stderr.strip()}'.strip())


def write_state(values):
    try:
        STATE.write_text(json.dumps(values, sort_keys=True) + '\n')
    except OSError:
        pass


def native_guard_mode(mode, *, change):
    """Conceal first, or refuse the transition. This reply never authenticates.

    Used only by the future mandatory-guard launch lane. Existing r17 sessions
    do not opt into this flag, so this preparation cannot strand them.
    """
    try:
        result = subprocess.run(['/usr/bin/gdbus', 'call', '--session',
            '--dest', 'org.gnome.Shell', '--object-path', '/org/sage/Lantern/DesktopGuard',
            '--method', 'org.sage.Lantern.DesktopGuard1.' + ('SetMode' if change else 'KeepMode'),
            mode], capture_output=True, text=True, timeout=2)
        return result.returncode == 0 and result.stdout.strip() == '(true,)'
    except (OSError, subprocess.TimeoutExpired):
        return False


def session_bus():
    """Point D-Bus at the operator's session bus when we were started outside it."""
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime and not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + runtime + '/bus'


@dataclass(frozen=True)
class SessionBinding:
    """The one session this supervisor is allowed to gate, and its lifetime.

    Only the identity is carried, not the whole property dump: the fence compares
    exactly the properties that decide whether the session we confirmed is still
    the session about to be exposed. `active` belongs in it because selection
    requires an active session and a session that stopped being the active one
    is no longer the one that was admitted. `leader` and `leader_lifetime` are
    the lifetime discriminator: a different leader, or the same leader pid whose
    process start time differs, is a different session wearing the same name.
    """

    session_id: str
    uid: str
    seat: str
    kind: str
    session_type: str
    remote: str
    active: str
    leader: str
    leader_lifetime: str

    def fence(self):
        return (self.session_id, self.uid, self.seat, self.kind, self.session_type,
                self.remote, self.active, self.leader, self.leader_lifetime)


SESSION_PROPERTIES = ('Active', 'Seat', 'Class', 'Type', 'Remote', 'User', 'Leader', 'LockedHint')


def leader_lifetime(leader):
    """The leader process's own start time, or None when this host cannot say.

    `Leader` is a pid, and pids are recycled: a replacement session that lands
    on the same session id and the same leader number would otherwise present an
    identity indistinguishable from the one this gate confirmed. The kernel's
    start time for that pid is unique per process, so the pair cannot be forged
    by reuse.

    A lifetime this host cannot observe is *not* an identity. An empty string
    compares equal to another empty string, so returning one for an unreadable or
    truncated stat let two different processes present the same fence and this
    gate admit the second on the first one's login. None is returned instead, and
    the caller refuses: an unverifiable lifetime is a loss of authority at both
    admission and restart. The one exception is the leader pid itself - absent,
    the property fence alone still applies, and an unknown lifetime is compared
    as unknown rather than as a value.
    """
    if not leader.isdigit() or int(leader) <= 0:
        return None
    try:
        stat = (Path(PROC) / leader / 'stat').read_text()
    except OSError:
        return None
    fields = stat.rsplit(')', 1)[-1].split()
    if len(fields) <= 19:
        return None
    starttime = fields[19]
    return starttime if starttime.isdigit() and int(starttime) > 0 else None


def session_properties(session_id):
    """Read one session's own properties, or None when it cannot be read at all.

    One `-p` per property. `loginctl` consumes exactly one argument per option
    and reads every remaining word as another session id, so the single `-p`
    form this replaces selected `Active` and then looked up seven sessions named
    `Seat`, `Class` and so on: on the unit an ordinary query failed, and every
    desktop entry was refused as `session_unreadable` before the gate was even
    reached.
    """
    command = ['loginctl', 'show-session', session_id]
    for name in SESSION_PROPERTIES:
        command += ['-p', name]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return dict(part.split('=', 1) for part in done.stdout.split() if '=' in part)


def binding_from(properties, session_id):
    """The binding a property dump describes, or None when it cannot be one.

    An absent or malformed lifetime also refuses here rather than constructing a
    binding an empty value would make reusable: `leader_lifetime` reports None
    for exactly the cases the kernel could not answer, and a fence must not be
    built from evidence this host does not have.
    """
    if not properties:
        return None
    leader = properties.get('Leader', '')
    lifetime = leader_lifetime(leader)
    if leader and lifetime is None:
        return None
    return SessionBinding(session_id=session_id, uid=properties.get('User', ''),
                          seat=properties.get('Seat', ''), kind=properties.get('Class', ''),
                          session_type=properties.get('Type', ''), remote=properties.get('Remote', ''),
                          active=properties.get('Active', ''), leader=leader,
                          leader_lifetime=lifetime or '')


def graphical_session(values):
    """The one active local graphical session of the expected operator and seat.

    Returns (binding, reason). The reason is a plain phrase so a refusal can say
    which predicate failed instead of looking like a mysterious absence.

    A supervisor started outside the seat (for example from an ssh session) must
    not select its own session: that session has no graphical lock, and polkit
    answers 'Access denied ... requires interactive authentication'. Requiring a
    local seat, a graphical type and Remote=no excludes it by construction rather
    than by hoping the ordering picked the right one. The operator account and
    seat are compared, so another user's session on another seat cannot satisfy
    this the way a first-match-wins predicate did.

    The expected account and seat are validated here as well as in
    `binding_values()`: an empty or malformed expectation is a refusal, never an
    unset filter that matches the first session on the machine. The returned
    binding carries the lifetime this selection is bound to, so the caller can
    re-ask this same question after every blocking step.
    """
    expected_uid = str(values.get('operator_uid') or '').strip()
    expected_seat = str(values.get('operator_seat') or '').strip()
    if not expected_uid.isdigit() or int(expected_uid) <= 0 or not expected_seat or expected_seat == '-':
        return None, 'binding_invalid'
    try:
        listing = subprocess.run(['loginctl', 'list-sessions', '--no-legend'],
                                 capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None, 'session_listing_unavailable'
    if listing.returncode != 0:
        return None, 'session_listing_failed'
    candidates, seen = [], 0
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        seen += 1
        session_id = parts[0]
        properties = session_properties(session_id)
        if properties is None:
            return None, 'session_unreadable'
        if (properties.get('Active') == 'yes'
                and properties.get('Class') == 'user'
                and properties.get('Type') in ('wayland', 'x11')
                and properties.get('Remote') == 'no'
                and properties.get('Seat') not in ('', '-')
                and properties.get('User') not in ('', '0')
                and properties.get('User') == expected_uid
                and properties.get('Seat') == expected_seat):
            candidate = binding_from(properties, session_id)
            if candidate is None:
                # A session whose leader process this host cannot time is not the
                # eligible one: its fence cannot be told apart from a recycled pid.
                return None, 'session_lifetime_unverified'
            candidates.append(candidate)
    if not seen:
        return None, 'no_sessions'
    if not candidates:
        return None, 'no_bound_session'
    if len(candidates) > 1:
        return None, 'ambiguous_bound_session'
    return candidates[0], 'bound'


def screen_shield_active():
    """GNOME's own screen shield state, or None when the probe cannot answer.

    Corroboration only, and it may only ever *refuse*: a session whose LockedHint
    says locked while the shield says otherwise is a contradiction this does not
    paper over, while a machine without the probe is not treated as unlocked.
    """
    session_bus()
    try:
        done = subprocess.run(['busctl', '--user', 'call', 'org.gnome.ScreenSaver',
                               '/org/gnome/ScreenSaver', 'org.gnome.ScreenSaver', 'GetActive'],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    answer = done.stdout.strip()
    if answer == 'b true':
        return True
    if answer == 'b false':
        return False
    return None


def request_lock(binding, result):
    """Ask logind to lock exactly this session. Never the caller's own by omission."""
    session_bus()
    command = ['loginctl', 'lock-session', binding.session_id]
    result['requests'] += 1
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        result['request_errors'].append(str(error))
        log('session lock request failed: %s' % error)
        return
    if done.returncode != 0:
        result['request_errors'].append((done.stderr or '').strip() or f'rc={done.returncode}')
        log('session lock %s returned %d: %s'
            % (' '.join(command), done.returncode, (done.stderr or '').strip()))
    else:
        log('session lock %s requested' % (' '.join(command),))


def lock_and_confirm(binding, *, polls=None, poll_seconds=None, intent=None):
    """Lock the bound session and prove it, before anything is exposed.

    Confirmation is the session's own LockedHint, re-read together with the
    binding that must not have changed underneath us. Exit status is deliberately
    not part of the answer: on C it was 0 while the lock never happened. When the
    request does not take it is repeated a bounded number of times inside this
    call, rather than fired from a detached timer that outlives the transition it
    belonged to.

    A pending entry is also abandoned the moment the operator asks for kiosk
    again: an explicit override is re-read before every further request rather
    than only before the first one, so a transition that is still waiting cannot
    outlive the intent it started with. `waited` reports whether any poll
    actually elapsed, which is what the caller re-checks the rest of the intent
    against.
    """
    polls = CONFIRM_POLLS if polls is None else polls
    poll_seconds = CONFIRM_POLL_SECONDS if poll_seconds is None else poll_seconds
    result = {'sid': binding.session_id, 'confirmed': False, 'reason': 'not_attempted',
              'locked_hint': None, 'shield': None, 'requests': 0, 'polls': 0,
              'waited': False, 'intent_error': None, 'request_errors': []}
    for poll in range(1, polls + 1):
        if poll > 1 and result['waited']:
            # Every further request is a fresh act, not the continuation of the
            # first one, so the intent that justified it is re-read before the
            # retry rather than after: a retry that outlives its own request is
            # how a waiting transition locks a screen for a desktop nobody is
            # attached for any more. Only a poll that actually waited is
            # re-checked - the operator can only have changed their mind while
            # this loop was not looking - so a confirmation that settles at once
            # costs no extra device scan.
            try:
                keyboard, mouse = (inputs if intent is None else intent)()
                pending = wanted_mode(config(), keyboard, mouse)[0]
            except Exception as error:
                # An intent that cannot be read is not permission to keep
                # asking: the retry stops and says why.
                result['reason'] = 'intent_unreadable'
                result['intent_error'] = str(error)
                return result
            if pending != 'desktop':
                result['keyboard'], result['mouse'] = keyboard, mouse
                result['reason'] = 'intent_changed'
                return result
        if poll == 1 or poll % RE_REQUEST_EVERY == 1:
            request_lock(binding, result)
        result['polls'] = poll
        properties = session_properties(binding.session_id)
        if properties is None:
            result['reason'] = 'session_unreadable'
            return result
        if binding_from(properties, binding.session_id) != binding:
            result['reason'] = 'session_changed'
            return result
        result['locked_hint'] = properties.get('LockedHint')
        result['shield'] = screen_shield_active()
        if result['locked_hint'] != 'yes':
            result['reason'] = 'locked_hint_not_set'
        elif result['shield'] is False:
            result['reason'] = 'shield_reports_unlocked'
        else:
            result['confirmed'] = True
            result['reason'] = 'confirmed'
            return result
        if poll < polls:
            time.sleep(poll_seconds)
            result['waited'] = True
    return result


def refusal_backoff(refusals):
    """Back off between refused desktop entries so a stuck gate cannot spin."""
    return min(2 ** max(1, min(refusals, 5)), REFUSAL_BACKOFF_MAX)


def establish_kiosk(browser, values, scale):
    """Put a real appliance kiosk on screen after a refused desktop entry.

    Returns what actually happened, so the state file can describe the browser
    instead of the intention: `kept` when the running browser already is the
    kiosk, `started` when this call produced one, and `failed` when the console
    is not on the appliance at all. A kiosk that died while the gate was polling
    is recovered here; an already-running kiosk is never disturbed.
    """
    if browser.running() and browser.mode == 'kiosk':
        return 'kept'
    transform = values.get('kiosk_transform', DEFAULTS['kiosk_transform'])
    try:
        apply_transform(transform, scale)
        browser.stop()
        browser.start('kiosk', transform)
    except Exception as error:  # the supervisor must outlive one failed launch
        log('kiosk fallback could not start a browser: %s' % error)
        return 'failed'
    if browser.running() and browser.mode == 'kiosk':
        return 'started'
    return 'failed'


def actual_mode(browser, fallback):
    """The mode the state file may honestly report after a refused entry.

    It names the browser that exists, not the one that was wanted: `none` when
    the fallback could not produce a browser either, because a state file that
    says `kiosk` while nothing is running is the claim this finding is about.
    """
    if browser.running() and browser.mode:
        return browser.mode
    return 'none'


def changed_session(gate):
    """Why the confirmed session is no longer the eligible one, or None.

    A confirmation is not atomic over the compositor: the lock can be confirmed
    and a replacement session appear before the rotation, the kiosk stop or the
    desktop browser. Re-selection is the whole check - the eligible session must
    still be exactly the lifetime this gate confirmed.
    """
    current, reason = graphical_session(binding_values())
    if current is None:
        return reason
    if current.fence() != gate.fence():
        return 'session_replaced'
    return None


def intent_lost(values, mode, *, lenient=False):
    """The operator's current intent when it is no longer `mode`, or None.

    Both the override and the inputs are re-read together, because the resolved
    mode is the whole question: a keyboard unplugged during the lock request and
    an explicit kiosk override written during the rotation are the same answer -
    stop this transition. The override is read first because it is the cheapest
    and the most explicit thing the operator can write.

    `lenient` decides what an unreadable intent means. After the lock the
    request has already blocked, so a read that cannot be taken leaves the
    desktop unasked-for with nothing to say otherwise: that refuses. On the last
    check before the browser starts, the intent was already read at the top of
    this pass and re-read after the lock, so a third read that still cannot be
    taken adds nothing and must not cancel an otherwise verified entry.

    An interrupted read is not a verdict. When the failed read carries the
    operator state it was trying to report - an inputs() that was cut short
    still knows which devices were attached - that state answers the question
    and is used, in either mode. Only a read that carries nothing usable makes
    the answer unknown.
    """
    try:
        keyboard, mouse = inputs()
        pending, source = wanted_mode(values, keyboard, mouse)
    except Exception as error:
        reported = getattr(error, 'inputs', None)
        if not (isinstance(reported, tuple) and len(reported) == 2):
            return {'lost': not lenient, 'reason': None if lenient else 'intent_unreadable',
                    'keyboard': None, 'mouse': None, 'source': None, 'detail': str(error)}
        keyboard, mouse = reported
        pending, source = wanted_mode(values, keyboard, mouse)
        if pending != mode:
            return {'lost': True, 'reason': 'intent_changed', 'keyboard': keyboard,
                    'mouse': mouse, 'source': source, 'detail': str(error)}
        return {'lost': False, 'reason': None, 'keyboard': keyboard, 'mouse': mouse,
                'source': source, 'detail': str(error)}
    if pending != mode:
        return {'lost': True, 'reason': 'intent_changed', 'keyboard': keyboard,
                'mouse': mouse, 'source': source, 'detail': None}
    return {'lost': False, 'reason': None, 'keyboard': keyboard, 'mouse': mouse,
            'source': source, 'detail': None}


def refuse_desktop(browser, values, scale, *, reason, source, keyboard, mouse, refusals, extra=None):
    """Stay on the appliance and write down what is actually running there.

    A refused desktop entry owes the operator a working console, not a state
    string: the kiosk is kept or restarted first, and the state file then names
    the browser that exists. `browser_mode` reports what that browser is, which
    is None when nothing is running at all.
    """
    fallback = establish_kiosk(browser, values, scale)
    state = dict(mode=actual_mode(browser, fallback), source=source, keyboard=keyboard,
                 mouse=mouse, panel_scale=scale, desktop_refused=reason, refusals=refusals,
                 kiosk_fallback=fallback,
                 browser_pid=browser.process.pid if browser.running() else None,
                 browser_mode=browser.mode,
                 updated=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
    if extra:
        state.update(extra)
    write_state(state)
    return fallback


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-native-pin', action='store_true',
                        help='future mandatory compositor-guard lane; refuse absent guard')
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        raise SystemExit('run as the operator account, not root')
    lock_path = RUNTIME / 'sage-lantern-mode.lock'
    lock = lock_path.open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log('another mode supervisor already holds the lock; exiting')
        return 0
    values = config()
    scale = panel_scale(values)
    browser = Browser()
    last = None
    gate = None          # the confirmed binding this desktop entry is standing on
    refusals = 0
    log('mode supervisor starting')
    while True:
        keyboard, mouse = inputs()
        mode, source = wanted_mode(values, keyboard, mouse)
        restart = mode == last and not browser.running()
        if restart and mode == 'desktop':
            # A browser that died inside a continuing, gated session may be
            # restarted without a second login. That holds only while the session
            # the gate confirmed is still the live one.
            current, reason = graphical_session(binding_values())
            if gate is None or current is None or current.fence() != gate.fence():
                log('desktop browser restart refused (%s): the gated session is gone' % reason)
                mode, source = 'kiosk', 'gate-lost'
                restart = False
        if args.require_native_pin and not native_guard_mode(mode,
                change=mode != last or not browser.running()):
            write_state(dict(mode=last, requested_mode=mode, native_guard='unavailable'))
            time.sleep(1)
            continue
        if mode != last:
            if mode == 'desktop':
                binding, reason = graphical_session(binding_values())
                if binding is None:
                    refusals += 1
                    log('desktop refused (%s): nothing to lock; staying in %s'
                        % (reason, last or 'kiosk'))
                    refuse_desktop(browser, values, scale, reason=reason, source=source,
                                   keyboard=keyboard, mouse=mouse, refusals=refusals)
                    time.sleep(refusal_backoff(refusals))
                    continue
                result = lock_and_confirm(binding)
                if result['confirmed']:
                    # Requesting the lock blocks, and so does every poll that
                    # waits for it, so the mode that asked for it is not current
                    # evidence by the time it reports back - whether or not a
                    # poll happened to sleep. Re-read the whole resolved mode
                    # before anything at all is exposed on this gate, and refuse
                    # when even that cannot be read: the request that just
                    # returned is the blocking operation, and the intent that
                    # preceded it is not evidence for what to do after it.
                    intent = intent_lost(values, 'desktop')
                    if intent['lost']:
                        result['confirmed'] = False
                        result['reason'] = intent['reason']
                        result['intent_error'] = intent['detail']
                    elif intent['keyboard'] is not None:
                        keyboard, mouse, source = intent['keyboard'], intent['mouse'], intent['source']
                if not result['confirmed']:
                    refusals += 1
                    log('desktop refused (%s): session %s, LockedHint=%s, shield=%s; staying in %s'
                        % (result['reason'], binding.session_id, result['locked_hint'],
                           result['shield'], last or 'kiosk'))
                    refuse_desktop(browser, values, scale, reason=result['reason'], source=source,
                                   keyboard=keyboard, mouse=mouse, refusals=refusals,
                                   extra=dict(locked_hint=result['locked_hint'],
                                              shield=result['shield'],
                                              lock_error=result['intent_error'],
                                              lock_requests=result['requests'],
                                              lock_polls=result['polls']))
                    time.sleep(refusal_backoff(refusals))
                    continue
                gate = binding
                log('session %s confirmed locked before exposure (%s, %d poll(s), %d request(s))'
                    % (binding.session_id, result['reason'], result['polls'], result['requests']))
                refused = changed_session(gate)
                if refused is not None:
                    refusals += 1
                    gate = None
                    log('desktop refused (%s): the confirmed session is no longer current' % refused)
                    refuse_desktop(browser, values, scale, reason=refused, source=source,
                                   keyboard=keyboard, mouse=mouse, refusals=refusals)
                    time.sleep(refusal_backoff(refusals))
                    continue
            transform = values['desktop_transform'] if mode == 'desktop' else values['kiosk_transform']
            apply_transform(transform, scale)
            if mode == 'desktop':
                refused = changed_session(gate)
                if refused is not None:
                    refusals += 1
                    gate = None
                    log('desktop refused (%s): the session changed during the rotation' % refused)
                    refuse_desktop(browser, values, scale, reason=refused, source=source,
                                   keyboard=keyboard, mouse=mouse, refusals=refusals)
                    time.sleep(refusal_backoff(refusals))
                    continue
                # The rotation and the browser shutdown below are the last two
                # things that happen before a desktop is exposed. Both block, and
                # either can outlast the intent or the session that justified
                # them, so both are re-read after they return and before the
                # browser that would expose them is started. Nothing is admitted
                # on evidence that predates a blocking operation.
                intent = intent_lost(values, 'desktop', lenient=True)
                refused = changed_session(gate)
                if intent['lost'] or refused is not None:
                    refusals += 1
                    gate = None
                    reason = intent['reason'] or refused or 'intent_unreadable'
                    log('desktop refused (%s): the entry did not survive the rotation' % reason)
                    refuse_desktop(browser, values, scale, reason=reason, source=source,
                                   keyboard=keyboard, mouse=mouse, refusals=refusals,
                                   extra=dict(intent_error=intent['detail']))
                    time.sleep(refusal_backoff(refusals))
                    continue
                keyboard, mouse, source = intent['keyboard'], intent['mouse'], intent['source']
            browser.stop()
            if mode == 'desktop':
                # Browser.stop waits up to eight seconds for the kiosk to go, and
                # that is long enough for the session to be replaced or for the
                # operator to withdraw the request underneath a confirmed gate.
                # Re-ask both after the stop and before the start: the check
                # above ran before this port blocked, so it cannot see anything
                # that happened while the kiosk was going away.
                intent = intent_lost(values, 'desktop', lenient=True)
                refused = changed_session(gate)
                if intent['lost'] or refused is not None:
                    refusals += 1
                    gate = None
                    reason = intent['reason'] or refused or 'intent_unreadable'
                    log('desktop refused (%s): the entry did not survive the kiosk stop'
                        % reason)
                    refuse_desktop(browser, values, scale, reason=reason, source=source,
                                   keyboard=keyboard, mouse=mouse, refusals=refusals,
                                   extra=dict(intent_error=intent['detail']))
                    time.sleep(refusal_backoff(refusals))
                    continue
                keyboard, mouse, source = intent['keyboard'], intent['mouse'], intent['source']
            browser.start(mode, transform)
            if mode == 'kiosk':
                gate = None
            last = mode
            refusals = 0
            log(f'mode={mode} source={source} keyboard={keyboard} mouse={mouse} scale={scale:g}')
        elif restart:
            transform = values['desktop_transform'] if mode == 'desktop' else values['kiosk_transform']
            delay = browser.note_exit()
            browser.start(mode, transform)
            log(f'browser was not running; restart after {delay}s backoff (failures={browser.failures})')
            write_state(dict(mode=mode, source=source, keyboard=keyboard, mouse=mouse,
                             panel_scale=scale,
                             browser_pid=browser.process.pid if browser.running() else None,
                             browser_mode=browser.mode, backoff_seconds=delay,
                             updated=time.strftime('%Y-%m-%dT%H:%M:%S%z')))
            time.sleep(delay)
            continue
        else:
            browser.failures = 0
        write_state(dict(mode=mode, source=source, keyboard=keyboard, mouse=mouse,
                         panel_scale=scale,
                         browser_pid=browser.process.pid if browser.running() else None,
                         browser_mode=browser.mode, updated=time.strftime('%Y-%m-%dT%H:%M:%S%z')))
        time.sleep(2)


if __name__ == '__main__':
    sys.exit(main())
