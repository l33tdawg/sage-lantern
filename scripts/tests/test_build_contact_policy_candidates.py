import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('contact_policy_build', Path(__file__).resolve().parents[1]/'lantern/build-contact-policy-candidates.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_narrow_overlay_preserves_private_policy_and_resident_key():
    base = {'workflow.go': b'old', 'vault.go': b'resident', 'policy.go': b'explicit private'}
    result = module.merge(base, {'workflow.go': b'contact'}, {'workflow.go': module.sha(b'old')})
    assert result == dict(base, **{'workflow.go': b'contact'})
    assert base['workflow.go'] == b'old'


@pytest.mark.parametrize('additions', [{'workflow.go': b'contact', 'vault.go': b'old vault'}, {}, {'other.go': b'contact'}])
def test_wholesale_or_extra_source_replacement_refused(additions):
    with pytest.raises(ValueError):
        module.merge({'workflow.go': b'old', 'vault.go': b'resident'}, additions,
            {'workflow.go': module.sha(b'old')})


def test_wrong_native_base_refused():
    with pytest.raises(ValueError):
        module.merge({'workflow.go': b'unreviewed'}, {'workflow.go': b'contact'},
            {'workflow.go': module.sha(b'old')})
