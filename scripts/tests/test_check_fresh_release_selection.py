import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('fresh_selection', Path(__file__).resolve().parents[1] / 'lantern/check-fresh-release-selection.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def manifest():
    return {'files': [{'path': module.NATIVE, 'sha256': 'a' * 64}, {'path': module.RUNTIME, 'sha256': 'b' * 64}]}


def test_selected_pair_does_not_claim_unit_acceptance():
    result = module.selection(manifest(), 'a' * 64, 'b' * 64)
    assert result['identity_copy_allowed'] is False
    assert result['hardware_accepted'] is False
    assert result['automatic_startup_accepted'] is False


@pytest.mark.parametrize('native,runtime', [('c' * 64, 'b' * 64), ('a' * 64, 'c' * 64), ('latest', 'b' * 64)])
def test_mismatched_or_unpinned_pair_refused(native, runtime):
    with pytest.raises(ValueError):
        module.selection(manifest(), native, runtime)


def test_duplicate_entries_refused():
    value = manifest()
    value['files'].append(value['files'][0])
    with pytest.raises(ValueError, match='duplicate'):
        module.selection(value, 'a' * 64, 'b' * 64)
