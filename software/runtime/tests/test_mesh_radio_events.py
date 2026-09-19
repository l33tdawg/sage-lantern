import pytest

from sage_lantern.mesh_radio_events import RadioEvents, validate_status
from sage_lantern.meshcore_transport import MeshCoreError, encode_frame
from test_meshcore_transport import client, frame, handshake_bytes, incoming, PREFIX
from test_mesh_daemon import runtime, socket_connector
from test_mesh_runtime import factory


def event(prefix=PREFIX, snr=-8):
    return dict(kind='contact', sender_prefix=prefix, snr_quarter_db=snr)


def test_bounded_epoch_scoped_metadata_and_expiry():
    clock = [100.0]
    projection = RadioEvents(clock=lambda: clock[0], wall=lambda: 1000.0)
    for number in range(40):
        projection.observe(event(number.to_bytes(6, 'big')), 'e' * 32)
    result = validate_status(projection.snapshot('e' * 32, identity_verified=True, receiving=True))
    assert len(result['last_heard']['entries']) == 32
    entry = result['last_heard']['entries'][0]
    assert entry['snr_db'] == -2 and entry['rssi_dbm'] is None
    assert entry['rf_received_at_unix_ms'] is None and not entry['direct_neighbor_verified']
    assert not result['neighbors']['available'] and result['saved_contacts']['count'] is None
    clock[0] += 60
    assert projection.snapshot('e' * 32, identity_verified=True, receiving=True)['last_heard']['entries'] == []
    projection.observe(event(), 'e' * 32)
    assert projection.snapshot('f' * 32, identity_verified=True, receiving=True)['last_heard']['entries'] == []


@pytest.mark.parametrize('code', [7, 8, 16, 17])
def test_transport_projects_only_after_confirmed_capture(code):
    raw = incoming(code, payload=b'PRIVATE NEVER IN TELEMETRY')
    device, stream = client(handshake_bytes() + frame(raw))
    device.handshake()
    events, order = [], []
    def observe(value):
        order.append('observe')
        events.append(value)
    device.set_receive_observer(observe)
    before = bytes(stream.output)
    def persist(message):
        order.append('persist')
        return True
    device.receive_next(authorize=lambda: True, persist=persist)
    assert order == ['persist', 'observe']
    assert events == [dict(kind='contact' if code in (7, 16) else 'channel',
        sender_prefix=PREFIX if code in (7, 16) else None, snr_quarter_db=-8 if code in (16, 17) else None)]
    assert b'PRIVATE' not in repr(events).encode()
    assert bytes(stream.output)[len(before):] == encode_frame(b'\x0a')


@pytest.mark.parametrize('failure', ['storage', 'lock', 'timeout'])
def test_uncertain_capture_never_projects(failure):
    device, stream = client(handshake_bytes() + (b'' if failure == 'timeout' else frame(incoming())))
    device.handshake()
    events = []
    device.set_receive_observer(events.append)
    checks = iter([True, False] if failure == 'lock' else [True, True])
    with pytest.raises(MeshCoreError):
        device.receive_next(authorize=lambda: next(checks), persist=lambda message: failure != 'storage')
    assert not events


def test_observer_failure_does_not_replay_or_poison_durable_capture():
    device, stream = client(handshake_bytes() + frame(incoming()))
    device.handshake()
    def broken(event):
        raise RuntimeError()
    device.set_receive_observer(broken)
    assert device.receive_next(authorize=lambda: True, persist=lambda message: True) is not None
    assert device._receive_observer is None and not device._poisoned


def test_lock_loss_clock_change_and_close_clear():
    clock = [10.0]
    projection = RadioEvents(clock=lambda: clock[0], wall=lambda: 1000)
    projection.observe(event(), 'e' * 32)
    projection.observe(event(), None)
    assert not projection.snapshot('e' * 32, identity_verified=True, receiving=True)['last_heard']['entries']
    projection.observe(event(), 'e' * 32)
    clock[0] = 9
    assert not projection.snapshot('e' * 32, identity_verified=True, receiving=True)['last_heard']['entries']
    projection.observe(event(), 'e' * 32)
    assert not projection.snapshot('e' * 32, identity_verified=True, receiving=False)['last_heard']['entries']
    projection.observe(event(), 'e' * 32)
    projection.clear()
    assert not projection.snapshot('e' * 32, identity_verified=True, receiving=True)['last_heard']['entries']


