# SPDX-License-Identifier: Apache-2.0
"""Local archive bounds; no extraction, installation, device or transport."""
import hashlib
import io
import json
import tarfile

import pytest
from tests.test_lantern_meshticulum_bench_runtime import helper


def archive(payloads):
    bodies = {'bench_node.py': b'# synthetic source\n'}
    bodies.update({'module_%03d.py' % i: b'# synthetic module\n' for i in range(payloads-1)})
    manifest = dict(schema='meshticulum.bench-runtime-bundle.v1',
        files={name:hashlib.sha256(raw).hexdigest() for name,raw in bodies.items()})
    bodies['manifest.json'] = json.dumps(manifest,sort_keys=True).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as bundle:
        for name,raw in bodies.items():
            entry=tarfile.TarInfo(name); entry.mode=0o600; entry.size=len(raw)
            bundle.addfile(entry,io.BytesIO(raw))
    return output.getvalue(), bodies


@pytest.mark.parametrize('payloads',[81,127])
def test_extended_bound_accepts_82_and_exactly_128_total_members(payloads):
    raw,bodies=archive(payloads)
    assert len(bodies)==payloads+1
    assert helper('runtime_bootstrap').members(raw,hashlib.sha256(raw).hexdigest())==bodies


@pytest.mark.parametrize('payloads',[128,129])
def test_128_and_129_payloads_plus_manifest_are_rejected(payloads):
    raw,bodies=archive(payloads)
    assert len(bodies)>128
    with pytest.raises(RuntimeError,match='runtime_archive_member_limit'):
        helper('runtime_bootstrap').members(raw,hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('sizes,reason',[( [500001], 'runtime_archive_member_limit'),
    ([450000]*5, 'runtime_archive_total_limit')])
def test_member_count_change_keeps_original_byte_limits(sizes,reason):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as bundle:
        for index,size in enumerate(sizes):
            entry=tarfile.TarInfo('blob_%d' % index); entry.size=size
            bundle.addfile(entry,io.BytesIO(b'x'*size))
    raw=output.getvalue()
    with pytest.raises(RuntimeError,match=reason):
        helper('runtime_bootstrap').members(raw,hashlib.sha256(raw).hexdigest())
