# SPDX-License-Identifier: Apache-2.0
"""Opt-in, sealed source-image imports. This supplies no native/RF authority.

The independent launcher verifies this file before loading it. Canonical owned
modules execute only bytes held from the verified image; mutable path finders,
bytecode files and loose copies are never used for those module names.
"""
import hashlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import threading
from types import MappingProxyType, ModuleType
import zipfile
import zlib

SCHEMA = 'meshticulum.bench-runtime-bundle.v2'
LAYOUT = 'sage-lantern.sealed-source-image.v1'
DEFLATED_LAYOUT = 'sage-lantern.sealed-source-image.v2'
IMAGE = 'execution.zip'
LOADER = 'execution_runtime.py'
MODULE = '_lantern_execution_runtime'
MAX_IMAGE = 2_000_000
OWNED = ('meshticulum', 'lantern_meshticulum')
ALIASES = MappingProxyType({
    **{'meshticulum.meshticulum_contracts.' + name: 'meshticulum.contracts.' + name
       for name in ('errors', 'identity', 'carrier', 'negotiation')},
    **{'meshticulum.meshticulum_scheduler.' + name: 'meshticulum.scheduler.' + name
       for name in ('budget', 'scheduler', 'flow_attempt')},
})


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def owned(name):
    return any(name == prefix or name.startswith(prefix + '.') for prefix in OWNED)


def path_name(name):
    path = PurePosixPath(name)
    require(type(name) is str and 0 < len(name) < 200 and not path.is_absolute()
            and '..' not in path.parts and str(path) == name and path.parts, 'unsafe_image_member')
    return path


def manifest_layout(manifest):
    require(type(manifest) is dict and manifest.get('schema') == SCHEMA
            and manifest.get('private_services_enabled') is False
            and manifest.get('public_parent_claims_required') is True, 'sealed_runtime_manifest_required')
    layout = manifest.get('execution_layout')
    compressed = type(layout) is dict and layout.get('schema') == DEFLATED_LAYOUT
    require(type(layout) is dict and set(layout) == {'schema', 'image', 'bytes', 'sha256', 'loader',
            'loader_sha256', 'source_files_sha256'} | ({'expanded_bytes'} if compressed else set())
            and layout['schema'] in (LAYOUT, DEFLATED_LAYOUT)
            and layout['image'] == IMAGE and layout['loader'] == LOADER
            and type(layout['bytes']) is int and 0 < layout['bytes'] <= MAX_IMAGE, 'invalid_execution_layout')
    require(not compressed or type(layout['expanded_bytes']) is int
            and 0 < layout['expanded_bytes'] <= MAX_IMAGE, 'invalid_image_expansion_bound')
    sources = manifest.get('source_files')
    require(type(sources) is dict and 75 <= len(sources) <= 150
            and sha(canonical(sources)) == layout['source_files_sha256'], 'image_source_correspondence_changed')
    for name, pin in sources.items():
        path_name(name)
        require(type(pin) is str and len(pin) == 64 and all(c in '0123456789abcdef' for c in pin), 'invalid_image_member_pin')
        require(not name.endswith(('.pyc', '.pyo', '.so', '.pyd', '.dll')), 'unsupported_image_execution_member')
    require(type(manifest.get('files')) is dict and {'bench_node.py', LOADER, IMAGE} <= set(manifest['files'])
            and manifest['files'][IMAGE] == layout['sha256']
            and manifest['files'][LOADER] == layout['loader_sha256'], 'image_physical_pins_changed')
    return layout, sources


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
            and 0 < row.compress_size <= MAX_IMAGE and cursor+30+len(name) <= central, 'deflated_image_member')
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
    layout, sources = manifest_layout(manifest)
    require(type(raw) is bytes and len(raw) == layout['bytes'] and sha(raw) == layout['sha256'], 'execution_image_changed')
    compressed = layout['schema'] == DEFLATED_LAYOUT
    central = deflated_directory(raw, len(sources)) if compressed else None
    result = {}; cursor = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows = archive.infolist()
        require(len(rows) == len(sources) and {r.filename for r in rows} == set(sources), 'image_member_roster_changed')
        require(not compressed or archive.start_dir == central and [r.filename for r in rows] == sorted(sources)
                and sum(46+len(r.filename.encode('ascii')) for r in rows) == len(raw)-22-central,
                'deflated_image_roster')
        total = 0
        for row in rows:
            path_name(row.filename)
            mode = row.external_attr >> 16
            require(row.filename not in result and not row.is_dir()
                    and row.compress_type == (zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED)
                    and not row.flag_bits & 1 and stat.S_ISREG(mode) and row.file_size <= 500000,
                    'unsafe_image_member_type')
            total += row.file_size
            require(total <= MAX_IMAGE, 'image_expansion_limit')
            if compressed:data, cursor = deflated_member(raw, row, cursor, central)
            else:data = archive.read(row)
            require(len(data) == row.file_size and sha(data) == sources[row.filename], 'image_member_source_changed')
            if row.filename.endswith('.py'):
                compile(data, row.filename, 'exec')
            result[row.filename] = data
        require(not compressed or cursor == central and total == layout['expanded_bytes'], 'deflated_image_extent')
    return MappingProxyType(result)


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Sealed:
    __slots__ = ()
    def __setattr__(self, name, value):
        require(not hasattr(self, name), 'sealed_runtime_state_rebound')
        object.__setattr__(self, name, value)
    def __delattr__(self, name):
        raise RuntimeError('sealed_runtime_state_rebound')


