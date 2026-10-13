"""Installation-format budgets, separate from source and radio limits."""
import hashlib
import sys

import pytest

from tests.test_fast_bundle_archive import archive, runner
from tests.test_lantern_meshticulum_bench_runtime import helper
from tests.test_sealed_runtime_image import packaged_image


def test_real_stored_and_deflated_packages_keep_exact_sources(runner, packaged_image):
    _, files, record = packaged_image
    from pathlib import Path
    raw = Path(record['archive']).read_bytes()
    manifest, logical = runner.archive_source_members(raw, hashlib.sha256(files['manifest.json']).hexdigest())
    assert manifest == record['manifest']
    assert logical == helper('runtime_bootstrap').image_members(files['execution.zip'], manifest)
    helper('build_runtime').validate_package(files, sealed=True)


@pytest.mark.parametrize('which', ['bootstrap', 'runner'])
@pytest.mark.parametrize('case', ['input', 'image', 'packaging', 'container', 'legacy'])
def test_outer_allowance_cannot_expand_other_budgets(runner, which, case):
    physical = {'bench_node.py': b'entry', 'execution.zip': b'i'}
    version = 'v2'
    if case == 'image': physical['execution.zip'] = b'i' * 2000001
    if case == 'packaging': physical.update(a=b'a' * 250000, b=b'b' * 250000)
    if case == 'container':
        physical['execution.zip'] = b'i' * 2000000
        physical.update(a=b'a' * 250000, b=b'b' * 250000)
    if case == 'legacy':
        version = 'v1'; physical.pop('execution.zip')
        physical.update({str(i): b'a' * 400000 for i in range(5)})
    manifest = dict(schema='meshticulum.bench-runtime-bundle.' + version,
                    files={name: hashlib.sha256(raw).hexdigest() for name, raw in physical.items()})
    raw, pin = archive(physical, manifest)
    if case == 'input': raw = b'x' * 2000001
    reason = {'input': 'input', 'image': 'member', 'packaging': 'packaging',
              'container': 'total', 'legacy': 'packaging'}[case]
    with pytest.raises(RuntimeError, match=reason):
        if which == 'bootstrap': helper('runtime_bootstrap').members(raw, hashlib.sha256(raw).hexdigest())
        else: runner.archive_source_members(raw, pin)
    if case != 'input':
        from json import dumps
        files = dict(physical, **{'manifest.json': dumps(manifest).encode()})
        with pytest.raises(SystemExit): helper('build_runtime').validate_package(files, sealed=version == 'v2')


@pytest.mark.parametrize('refusal', ['container', 'compressed'])
def test_builder_refusal_never_publishes_an_artifact(runner, monkeypatch, tmp_path, refusal):
    build = helper('build_runtime')
    monkeypatch.setattr(sys, 'argv', ['build_runtime.py', '--execution-layout', 'sealed-image-deflated',
                                    '--output-dir', str(tmp_path)])
    if refusal == 'container':
        original = build.validate_package
        def oversized(files, *, sealed):
            # Exercise the real package validator before archive serialization.
            original(dict(files, extra_notice=b'x' * 500000), sealed=sealed)
        monkeypatch.setattr(build, 'validate_package', oversized)
    else:
        monkeypatch.setattr(build.gzip, 'compress', lambda *a, **k: b'x' * 2000001)
    with pytest.raises(SystemExit): build.main()
    assert not (tmp_path / 'RUNTIME_BUNDLE.json').exists()
    assert not list(tmp_path.glob('runtime-*'))
