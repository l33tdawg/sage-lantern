import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('first_handoff', Path(__file__).resolve().parents[1] / 'lantern/handoff-first-audio-lifetime.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
BOOT = '11111111-1111-4111-8111-111111111111'


def tools():
    return {name: {'path': '/opt/sage-lantern/libexec/' + name, 'sha256': 'a' * 64}
            for name in ('coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel')}


def plan():
    return dict(unit_id='unit-b', boot_id=BOOT, bootstrap_sha256=module.sha(b'real-receipt'), baseline={'actor': {'agent_id': 'B'}})


def test_b_only_both_metadata_and_config():
    module.validate_unit(b'UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n', {'unit_id': 'unit-b'})
    for metadata, config in [(b'UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n', {'unit_id': 'unit-b'}),
        (b'UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n', {'unit_id': 'unit-a'}),
        (b'UNIT_ID=unit-b\nUNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n', {'unit_id': 'unit-b'})]:
        with pytest.raises(ValueError):
            module.validate_unit(metadata, config)


def test_documents_link_exact_b_receipt_and_baseline():
    files = module.documents(plan(), tools(), b'real-receipt')
    directory = module.HANDOFF / BOOT
    policy = module.decode(files[directory / 'policy.json'])
    config = module.decode(files[directory / 'startup.json'])
    assert policy['baseline']['sha256'] == module.sha(files[directory / 'baseline.json'])
    assert config['policy']['sha256'] == module.sha(files[directory / 'policy.json'])
    assert config['initial_lifetime'] == {'path': str(module.LIFETIMES / BOOT / 'new-lifetime.json'), 'sha256': module.sha(b'real-receipt')}
    assert set(policy) == {'schema', 'helper', 'collector', 'runtime_wheel', 'baseline'}
    assert set(config) == {'schema', 'adapter', 'coordinator', 'policy', 'initial_lifetime'}


def test_a_or_changed_receipt_refused():
    changed = plan()
    changed['unit_id'] = 'unit-a'
    for value, record in ((changed, b'real-receipt'), (plan(), b'changed')):
        with pytest.raises(ValueError):
            module.documents(value, tools(), record)


class Backend:
    def __init__(self, change=False, fail=False):
        self.tools = tools()
        self.coordinator = SimpleNamespace(validate_full_config=lambda value: None)
        self.calls = []
        self.change, self.fail = change, fail

    def snapshot(self):
        self.calls.append('snapshot')
        current = plan()
        if self.change and self.calls.count('snapshot') > 1:
            current['changed'] = True
        return current, b'real-receipt'

    def claim(self, value):
        self.calls.append('claim')

    def write(self, path, raw):
        self.calls.append(path.name)
        if self.fail:
            raise OSError('disk full')


def test_success_only_after_postcheck():
    backend = Backend()
    result = module.apply(backend, plan())
    assert result['services_enabled'] is False and result['services_started'] is False
    assert result['reboot_accepted'] is False
    assert backend.calls[-3:] == ['snapshot', 'startup.json', 'complete.json']


@pytest.mark.parametrize('change,fail', [(True, False), (False, True)])
def test_partial_failure_never_claims_complete_or_retries(change, fail):
    backend = Backend(change, fail)
    with pytest.raises((ValueError, OSError)):
        module.apply(backend, plan())
    assert 'complete.json' not in backend.calls
    assert 'startup.json' not in backend.calls
    assert backend.calls.count('claim') == 1


def test_stale_reviewed_plan_prevents_any_write():
    backend = Backend()
    changed = plan()
    changed['boot_id'] = 'old'
    with pytest.raises(ValueError):
        module.apply(backend, changed)
    assert backend.calls == ['snapshot']


def test_no_start_enable_or_identity_generation():
    source = Path(module.__file__).read_text()
    for forbidden in ('systemctl', 'owner.start(', 'load_identity(', 'init-lantern-private', 'workflow_put('):
        assert forbidden not in source
