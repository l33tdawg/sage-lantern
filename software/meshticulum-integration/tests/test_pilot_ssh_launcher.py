"""Execute the shared launcher locally; model root ownership, never dispatch."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench.pilot_ssh import INVOKE
from tests.test_sealed_runtime_image import packaged_image, extract
from tests.test_lantern_meshticulum_bench_runtime import helper


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def invoke(root, pin, monkeypatch, *, ancestor_owner=0, file_owner=0):
    """Run unchanged INVOKE with real reads/hashes and an inert final dispatch."""
    original_lstat, original_fstat = Path.lstat, os.fstat
    calls = []

    def record(info, owner):
        return SimpleNamespace(st_uid=owner, st_mode=info.st_mode,
            st_nlink=info.st_nlink, st_size=info.st_size)

    def lstat(path, *args, **kwargs):
        # The only modeled authority is root ownership. Modes, inode types,
        # hardlinks and all bytes remain those of the real private test tree.
        info = original_lstat(path, *args, **kwargs)
        return record(info, ancestor_owner if path == root else 0)

    def dispatch(path, *, run_name):
        calls.append((path, run_name, list(sys.argv)))

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'lstat', lstat)
        patch.setattr(os, 'fstat', lambda fd: record(original_fstat(fd), file_owner))
        patch.setattr(runpy, 'run_path', dispatch)
        patch.setattr(sys, 'argv', ['launcher', str(root/'bench_node.py'), pin, 'pilot-facts', 'unit-c'])
        exec(compile(INVOKE, 'actual-pinned-SSH-INVOKE', 'exec'), {})
    return calls


@pytest.mark.parametrize('version', [1, 2])
def test_actual_pinned_launcher_accepts_both_explicit_runtime_layouts(packaged_image, monkeypatch, version):
    root = extract(packaged_image, 'launcher-v%d' % version)
    manifest_path = root/'manifest.json'
    if version == 1:
        manifest = json.loads(manifest_path.read_bytes())
        logical = helper('runtime_bootstrap').image_members((root/'execution.zip').read_bytes(), manifest)
        for name, raw in logical.items():
            path = root/name; path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(raw); path.chmod(0o600)
        manifest['schema'] = 'meshticulum.bench-runtime-bundle.v1'
        manifest['files'] = manifest.pop('source_files'); manifest.pop('execution_layout')
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    pin = digest(manifest_path.read_bytes())
    assert invoke(root, pin, monkeypatch) == [(str(root/'bench_node.py'), '__main__',
        [str(root/'bench_node.py'), 'pilot-facts', 'unit-c'])]


@pytest.mark.parametrize('fault', ['manifest_pin', 'unknown_schema', 'private_services', 'entry_bytes',
    'entry_symlink', 'entry_hardlink', 'entry_writable', 'ancestor_writable', 'ancestor_owner', 'file_owner'])
def test_sealed_launcher_refuses_pin_or_ownership_changes_before_dispatch(packaged_image, monkeypatch, fault):
    root = extract(packaged_image, 'launcher-refuse-'+fault)
    manifest_path, entry = root/'manifest.json', root/'bench_node.py'
    manifest = json.loads(manifest_path.read_bytes())
    if fault == 'unknown_schema': manifest['schema'] = 'meshticulum.bench-runtime-bundle.v3'
    elif fault == 'private_services': manifest['private_services_enabled'] = True
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    pin = '0'*64 if fault == 'manifest_pin' else digest(manifest_path.read_bytes())
    if fault == 'entry_bytes': entry.write_bytes(entry.read_bytes()+b'\n# changed\n')
    elif fault == 'entry_symlink':
        other = root/'held-entry.py'; entry.rename(other); entry.symlink_to(other)
    elif fault == 'entry_hardlink': (root/'linked-entry.py').hardlink_to(entry)
    elif fault == 'entry_writable': entry.chmod(0o622)
    elif fault == 'ancestor_writable': root.chmod(0o722)
    with pytest.raises((AssertionError, OSError)):
        invoke(root, pin, monkeypatch, ancestor_owner=1 if fault == 'ancestor_owner' else 0,
               file_owner=1 if fault == 'file_owner' else 0)
