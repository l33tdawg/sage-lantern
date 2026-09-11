"""Public USB inventory only; no RF commands, private keys, flashing or reset.

Initial TTY open/close electrical behavior must be reviewed by the operator.
TIOCEXCL cannot prevent a privileged competing opener; keep producers stopped.
Default invocation inspects USB only. --query explicitly permits two USB queries.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import sys
import termios
import time


STABLE = '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_44:1B:F6:62:56:54-if00'
USB = dict(stable_path=STABLE, serial='44:1B:F6:62:56:54', vendor_id='303a', product_id='1001')
FIRMWARE = 'v1.17.1-d929643'
COMMANDS = (bytes((22, 13)), b'\x01' + bytes(7) + b'Lantern')


class InventoryRefused(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise InventoryRefused(message)


def refusal_reason(error):
    if type(error) is InventoryRefused:
        return re.sub(r'[^a-z0-9]+', '_', str(error).lower()).strip('_')
    if isinstance(error, OSError):
        return 'os_error_' + str(error.errno) if type(error.errno) is int else 'os_error'
    return 'internal_error'


def inspect_usb():
    stable = Path(STABLE)
    require(stable.is_symlink(), 'stable USB link missing')
    target = stable.resolve(strict=True)
    require(target.parent == Path('/dev') and re.fullmatch(r'tty(?:ACM|USB)[0-9]+', target.name), 'unexpected TTY')
    info = target.stat()
    require(stat.S_ISCHR(info.st_mode), 'not a character device')
    device = Path(f'/sys/dev/char/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}/device').resolve(strict=True)
    for parent in (device, *device.parents):
        if (parent / 'idVendor').is_file():
            values = [(parent / field).read_text().strip() for field in ('idVendor', 'idProduct', 'serial')]
            require(values == [USB['vendor_id'], USB['product_id'], USB['serial']], 'USB identity mismatch')
            return target, (info.st_dev, info.st_ino, info.st_rdev)
    raise InventoryRefused('USB identity unavailable')


def no_other_openers(device_number, owned_fd=None):
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / 'fd').iterdir())
        except FileNotFoundError:
            continue
        for descriptor in descriptors:
            if process.name == str(os.getpid()) and descriptor.name == str(owned_fd):
                continue
            try:
                info = descriptor.stat()
            except FileNotFoundError:
                continue
            require(not (stat.S_ISCHR(info.st_mode) and info.st_rdev == device_number), 'competing serial opener')


def remaining(deadline):
    value = deadline - time.monotonic()
    require(value > 0, 'query timeout')
    return value


def read_exact(descriptor, size, deadline):
    result = bytearray()
    while len(result) < size:
        readable, _, _ = select.select([descriptor], [], [], remaining(deadline))
        require(readable, 'query timeout')
        chunk = os.read(descriptor, size - len(result))
        require(chunk, 'incomplete response')
        result.extend(chunk)
    return bytes(result)


def exchange(descriptor, command, expected_code, deadline):
    require(command in COMMANDS, 'forbidden command')
    pending = b'<' + len(command).to_bytes(2, 'little') + command
    while pending:
        _, writable, _ = select.select([], [descriptor], [], remaining(deadline))
        require(writable, 'query timeout')
        written = os.write(descriptor, pending)
        require(written > 0, 'incomplete write')
        pending = pending[written:]
    header = read_exact(descriptor, 3, deadline)
    size = int.from_bytes(header[1:], 'little')
    require(header[:1] == b'>' and 1 <= size <= 176, 'invalid frame')
    response = read_exact(descriptor, size, deadline)
    require(response[0] == expected_code, 'unexpected response; no retry')
    return response


def query(descriptor):
    deadline = time.monotonic() + 6
    info = exchange(descriptor, COMMANDS[0], 13, deadline)
    require(len(info) == 82, 'unreviewed firmware response length')
    require(info[1] == 13, 'unreviewed firmware protocol')
    require(info[60:80].split(b'\0', 1)[0] == FIRMWARE.encode(), 'unreviewed firmware version')
    identity = exchange(descriptor, COMMANDS[1], 5, deadline)
    require(58 <= len(identity) <= 90, 'invalid public identity response')
    public = identity[4:36]
    require(len(public) == 32 and public != bytes(32), 'invalid public identity')
    return dict(schema='sage.lantern.meshcore-radio-public.v1', unit_id='unit-b', usb=USB.copy(),
                radio_identity=dict(public_key=public.hex(), public_key_sha256='sha256:' + hashlib.sha256(public).hexdigest()))


def collect(expected):
    target, identity = inspect_usb()
    require(identity == expected, 'USB changed')
    no_other_openers(identity[2])
    descriptor = os.open(target, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        require((info.st_dev, info.st_ino, info.st_rdev) == identity, 'TTY changed during open')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(descriptor, termios.TIOCEXCL)
        no_other_openers(identity[2], descriptor)
        settings = termios.tcgetattr(descriptor)
        settings[:4] = [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0]
        settings[4:6] = [termios.B115200, termios.B115200]
        settings[6][termios.VMIN] = 0
        settings[6][termios.VTIME] = 0
        termios.tcsetattr(descriptor, termios.TCSANOW, settings)
        require(not select.select([descriptor], [], [], 0)[0], 'preexisting serial data; no flush/retry')
        result = query(descriptor)
        require(inspect_usb()[1] == identity, 'USB changed during query')
        no_other_openers(identity[2], descriptor)
        return result
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', action='store_true')
    parser.add_argument('--operator-reviewed-open-close-and-stopped-producers', action='store_true')
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root inspection required')
    target, identity = inspect_usb()
    no_other_openers(identity[2])
    if not args.query:
        result = dict(schema='sage.lantern.radio-usb-preflight.v1', unit_id='unit-b', usb=USB,
                      target=str(target), queried=False)
    else:
        require(args.operator_reviewed_open_close_and_stopped_producers, 'operator review required')
        result = collect(identity)
    print(json.dumps(result, sort_keys=True))


def entrypoint():
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(schema='sage.lantern.radio-refusal.v1', success=False,
                              reason=refusal_reason(error)), sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(entrypoint())
