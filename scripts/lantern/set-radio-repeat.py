"""Set or clear the MeshCore companion's repeat (relay) bit, reviewed write.

Runs on the appliance as root with the mesh runtime stopped, so nothing else
owns the serial device. Reads the live radio parameters and firmware identity
first, then writes CMD_SET_RADIO_PARAMS carrying the values just read back with
only the repeat byte changed, and verifies the read-back: every other field
must be unchanged and the repeat flag must match the request. Nothing is
transmitted - the repeat bit only changes how the firmware treats flood traffic
it hears from other nodes.

    python3 set-radio-repeat.py --serial <usb-serial> --query
    python3 set-radio-repeat.py --serial <usb-serial> --enable \
        --operator-reviewed-open-close-and-stopped-producers \
        --consent SET-REPEAT-ON-<usb-serial> --apply

The write is refused on firmware older than r4 (which has no repeat-frequency
allowance for the commissioned 917.375 MHz channel) and on any frequency other
than the commissioned one, because the allowance is pinned to that exact
frequency at build time. Each attempt journals to its own file under
/var/lib/sage-lantern-radio-repeat and is never retried automatically.
"""

import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import stat
import struct
import sys
import termios
import time


SPEC = importlib.util.spec_from_file_location(
    'radio_inventory', Path(__file__).with_name('collect-b-radio-inventory.py'))
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)
VENDOR, PRODUCT = '303a', '1001'
SERIAL = re.compile(r'[0-9A-F]{2}(?::[0-9A-F]{2}){5}')
FREQ_KHZ = 917375
REPEAT_CAPABLE = re.compile(r'v1\.17\.1-d929643-r([0-9]+)')
JOURNAL_ROOT = Path('/var/lib/sage-lantern-radio-repeat')
QUERY = bytes((22, 13))
APP_START = b'\x01' + bytes(7) + b'Lantern'
require = INVENTORY.require


def stable_path(serial):
    return Path('/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_' + serial + '-if00')


def inspect_usb(serial):
    require(SERIAL.fullmatch(serial) is not None, 'usb serial malformed')
    stable = stable_path(serial)
    require(stable.is_symlink(), 'stable USB link missing')
    target = stable.resolve(strict=True)
    require(target.parent == Path('/dev') and re.fullmatch(r'ttyACM[0-9]+', target.name), 'unexpected TTY')
    info = target.stat()
    require(stat.S_ISCHR(info.st_mode), 'not a character device')
    device = Path(f'/sys/dev/char/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}/device').resolve(strict=True)
    for parent in (device, *device.parents):
        if (parent / 'idVendor').is_file():
            values = [(parent / field).read_text().strip() for field in ('idVendor', 'idProduct', 'serial')]
            require(values == [VENDOR, PRODUCT, serial], 'USB identity mismatch')
            return target, (info.st_dev, info.st_ino, info.st_rdev)
    raise INVENTORY.InventoryRefused('USB identity unavailable')


def parse_info(response):
    require(type(response) is bytes and len(response) == 82, 'firmware response length')
    require(response[:2] == bytes((13, 13)), 'firmware protocol mismatch')
    version = response[60:80].split(b'\0', 1)[0]
    try:
        version = version.decode('ascii')
    except UnicodeError:
        raise INVENTORY.InventoryRefused('firmware version encoding') from None
    require(re.fullmatch(r'v1\.17\.1-d929643(?:-r[1-9][0-9]*)?', version) is not None, 'firmware version mismatch')
    require(response[80] in (0, 1) and response[81] in (0, 1, 2), 'firmware flags invalid')
    return dict(firmware=version, protocol=13, repeat=response[80], path_hash_mode=response[81])


def parse_self(response, expected_key=None):
    require(type(response) is bytes and 58 <= len(response) <= 89, 'self info length')
    require(response[:2] == bytes((5, 1)), 'self info type')
    if expected_key is not None:
        require(response[4:36] == bytes.fromhex(expected_key), 'public key mismatch')
    power = struct.unpack('b', response[2:3])[0]
    maximum = response[3]
    require(-9 <= power <= maximum <= 127 and maximum >= 10, 'power range invalid')
    frequency, bandwidth, spreading, coding = struct.unpack('<IIBB', response[48:58])
    require(150000 <= frequency <= 2500000 and 7000 <= bandwidth <= 500000
            and 5 <= spreading <= 12 and 5 <= coding <= 8, 'radio range invalid')
    name_bytes = response[58:]
    require(b'\0' not in name_bytes, 'name encoding invalid')
    try:
        name = name_bytes.decode('utf-8')
    except UnicodeError:
        raise INVENTORY.InventoryRefused('name encoding invalid') from None
    require(all(character.isprintable() for character in name), 'name encoding invalid')
    return dict(public_key=response[4:36].hex(), tx_setting_dbm=power, max_tx_setting_dbm=maximum,
                freq_khz=frequency, bandwidth_hz=bandwidth, sf=spreading, cr=coding, name=name)


