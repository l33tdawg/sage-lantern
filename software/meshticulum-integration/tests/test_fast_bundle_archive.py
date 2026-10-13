"""Synthetic v1/v2 archive controls, not native installation evidence."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


@pytest.fixture
def runner(monkeypatch):
    bench=Path(__file__).parents[3]/'firmware/meshticulum-bench';monkeypatch.syspath_prepend(str(bench))
    spec=importlib.util.spec_from_file_location('fast_archive_runner',bench/'run_public_gfsk250_mb1_fast_bundle100k.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def archive(physical,manifest):
    raw=canonical(manifest);stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as tar:
        for name,data in dict(physical,**{'manifest.json':raw}).items():
            row=tarfile.TarInfo(name);row.size=len(data);row.mode=0o600;tar.addfile(row,io.BytesIO(data))
    return stream.getvalue(),sha(raw)


def source_image(sources,*,duplicate=False,compression=zipfile.ZIP_STORED):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=compression) as image:
        for name,data in sources.items():
            row=zipfile.ZipInfo(name);row.compress_type=compression;row.external_attr=0o100600<<16
            image.writestr(row,data)
        if duplicate:image.writestr(next(iter(sources)),next(iter(sources.values())))
    return stream.getvalue()


def sealed(*,fault=None):
    sources={**{f'lantern_meshticulum/bench/synthetic{i}.py':b'# synthetic source only\n' for i in range(72)},
        'bench_node.py':b'# synthetic entry\n','LICENSE':b'synthetic notice\n',
        'lantern_meshticulum/bench/pilot_resume_rpc.py':b'# synthetic adapter\n'}
    if fault=='traversal':sources['../escaped.py']=sources.pop('lantern_meshticulum/bench/synthetic0.py')
    image=source_image(sources,duplicate=fault=='duplicate',compression=zipfile.ZIP_DEFLATED if fault=='compressed' else zipfile.ZIP_STORED)
    loader=b'# synthetic loader\n'
    physical={name:data for name,data in sources.items() if not name.startswith(('meshticulum/','lantern_meshticulum/'))}
    physical.update({'execution.zip':image,'execution_runtime.py':loader})
    pins={name:sha(data) for name,data in sources.items()}
    manifest=dict(schema='meshticulum.bench-runtime-bundle.v2',files={name:sha(data) for name,data in physical.items()},
        source_files=pins,execution_layout=dict(schema='sage-lantern.sealed-source-image.v1',image='execution.zip',
            bytes=len(image),sha256=sha(image),loader='execution_runtime.py',loader_sha256=sha(loader),source_files_sha256=sha(canonical(pins))))
    if fault=='inner_hash':
        pins['lantern_meshticulum/bench/pilot_resume_rpc.py']='0'*64;manifest['execution_layout']['source_files_sha256']=sha(canonical(pins))
    if fault=='inner_roster':
        pins['lantern_meshticulum/bench/foreign.py']=sha(b'new');manifest['execution_layout']['source_files_sha256']=sha(canonical(pins))
    if fault=='source_map':manifest['execution_layout']['source_files_sha256']='0'*64
    if fault=='outer_disagreement':
        physical['bench_node.py']=b'# different entry\n';manifest['files']['bench_node.py']=sha(physical['bench_node.py'])
    if fault=='image_hash':manifest['execution_layout']['sha256']='0'*64
    return archive(physical,manifest),sources


def test_default_v1_and_explicit_v2_preserve_native_adapter_source_identity(runner):
    physical={'bench_node.py':b'entry','lantern_meshticulum/bench/pilot_resume_rpc.py':b'adapter'}
    raw,pin=archive(physical,dict(schema='meshticulum.bench-runtime-bundle.v1',files={n:sha(v) for n,v in physical.items()}))
    manifest,logical=runner.archive_source_members(raw,pin)
    assert logical==physical and manifest['schema'].endswith('.v1')
    (raw,pin),sources=sealed();manifest,logical=runner.archive_source_members(raw,pin)
    assert logical==sources and 'execution.zip' not in logical
    assert sha(logical['lantern_meshticulum/bench/pilot_resume_rpc.py'])==manifest['source_files']['lantern_meshticulum/bench/pilot_resume_rpc.py']


@pytest.mark.parametrize('fault',['inner_hash','inner_roster','source_map','outer_disagreement','image_hash','traversal','duplicate','compressed'])
def test_v2_rejects_ambiguity_source_change_or_unbounded_archive_type(runner,fault):
    (raw,pin),_=sealed(fault=fault)
    with pytest.raises(Exception):runner.archive_source_members(raw,pin)


def test_legacy_layout_cannot_be_silently_relabelled(runner):
    physical={'bench_node.py':b'entry'}
    raw,pin=archive(physical,dict(schema='meshticulum.bench-runtime-bundle.v1',files={'bench_node.py':sha(b'entry')},source_files={'bench_node.py':sha(b'entry')}))
    with pytest.raises(Exception):runner.archive_source_members(raw,pin)
