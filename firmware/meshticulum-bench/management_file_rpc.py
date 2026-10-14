# SPDX-License-Identifier: Apache-2.0
"""Explicit alternate management entry; the existing argv input remains unchanged."""
import hashlib
import re
import time

RPC_FILE_ENTRY=r'''try:
 import hashlib,importlib,json,os,re,stat,sys,types
 from pathlib import Path
 root,pin,adapter_pin,action,unit,parent,operation,nonce,path,csha,rsha,end,uid,identity=sys.argv[1:]
 def need(v,r):
  if not v:raise RuntimeError(r)
 def read(path,limit):
  for p in (path.parent,*path.parent.parents):
   s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022 and p.resolve()==p,'runtime_ancestry')
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
  try:
   s=os.fstat(fd);need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=limit,'runtime_record')
   raw=os.pread(fd,limit+1,0);need(len(raw)==s.st_size and os.fstat(fd)==path.lstat(),'runtime_record_changed');return raw
  finally:os.close(fd)
 need(os.geteuid()==0,'actual_root_required');root=Path(root)
 mraw=read(root/'manifest.json',65536);need(hashlib.sha256(mraw).hexdigest()==pin,'runtime_manifest_pin')
 manifest=json.loads(mraw);need(manifest['private_services_enabled'] is False and manifest['public_parent_claims_required'] is True,'public_runtime_required')
 for name,wanted in manifest['files'].items():
  part=Path(name);need(not part.is_absolute() and '..' not in part.parts,'runtime_member')
  limit=2000000 if manifest.get('schema')=='meshticulum.bench-runtime-bundle.v2' and name=='execution.zip' else 500000
  need(hashlib.sha256(read(root/part,limit)).hexdigest()==wanted,'runtime_member_pin')
 sources=manifest['source_files'] if manifest.get('schema')=='meshticulum.bench-runtime-bundle.v2' else manifest['files']
 need(sources.get('lantern_meshticulum/bench/pilot_resume_rpc.py')==adapter_pin,'native_adapter_pin')
 need('lantern_meshticulum/bench/management_input.py' in sources,'management_input_runtime_missing')
 need(len(identity)<=1024,'management_input_identity_bound');identities=json.loads(identity)
 need(type(identities) is dict and set(identities)=={'file','directory'} and all(type(v) is list for v in identities.values())
  and len(identities['file'])==9 and len(identities['directory'])==5
  and all(type(x) is int for v in identities.values() for x in v),'management_input_identity_shape')
 if manifest.get('schema')=='meshticulum.bench-runtime-bundle.v2':
  hraw=read(root/'execution_runtime.py',500000);need(hashlib.sha256(hraw).hexdigest()==manifest['execution_layout']['loader_sha256'],'image_loader_pin')
  need('_lantern_execution_runtime' not in sys.modules,'image_loader_duplicate')
  helper=types.ModuleType('_lantern_execution_runtime');helper.__file__=str(root/'execution_runtime.py');sys.modules[helper.__name__]=helper
  exec(compile(hraw,helper.__file__,'exec'),helper.__dict__);helper.install(root,mraw,expected_manifest_sha256=pin)
 else:sys.path.insert(0,str(root))
 inputs=importlib.import_module('lantern_meshticulum.bench.management_input')
 if action=='cleanup-management-input':
  absent=inputs.cleanup(path,nonce=nonce,compressed_sha256=csha,expected_stage_uid=int(uid),
   expected_identity=identities['file'],expected_directory_identity=identities['directory'])
  result=dict(schema='meshticulum.management-input-cleaned.v1',unit=unit,nonce=nonce,cleaned=True,already_absent=absent,
   native_dispatch_called=False,radio_opened=False,native_state_writes=False)
 else:
  module=importlib.import_module('lantern_meshticulum.bench.pilot_resume_rpc')
  need(getattr(module,'CONTRACT_SCHEMA',None)=='meshticulum.private-resume-native-rpc.v1','native_integration_contract_missing')
  value=inputs.consume(path,unit=unit,action=action,parent_id=parent,operation_sha256=operation,nonce=nonce,
   compressed_sha256=csha,raw_sha256=rsha,original_end_epoch_us=int(end),expected_stage_uid=int(uid),
   expected_identity=identities['file'],expected_directory_identity=identities['directory'])
  result=module.dispatch(action,unit,value,runtime_root=root,manifest_sha256=pin)
  need(inputs.time.time_ns()//1000<int(end),'management_input_window_expired')
  need(type(result) is dict and result.get('schema')=='meshticulum.private-resume-native-rpc.v1'
   and result.get('unit')==unit and result.get('action')==action,'RPC_result_scope')
 print(json.dumps(result,sort_keys=True),flush=True)
except BaseException as error:
 # The transport reports no input bytes or exception message excerpts.
 kind=type(error).__name__
 diagnostic=dict(schema='meshticulum.management-file-RPC-error.v1',
  error_type=kind if re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',kind) else 'Exception',
  reason='management_file_RPC_refused')
 if type(error) is KeyError:
  field=error.args[0] if len(error.args)==1 and type(error.args[0]) is str else None
  if field is not None and not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}',field):field=None
  frames=[];tb=error.__traceback__
  for _ in range(32):
   if tb is None:break
   code=tb.tb_frame.f_code;name=os.path.basename(code.co_filename);function=code.co_name
   if (name=='<string>' or re.fullmatch('[A-Za-z0-9_.-]{1,64}',name)) and \
      (function=='<module>' or re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}',function)) and 0<tb.tb_lineno<=1000000:
    frames.append(dict(file=name,line=tb.tb_lineno,function=function));frames=frames[-3:]
   tb=tb.tb_next
  diagnostic['error_detail']=dict(key_error_field=field,source_frames=frames)
 print(json.dumps(diagnostic),flush=True)
 raise SystemExit(1) from None
'''

