"""Management integrity controls; local children do not qualify devices."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from lantern_meshticulum.bench.pilot_ssh import Job, SSHOperator, PROMPT, STAGE, one_record
from meshticulum.bench.wire import WireError


def config():
    source=b'print("test bootstrap")'
    result=dict(schema='meshticulum.finite-ssh-pilot.v1',runtime_sha256='a'*64,manifest_sha256='b'*64,
        provision_sha256=hashlib.sha256(source).hexdigest(),runtime_bootstrap_sha256='c'*64,
        handoff_end_epoch_us=1200000000,port_base=35000,units={})
    for i,unit in enumerate(('unit-a','unit-b','unit-c'),1):
        result['units'][unit]=dict(ssh=['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','host-'+str(i)],
            python='/usr/bin/python3',sudo=unit!='unit-c',host_boot_id='00000000-0000-4000-8000-'+str(i).zfill(12),ip_address='192.0.2.'+str(i))
    return result,source


def operator(value=None):
    cfg,source=config()
    return SSHOperator(value or cfg,provision_source=source,clock=lambda:10,epoch=lambda:1000000000)


def test_constructor_connects_nothing_and_allocates_distinct_exact_endpoints(monkeypatch):
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('unexpected process'))
    op=operator()
    assert op.endpoints('unit-a','unit-b')=={'unit-a':('192.0.2.1',35000),'unit-b':('192.0.2.2',35001)}
    assert op.endpoints('unit-b','unit-a')=={'unit-b':('192.0.2.2',35002),'unit-a':('192.0.2.1',35003)}


@pytest.mark.parametrize('fault',['pin','window','hostkey','proxy','duplicate'])
def test_invalid_target_authority_or_source_pins_refused_before_connection(fault):
    cfg,source=config()
    if fault=='pin':cfg['provision_sha256']='d'*64
    if fault=='window':cfg['handoff_end_epoch_us']=900000000
    if fault=='hostkey':cfg['units']['unit-a']['ssh'][5]='StrictHostKeyChecking=no'
    if fault=='proxy':cfg['units']['unit-a']['ssh'][5]='ProxyCommand=unsafe'
    if fault=='duplicate':cfg['units']['unit-a']['ssh'][-1:-1]=['-o','StrictHostKeyChecking=no']
    with pytest.raises(WireError):operator(cfg)


def test_commands_pin_manifest_and_choose_tty_only_for_sudo():
    op=operator()
    root=op._argv('unit-a',['/usr/bin/python3','-I','-c','safe source'],root=True)
    assert root[1]=='-tt' and 'sudo' in root[-1] and '-n' in root[-1]
    stage=op._argv('unit-a',['python3','-I','-c','safe source'],root=False)
    assert stage[1]=='-T' and 'sudo' not in stage[-1]
    assert 'ControlMaster=no' in root and 'ForwardAgent=no' in root


def test_one_record_rejects_ambiguous_or_unexpected_schema():
    with pytest.raises(WireError):one_record(b'{"schema":"x"}\n{"schema":"x"}\n','x')
    with pytest.raises(WireError):one_record(b'{"schema":"other"}\n','x')
    assert one_record(PROMPT+b'\r\n{"schema":"x","ok":true}\n','x')['ok']


def test_password_reaches_only_one_prompt_and_is_never_retained_in_output():
    code="import sys;print('MESHTICULUM_SUDO_PROMPT:',end='',flush=True);assert sys.stdin.readline().strip()=='source-only-dummy';print('\\n{\"schema\":\"test\"}')"
    job=Job([sys.executable,'-c',code],password='source-only-dummy',deadline=time.monotonic()+3)
    try:
        assert job.wait()==0
        assert job.prompted and b'source-only-dummy' not in job.output
    finally:job.close()
    assert job.password is None


def test_credential_substring_in_native_path_or_service_name_is_not_an_echo():
    code="import sys;print('MESHTICULUM_SUDO_PROMPT:',end='',flush=True);assert sys.stdin.readline().strip()=='pilot';print('\\n{\"schema\":\"test\",\"path\":\"/opt/pilot/runtime\",\"service\":\"sage-pilot.service\"}')"
    job=Job([sys.executable,'-c',code],password='pilot',deadline=time.monotonic()+3)
    try:
        assert job.wait()==0 and job.prompted
        assert one_record(bytes(job.output),'test')['service']=='sage-pilot.service'
    finally:job.close()
    assert job.password is None


@pytest.mark.parametrize('output',[b'pilot\r\n',b'MESHTICULUM_SUDO_PROMPT:pilot\r\n',b'debug: pilot\n'])
def test_standalone_or_prompt_adjacent_credential_echo_is_cleared(output):
    code='import sys;sys.stdout.buffer.write('+repr(output)+');sys.stdout.flush()'
    job=Job([sys.executable,'-c',code],password='pilot',deadline=time.monotonic()+3)
    try:
        with pytest.raises(RuntimeError,match='credential_echo_refused'):job.wait()
        assert not job.output
    finally:job.close()
    assert job.password is None and job.process.poll() is not None


@pytest.mark.parametrize('failure',['repeated','echo','timeout'])
def test_bad_authentication_or_stalled_owned_child_is_reaped(failure):
    if failure=='repeated':code="import sys;print('MESHTICULUM_SUDO_PROMPT:',end='',flush=True);sys.stdin.readline();print('MESHTICULUM_SUDO_PROMPT:',end='',flush=True);sys.stdin.readline()"
    elif failure=='echo':code="print('source-only-dummy',flush=True)"
    else:code="import time;time.sleep(10)"
    job=Job([sys.executable,'-c',code],password='source-only-dummy',deadline=time.monotonic()+(.1 if failure=='timeout' else 3))
    try:
        with pytest.raises((WireError,RuntimeError)):job.wait()
    finally:job.close()
    assert job.process.poll() is not None and job.password is None
    if failure=='echo':assert not job.output


def stage(tmp_path,raw):
    prefix='from pathlib import Path\nPath.home=classmethod(lambda cls:Path('+repr(str(tmp_path))+'))\n'
    return subprocess.run([sys.executable,'-I','-B','-c',prefix+STAGE,hashlib.sha256(raw).hexdigest()],
        input=raw,capture_output=True,timeout=3)


def test_immutable_staging_reuses_identical_protected_bytes_without_rewriting(tmp_path):
    raw=b'synthetic staging object'
    first=stage(tmp_path,raw);assert first.returncode==0
    receipt=one_record(first.stdout,'meshticulum.staged-pilot-package.v1')
    path=Path(receipt['path']);before=path.stat()
    second=stage(tmp_path,raw);assert second.returncode==0
    assert one_record(second.stdout,'meshticulum.staged-pilot-package.v1')==receipt
    assert path.read_bytes()==raw and path.stat().st_ino==before.st_ino and path.stat().st_mtime_ns==before.st_mtime_ns


@pytest.mark.parametrize('fault',['contents','permissions','hardlink','symlink'])
def test_staging_refuses_existing_mutation_or_unsafe_identity_without_replacing_it(tmp_path,fault):
    raw=b'synthetic staging object'
    first=stage(tmp_path,raw);assert first.returncode==0
    path=Path(one_record(first.stdout,'meshticulum.staged-pilot-package.v1')['path'])
    if fault=='contents':path.write_bytes(b'changed staging contents')
    elif fault=='permissions':path.chmod(0o644)
    elif fault=='hardlink':(path.parent/'extra-link').hardlink_to(path)
    else:
        target=path.parent/'real-file';path.rename(target);path.symlink_to(target)
    before=path.lstat()
    assert stage(tmp_path,raw).returncode!=0
    after=path.lstat()
    assert (after.st_ino,after.st_mode,after.st_nlink)==(before.st_ino,before.st_mode,before.st_nlink)
