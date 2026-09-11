import copy
import os
import struct
import threading
import time
from types import SimpleNamespace

import pytest
from sage_sdk import AgentIdentity

from sage_lantern import mesh_radio_commission as module
from sage_lantern.mesh_contacts import sign_contact_card
from sage_lantern.mesh_serial_owner import SerialScheduler
from sage_lantern.meshcore_transport import MeshCoreError, encode_frame
from test_meshcore_transport import KEY, client, frame


@pytest.fixture
def request_data():
    identity = AgentIdentity.from_seed(b'r' * 32)
    radio = AgentIdentity.from_seed(b's' * 32).agent_id
    now = int(time.time())
    card = sign_contact_card(identity, chain_id='remote', radio_public_key=radio,
        display_name='Synthetic', revision=1, issued_at=now, expires_at=now + 600,
        now=now, messaging_allowed=True).decode()
    config = dict(unit_id='unit-a', messaging_enabled=False, dispatch_enabled=False,
        peers=[dict(chain_id='remote', agent_id=identity.agent_id, radio_public_key=radio)],
        radio=dict(public_key=KEY.hex()))
    contact = bytes((3,)) + bytes.fromhex(radio) + bytes((1, 0, 255)) + bytes(64)
    contact += b'Synthetic'.ljust(32, b'\0') + bytes(12) + struct.pack('<I', now)
    request = dict(op='commission-radio', broker_id='b' * 32, epoch='e' * 32,
        config_sha256=module.sha(module.canonical(config)), card=card, card_sha256=module.sha(card.encode()),
        profile=dict(frequency_khz=869525, bandwidth_hz=250000, sf=11, cr=5, tx_dbm=14, repeat=0, path_hash_mode=0),
        contact_frame=contact.hex(), before_sha256=None, attempt_id='a' * 32,
        approve_contact_update=True, approve_initial_open=False)
    return config, request, contact


def profile_bytes(request, key=KEY):
    info = bytearray(82)
    info[0:2] = bytes((13, 13))
    info[60:60 + len(module.FIRMWARE)] = module.FIRMWARE.encode()
    info[80:82] = bytes((request['profile']['repeat'], request['profile']['path_hash_mode']))
    own = bytearray(58)
    own[0], own[2] = 5, request['profile']['tx_dbm'] & 255
    own[4:36] = key
    own[48:58] = struct.pack('<IIBB', *(request['profile'][name]
        for name in ('frequency_khz', 'bandwidth_hz', 'sf', 'cr')))
    return frame(info) + frame(own)


def incoming(request, contact, prior=b'\x01\x02', readback=None):
    return profile_bytes(request) + frame(prior) + frame(b'\0') + frame(contact if readback is None else readback) + profile_bytes(request)


def test_actual_transport_exact_commands_and_no_rf(request_data):
    config, request, contact = request_data
    assert module.validate(request, config, request['broker_id'], request['epoch']) == contact
    device, stream = client(incoming(request, contact))
    intents = []
    assert module.wire(device, request, contact, lambda: True, lambda: intents.append(bytes(stream.output)))
    queries = encode_frame(bytes((22, 13))) + encode_frame(b'\x01' + bytes(7) + b'Lantern')
    get = encode_frame(b'\x1e' + contact[1:33])
    assert intents == [queries + get]
    assert bytes(stream.output) == queries + get + encode_frame(b'\x09' + contact[1:]) + get + queries


@pytest.mark.parametrize('change', ['epoch', 'broker', 'config', 'consent', 'card', 'peer', 'frame', 'zero', 'enabled'])
def test_authority_rejected_before_serial(request_data, change):
    config, request, contact = request_data
    if change == 'epoch':
        request['epoch'] = 'f' * 32
    elif change == 'broker':
        request['broker_id'] = 'c' * 32
    elif change == 'config':
        request['config_sha256'] = '0' * 64
    elif change == 'consent':
        request['approve_contact_update'] = False
    elif change == 'card':
        request['card'] += ' '
    elif change == 'peer':
        config['peers'] = []
        request['config_sha256'] = module.sha(module.canonical(config))
    elif change == 'frame':
        request['contact_frame'] = (contact[:35] + b'\x00' + contact[36:]).hex()
    elif change == 'zero':
        request['attempt_id'] = '0' * 32
    else:
        config['messaging_enabled'] = True
        request['config_sha256'] = module.sha(module.canonical(config))
    with pytest.raises(Exception):
        module.validate(request, config, 'b' * 32, 'e' * 32)


@pytest.mark.parametrize('failure', ['self', 'profile', 'prior', 'readback', 'timeout', 'revoked'])
def test_failure_poison_no_retry(request_data, failure):
    config, request, contact = request_data
    raw = incoming(request, contact)
    guard = lambda: True
    if failure == 'self':
        raw = profile_bytes(request, key=b'x' * 32)
    elif failure == 'profile':
        other = copy.deepcopy(request)
        other['profile']['sf'] = 10
        raw = profile_bytes(other)
    elif failure == 'prior':
        raw = incoming(request, contact, prior=contact)
    elif failure == 'readback':
        raw = incoming(request, contact, readback=contact[:-1] + b'\xff')
    elif failure == 'timeout':
        raw = profile_bytes(request) + frame(b'\x01\x02')
    else:
        guard = lambda: False
    device, stream = client(raw)
    intents = []
    with pytest.raises(MeshCoreError):
        module.wire(device, request, contact, guard, lambda: intents.append(True))
    before = bytes(stream.output)
    with pytest.raises(MeshCoreError):
        module.wire(device, request, contact, lambda: True, lambda: None)
    assert bytes(stream.output) == before
    assert bool(intents) == (failure in ('readback', 'timeout'))


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ROOT', tmp_path / 'journal')
    monkeypatch.setattr(module, 'trusted_directory', lambda path: None)
    def local_read(path):
        assert path.lstat().st_nlink == 1 and path.lstat().st_mode & 0o777 == 0o600
        return path.read_bytes()
    monkeypatch.setattr(module, 'read', local_read)
    return module.ROOT