class _Guard(_Sealed):
    """Private, held files with fresh full bytes, named paths and ancestry."""
    __slots__ = ('root', 'owner', 'fds', 'closed', 'chain', 'held')
    def __init__(self, root, files):
        self.root = Path(root).absolute(); self.owner = (os.getpid(), threading.get_ident(), os.geteuid())
        self.closed = False
        chain = []; held = []; fds = []
        try:
            require(self.root.resolve() == self.root and len(self.root.parts) <= 64, 'sealed_runtime_ancestry')
            parent = None
            for name in self.root.parts:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                info = os.fstat(fd); fds.append((fd, stamp(info)[:2], None))
                require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022, 'sealed_runtime_ancestor_writable')
                chain.append((fd, parent, name, stamp(info)[:4])); parent = fd
            require(stat.S_IMODE(os.fstat(parent).st_mode) == 0o700 and os.fstat(parent).st_uid == os.geteuid(), 'sealed_runtime_leaf_not_private')
            for name, expected in files.items():
                require('/' not in name, 'sealed_runtime_guard_name')
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                info = os.fstat(fd); fds.append((fd, stamp(info)[:2], None))
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
                        and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == len(expected), 'sealed_runtime_file_type')
                token = MAX_IMAGE + fd + 1; require(os.lseek(fd, token, os.SEEK_SET) == token, 'sealed_runtime_fd_token')
                fds[-1] = (fd, stamp(info)[:2], token)
                held.append((fd, parent, name, stamp(info), expected, token))
            self.chain, self.held, self.fds = tuple(chain), tuple(held), tuple(fds)
            self.check()
        except BaseException:
            if not hasattr(self, 'fds'): self.fds = tuple(fds)
            self.close()
            raise

    def check(self):
        require(not self.closed and self.owner == (os.getpid(), threading.get_ident(), os.geteuid()), 'sealed_runtime_guard_changed')
        for fd, parent, name, expected in self.chain:
            require(stamp(os.fstat(fd))[:4] == expected == stamp(os.stat(name, dir_fd=parent, follow_symlinks=False))[:4], 'sealed_runtime_ancestry_changed')
        for fd, parent, name, expected, raw, token in self.held:
            require(stamp(os.fstat(fd)) == expected == stamp(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    and os.lseek(fd, 0, os.SEEK_CUR) == token
                    and fcntl.fcntl(fd, fcntl.F_GETFL) & (os.O_ACCMODE | os.O_NONBLOCK) == os.O_RDONLY | os.O_NONBLOCK
                    and fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC, 'sealed_runtime_file_changed')
            current = os.pread(fd, len(raw) + 1, 0)
            require(current == raw and stamp(os.fstat(fd)) == expected
                    == stamp(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    and os.lseek(fd, 0, os.SEEK_CUR) == token, 'sealed_runtime_bytes_changed')
        for fd, parent, name, expected in self.chain:
            require(stamp(os.fstat(fd))[:4] == expected == stamp(os.stat(name, dir_fd=parent, follow_symlinks=False))[:4], 'sealed_runtime_ancestry_changed')
        return True

    def close(self):
        if self.closed or self.owner != (os.getpid(), threading.get_ident(), os.geteuid()):
            return
        object.__setattr__(self, 'closed', True)
        for fd, identity, token in self.fds:
            try:
                if stamp(os.fstat(fd))[:2] == identity and (token is None or os.lseek(fd, 0, os.SEEK_CUR) == token):
                    os.close(fd)
            except OSError:
                pass


class _ModuleLoader(_Sealed):
    __slots__ = ('session', 'name', 'member', 'package', 'origin', '__execute', '__created')
    def __init__(self, session, name, member, package, execute):
        self.session, self.name, self.member, self.package = session, name, member, package
        self.origin = str(session.root / IMAGE / member)
        self.__execute = execute; self.__created = None

    def create_module(self, spec):
        require(self.__created is None and spec.name == self.name and spec.loader is self, 'image_module_creation_changed')
        module = ModuleType(self.name)
        object.__setattr__(self, '_ModuleLoader__created', module)
        return module

    def exec_module(self, module):
        require(module is self.__created and module.__name__ == self.name and module.__spec__.loader is self,
                'image_loader_rebound')
        self.__execute(self, module)

    def get_source(self, fullname):
        require(fullname == self.name, 'image_source_name_changed')
        return self.session.members[self.member].decode('utf-8')


class _Session(_Sealed):
    __slots__ = ('root', 'manifest_raw', 'members', 'guard', 'loaded', '__execute')
    def __init__(self, root, manifest_raw, manifest, members, guard):
        self.root, self.manifest_raw, self.members, self.guard = root, manifest_raw, members, guard
        registry = {}
        self.loaded = MappingProxyType(registry)
        def execute(loader, module):
            require(type(loader) is _ModuleLoader and loader.session is self and loader.member in members
                    and type(module) is ModuleType and module is loader._ModuleLoader__created
                    and module.__spec__.loader is loader and module.__name__ == loader.name,
                    'image_module_registration_changed')
            name = loader.name
            require(name not in registry and set(module.__dict__) <= {
                '__name__','__doc__','__loader__','__package__','__spec__','__path__','__file__','__cached__'},
                'image_module_reload_or_namespace_refused')
            guard.check()
            module.__file__ = loader.origin
            module.__package__ = name if loader.package else name.rpartition('.')[0]
            if loader.package:module.__path__ = [str(root / IMAGE / loader.member.rsplit('/', 1)[0])]
            registry[name] = (module, loader, loader.origin, module.__package__, tuple(getattr(module, '__path__', ())))
            try:
                exec(compile(members[loader.member], loader.origin, 'exec'), module.__dict__)
                guard.check(); self.check_modules()
            except BaseException:
                registry.pop(name, None)
                raise
        self.__execute = execute

    def find_spec(self, fullname, path=None, target=None):
        if not owned(fullname):
            return None
        require(target is None, 'image_module_reload_refused')
        stem = fullname.replace('.', '/')
        package = stem + '/__init__.py'
        member = package if package in self.members else stem + '.py'
        if member not in self.members:
            raise ImportError('owned module absent from sealed image: ' + fullname)
        loader = _ModuleLoader(self, fullname, member, member == package, self.__execute)
        return importlib.util.spec_from_loader(fullname, loader, origin=loader.origin, is_package=loader.package)

    def check_modules(self):
        require(self in sys.meta_path and sys.meta_path[0] is self, 'image_finder_rebound')
        for name, module in tuple(sys.modules.items()):
            if owned(name):
                canonical_name = ALIASES.get(name, name)
                require(canonical_name in self.loaded and self.loaded[canonical_name][0] is module,
                        'mixed_owned_runtime_module')
        for name, (module, loader, origin, package, path) in self.loaded.items():
            require(sys.modules.get(name) is module and type(module) is ModuleType
                    and module.__spec__.name == name and module.__spec__.loader is loader
                    and module.__spec__.origin == origin and module.__file__ == origin
                    and module.__package__ == package and tuple(getattr(module, '__path__', ())) == path,
                    'image_module_origin_changed')
        return True


def _construct(root, manifest_raw, expected_manifest_sha256):
    root = Path(root).absolute()
    require(type(manifest_raw) is bytes and len(manifest_raw) <= 65536
            and sha(manifest_raw) == expected_manifest_sha256, 'image_manifest_pin_changed')
    manifest = json.loads(manifest_raw); layout, sources = manifest_layout(manifest)
    require(not any(owned(name) for name in sys.modules), 'mixed_owned_runtime_module')
    files = {'manifest.json': manifest_raw}
    for name in ('bench_node.py', LOADER, IMAGE):
        fd = os.open(root / name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd); maximum = MAX_IMAGE if name == IMAGE else 500000
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= maximum, 'image_initial_file_type')
            raw = os.pread(fd, maximum + 1, 0)
            require(len(raw) == info.st_size and stamp(os.fstat(fd)) == stamp(info) == stamp((root / name).lstat())
                    and sha(raw) == manifest['files'][name], 'image_initial_bytes_changed')
            files[name] = raw
        finally:
            os.close(fd)
    members = image_members(files[IMAGE], manifest)
    guard = _Guard(root, files)
    session = _Session(root, manifest_raw, manifest, members, guard)
    return session


def _api():
    # Installation identity is held by these original closures, never a module
    # global that a callback can replace with another superficially valid session.
    installed = None
    constructor, session_type = _construct, _Session
    api = None
    def check_api():
        require(api is not None and all(globals().get(name) is function
                for name, function in api.items()), 'sealed_runtime_api_rebound')
    def install(root, manifest_raw, *, expected_manifest_sha256):
        nonlocal installed
        check_api()
        require(installed is None, 'image_runtime_already_installed')
        session = constructor(root, manifest_raw, expected_manifest_sha256)
        require(type(session) is session_type, 'image_installation_type_changed')
        sys.meta_path.insert(0, session); installed = session
        return session
    def active(root, manifest_sha256):
        check_api()
        require(type(installed) is session_type and installed.root == Path(root).absolute()
                and sha(installed.manifest_raw) == manifest_sha256, 'sealed_runtime_not_active')
        installed.check_modules()
        return installed
    def module_root(name):
        check_api()
        require(type(installed) is session_type and name in installed.loaded, 'module_not_from_sealed_runtime')
        installed.check_modules()
        return installed.root
    def member_source(name):
        check_api()
        require(type(installed) is session_type and name in installed.loaded, 'module_not_from_sealed_runtime')
        installed.guard.check(); installed.check_modules()
        loader = installed.loaded[name][1]
        return installed.members[loader.member]
    api = {'install': install, 'active': active, 'module_root': module_root, 'member_source': member_source}
    return install, active, module_root, member_source


install, active, module_root, member_source = _api()
del _api
