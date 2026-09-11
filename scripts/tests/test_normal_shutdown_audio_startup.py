"""Use fence bytes emitted by the real supervisor clean-shutdown methods."""

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software/runtime/src'))
from sage_lantern.storage_supervisor import RootStorageSupervisor


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RECOVERY_TESTS = load('shutdown_recovery_tests', ROOT / 'scripts/tests/test_recover_cross_boot_audio.py')
ADAPTER = load('shutdown_adapter', ROOT / 'scripts/lantern/startup-audio-adapter.py')
supervisor = RECOVERY_TESTS.supervisor
current_backend = RECOVERY_TESTS.current_backend


@pytest.fixture(params=[False, True], ids=['interrupted-stopping', 'stopped-verified'])
def clean_shutdown_fence(tmp_path, request):
    directory = tmp_path.resolve() / 'real-supervisor'
    directory.mkdir(mode=0o700)
    owner = RootStorageSupervisor(directory, backend=None, snapshot=None, access_epoch=None,
                                  owner_uid=os.geteuid())
    try:
        assert owner.begin_shutdown(1) is True
        if request.param:
            assert owner.finish_shutdown(1) is True
        raw = (directory / 'fence.json').read_bytes()
        assert json.loads(raw) == dict(schema='sage.lantern.storage-supervisor.v1',
                                      broker_id=owner.broker_id,
                                      phase='stopped_verified' if request.param else 'stopping')
    finally:
        owner.close()
    return raw


def test_real_clean_shutdown_fence_is_accepted_by_adapter(clean_shutdown_fence):
    fence = json.loads(clean_shutdown_fence)
    prior = dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete',
        boot_id='fa412850-b01f-4a1f-973b-a4c508f65417', pid=100, inventory={},
        evidence=dict(broker_id=fence['broker_id'], vault_epoch='a' * 64 + ':2',
                      broker_pid=101, voice_pid=102))
    before = json.dumps(prior, sort_keys=True)
    ADAPTER.validate_prior(prior, fence, {'inventory': {}}, 'd741af22-1a90-4779-bbd2-fe263c032819')
    assert json.dumps(prior, sort_keys=True) == before


@pytest.mark.parametrize('fault', ['same_boot', 'broker', 'unknown_phase'])
def test_shutdown_state_does_not_relax_previous_lifetime_binding(clean_shutdown_fence, fault):
    fence = json.loads(clean_shutdown_fence)
    boot = 'd741af22-1a90-4779-bbd2-fe263c032819'
    prior = dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete',
        boot_id='fa412850-b01f-4a1f-973b-a4c508f65417', pid=100, inventory={},
        evidence=dict(broker_id=fence['broker_id'], vault_epoch='a' * 64 + ':2',
                      broker_pid=101, voice_pid=102))
    if fault == 'same_boot':
        prior['boot_id'] = boot
    elif fault == 'broker':
        fence['broker_id'] = '0' * 32
    else:
        fence['phase'] = 'unknown'
    with pytest.raises(ValueError):
        ADAPTER.validate_prior(prior, fence, {'inventory': {}}, boot)


def test_real_clean_shutdown_fence_survives_current_and_archive(clean_shutdown_fence, current_backend):
    backend, source = current_backend
    module = RECOVERY_TESTS.MODULE
    fence_path = module.SUPERVISOR / 'fence.json'
    fence_path.write_bytes(clean_shutdown_fence)
    backend.expected['tree'] = module.tree(module.SUPERVISOR)
    backend.current()
    backend.lock()
    try:
        backend.inhibit()
        backend.archive()
        backend.current()
        assert (backend.location() / 'fence.json').read_bytes() == clean_shutdown_fence
        assert not module.SUPERVISOR.exists()
    finally:
        backend.close()
