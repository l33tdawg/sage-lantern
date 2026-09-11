import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('final_inhibitor',
    Path(__file__).resolve().parents[1] / 'lantern/release-b-reconciled-inhibitor.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_exact_verifier_binding():
    import hashlib
    source = Path(module.__file__).with_name('handoff-b-policy-guards.py').read_bytes()
    assert hashlib.sha256(source).hexdigest() == module.VERIFIER_SHA


@pytest.mark.parametrize('fault', [None, 'acquire_check', 'validate_completion', 'old_status',
    'stop_old', 'wait_old_remasked', 'archive_masks', 'remove_mask', 'final_idle'])
def test_transfer_requires_inhibition_before_stopping_old_and_final_authority(fault):
    events = []
    def call(name, *args):
        events.append(name)
        if name == fault:
            raise RuntimeError('contained test failure')
    backend = SimpleNamespace(targets=('sleep.target', 'suspend.target', 'hibernate.target', 'hybrid-sleep.target'),
        record=lambda name, value: events.append(name))
    for name in ('acquire_check', 'validate_completion', 'old_status', 'stop_old',
            'wait_old_remasked', 'archive_masks', 'remove_mask', 'final_idle'):
        setattr(backend, name, lambda *args, name=name: call(name, *args))
    if fault:
        with pytest.raises(RuntimeError):
            module.transfer(backend)
        assert 'release-authorized' not in events
    else:
        module.transfer(backend)
        assert events.index('acquire_check') < events.index('old-holder-stop-intent') < events.index('stop_old')
        assert events.index('wait_old_remasked') < events.index('remove_mask')
        assert events.count('remove_mask') == 4 and events.count('validate_completion') == 2
        assert events[-3:] == ['final_idle', 'acquire_check', 'release-authorized']
