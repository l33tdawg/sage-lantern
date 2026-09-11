import importlib.util
from pathlib import Path
import json

import pytest
import yaml

SPEC = importlib.util.spec_from_file_location('b_identity_inputs', Path(__file__).resolve().parents[1] / 'lantern/prepare-b-identity-inputs.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
RUNTIME = b'[sage]\nbase_url="http://127.0.0.1:8080"\nidentity_path="/var/lib/sage-lantern/identity/lantern-agent.key"\ndomain="local-field-knowledge"\n'


def quotas():
    return {name: 1024 for name in module.QUOTAS}


def test_fresh_b_no_chain_or_seed():
    files = module.prepare(RUNTIME, module.digest(RUNTIME), '/opt/lantern-b-identity-inputs', quotas())
    plan = json.loads(files['commission-plan.json'])
    assert plan['unit_id'] == 'unit-b' and plan['quotas'] == quotas()
    assert b'chain_id' not in files['initial-sage.yaml']
    assert b'seed:' not in files['initial-sage.yaml']
    assert files['runtime-template.toml'] == RUNTIME


def test_explicit_private_transport_and_required_voter():
    policy = yaml.safe_load(module.YAML)
    assert policy['federation'] == {'enabled': False, 'p2p_enabled': False,
        'p2p_relay_addrs': [], 'p2p_force_private': False}
    assert policy['voter'] == {'enabled': True, 'required': True}
    assert policy['quorum'] == {'enabled': False}


@pytest.mark.parametrize('value', [None, {}, {'actor_bytes': True}])
def test_missing_quotas_refused(value):
    with pytest.raises(ValueError):
        module.prepare(RUNTIME, module.digest(RUNTIME), '/opt/b', value)


def test_copied_chain_and_changed_runtime_refused():
    for raw, checksum in ((RUNTIME, 'wrong'), (RUNTIME + b'chain_id="A"\n', module.digest(RUNTIME + b'chain_id="A"\n'))):
        with pytest.raises(ValueError):
            module.prepare(raw, checksum, '/opt/b', quotas())


def test_quota_order():
    value = quotas()
    value['actor_bytes'] += 1
    with pytest.raises(ValueError):
        module.prepare(RUNTIME, module.digest(RUNTIME), '/opt/b', value)
