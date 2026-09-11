#!/usr/bin/env python3
"""Read-only fresh vision handoff; never drain, capture, start, install or grant.

Run with root Linux Python -I -B and an explicitly hash-pinned reviewed planner.
Output is an observation, not a lease or permission to apply. History outcomes
cannot be inferred from idle/complete, missing preferences or successful answers.
No private state, tokens, keys, conversation records or audio are read.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import struct
import subprocess
import sys
import types
import uuid


SOCKET = '/run/sage-lantern-mesh-control/broker.sock'
SERVICES = tuple('sage-lantern-' + name + '.service' for name in (
    'voice-loop', 'mesh-runtime', 'llm', 'device-auth', 'idle-suspend',
    'fleet-admin', 'status', 'camera', 'display-power'))
FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'InvocationID', 'Job',
          'ControlGroup', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')


def require(value):
    if not value:
        raise ValueError('preflight observation rejected')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def identity(info):
    return {name: getattr(info, name) for name in (
        'st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
        'st_size', 'st_mtime_ns', 'st_ctime_ns')}


def public_file(name, *, limit, content=False):
    path = Path(name)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in reversed(path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= limit)
        digest = hashlib.sha256()
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1048576, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            require(total <= limit)
            digest.update(chunk)
            if content:
                chunks.append(chunk)
        require(total == before.st_size and identity(before) == identity(os.fstat(descriptor))
                == identity(path.lstat()))
        return dict(path=str(path), sha256=digest.hexdigest(), metadata=identity(before)), b''.join(chunks)
    finally:
        os.close(descriptor)


def planner(path, expected):
    require(re.fullmatch('[0-9a-f]{64}', expected) is not None)
    pin, raw = public_file(path, limit=131072, content=True)
    require(pin['sha256'] == expected)
    module = types.ModuleType('reviewed_fresh_vision_planner')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module, pin


def service(name):
    command = ['/usr/bin/systemctl', 'show', name, '--all']
    for field in FIELDS:
        command += ['-p', field]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=5,
                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}, stdin=subprocess.DEVNULL)
    require(len(result.stdout) <= 32768)
    value = unique(line.split('=', 1) for line in result.stdout.splitlines())
    require(set(value) == set(FIELDS))
    return value


def exact(connection, size):
    raw = bytearray()
    while len(raw) < size:
        chunk = connection.recv(size - len(raw))
        require(bool(chunk))
        raw.extend(chunk)
    return bytes(raw)


def broker_active():
    nonce = str(uuid.uuid4())
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(SOCKET)
        peer_pid, peer_uid, _ = struct.unpack('3i', connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(peer_uid == 0 and peer_pid > 0)
        raw = json.dumps(dict(op='verify-deployment-active', nonce=nonce)).encode()
        connection.sendall(len(raw).to_bytes(4, 'big') + raw)
        size = int.from_bytes(exact(connection, 4), 'big')
        require(0 < size <= 1024)
        response = json.loads(exact(connection, size), object_pairs_hook=unique)
        require(type(response) is dict and set(response) == {'result'})
        value = response['result']
        require(type(value) is dict and set(value) == {
            'nonce', 'broker_id', 'vault_epoch', 'broker_pid', 'voice_pid'})
        require(value['nonce'] == nonce and type(value['broker_id']) is str
                and re.fullmatch('[0-9a-f]{32}', value['broker_id']) is not None)
        require(type(value['vault_epoch']) is str
                and re.fullmatch('[0-9a-f]{64}:[1-9][0-9]{0,19}', value['vault_epoch']) is not None
                and int(value['vault_epoch'].split(':')[1]) <= 2**64 - 1)
        require(all(type(value[key]) is int and value[key] > 0 for key in ('broker_pid', 'voice_pid')))
        require(value['broker_pid'] == peer_pid)
        return {key: member for key, member in value.items() if key != 'nonce'}


def observe(module, planner_pin, unit, binary_sha, *, capture_approved=False):
    require(re.fullmatch('[0-9a-f]{64}', binary_sha) is not None)
    require(type(capture_approved) is bool)
    blockers = ['positive_history_and_broker_drain_not_observed',
                'changed_voice_dropin_requires_reviewed_audio_recommissioning',
                'matching_photo_inference_evidence_not_observed',
                'bounded_apply_and_completion_adapter_not_implemented']
    if not capture_approved:
        blockers.append('operator_capture_approval_pending')
    pins = {'planner': planner_pin}
    candidate = None

    def check(label, action):
        try:
            return action()
        except Exception:
            blockers.append(label)
            return None

    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(str(uuid.UUID(boot)) == boot)
    candidate = check('installed_configuration_rejected', lambda: module.plan(unit, fresh_audio=True))
    if candidate is not None:
        pins['plan_sha256'] = sha(json.dumps(candidate, sort_keys=True, separators=(',', ':')).encode())
        artifacts = candidate['evidence_requirements']
        for label in ('model', 'projector', 'binary'):
            def artifact(label=label):
                item = artifacts[label]
                pin, _ = public_file(item['path'], limit=8 * 1024**3)
                require(pin['sha256'] == (binary_sha if label == 'binary' else item['sha256']))
                return pin
            pins[label] = check(label + '_pin_rejected', artifact)
    before = check('service_observation_unavailable', lambda: {name: service(name) for name in SERVICES})
    active = check('live_broker_proof_unavailable', broker_active)
    after = check('service_recheck_unavailable', lambda: {name: service(name) for name in SERVICES})
    pins.update(services=after, broker=active, boot_id=boot)
    if before is None or before != after:
        blockers.append('service_identity_changed_or_unobserved')
    if after is not None:
        if after['sage-lantern-idle-suspend.service']['LoadState'] != 'loaded':
            blockers.append('warm_deployment_idle_controller_unavailable')
        for name, value in after.items():
            if value['Job'] not in ('', '0') or value['NeedDaemonReload'] != 'no':
                blockers.append('pending_job_or_reload:' + name)
        if active is not None:
            for name, field in (('mesh-runtime', 'broker_pid'), ('voice-loop', 'voice_pid')):
                value = after['sage-lantern-' + name + '.service']
                if (value['LoadState'] != 'loaded' or value['ActiveState'] != 'active'
                        or value['MainPID'] != str(active[field])):
                    blockers.append('broker_service_binding_mismatch:' + name)
    if candidate is not None:
        repeated = check('configuration_recheck_rejected', lambda: module.plan(unit, fresh_audio=True))
        if repeated != candidate:
            blockers.append('configuration_changed')
    repeated_active = check('broker_recheck_unavailable', broker_active)
    if repeated_active != active:
        blockers.append('broker_lifetime_changed')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == boot)
    return dict(schema='sage.lantern.fresh-vision-activation-preflight.v1', mode='read-only',
                activation_authorized=False, drain_verified=False, history_outcome='unobserved',
                capture_performed=False, capture_approved=capture_approved, pins=pins,
                candidate=candidate, blockers=blockers,
                apply_handshake=['revalidate_exact_inputs_and_live_identities',
                    'DeploymentDrain.acquire_with_real_verify_root_broker_then_prepare',
                    'retain_failure_fences_on_any_unknown_history_or_work',
                    'per_mutation_assert_current_and_durable_intent_done',
                    'explicit_new_configuration_audio_commissioning_and_model_inference_validation',
                    'reviewed_DeploymentCompletion_broker_owned_resume_and_surface_restoration'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--planner', type=Path, required=True)
    parser.add_argument('--planner-sha256', required=True)
    parser.add_argument('--llama-sha256', required=True)
    parser.add_argument('--unit-id', choices=('unit-a', 'unit-b'), required=True)
    parser.add_argument('--capture-approved', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    def expired(signum, frame):
        raise SystemExit('read-only preflight deadline exceeded')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(180)
    module, pin = planner(args.planner, args.planner_sha256)
    result = observe(module, pin, args.unit_id, args.llama_sha256, capture_approved=args.capture_approved)
    print(json.dumps(result, sort_keys=True))
    signal.alarm(0)
    return 2 if result['blockers'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
