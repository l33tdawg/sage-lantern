# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Dedicated Linux-entry source replay with modeled hardware, never RF evidence."""
import json
import base64
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench import node
from meshticulum.bench.services import resource
from tests.test_meshticulum_bench_runtime import BOOT, Clock, ModelWorker, provision
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.service_helpers import scope


def isolate_node(monkeypatch, clock):
    from lantern_meshticulum.bench import pilot_job
    monkeypatch.setattr(pilot_job, 'kernel', lambda pid: dict(start_ticks=1, state='R', command_sha256='0' * 64))
    original = node.Path.read_text
    monkeypatch.setattr(node.Path, 'read_text', lambda path, *a, **k:
        BOOT if str(path) == '/proc/sys/kernel/random/boot_id' else original(path, *a, **k))
    monkeypatch.setattr(node, 'clock', clock)
    monkeypatch.setattr(node, 'roles', lambda unit: {'source_model_only': 'no_service_change'})
    from lantern_meshticulum.ip import node as ip_node
    monkeypatch.setattr(ip_node, '_facts', lambda unit: dict(unit=unit,host_boot_id=BOOT,
        services={'source_model_only':'no_service_change'},service_lifetimes={}))


def qualify_entry(directory, payload, clock, unit):
    """Prepare through an explicitly enrolled synthetic local owner."""
    from tests.public_parent_helpers import authorize
    from lantern_meshticulum.bench.pilot_provision import provision as native_provision
    state = directory.parent/('native-'+unit)
    state.mkdir(mode=0o700)
    plan=json.loads((directory/'grant.json').read_text())
    plan.pop('application_deadline_monotonic_us',None)
    sends=plan['sender_unit']==unit
    value=dict(schema='meshticulum.public-pilot-package.v1',unit=unit,
        campaign_id='c'*32,resource_sha256='a'*64,
        application_expires_epoch_us=plan['service']['application_expires_epoch_us'],
        transport='lora',grant=plan,secret_base64=base64.b64encode((directory/'secret.bin').read_bytes()).decode(),
        payload_base64=base64.b64encode(payload).decode() if sends else None,source_run_id=None)
    value=authorize(value,state,clock,payload)
    facts=dict(unit=unit,host_boot_id=BOOT,services={'source_model_only':'no_service_change'},service_lifetimes={})
    receipt=native_provision(value,state=state,facts=facts,clock=clock)
    return state/'runs'/receipt['run_id']


