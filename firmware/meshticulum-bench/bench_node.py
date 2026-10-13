#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Hash-verified root launcher for explicitly provisioned, finite bench runs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


def trusted(path, limit=2000000):
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not (stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022):
            raise RuntimeError('untrusted_bundle_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= limit):
            raise RuntimeError('untrusted_bundle_file')
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'run', 'service-run', 'ip-service-run', 'pilot-facts', 'pilot-debt-snapshot', 'pilot-parent-snapshot', 'pilot-cache-status', 'pilot-result', 'pilot-status', 'pilot-stop', 'pilot-retire', 'pilot-verify', 'pilot-bundle-status', 'pilot-bundle-finish', 'revoke', 'ip-revoke'))
    parser.add_argument('unit', choices=('unit-a', 'unit-b', 'unit-c'))
    parser.add_argument('--run-id')
    parser.add_argument('--campaign-id')
    parser.add_argument('--parent-id')
    parser.add_argument('--request-run-id')
    parser.add_argument('--test-rx-cutoff',type=int)
    parser.add_argument('--sender-receive-ready',action='store_true')
    parser.add_argument('--native-phase-timing',action='store_true')
    parser.add_argument('--scope-file',type=Path)
    parser.add_argument('--scope-sha256')
    parser.add_argument('--expected-boot')
    parser.add_argument('--observation-end-epoch-us',type=int)
    args = parser.parse_args()
    if args.sender_receive_ready and args.action!='service-run':
        raise RuntimeError('sender_receive_ready_requires_radio_service')
    if args.native_phase_timing and args.action!='service-run':
        raise RuntimeError('native_phase_timing_requires_radio_service')
    if os.geteuid() != 0: raise RuntimeError('requires_root')
    bundle = Path(__file__).resolve().parent
    manifest_bytes = trusted(bundle / 'manifest.json')
    manifest = json.loads(manifest_bytes)
    if manifest['schema'] != 'meshticulum.bench-runtime-bundle.v1':
        raise RuntimeError('wrong_runtime_bundle')
    for name, digest in manifest['files'].items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise RuntimeError('unsafe_runtime_member')
        if hashlib.sha256(trusted(bundle / path)).hexdigest() != digest:
            raise RuntimeError('runtime_source_drift')
    sys.path.insert(0, str(bundle))
    from lantern_meshticulum.bench import node; from meshticulum.bench import authority
    if args.action == 'inspect':
        if manifest.get('radio_profile_name')=='gfsk250' and args.unit=='unit-a':
            result=node.inspect(args.unit,radio_modem='gfsk250')
        else:result = node.inspect(args.unit)
        result['schema'] = 'meshticulum.native-bench-inspection.v1'
    elif args.action == 'pilot-facts':
        from lantern_meshticulum.bench.pilot_manage import facts
        result = facts(args.unit)
    elif args.action == 'pilot-debt-snapshot':
        if manifest.get('public_debt_snapshot_schemas') != ['meshticulum.public-debt-scope.v1']:
            raise RuntimeError('debt_snapshot_not_in_bundle')
        from lantern_meshticulum.bench.pilot_debt_snapshot import Scope, collect
        from lantern_meshticulum.bench.pilot_manage import facts
        from lantern_meshticulum.ip.node import clock
        if (args.scope_file is None or args.expected_boot is None
                or args.observation_end_epoch_us is None
                or args.scope_sha256 is None or not re.fullmatch(r'[0-9a-f]{64}',args.scope_sha256)):
            raise RuntimeError('debt_snapshot_explicit_scope_required')
        raw = trusted(args.scope_file,200000)
        if hashlib.sha256(raw).hexdigest() != args.scope_sha256:
            raise RuntimeError('debt_snapshot_scope_changed')
        scope = Scope.from_dict(json.loads(raw))
        before = facts(args.unit)
        if (scope.unit != args.unit or scope.current_host_boot_id != args.expected_boot
                or before['host_boot_id'] != args.expected_boot
                or bundle.name != 'runtime-' + scope.runtime_sha256[:16]
                or scope.manifest_sha256 != hashlib.sha256(manifest_bytes).hexdigest()
                or scope.observation_expires_epoch_us != args.observation_end_epoch_us
                or clock()[0] >= args.observation_end_epoch_us):
            raise RuntimeError('debt_snapshot_scope_or_native_facts_changed')
        result = collect(Path('/var/lib/meshticulum-bench'),scope,clock=clock,
            boot_reader=lambda:Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        after = facts(args.unit)
        stable = lambda value:{k:v for k,v in value.items() if k not in ('epoch_us','monotonic_us')}
        if stable(before) != stable(after) or clock()[0] >= args.observation_end_epoch_us:
            raise RuntimeError('debt_snapshot_native_lifecycle_changed')
        result['facts_before'],result['facts_after'] = before,after
        result['declared_runtime_archive_sha256'] = scope.runtime_sha256
        result['runtime_archive_sha256_observed'] = False
        result['runtime_manifest_and_payloads_verified'] = True
    elif args.action == 'pilot-parent-snapshot':
        if manifest.get('public_parent_snapshot_schemas') != ['meshticulum.public-parent-observation.v1']:
            raise RuntimeError('parent_snapshot_not_in_bundle')
        from lantern_meshticulum.bench.pilot_parent_snapshot import Scope, collect, public_json
        from lantern_meshticulum.bench.pilot_debt_snapshot import Scope as DebtScope
        from lantern_meshticulum.bench.pilot_manage import facts
        from lantern_meshticulum.ip.node import clock
        if (args.scope_file is None or args.expected_boot is None
                or args.observation_end_epoch_us is None or args.scope_sha256 is None
                or not re.fullmatch(r'[0-9a-f]{64}',args.scope_sha256)):
            raise RuntimeError('parent_snapshot_explicit_scope_required')
        raw = trusted(args.scope_file,200000)
        if hashlib.sha256(raw).hexdigest() != args.scope_sha256:
            raise RuntimeError('parent_snapshot_scope_changed')
        wrapper = public_json(raw)
        if (type(wrapper) is not dict or set(wrapper) != {'schema','parent_scope','legacy_scope',
                'runtime_sha256','manifest_sha256'}
                or wrapper['schema'] != 'meshticulum.public-parent-observation.v1'
                or any(type(wrapper[k]) is not str or not re.fullmatch(r'[0-9a-f]{64}',wrapper[k])
                       for k in ('runtime_sha256','manifest_sha256'))):
            raise RuntimeError('parent_snapshot_invalid_wrapper')
        scope = Scope.from_dict(wrapper['parent_scope'])
        legacy = None if wrapper['legacy_scope'] is None else DebtScope.from_dict(wrapper['legacy_scope'])
        before = facts(args.unit)
        if (scope.role != 'native' or scope.owner_uid != 0 or scope.unit != args.unit
                or scope.current_host_boot_id != args.expected_boot
                or before['host_boot_id'] != args.expected_boot
                or bundle.name != 'runtime-' + wrapper['runtime_sha256'][:16]
                or wrapper['manifest_sha256'] != hashlib.sha256(manifest_bytes).hexdigest()
                or scope.observation_expires_epoch_us != args.observation_end_epoch_us
                or clock()[0] >= args.observation_end_epoch_us
                or (legacy is not None and (legacy.unit != args.unit
                    or legacy.current_host_boot_id != args.expected_boot
                    or legacy.observation_expires_epoch_us != args.observation_end_epoch_us
                    or legacy.runtime_sha256 != wrapper['runtime_sha256']
                    or legacy.manifest_sha256 != wrapper['manifest_sha256']))):
            raise RuntimeError('parent_snapshot_scope_or_native_facts_changed')
        result = collect(Path('/var/lib/meshticulum-bench'),scope,clock=clock,
            boot_reader=lambda:Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            legacy_scope=legacy).to_dict()
        after = facts(args.unit)
        stable = lambda value:{k:v for k,v in value.items() if k not in ('epoch_us','monotonic_us')}
        if stable(before) != stable(after) or clock()[0] >= args.observation_end_epoch_us:
            raise RuntimeError('parent_snapshot_native_lifecycle_changed')
        result['facts_before'],result['facts_after'] = before,after
        result['declared_runtime_archive_sha256'] = wrapper['runtime_sha256']
        result['runtime_archive_sha256_observed'] = False
        result['runtime_manifest_and_payloads_verified'] = True
    elif args.action in ('pilot-bundle-status','pilot-bundle-finish'):
        from lantern_meshticulum.bench.pilot_bundle import native_action
        result=native_action(args.unit,args.parent_id,'status' if args.action=='pilot-bundle-status' else 'finish')
    elif args.action == 'pilot-cache-status':
        from lantern_meshticulum.bench.pilot_manage import cache_status
        result = cache_status(args.unit, args.campaign_id)
    else:
        if args.run_id is None or not re.fullmatch(r'[0-9a-f]{32}', args.run_id):
            raise RuntimeError('invalid_run_id')
        root = Path('/var/lib/meshticulum-bench')
        directory = root / 'runs' / args.run_id
        if args.action == 'pilot-result':
            from lantern_meshticulum.bench.pilot_manage import result as collect_result
            result = collect_result(args.unit, args.run_id)
        elif args.action in ('pilot-status', 'pilot-stop', 'pilot-retire'):
            import platform
            if platform.system() != 'Linux': raise RuntimeError('requires_linux_root')
            from lantern_meshticulum.bench.pilot_job import inspect, retire
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            result = (retire(directory, unit=args.unit, run_id=args.run_id, boot=boot)
                if args.action == 'pilot-retire' else inspect(directory, unit=args.unit, run_id=args.run_id,
                    boot=boot, stop=args.action == 'pilot-stop'))
        elif args.action in ('revoke', 'ip-revoke'):
            node.roles(args.unit)
            if args.action == 'ip-revoke':
                from meshticulum.ip.authority import revoke
                revoke(directory)
            else:
                authority.revoke(directory)
            result = {'revoked': True, 'run_id': args.run_id}
        else:
            if args.action == 'pilot-verify':
                from lantern_meshticulum.bench.pilot_resource_node import verify
                result = verify(args.unit, args.campaign_id, args.request_run_id, args.run_id)
            elif args.action == 'ip-service-run':
                if manifest.get('public_ip_service_formats') != ['MR1.request', 'MR1.response']:
                    raise RuntimeError('public_ip_runner_not_in_bundle')
                from lantern_meshticulum.ip.node import run_public_service
                result = run_public_service(args.unit, directory,test_rx_cutoff=args.test_rx_cutoff)
            elif args.action == 'service-run':
                if manifest.get('public_service_formats') != ['MR1.request', 'MR1.response']:
                    raise RuntimeError('public_service_runner_not_in_bundle')
                result = node.run_public_service(args.unit, directory, sender_receive_ready=args.sender_receive_ready,
                                                 native_phase_timing=args.native_phase_timing)
            else:
                result = node.run(args.unit, directory, root / 'objects')
    result['runtime_bundle'] = str(bundle)
    result['bundle_manifest_sha256'] = hashlib.sha256(manifest_bytes).hexdigest()
    print(json.dumps(result))
    if args.action in ('run', 'service-run', 'ip-service-run') and not result['complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
