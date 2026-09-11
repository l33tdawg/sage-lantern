import importlib.util
import json
import os
from pathlib import Path

import pytest
import yaml


SPEC = importlib.util.spec_from_file_location('b_policy',
    Path(__file__).resolve().parents[1] / 'lantern/prepare-b-private-policy.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture
def inputs(monkeypatch):
    value = dict(chain_id=module.CHAIN,
        data_dir='/var/lib/sage-lantern/sage-personal/data',
        agent_key_file='/var/lib/sage-lantern/sage-personal/agent.key',
        rest_addr='127.0.0.1:8080', encryption={'enabled': True},
        private_media={'enabled': True, 'actor_bytes': 100},
        embedding={'provider': 'hash', 'dimension': 768},
        federation={'enabled': False, 'p2p_enabled': True, 'p2p_force_private': True,
            'p2p_relay_addrs': ['fixture-relay']}, quorum={'enabled': False},
        voter={'enabled': True, 'poll_interval': '2s'},
        vendored_agent_bootstrap={'agent_key_file': '/var/lib/sage-lantern/identity/lantern-agent.key',
            'home_domain': 'local-field-knowledge', 'clearance': 1})
    raw = yaml.safe_dump(value).encode()
    receipt = json.dumps(dict(schema='sage.lantern.initialized.v1', unit_id='unit-b',
        root_id=module.ROOT, chain_id=module.CHAIN)).encode()
    monkeypatch.setattr(module, 'CURRENT', module.digest(raw))
    monkeypatch.setattr(module, 'INITIALIZED', module.digest(receipt))
    return raw, receipt


def correct(inputs):
    raw, receipt = inputs
    return module.corrected(raw, receipt, module.CURRENT)


def test_only_reviewed_policy_fields_change(inputs):
    before = yaml.safe_load(inputs[0])
    after = yaml.safe_load(correct(inputs))
    assert after['federation'] == {'enabled': False, 'p2p_enabled': False,
        'p2p_force_private': False, 'p2p_relay_addrs': []}
    assert after['voter']['required'] is True
    after['federation'] = before['federation']
    after['voter'].pop('required')
    assert after == before


@pytest.mark.parametrize('which', ['policy', 'receipt'])
def test_changed_input_refused(inputs, which):
    raw, receipt = inputs
    with pytest.raises(ValueError):
        module.corrected(raw + (b'\n' if which == 'policy' else b''),
            receipt + (b'\n' if which == 'receipt' else b''), module.CURRENT)


def test_cannot_substitute_a_new_current_pin(inputs):
    with pytest.raises(ValueError):
        module.corrected(*inputs, '0' * 64)


@pytest.mark.parametrize('field,value', [('unit_id', 'unit-a'), ('root_id', 'a' * 64),
    ('chain_id', 'sage-personal-other')])
def test_independent_identity_required(inputs, monkeypatch, field, value):
    receipt = json.loads(inputs[1])
    receipt[field] = value
    raw = json.dumps(receipt).encode()
    monkeypatch.setattr(module, 'INITIALIZED', module.digest(raw))
    with pytest.raises(ValueError):
        module.corrected(inputs[0], raw, module.CURRENT)


def test_no_apply_or_restart_entry_point():
    source = Path(SPEC.origin).read_text()
    assert 'systemctl' not in source
    assert 'HTTPConnection' not in source
    assert "add_argument('--apply'" not in source


def test_native_patch_preserves_defaults():
    source = Path(SPEC.origin).with_name('lantern-private-policy-roundtrip.patch').read_text()
    assert 'defaultFederationConfig' not in source
    for name in ('p2p_enabled', 'p2p_relay_addrs', 'p2p_force_private'):
        assert f'`yaml:"{name},omitempty"`' in source
        assert f'`yaml:"{name}"`' in source


def test_directory_is_descriptor_anchored_and_rejects_links(tmp_path):
    descriptor = module.directory(tmp_path, {0, os.geteuid()})
    try:
        assert os.fstat(descriptor).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(descriptor)
    linked = tmp_path / 'link'
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        module.directory(linked, {0, os.geteuid()})


def test_stage_is_exclusive_private_and_preserves_baseline(tmp_path, monkeypatch):
    real_directory = module.directory
    def directory(path, owners):
        assert owners == {0}
        return real_directory(path, {0, os.geteuid()})
    monkeypatch.setattr(module, 'directory', directory)
    output = tmp_path / 'exclusive'
    report = module.stage(output, b'baseline', b'candidate')
    assert report['installed'] is False and report['restart_requested'] is False
    assert (output / 'before.yaml').read_bytes() == b'baseline'
    assert (output.stat().st_mode & 0o777) == 0o700
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in output.iterdir())
    with pytest.raises(FileExistsError):
        module.stage(output, b'changed', b'changed')
    assert (output / 'before.yaml').read_bytes() == b'baseline'


def test_unknown_directory_owner_refused(tmp_path):
    if os.geteuid() == 0:
        pytest.skip('test requires non-root fixture owner')
    with pytest.raises(ValueError):
        module.directory(tmp_path, {0})
