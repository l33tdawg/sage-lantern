"""Actual signed five-piece pack and local protected staging; no native grant."""
from copy import deepcopy
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from lantern_meshticulum.bench import pilot_seed as seed,pilot_ssh as ssh
from meshticulum.resources import lamplighter as ll,byte_bundle as bb
from tests.test_pilot_ssh import config
from tests.test_ip_node import facts

BASE=Path(__file__).resolve().parents[5]
FIXTURE=BASE/'meshticulum-proper-bundle-source-20261011/public-bundle-100000-exact32'
_spec=importlib.util.spec_from_file_location('aggregate_seed_bootstrap',Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/pilot_grant_bootstrap.py')
bootstrap=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(bootstrap)


def package(unit='unit-c',role='source'):
    manifest=json.loads((FIXTURE/'manifest.json').read_bytes());names=['manifest.json','manifest.sig.json']
    if role=='source':names+=['chunks/'+row['sha256']+'.json' for row in manifest['chunks']]
    return dict(schema='meshticulum.pilot-public-seed.v1',unit=unit,host_boot_id=facts(unit)['host_boot_id'],role=role,
        manifest_sha256=hashlib.sha256((FIXTURE/'manifest.json').read_bytes()).hexdigest(),
        trust=json.loads((FIXTURE/'trust.json').read_bytes()),files={n:base64.b64encode((FIXTURE/n).read_bytes()).decode() for n in names})


def stage(directory,value,script=ssh.PUBLIC_SEED_STAGE):
    raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    prefix='from pathlib import Path\nPath.home=classmethod(lambda cls:Path('+repr(str(directory))+'))\n'
    result=subprocess.run([sys.executable,'-I','-B','-c',prefix+script,hashlib.sha256(raw).hexdigest()],input=raw,capture_output=True,timeout=3)
    return raw,result


def test_actual223k_signed_source_stages_then_installs_and_verifies_all_decoded100k(tmp_path):
    value=package();raw,result=stage(tmp_path,value)
    assert 100000<len(raw)<700000 and len(value['files'])==7 and result.returncode==0
    record=ssh.one_record(result.stdout,'meshticulum.staged-public-seed.v1');path=Path(record['path'])
    assert bootstrap.load_package(path,record['sha256'],public_seed=True)==value
    with pytest.raises(RuntimeError):bootstrap.load_package(path,record['sha256'])
    state=tmp_path/'state';state.mkdir(mode=0o700)
    installed=seed.install(value,state=state,facts=facts('unit-c'))
    source=state/'public'/value['manifest_sha256']/'pack';cache=ll.VerifiedCache(source,value['trust'],value['manifest_sha256'])
    whole=bb.descriptor(cache.value);assert len(whole['parts'])==5 and whole['bytes']==100000
    for row in cache.value['chunks']:assert cache.piece(row['sha256'])
    assert installed['publisher_verified'] and installed['decoded_seed_bytes']<525000
    assert not installed['radio_opened'] and not installed['socket_opened'] and not installed['private_admission']
    assert seed.install(value,state=state,facts=facts('unit-c'))==installed

    foreign=value|{'schema':'meshticulum.public-pilot-package.v2'}
    foreign_raw=json.dumps(foreign,sort_keys=True,separators=(',',':')).encode();pin=hashlib.sha256(foreign_raw).hexdigest()
    foreign_path=path.parent/('public-seed-'+pin+'.json');foreign_path.write_bytes(foreign_raw);foreign_path.chmod(0o600)
    with pytest.raises(RuntimeError,match='large_public_seed_schema_required'):
        bootstrap.load_package(foreign_path,pin,public_seed=True)


@pytest.mark.parametrize('fault',('nonseed','role','recipient_body','trust','content','foreign_path','file_bound','aggregate'))
def test_large_selection_rejects_wrong_schema_role_trust_content_namespace_before_native_store(tmp_path,fault):
    value=package();name=next(n for n in value['files'] if n.startswith('chunks/'))
    if fault=='nonseed':value['schema']='meshticulum.public-pilot-package.v2'
    elif fault=='role':value['role']='private'
    elif fault=='recipient_body':value['role']='recipient'
    elif fault=='trust':value['trust']['keys'][0]['status']='revoked'
    elif fault=='content':value['files'][name]=base64.b64encode(b'changed').decode()
    elif fault=='foreign_path':value['files']['../secret.json']=value['files'].pop(name)
    elif fault=='file_bound':value['files'][name]=base64.b64encode(b'x'*65537).decode()
    else:value['files'][name]='x'*700001
    state=tmp_path/'state';state.mkdir(mode=0o700)
    with pytest.raises(Exception):seed.install(value,state=state,facts=facts('unit-c'))
    assert not (state/'public').exists()


def test_operator_seed_is_explicit_and_generic_child_limit_remains100000(monkeypatch):
    cfg,source=config();cfg['units']['unit-c']['host_boot_id']=facts('unit-c')['host_boot_id']
    operator=ssh.SSHOperator(cfg,provision_source=source,clock=lambda:10,epoch=lambda:1000000000)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('oversized generic package staged'))
    with pytest.raises(Exception):operator._package(package(),'unused')
    bad=package();bad['schema']='meshticulum.public-pilot-package.v2'
    with pytest.raises(Exception):operator._package(bad,'unused',public_seed=True)
    seen=[]
    monkeypatch.setattr(operator,'_package',lambda value,schema,**kwargs:seen.append((schema,kwargs)))
    operator.seed(package())
    assert seen==[('meshticulum.installed-public-pilot-seed.v1',{'public_seed':True})]


@pytest.mark.parametrize('fault',('nonseed','recipient_body','oversize'))
def test_direct_unprivileged_large_stager_refuses_before_file_creation(tmp_path,fault):
    value=package()
    if fault=='nonseed':value['schema']='meshticulum.public-pilot-package.v2'
    elif fault=='recipient_body':value['role']='recipient'
    else:value['files']['manifest.json']='x'*700001
    _,result=stage(tmp_path,value)
    assert result.returncode!=0 and not (tmp_path/'meshticulum-bench-staging').exists()


def test_selected_bootstrap_foreign_schema_source_path_fifo_and_ancestry_refuse(tmp_path,monkeypatch):
    value=package();raw,result=stage(tmp_path,value);record=ssh.one_record(result.stdout,'meshticulum.staged-public-seed.v1')
    path=Path(record['path'])
    foreign=path.parent/'other.json';foreign.write_bytes(raw);foreign.chmod(0o600)
    with pytest.raises(RuntimeError):bootstrap.load_package(foreign,record['sha256'],public_seed=True)
    path.unlink();os.mkfifo(path,0o600)
    start=time.monotonic()
    with pytest.raises(RuntimeError):bootstrap.load_package(path,record['sha256'],public_seed=True)
    assert time.monotonic()-start<1
    path.unlink();path.write_bytes(raw);path.chmod(0o600)
    actual=bootstrap.os.fdopen
    class MutatingRead:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def read(self,n):
            data=self.stream.read(n);path.parent.chmod(0o755);return data
    monkeypatch.setattr(bootstrap.os,'fdopen',lambda *a,**k:MutatingRead(actual(*a,**k)))
    with pytest.raises(RuntimeError,match='public_seed_ancestry_changed'):bootstrap.load_package(path,record['sha256'],public_seed=True)
