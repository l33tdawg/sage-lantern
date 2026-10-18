"""One operator-approved radio contact update; no RF send or automatic retry."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import threading
import time
import uuid

from .mesh_contacts import verify_contact_card
from .meshcore_transport import FIRMWARE, PROTOCOL_VERSION, SUPPORTED_FIRMWARE
from .mesh_voice_ipc import require
from .suspend_io import unique_object


ROOT = Path('/var/lib/sage-lantern-radio-commission')
LOCK = threading.Lock()
TIMEOUT_SECONDS = 20


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def trusted_directory(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def read(path):
    trusted_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= 4096)
        raw = os.read(descriptor, 4097)
        fields = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, name) == getattr(after, name)
            for after in (os.fstat(descriptor), path.lstat()) for name in fields))
        return raw
    finally:
        os.close(descriptor)


def write(path, value):
    raw = canonical(value)
    require(len(raw) <= 4096)
    trusted_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    require(read(path) == raw)


def no_unknown():
    try:
        if not os.path.lexists(ROOT):
            trusted_directory(ROOT.parent)
            return True
        trusted_directory(ROOT)
        entries = list(ROOT.iterdir())
        require(len(entries) <= 256)
        for directory in entries:
            require(re.fullmatch('[0-9a-f]{32}', directory.name) is not None)
            trusted_directory(directory)
            intent = json.loads(read(directory / 'intent.json'), object_pairs_hook=unique_object)
            completed = json.loads(read(directory / 'complete.json'), object_pairs_hook=unique_object)
            setter = json.loads(read(directory / 'setter-intent.json'), object_pairs_hook=unique_object)
            require(set(path.name for path in directory.iterdir()) == {'intent.json', 'setter-intent.json', 'complete.json'})
            require(set(intent) == {'schema', 'broker_id', 'epoch', 'unit_id', 'attempt_id',
                'config_sha256', 'request_sha256', 'card_sha256'})
            require(intent['schema'] == 'sage.lantern.radio-commission.v1'
                and intent['attempt_id'] == directory.name and intent['unit_id'] in ('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'))
            for name, width in (('broker_id', 32), ('epoch', 32), ('config_sha256', 64),
                                ('request_sha256', 64), ('card_sha256', 64)):
                require(type(intent[name]) is str and re.fullmatch('[0-9a-f]{' + str(width) + '}', intent[name]) is not None)
            require(canonical(setter) == canonical(dict(request_sha256=intent['request_sha256'], outcome='unknown')))
            require(canonical(completed) == canonical(dict(intent_sha256=sha(canonical(intent)), readback_verified=True,
                durable_radio_storage_verified=False, rf_delivery_verified=False)))
        return True
    except Exception:
        return False


def validate(request, config, broker_id, epoch):
    require(type(request) is dict and set(request) == {'op', 'broker_id', 'epoch', 'config_sha256',
        'card', 'card_sha256', 'profile', 'contact_frame', 'before_sha256', 'attempt_id', 'approve_contact_update',
        'approve_initial_open'})
    require(request['op'] == 'commission-radio' and request['broker_id'] == broker_id
        and request['epoch'] == epoch and type(epoch) is str and re.fullmatch('[0-9a-f]{32}', epoch) is not None
        and request['config_sha256'] == sha(canonical(config))
        and request['approve_contact_update'] is True and type(request['approve_initial_open']) is bool)
    require(not config['messaging_enabled'] and not config['dispatch_enabled']
        and 'public_mesh' not in config and 'public_gateway' not in config)
    require(type(request['attempt_id']) is str and uuid.UUID(request['attempt_id']).hex == request['attempt_id']
        and uuid.UUID(request['attempt_id']).int != 0)
    require(type(request['card']) is str and len(request['card'].encode()) <= 8192
        and sha(request['card'].encode()) == request['card_sha256'])
    card = verify_contact_card(request['card'].encode(), now=time.time())
    key = bytes.fromhex(card.radio_public_key)
    require(dict(chain_id=card.chain_id, agent_id=card.agent_id, radio_public_key=key.hex()) in config['peers']
        and key.hex() != config['radio']['public_key'] and card.messaging_allowed is True)
    require(type(request['contact_frame']) is str and re.fullmatch('[0-9a-f]{296}', request['contact_frame']) is not None)
    frame = bytes.fromhex(request['contact_frame'])
    require(frame[0] == 3 and frame[1:33] == key and frame[33] == 1
        and frame[34] in (0, 1) and frame[35] == 255 and frame[36:100] == bytes(64)
        and frame[132:144] == bytes(12) and int.from_bytes(frame[144:], 'little') > 0)
    name = frame[100:132]
    require(b'\0' in name and name == name.split(b'\0')[0].ljust(32, b'\0'))
    require(all(32 <= value < 127 for value in name.split(b'\0')[0]))
    require(request['before_sha256'] is None or (type(request['before_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', request['before_sha256']) is not None))
    profile = request['profile']
    require(type(profile) is dict and set(profile) == {'frequency_khz', 'bandwidth_hz', 'sf', 'cr', 'tx_dbm', 'repeat', 'path_hash_mode'}
        and all(type(value) is int for value in profile.values()))
    require(150000 <= profile['frequency_khz'] <= 2500000 and 7000 <= profile['bandwidth_hz'] <= 500000
        and 5 <= profile['sf'] <= 12 and 5 <= profile['cr'] <= 8 and -9 <= profile['tx_dbm'] <= 127
        and profile['repeat'] in (0, 1) and profile['path_hash_mode'] in (0, 1, 2))
    return frame


def read_profile(client, exchange):
    info = exchange(bytes((22, PROTOCOL_VERSION)), 13)
    require(len(info) == 82 and info[1] == PROTOCOL_VERSION
        and info[60:80].split(b'\0')[0] in tuple(version.encode() for version in SUPPORTED_FIRMWARE))
    own = exchange(b'\x01' + bytes(7) + b'Lantern', 5)
    require(58 <= len(own) <= 90 and own[4:36] == client._key)
    frequency, bandwidth, spreading, coding = struct.unpack('<IIBB', own[48:58])
    return dict(frequency_khz=frequency, bandwidth_hz=bandwidth, sf=spreading,
        cr=coding, tx_dbm=struct.unpack('b', own[2:3])[0], repeat=info[80], path_hash_mode=info[81])


def wire(client, request, frame, guard, intent):
    def operation(deadline):
        def exchange(payload, expected):
            require(guard() is True)
            result = client._exchange(payload, expected, deadline)
            require(guard() is True)
            return result
        def profile():
            require(request['profile'] == read_profile(client, exchange))
        profile()
        prior = exchange(b'\x1e' + frame[1:33], (3, 1))
        require((request['before_sha256'] is None and prior == b'\x01\x02')
            or (len(prior) == 148 and prior[0] == 3 and prior[1:33] == frame[1:33]
                and sha(prior) == request['before_sha256']))
        intent()
        require(exchange(b'\x09' + frame[1:], 0) == b'\0')
        require(exchange(b'\x1e' + frame[1:33], 3) == frame)
        profile()
        return True
    return client._run(operation, sending=True)


def commission(resource, request, *, broker_id, epoch_reader):
    frame = validate(request, resource.config, broker_id, epoch_reader())
    require(LOCK.acquire(blocking=False))
    acquired = False
    directory = None
    cancelled = threading.Event()
    deadline = time.monotonic() + TIMEOUT_SECONDS
    def guard():
        return (not cancelled.is_set() and time.monotonic() < deadline and resource.active()
            and epoch_reader() == request['epoch'] and resource.supervisor.broker_id == broker_id
            and validate(request, resource.config, broker_id, epoch_reader()) == frame)
    try:
        require(no_unknown())
        require(resource._mutex.acquire(blocking=False))
        acquired = True
        require(guard())
        require(resource._coordinator is not None or request['approve_initial_open'])
        if not os.path.lexists(ROOT):
            trusted_directory(ROOT.parent)
            ROOT.mkdir(mode=0o700)
        trusted_directory(ROOT)
        require(len(list(ROOT.iterdir())) < 256)
        directory = ROOT / request['attempt_id']
        directory.mkdir(mode=0o700)
        intent = dict(schema='sage.lantern.radio-commission.v1', broker_id=broker_id,
            epoch=request['epoch'], unit_id=resource.config['unit_id'],
            attempt_id=request['attempt_id'], config_sha256=request['config_sha256'],
            request_sha256=sha(canonical(request)), card_sha256=request['card_sha256'])
        write(directory / 'intent.json', intent)
        for parent in (ROOT, ROOT.parent):
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        if resource._coordinator is None:
            require(request['approve_initial_open'])
            resource.provider(guard)
        coordinator = resource._coordinator
        require(coordinator is not None and guard())
        def execute(client):
            require(guard() and resource._coordinator is coordinator)
            require(client._key.hex() == resource.config['radio']['public_key'])
            wire(client, request, frame, guard, lambda: write(directory / 'setter-intent.json',
                dict(request_sha256=intent['request_sha256'], outcome='unknown')))
            require(guard())
            write(directory / 'complete.json', dict(intent_sha256=sha(canonical(intent)), readback_verified=True,
                durable_radio_storage_verified=False, rf_delivery_verified=False))
            return dict(readback_verified=True, durable_radio_storage_verified=False, rf_delivery_verified=False,
                        contact_sha256=sha(frame), attempt_id=request['attempt_id'])
        return coordinator.submit(execute).result(timeout=max(0.001, deadline - time.monotonic()))
    except BaseException:
        cancelled.set()
        if directory is not None:
            resource._uncertain = True
        raise
    finally:
        if acquired:
            resource._mutex.release()
        LOCK.release()
