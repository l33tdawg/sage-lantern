# SPDX-License-Identifier: Apache-2.0
"""Explicit, content-pinned Meshticulum input for Lantern bundle builders."""
import hashlib
import json
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2] / 'software/meshticulum-integration'


def core_source(path=None):
    value = path or os.environ.get('MESHTICULUM_SOURCE')
    if not value:
        raise RuntimeError('meshticulum_source_required')
    root = Path(value).resolve(strict=True)
    lock = json.loads((PROJECT / 'meshticulum-source-lock.json').read_bytes())
    if lock['repository'] != 'https://github.com/l33tdawg/meshticulum':
        raise RuntimeError('wrong_meshticulum_source_repository')
    actual = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((root / 'src/meshticulum').rglob('*'))
        if p.is_file() and p.suffix in ('.py', '.json')}
    for p in sorted((root / 'firmware/meshticulum-bench').glob('*')):
        if p.is_file() and p.suffix in ('.cpp', '.hpp', '.c', '.dts'):
            actual[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    for name in ('LICENSE', 'NOTICE'):
        actual[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if actual != lock['files']:
        raise RuntimeError('meshticulum_source_lock_mismatch')
    return root
