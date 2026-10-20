# SPDX-License-Identifier: Apache-2.0
"""Launcher scope controls with invented facts; no native or journal access."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import os
import stat

import pytest

BOOT = '00000000-0000-4000-8000-000000000001'


@pytest.mark.parametrize('fault', [None, 'scope-hash', 'boot', 'expired', 'runtime', 'manifest', 'lifecycle'])
def test_debt_launcher_checks_explicit_pins_and_current_facts_before_return(tmp_path, monkeypatch, capsys, fault):
    source = Path(__file__).resolve().parents[3] / 'firmware/meshticulum-bench/bench_node.py'
    spec = importlib.util.spec_from_file_location('debt_launcher', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    bundle = tmp_path / ('runtime-' + 'a'*16)
    module.__file__ = str(bundle / 'bench_node.py')
    manifest_raw = json.dumps({'schema':'meshticulum.bench-runtime-bundle.v1', 'files':{},
        'public_debt_snapshot_schemas':['meshticulum.public-debt-scope.v1']}).encode()
    scope = dict(schema='meshticulum.public-debt-scope.v1',unit='unit-c',current_host_boot_id=BOOT,
        runtime_sha256='b'*64 if fault=='runtime' else 'a'*64,
        manifest_sha256='b'*64 if fault=='manifest' else hashlib.sha256(manifest_raw).hexdigest(),
        observation_expires_epoch_us=1005000000,runs=[])
    raw = json.dumps(scope).encode();scope_path=tmp_path/'scope.json';calls=[];observations=[]
    monkeypatch.setattr(module,'trusted',lambda path,limit=2000000:manifest_raw if path.name=='manifest.json' else raw)
    monkeypatch.setattr(module.os,'geteuid',lambda:0)
    from lantern_meshticulum.bench import pilot_manage
    from lantern_meshticulum.ip import node
    def facts(unit):
        observations.append(unit)
        return dict(unit=unit,host_boot_id='different' if fault=='boot' else BOOT,
            services={'model':len(observations) if fault=='lifecycle' else 'stable'},
            epoch_us=1000000000,monotonic_us=50000000)
    monkeypatch.setattr(pilot_manage,'facts',facts)
    monkeypatch.setattr(node,'clock',lambda:(1006000000 if fault=='expired' else 1000000000,50000000))
    class Scope:
        @classmethod
        def from_dict(cls,value):return SimpleNamespace(**value)
    def collect(state,value,**kwargs):
        calls.append((state,value))
        return dict(schema='meshticulum.public-debt-snapshot.v1',eligible_for_enrollment=False)
    monkeypatch.setitem(sys.modules,'lantern_meshticulum.bench.pilot_debt_snapshot',SimpleNamespace(Scope=Scope,collect=collect))
    monkeypatch.setattr(sys,'argv',['bench_node','pilot-debt-snapshot','unit-c','--scope-file',str(scope_path),
        '--scope-sha256','b'*64 if fault=='scope-hash' else hashlib.sha256(raw).hexdigest(),
        '--expected-boot',BOOT,'--observation-end-epoch-us','1005000000'])
    if fault:
        with pytest.raises(RuntimeError):module.main()
        assert len(calls)==(1 if fault=='lifecycle' else 0)
    else:
        module.main();result=json.loads(capsys.readouterr().out)
        assert len(calls)==1 and calls[0][0]==Path('/var/lib/meshticulum-bench')
        assert result['eligible_for_enrollment'] is False
        assert result['runtime_archive_sha256_observed'] is False
        assert result['runtime_manifest_and_payloads_verified'] is True


def test_debt_help_performs_no_root_or_device_observation(monkeypatch):
    source = Path(__file__).resolve().parents[3] / 'firmware/meshticulum-bench/bench_node.py'
    spec=importlib.util.spec_from_file_location('debt_help',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module.os,'geteuid',lambda:pytest.fail('help must not observe root'))
    monkeypatch.setattr(sys,'argv',['bench_node','--help'])
    with pytest.raises(SystemExit) as caught:module.main()
    assert caught.value.code==0


def test_scope_fifo_is_rejected_without_waiting_for_writer(tmp_path, monkeypatch):
    source=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/bench_node.py'
    spec=importlib.util.spec_from_file_location('debt_fifo',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    fifo=tmp_path/'scope.json';os.mkfifo(fifo,0o600)
    monkeypatch.setattr(module.Path,'lstat',lambda path:SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o700))
    with pytest.raises(RuntimeError,match='untrusted_bundle_file'):module.trusted(fifo,200000)
