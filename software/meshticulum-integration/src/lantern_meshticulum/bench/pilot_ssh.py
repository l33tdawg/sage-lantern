# SPDX-License-Identifier: Apache-2.0
"""Explicit finite SSH management adapter for the public pilot.

No connection occurs on import or construction. Target configuration and a
current device window come from the operator. Sudo credentials stay in memory;
package data uses a non-PTY channel and never appears in command arguments.
"""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import uuid

from lantern_meshticulum.bench.pilot_provision import identifier
from meshticulum.bench.wire import require
from .pilot_parent_caps import canonical

PROMPT = b'MESHTICULUM_SUDO_PROMPT:'

# This script receives only the private package on stdin, without a terminal.
STAGE = '''import hashlib,json,os,stat,sys
from pathlib import Path
digest=sys.argv[1]
data=sys.stdin.buffer.read(100001)
assert len(data)<=100000 and hashlib.sha256(data).hexdigest()==digest
root=Path.home()/'meshticulum-bench-staging'
root.mkdir(mode=0o700,exist_ok=True)
s=root.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700
target=root/('pilot-'+digest+'.json')
try:fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
except FileExistsError:
 fd=os.open(target,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd);assert stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and s.st_size==len(data)
  with os.fdopen(fd,'rb',closefd=False) as inp:assert inp.read(100001)==data
 finally:os.close(fd)
else:
 with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:os.fsync(fd)
finally:os.close(fd)
print(json.dumps(dict(schema='meshticulum.staged-pilot-package.v1',path=str(target),sha256=digest)))
'''

# Explicit public-seed path. Child/grant staging above remains exactly100000.
# Bodies pass only through the unprivileged non-PTY stdin, never argv.
PUBLIC_SEED_STAGE = STAGE.replace('100001','700001').replace('100000','700000')
PUBLIC_SEED_STAGE = PUBLIC_SEED_STAGE.replace("'pilot-'+digest+'.json'","'public-seed-'+digest+'.json'")
PUBLIC_SEED_STAGE = PUBLIC_SEED_STAGE.replace('meshticulum.staged-pilot-package.v1','meshticulum.staged-public-seed.v1')
PUBLIC_SEED_STAGE = PUBLIC_SEED_STAGE.replace("root=Path.home()",'''value=json.loads(data)
assert type(value) is dict and set(value)=={'schema','unit','host_boot_id','role','manifest_sha256','trust','files'}
assert value['schema']=='meshticulum.pilot-public-seed.v1' and value['unit'] in ('unit-a','unit-b','unit-c')
assert value['role']=='source' or value['role']=='recipient' and value['unit'] in ('unit-a','unit-c')
assert type(value['files']) is dict and 2<=len(value['files'])<=10 and {'manifest.json','manifest.sig.json'}<=set(value['files'])
assert type(value['trust']) is dict and len(json.dumps(value['trust'],sort_keys=True).encode())<=16384
import base64
assert all(type(n) is str and type(v) is str and len(v)<=87384 for n,v in value['files'].items())
files={n:base64.b64decode(v,validate=True) for n,v in value['files'].items()}
assert all(len(v)<=65536 for v in files.values()) and sum(map(len,files.values()))<=525000
assert value['role']!='recipient' or set(files)=={'manifest.json','manifest.sig.json'}
root=Path.home()''')

# Archived argv evidence only: these exact v1 bytes belong to retained native
# command hashes. New commands use INVOKE below.
HISTORICAL_INVOKE_V1 = '''import hashlib,json,os,runpy,stat,sys
from pathlib import Path
path,pin,*args=sys.argv[1:]
root=Path(path).parent
for parent in (root,*root.parents):
 s=parent.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022
def read(p):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd);assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=2000000
  return os.read(fd,2000001)
 finally:os.close(fd)
raw=read(root/'manifest.json');assert hashlib.sha256(raw).hexdigest()==pin
m=json.loads(raw);assert m['schema']=='meshticulum.bench-runtime-bundle.v1' and m['private_services_enabled'] is False
assert hashlib.sha256(read(Path(path))).hexdigest()==m['files']['bench_node.py']
sys.argv=[path,*args]
runpy.run_path(path,run_name='__main__')
'''

# The fixed root launcher is checked against the operator's manifest pin, not
# merely against a potentially changed manifest's own source map.
INVOKE = '''import hashlib,json,os,runpy,stat,sys
from pathlib import Path
path,pin,*args=sys.argv[1:]
root=Path(path).parent
for parent in (root,*root.parents):
 s=parent.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022
def read(p):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd);assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=2000000
  return os.read(fd,2000001)
 finally:os.close(fd)
raw=read(root/'manifest.json');assert hashlib.sha256(raw).hexdigest()==pin
m=json.loads(raw);assert m['schema'] in ('meshticulum.bench-runtime-bundle.v1','meshticulum.bench-runtime-bundle.v2') and m['private_services_enabled'] is False
assert hashlib.sha256(read(Path(path))).hexdigest()==m['files']['bench_node.py']
sys.argv=[path,*args]
runpy.run_path(path,run_name='__main__')
'''


