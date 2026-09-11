import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


SPEC = importlib.util.spec_from_file_location('prepare_new_release', Path(__file__).resolve().parents[1]/'lantern/prepare-postcommission-release.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def audit():
    return dict(schema='sage.lantern.public-share-build-review.v1', identical_builds=True,
        ui_dist_built=False, sdk_native_selected=False, wheel_sha256='a'*64,
        source_sha256={'software/runtime/src/sage_lantern/__init__.py': 'b'*64},
        reviewed_companions={'scripts/lantern/unit-a-voice-loop.py': 'c'*64})


def test_public_future_closure_not_implicitly_selected():
    value = audit()
    sources = {**value['source_sha256'], **value['reviewed_companions']}
    assert module.public_gaps(value, sources, 'a'*64) == ['sdk_native_selected', 'ui_dist_built']


@pytest.mark.parametrize('group', ['source_sha256', 'reviewed_companions'])
def test_public_companion_or_runtime_mismatch(group):
    value = audit()
    sources = {**value['source_sha256'], **value['reviewed_companions']}
    name = next(iter(value[group]))
    sources[name] = 'd'*64
    assert 'public_source_mismatch:'+name in module.public_gaps(value, sources, 'a'*64)


def test_old_r7_wheel_cannot_stand_in_for_public_wheel():
    assert 'public_runtime_wheel_mismatch' in module.public_gaps(audit(), {}, 'f'*64)


def test_wheel_source_set_and_hash_must_match(tmp_path):
    wheel = tmp_path/'sage_lantern_runtime-0.2.0-py3-none-any.whl'
    recorded = {'src/sage_lantern/__init__.py': 'a'*64, 'pyproject.toml': 'b'*64}
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('sage_lantern/release-manifest.json', json.dumps(dict(source_sha256=recorded)))
    sources = {'software/runtime/'+name: value for name, value in recorded.items()}
    assert module.runtime_gaps(wheel, sources) == []
    sources['software/runtime/src/sage_lantern/__init__.py'] = 'c'*64
    sources['software/runtime/src/sage_lantern/new.py'] = 'd'*64
    assert module.runtime_gaps(wheel, sources) == ['src/sage_lantern/__init__.py', 'src/sage_lantern/new.py']


def test_canonical_required_entries_are_nonactivating():
    stage = module.load_stage()
    rows = {row['path']: row for row in stage.required_sources()}
    for name in module.GENERIC | {'scripts/lantern/prepare-postcommission-release.py'}:
        assert rows[name]['operation'] == 'review_only'
        assert rows[name]['destination'] is None
