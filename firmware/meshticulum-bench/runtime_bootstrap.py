# SPDX-License-Identifier: Apache-2.0
"""Inline root bootstrap for a source-only runtime archive. Installs disarmed."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import tarfile
import tempfile
import zipfile
import zlib


def require(condition, reason):
    if not condition: raise RuntimeError(reason)


def deflated_directory(raw, count):
    require(len(raw) >= 22, 'deflated_image_directory')
    signature, disk, central_disk, local_count, total_count, size, start, comment = struct.unpack_from('<4s4H2IH', raw, len(raw)-22)
    require(signature == b'PK\x05\x06' and disk == central_disk == comment == 0
            and local_count == total_count == count and 0 <= size <= 245*count
            and 0 <= start and start+size == len(raw)-22, 'deflated_image_directory')
    return start


def deflated_member(raw, row, cursor, central):
    name = row.filename.encode('ascii')
    require(row.header_offset == cursor and row.create_system == 3 and row.create_version == row.extract_version == 20
            and row.reserved == row.flag_bits == row.volume == row.internal_attr == 0 and not row.extra and not row.comment
            and row.date_time == (1980,1,1,0,0,0) and row.external_attr == 0o100600 << 16
            and row.compress_type == zipfile.ZIP_DEFLATED and 0 <= row.file_size <= 500000
            and 0 < row.compress_size <= 2000000 and cursor+30+len(name) <= central, 'deflated_image_member')
    header = struct.unpack_from('<4s5H3I2H', raw, cursor)
    require(header == (b'PK\x03\x04',20,0,8,0,33,row.CRC,row.compress_size,row.file_size,len(name),0)
            and raw[cursor+30:cursor+30+len(name)] == name, 'deflated_image_header')
    start = cursor+30+len(name); end = start+row.compress_size
    require(end <= central, 'deflated_image_overlap')
    decoder = zlib.decompressobj(-15)
    data = decoder.decompress(raw[start:end], max_length=row.file_size+1)
    require(len(data) == row.file_size and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
            and zlib.crc32(data) == row.CRC, 'deflated_image_stream')
    return data, end


def image_members(raw, manifest):
    """Validate data only. Never execute a loader received in an archive."""
    layout = manifest.get('execution_layout'); sources = manifest.get('source_files')
    compressed = type(layout) is dict and layout.get('schema') == 'sage-lantern.sealed-source-image.v2'
    require(type(layout) is dict and set(layout) == {'schema','image','bytes','sha256','loader','loader_sha256','source_files_sha256'}
            | ({'expanded_bytes'} if compressed else set())
            and layout['schema'] in ('sage-lantern.sealed-source-image.v1','sage-lantern.sealed-source-image.v2')
            and layout['image'] == 'execution.zip'
            and layout['loader'] == 'execution_runtime.py' and type(layout['bytes']) is int
            and 0 < layout['bytes'] <= 2250000 and len(raw) == layout['bytes']
            and hashlib.sha256(raw).hexdigest() == layout['sha256'], 'sealed_runtime_layout_changed')
    require(not compressed or type(layout['expanded_bytes']) is int and 0 < layout['expanded_bytes'] <= 2250000,
            'sealed_runtime_expansion_bound')
    require(type(sources) is dict and 75 <= len(sources) <= 150
            and hashlib.sha256(json.dumps(sources,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                == layout['source_files_sha256'], 'sealed_runtime_source_correspondence_changed')
    central = deflated_directory(raw, len(sources)) if compressed else None
    result = {}; total = 0; cursor = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows = archive.infolist()
        require(len(rows) == len(sources) and {row.filename for row in rows} == set(sources), 'sealed_runtime_member_roster')
        require(not compressed or archive.start_dir == central and [r.filename for r in rows] == sorted(sources)
                and sum(46+len(r.filename.encode('ascii')) for r in rows) == len(raw)-22-central,
                'deflated_image_roster')
        for row in rows:
            path = PurePosixPath(row.filename)
            require(row.filename not in result and not path.is_absolute() and '..' not in path.parts
                    and str(path) == row.filename and not row.is_dir()
                    and row.compress_type == (zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED)
                    and not row.flag_bits & 1 and stat.S_ISREG(row.external_attr >> 16)
                    and not row.filename.endswith(('.pyc','.pyo','.so','.pyd','.dll'))
                    and 0 <= row.file_size <= 500000, 'sealed_runtime_member_type')
            total += row.file_size; require(total <= 2250000, 'sealed_runtime_expansion_limit')
            if compressed:data, cursor = deflated_member(raw, row, cursor, central)
            else:data = archive.read(row)
            require(len(data) == row.file_size and hashlib.sha256(data).hexdigest() == sources[row.filename],
                    'sealed_runtime_member_source_changed')
            result[row.filename] = data
        require(not compressed or cursor == central and total == layout['expanded_bytes'], 'deflated_image_extent')
    return result


def members(data, digest):
    require(type(data) is bytes and 0 < len(data) <= 2000000, 'runtime_archive_input_limit')
    require(hashlib.sha256(data).hexdigest() == digest, 'runtime_archive_hash_mismatch')
    result = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for entry in archive:
            path = PurePosixPath(entry.name)
            require(entry.isfile() and not path.is_absolute() and '..' not in path.parts
                    and str(path) == entry.name and path.parts and len(entry.name) < 200,
                    'invalid_runtime_archive_member')
            maximum = 2250000 if entry.name == 'execution.zip' else 500000
            require(entry.name not in result and len(result) < 128 and 0 <= entry.size <= maximum,
                    'runtime_archive_member_limit')
            total += entry.size
            require(total <= 2500000, 'runtime_archive_total_limit')
            result[entry.name] = archive.extractfile(entry).read(entry.size + 1)
            require(len(result[entry.name]) == entry.size, 'truncated_runtime_member')
    require({'manifest.json', 'bench_node.py'} <= result.keys(), 'incomplete_runtime_archive')
    manifest = json.loads(result['manifest.json'])
    image_layout = manifest.get('schema') == 'meshticulum.bench-runtime-bundle.v2'
    # The sealed image has its own 2.25 MB limit; notices and loader need a
    # separate, bounded allowance. Legacy directory archives stay at 2.25 MB.
    require(total - len(result.get('execution.zip', b'')) <= 500000 if image_layout
            else total <= 2250000, 'runtime_archive_packaging_limit')
    require(manifest['schema'] in ('meshticulum.bench-runtime-bundle.v1', 'meshticulum.bench-runtime-bundle.v2')
            and set(manifest['files']) == set(result) - {'manifest.json'}, 'wrong_runtime_manifest')
    for name, expected in manifest['files'].items():
        require(hashlib.sha256(result[name]).hexdigest() == expected, 'runtime_member_hash_mismatch')
    if not image_layout:
        require(all(len(raw) <= 500000 for raw in result.values()), 'runtime_archive_member_limit')
    if image_layout:
        loader = result.get('execution_runtime.py')
        require(type(loader) is bytes and len(loader) <= 500000, 'sealed_runtime_loader_missing')
        require(hashlib.sha256(loader).hexdigest() == manifest['execution_layout']['loader_sha256'], 'sealed_runtime_loader_pin')
        image_members(result['execution.zip'], manifest)
    return result


def private_root(path, *, private=True):
    path.mkdir(mode=0o700, exist_ok=True)
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'unsafe_runtime_parent')
    if private:
        require(stat.S_IMODE(path.stat().st_mode) == 0o700, 'runtime_directory_not_private')


def read_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
                'unsafe_runtime_source')
        with os.fdopen(fd, 'rb', closefd=False) as source: return source.read(limit + 1)
    finally: os.close(fd)


def main():
    require(os.geteuid() == 0 and sys.flags.isolated and (len(sys.argv) == 4
            or len(sys.argv) == 5 and sys.argv[4] == '--metadata-only'),
            'requires_isolated_root_unit_archive_hash')
    metadata_only = len(sys.argv) == 5
    unit, archive, digest = sys.argv[1:4]
    require(unit in ('unit-a', 'unit-b', 'unit-c') and len(digest) == 64
            and all(c in '0123456789abcdef' for c in digest), 'invalid_runtime_target')
    os.umask(0o077)
    files = members(read_regular(archive, 2000000), digest)
    # Public/private journal consumers require exactly 0700, including when a
    # legacy bench directory already exists. Refuse before installing a bundle;
    # an operator must inspect current owners before migrating its permissions.
    state = Path('/var/lib/meshticulum-bench'); private_root(state)
    root = Path('/opt/meshticulum-bench'); private_root(root, private=False)
    destination = root / ('runtime-' + digest[:16])
    if not destination.exists():
        staging = Path(tempfile.mkdtemp(prefix='.runtime-', dir=root))
        for name, data in files.items():
            path = staging / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as out:
                out.write(data); out.flush(); os.fsync(out.fileno())
        os.rename(staging, destination)
    private_root(destination)
    for name, data in files.items():
        path = destination / name
        private_root(path.parent)
        require(path.lstat().st_uid == 0 and not path.lstat().st_mode & 0o022
                and read_regular(path, 2250000 if name == 'execution.zip' else 500000) == data, 'runtime_installed_bytes_changed')
    for name in ('runs', 'objects'): private_root(state / name)
    executable = '/usr/bin/python3' if unit == 'unit-c' else '/root/esp-venv-r3/bin/python'
    os.chdir(destination)
    action = 'pilot-facts' if metadata_only else 'inspect'
    os.execve(executable, [executable, '-I', '-B', str(destination / 'bench_node.py'), action, unit],
              {'HOME': '/root', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})


if __name__ == '__main__': main()
