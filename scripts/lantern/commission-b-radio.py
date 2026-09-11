"""Pinned B USB commissioning, not RF silence or delivery verification.

Default: USB metadata only. Query/apply require reviewed serial ownership.
The fixed exclusive journal is never removed or automatically resumed.
"""

import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import select
import stat
import struct
import sys
import termios
import time


SPEC = importlib.util.spec_from_file_location('b_radio_inventory', Path(__file__).with_name('collect-b-radio-inventory.py'))
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)
PUBLIC_KEY = bytes.fromhex('c98510036349cec6495b3712880a12ea58d23cd8f09ea9fe9cd804cd98b29e54')
JOURNAL = Path('/var/lib/sage-lantern-b-radio-commission/attempt.jsonl')
CONSENT = 'SET-B-917375-62500-8-5-TX10-SAGE-LANTERN-B'
TARGET = dict(freq_khz=917375, bandwidth_hz=62500, sf=8, cr=5, tx_setting_dbm=10, name='SAGE-LANTERN-B')
QUERY, APP_START = INVENTORY.COMMANDS
NAME = b'\x08SAGE-LANTERN-B'
POWER = b'\x0c\x0a'
RADIO = b'\x0b' + struct.pack('<IIBB', 917375, 62500, 8, 5)
ALLOWED = (QUERY, APP_START, NAME, POWER, RADIO + b'\x00', RADIO + b'\x01')
require = INVENTORY.require


def parse_info(response):
    require(type(response) is bytes and len(response) == 82, 'firmware response length')
    require(response[:2] == bytes((13, 13)), 'firmware protocol mismatch')
    version = response[60:80]
    require(b'\0' in version and version.split(b'\0', 1)[0] == INVENTORY.FIRMWARE.encode(), 'firmware version mismatch')
    require(response[80] in (0, 1) and response[81] in (0, 1, 2), 'firmware flags invalid')
    return dict(firmware=INVENTORY.FIRMWARE, protocol=13, repeat=response[80], path_hash_mode=response[81])


def parse_self(response):
    require(type(response) is bytes and 58 <= len(response) <= 89, 'self info length')
    require(response[:2] == bytes((5, 1)), 'self info type')
    require(response[4:36] == PUBLIC_KEY, 'public key mismatch')
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
    return dict(public_key=PUBLIC_KEY.hex(), tx_setting_dbm=power, max_tx_setting_dbm=maximum,
                freq_khz=frequency, bandwidth_hz=bandwidth, sf=spreading, cr=coding, name=name)


def snapshot(exchange):
    info = parse_info(exchange(QUERY))
    return {**info, **parse_self(exchange(APP_START))}


def write_all(descriptor, content):
    while content:
        written = os.write(descriptor, content)
        require(written > 0, 'journal write failed')
        content = content[written:]


@contextmanager
def journal(path=JOURNAL):
    require(path.is_absolute() and '..' not in path.parts, 'journal path invalid')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    descriptor = None
    try:
        for component in path.parent.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
            metadata = os.fstat(directory)
            require(metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o022, 'journal ancestor unsafe')
        metadata = os.fstat(directory)
        require(stat.S_IMODE(metadata.st_mode) == 0o700, 'journal directory mode')
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
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
    def __init__(self, descriptor, verify):
        self.descriptor = descriptor
        self.verify = verify
        self.poisoned = False

    def __call__(self, command):
        require(not self.poisoned and command in ALLOWED, 'exchange forbidden')
        try:
            self.verify()
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
            if command == QUERY:
                parse_info(response)
            elif command == APP_START:
                parse_self(response)
            else:
                require(response == b'\x00', 'setter result unverified')
            self.verify()
            return response
        except BaseException:
            self.poisoned = True
            raise


@contextmanager
def connection(expected):
    target, identity = INVENTORY.inspect_usb()
    require(identity == expected, 'USB changed')
    INVENTORY.no_other_openers(identity[2])
    descriptor = os.open(target, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        def verify():
            metadata = os.fstat(descriptor)
            require((metadata.st_dev, metadata.st_ino, metadata.st_rdev) == identity, 'TTY changed')
            require(INVENTORY.inspect_usb()[1] == identity, 'USB changed')
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
        yield Exchange(descriptor, verify)
        verify()
    finally:
        os.close(descriptor)


def commission(exchange, append):
    before = snapshot(exchange)
    expected = before.copy()
    append(dict(event='prepared', before=before, target=TARGET, usb=INVENTORY.USB,
                outcome='not_started', schema='sage.lantern.b-radio-commission.v1'))
    operations = [(POWER, {'tx_setting_dbm': 10}), (NAME, {'name': TARGET['name']}),
                  (RADIO + bytes((before['repeat'],)), {key: TARGET[key] for key in ('freq_khz', 'bandwidth_hz', 'sf', 'cr')})]
    for command, changes in operations:
        if all(expected[key] == value for key, value in changes.items()):
            continue
        append(dict(event='setter_intent', opcode=command[0], outcome='unknown_until_verified'))
        response = exchange(command)
        require(response == b'\x00', 'setter result unverified')
        expected.update(changes)
        observed = snapshot(exchange)
        require(observed == expected, 'readback mismatch')
        append(dict(event='setter_verified', opcode=command[0], observed=observed))
    final = snapshot(exchange)
    require(final == expected and all(final[key] == value for key, value in TARGET.items()), 'final readback mismatch')
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--query', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--operator-reviewed-open-close-and-stopped-producers', action='store_true')
    parser.add_argument('--consent')
    args = parser.parse_args(argv)
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root required')
    require(args.apply or args.consent is None, 'unexpected consent')
    if args.query or args.apply:
        require(args.operator_reviewed_open_close_and_stopped_producers, 'operator review required')
    if args.apply:
        require(args.consent == CONSENT, 'apply consent required')
    _, identity = INVENTORY.inspect_usb()
    INVENTORY.no_other_openers(identity[2])
    if args.apply:
        with journal() as append:
            with connection(identity) as exchange:
                result = commission(exchange, append)
            append(dict(event='complete', observed=result, outcome='configuration_readback_verified'))
    elif args.query:
        with connection(identity) as exchange:
            result = snapshot(exchange)
    else:
        result = dict(usb=INVENTORY.USB, queried=False)
    print(json.dumps(dict(schema='sage.lantern.b-radio-review.v1', unit_id='unit-b',
                          mode='apply' if args.apply else 'query' if args.query else 'usb',
                          result=result, rf_delivery_verified=False, radio_silence_verified=False), sort_keys=True))


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
