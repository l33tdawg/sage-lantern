import copy
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import sage_sdk
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'software/runtime/tests'))
from test_mesh_contact_owner import owner
from test_maintenance_owner_ledgers import attempt
from test_recover_cross_boot_audio import MODULE as helper, SLOT, records
from sage_lantern import mesh_daemon, device_access


def load(name):
    path = Path(__file__).resolve().parents[1] / 'lantern' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


collector = load('collect-cross-boot-audio-pins')
maintenance = load('prepare-runtime-maintenance')
vision = load('collect-fresh-vision-inputs')


def test_real_signed_collector_observation_then_contained_maintenance(owner, monkeypatch, tmp_path):
    _, ledger, state = owner
    correction = attempt(ledger, 'accepted')
    payload = dict(schema=records.CONTROL_SCHEMA, binding=ledger.owner, enabled=True,
        generation=SLOT, history_generation=None, dispatch_epoch=SLOT, clear_epoch=SLOT, suppressed=[])
    control = dict(correction, kind='conversation_control', record_id=records.record_id(ledger.owner), payload=payload)
    state['rows'] = {row['record_id']: row for row in [control, correction]}
    before = copy.deepcopy(state['rows'])
    monkeypatch.setattr(mesh_daemon, 'load_config', lambda: dict(ledger.owner,
        endpoint='http://127.0.0.1:19100', receiver_slot=SLOT))
    monkeypatch.setattr(mesh_daemon, 'load_identity', lambda config: (ledger.identity, os.open(os.devnull, os.O_RDONLY)))
    monkeypatch.setattr(device_access, 'access_epoch', lambda: 'synthetic-epoch')
    client = ledger.store.client
    class BorrowedClient:
        def __enter__(self):
            return client
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(sage_sdk, 'SageClient', lambda *args, **kwargs: BorrowedClient())
    expected = dict(actor=ledger.owner)
    live_checks = []
    def current():
        live_checks.append(True)
    epoch, digest = vision.observe_workflows(collector, helper, current, expected)
    assert digest == helper.sha(helper.canonical(sorted(before.values(), key=lambda row: row['record_id'])))
    with pytest.raises(AttributeError):
        collector.signed_inventory(helper, SimpleNamespace(current=current), expected)
    backend = maintenance.Maintenance.__new__(maintenance.Maintenance)
    backend.expected = dict(expected, storage_epoch=epoch, workflow_sha256=digest)
    backend.helper, backend.collector, backend.current = helper, collector, current
    backend.stopped = set()
    assert backend.reconcile() == dict(storage_epoch=epoch, workflow_sha256=digest)
    backend.stopped = {maintenance.VOICE}
    with pytest.raises(ValueError):
        backend.reconcile()
    backend.stopped = set(maintenance.STOP)
    backend.guarded = set(maintenance.STOP)
    backend.running = {}
    path = tmp_path / 'owner.lock'
    path.write_bytes(b'')
    descriptor = os.open(path, os.O_RDONLY)
    backend.tree = {'owner.lock': dict(metadata=helper.metadata(os.fstat(descriptor)))}
    backend.locks = [descriptor]
    checked = []
    backend.show = lambda service: {'state': 'inactive'}
    def empty(service, value):
        assert value == {'state': 'inactive'}
        checked.append(service)
    backend.empty = empty
    try:
        assert backend.reconcile() == dict(storage_epoch=epoch, workflow_sha256=digest)
        assert set(checked) == set(maintenance.STOP)
        backend.locks = []
        with pytest.raises(ValueError):
            backend.reconcile()
        backend.locks = [descriptor]
        backend.show = lambda service: {'state': 'active'}
        with pytest.raises(AssertionError):
            backend.reconcile()
        backend.show = lambda service: {'state': 'inactive'}
        backend.stopped = set(maintenance.STOP + maintenance.BASIC + (maintenance.AUTH,))
        backend.running = {maintenance.AUTH: {}, maintenance.UI: {}}
        backend.installed = backend.post_auth = True
        backend.handoff_sha256 = 'synthetic-handoff'
        backend.activation_access = 'synthetic-epoch'
        backend.audio_owner = None
        backend.current_after_auth = current
        backend.guarded = {maintenance.VOICE, maintenance.BROKER}
        with pytest.raises(ValueError):
            backend.reconcile()
        assert backend.reconcile_audio() == dict(storage_epoch=epoch, workflow_sha256=digest)
        for attribute, invalid in (
            ('installed', False), ('post_auth', False), ('handoff_sha256', None),
            ('activation_access', None), ('audio_owner', object()), ('locks', []),
            ('running', {maintenance.AUTH: {}, maintenance.UI: {}, maintenance.VOICE: {}}),
            ('guarded', {maintenance.BROKER}), ('stopped', set(maintenance.STOP)),
        ):
            original = getattr(backend, attribute)
            setattr(backend, attribute, invalid)
            with pytest.raises(ValueError):
                backend.reconcile_audio()
            setattr(backend, attribute, original)
        from sage_lantern.deployment_bringup import ColdBrokerBootstrap
        prestart = ColdBrokerBootstrap.__new__(ColdBrokerBootstrap)
        backend.expected['audio_inventory'] = {'synthetic-audio': 'a' * 64}
        prestart.inventory = dict(backend.expected['audio_inventory'])
        fresh_calls = []
        monkeypatch.setattr(ColdBrokerBootstrap, 'fresh', lambda self: fresh_calls.append(True))
        backend.guarded = set()
        with pytest.raises(ValueError):
            backend.reconcile_audio()
        assert backend.reconcile_audio(prestart_owner=prestart) == dict(
            storage_epoch=epoch, workflow_sha256=digest)
        assert len(fresh_calls) >= 4
        prestart.inventory = {}
        with pytest.raises(ValueError):
            backend.reconcile_audio(prestart_owner=prestart)
        prestart.inventory = dict(backend.expected['audio_inventory'])
        with pytest.raises(ValueError):
            backend.reconcile_audio(prestart_owner=SimpleNamespace(fresh=lambda: True))
        backend.guarded = {maintenance.VOICE}
        with pytest.raises(ValueError):
            backend.reconcile_audio(prestart_owner=prestart)
        backend.guarded = set()
        def changed_authority():
            raise ValueError('changed-authority')
        backend.current_after_auth = changed_authority
        with pytest.raises(ValueError, match='changed-authority'):
            backend.reconcile_audio(prestart_owner=prestart)
    finally:
        os.close(descriptor)
    assert state['rows'] == before and state['puts'] == 0 and len(live_checks) > 4
    transport = client._client._transport
    scans = []
    def changed_scan(request):
        if request.url.path == '/v1/workflows':
            scans.append(True)
            if len(scans) == 2:
                state['rows'][correction['record_id']]['revision'] += 1
        return transport.handle_request(request)
    client._client._transport = httpx.MockTransport(changed_scan)
    with pytest.raises(ValueError):
        vision.observe_workflows(collector, helper, current, expected)
    assert len(scans) == 2 and state['puts'] == 0
