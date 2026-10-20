# SPDX-License-Identifier: Apache-2.0
"""Native-only launcher bindings with invented facts; no native file access."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

BOOT = '00000000-0000-4000-8000-000000000001'


@pytest.mark.parametrize('fault', [None, 'no-legacy', 'duplicate-wrapper', 'role', 'owner', 'unit', 'boot', 'expired',
    'runtime', 'manifest', 'scope-hash', 'extra-wrapper', 'legacy-unit', 'legacy-boot',
    'legacy-expiry', 'legacy-runtime', 'legacy-manifest', 'lifecycle'])
def test_parent_launcher_binds_native_scope_and_legacy_to_verified_bundle(tmp_path, monkeypatch, capsys, fault):
    source = Path(__file__).resolve().parents[3] / 'firmware/meshticulum-bench/bench_node.py'
    spec = importlib.util.spec_from_file_location('parent_launcher', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(tmp_path / ('runtime-' + 'a'*16) / 'bench_node.py')
    manifest = json.dumps(dict(schema='meshticulum.bench-runtime-bundle.v1', files={},
        public_parent_snapshot_schemas=['meshticulum.public-parent-observation.v1'])).encode()
    scope = dict(role='controller' if fault == 'role' else 'native',
        owner_uid=1 if fault == 'owner' else 0, unit='unit-a' if fault == 'unit' else 'unit-c',
        current_host_boot_id=BOOT, observation_expires_epoch_us=1005000000)
    legacy = dict(unit='unit-a' if fault == 'legacy-unit' else 'unit-c',
        current_host_boot_id='different' if fault == 'legacy-boot' else BOOT,
        observation_expires_epoch_us=1006000000 if fault == 'legacy-expiry' else 1005000000,
        runtime_sha256='b'*64 if fault == 'legacy-runtime' else 'a'*64,
        manifest_sha256='b'*64 if fault == 'legacy-manifest' else hashlib.sha256(manifest).hexdigest())
    wrapper = dict(schema='meshticulum.public-parent-observation.v1', parent_scope=scope,
        legacy_scope=None if fault == 'no-legacy' else legacy, runtime_sha256='b'*64 if fault == 'runtime' else 'a'*64,
        manifest_sha256='b'*64 if fault == 'manifest' else hashlib.sha256(manifest).hexdigest())
    if fault == 'extra-wrapper':
        wrapper['unexpected'] = True
    raw = json.dumps(wrapper).encode()
    if fault == 'duplicate-wrapper':
        raw = raw[:-1] + b', \"runtime_sha256\": \"' + b'a'*64 + b'\"}'
    monkeypatch.setattr(module, 'trusted', lambda path, limit=2000000:
        manifest if path.name == 'manifest.json' else raw)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    from lantern_meshticulum.bench import pilot_manage
    from lantern_meshticulum.ip import node
    observations, calls = [], []

    def facts(unit):
        observations.append(unit)
        return dict(unit=unit, host_boot_id='different' if fault == 'boot' else BOOT,
            epoch_us=1000000000, monotonic_us=50000000,
            services={'public-service': len(observations) if fault == 'lifecycle' else 'stable'})

    monkeypatch.setattr(pilot_manage, 'facts', facts)
    monkeypatch.setattr(node, 'clock', lambda:
        (1006000000 if fault == 'expired' else 1000000000, 50000000))

    class Scope:
        @classmethod
        def from_dict(cls, value):
            return SimpleNamespace(**value)

    class Snapshot:
        def to_dict(self):
            return dict(schema='meshticulum.public-parent-snapshot.v1',
                complete_global_debt=False, eligible_for_enrollment=False)

    def collect(state, value, **kwargs):
        calls.append((state, value, kwargs))
        return Snapshot()

    from lantern_meshticulum.bench import pilot_debt_snapshot
    monkeypatch.setitem(sys.modules, 'lantern_meshticulum.bench.pilot_parent_snapshot',
        SimpleNamespace(Scope=Scope, collect=collect, public_json=pilot_debt_snapshot.public_json))
    monkeypatch.setattr(pilot_debt_snapshot, 'Scope', Scope)
    monkeypatch.setattr(sys, 'argv', ['bench_node', 'pilot-parent-snapshot', 'unit-c',
        '--scope-file', str(tmp_path / 'scope.json'), '--scope-sha256',
        'b'*64 if fault == 'scope-hash' else hashlib.sha256(raw).hexdigest(),
        '--expected-boot', BOOT, '--observation-end-epoch-us', '1005000000'])
    if fault not in (None, 'no-legacy'):
        with pytest.raises(RuntimeError):
            module.main()
        assert len(calls) == (1 if fault == 'lifecycle' else 0)
    else:
        module.main()
        result = json.loads(capsys.readouterr().out)
        assert calls[0][0] == Path('/var/lib/meshticulum-bench')
        if fault == 'no-legacy':
            assert calls[0][2]['legacy_scope'] is None
        else:
            assert calls[0][2]['legacy_scope'].unit == 'unit-c'
        assert result['complete_global_debt'] is False
        assert result['eligible_for_enrollment'] is False
        assert result['runtime_archive_sha256_observed'] is False
        assert result['runtime_manifest_and_payloads_verified'] is True
