import struct

import pytest

from sage_lantern.mesh_radio_snapshot import snapshot
from sage_lantern.mesh_serial_owner import SerialScheduler
from test_mesh_radio_commission import request_data, profile_bytes, resource_for
from test_meshcore_transport import client, frame, encode_frame


def test_complete_snapshot_no_names_or_neighbor_inference(request_data):
    config, request, contact = request_data
    from sage_lantern.mesh_radio_commission import sha
    raw = profile_bytes(request) + frame(b'\x02' + struct.pack('<I', 1)) + frame(contact)
    raw += frame(b'\x04' + contact[144:148]) + profile_bytes(request)
    device, stream = client(raw)
    scheduler = SerialScheduler(device, release=lambda: None)
    resource = resource_for(config, scheduler)
    query = dict(op='radio-snapshot', broker_id=request['broker_id'], epoch=request['epoch'], config_sha256=request['config_sha256'])
    try:
        result = snapshot(resource, query, broker_id=request['broker_id'], epoch_reader=lambda: request['epoch'])
        assert result['saved_contacts']['count'] == 1
        assert result['saved_contacts']['entries'][0]['contact_frame_sha256'] == sha(contact)
        assert result['neighbors'] == dict(available=False, reason='no_fresh_last_heard_collector', entries=[])
        assert result['rssi_dbm'] is None and result['snr_db'] is None
        assert result['observed_at_unix_ms'] > 0 and result['valid_for_ms'] == 5000
        assert not result['rf_delivery_verified'] and result['self_key_verified']
        assert 'Synthetic' not in str(result) and contact[1:33].hex() not in str(result)
        queries = encode_frame(bytes((22, 13))) + encode_frame(b'\x01' + bytes(7) + b'Lantern')
        assert bytes(stream.output) == queries + encode_frame(b'\x04') + queries
    finally:
        scheduler.close(timeout=2)


@pytest.mark.parametrize('failure', ['missing_owner', 'locked', 'wrong_broker', 'enabled', 'partial'])
def test_snapshot_refuses_without_partial_success(request_data, failure):
    config, request, contact = request_data
    device, stream = client(profile_bytes(request) + frame(b'\x02' + struct.pack('<I', 1)))
    scheduler = SerialScheduler(device, release=lambda: None)
    resource = resource_for(config, None if failure == 'missing_owner' else scheduler)
    query = dict(op='radio-snapshot', broker_id=request['broker_id'], epoch=request['epoch'], config_sha256=request['config_sha256'])
    if failure == 'wrong_broker':
        query['broker_id'] = 'c' * 32
    if failure == 'enabled':
        config['messaging_enabled'] = True
    try:
        with pytest.raises(Exception):
            snapshot(resource, query, broker_id=request['broker_id'],
                epoch_reader=lambda: None if failure == 'locked' else request['epoch'])
        if failure != 'partial':
            assert not stream.output
    finally:
        scheduler.close(timeout=2)
