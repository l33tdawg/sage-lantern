import importlib.util
from pathlib import Path
import types

import pytest


ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('vision_input_collector',ROOT/'scripts/lantern/collect-fresh-vision-inputs.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_unknown_spec_refuses_before_loading_or_signing(monkeypatch):
    monkeypatch.setattr(MODULE,'load',lambda pin:pytest.fail('no dependency load'))
    with pytest.raises(ValueError):MODULE.collect({'unreviewed':'input'})


def test_duplicate_keys_are_not_accepted():
    with pytest.raises(ValueError):MODULE.unique([('actor','one'),('actor','two')])


def test_process_observation_exports_hash_not_arguments(tmp_path,monkeypatch):
    root=tmp_path/'123';root.mkdir()
    image=tmp_path/'image';image.write_bytes(b'fixture-executable')
    (root/'exe').symlink_to(image)
    (root/'stat').write_text('123 (fixture) S '+' '.join(['0']*18+['456']))
    service='sage-lantern-fixture.service'
    (root/'cgroup').write_text('0::/system.slice/'+service+'\n')
    raw=b'/fixture/image\0synthetic-not-exported-argument\0'
    (root/'cmdline').write_bytes(raw)
    real_path=Path
    monkeypatch.setattr(MODULE,'Path',lambda value,*args:tmp_path.joinpath(*args) if value=='/proc' else real_path(value,*args))
    value=dict(ActiveState='active',SubState='running',MainPID='123',ControlGroup='/system.slice/'+service)
    result,arguments=MODULE.observe_process(service,value)
    assert result['argv_sha256']==MODULE.digest(raw) and result['start_ticks']==456
    assert 'synthetic-not-exported-argument' not in str(result)
    assert arguments[-1]==b'synthetic-not-exported-argument'


def test_collector_has_no_mutating_cli_or_service_commands():
    source=Path(MODULE.__file__).read_text()
    assert "'--apply'" not in source
    assert 'collector.signed_observation' in source
    assert "'--inspect'" in source
    assert 'base.validate(expected,candidate_sha256=driver.R7)' in source
    assert 'load_identity(' not in source


@pytest.mark.parametrize('fault', [None, 'foreign', 'duplicate', 'wrong_port', 'wrong_owner'])
def test_listener_uses_exact_configured_port_and_kernel_owner(tmp_path, monkeypatch, fault):
    proc = tmp_path / 'proc'
    (proc / 'net').mkdir(parents=True)
    descriptors = proc / '123' / 'fd'
    descriptors.mkdir(parents=True)
    address = '00000000:1F90' if fault == 'foreign' else '0100007F:1F90'
    if fault == 'wrong_port': address = '0100007F:09DF'
    row = f'0: {address} 00000000:0000 0A 0 0 0 0 0 789\n'
    (proc / 'net' / 'tcp').write_text('header\n' + row * (2 if fault == 'duplicate' else 1))
    (descriptors / '7').symlink_to('socket:[999]' if fault == 'wrong_owner' else 'socket:[789]')
    real_path = Path
    def path(value, *args):
        value = str(value)
        if value == '/proc' or value.startswith('/proc/'):
            return proc.joinpath(value.removeprefix('/proc').lstrip('/'), *args)
        return real_path(value, *args)
    monkeypatch.setattr(MODULE, 'Path', path)
    if fault:
        with pytest.raises(ValueError): MODULE.listener(123, 8080)
    else:
        assert MODULE.listener(123, 8080) == '789'
