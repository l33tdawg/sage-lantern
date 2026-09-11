"""B-only, two-pass 1MiB flash backup. Never flashes or erases a device."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


CHUNK = 1048576
TOTAL = 16777216
SERIALS = {'audio': '28:84:85:B2:B9:B0', 'radio': '44:1B:F6:62:56:54'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stable_path(kind):
    return '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_' + SERIALS[kind] + '-if00'


def verify_device(kind):
    stable = Path(stable_path(kind))
    require(stable.is_symlink(), 'missing stable device')
    target = stable.resolve(strict=True)
    require(target.parent == Path('/dev') and re.fullmatch(r'ttyACM[0-9]+', target.name), 'unexpected tty')
    info = target.stat()
    require(stat.S_ISCHR(info.st_mode), 'not a serial device')
    device = Path(f'/sys/dev/char/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}/device').resolve(strict=True)
    for parent in (device, *device.parents):
        if (parent / 'idVendor').exists():
            observed = [(parent / name).read_text().strip() for name in ('idVendor', 'idProduct', 'serial')]
            require(observed == ['303a', '1001', SERIALS[kind]], 'USB identity mismatch')
            break
    else:
        raise RuntimeError('USB identity unavailable')
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / 'fd').iterdir())
        except FileNotFoundError:
            continue
        for descriptor in descriptors:
            try:
                opened = descriptor.stat()
            except FileNotFoundError:
                continue
            require(not (stat.S_ISCHR(opened.st_mode) and opened.st_rdev == info.st_rdev), 'serial already owned')
    return dict(serial=SERIALS[kind], stable_path=str(stable), vendor='303a', product='1001')


def new_file(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(descriptor, 'wb')


def read_chunk(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == CHUNK, 'invalid chunk file')
        data = stream.read(CHUNK + 1)
        require(len(data) == CHUNK, 'incomplete chunk')
        return data


def command(python, kind, offset, destination, rom):
    require(offset in range(0, TOTAL, CHUNK), 'invalid span')
    args = [str(python), '-B', '-m', 'esptool', '--chip', 'esp32s3', '--port', stable_path(kind),
            '--baud', '115200', '--after', 'no-reset']
    if rom:
        args.append('--no-stub')
    return args + ['read-flash', '--flash-size', '16MB', hex(offset), hex(CHUNK), str(destination)]


def backup(directory, python, kind, rom=False, observer=verify_device, runner=subprocess.run):
    expected = observer(kind)
    chunks = []
    for offset in range(0, TOTAL, CHUNK):
        paths = []
        for attempt in ('first', 'second'):
            require(observer(kind) == expected, 'device changed')
            destination = directory / f'{offset:08x}-{attempt}.bin'
            require(not os.path.lexists(destination), 'destination exists')
            with new_file(directory / f'{offset:08x}-{attempt}.log') as log:
                result = runner(command(python, kind, offset, destination, rom), stdout=log,
                                stderr=subprocess.STDOUT, timeout=1800, check=False)
                log.flush()
                os.fsync(log.fileno())
            require(result.returncode == 0, 'read failed; partials retained, no retry')
            require(observer(kind) == expected, 'device changed after read')
            read_chunk(destination)
            paths.append(destination)
        first, second = (read_chunk(path) for path in paths)
        require(first == second, 'chunk mismatch; all files retained')
        chunks.append(dict(offset=offset, size=CHUNK, sha256=hashlib.sha256(first).hexdigest(),
                           first=paths[0].name, second=paths[1].name))
    require(len(chunks) * CHUNK == TOTAL, 'incomplete coverage')
    pending = directory / 'assembled.pending'
    digest = hashlib.sha256()
    with new_file(pending) as assembled:
        for item in chunks:
            data = read_chunk(directory / item['first'])
            require(data == read_chunk(directory / item['second'])
                    and hashlib.sha256(data).hexdigest() == item['sha256'], 'chunk changed')
            assembled.write(data)
            digest.update(data)
        assembled.flush()
        os.fsync(assembled.fileno())
    require(pending.stat().st_size == TOTAL, 'incorrect full backup size')
    final = directory / 'factory-full-16MiB.bin'
    os.link(pending, final, follow_symlinks=False)
    pending.unlink()
    receipt = dict(schema='sage.lantern.b-factory-backup.v1', usb=expected, bytes=TOTAL,
                   sha256=digest.hexdigest(), chunks=chunks, rom=rom, passes=2)
    with new_file(directory / 'complete.json') as output:
        output.write((json.dumps(receipt, sort_keys=True) + '\n').encode())
        output.flush()
        os.fsync(output.fileno())
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=SERIALS, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rom', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(args.python.is_absolute() and args.output.is_absolute(), 'absolute paths required')
    require(not os.path.lexists(args.output), 'output already exists; choose a new directory')
    if not args.apply:
        print(json.dumps(dict(device=args.device, stable_path=stable_path(args.device), reads=32,
                              chunk_bytes=CHUNK, total_bytes=TOTAL, rom=args.rom, applied=False)))
        return
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Linux root required')
    for parent in (args.output.parent, *args.output.parent.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'unsafe output ancestor')
    require(args.python.is_file(), 'missing isolated Python')
    version = subprocess.run([str(args.python), '-B', '-m', 'esptool', 'version'],
                             capture_output=True, text=True, timeout=30, check=True)
    require(re.search(r'\besptool v5\.3\.1\b', version.stdout), 'wrong esptool version')
    os.umask(0o077)
    lock_path = '/run/lantern-b-backup-' + SERIALS[args.device].replace(':', '').lower() + '.lock'
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, 'wb') as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_device(args.device)
        args.output.mkdir(mode=0o700)
        result = backup(args.output, args.python, args.device, args.rom)
    print(json.dumps(dict(bytes=result['bytes'], sha256=result['sha256'], complete=True)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Backup refused/incomplete: {error}. Preserve attempt directory; never flash from partials.', file=sys.stderr)
        sys.exit(1)
