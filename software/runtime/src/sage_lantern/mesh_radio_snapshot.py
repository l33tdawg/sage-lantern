"""Explicit local snapshot; no discovery broadcast or second serial reader."""

import threading
import time

from .mesh_radio_commission import canonical, sha, read_profile, FIRMWARE, PROTOCOL_VERSION
from .mesh_voice_ipc import require
from .meshcore_contacts import get_contacts


def validate(request, config, broker_id, epoch):
    require(type(request) is dict and set(request) == {'op', 'broker_id', 'epoch', 'config_sha256'})
    require(request['op'] == 'radio-snapshot' and request['broker_id'] == broker_id
        and request['epoch'] is not None and request['epoch'] == epoch
        and request['config_sha256'] == sha(canonical(config)))
    require(not config['messaging_enabled'] and not config['dispatch_enabled']
        and 'public_mesh' not in config and 'public_gateway' not in config)


def snapshot(resource, request, *, broker_id, epoch_reader):
    validate(request, resource.config, broker_id, epoch_reader())
    require(resource._mutex.acquire(blocking=False))
    cancel = threading.Event()
    deadline = time.monotonic() + 5
    coordinator = resource._coordinator
    def guard():
        return (not cancel.is_set() and time.monotonic() < deadline and resource.active()
            and resource._coordinator is coordinator and resource.supervisor.broker_id == broker_id
            and epoch_reader() == request['epoch']
            and sha(canonical(resource.config)) == request['config_sha256'])
    try:
        require(coordinator is not None and guard())
        def collect(client):
            require(guard() and client._key.hex() == resource.config['radio']['public_key'])
            def operation(wire_deadline):
                def exchange(payload, expected, ignored_deadline=None):
                    require(guard())
                    result = client._exchange(payload, expected, wire_deadline)
                    require(guard())
                    return result
                profile = read_profile(client, exchange)
                frame_hashes = {}
                def read_contact(deadline):
                    require(guard())
                    payload = client._frame(deadline)
                    require(guard())
                    if len(payload) == 148 and payload[0] == 3:
                        require(len(frame_hashes) < 256)
                        frame_hashes[payload[1:33]] = sha(payload)
                    return payload
                inventory = get_contacts(exchange=exchange, read_frame=read_contact,
                    deadline=wire_deadline, authorize=guard)
                require(profile == read_profile(client, exchange) and guard())
                return dict(schema='sage.lantern.radio-snapshot.v1', broker_id=broker_id,
                    epoch=request['epoch'],
                    config_sha256=request['config_sha256'], observed_at_unix_ms=int(time.time() * 1000),
                    valid_for_ms=5000, firmware=FIRMWARE, protocol_version=PROTOCOL_VERSION,
                    self_key_verified=True, firmware_verified=True, profile_readback_verified=True,
                    self_key_sha256=sha(client._key), profile=profile,
                    saved_contacts=dict(count=len(inventory.contacts), watermark=inventory.watermark,
                        entries=[dict(key_sha256=sha(contact.public_key), contact_type=contact.contact_type,
                            contact_frame_sha256=frame_hashes[contact.public_key],
                            last_modified=contact.last_modified) for contact in inventory.contacts]),
                    neighbors=dict(available=False, reason='no_fresh_last_heard_collector', entries=[]),
                    rssi_dbm=None, snr_db=None, rf_delivery_verified=False)
            return client._run(operation, sending=False)
        result = coordinator.submit(collect).result(timeout=max(0.001, deadline - time.monotonic()))
        require(guard())
        return result
    except BaseException:
        cancel.set()
        resource._uncertain = True
        raise
    finally:
        resource._mutex.release()