def radio_command(snapshot, repeat):
    return (b'\x0b' + struct.pack('<IIBB', snapshot['freq_khz'], snapshot['bandwidth_hz'],
                                  snapshot['sf'], snapshot['cr']) + bytes((repeat,)))


def snapshot(exchange, expected_key=None):
    return {**parse_info(exchange(QUERY)), **parse_self(exchange(APP_START), expected_key)}


def write_all(descriptor, content):
    while content:
        written = os.write(descriptor, content)
        require(written > 0, 'journal write failed')
        content = content[written:]


@contextmanager
def journal(stamp, serial, root=JOURNAL_ROOT):
    require(root.is_absolute() and '..' not in root.parts, 'journal path invalid')
    require(re.fullmatch(r'[0-9]{8}-[0-9]{6}', stamp) is not None, 'journal stamp invalid')
    directory, descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY), None
    try:
        for component in root.parts[1:]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=directory)
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
            metadata = os.fstat(directory)
            require(metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o022, 'journal ancestor unsafe')
        metadata = os.fstat(directory)
        require(stat.S_IMODE(metadata.st_mode) == 0o700, 'journal directory mode')
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        name = stamp + '-' + serial.replace(':', '')
        os.mkdir(name, 0o700, dir_fd=directory)
        attempt = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        os.close(directory)
        directory = attempt
        descriptor = os.open('attempt.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.fsync(directory)

        def append(event):
            write_all(descriptor, (json.dumps(event, sort_keys=True, ensure_ascii=True) + '\n').encode())
            os.fsync(descriptor)

        yield append
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


class Exchange:
    """One framed exchange per call, bounded by an allowlist of exact commands."""

    def __init__(self, descriptor, verify, allowed, drain_pending=False):
        self.descriptor = descriptor
        self.verify = verify
        self.allowed = allowed
        self.drain_pending = drain_pending
        self.drained_bytes = 0
        self.poisoned = False

    def drain(self, limit=4096):
        """Discard bytes the host TTY already holds, then return how many.

        Only needed when a previous attempt was abandoned mid-exchange (a
        flash's hard reset leaves its late reply in the buffer). The operator
        reviews the count, and the caller reads the live state afterwards, so
        the write is still built from the device's own current values.
        """
        self.verify()
        data = bytearray()
        while len(data) <= limit and select.select([self.descriptor], [], [], 0)[0]:
            chunk = os.read(self.descriptor, min(256, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        require(len(data) <= limit, 'drain limit exceeded')
        self.verify()
        return len(data)

    def __call__(self, command):
        require(not self.poisoned and command in self.allowed, 'exchange forbidden')
        try:
            self.verify()
            if self.drain_pending:
                # Unsolicited frames (a new-advert push when the radio hears an
                # advert) are expected on this link; they are framed and never
                # a reply to a command that has not been sent yet.
                self.drained_bytes += self.drain()
            else:
                require(not select.select([self.descriptor], [], [], 0)[0], 'preexisting serial data')
            deadline = time.monotonic() + 3
            pending = b'<' + len(command).to_bytes(2, 'little') + command
            while pending:
                _, writable, _ = select.select([], [self.descriptor], [], INVENTORY.remaining(deadline))
                require(writable, 'serial timeout')
                written = os.write(self.descriptor, pending)
                require(written > 0, 'serial incomplete write')
                pending = pending[written:]
            header = INVENTORY.read_exact(self.descriptor, 3, deadline)
            size = int.from_bytes(header[1:], 'little')
            require(header[:1] == b'>' and 1 <= size <= 176, 'serial frame invalid')
            response = INVENTORY.read_exact(self.descriptor, size, deadline)
            self.verify()
            return response
        except BaseException:
            self.poisoned = True
            raise


@contextmanager
def connection(serial, allowed, drain_pending=False):
    target, identity = inspect_usb(serial)
    INVENTORY.no_other_openers(identity[2])
    descriptor = os.open(target, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        def verify():
            metadata = os.fstat(descriptor)
            require((metadata.st_dev, metadata.st_ino, metadata.st_rdev) == identity, 'TTY changed')
            require(inspect_usb(serial)[1] == identity, 'USB changed')
            INVENTORY.no_other_openers(identity[2], descriptor)

        verify()
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(descriptor, termios.TIOCEXCL)
        verify()
        settings = termios.tcgetattr(descriptor)
        settings[:4] = [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0]
        settings[4:6] = [termios.B115200, termios.B115200]
        settings[6][termios.VMIN] = 0
        settings[6][termios.VTIME] = 0
        termios.tcsetattr(descriptor, termios.TCSANOW, settings)
        yield Exchange(descriptor, verify, allowed, drain_pending)
        verify()
    finally:
        os.close(descriptor)


def evaluate(before, repeat):
    """Refuse a repeat request the deployed firmware cannot honour."""
    capability = REPEAT_CAPABLE.fullmatch(before['firmware'])
    if repeat == 1:
        require(capability is not None and int(capability.group(1)) >= 4, 'firmware has no repeat allowance')
        require(before['freq_khz'] == FREQ_KHZ, 'repeat allowance frequency mismatch')
    return dict(before, repeat=repeat)


def configure(exchange, repeat, append, expected_key=None):
    before = snapshot(exchange, expected_key)
    append(dict(event='prepared', before=before, requested_repeat=repeat,
                outcome='not_started', schema='sage.lantern.radio-repeat.v1'))
    evaluate(before, repeat)
    if before['repeat'] == repeat:
        append(dict(event='already_set', repeat=repeat, outcome='no_write_needed'))
        return before
    command = radio_command(before, repeat)
    exchange.allowed.add(command)
    append(dict(event='setter_intent', opcode=command[0], outcome='unknown_until_verified'))
    response = exchange(command)
    require(response == b'\x00', 'setter result unverified')
    observed = snapshot(exchange, expected_key)
    require(observed == dict(before, repeat=repeat), 'readback mismatch')
    append(dict(event='setter_verified', opcode=command[0], observed=observed))
    return observed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--serial', required=True, help='USB serial of the companion radio')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--query', action='store_true')
    mode.add_argument('--enable', action='store_true')
    mode.add_argument('--disable', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--operator-reviewed-open-close-and-stopped-producers', action='store_true')
    parser.add_argument('--consent')
    parser.add_argument('--expect-public-key', help='64 hex characters; the key the runtime is pinned to')
    parser.add_argument('--drain', action='store_true', help='discard data an abandoned attempt left buffered')
    parser.add_argument('--journal-root', default=str(JOURNAL_ROOT))
    parser.add_argument('--stamp', default=time.strftime('%Y%m%d-%H%M%S', time.gmtime()))
    args = parser.parse_args(argv)
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root required')
    desired = 1 if args.enable else 0
    require(args.enable or args.disable or args.query, 'mode required')
    require(not args.apply or args.enable or args.disable, 'apply needs enable or disable')
    require(args.apply or args.consent is None, 'unexpected consent')
    if args.apply:
        require(args.operator_reviewed_open_close_and_stopped_producers, 'operator review required')
        require(args.consent == f"SET-REPEAT-{'ON' if desired else 'OFF'}-{args.serial}", 'apply consent required')
    expected_key = None
    if args.expect_public_key is not None:
        require(re.fullmatch(r'[0-9a-f]{64}', args.expect_public_key) is not None, 'expected key malformed')
        expected_key = args.expect_public_key
    if args.apply:
        with journal(args.stamp, args.serial, root=Path(args.journal_root)) as append:
            with connection(args.serial, {QUERY, APP_START}, drain_pending=args.drain) as exchange:
                result = configure(exchange, desired, append, expected_key)
                append(dict(event='drained', bytes=exchange.drained_bytes))
            append(dict(event='complete', observed=result, outcome='configuration_readback_verified'))
        mode = 'apply'
    else:
        with connection(args.serial, {QUERY, APP_START}, drain_pending=args.drain) as exchange:
            result = snapshot(exchange, expected_key)
        if args.enable or args.disable:
            evaluate(result, desired)
        mode = 'query' if args.query else 'check'
    print(json.dumps(dict(schema='sage.lantern.radio-repeat-review.v1', serial=args.serial,
                          mode=mode, requested_repeat=desired if (args.enable or args.disable) else None,
                          result=result, rf_transmission=False, rf_delivery_verified=False), sort_keys=True))


def entrypoint():
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(success=False, reason=INVENTORY.refusal_reason(error),
                              instruction='preserve_journal_no_automatic_retry')), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(entrypoint())
