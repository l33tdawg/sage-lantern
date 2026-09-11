import hashlib
import importlib.util
import json
from pathlib import Path
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('endpoint_maintenance', ROOT / 'scripts/lantern/prepare-runtime-maintenance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('endpoint', ['http://127.0.0.1:8080', 'http://127.0.0.1:2527'])
def test_explicit_loopback_port(endpoint):
    assert MODULE.configured_native_port({'endpoint': endpoint}) == int(endpoint.rsplit(':', 1)[1])


@pytest.mark.parametrize('endpoint', [None, True, 'http://localhost:8080', 'http://0.0.0.0:8080',
    'https://127.0.0.1:8080', 'http://127.0.0.1:0', 'http://127.0.0.1:65536',
    'http://127.0.0.1:08080', 'http://127.0.0.1:8080/', 'http://user@127.0.0.1:8080',
    'http://127.0.0.1:8080\n'])
def test_reject_noncanonical_endpoint(endpoint):
    with pytest.raises(ValueError):
        MODULE.configured_native_port({'endpoint': endpoint})


@pytest.mark.parametrize('fault', [None, 'hash', 'actor', 'changed_config', 'listener_before',
    'listener_after', 'process_after', 'health'])
def test_native_live_uses_pinned_endpoint_and_rechecks_identity(monkeypatch, fault):
    import http.client
    from sage_lantern import mesh_gateway_root
    config = dict(endpoint='http://127.0.0.1:8080', chain_id='chain', agent_id='agent')
    raw = json.dumps(config).encode()
    process = dict(pid=123, start_ticks=456)
    unit = dict(InvocationID='invocation')
    health = dict(encrypted=True, boot_id='application-boot', version='reviewed-v27')
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    backend.expected = dict(services={MODULE.NATIVE: dict(unit=unit, process=process)},
        files={str(MODULE.MESH_CONFIG): hashlib.sha256(raw).hexdigest()}, actor=dict(chain_id='chain', agent_id='agent'),
        boot_id='host-boot', native_listener_inode='789', native_health=health)
    if fault == 'hash': backend.expected['files'][str(MODULE.MESH_CONFIG)] = '0' * 64
    if fault == 'actor': backend.expected['actor']['agent_id'] = 'different'
    reads = []
    def public(path):
        assert path == MODULE.MESH_CONFIG
        reads.append(path)
        return raw + b' ' if fault == 'changed_config' and len(reads) > 1 else raw
    backend.helper = types.SimpleNamespace(public=public, sha=lambda value: hashlib.sha256(value).hexdigest(), decode=json.loads)
    backend.show = lambda service: dict(unit)
    process_reads = []
    def observe(value):
        process_reads.append(value)
        return dict(process, pid=999) if fault == 'process_after' and len(process_reads) > 1 else dict(process)
    backend.process = observe
    ownership = []
    def owned(pin):
        ownership.append(pin)
        assert pin['role']['endpoint'] == config['endpoint']
        assert pin['backend_pin']['listener_inode'] == '789'
        return not ((fault == 'listener_before' and len(ownership) == 1)
                    or (fault == 'listener_after' and len(ownership) == 2))
    monkeypatch.setattr(mesh_gateway_root, 'backend_process_current', owned)
    requests = []
    class Connection:
        def __init__(self, host, port, timeout):
            assert host == '127.0.0.1' and timeout == 2
            self.port = port
        def request(self, method, route):
            assert method == 'GET'
            requests.append((self.port, route))
        def getresponse(self):
            result = dict(health, encrypted=False) if fault == 'health' else health
            return types.SimpleNamespace(status=200, read=lambda limit: json.dumps(result if self.port == 8080 else {}).encode())
        def close(self):
            pass
    monkeypatch.setattr(http.client, 'HTTPConnection', Connection)
    governance = []
    backend.native_observer = types.SimpleNamespace(governance=lambda *args: governance.append(args))
    if fault:
        with pytest.raises(ValueError): backend.native_live()
    else:
        backend.native_live()
        assert requests[0] == (8080, '/v1/dashboard/health')
        assert [port for port, route in requests[1:]] == [26657] * 3
        assert len(ownership) == len(reads) == len(process_reads) == 2
        assert len(governance) == 1
    if fault in ('hash', 'actor', 'listener_before'):
        assert requests == []
