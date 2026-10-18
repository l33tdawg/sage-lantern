#!/usr/bin/env python3
"""Rotate the C-F panel output through Mutter; never a CSS transform.

Reads the live display configuration, changes only the transform of the
current logical monitor, and re-applies the existing connector and mode.
Touch mapping follows the logical monitor, so display and touch rotate
together. No reboot, no session restart, no mode invention.
"""
import argparse
import sys

import gi

gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib  # noqa: E402

BUS = 'org.gnome.Mutter.DisplayConfig'
PATH = '/org/gnome/Mutter/DisplayConfig'
TRANSFORMS = {'normal': 0, 'rotate-90': 1, 'rotate-180': 2, 'rotate-270': 3}


def call(method, parameters=None):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync(BUS, PATH, BUS, method, parameters, None, Gio.DBusCallFlags.NONE, 5000, None)


def current():
    serial, monitors, logical, _properties = call('GetCurrentState', None).unpack()
    if not logical:
        raise SystemExit('no logical monitor')
    entry = logical[0]
    x, y, scale, transform, primary, attached = entry[0], entry[1], entry[2], entry[3], entry[4], entry[5]
    if not attached:
        raise SystemExit('logical monitor has no attached output')
    connector = attached[0][0]
    modes = next((monitor[1] for monitor in monitors if monitor[0][0] == connector), [])
    if not modes:
        raise SystemExit(f'no modes for {connector}')
    current_mode = next((mode[0] for mode in modes
                         if len(mode) > 6 and isinstance(mode[6], dict) and mode[6].get('is-current')), None)
    mode_id = current_mode or modes[0][0]
    return dict(serial=serial, monitors=monitors, x=x, y=y, scale=scale, transform=transform,
                primary=primary, connector=connector, mode_id=mode_id)


def monitors_tuple(state, *, transform, scale):
    """The exact logical-monitor tuple sent to Mutter: same position, connector,
    current mode and primary flag; only the transform and scale may change."""
    return [(state['x'], state['y'], scale, transform, bool(state['primary']),
             [(state['connector'], state['mode_id'], {})])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('transform', nargs='?', choices=sorted(TRANSFORMS))
    parser.add_argument('--scale', type=float,
                        help='Logical-monitor scale to apply (the 720x1560 panel '
                             'needs 1.0: fractional scaling crops this Chromium build)')
    parser.add_argument('--print', action='store_true')
    args = parser.parse_args()
    if args.scale is not None and not 0.5 <= args.scale <= 4.0:
        parser.error('--scale must be between 0.5 and 4.0')
    state = current()
    if args.print or (args.transform is None and args.scale is None):
        name = next(key for key, value in TRANSFORMS.items() if value == state['transform'])
        print(f"connector={state['connector']} mode={state['mode_id']} "
              f"transform={name} scale={state['scale']}")
        return 0
    wanted = state['transform'] if args.transform is None else TRANSFORMS[args.transform]
    scale = state['scale'] if args.scale is None else args.scale
    if wanted == state['transform'] and scale == state['scale']:
        label = next(key for key, value in TRANSFORMS.items() if value == wanted)
        print(f"already {label} scale={scale}")
        return 0
    logical_monitors = monitors_tuple(state, transform=wanted, scale=scale)
    call('ApplyMonitorsConfig',
         GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
                      (state['serial'], 1, logical_monitors, {})),)
    label = next(key for key, value in TRANSFORMS.items() if value == wanted)
    print(f"applied {label} scale={scale} on {state['connector']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
