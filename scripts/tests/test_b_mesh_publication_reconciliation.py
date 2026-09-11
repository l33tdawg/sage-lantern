import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('mesh_reconciliation',
    Path(__file__).resolve().parents[1] / 'lantern/handoff-b-policy-guards.py')
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


@pytest.mark.parametrize('failed', [False, True])
def test_reconciliation_checks_once_never_republishes_or_removes(failed):
    events = []
    def check():
        events.append('check')
        if failed:
            raise RuntimeError('unknown historical cause')
    backend = SimpleNamespace(phase='mesh', expected={'partial_mesh': {}},
        claim=lambda: events.append('claim'), check=check,
        record=lambda phase, value: events.append(phase))
    if failed:
        with pytest.raises(RuntimeError):
            helper.reconcile_mesh(backend)
        assert events[-1] == 'reconciliation_failed'
        assert 'complete' not in events
    else:
        assert helper.reconcile_mesh(backend)['original_failed'] is True
        assert events[-1] == 'complete'
    assert events.count('check') == 1 and events[0] == 'claim'


def test_regular_transfer_cannot_replay_partial_attempt():
    with pytest.raises(RuntimeError):
        helper.transfer(SimpleNamespace(expected={'partial_mesh': {}}))


@pytest.mark.parametrize('fault', [None, 'changed', 'extra', 'fake_complete', 'wrong_failure'])
def test_failed_inventory_stays_exact(monkeypatch, fault):
    raw = helper.encoded({'old': 'intent'})
    failure = helper.encoded(dict(phase='failed_contained', path=None, expected_sha256=helper.sha(raw)))
    contents = {'intent.json': raw, '123-failed_contained.json': failure,
        **{str(index) + '.guard': b'preserved' for index in range(6)}}
    document = dict(schema='sage.lantern.b-failed-mesh-inventory.v1',
        files={name: helper.sha(value) for name, value in contents.items()})
    monkeypatch.setattr(helper, 'FAILED_MESH_INVENTORY_SHA', helper.sha(helper.encoded(document)))
    monkeypatch.setattr(helper, 'FAILED_MESH_SHA', helper.sha(failure))
    if fault == 'changed':
        contents['0.guard'] = b'changed'
    elif fault in ('extra', 'fake_complete'):
        contents['complete.json' if fault == 'fake_complete' else 'extra'] = b'fake'
    elif fault == 'wrong_failure':
        monkeypatch.setattr(helper, 'FAILED_MESH_SHA', 'wrong')
    monkeypatch.setattr(helper, 'pinned', lambda pin: helper.encoded(document))
    monkeypatch.setattr(helper, 'read', lambda path: contents[path.name])
    monkeypatch.setattr(Path, 'iterdir', lambda path: iter(path / name for name in contents))
    if fault:
        with pytest.raises(RuntimeError):
            helper.failed_mesh_inventory({})
    else:
        assert helper.failed_mesh_inventory({}) == contents
