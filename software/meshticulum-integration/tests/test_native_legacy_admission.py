# SPDX-License-Identifier: Apache-2.0
import json
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision
from tests.service_helpers import scope

def test_legacy_public_runner_refuses_service_before_radio(tmp_path, monkeypatch):
    from lantern_meshticulum.bench import node
    clock = Clock()
    directory = tmp_path / 'grant'
    plan = provision(directory, 'unit-a', b'x', clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('LME.binary', clock)
    (directory / 'grant.json').write_text(json.dumps(plan))
    original = node.Path.read_text
    monkeypatch.setattr(node.Path, 'read_text', lambda path, *a, **k:
        BOOT if str(path) == '/proc/sys/kernel/random/boot_id' else original(path, *a, **k))
    monkeypatch.setattr(node, 'clock', clock)
    monkeypatch.setattr(node, 'roles', lambda unit: {'model': 'test-only'})
    opened = []
    monkeypatch.setattr(node, '_serial', lambda unit: opened.append(unit))
    result = node.run('unit-a', directory, tmp_path / 'public-objects')
    assert result['error'].endswith('service_requires_dedicated_runner')
    assert not opened and not (tmp_path / 'public-objects').exists()