def one_record(raw, schema):
    records=[]
    for line in raw.replace(PROMPT,b'').decode('utf-8').splitlines():
        try:value=json.loads(line)
        except ValueError:continue
        if type(value) is dict and value.get('schema')==schema:records.append(value)
    require(len(records)==1,'missing_or_ambiguous_native_record')
    return records[0]


def preparation_schema(config,package):
    """Pure exact receipt dispatch; selection itself supplies no permission."""
    from .mcu_artifact import SSH_SCHEMA, PAID_SCHEMA, PROVISION_SCHEMA, validate_artifact, validate_metadata
    require(type(config) is dict and type(package) is dict,'invalid_pilot_preparation')
    from .lora_comparison import SSH_SCHEMA as COMPARISON_SSH_SCHEMA,KIND as COMPARISON_KIND
    from .mcu_artifact import validate_lora_artifact,lora_profile_name,require_lora_selector_scope
    if config.get('schema')==COMPARISON_SSH_SCHEMA:
        validate_lora_artifact(config['lora_mcu_artifact']);require_lora_selector_scope(COMPARISON_KIND)
        require(package.get('schema')=='meshticulum.public-pilot-package.v2'
            and package['unit'] in ('unit-a','unit-c') and package['transport']=='lora'
            and 'radio_modem' not in package['grant'],'comparison_original_package_changed')
        from meshticulum.bench.serial_protocol import RadioProfile
        lora_profile_name(RadioProfile(**package['grant']['profile']))
        return 'meshticulum.provisioned-public-pilot.v2'
    if config.get('schema')==SSH_SCHEMA:
        selected=validate_artifact(config['mcu_artifact'])
        require(package.get('schema')==PAID_SCHEMA and package['unit'] in ('unit-a','unit-c')
            and validate_artifact(package['mcu_artifact'])==selected
            and validate_metadata(package['parent']['child']['metadata'])==selected
            and package['grant'].get('radio_modem')=='gfsk250','ssh_mcu_original_package_changed')
        return PROVISION_SCHEMA
    require('mcu_artifact' not in config and 'lora_mcu_artifact' not in config
            and package.get('schema')=='meshticulum.public-pilot-package.v2',
            'pilot_parent_package_required')
    return 'meshticulum.provisioned-public-pilot.v2'


class Job:
    """An owned local SSH process; native liveness is checked separately."""
    def __init__(self,argv,*,password=None,deadline,clock=time.monotonic,popen=subprocess.Popen):
        self.process=popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           bufsize=0,start_new_session=True)
        self.password=password;self.deadline=deadline;self.clock=clock
        self.output=bytearray();self.prompted=False;self.closed=False

    def pump(self):
        if self.closed:return
        while select.select([self.process.stdout],[],[],0)[0]:
            part=os.read(self.process.stdout.fileno(),4096)
            if not part:break
            self.output.extend(part)
            require(len(self.output)<=1000000,'native_management_output_limit')
            # A hostname or source path can contain the same characters as a
            # short credential. Refuse an echoed credential token, including
            # one immediately after the sudo prompt, without treating those
            # unrelated path/name substrings as authentication output.
            if self.password and re.search(
                    rb'(?<![A-Za-z0-9._/-])'+re.escape(self.password.encode())+rb'(?![A-Za-z0-9._/-])',
                    bytes(self.output).replace(PROMPT,b'')):
                self.output.clear()
                raise RuntimeError('credential_echo_refused')
            count=self.output.count(PROMPT)
            if count:
                require(count==1 and self.password is not None,'unexpected_or_repeated_sudo_prompt')
                if not self.prompted:
                    self.process.stdin.write(self.password.encode()+b'\n');self.process.stdin.flush()
                    self.prompted=True

    def poll(self):
        self.pump()
        code=self.process.poll()
        require(code is not None or self.clock()<self.deadline,'native_management_deadline')
        return code

    def wait(self):
        while self.poll() is None:time.sleep(.01)
        self.pump()
        return self.process.returncode

    def close(self):
        if self.closed:return
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        self.process.stdin.close();self.process.stdout.close()
        self.password=None;self.closed=True


