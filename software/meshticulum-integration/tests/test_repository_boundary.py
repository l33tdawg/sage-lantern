# SPDX-License-Identifier: Apache-2.0
"""One-way dependency, canonical envelope and pinned build-input controls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import pytest

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parents[1]


def test_dependency_and_builder_lock_use_the_same_exact_core_revision():
    lock = json.loads((PROJECT / 'meshticulum-source-lock.json').read_bytes())
    dependency = 'meshticulum @ git+https://github.com/l33tdawg/meshticulum.git@' + lock['revision']
    assert len(lock['revision']) == 40 and bytes.fromhex(lock['revision']).hex() == lock['revision']
    for path in (PROJECT / 'pyproject.toml', ROOT / 'software/runtime/pyproject.toml'):
        assert dependency in tomllib.loads(path.read_text())['project']['dependencies']
    assert not (PROJECT / 'src/meshticulum').exists()


def test_existing_lantern_runtime_envelope_is_the_canonical_core_module(tmp_path):
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(ROOT / 'software/runtime/src') + os.pathsep + environment.get('PYTHONPATH', '')
    result = subprocess.run([sys.executable, '-c',
        'import sage_lantern.mesh_envelope as legacy; '
        'import meshticulum.privacy.mesh_envelope as core; '
        'assert legacy is core and legacy.Peer is core.Peer'],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('relative', ['src/meshticulum/bench/services.py',
                                    'src/meshticulum/privacy/forward_session.py',
                                    'firmware/meshticulum-bench/main.cpp'])
def test_builder_refuses_changed_core_code_and_firmware_before_bundle_creation(tmp_path, relative):
    path = ROOT / 'firmware/meshticulum-bench/source_core.py'
    spec = importlib.util.spec_from_file_location('source_core_boundary', path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    original = Path(os.environ['MESHTICULUM_SOURCE'])
    assert guard.core_source(original) == original.resolve()
    lock = json.loads((PROJECT / 'meshticulum-source-lock.json').read_bytes())
    candidate = tmp_path / 'changed-core'
    for name in lock['files']:
        destination = candidate / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((original / name).read_bytes())
    changed = candidate / relative
    changed.write_bytes(changed.read_bytes() + b'\n# changed input\n')
    with pytest.raises(RuntimeError, match='meshticulum_source_lock_mismatch'):
        guard.core_source(candidate)
    assert not (ROOT / 'build/new-boundary-bundle').exists()
