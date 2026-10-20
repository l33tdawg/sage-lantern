# SPDX-License-Identifier: Apache-2.0
"""PRIVATE bounded public-cache observation; no authority, writes or transport.

Native entry is fixed to /var/lib/meshticulum-bench/public/<exact manifest>.
The underscored anchor entry exists for real local filesystem controls only.
Results are observations of held bytes and stable names, not admission or a
cross-process snapshot. An externally pinned installed runtime must supply the
Core validation implementation; this helper does not attest its own imports.
"""
import hashlib
import json
import os
import re
import stat

from meshticulum.resources import lamplighter as ll

HEX = re.compile(r'[0-9a-f]{64}\Z')
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
MAX_ENTRIES = 4096


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def names(fd):
    result = []
    with os.scandir(fd) as entries:
        for entry in entries:
            result.append(entry.name)
            require(len(result) <= MAX_ENTRIES, 'directory entry bound exceeded')
    return tuple(sorted(result))


class Held:
    def __init__(self):
        self.items = []

    def open(self, name, parent=None, *, directory=False, private=True, missing=False):
        try:
            fd = os.open(name, FLAGS | (os.O_DIRECTORY if directory else 0), dir_fd=parent)
        except FileNotFoundError:
            if missing:
                return None
            raise
        try:
            info = os.fstat(fd)
            require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
                    'wrong path type')
            require(info.st_uid == os.geteuid(), 'wrong path owner')
            mode = stat.S_IMODE(info.st_mode)
            require(mode == (0o700 if directory else 0o600) if private else mode & 0o022 == 0,
                    'unsafe path mode')
            require(directory or info.st_nlink == 1, 'hardlinked file')
            original = identity(info)
            listed = names(fd) if directory and private else None
            item = [fd, name, parent, original, listed, None]
            self.items.append(item)
            self.check_item(item)
            return fd
        except BaseException:
            if not self.items or self.items[-1][0] != fd:
                os.close(fd)
            raise

    def read(self, name, parent, limit):
        fd = self.open(name, parent)
        item = self.items[-1]
        require(item[3][5] <= limit, 'file exceeds byte bound')
        raw = os.pread(fd, limit + 1, 0)
        require(len(raw) == item[3][5] and len(raw) <= limit, 'file size changed')
        item[5] = sha(raw)
        self.check_item(item)
        return raw

    def check_item(self, item):
        fd, name, parent, original, listed, digest = item
        require(identity(os.fstat(fd)) == original, 'held path changed')
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        require(identity(current) == original, 'named path changed')
        if listed is not None:
            require(names(fd) == listed, 'directory namespace changed')
        if digest is not None:
            require(sha(os.pread(fd, original[5] + 1, 0)) == digest, 'held bytes changed')

    def check(self):
        for item in self.items:
            self.check_item(item)

    def close(self):
        for item in reversed(self.items):
            os.close(item[0])
        self.items.clear()


def _validate(raw, signature, trust_raw, pins, wanted):
    require(type(pins) is dict and set(pins) == {'manifest', 'signature', 'trust'}, 'invalid pins')
    require(all(type(v) is str and HEX.fullmatch(v) for v in pins.values()), 'invalid pin digest')
    for key, value, limit in [('manifest', raw, 65536), ('signature', signature, 4096),
                              ('trust', trust_raw, 16384)]:
        require(type(value) is bytes and len(value) <= limit and sha(value) == pins[key],
                'caller public bytes differ from pin')
    trust = json.loads(trust_raw)
    value = ll.manifest(raw, signature, trust, pins['manifest'])
    require(len(value['chunks']) <= 8, 'too many selected public chunks')
    require(type(wanted) is tuple and 1 <= len(wanted) <= 8 and len(set(wanted)) == len(wanted),
            'invalid wanted chunks')
    entries = {e['sha256']: e for e in value['chunks']}
    require(all(type(d) is str and HEX.fullmatch(d) and d in entries for d in wanted),
            'wanted chunk outside signed manifest')
    return value, trust, entries


