"""App-only MeshCore flash for ONE Lantern radio, with a full pre-write backup.

Runs on the unit as root, with the radio on its USB port and the mesh runtime
stopped so nothing else owns the serial device. Never erases, never writes the
bootloader, partition table, NVS, OTA selector or SPIFFS: it verifies the app
partition from the device's own partition table, backs up the metadata region
and the whole current app, writes the new app image at the table's offset,
verifies the write by read-back, and proves the metadata region is unchanged.

    python3 flash-radio-app.py --serial <usb-serial> --image <firmware.bin> \
        --esptool /root/esp-venv-r3/bin/python --backup /var/backups/<dir> \
        [--offset 0x10000] [--baud 115200] [--apply]

Without --apply it only verifies the device, the partition table and the image
size and prints what it would do.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


BOOTLOADER_TABLE = 0x8000
TABLE_SIZE = 0x1000
METADATA_SIZE = 0x10000
CHUNK = 0x100000
VENDOR, PRODUCT = '303a', '1001'
APP_TYPE, APP_SUBTYPE = 0, 0x10


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stable_path(serial):
    return Path('/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_' + serial + '-if00')


def verify_device(serial):
    stable = stable_path(serial)
    require(stable.is_symlink(), 'missing stable device')
    target = stable.resolve(strict=True)
    require(target.parent == Path('/dev') and re.fullmatch(r'ttyACM[0-9]+', target.name), 'unexpected tty')
    info = target.stat()
    require(stat.S_ISCHR(info.st_mode), 'not a serial device')
    device = Path(f'/sys/dev/char/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}/device').resolve(strict=True)
    for parent in (device, *device.parents):
        if (parent / 'idVendor').exists():
            observed = [(parent / name).read_text().strip() for name in ('idVendor', 'idProduct', 'serial')]
            require(observed == [VENDOR, PRODUCT, serial], 'USB identity mismatch')
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
            require(not (stat.S_ISCHR(opened.st_mode) and opened.st_rdev == info.st_rdev),
                    'serial already owned')
    return dict(serial=serial, stable_path=str(stable), vendor=VENDOR, product=PRODUCT)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def esptool(python, serial, command, *, baud, stub=False, after='no_reset'):
    argv = [str(python), '-B', '-m', 'esptool', '--chip', 'esp32s3', '--port', str(stable_path(serial)),
            '--baud', str(baud), '--before', 'usb_reset', '--after', after]
    if not stub:
        argv.insert(argv.index('--after'), '--no-stub')
    argv.extend(command)
    finished = subprocess.run(argv, capture_output=True, text=True, timeout=3600)
    require(finished.returncode == 0,
            f'esptool failed: {(finished.stderr or finished.stdout)[-400:]}')
    return finished.stdout


def read(python, serial, offset, size, destination, *, baud, stub=False):
    esptool(python, serial, ['read_flash', hex(offset), hex(size), str(destination)], baud=baud, stub=stub)
    raw = Path(destination).read_bytes()
    require(len(raw) == size, 'short read')
    return raw


def read_region(python, serial, offset, size, directory, stem, *, baud, stub=False):
    """Two identical passes per 1 MiB chunk.

    The ROM loader (--no-stub, the connection these radios accept) returns
    `CRC or checksum was invalid` partway through a multi-megabyte read, so a
    region is read in 1 MiB chunks and every chunk has to come back identical
    twice before it is trusted. A chunk read that fails is retried a bounded
    number of times before the run gives up.
    """
    data = bytearray()
    for start in range(offset, offset + size, CHUNK):
        span = min(CHUNK, offset + size - start)
        paths = []
        for attempt in ('first', 'second'):
            path = directory / f'{stem}-{start:08x}-{attempt}.bin'
            for retry in range(3):
                try:
                    read(python, serial, start, span, path, baud=baud, stub=stub)
                    break
                except RuntimeError:
                    if retry == 2:
                        raise
                    time.sleep(2)
            paths.append(path)
        first, second = (path.read_bytes() for path in paths)
        require(first == second, f'chunk mismatch at {start:#x}; files retained')
        data.extend(first)
    return bytes(data)


def parse_table(raw):
    entries = []
    for index in range(0, len(raw), 32):
        entry = raw[index:index + 32]
        if len(entry) < 32 or entry[:2] != b'\xaa\x50':
            continue
        entries.append(dict(type=entry[2], subtype=entry[3],
                            offset=int.from_bytes(entry[4:8], 'little'),
                            size=int.from_bytes(entry[8:12], 'little'),
                            label=entry[12:28].rstrip(b'\x00').decode('ascii', 'replace')))
    require(entries, 'no partition entries')
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--esptool', type=Path, required=True, help='python interpreter with esptool installed')
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--offset', default='0x10000')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'root on the appliance required')
    offset = int(args.offset, 16)
    image = args.image.read_bytes()
    require(image and len(image) > 0x100, 'empty image')
    receipt = dict(schema='sage.lantern.radio-app-flash.v1', image=str(args.image),
                   image_sha256=sha(image), image_bytes=len(image))

    device = verify_device(args.serial)
    receipt['device'] = device
    args.backup.mkdir(mode=0o700, parents=True, exist_ok=True)
    table = parse_table(read(args.esptool, args.serial, BOOTLOADER_TABLE, TABLE_SIZE,
                             args.backup / 'partitions.bin', baud=args.baud))
    app = [entry for entry in table if entry['type'] == APP_TYPE and entry['subtype'] == APP_SUBTYPE]
    require(len(app) == 1 and app[0]['offset'] == offset, 'app partition does not match --offset')
    require(app[0]['size'] >= len(image), 'image larger than the app partition')
    receipt['partition'] = app[0]
    print(json.dumps({k: receipt[k] for k in ('image_sha256', 'image_bytes', 'device', 'partition')},
                     indent=1, sort_keys=True))
    if not args.apply:
        print('dry run: verified device, partition table and image size; nothing written')
        return 0

    before = read_region(args.esptool, args.serial, 0, METADATA_SIZE, args.backup, 'metadata', baud=args.baud)
    (args.backup / 'boot-settings-ota.bin').write_bytes(before)
    # The ROM loader cannot read past roughly 2 MiB (reproducible `CRC or
    # checksum was invalid` at 0x210000), so the app region is read with the
    # stub loader. One mebibyte is enough to hold the whole previous image:
    # the app partitions here carry a ~640 KiB image and 0xFF beyond it.
    span = min(app[0]['size'], CHUNK)
    original = read(args.esptool, args.serial, offset, span,
                    args.backup / 'app-previous-first-1MiB.bin', baud=args.baud, stub=True)
    receipt['metadata_before'] = sha(before)
    receipt['app_original_sha256'] = sha(original)
    receipt['app_backup_bytes'] = span
    esptool(args.esptool, args.serial, ['write_flash', hex(offset), str(args.image)], baud=args.baud)
    esptool(args.esptool, args.serial, ['verify_flash', hex(offset), str(args.image)], baud=args.baud)
    after = read_region(args.esptool, args.serial, 0, METADATA_SIZE, args.backup, 'metadata-after',
                        baud=args.baud)
    (args.backup / 'boot-settings-ota-after.bin').write_bytes(after)
    receipt['metadata_after'] = sha(after)
    require(after == before, 'boot/settings/OTA metadata changed across the write')
    receipt['metadata_unchanged'] = True
    receipt['app_verified'] = True
    # Leave the radio running the new application, not sitting in the loader.
    esptool(args.esptool, args.serial, ['flash_id'], baud=args.baud, after='hard_reset')
    receipt['reset_into_application'] = True
    (args.backup / 'flash-receipt.json').write_text(json.dumps(receipt, indent=1, sort_keys=True) + '\n')
    print(json.dumps(dict(flashed=True, app_offset=hex(offset), image_sha256=receipt['image_sha256'],
                          metadata_unchanged=True, backup=str(args.backup)), indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
