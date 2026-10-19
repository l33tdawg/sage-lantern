#!/usr/bin/env python3
"""Apply or check the kiosk session settings that keep a Lantern usable.

The appliance's lock is the Lantern PIN, not the operator account password. A
GNOME lock screen strands the panel behind a password the owner may never set —
on C a stray `Super+L` did exactly that on 30 Sep 2026. Locking therefore stays
*deliberate*: the shortcut is unbound and the session never idle-locks, so
nothing can lock the appliance by accident. It must also stay *possible* —
D-022 requires a fresh operator login on entering the docked desktop, and the
mode supervisor asks for that lock on the transition. Disabling the lock
outright (disable-lock-screen true, lock-enabled false) made the D-022 gate
unsatisfiable: `loginctl lock-session` returned 0 while doing nothing. Those two
values are therefore reversed here, which is what lets the gate exist at all.

Eight settings, applied to the operator account's live session and verified by
read-back:

  org.gnome.desktop.lockdown disable-lock-screen             false
  org.gnome.settings-daemon.plugins.media-keys screensaver   []
  org.gnome.desktop.screensaver lock-enabled                 true
  org.gnome.desktop.session idle-delay                       0
  org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type        nothing
  org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout     0
  org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type   nothing
  org.gnome.settings-daemon.plugins.power sleep-inactive-battery-timeout 0

The four power-plugin settings are what keep the console usable *and* keep the
sleep lane honest: the reviewed lane installs `AllowSuspend=yes`, so the session
must not ask logind to sleep on its own. GNOME's default (suspend after fifteen
idle minutes on AC) would reach systemd with no transition, no fence and no
drain — an unmanaged suspend that the guard refuses, latching
`systemd-suspend.service` failed and `suspend.target` active. The controller is
the only reviewed suspend source.

`--check` is read-only and exits non-zero when the session would strand the
appliance in either direction: a lock the operator cannot get past (a bound
shortcut, an idle timer) or a lock that cannot be requested at all. `--apply`
writes them. Both refuse when the account has no running session.
"""

import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys


SETTINGS = (
    ('org.gnome.desktop.lockdown', 'disable-lock-screen', 'false'),
    ('org.gnome.settings-daemon.plugins.media-keys', 'screensaver', '[]'),
    ('org.gnome.desktop.screensaver', 'lock-enabled', 'true'),
    ('org.gnome.desktop.session', 'idle-delay', '0'),
    ('org.gnome.settings-daemon.plugins.power', 'sleep-inactive-ac-type', "'nothing'"),
    ('org.gnome.settings-daemon.plugins.power', 'sleep-inactive-ac-timeout', '0'),
    ('org.gnome.settings-daemon.plugins.power', 'sleep-inactive-battery-type', "'nothing'"),
    ('org.gnome.settings-daemon.plugins.power', 'sleep-inactive-battery-timeout', '0'),
)
GSETTINGS = '/usr/bin/gsettings'
RUNUSER = '/usr/sbin/runuser'


def session_environment(uid):
    runtime = Path(f'/run/user/{uid}')
    if not (runtime / 'bus').exists():
        raise SystemExit('operator_session_unavailable')
    return {'XDG_RUNTIME_DIR': str(runtime),
            'DBUS_SESSION_BUS_ADDRESS': f'unix:path={runtime}/bus'}


def normalize(value):
    """`gsettings get` prints typed values: `@as []`, `uint32 0`, `true`."""
    if value is None:
        return None
    text = value.strip()
    if text.startswith('@'):
        text = text.split(' ', 1)[1].strip() if ' ' in text else ''
    for prefix in ('uint32', 'uint64', 'uint16', 'uint8', 'int32', 'int64', 'int16', 'int8', 'byte'):
        if text.startswith(prefix + ' '):
            text = text[len(prefix) + 1:].strip()
    return text


def read_all(account, run):
    values = {}
    for schema, key, _ in SETTINGS:
        code, output = run(account, ['get', schema, key])
        values[f'{schema} {key}'] = normalize(output.decode('utf-8')) if code == 0 else None
    return values


def runner(account, environment, *, runuser=RUNUSER, gsettings=GSETTINGS):
    def run(user, arguments):
        command = ([runuser, '-u', user, '--'] if os.geteuid() == 0 else []) + [gsettings, *arguments]
        environment_variables = dict(os.environ)
        environment_variables.update(environment)
        result = subprocess.run(command, env=environment_variables, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return result.returncode, result.stdout
    return run


def main(argv=None, run=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', default='sage', help='operator account whose session runs the console')
    parser.add_argument('--apply', action='store_true', help='write the settings (default: check only)')
    parser.add_argument('--runuser', default=RUNUSER, help=argparse.SUPPRESS)
    parser.add_argument('--gsettings', default=GSETTINGS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        account = pwd.getpwnam(args.account)
    except KeyError:
        raise SystemExit('operator_account_unknown') from None
    environment = session_environment(account.pw_uid)
    if run is None:
        run = runner(args.account, environment, runuser=args.runuser, gsettings=args.gsettings)

    before = read_all(args.account, run)
    report = {'schema': 'sage.lantern.kiosk-session-settings.v1', 'account': args.account,
              'mode': 'apply' if args.apply else 'check', 'before': before, 'after': before, 'changed': []}
    if args.apply:
        for schema, key, expected in SETTINGS:
            if before.get(f'{schema} {key}') == expected:
                continue
            code, _ = run(args.account, ['set', schema, key, expected])
            if code != 0:
                raise SystemExit('gsettings_write_failed: ' + schema + ' ' + key)
            report['changed'].append(f'{schema} {key}')
        report['after'] = read_all(args.account, run)
    wrong = [f'{schema} {key}' for schema, key, expected in SETTINGS
             if report['after'].get(f'{schema} {key}') != expected]
    report['wrong'] = wrong
    report['ok'] = not wrong
    print(json.dumps(report, sort_keys=True, indent=1))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