def _observe(held, public, raw, signature, trust_raw, pins, wanted, *, _after_read=None):
    value, trust, entries = _validate(raw, signature, trust_raw, pins, wanted)
    target = held.open(pins['manifest'], public, directory=True, missing=True)
    have, absent, files = [], list(wanted), {}
    status = 'target_absent'
    if target is not None:
        require(set(names(target)) == {'trust.json', 'received'}, 'unexpected public target entries')
        observed_trust = held.read('trust.json', target, 16384)
        # pilot_seed stores its validated trust in sorted JSON plus a newline.
        require(observed_trust == json.dumps(trust, sort_keys=True).encode() + b'\n',
                'native trust differs from selected pinned trust')
        received = held.open('received', target, directory=True)
        require(set(names(received)) in ({'manifest.json', 'manifest.sig.json'},
                                        {'manifest.json', 'manifest.sig.json', 'chunks'}),
                'unexpected recipient entries')
        observed_raw = held.read('manifest.json', received, 65536)
        observed_sig = held.read('manifest.sig.json', received, 4096)
        require(observed_raw == raw and observed_sig == signature, 'native manifest/signature differs')
        ll.manifest(observed_raw, observed_sig, trust, pins['manifest'])
        store = held.open('chunks', received, directory=True, missing=True)
        if store is not None:
            require(set(names(store)) <= {d + '.json' for d in entries}, 'unexpected chunk entry')
            for digest in wanted:
                if digest + '.json' not in names(store):
                    continue
                piece = held.read(digest + '.json', store, ll.MAX_CHUNK)
                record = ll.chunk(piece, entries[digest])
                ll.kp.validate_pack_rules(value, [record])
                have.append(digest)
                absent.remove(digest)
                files[digest] = {'bytes': len(piece), 'stored_sha256': sha(piece)}
        status = 'manifest_only' if not have else ('complete' if not absent else 'partial')
    if _after_read is not None:
        _after_read()
    held.check()
    return {'schema': 'meshticulum.private-public-cache-observation.v1', 'status': status,
            'manifest_sha256': pins['manifest'], 'signature_sha256': pins['signature'],
            'trust_sha256': pins['trust'], 'wanted': sorted(wanted),
            'verified_present': sorted(have), 'absent': sorted(absent), 'files': files,
            'native_authority': False, 'dispatch_eligible': False, 'writes': False,
            'radio_opened': False, 'socket_opened': False, 'private_store_opened': False,
            'whole_store_snapshot': False}


def observe_native(raw, signature, trust_raw, pins, wanted):
    """Read only the selected public recipient target as actual root EUID."""
    require(os.geteuid() == 0, 'native observation requires actual root EUID')
    _validate(raw, signature, trust_raw, pins, wanted)
    held = Held()
    try:
        parent = held.open('/', directory=True, private=False)
        for name in ('var', 'lib'):
            parent = held.open(name, parent, directory=True, private=False)
        parent = held.open('meshticulum-bench', parent, directory=True)
        public = held.open('public', parent, directory=True, missing=True)
        if public is None:
            held.check()
            return dict(schema='meshticulum.private-public-cache-observation.v1',
                        status='public_directory_absent', manifest_sha256=pins['manifest'],
                        wanted=sorted(wanted), absent=sorted(wanted), verified_present=[],
                        native_authority=False, dispatch_eligible=False, writes=False,
                        radio_opened=False, socket_opened=False, private_store_opened=False,
                        whole_store_snapshot=False)
        return _observe(held, public, raw, signature, trust_raw, pins, wanted)
    finally:
        held.close()


def _observe_local_anchor(anchor, raw, signature, trust_raw, pins, wanted, *, _after_read=None):
    """TEST ONLY: an independently created local 0700 directory, never native evidence."""
    require(os.path.isabs(os.fspath(anchor)), 'local anchor must be absolute')
    held = Held()
    try:
        public = held.open(os.fspath(anchor), directory=True)
        result = _observe(held, public, raw, signature, trust_raw, pins, wanted,
                          _after_read=_after_read)
        result['local_control_only'] = True
        return result
    finally:
        held.close()
