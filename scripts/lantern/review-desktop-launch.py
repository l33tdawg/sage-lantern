#!/usr/bin/env python3
"""Review launch-path inventory. Reports evidence; never authorizes a launch."""
import argparse
import json
from pathlib import Path


def review(value):
    if (value.get('schema') != 'sage.lantern.desktop-launch-inventory.v1'
            or value.get('same_lifecycle') is not True
            or value.get('lifecycle_before') != value.get('lifecycle_after')
            or value.get('live_changes') != []):
        raise ValueError('read_only_inventory_required')
    findings = []
    files, paths = value['files'], value['paths']
    for name in ('/usr/share/wayland-sessions/gnome.desktop',
                 '/usr/share/wayland-sessions/ubuntu.desktop',
                 '/usr/share/xsessions/sage-lantern.desktop'):
        if name in files:
            findings.append(dict(code='stock_session_route', evidence=name,
                detail='A stock session selector remains; its supported/selected state needs qualification.'))
    for name in ('/home/sage', '/home/sage/.config/systemd/user',
                 '/home/sage/.config/autostart', '/run/user/1000'):
        info = paths.get(name, {})
        if info.get('writable_by_kiosk') is True:
            findings.append(dict(code='user_writable_launch_parent', evidence=name,
                detail='User-owned lookup parents can create or replace launch configuration.'))
    unit_path = value['user_unit_path']
    if unit_path['rc'] != 0 or not unit_path['stdout'].startswith('UnitPath='):
        raise ValueError('unit_lookup_observation_required')
    if any(name.startswith(('/home/sage/', '/run/user/1000/'))
           for name in unit_path['stdout'][len('UnitPath='):].split()):
        findings.append(dict(code='user_unit_precedence', evidence='user_unit_path',
            detail='User/transient/generator lookup remains. A system drop-in alone cannot exclude it.'))
    if value['shell_security_label'] == 'unconfined':
        findings.append(dict(code='unconfined_compositor', evidence='shell_security_label',
            detail='No compositor confinement is established by this observation.'))
    reply = value['controller_pid']
    controller_is_observed_shell = (reply['rc'] == 0 and json.loads(reply['stdout']) ==
        dict(type='u', data=[value['shell_pid']]))
    return dict(schema='sage.lantern.desktop-launch-review.v1', findings=findings,
        observed_stock_shell_controls_seat=controller_is_observed_shell,
        candidate_seat_qualified=False, alternate_launch_closed=False,
        desktop_pin_accepted=False, installed_on_c=False, deployable=False,
        # An empty inventory is never positive authorization/acceptance evidence.
        remaining=['root-controlled selector and non-user-writable session/manager lookups',
            'enforced direct-launch/device/control/injection restrictions across failure and fresh login',
            'exact root-created child on real logind/DRM/evdev after qualified power',
            'physical first-frame/input/failure/sleep and application-preservation acceptance',
            'matched release integration against installed r17'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(review(json.loads(args.inventory.read_text())), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
