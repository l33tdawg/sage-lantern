import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_mesh_radio_commission import request_data


@pytest.fixture
def cli(monkeypatch, request_data, tmp_path):
    path = Path(__file__).parents[1] / 'lantern/request-broker-contact.py'
    spec = importlib.util.spec_from_file_location('operator_contact', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import sage_lantern.mesh_radio_commission as journal
    monkeypatch.setattr(module, 'OPERATOR_ROOT', tmp_path / 'operator')
    monkeypatch.setattr(module, 'trusted_directory', lambda path: None)
    monkeypatch.setattr(journal, 'trusted_directory', lambda path: None)
    monkeypatch.setattr(journal, 'read', lambda path: path.read_bytes())
    config, request, contact = request_data
    import sage_lantern.mesh_daemon as daemon
    import sage_lantern.device_access as access
    monkeypatch.setattr(daemon, 'load_config', lambda: config)
    monkeypatch.setattr(access, 'access_epoch', lambda: request['epoch'])
    monkeypatch.setattr(module, 'process', lambda pin: None)
    sent, closed = [], []
    connection = SimpleNamespace(settimeout=lambda timeout: None, close=lambda: closed.append(True))
    monkeypatch.setattr(module, 'connect', lambda: connection)
    monkeypatch.setattr(module, 'peer', lambda connection: (123, 0))
    monkeypatch.setattr(module, 'send', lambda connection, request: sent.append(request))
    monkeypatch.setattr(module, 'receive', lambda connection: {'result': {'readback_verified': True}})
    return module, dict(broker_process=dict(pid=123, start_ticks='456'), request=request), sent, closed


def test_default_has_no_ipc(cli, monkeypatch):
    module, envelope, sent, closed = cli
    monkeypatch.setattr(module, 'connect', lambda: pytest.fail('read-only validation must not connect'))
    assert module.run(envelope) == dict(validated=True, dispatched=False)
    assert sent == []


@pytest.mark.parametrize('peer', [(123, 994), (124, 0)])
def test_wrong_socket_owner_no_dispatch(cli, monkeypatch, peer):
    module, envelope, sent, closed = cli
    monkeypatch.setattr(module, 'peer', lambda connection: peer)
    with pytest.raises(Exception):
        module.run(envelope, True)
    assert not sent and closed == [True]


def test_response_lost_no_retry(cli, monkeypatch):
    module, envelope, sent, closed = cli
    def lost(connection):
        raise TimeoutError()
    monkeypatch.setattr(module, 'receive', lost)
    with pytest.raises(TimeoutError):
        module.run(envelope, True)
    assert len(sent) == 1 and closed == [True]
    attempts = list(module.OPERATOR_ROOT.iterdir())
    assert len(attempts) == 1 and (attempts[0] / 'intent.json').exists()
    assert not (attempts[0] / 'response.json').exists()
    with pytest.raises(FileExistsError):
        module.run(envelope, True)
    assert len(sent) == 1


def test_existing_import_contact_exact_request(cli):
    module, envelope, sent, closed = cli
    request = envelope['request']
    envelope['request'] = {key: request[key] for key in ('broker_id', 'epoch', 'config_sha256', 'card', 'card_sha256')}
    envelope['request']['op'] = 'import-contact'
    assert module.run(envelope, True)['response_confirmed']
    assert sent == [envelope['request']]


def test_root_route_rejects_nonroot_before_operation(monkeypatch):
    import sage_lantern.mesh_daemon as daemon
    monkeypatch.setattr(daemon, 'receive', lambda connection: {'op': 'commission-radio'})
    instance = object.__new__(daemon.MeshDaemon)
    with pytest.raises(Exception):
        instance.handle(object(), pid=123, uid=994)
