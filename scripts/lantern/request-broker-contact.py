"""Pinned root operator client; default validates only, never opens serial."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from sage_lantern.mesh_voice_ipc import connect, peer, send, receive, require
from sage_lantern.suspend_io import unique_object
from sage_lantern.mesh_radio_commission import canonical, sha, trusted_directory, write


OPERATOR_ROOT = Path('/var/lib/sage-lantern-radio-operator')


def begin_attempt(envelope):
    trusted_directory(OPERATOR_ROOT.parent)
    if not os.path.lexists(OPERATOR_ROOT):
        OPERATOR_ROOT.mkdir(mode=0o700)
    trusted_directory(OPERATOR_ROOT)
    attempt = OPERATOR_ROOT / sha(canonical(envelope))
    attempt.mkdir(mode=0o700)
    write(attempt / 'intent.json', dict(schema='sage.lantern.radio-operator.v1',
        envelope_sha256=sha(canonical(envelope)), broker_process=envelope['broker_process'],
        operation=envelope['request']['op'], outcome='unknown'))
    for parent in (OPERATOR_ROOT, OPERATOR_ROOT.parent):
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return attempt


def pinned(path, checksum):
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
            and not before.st_mode & 0o022 and 0 < before.st_size <= 32768)
        raw = os.read(descriptor, 32769)
        fields = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == checksum
            and all(getattr(before, field) == getattr(after, field)
                for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return json.loads(raw, object_pairs_hook=unique_object)
    finally:
        os.close(descriptor)


def process(pin):
    require(type(pin) is dict and set(pin) == {'pid', 'start_ticks'}
        and type(pin['pid']) is int and pin['pid'] > 0
        and type(pin['start_ticks']) is str and re.fullmatch('[1-9][0-9]*', pin['start_ticks']) is not None)
    fields = Path('/proc', str(pin['pid']), 'stat').read_bytes().rsplit(b')', 1)[1].split()
    require(fields[0] not in (b'Z', b'T', b't', b'X') and fields[19].decode() == pin['start_ticks'])


def run(envelope, apply=False):
    require(type(envelope) is dict and set(envelope) == {'broker_process', 'request'})
    request = envelope['request']
    require(type(request) is dict and request.get('op') in ('import-contact', 'commission-radio', 'radio-snapshot'))
    from sage_lantern.mesh_daemon import load_config
    from sage_lantern.device_access import access_epoch
    from sage_lantern.mesh_radio_commission import canonical, sha, validate
    from sage_lantern.mesh_contacts import verify_contact_card
    config = load_config()
    epoch = access_epoch()
    require(epoch is not None and request['epoch'] == epoch
        and request['config_sha256'] == sha(canonical(config)))
    if request['op'] == 'radio-snapshot':
        from sage_lantern.mesh_radio_snapshot import validate as validate_snapshot
        validate_snapshot(request, config, request['broker_id'], epoch)
    elif request['op'] == 'commission-radio':
        validate(request, config, request['broker_id'], epoch)
    else:
        require(set(request) == {'op', 'broker_id', 'epoch', 'config_sha256', 'card_sha256', 'card'})
        require(sha(request['card'].encode()) == request['card_sha256'])
        import time
        card = verify_contact_card(request['card'].encode(), now=time.time())
        require(dict(chain_id=card.chain_id, agent_id=card.agent_id,
            radio_public_key=card.radio_public_key) in config['peers'])
    process(envelope['broker_process'])
    if not apply:
        return dict(validated=True, dispatched=False)
    connection = connect()
    try:
        require(peer(connection) == (envelope['broker_process']['pid'], 0))
        process(envelope['broker_process'])
        require(access_epoch() == epoch)
        connection.settimeout(25)
        attempt = None if request['op'] == 'radio-snapshot' else begin_attempt(envelope)
        process(envelope['broker_process'])
        require(access_epoch() == epoch and load_config() == config)
        send(connection, request)
        result = receive(connection)
        process(envelope['broker_process'])
        require(access_epoch() == epoch and load_config() == config and set(result) == {'result'})
        if attempt is not None:
            write(attempt / 'response.json', dict(response_confirmed=True, result_sha256=sha(canonical(result))))
        return dict(dispatched=True, response_confirmed=True, result=result['result'])
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0)
    try:
        print(json.dumps(run(pinned(args.expected, args.expected_sha256), args.apply), sort_keys=True))
    except Exception:
        print(json.dumps(dict(response_confirmed=False, outcome='unknown', retry_allowed=False)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