RPC_FILE_SHA256=hashlib.sha256(RPC_FILE_ENTRY.encode()).hexdigest()


def command(runtime_root,manifest_sha256,adapter_sha256,staged,python,remaining_seconds,*,cleanup=False):
    """Small argv contains only file/scope/hash identities, never the metadata body."""
    from lantern_meshticulum.bench.management_input import canonical,parameters,need,SCHEMA
    need(type(remaining_seconds) is int and 0<remaining_seconds<=120,'management_input_callback_bound')
    need(type(cleanup) is bool and (not cleanup or remaining_seconds<=5),'management_input_cleanup_bound')
    need(type(runtime_root) is str and re.fullmatch('/opt/meshticulum-bench/runtime-[0-9a-f]{16}',runtime_root)
         and type(python) is str and python.startswith('/')
         and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in (manifest_sha256,adapter_sha256)),
         'management_input_runtime_arguments')
    need(staged.get('schema')==SCHEMA,'management_input_receipt_schema')
    parameters(staged['unit'],staged['action'],staged['parent_id'],staged['operation_sha256'],staged['nonce'],staged['original_end_epoch_us'])
    # Staging and sudo setup cannot buy a fresh callback budget.
    if not cleanup:
        remaining_seconds=min(remaining_seconds,(staged['original_end_epoch_us']-time.time_ns()//1000)//1000000)
        need(remaining_seconds>0,'management_input_window_expired')
    identity=canonical(dict(file=staged['file_identity'],directory=staged['directory_identity'])).decode()
    return ['/usr/bin/timeout','--kill-after=2s',str(remaining_seconds)+'s',python,'-I','-B','-c',RPC_FILE_ENTRY,
        runtime_root,manifest_sha256,adapter_sha256,'cleanup-management-input' if cleanup else staged['action'],staged['unit'],staged['parent_id'],
        staged['operation_sha256'],staged['nonce'],staged['path'],staged['compressed_sha256'],staged['raw_sha256'],
        str(staged['original_end_epoch_us']),str(staged['owner_uid']),identity]
