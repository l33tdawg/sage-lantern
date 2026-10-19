#!/usr/bin/env python3
"""Freeze a disabled BPF source/object review; never load or install policy."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

HOOKS = frozenset(('file_open', 'file_receive', 'file_permission', 'file_ioctl',
    'mmap_file', 'file_mprotect', 'ptrace_access_check', 'task_kill', 'task_alloc',
    'bprm_check_security', 'task_fix_setuid', 'capable', 'bpf'))
FLAGS = ('installed_on_c', 'kernel_enforcement_qualified', 'alternate_launch_closed',
         'deployable', 'desktop_pin_accepted')
ROOT = Path(__file__).resolve().parents[2]


def digest(body):
    return hashlib.sha256(body).hexdigest()


def object_sections(body):
    """Bounded ELF inspection proves structure, never verifier acceptance."""
    if (len(body) < 64 or len(body) > 16 * 1024 * 1024
            or body[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<HH', body, 16) != (1, 247)):
        raise ValueError('not_elf64_bpf_object')
    start = struct.unpack_from('<Q', body, 40)[0]
    size, count, names = struct.unpack_from('<HHH', body, 58)
    if (size != 64 or not 1 <= count <= 1024 or names >= count
            or start < 64 or start + count * size > len(body)):
        raise ValueError('invalid_section_table')
    records = [struct.unpack_from('<IIQQQQIIQQ', body, start + i * size) for i in range(count)]
    strings = records[names]
    if strings[1] != 3 or strings[4] + strings[5] > len(body):
        raise ValueError('invalid_section_names')
    table = body[strings[4]:strings[4] + strings[5]]
    result = {}
    for record in records[1:]:
        name_at, kind, flags, _, offset, length, *_ = record
        if name_at >= len(table) or table.find(b'\0', name_at) < 0:
            raise ValueError('invalid_section_name')
        name = table[name_at:table.find(b'\0', name_at)].decode('ascii')
        if not name or name in result or (kind != 8 and offset + length > len(body)):
            raise ValueError('invalid_section')
        result[name] = dict(type=kind, flags=flags, size=length)
    sections = {name for name in result if name.startswith('lsm/')}
    if sections != {'lsm/' + hook for hook in HOOKS}:
        raise ValueError('hook_set_mismatch')
    if any(result[name]['type'] != 1 or not result[name]['size']
           or not result[name]['flags'] & 4 for name in sections):
        raise ValueError('hook_code_missing')
    if any(name not in result or not result[name]['size']
           for name in ('.BTF', '.BTF.ext', '.maps', 'license')):
        raise ValueError('core_metadata_missing')
    return sorted(sections)


def stage(build, output):
    if output.exists():
        raise FileExistsError(output)
    source = build / 'build-source'
    hashes = json.loads((source / 'source-sha256.json').read_text())
    if set(hashes) != {'desktop_guard.bpf.c', 'desktop_guard_policy.h'}:
        raise ValueError('source_set_mismatch')
    for name, expected in hashes.items():
        if digest((source / name).read_bytes()) != expected:
            raise ValueError('source_digest_mismatch')
    platform = json.loads((build / 'bpf-platform.json').read_text())
    features = json.loads((build / 'platform.json').read_text())
    btf = (build / 'kernel.btf').read_bytes()
    hook_types = json.loads((build / 'kernel-hook-types.json').read_text())
    if (platform.get('btf_size') != len(btf) or not btf
            or platform.get('kernel_release') != features.get('kernel_release')
            or hook_types.get('kernel_btf_sha256') != digest(btf)
            or set(hook_types.get('hooks', {})) != HOOKS
            or hook_types.get('kernel_enforcement_qualified') is not False
            or platform.get('live_changes') != []
            or features.get('live_changes') != []
            or 'bpf' not in features.get('lsm', '').split(',')
            or any(platform.get('config', {}).get(key) != key + '=y'
                   for key in ('CONFIG_BPF_LSM', 'CONFIG_DEBUG_INFO_BTF'))):
        raise ValueError('platform_preparation_incomplete')
    body = (build / 'desktop_guard.bpf.o').read_bytes()
    sections = object_sections(body)
    payloads = {
        'object/desktop_guard.bpf.o': body,
        **{'source/software/security/' + name: (source / name).read_bytes() for name in hashes},
        **{'evidence/' + name: (build / name).read_bytes() for name in
           ('platform.json', 'bpf-platform.json', 'build-tools.txt', 'object-sections.txt',
            'kernel-hook-types.json', 'hook-replay.txt', 'python-tests.txt',
            'javascript-tests.txt', 'build-source/source-sha256.json')},
        **{'source/' + name: (ROOT / name).read_bytes() for name in
           ('scripts/lantern/stage-desktop-confinement.py',
            'scripts/tests/test_desktop_guard_replay.py',
            'scripts/tests/test_stage_desktop_confinement.py',
            'scripts/tests/native/desktop_guard_replay.c',
            'scripts/tests/native/desktop_guard_replay.h',
            'docs/lantern/DESKTOP_CONFINEMENT_CANDIDATE.md')},
    }
    manifest = dict(schema='sage.lantern.desktop-confinement-review.v1',
        **{key: False for key in FLAGS},
        kernel_release=platform['kernel_release'],
        kernel_btf_sha256=digest(btf),
        uid_boundary=1000, program_sections=sections,
        enrollment='root pidfd update of leader task storage; future launcher handoff required',
        files={name: digest(body) for name, body in sorted(payloads.items())},
        unresolved=['complete root loader/link/map ownership and cancellation contract',
            'exact-child stopped-before-exec pidfd enrollment and map freeze',
            'vendor kernel verifier acceptance and live deny/allow probes',
            'all UID 1000 entry paths start after enforcement; no old mappings',
            'root-controlled GDM, user-manager and GNOME search paths',
            'trusted compositor D-Bus/configuration/code injection review',
            'GNOME app-launch compatibility without compositor fork/reexec',
            'physical qualified-power, fresh-session, seat and whole-desktop acceptance'])
    output.mkdir(parents=True)
    for name, payload in payloads.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def verify(directory):
    directory = directory.resolve(strict=True)
    value = json.loads((directory / 'manifest.json').read_text())
    if (value.get('schema') != 'sage.lantern.desktop-confinement-review.v1'
            or any(value.get(flag) is not False for flag in FLAGS)):
        raise ValueError('review_flags_invalid')
    if type(value.get('files')) is not dict:
        raise ValueError('files_invalid')
    for name, expected in value['files'].items():
        relative = Path(name)
        path = directory / relative
        if (relative.is_absolute() or '..' in relative.parts or str(relative) != name
                or name == 'manifest.json' or path.is_symlink()
                or any(parent.is_symlink() for parent in path.parents)
                or digest(path.read_bytes()) != expected):
            raise ValueError('payload_invalid')
    actual = {str(path.relative_to(directory)) for path in directory.rglob('*') if path.is_file()}
    if actual != set(value['files']) | {'manifest.json'}:
        raise ValueError('payload_set_mismatch')
    sections = object_sections((directory / 'object/desktop_guard.bpf.o').read_bytes())
    if value.get('program_sections') != sections or value.get('uid_boundary') != 1000:
        raise ValueError('object_contract_mismatch')
    return len(value['files'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args(argv)
    if args.verify is not None:
        print(json.dumps(dict(verified_files=verify(args.verify), kernel_enforcement_qualified=False)))
    elif args.build is not None and args.output is not None:
        result = stage(args.build, args.output)
        print(json.dumps(dict(files=len(result['files']), kernel_enforcement_qualified=False)))
    else:
        parser.error('use --verify or both --build and --output')


if __name__ == '__main__':
    main()