def resource_for(config, scheduler):
    resource = SimpleNamespace(config=config, supervisor=SimpleNamespace(broker_id='b' * 32),
        _mutex=threading.Lock(), _coordinator=scheduler, _uncertain=False)
    resource.active = lambda: not resource._uncertain
    resource.provider = lambda guard: pytest.fail('must reuse exclusive owner')
    return resource


def test_real_scheduler_completion_and_exclusive_attempt(request_data, journal):
    config, request, contact = request_data
    device, stream = client(incoming(request, contact))
    scheduler = SerialScheduler(device, release=lambda: None)
    resource = resource_for(config, scheduler)
    try:
        result = module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: 'e' * 32)
        assert result['readback_verified'] and not result['durable_radio_storage_verified']
        assert module.no_unknown()
        before = bytes(stream.output)
        with pytest.raises(FileExistsError):
            module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: 'e' * 32)
        assert bytes(stream.output) == before
    finally:
        scheduler.close(timeout=2)


def test_readback_unknown_survives_restart(request_data, journal):
    config, request, contact = request_data
    device, stream = client(incoming(request, contact, readback=b'\x03bad'))
    scheduler = SerialScheduler(device, release=lambda: None)
    resource = resource_for(config, scheduler)
    try:
        with pytest.raises(Exception):
            module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: 'e' * 32)
        assert resource._uncertain and not module.no_unknown()
        assert (journal / request['attempt_id'] / 'setter-intent.json').exists()
        assert not (journal / request['attempt_id'] / 'complete.json').exists()
        resource._uncertain = False
        before = bytes(stream.output)
        with pytest.raises(Exception):
            module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: 'e' * 32)
        assert bytes(stream.output) == before
    finally:
        scheduler.close(timeout=2)


def test_queued_timeout_cannot_later_touch_serial(request_data, journal, monkeypatch):
    config, request, contact = request_data
    device, stream = client(incoming(request, contact))
    scheduler = SerialScheduler(device, release=lambda: None)
    entered, release = threading.Event(), threading.Event()
    def blocking(client):
        entered.set()
        release.wait(2)
    pending = scheduler.submit(blocking)
    assert entered.wait(1)
    resource = resource_for(config, scheduler)
    monkeypatch.setattr(module, 'TIMEOUT_SECONDS', 0.03)
    try:
        with pytest.raises(TimeoutError):
            module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: 'e' * 32)
        release.set()
        pending.result(1)
        scheduler.submit(lambda client: None).result(1)
        assert not stream.output and resource._uncertain and not module.no_unknown()
    finally:
        release.set()
        scheduler.close(timeout=2)


def test_mutex_ownership_refusal_no_journal(request_data, journal):
    config, request, contact = request_data
    resource = resource_for(config, None)
    resource._mutex.acquire()
    try:
        with pytest.raises(Exception):
            module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: 'e' * 32)
        assert not journal.exists()
    finally:
        resource._mutex.release()


def test_untrusted_journal_latches(monkeypatch):
    monkeypatch.setattr(module, 'trusted_directory', lambda path: (_ for _ in ()).throw(PermissionError()))
    assert module.no_unknown() is False


def test_initial_open_requires_explicit_approval(request_data, journal):
    config, request, contact = request_data
    resource = resource_for(config, None)
    with pytest.raises(Exception):
        module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: 'e' * 32)
    assert not journal.exists() and not resource._uncertain


def test_initial_open_uses_existing_provider_once(request_data, journal):
    config, request, contact = request_data
    request['approve_initial_open'] = True
    device, stream = client(incoming(request, contact))
    scheduler = SerialScheduler(device, release=lambda: None)
    resource = resource_for(config, None)
    opened = []
    def provider(guard):
        assert guard()
        opened.append(True)
        resource._coordinator = scheduler
    resource.provider = provider
    try:
        assert module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: 'e' * 32)['readback_verified']
        assert opened == [True]
    finally:
        scheduler.close(timeout=2)


def test_lock_after_setter_preserves_unknown(request_data, journal):
    config, request, contact = request_data
    device, stream = client(incoming(request, contact), chunk=1000)
    epoch = ['e' * 32]
    original = stream.write
    def write(data, timeout):
        count = original(data, timeout)
        if data[:4] == encode_frame(b'\x09' + contact[1:])[:4]:
            epoch[0] = None
        return count
    stream.write = write
    scheduler = SerialScheduler(device, release=lambda: None)
    resource = resource_for(config, scheduler)
    try:
        with pytest.raises(Exception):
            module.commission(resource, request, broker_id='b' * 32, epoch_reader=lambda: epoch[0])
        assert not module.no_unknown() and resource._uncertain
        assert (journal / request['attempt_id'] / 'setter-intent.json').exists()
    finally:
        scheduler.close(timeout=2)
