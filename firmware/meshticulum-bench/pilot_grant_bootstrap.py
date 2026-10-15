# SPDX-License-Identifier: Apache-2.0
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Prepare one hash-pinned finite public pilot child. Opens no socket or radio."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types
import tempfile
import time


def need(ok, message):
    if not ok: raise RuntimeError(message)


def read(path, maximum, *, root=False, public_seed=False):
    ancestry=[]
    identity=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid)
    if public_seed:
        need(path.is_absolute() and path.resolve()==path and path.parent.name=='meshticulum-bench-staging',
             'unscoped_public_seed_source')
        parent=path.parent.lstat()
        need(stat.S_ISDIR(parent.st_mode) and stat.S_IMODE(parent.st_mode)==0o700,'unprotected_public_seed_source')
        ancestry.append((path.parent,identity(parent)))
        for ancestor in path.parent.parents:
            info=ancestor.lstat()
            need(stat.S_ISDIR(info.st_mode) and not info.st_mode&0o022 and ancestor.resolve()==ancestor,
                 'unsafe_public_seed_ancestry')
            ancestry.append((ancestor,identity(info)))
    if root:
        for parent in (path.parent, *path.parent.parents):
            info = parent.lstat()
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                 'unsafe_grant_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum
             and (not root or info.st_uid == 0 and not info.st_mode & 0o022), 'unsafe_grant_file')
        if public_seed:
            need(stat.S_IMODE(info.st_mode)==0o600 and info.st_uid==parent.st_uid,'unprotected_public_seed_file')
        with os.fdopen(fd, 'rb', closefd=False) as inp:raw=inp.read(maximum + 1)
        stamp=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        need(len(raw)==info.st_size and stamp(os.fstat(fd))==stamp(path.lstat())==stamp(info),
             'source_package_changed')
        need(all(identity(parent.lstat())==saved and parent.resolve()==parent for parent,saved in ancestry),
             'public_seed_ancestry_changed')
        return raw
    finally: os.close(fd)


def load_package(source,digest,*,public_seed=False):
    need(type(public_seed) is bool,'invalid_public_seed_selection')
    path=Path(source)
    if public_seed:need(path.name=='public-seed-'+digest+'.json','wrong_public_seed_source_name')
    raw=read(path,700000 if public_seed else 100000,public_seed=public_seed)
    need(hashlib.sha256(raw).hexdigest()==digest,'pilot_package_hash_mismatch')
    package=json.loads(raw)
    need(not public_seed or package.get('schema')=='meshticulum.pilot-public-seed.v1','large_public_seed_schema_required')
    return package


def main():
    need(os.geteuid() == 0 and sys.flags.isolated and len(sys.argv) in (6,7),
         'expected_isolated_root_unit_package_hash_runtime_manifest')
    public_seed=len(sys.argv)==7
    need(not public_seed or sys.argv[-1]=='--public-seed','invalid_public_seed_selection')
    unit, source, digest, runtime_digest, manifest_digest = sys.argv[1:6]
    for value in (digest, runtime_digest, manifest_digest):
        need(len(value) == 64 and all(c in '0123456789abcdef' for c in value), 'invalid_pin')
    need(unit in ('unit-a', 'unit-b', 'unit-c'), 'invalid_unit')
    os.umask(0o077)
    package=load_package(source,digest,public_seed=public_seed)
    bundle = Path('/opt/meshticulum-bench') / ('runtime-' + runtime_digest[:16])
    manifest_raw = read(bundle / 'manifest.json', 20000, root=True)
    need(hashlib.sha256(manifest_raw).hexdigest() == manifest_digest, 'runtime_manifest_changed')
    manifest = json.loads(manifest_raw)
    need(manifest.get('schema') in ('meshticulum.bench-runtime-bundle.v1','meshticulum.bench-runtime-bundle.v2')
         and manifest.get('private_services_enabled') is False
         and manifest.get('public_parent_claims_required') is True
         and manifest.get('public_ip_service_formats') == ['MR1.request', 'MR1.response'],
         'public_pilot_not_in_bundle')
    for name, expected in manifest['files'].items():
        part = Path(name)
        need(not part.is_absolute() and '..' not in part.parts, 'invalid_runtime_path')
        maximum=2250000 if manifest.get('schema')=='meshticulum.bench-runtime-bundle.v2' and name=='execution.zip' else 500000
        need(hashlib.sha256(read(bundle / part, maximum, root=True)).hexdigest() == expected,
             'runtime_source_changed')
    if manifest.get('schema')=='meshticulum.bench-runtime-bundle.v2':
        hraw=read(bundle/'execution_runtime.py',500000,root=True)
        need(hashlib.sha256(hraw).hexdigest()==manifest['execution_layout']['loader_sha256'],'image_loader_pin')
        need('_lantern_execution_runtime' not in sys.modules,'image_loader_duplicate')
        helper=types.ModuleType('_lantern_execution_runtime');helper.__file__=str(bundle/'execution_runtime.py')
        sys.modules[helper.__name__]=helper;exec(compile(hraw,helper.__file__,'exec'),helper.__dict__)
        helper.install(bundle,manifest_raw,expected_manifest_sha256=manifest_digest)
    else:sys.path.insert(0, str(bundle))
    from lantern_meshticulum.ip.node import _facts, clock
    facts = _facts(unit)
    if package.get('schema') == 'meshticulum.pilot-public-seed.v1':
        from lantern_meshticulum.bench.pilot_seed import install
        result = install(package, state='/var/lib/meshticulum-bench', facts=facts)
    else:
        from lantern_meshticulum.bench.pilot_provision import provision
        result = provision(package, state='/var/lib/meshticulum-bench', facts=facts, clock=clock)
    need(_facts(unit) == facts, 'native_pilot_lifecycle_changed')
    result['package_sha256'] = digest
    result['bundle_manifest_sha256'] = manifest_digest
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__': main()