def test_private_service_grant_is_refused_before_radio_or_private_backend(tmp_path, monkeypatch):
    clock = Clock()
    directory = tmp_path / 'grant'
    plan = provision(directory, 'unit-a', b'x', clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('LME.binary', clock)
    (directory / 'grant.json').write_text(json.dumps(plan))
    isolate_node(monkeypatch, clock)
    opened = []
    monkeypatch.setattr(node, '_serial', lambda unit: opened.append(unit))
    result = node.run_public_service('unit-a', directory)
    assert result['complete'] is False
    assert result['error'].endswith('private_service_requires_qualified_native_provider')
    assert not opened and not (directory / 'wire.sqlite').exists()
    assert not (directory / 'receiver.bin').exists()
    assert 'object' not in result and 'grant_context_sha256' not in result


def test_malformed_public_service_payload_is_refused_before_radio(tmp_path, monkeypatch):
    clock = Clock()
    directory = tmp_path / 'grant'
    payload = b'not a resource request'
    plan = provision(directory, 'unit-a', payload, clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('MR1.request', clock)
    (directory / 'grant.json').write_text(json.dumps(plan))
    descriptor = os.open(directory / 'sender.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(payload)
    isolate_node(monkeypatch, clock)
    opened = []
    monkeypatch.setattr(node, '_serial', lambda unit: opened.append(unit))
    result = node.run_public_service('unit-a', directory)
    assert result['complete'] is False and 'resource request' in result['error']
    assert not opened and not (directory / 'wire.sqlite').exists()


def test_historical_public_grant_cannot_activate_without_prepared_v2_parent(tmp_path,monkeypatch):
    clock=Clock();directory=tmp_path/'grant'
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    plan=provision(directory,'unit-a',payload,clock)
    plan['schema']='meshticulum.service-run.v1';plan['service']=scope('MR1.request',clock)
    (directory/'grant.json').write_text(json.dumps(plan))
    fd=os.open(directory/'sender.bin',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(payload)
    isolate_node(monkeypatch,clock)
    opened=[];monkeypatch.setattr(node,'_serial',lambda unit:opened.append(unit))
    result=node.run_public_service('unit-a',directory)
    assert not result['complete'] and not opened
    assert not any((directory/name).exists() for name in ('started','wire.sqlite','attempts.sqlite','job.json'))


def test_parent_revocation_during_radio_setup_refuses_arm_and_retains_start_debt(tmp_path,monkeypatch):
    from tests.radio_profiles import profile_for
    import sqlite3
    clock=Clock();directory=tmp_path/'grant'
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    plan=provision(directory,'unit-a',payload,clock,profile_for('sf7-500'))
    plan['schema']='meshticulum.service-run.v1';plan['service']=scope('MR1.request',clock)
    (directory/'grant.json').write_text(json.dumps(plan))
    fd=os.open(directory/'sender.bin',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(payload)
    directory=qualify_entry(directory,payload,clock,'unit-a')
    isolate_node(monkeypatch,clock)
    opened=[];armed=[]
    def serial(unit):
        fd=os.open(tmp_path/'channel-model',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        opened.append(unit)
        (directory.parent.parent/'parents'/('1'*32)/'native.sqlite.approval').write_bytes(b'retired\n')
        channel=SimpleNamespace(fd=fd,lock_fd=fd,close=lambda:os.close(fd))
        worker=SimpleNamespace(journal=None,arm=lambda **kwargs:armed.append(True))
        return channel,worker,dict(boot_attempts=0,boot_reserved_estimated_airtime_us=0)
    monkeypatch.setattr(node,'_serial',serial)
    result=node.run_public_service('unit-a',directory)
    assert opened==['unit-a'] and not armed and not result['complete']
    assert not (directory/'attempts.sqlite').exists()
    connection=sqlite3.connect(directory.parent.parent/'parents'/('1'*32)/'native.sqlite')
    try:
        assert connection.execute('SELECT COUNT(*) FROM claims').fetchone()==(1,)
        assert connection.execute('SELECT COUNT(*) FROM starts').fetchone()==(1,)
    finally:connection.close()


def test_public_service_entry_uses_real_flow_pipeline_and_disarms_on_exit(tmp_path, monkeypatch):
    clock = Clock()
    payload = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    with carrier_pair(tmp_path / 'peer', payload, clock, 'unit-a', 'unit-b', 91,
                      service=scope('MR1.request', clock)) as pair:
        # Only the peer receiver runs. The helper's unused sender never emits
        # a nonce/frame; its public deterministic plan provides test inputs.
        directory = tmp_path / 'entry'
        directory.mkdir(mode=0o700)
        for name in ('grant.json', 'secret.bin', 'active'):
            data = (pair.sender.authority.path / name).read_bytes()
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
        descriptor = os.open(directory / 'sender.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(payload)
        directory=qualify_entry(directory,payload,clock,'unit-a')
        isolate_node(monkeypatch, clock)
        fd = os.open(tmp_path / 'channel-model', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        channel = SimpleNamespace(fd=fd, lock_fd=fd, close=lambda: os.close(fd))
        class Worker(ModelWorker):
            def __init__(self):
                super().__init__(None, clock)
                self.identity = SimpleNamespace(boot=b'B' * 16, board_id=1, version=node.FIRMWARE)
                self.armed = False
            def arm(self, *, journal, **kwargs):
                self.journal = journal
                self.armed = True
            def disarm(self):
                self.armed = False
                self.owned = False
        worker = Worker()
        worker.peer, pair.workers[1].peer = pair.workers[1], worker
        monkeypatch.setattr(node, '_serial', lambda unit: (channel, worker,
            {'boot_attempts': 0, 'boot_reserved_estimated_airtime_us': 0, 'source_model_only': True}))
        def tick(seconds):
            pair.receiver.step()
            clock.advance(50000)
        monkeypatch.setattr(node.time, 'sleep', tick)
        result = node.run_public_service('unit-a', directory)
        assert result['complete'] is True and result['disarmed_on_exit'] is True
        assert result['publisher_verified'] is False and result['private_admission'] is False
        assert result['service_runner']['state'] == 'wire_complete'
        assert result['attempt_journal']['attempts'] > 0 and not worker.armed
        assert result['parent']['started'] and result['parent_caps']['starts']==1
        assert bytes(pair.receiver.received) == payload
        assert not (directory / 'receiver.bin').exists()
        assert (directory / 'result.json').is_file()


def test_quarantine_is_bounded_create_only_and_never_follows_existing_file(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir(mode=0o700)
    node._quarantine(directory, b'public block')
    assert (directory / 'receiver.bin').read_bytes() == b'public block'
    assert (directory / 'receiver.bin').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        node._quarantine(directory, b'replacement')
    assert (directory / 'receiver.bin').read_bytes() == b'public block'


def test_public_receiver_quarantines_exact_flow_bytes_without_seeding(tmp_path, monkeypatch):
    clock = Clock()
    request = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    payload = resource.BlockServer(lambda path: b'public fixture').handle(request)
    with carrier_pair(tmp_path / 'peer', payload, clock, 'unit-a', 'unit-b', 92,
                      service=scope('MR1.response', clock)) as pair:
        directory = tmp_path / 'entry'
        directory.mkdir(mode=0o700)
        for name in ('grant.json', 'secret.bin', 'active'):
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write((pair.receiver.authority.path / name).read_bytes())
        directory=qualify_entry(directory,payload,clock,'unit-b')
        isolate_node(monkeypatch, clock)
        fd = os.open(tmp_path / 'channel-model', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        channel = SimpleNamespace(fd=fd, lock_fd=fd, close=lambda: os.close(fd))
        class Worker(ModelWorker):
            def __init__(self):
                super().__init__(None, clock)
                self.identity = SimpleNamespace(boot=b'C' * 16, board_id=1, version=node.FIRMWARE)
            def arm(self, *, journal, **kwargs):
                self.journal = journal
            def disarm(self):
                self.owned = False
        worker = Worker()
        worker.peer, pair.workers[0].peer = pair.workers[0], worker
        monkeypatch.setattr(node, '_serial', lambda unit: (channel, worker,
            {'boot_attempts': 0, 'boot_reserved_estimated_airtime_us': 0, 'source_model_only': True}))
        pair.sender.send(payload)
        def tick(seconds):
            pair.sender.step()
            clock.advance(50000)
        monkeypatch.setattr(node.time, 'sleep', tick)
        result = node.run_public_service('unit-b', directory)
        assert result['complete'] is True and result['disarmed_on_exit'] is True
        assert result['application_status'] == 'public_block_quarantined'
        assert (directory / 'receiver.bin').read_bytes() == payload
        assert result['service_object_durable'] is True and result['publisher_verified'] is False
        assert result['parent']['started'] and result['parent_caps']['starts']==1
        assert 'chunk_durable' not in result and 'verified_chunk_sha256' not in result
        assert not (directory / 'chunks').exists()
    assert (directory / 'receiver.bin').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        node._quarantine(directory, b'replacement')
    assert (directory / 'receiver.bin').read_bytes() == payload


def test_quarantine_refuses_oversized_payload_and_preexisting_symlink(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        node._quarantine(directory, b'x' * 16385)
    assert not (directory / 'receiver.bin').exists()
    outside = tmp_path / 'sentinel'
    outside.write_bytes(b'preserved')
    (directory / 'receiver.bin').symlink_to(outside)
    with pytest.raises(FileExistsError):
        node._quarantine(directory, b'replacement')
    assert outside.read_bytes() == b'preserved'


def test_entry_help_has_no_device_or_private_runtime_side_effect():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, '-m', 'lantern_meshticulum.bench.service_node', '--help'],
        cwd=root, env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src') + os.pathsep + os.environ.get('PYTHONPATH', '')}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and '--grant-dir' in result.stdout


def test_frozen_public_runtime_contains_service_dependencies_and_imports_without_private_home(tmp_path):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / 'bundle'
    result = subprocess.run([sys.executable, str(root / 'firmware/meshticulum-bench/build_runtime.py'),
        '--output-dir', str(output)], cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    record = json.loads((output / 'RUNTIME_BUNDLE.json').read_text())
    assert record['installed'] is False
    manifest = record['manifest']
    assert manifest['private_services_enabled'] is False
    assert manifest['public_service_formats'] == ['MR1.request', 'MR1.response']
    assert manifest['public_ip_service_formats'] == ['MR1.request', 'MR1.response']
    assert 'lantern_meshticulum/ip/node.py' in manifest['files']
    assert 'meshticulum/resources/resource.py' in manifest['files']
    assert 'lantern_meshticulum/bench/pilot_resource_node.py' in manifest['files']
    assert {'LICENSE', 'NOTICE', 'docs/THIRD_PARTY.md', 'docs/EXTRACTION.md',
            'provenance/UPSTREAM_SOURCE.json', 'provenance/THIRD_PARTY_SOURCE_AUDIT.json'} <= set(manifest['files'])
    assert not any(p.startswith('sage_lantern/') or 'sdk' in p for p in manifest['files'])
    extracted = tmp_path / 'extracted'
    extracted.mkdir()
    with tarfile.open(record['archive'], 'r:gz') as archive:
        archive.extractall(extracted, filter='data')
    assert (extracted / 'LICENSE').read_bytes() == (root / 'LICENSE').read_bytes()
    assert (extracted / 'NOTICE').read_bytes() == (Path(os.environ['MESHTICULUM_SOURCE']) / 'NOTICE').read_bytes()
    result = subprocess.run([sys.executable, '-c',
        'import sys; from lantern_meshticulum.bench import node; from meshticulum.bench import services, service_runner; '
        'node.BenchFlow; node.run_public_service; node.BenchFlow.__module__; '
        'from lantern_meshticulum.ip import node as ip_node; ip_node.run_public_service; '
        'from lantern_meshticulum.bench import pilot_provision, pilot_resources, pilot_resource_node; '
        'assert not any(n == "sage_lantern" or n.startswith("sage_lantern.") '
        'or n == "sage_sdk" or n.startswith("sage_sdk.") for n in sys.modules)'],
        cwd=extracted, env={**os.environ, 'PYTHONPATH': str(extracted)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
