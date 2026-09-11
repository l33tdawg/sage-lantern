import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location('independent_release', Path(__file__).resolve().parents[1] / 'lantern/assemble-independent-release.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'dist/../outside', './file', ''])
def test_archive_path_refusal(name):
    with pytest.raises(ValueError):
        module.safe_name(name)


def test_nonregular_ui_refused():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        member = tarfile.TarInfo('manifest.json')
        member.type = tarfile.SYMTYPE
        member.linkname = '/outside'
        archive.addfile(member)
    data.seek(0)
    with tarfile.open(fileobj=data) as archive, pytest.raises(ValueError, match='nonregular'):
        module.ui_members(archive)


def test_new_output_never_overwrites(tmp_path):
    path = tmp_path / 'payload'
    module.write_new(path, b'original')
    with pytest.raises(FileExistsError):
        module.write_new(path, b'changed')
    assert path.read_bytes() == b'original'


def test_native_version_tracks_matched_manifest_not_old_lock():
    lock = {'components': {'sage': {'version': 'old'}}}
    manifest = {'binary_sha256': module.NATIVE, 'version': 'lantern-local.20260907-fresh-v27-resident-vault'}
    module.select_native(lock, manifest)
    assert lock['components']['sage']['version'] == manifest['version']


def test_wrong_native_manifest_refused():
    with pytest.raises(ValueError):
        module.select_native({'components': {'sage': {}}}, {'binary_sha256': 'wrong', 'version': 'wrong'})
