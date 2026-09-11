#!/usr/bin/env python3
"""One-shot local signed restart request, NOT deployment admission or completion.

Caller must first establish the reviewed cold-appliance containment, auth drain,
governance and artifact transition. This helper neither stops services nor
installs anything. SAGE must accept its own signer/snapshot/restart gates.
Run the root-owned reviewed helper with the installed Python using -I -B.
The retained intent forbids automatic retry, including after ambiguous delivery.
Unit metadata accepts provision-lantern-unit.sh's root:service-group 0640 or the
previously reviewed root-owned 0644 public profile. The Root key remains 0600.
"""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import resource
import stat
import subprocess
import sys
import uuid


KEY = Path('/var/lib/sage-lantern/sage-personal/agent.key')
PUBLIC = Path('/etc/sage-lantern/initialized-public.json')
UNIT = Path('/etc/sage-lantern/unit.env')
INTENT = Path('/run/sage-lantern-native-restart-request.jsonl')
ROUTE = '/v1/dashboard/settings/update/restart'
SERVICE = 'sage-lantern-personal-sage.service'


def require(value):
    if value is not True:
        raise RuntimeError('fresh_restart_refused')


def signature(info):
    return tuple(getattr(info, name) for name in
                 ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def opened(path, owner, mode, maximum, *, parent_owners):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in parent_owners and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == mode and 0 < info.st_size <= maximum
                and signature(info) == signature(path.lstat()))
        return descriptor, info
    except BaseException:
        os.close(descriptor)
        raise


def public_bytes(path):
    modes = {PUBLIC: 0o600, UNIT: 0o644}
    require(path in modes)
    if path == UNIT:
        modes[UNIT] = stat.S_IMODE(path.lstat().st_mode)
        require(modes[UNIT] in (0o640, 0o644))
    descriptor, before = opened(path, 0, modes[path], 65536, parent_owners={0})
    try:
        if path == UNIT and modes[UNIT] == 0o640:
            group = pwd.getpwnam('sage-lantern').pw_gid
            require(group > 0 and before.st_gid == group)
        raw = os.read(descriptor, 65537)
        require(len(raw) == before.st_size and signature(before) == signature(os.fstat(descriptor)))
        return raw
    finally:
        os.close(descriptor)


def request_restart(identity):
    headers = identity.sign_request('POST', ROUTE, b'')
    require(type(headers) is dict and set(headers) == {'X-Agent-ID', 'X-Timestamp', 'X-Signature', 'X-Nonce'}
            and headers['X-Agent-ID'] == identity.agent_id)
    connection = http.client.HTTPConnection('127.0.0.1', 8080, timeout=25)
    try:
        connection.request('POST', ROUTE, body=b'', headers=headers)
        response = connection.getresponse()
        raw = response.read(4097)
        require(response.status == 202 and len(raw) <= 4096)
        value = json.loads(raw)
        require(type(value) is dict and value.get('ok') is True and value.get('status') == 'draining'
                and type(value.get('boot_id')) is str and bool(value['boot_id']))
        return {'accepted': True, 'completed': False}
    finally:
        connection.close()


def record(descriptor, value):
    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
    with os.fdopen(os.dup(descriptor), 'ab') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--unit-id', choices=['unit-a'], required=True)
    parser.add_argument('--boot-id', required=True)
    parser.add_argument('--root-id', required=True)
    parser.add_argument('--initialized-sha256', required=True)
    parser.add_argument('--request-restart', action='store_true', required=True)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    script = Path(__file__).lstat()
    require(stat.S_ISREG(script.st_mode) and script.st_uid == 0 and script.st_nlink == 1
            and not script.st_mode & 0o022)
    require(str(uuid.UUID(args.boot_id)) == args.boot_id
            and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == args.boot_id)
    require(re.fullmatch('[0-9a-f]{64}', args.root_id) is not None
            and re.fullmatch('[0-9a-f]{64}', args.initialized_sha256) is not None)
    require(public_bytes(UNIT) == b'UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n')
    raw = public_bytes(PUBLIC)
    require(hashlib.sha256(raw).hexdigest() == args.initialized_sha256)
    receipt = json.loads(raw)
    require(receipt.get('schema') == 'sage.lantern.initialized.v1'
            and receipt.get('unit_id') == args.unit_id and receipt.get('root_id') == args.root_id)
    account = pwd.getpwnam('sage-lantern')
    require(account.pw_uid > 0 and account.pw_gid > 0)
    environment = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'}
    observed = subprocess.run(['/usr/bin/systemctl', 'show', SERVICE,
        '-p', 'User', '-p', 'ActiveState', '-p', 'MainPID', '-p', 'Job', '--all'],
        env=environment, capture_output=True, text=True, timeout=5, check=True)
    fields = dict(line.split('=', 1) for line in observed.stdout.splitlines())
    require(fields.get('User') == 'sage-lantern' and fields.get('ActiveState') == 'active'
            and re.fullmatch('[1-9][0-9]*', fields.get('MainPID', '')) is not None
            and fields.get('Job') in ('', '0'))
    key_fd, key_info = opened(KEY, account.pw_uid, 0o600, 32, parent_owners={0, account.pw_uid})
    journal = None
    try:
        require(key_info.st_size == 32)
        journal = os.open(INTENT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(journal, 0o600)
        record(journal, dict(phase='intent', unit_id=args.unit_id, boot_id=args.boot_id,
                            root_id=args.root_id, initialized_sha256=args.initialized_sha256))
        parent = os.open(INTENT.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        os.environ.clear()
        os.environ.update(environment)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        from sage_sdk import AgentIdentity
        identity = AgentIdentity.from_file(f'/proc/self/fd/{key_fd}')
        require(identity.agent_id == args.root_id and os.geteuid() == account.pw_uid
                and signature(key_info) == signature(os.fstat(key_fd))
                and signature(key_info) == signature(KEY.lstat()))
        result = request_restart(identity)
        record(journal, dict(phase='accepted_not_completed', **result))
        print(json.dumps(result, sort_keys=True))
    finally:
        os.close(key_fd)
        if journal is not None:
            os.close(journal)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Restart request refused or outcome unknown; retain intent, do not retry automatically.', file=sys.stderr)
        raise SystemExit(2) from None