def test_real_authenticated_facade_status_never_initializes_or_reads_serial(runtime):
    from sage_lantern.mesh_daemon import MeshDaemon
    from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
    from sage_lantern.community_service import CommunityService, CommunityError
    state, supervisor, resource, config, opened = runtime
    daemon = MeshDaemon(supervisor, resource, epoch_reader=lambda: 'e' * 32)
    connector, workers = socket_connector(daemon)
    facade = MeshVoiceFacade(connector=connector, epoch_reader=lambda: 'e' * 32)
    service = CommunityService(mesh_provider=facade)
    before = bytes(state.scheduler._client._stream.output)
    response = service.dispatch('/community/radio/status', {}, lambda: True, session_id='synthetic')
    assert response['receiving'] is False and response['last_heard']['entries'] == []
    assert resource._provider is None and not opened and bytes(state.scheduler._client._stream.output) == before
    with pytest.raises(CommunityError):
        service.dispatch('/community/radio/status', {}, lambda: False, session_id='synthetic')
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()


def test_real_authenticated_facade_custody_status_is_counts_only(runtime):
    from sage_lantern.mesh_daemon import MeshDaemon
    from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
    from sage_lantern.community_service import CommunityService, CommunityError
    state, supervisor, resource, config, opened = runtime
    daemon = MeshDaemon(supervisor, resource, epoch_reader=lambda: 'e' * 32)
    connector, workers = socket_connector(daemon)
    facade = MeshVoiceFacade(connector=connector, epoch_reader=lambda: 'e' * 32)
    service = CommunityService(mesh_provider=facade)
    response = service.dispatch('/community/custody/status', {}, lambda: True, session_id='synthetic')
    assert response == dict(schema='sage.lantern.mesh-custody-status.v1', enabled=False, held=0,
                            bytes=0, recipients=0, oldest_expires_at=None, delivered=0, depositors=0)
    assert resource._provider is None and not opened
    with pytest.raises(CommunityError):
        service.dispatch('/community/custody/status', {}, lambda: False, session_id='synthetic')
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()


def test_status_body_cannot_trigger_radio_commands(runtime):
    state, supervisor, resource, config, opened = runtime
    with pytest.raises(Exception):
        resource.invoke('radio_status', {'open': True}, lambda: True)
    assert not opened


def test_status_does_not_pass_guard_revocation(runtime):
    state, supervisor, resource, config, opened = runtime
    calls = iter([True, False])
    with pytest.raises(Exception):
        resource.invoke('radio_status', {}, lambda: next(calls))
    assert not opened


def test_status_validator_rejects_private_fields_and_fake_neighbor():
    result = RadioEvents().snapshot('e' * 32, identity_verified=False, receiving=False)
    result['payload'] = 'PRIVATE'
    with pytest.raises(Exception):
        validate_status(result)
    del result['payload']
    result['neighbors']['available'] = True
    with pytest.raises(Exception):
        validate_status(result)


def test_real_enabled_receive_worker_projects_without_extra_reader(runtime):
    import time
    from test_meshcore_transport import KEY
    state, supervisor, resource, config, opened = runtime
    config['messaging_enabled'] = True
    config['radio']['public_key'] = KEY.hex()
    state.pending.append(incoming(payload=b'not an application envelope'))
    assert resource.invoke('ready', {}, lambda: True)
    deadline = time.monotonic() + 2
    response = None
    while time.monotonic() < deadline:
        response = resource.invoke('radio_status', {}, lambda: True)
        if response['last_heard']['entries']:
            break
        time.sleep(0.01)
    assert len(opened) == 1 and response['receiving']
    assert len(response['last_heard']['entries']) == 1
    assert response['last_heard']['entries'][0]['snr_db'] == -2
    assert state.transmits == []
