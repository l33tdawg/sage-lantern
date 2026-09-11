import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('independent_package', Path(__file__).resolve().parents[1] / 'lantern/package-independent-release.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_exact_tree(tmp_path):
    (tmp_path / 'source.py').write_text('pass\n')
    module.exact_files(tmp_path, {'source.py'})


@pytest.mark.parametrize('name', ['__pycache__/source.cpython-312.pyc', 'state/history.json', 'unlisted.py'])
def test_extra_files_refused_not_filtered(tmp_path, name):
    (tmp_path / 'source.py').write_text('pass\n')
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'contamination')
    with pytest.raises(ValueError):
        module.exact_files(tmp_path, {'source.py'})
    assert path.read_bytes() == b'contamination'


def test_empty_cache_directory_refused(tmp_path):
    (tmp_path / '__pycache__').mkdir()
    with pytest.raises(ValueError):
        module.exact_files(tmp_path, set())


def test_sidecar_runner_disables_bytecode_and_pytest_cache():
    source = (Path(__file__).resolve().parents[1] / 'lantern/test-startup-sidecar-source.sh').read_text()
    assert 'PYTHONDONTWRITEBYTECODE=1' in source
    assert '-B -m pytest' in source and '-p no:cacheprovider' in source