class SSHOperator:
    def __init__(self,config,*,provision_source,runtime_source=None,passwords=None,clock=time.monotonic,epoch=None,
                 parallel_preparation=False,native_phase_timing=False):
        require(type(parallel_preparation) is bool,'invalid_parallel_preparation_mode')
        require(type(native_phase_timing) is bool,'invalid_native_phase_timing_mode')
        self.parallel_preparation=parallel_preparation;self._preparation_lock=Lock()
        self.native_phase_timing=native_phase_timing
        require(type(config) is dict,'invalid_ssh_pilot_config')
        from .pilot_bundle import SSH_SCHEMA_V2, SSH_SCHEMA_V3, COMMITTED_CONTRACTS, public_context_commitment
        from .mcu_artifact import SSH_SCHEMA as MCU_SSH_SCHEMA, KIND as MCU_KIND, validate_artifact, require_selector_scope
        from .lora_comparison import SSH_SCHEMA as COMPARISON_SSH_SCHEMA,KIND as COMPARISON_KIND
        from .mcu_artifact import validate_lora_artifact,require_lora_selector_scope
        schemas=tuple(v[5] for v in COMMITTED_CONTRACTS.values())
        selected_mcu=config.get('schema')==MCU_SSH_SCHEMA
        selected_lora=config.get('schema')==COMPARISON_SSH_SCHEMA
        versioned=config.get('schema') in schemas or selected_mcu
        fields={'schema','runtime_sha256','manifest_sha256','provision_sha256','runtime_bootstrap_sha256',
                'handoff_end_epoch_us','port_base','units'}
        expected=fields|{'bundle_parent_id','bundle_context','bundle_capture_commitment'} if versioned else fields
        if selected_mcu:expected|={'mcu_artifact'}
        if selected_lora:expected|={'lora_mcu_artifact','comparison_clock'}
        require(set(config)==expected
            and config['schema'] in ('meshticulum.finite-ssh-pilot.v1',*schemas,MCU_SSH_SCHEMA,COMPARISON_SSH_SCHEMA),'invalid_ssh_pilot_config')
        commitment=None
        if versioned:
            commitment=public_context_commitment(config['bundle_context'],parent_id=config['bundle_parent_id'])
            require(config['schema']==(MCU_SSH_SCHEMA if selected_mcu else COMMITTED_CONTRACTS[commitment['qualification_kind']][5])
                    and config['bundle_capture_commitment']==commitment
                    and config['handoff_end_epoch_us']==commitment['phase_end_epoch_us'],'ssh_v2_original_context_changed')
        if selected_mcu:
            validate_artifact(config['mcu_artifact']);require_selector_scope(MCU_KIND)
            require(commitment['qualification_kind']==MCU_KIND,'ssh_mcu_original_kind_changed')
        if selected_lora:
            validate_lora_artifact(config['lora_mcu_artifact']);require_lora_selector_scope(COMPARISON_KIND)
            comparison=config['comparison_clock']
            require(type(comparison) is dict and set(comparison)=={'phase_start_epoch_us','phase_end_epoch_us',
                'original_monotonic_us','deadline_monotonic_us'} and all(type(v) is int for v in comparison.values())
                and comparison['phase_end_epoch_us']==config['handoff_end_epoch_us']
                and comparison['phase_end_epoch_us']-comparison['phase_start_epoch_us']==600000000
                and comparison['deadline_monotonic_us']-comparison['original_monotonic_us']==600000000,
                'ssh_comparison_original_clock_changed')
        for key in ('runtime_sha256','manifest_sha256','provision_sha256','runtime_bootstrap_sha256'):
            require(type(config[key]) is str and re.fullmatch('[0-9a-f]{64}',config[key]),'invalid_ssh_pilot_pin')
        require(hashlib.sha256(provision_source).hexdigest()==config['provision_sha256'], 'pilot_provision_source_changed')
        if runtime_source is not None:
            require(hashlib.sha256(runtime_source).hexdigest()==config['runtime_bootstrap_sha256'],'pilot_runtime_bootstrap_changed')
        require(type(config['units']) is dict and set(config['units'])=={'unit-a','unit-b','unit-c'},'pilot_three_exact_hosts_required')
        self.config=json.loads(json.dumps(config));self.source=provision_source.decode('utf-8')
        self.runtime_source=runtime_source.decode('utf-8') if runtime_source is not None else None
        self.clock=clock;self.epoch=epoch or (lambda:time.time_ns()//1000)
        now_epoch,now_mono=self.epoch(),self.clock()
        remaining=(config['handoff_end_epoch_us']-now_epoch)/1e6
        maximum=(commitment['phase_end_epoch_us']-commitment['phase_start_epoch_us'])/1000000 if versioned else (600 if selected_lora else 900)
        require(20<remaining<=maximum,'current_finite_ssh_window_required')
        self.deadline=now_mono+remaining
        if versioned:
            original_end=(commitment['original_monotonic_us']+COMMITTED_CONTRACTS[commitment['qualification_kind']][2])/1000000
            require(commitment['phase_start_epoch_us']<=now_epoch and commitment['original_monotonic_us']/1000000<=now_mono<original_end,
                    'ssh_v2_original_clock_expired_or_regressed')
            self.deadline=min(self.deadline,original_end)
        if selected_lora:
            require(comparison['phase_start_epoch_us']<=now_epoch<comparison['phase_end_epoch_us']
                and comparison['original_monotonic_us']/1000000<=now_mono<comparison['deadline_monotonic_us']/1000000,
                'ssh_comparison_original_clock_expired_or_regressed')
            self.deadline=min(self.deadline,comparison['deadline_monotonic_us']/1000000)
        self.passwords=dict(passwords or {})
        self.next_port=config['port_base'];require(type(self.next_port) is int and 1024<=self.next_port<=65400,'invalid_pilot_port_range')
        self.jobs={};self.events=[];self.connections=None
        self.management_cleanup_jobs=[];self.management_cleanup_audits=[]
        for unit,row in self.config['units'].items():
            require(type(row) is dict and set(row)=={'ssh','python','sudo','host_boot_id','ip_address'},'invalid_pilot_host_config')
            require(type(row['sudo']) is bool and type(row['python']) is str and row['python'].startswith('/'),'invalid_pilot_host_python')
            require(str(uuid.UUID(row['host_boot_id']))==row['host_boot_id'],'invalid_pilot_host_boot')
            require(str(ipaddress.ip_address(row['ip_address']))==row['ip_address'],'invalid_pilot_host_address')
            self._ssh(unit,pty=False)

    def _ssh(self,unit,*,pty,use_connections=True):
        argv=self.config['units'][unit]['ssh']
        require(type(argv) is list and len(argv)>=6 and Path(argv[0]).name=='ssh'
                and all(type(x) is str and '\n' not in x for x in argv),'invalid_pinned_ssh_argv')
        allowed={'BatchMode','StrictHostKeyChecking','ConnectTimeout','HostKeyAlias','UserKnownHostsFile','IdentitiesOnly','ServerAliveInterval','ServerAliveCountMax'}
        index=1;options={};result=[argv[0],'-tt' if pty else '-T']
        while index<len(argv)-1:
            flag=argv[index]
            if flag in ('-T','-tt'):index+=1;continue
            require(flag in ('-o','-i','-p') and index+1<len(argv)-1,'unsupported_ssh_option')
            value=argv[index+1]
            if flag=='-o':
                key,sep,entry=value.partition('=');require(sep and key in allowed and key not in options,'unsupported_ssh_override')
                options[key]=entry
            result.extend((flag,value));index+=2
        require(index==len(argv)-1 and not argv[-1].startswith('-')
                and re.fullmatch('[A-Za-z0-9._:@\\[\\]-]+',argv[-1])
                and options.get('StrictHostKeyChecking')=='yes' and options.get('BatchMode')=='yes',
                'pinned_noninteractive_ssh_required')
        control = ['-o','ControlMaster=no','-o','ControlPath=none']
        if use_connections and self.connections is not None:
            # A missing control socket must fail rather than silently opening
            # an unrelated replacement SSH connection.
            control = ['-o','ControlMaster=no','-o','ControlPath='+str(self.connections.path(unit)),
                       '-o','ProxyCommand=/usr/bin/false']
        result.extend((*control,'-o','ClearAllForwardings=yes','-o','ForwardAgent=no',
                       '-o','ForwardX11=no','-o','PermitLocalCommand=no',argv[-1]))
        return result

    def open_connections(self):
        """Explicit finite management reuse, before the resource clock starts."""
        from lantern_meshticulum.bench.pilot_connections import Connections
        require(self.connections is None and self.clock()+20<self.deadline,
                'ssh_control_duplicate_or_expired')
        connections=Connections(clock=self.clock,deadline=self.deadline,job_factory=Job)
        self.connections=connections
        try:
            for unit in self.config['units']:
                connections.open(unit,self._ssh(unit,pty=False,use_connections=False))
            self.events.append(dict(schema='meshticulum.owned-ssh-connections.v1',opened=True,
                units=list(connections.jobs),outside_handoff=self.clock()>=self.deadline))
        except BaseException as setup_error:
            try:connections.close()
            except BaseException as cleanup_error:
                # Keep the original setup refusal visible even when cleanup
                # needs another explicit attempt with the retained owner.
                raise setup_error from cleanup_error
            finally:
                if connections.closed:self.connections=None
            raise

    def close_connections(self):
        if self.connections is None:return
        require(all(job.process.poll() is not None for job in list(self.jobs.values())+self.management_cleanup_jobs),
                'ssh_control_clients_still_live')
        connections=self.connections
        try:connections.close()
        finally:
            if connections.closed:self.connections=None
            self.events.append(dict(schema='meshticulum.owned-ssh-connections.v1',opened=False,
                all_master_processes_terminal=all(job.process.poll() is not None for job in connections.jobs.values()),
                outside_handoff=self.clock()>=self.deadline))

    def _argv(self,unit,command,*,root,use_connections=True):
        row=self.config['units'][unit];sudo=root and row['sudo']
        if sudo:command=['sudo','-p',PROMPT.decode(),*(['-n'] if self.passwords.get(unit) is None else []),'--',*command]
        return self._ssh(unit,pty=sudo,use_connections=use_connections)+[shlex.join(command)]

    def _command(self,unit,command,schema,*,root=True,cleanup=False,cleanup_transport_headroom=0,
                 bundle_management_expires_epoch_us=None):
        require(type(cleanup_transport_headroom) is int and cleanup_transport_headroom in (0,2),
                'invalid_management_cleanup_headroom')
        require(not cleanup_transport_headroom or (cleanup and root
            and schema=='meshticulum.management-input-cleaned.v1' and len(command)>11
            and command[:3]==['/usr/bin/timeout','--kill-after=2s','5s']
            and command[11]=='cleanup-management-input'),'management_cleanup_headroom_scope')
        audit=None
        if cleanup_transport_headroom:
            audit=dict(unit=unit,schema=schema,remote_housekeeping_bound_s=5,remote_kill_grace_s=2,
                transport_headroom_s=2,local_terminal=False,closed=False,success=False,returncode=None,
                output=None,output_sha256=None,error_type=None)
            self.management_cleanup_audits.append(audit)
        deadline=min(self.clock()+5,self.deadline)
        if bundle_management_expires_epoch_us is not None:
            from .profiles import GFSK250_FAST_BUNDLE_KIND
            commitment=self.config.get('bundle_capture_commitment',{})
            actions={
                'pilot-verify':('meshticulum.pilot-verified-piece.v1',('unit-a',)),
                'pilot-fast-admit':('meshticulum.native-fast-stage-admitted.v1',('unit-a','unit-c')),
                'pilot-bundle-status':('meshticulum.native-public100k-bundle-status.v1',('unit-a',)),
                'pilot-bundle-finish':('meshticulum.native-public100k-bundle-finish.v1',('unit-a',)),
            }
            action=command[7] if len(command)>8 else None
            require(root and not cleanup and not cleanup_transport_headroom and action in actions
                and schema==actions[action][0] and unit in actions[action][1]
                and command[1:5]==['-I','-B','-c',INVOKE] and command[8]==unit
                and commitment.get('qualification_kind')==GFSK250_FAST_BUNDLE_KIND,
                'extended_bundle_management_scope')
            epoch,mono=self.epoch(),self.clock()
            require(type(bundle_management_expires_epoch_us) is int
                and epoch<bundle_management_expires_epoch_us<=commitment['expires_epoch_us']
                and bundle_management_expires_epoch_us<=self.config['handoff_end_epoch_us'],
                'original_bundle_management_window_expired_or_changed')
            if action!='pilot-verify':
                require(command[9:11]==['--parent-id',self.config['bundle_parent_id']],
                    'bundle_management_parent_changed')
                if action=='pilot-fast-admit':
                    require(command[11:13] in [['--part-index',str(i)] for i in range(5)]
                        and command[15:17]==['--expires-epoch-us',str(bundle_management_expires_epoch_us)],
                        'bundle_management_part_window_changed')
                else:
                    require(bundle_management_expires_epoch_us==commitment['expires_epoch_us'],
                        'bundle_management_whole_window_changed')
            # Publisher acceptance, progress proof and whole-file verification
            # retain their native checks. Only these fast-bundle process waits
            # differ from the five-second status/metadata default.
            # Capture this monotonic end once, before argument/SSH validation.
            deadline=min(mono+30,self.deadline,mono+(bundle_management_expires_epoch_us-epoch)/1000000)
        require(self.clock()<deadline or cleanup,'ssh_pilot_window_expired')
        if cleanup:deadline=self.clock()+5
        try:argv=self._argv(unit,command,root=root)
        except (OSError,ValueError,RuntimeError):
            if not cleanup:raise
            # An explicitly requested native stop/retirement may need a new
            # pinned management connection after its control master is lost.
            # Dispatch never takes this recovery path. Native pidfd, boot and
            # run bindings are still checked by the cleanup action.
            argv=self._argv(unit,command,root=root,use_connections=False)
            self.events.append(dict(unit=unit,schema='meshticulum.ssh-cleanup-recovery.v1',
                cleanup=True,outside_handoff=self.clock()>=self.deadline))
        if cleanup_transport_headroom:
            # Argument/connection validation does not consume the remote
            # housekeeping interval. Five remote seconds + its fixed two-
            # second kill grace + two seconds for SSH/sudo/result transport.
            # This branch cannot dispatch or extend an application callback.
            deadline=self.clock()+5+2+cleanup_transport_headroom
            audit['local_deadline_monotonic']=deadline
        job=None
        try:
            job=Job(argv,password=self.passwords.get(unit),deadline=deadline,clock=self.clock)
            if audit is not None:self.management_cleanup_jobs.append(job)
            require(job.wait()==0,'native_management_command_failed')
            value=one_record(bytes(job.output),schema)
            self.events.append(dict(unit=unit,schema=schema,cleanup=cleanup,outside_handoff=self.clock()>=self.deadline))
            if audit is not None:audit['remote_receipt_valid']=True
            return value
        except BaseException as error:
            if audit is not None:
                audit['error_type']=type(error).__name__
                reason=str(error);audit['reason']=reason if re.fullmatch('[a-z_]{1,128}',reason) else None
            raise
        finally:
            try:
                if job is not None:job.close()
            except BaseException as error:
                if audit is not None and audit['error_type'] is None:
                    audit['error_type']=type(error).__name__
                    reason=str(error);audit['reason']=reason if re.fullmatch('[a-z_]{1,128}',reason) else None
                raise
            finally:
                if audit is not None and job is not None:
                    output=bytes(job.output)
                    audit.update(local_terminal=job.process.poll() is not None,closed=job.closed,
                        returncode=job.process.returncode,output=output.decode('utf-8','replace'),
                        output_sha256=hashlib.sha256(output).hexdigest(),credentials_cleared=job.password is None)
                    audit['success']=(audit.get('remote_receipt_valid') is True and audit['error_type'] is None
                        and audit['local_terminal'] and audit['closed'] and audit['returncode']==0)
                    self.events.append(dict(audit))

    def _action(self,unit,action,run_id=None,*extra,cleanup=False,schema,bundle_management_expires_epoch_us=None):
        row=self.config['units'][unit]
        launcher='/opt/meshticulum-bench/runtime-'+self.config['runtime_sha256'][:16]+'/bench_node.py'
        args=[action,unit,*(['--run-id',identifier(run_id)] if run_id is not None else []),*extra]
        options={} if bundle_management_expires_epoch_us is None else dict(bundle_management_expires_epoch_us=bundle_management_expires_epoch_us)
        return self._command(unit,[row['python'],'-I','-B','-c',INVOKE,launcher,self.config['manifest_sha256'],*args],schema,cleanup=cleanup,**options)

    def endpoints(self,sender,receiver):
        require(self.clock()<self.deadline and self.next_port<=65534,'ssh_pilot_port_or_window_exhausted')
        value={sender:(self.config['units'][sender]['ip_address'],self.next_port),
               receiver:(self.config['units'][receiver]['ip_address'],self.next_port+1)}
        self.next_port+=2;return value

    def _package(self,package,schema,*,public_seed=False):
        require(type(public_seed) is bool,'invalid_public_seed_staging_selection')
        unit=package['unit'];row=self.config['units'][unit]
        if public_seed:
            from .pilot_seed import validate
            validate(package,facts=dict(unit=unit,host_boot_id=row['host_boot_id']))
        raw=json.dumps(package,sort_keys=True,separators=(',',':')).encode();digest=hashlib.sha256(raw).hexdigest()
        maximum=700000 if public_seed else 100000
        prefix='public-seed-' if public_seed else 'pilot-'
        require(len(raw)<=maximum and self.clock()<self.deadline,'pilot_staging_bounds')
        result=subprocess.run(self._argv(unit,['python3','-I','-B','-c',PUBLIC_SEED_STAGE if public_seed else STAGE,digest],root=False),input=raw,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=min(5,self.deadline-self.clock()))
        require(result.returncode==0,'pilot_package_staging_failed')
        staged=one_record(result.stdout,'meshticulum.staged-public-seed.v1' if public_seed else 'meshticulum.staged-pilot-package.v1')
        path=Path(staged['path'])
        require(staged['sha256']==digest and path.is_absolute() and path.name==prefix+digest+'.json'
                and path.parent.name=='meshticulum-bench-staging' and '..' not in path.parts,
                'wrong_staged_pilot_package')
        value=self._command(unit,[row['python'],'-I','-B','-c',self.source,unit,staged['path'],digest,
            self.config['runtime_sha256'],self.config['manifest_sha256'],*(['--public-seed'] if public_seed else [])],schema)
        require(value['unit']==unit and value['package_sha256']==digest,'wrong_native_provision_receipt')
        return value

    def stage_management_input(self,unit,action,payload,*,parent_id,operation_sha256,nonce,original_end_epoch_us):
        """Explicit non-PTY binary-file lane; no package, grant or runtime activation."""
        from .management_input import (STAGE_SOURCE,STAGE_SHA256,SCHEMA,DIRECTORY,encode,parameters)
        parameters(unit,action,parent_id,operation_sha256,nonce,original_end_epoch_us)
        require(original_end_epoch_us<=self.config['handoff_end_epoch_us']
                and self.epoch()<original_end_epoch_us and self.clock()<self.deadline,
                'management_input_original_window')
        compressed,csha,rsha=encode(payload)
        remaining=min(5,self.deadline-self.clock(),(original_end_epoch_us-self.epoch())/1000000)
        require(remaining>1,'management_input_staging_reserve')
        # Existing sudo Job behavior is untouched. This channel has no PTY or password.
        argv=self._argv(unit,['/usr/bin/timeout','--kill-after=1s',str(remaining-1)+'s',
            'python3','-I','-B','-c',STAGE_SOURCE,unit,action,parent_id,operation_sha256,nonce,csha,rsha,str(original_end_epoch_us)],root=False)
        result=subprocess.run(argv,input=compressed,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=remaining)
        require(result.returncode==0 and len(result.stdout)<=1000000 and len(result.stderr)<=1000000,
                'management_input_staging_failed')
        value=one_record(result.stdout,SCHEMA);path=Path(value['path'])
        require(value['unit']==unit and value['action']==action and value['parent_id']==parent_id
                and value['operation_sha256']==operation_sha256 and value['nonce']==nonce
                and value['compressed_sha256']==csha and value['raw_sha256']==rsha
                and value['original_end_epoch_us']==original_end_epoch_us and value['bytes']==len(compressed)
                and type(value['owner_uid']) is int and value['owner_uid']>=0
                and path.is_absolute() and len(str(path))<=1024 and '..' not in path.parts
                and path.parent.name==DIRECTORY and path.name=='rpc-'+nonce+'-'+csha+'.xz'
                and type(value['file_identity']) is list and len(value['file_identity'])==9
                and type(value['directory_identity']) is list and len(value['directory_identity'])==5
                and all(type(x) is int for x in value['file_identity']+value['directory_identity']),
                'management_input_staged_receipt_changed')
        require(self.epoch()<original_end_epoch_us and self.clock()<self.deadline,'management_input_window_expired')
        self.events.append(dict(unit=unit,schema=SCHEMA,stage_source_sha256=STAGE_SHA256,
            compressed_sha256=csha,raw_sha256=rsha,bytes=len(compressed),nonce=nonce))
        return value

    def prepare(self,package):
        row=self.config['units'][package['unit']]
        require(package['grant']['host_boot_id']==row['host_boot_id']
                and package['application_expires_epoch_us']<=self.config['handoff_end_epoch_us']-10000000,
                'pilot_package_outside_boot_or_cleanup_window')
        from .mcu_artifact import SSH_SCHEMA, KIND, require_selector_scope
        schema=preparation_schema(self.config,package)
        if self.config.get('schema')==SSH_SCHEMA:
            require_selector_scope(KIND)
        return self._package(package,schema)

    def prepare_pair(self,packages):
        """Explicit two-unit preparation; drain both callbacks before returning.

        Calls the same prepare hook, including its genuine guards and native
        claim/capture. No launch, controller ledger access or deadline change.
        """
        require(self.parallel_preparation is True and type(packages) is dict and len(packages)==2
                and all(unit in self.config['units'] and type(package) is dict and package.get('unit')==unit
                        for unit,package in packages.items()),'invalid_parallel_preparation_pair')
        with self._preparation_lock:
            require(self.clock()<self.deadline,'ssh_pilot_window_expired')
            results={};failures=[]
            with ThreadPoolExecutor(max_workers=2,thread_name_prefix='meshticulum-prepare') as pool:
                futures={unit:pool.submit(self.prepare,package) for unit,package in packages.items()}
                for unit,future in futures.items():
                    try:results[unit]=future.result()
                    except BaseException as error:failures.append(error)
            if failures:raise failures[0]
            require(self.clock()<self.deadline,'ssh_pilot_window_expired')
            return results

    def seed(self,package):
        require(package['host_boot_id']==self.config['units'][package['unit']]['host_boot_id'],'public_seed_wrong_native_boot')
        return self._package(package,'meshticulum.installed-public-pilot-seed.v1',public_seed=True)

    def install_runtime(self,unit,archive,*,metadata_only=False):
        require(type(metadata_only) is bool and self.runtime_source is not None and len(archive)<=2000000
                and hashlib.sha256(archive).hexdigest()==self.config['runtime_sha256']
                and self.clock()<self.deadline,'pilot_runtime_archive_changed_or_expired')
        # Same create-only private staging discipline, with a separate name and
        # bound for a binary runtime archive rather than a grant package.
        stage=STAGE.replace('100001','2000001').replace('100000','2000000')
        stage=stage.replace("'pilot-'+digest+'.json'","'runtime-'+digest+'.tar.gz'")
        stage=stage.replace('meshticulum.staged-pilot-package.v1','meshticulum.staged-pilot-runtime.v1')
        result=subprocess.run(self._argv(unit,['python3','-I','-B','-c',stage,self.config['runtime_sha256']],root=False),
            input=archive,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=min(5,self.deadline-self.clock()))
        require(result.returncode==0,'pilot_runtime_staging_failed')
        staged=one_record(result.stdout,'meshticulum.staged-pilot-runtime.v1')
        path=Path(staged['path'])
        require(path.is_absolute() and path.parent.name=='meshticulum-bench-staging'
                and path.name=='runtime-'+self.config['runtime_sha256']+'.tar.gz'
                and staged['sha256']==self.config['runtime_sha256'],'wrong_staged_pilot_runtime')
        # Check the independent host boot before the installer makes any write.
        prefix="from pathlib import Path\nimport sys\nassert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==sys.argv.pop()\n"
        row=self.config['units'][unit]
        mode=['--metadata-only'] if metadata_only else []
        schema='meshticulum.native-pilot-facts.v1' if metadata_only else 'meshticulum.native-bench-inspection.v1'
        value=self._command(unit,[row['python'],'-I','-B','-c',prefix+self.runtime_source,unit,
            staged['path'],self.config['runtime_sha256'],*mode,row['host_boot_id']],schema)
        require(value['unit']==unit and value['host_boot_id']==row['host_boot_id']
                and value['bundle_manifest_sha256']==self.config['manifest_sha256'],'wrong_installed_pilot_runtime')
        return value

    def start(self,unit,pair):
        run=identifier(pair['run_id']);require((unit,run) not in self.jobs and self.clock()<self.deadline,'pilot_job_duplicate_or_expired')
        require(type(self.native_phase_timing) is bool and (not self.native_phase_timing or pair['transport']=='lora'),
                'native_phase_timing_requires_radio_service')
        row=self.config['units'][unit];launcher='/opt/meshticulum-bench/runtime-'+self.config['runtime_sha256'][:16]+'/bench_node.py'
        action='service-run' if pair['transport']=='lora' else 'ip-service-run'
        command=[row['python'],'-I','-B','-c',INVOKE,launcher,self.config['manifest_sha256'],action,unit,'--run-id',run]
        readiness=pair.get('sender_receive_ready',False)
        require(type(readiness) is bool and (not readiness or pair['transport']=='lora'),
                'invalid_native_sender_receive_ready_mode')
        if readiness and unit==pair['sender']:command.append('--sender-receive-ready')
        if self.native_phase_timing:command.append('--native-phase-timing')
        if unit==pair['receiver'] and pair.get('test_rx_cutoff') is not None:
            command.extend(('--test-rx-cutoff',str(pair['test_rx_cutoff'])))
        job=Job(self._argv(unit,command,root=True),password=self.passwords.get(unit),deadline=self.deadline-10,clock=self.clock)
        self.jobs[(unit,run)]=job;return job

    def status(self,unit,run):return self._action(unit,'pilot-status',run,schema='meshticulum.native-public-job-status.v1')
    def stop(self,unit,run):return self._action(unit,'pilot-stop',run,cleanup=True,schema='meshticulum.native-public-job-status.v1')
    def revoke(self,unit,run,transport):return self._action(unit,'pilot-retire',run,cleanup=True,schema='meshticulum.retired-public-pilot.v1')
    def collect(self,unit,run):return self._action(unit,'pilot-result',run,schema='meshticulum.public-pilot-result-record.v1')['result']
    def verify(self,unit,campaign,request,response,*,application_expires_epoch_us=None):
        options={} if application_expires_epoch_us is None else dict(bundle_management_expires_epoch_us=application_expires_epoch_us)
        return self._action(unit,'pilot-verify',response,'--campaign-id',identifier(campaign),'--request-run-id',identifier(request),
                            schema='meshticulum.pilot-verified-piece.v1',**options)
    def fast_complete(self,unit,run,proof):
        return self._action(unit,'pilot-fast-complete',identifier(run),'--proof-base64',
            base64.b64encode(canonical(proof)).decode(),schema='meshticulum.native-fast-completion-delivered.v1')
    def fast_admit(self,unit,parent_id,index,issued,expires,proof):
        return self._action(unit,'pilot-fast-admit',None,'--parent-id',identifier(parent_id),
            '--part-index',str(index),'--issued-epoch-us',str(issued),'--expires-epoch-us',str(expires),
            '--proof-base64',base64.b64encode(canonical(proof)).decode(),schema='meshticulum.native-fast-stage-admitted.v1',
            **self._fast_bundle_wait(expires))
    def _fast_bundle_wait(self,expires=None):
        from .profiles import GFSK250_FAST_BUNDLE_KIND
        commitment=self.config.get('bundle_capture_commitment',{})
        if commitment.get('qualification_kind')!=GFSK250_FAST_BUNDLE_KIND:return {}
        return dict(bundle_management_expires_epoch_us=commitment['expires_epoch_us'] if expires is None else expires)
    def bundle_status(self,unit,parent_id):
        return self._action(unit,'pilot-bundle-status',None,'--parent-id',identifier(parent_id),
            schema='meshticulum.native-public100k-bundle-status.v1',**self._fast_bundle_wait())
    def bundle_finish(self,unit,parent_id):
        return self._action(unit,'pilot-bundle-finish',None,'--parent-id',identifier(parent_id),
            schema='meshticulum.native-public100k-bundle-finish.v1',**self._fast_bundle_wait())
    def facts(self,unit):
        value=self._action(unit,'pilot-facts',schema='meshticulum.native-pilot-facts.v1')
        require(value['unit']==unit and value['host_boot_id']==self.config['units'][unit]['host_boot_id'],'native_host_boot_changed')
        return value
    def cache_status(self,unit,campaign):
        return self._action(unit,'pilot-cache-status',None,'--campaign-id',identifier(campaign),
                            schema='meshticulum.public-pilot-cache-status.v1')
    def reap(self,job):job.close()
