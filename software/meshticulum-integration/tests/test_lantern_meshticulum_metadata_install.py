# SPDX-License-Identifier: Apache-2.0
"""Local metadata-only installation controls; native UID/final exec are modeled."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace

import pytest
from lantern_meshticulum.bench.pilot_ssh import SSHOperator
from meshticulum.bench.wire import WireError
from tests.test_lantern_meshticulum_bench_runtime import helper
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_pilot_ssh import config


@pytest.mark.parametrize('mode',[None,False,True])
def test_ssh_install_default_schema_and_explicit_metadata_option(monkeypatch,mode):
    cfg,provision=config(); raw=b'synthetic runtime'; source=b'# local source\n'
    cfg.update(runtime_sha256=hashlib.sha256(raw).hexdigest(),runtime_bootstrap_sha256=hashlib.sha256(source).hexdigest())
    operator=SSHOperator(cfg,provision_source=provision,runtime_source=source,clock=lambda:10,epoch=lambda:1000000000)
    def stage(argv,**kwargs):
        assert kwargs['input']==raw and '--metadata-only' not in argv[-1]
        return subprocess.CompletedProcess(argv,0,json.dumps(dict(schema='meshticulum.staged-pilot-runtime.v1',
            sha256=cfg['runtime_sha256'],path='/home/source/meshticulum-bench-staging/runtime-'+cfg['runtime_sha256']+'.tar.gz')).encode(),b'')
    observed={}
    def command(unit,argv,schema,**kwargs):
        observed.update(unit=unit,argv=argv,schema=schema)
        return dict(unit=unit,host_boot_id=cfg['units'][unit]['host_boot_id'],bundle_manifest_sha256=cfg['manifest_sha256'])
    monkeypatch.setattr(subprocess,'run',stage); monkeypatch.setattr(operator,'_command',command)
    options={} if mode is None else dict(metadata_only=mode)
    operator.install_runtime('unit-a',raw,**options)
    metadata=mode is True
    assert ('--metadata-only' in observed['argv']) is metadata
    assert observed['argv'][-1]==cfg['units']['unit-a']['host_boot_id']
    assert observed['schema']==('meshticulum.native-pilot-facts.v1' if metadata else 'meshticulum.native-bench-inspection.v1')


@pytest.mark.parametrize('mode',[None,0,1,'true',[],{}])
def test_invalid_metadata_mode_refuses_before_connection(monkeypatch,mode):
    cfg,provision=config(); raw=b'synthetic runtime'; source=b'# local source\n'
    cfg.update(runtime_sha256=hashlib.sha256(raw).hexdigest(),runtime_bootstrap_sha256=hashlib.sha256(source).hexdigest())
    operator=SSHOperator(cfg,provision_source=provision,runtime_source=source,clock=lambda:10,epoch=lambda:1000000000)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('unexpected connection'))
    with pytest.raises(WireError): operator.install_runtime('unit-a',raw,metadata_only=mode)


def archive():
    bodies={'bench_node.py':b'# synthetic final entry\n'}
    bodies['manifest.json']=json.dumps(dict(schema='meshticulum.bench-runtime-bundle.v1',
        files={name:hashlib.sha256(raw).hexdigest() for name,raw in bodies.items()})).encode()
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as bundle:
        for name,raw in bodies.items():
            entry=tarfile.TarInfo(name); entry.mode=0o600; entry.size=len(raw); bundle.addfile(entry,io.BytesIO(raw))
    return output.getvalue()


@pytest.mark.parametrize('options,action',[([], 'inspect'),(['--metadata-only'],'pilot-facts')])
def test_bootstrap_exec_action_selection_without_any_driver(tmp_path,monkeypatch,root_observation,options,action):
    bootstrap=helper('runtime_bootstrap'); raw=archive(); path=tmp_path/'runtime.tar.gz'; path.write_bytes(raw); path.chmod(0o600)
    real_path=Path
    mapped={'/var/lib/meshticulum-bench':tmp_path/'state','/opt/meshticulum-bench':tmp_path/'bundles'}
    monkeypatch.setattr(bootstrap,'Path',lambda value: mapped.get(str(value),real_path(value)))
    monkeypatch.setattr(bootstrap,'sys',SimpleNamespace(flags=SimpleNamespace(isolated=True),
        argv=['bootstrap','unit-a',str(path),hashlib.sha256(raw).hexdigest(),*options]))
    observed={}
    class FinalExec(Exception): pass
    def execve(executable,argv,environment):
        observed.update(executable=executable,argv=argv,environment=environment); raise FinalExec()
    monkeypatch.setattr(bootstrap.os,'execve',execve); monkeypatch.setattr(bootstrap.os,'chdir',lambda _:None)
    with pytest.raises(FinalExec): bootstrap.main()
    assert observed['argv'][-2:]==[action,'unit-a']
    assert observed['executable']=='/root/esp-venv-r3/bin/python'
    assert observed['argv'][1:3]==['-I','-B']
    assert (mapped['/var/lib/meshticulum-bench']/'runs').is_dir()
    assert (mapped['/var/lib/meshticulum-bench']/'objects').is_dir()


@pytest.mark.parametrize('options',[['--inspect'],['--metadata-only','extra'],['True']])
def test_bootstrap_invalid_option_refuses_before_archive_read(monkeypatch,options):
    bootstrap=helper('runtime_bootstrap')
    monkeypatch.setattr(bootstrap.os,'geteuid',lambda:0)
    monkeypatch.setattr(bootstrap,'sys',SimpleNamespace(flags=SimpleNamespace(isolated=True),
        argv=['bootstrap','unit-a','unused-path','a'*64,*options]))
    monkeypatch.setattr(bootstrap,'read_regular',lambda *a:pytest.fail('unexpected archive read'))
    with pytest.raises(RuntimeError,match='requires_isolated_root'): bootstrap.main()
