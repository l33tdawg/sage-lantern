import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest


SPEC = importlib.util.spec_from_file_location('combined_candidate', Path(__file__).resolve().parents[1]/'lantern/assemble-postcommission-candidate.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def archive(name='server/index.js', link=False, duplicate=False):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as writer:
        for index in range(2 if duplicate else 1):
            info = tarfile.TarInfo(name)
            info.size = 2
            if link:
                info.type = tarfile.SYMTYPE
                info.linkname = '/other'
            writer.addfile(info, io.BytesIO(b'ui'))
    stream.seek(0)
    return tarfile.open(fileobj=stream)


def test_exact_ui_payload():
    with archive() as reader:
        assert list(module.ui_payload(reader, {'server/index.js': hashlib.sha256(b'ui').hexdigest()})) == [('server/index.js', b'ui')]


@pytest.mark.parametrize('options', [{'name': '../outside'}, {'name': '/outside'}, {'link': True}, {'duplicate': True}])
def test_unsafe_ui_refused(options):
    with archive(**options) as reader, pytest.raises(ValueError):
        list(module.ui_payload(reader, {'server/index.js': hashlib.sha256(b'ui').hexdigest()}))


def test_metrics_and_compact_are_explicit():
    assert module.PINS['runtime'] == 'bbc7f20f7faaa505455906e5fc270ee97129076f1f5c1caa2c2a93d23a1fc443'
    assert module.PINS['runtime_audit'] == 'b8477b245bf1cfc9dbf6beb7cdec3769e0924dc64c24d89507f5ba36bcddee2b'
    assert module.CONTEXT == '5129ad541c7ac7e3ccdaf86cdbe8c7587ca895f7d8058a7ca0467fa7bf2b815b'
