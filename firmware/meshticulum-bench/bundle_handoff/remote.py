"""Root-owned supervisor for one public SDK part under one original whole-file scope.

The controller prefixes common.py before this source. No code runs on import locally.
"""
import base64,hashlib,json,os,select,signal,stat,subprocess,sys,time
from pathlib import Path
print('MTQ_READY',flush=True)
request=json.loads(sys.stdin.readline(3000000));os.umask(0o077)
assert os.geteuid()==0
prepared=request['prepared'];unit=request['unit'];root=Path(prepared['staged_root'])
assert root==Path('/var/lib/meshticulum-public-sdk')/prepared['id'] and len(prepared['id'])==32
sha=lambda raw:hashlib.sha256(raw).hexdigest()
canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def clock():return time.time_ns()//1000,time.monotonic_ns()//1000
stage_guard=None
def facts():
    if stage_guard is not None:stage_guard()
    expected=prepared['expected_facts'][unit];names=sorted(expected['services'])
    properties=('Id','ActiveState','MainPID','InvocationID','ExecMainStartTimestampMonotonic')
    command=['/usr/bin/systemctl','--no-pager','--all','show']
    for name in properties:command+=['-p',name]
    command.extend(names)
    reply=subprocess.run(command,capture_output=True,text=True,timeout=5)
    assert reply.returncode==0,'service_properties_command_failed'
    assert len(reply.stdout)<65536,'service_properties_output_too_large'
    rows={};row={}
    for line in [*reply.stdout.splitlines(),'']:
        if line:k,_,v=line.partition('=');row[k]=v
        elif row:
            assert set(row)==set(properties),'service_properties_incomplete'
            rows[row['Id']]=row;row={}
    value=dict(unit=unit,host_boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        services={n:r['ActiveState'] for n,r in rows.items()},service_lifetimes={n:{k:r[k] for k in properties[2:]} for n,r in rows.items()})
    assert value==expected,'host_or_production_services_changed';return value
def write(path,raw):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
def safe_root(path):
    info=path.lstat();assert stat.S_ISDIR(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o700
    assert path.resolve()==path
def supervised_install(argv,seconds):
    """Bound the complete installer process group, including pip/venv children."""
    child=None;previous={}
    def interrupted(signum,frame):raise InterruptedError('stage_supervisor_signalled')
    for signum in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
        previous[signum]=signal.signal(signum,interrupted)
    try:
        child=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               text=True,start_new_session=True)
        stdout,stderr=child.communicate(timeout=seconds)
        return subprocess.CompletedProcess(argv,child.returncode,stdout,stderr)
    except BaseException:
        if child is not None:
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.communicate(timeout=1)
        raise
    finally:
        if child is not None:
            # Even a returned parent must not leave an installer descendant.
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            for stream in (child.stdout,child.stderr):stream.close()
        for signum,handler in previous.items():signal.signal(signum,handler)
def stage_file(path,data):
    if os.path.lexists(path):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            info=os.fstat(fd)
            assert stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600
            assert info.st_size==len(data) and os.pread(fd,len(data)+1,0)==data,'different_partial_stage_file'
            assert os.fstat(fd)==path.lstat(),'partial_stage_file_changed'
        finally:os.close(fd)
    else:write(path,data)
def current_status(by_id):
    import secrets,struct
    from meshticulum.bench.serial_port import ExclusiveSerial
    from meshticulum.bench.serial_worker import Gfsk250SerialWorker
    from meshticulum.bench.serial_protocol import Frame,Message,Gfsk250Status
    expected=prepared['expected_mcu'][unit]
    with ExclusiveSerial(by_id,usb_serial=expected['usb_serial']) as channel:
        worker=Gfsk250SerialWorker(channel);identity=worker.discover(expected_board_id=1,expected_version=expected['firmware'])
        rid=secrets.randbits(63) or 1;end=channel.monotonic()+2
        worker._send(Frame(Message.STATUS,identity.boot,rid),end);reply=worker._wait(rid,end)
        assert reply.boot==identity.boot and reply.request==rid
        if reply.type is Message.REFUSED:
            assert reply.body==b'\x01';rid+=1
            worker._send(Frame(Message.GFSK250_STATUS,identity.boot,rid),end);reply=worker._wait(rid,end)
            status=Gfsk250Status.from_reply(reply,boot=identity.boot,request=rid,board_id=1)
            counts=(status.boot_attempts,status.boot_reserved_estimated_airtime_us,status.dropped_receive)
            flags=(status.armed,status.transmitting,status.fenced)
        else:
            assert reply.type is Message.STATUS_REPLY and len(reply.body)==49
            counts=struct.unpack('!IQI',reply.body[30:46]);flags=tuple(bool(x) for x in reply.body[46:])
        assert not any(flags),'radio_not_disarmed_or_fenced'
        return dict(mcu_boot=identity.boot.hex(),firmware=identity.version,usb_serial=expected['usb_serial'],
            boot_attempts=counts[0],boot_reserved_estimated_airtime_us=counts[1],dropped_receive=counts[2])

started=clock()
if request['action']=='stage':
    stage=request['stage_window']
    stage_end=stage_remote_deadline(stage,prepared,*started)
    def stage_guard(reserve_us=0):
        check_stage_window(stage,*clock(),started[1],stage_end,reserve_us=reserve_us)
    stage_guard();before=facts();stage_guard()
    parent=root.parent;parent.mkdir(mode=0o700,exist_ok=True);safe_root(parent)
    root.mkdir(mode=0o700,exist_ok=True);safe_root(root)
    for name,pin in prepared['files'].items():
        stage_guard()
        relative=Path(name);assert not relative.is_absolute() and '..' not in relative.parts
        data=base64.b64decode(request['files'][name],validate=True);assert sha(data)==pin
        stage_file(root/relative,data)
    # The local external timeout also kills this process group. Reserve final
    # facts/cleanup time inside the immutable stage end before the installer.
    stage_guard(reserve_us=8_000_000)
    now_epoch,now_mono=clock()
    remaining=min(stage['expires_epoch_us']-now_epoch,stage_end-now_mono)/1_000_000
    installed=supervised_install([sys.executable,'-I','-B',str(root/'install_environment.py'),str(root),unit],
                                 min(75,remaining-8))
    stage_guard()
    assert installed.returncode==0,'clean_environment_install_failed:'+installed.stderr[-500:]
    environment=json.loads(installed.stdout)
    result=dict(schema='meshticulum.public-sdk-staging.v1',complete=True,unit=unit,root=str(root),
        core_commit=prepared['core_commit'],files=prepared['files'],facts_before=before,facts_after=facts(),
        owner_window_sha256=stage['owner_window_sha256'],stage_window=stage,
        radio_opened=False,grants_created=0,window_issued=False,environment=environment,finished_epoch_us=clock()[0])
    stage_guard()
    if os.path.lexists(root/'STAGED.json'):
        previous=json.loads((root/'STAGED.json').read_bytes())
        assert all(previous[k]==result[k] for k in ('schema','complete','unit','root','core_commit','files','facts_before','facts_after','environment','radio_opened','grants_created','window_issued','owner_window_sha256'))
        stage_file(root/'STAGED.json',canonical(previous));result=previous
    else:write(root/'STAGED.json',canonical(result))
    stage_guard()
    print('MTQ_RESULT '+json.dumps(result,sort_keys=True),flush=True)
    raise SystemExit(0)

assert request['action']=='run';before=facts();safe_root(root)
scope=request['scope'];part_index=request['part']
assert scope['prepared_id']==prepared['id'] and scope['useful_bytes']==prepared['useful_bytes']
assert scope['prepared_sha256']==sha(canonical(prepared)) and scope['core_commit']==prepared['core_commit']
assert scope['policy']==prepared['policy'] and scope['manifest_sha256']==prepared['manifest_sha256']
validate_mode_binding(prepared,scope)
assert scope['campaign_id']==prepared['campaign_id'] and scope['fresh_scope'] is True
assert type(part_index) is int and 0<=part_index<prepared['part_count']
issued,end=scope['issued_epoch_us'],scope['expires_epoch_us'];assert end-issued==prepared['policy']['scope_seconds']*1000000
epoch,mono=clock();assert issued<=epoch<end-(prepared['part_count']-part_index)*ACK_GRACE_US-5000000,'public_scope_no_longer_launchable'
# Reuse the initial supervisor mapping across every part; wall rollback cannot
# extend either the public FileWindow or this management lifetime.
binding_path=root/'SUPERVISOR_CLOCK.json'
if part_index==0:
    binding=dict(scope_sha256=sha(canonical(scope)),host_boot_id=before['host_boot_id'],
        original_monotonic_us=mono,original_deadline_monotonic_us=mono+end-epoch)
    write(binding_path,canonical(binding))
else:binding=json.loads(binding_path.read_bytes())
assert binding['scope_sha256']==sha(canonical(scope)) and binding['host_boot_id']==before['host_boot_id']
deadline=binding['original_deadline_monotonic_us']
assert binding['original_monotonic_us']<=mono<deadline-(prepared['part_count']-part_index)*ACK_GRACE_US-5000000,'original_supervisor_scope_expired'
for name,pin in prepared['files'].items():assert sha((root/name).read_bytes())==pin
import runpy
environment=runpy.run_path(str(root/'verify_environment.py'))['verify'](root,unit)
from meshticulum.bench.linux_hat import _existing_openers
radio_before=current_status(request['by_id'][unit])
assert radio_before==request['current_mcu'][unit],'pre_arm_counter_baseline_changed'
validate_counter_progress(
    prepared['expected_mcu'][unit],
    prepared['expected_mcu'][unit],
    radio_before,
    prepared['policy']['unit_limits'][unit],
)
grant=root/'grants'/str(part_index);grant.mkdir(mode=0o700,parents=True)
for name in ('grant.json','secret.bin','active'):
    data=base64.b64decode(request['grant_files'][name],validate=True)
    assert sha(data)==scope['grants'][unit][str(part_index)][name],'grant_file_pin_changed'
    write(grant/name,data)
state=root/'state'
if part_index==0:state.mkdir(mode=0o700)
else:safe_root(state)
plan=json.loads((grant/'grant.json').read_bytes());assert plan['unit']==unit and plan['issued_epoch_us']==issued and plan['expires_epoch_us']==end and plan['object']['chunk_index']==part_index
validate_plan_mode(plan,prepared)
scope_raw=canonical(scope)
if part_index==0:write(root/'SCOPE.json',scope_raw)
else:assert (root/'SCOPE.json').read_bytes()==scope_raw,'original_scope_rebound'
firmware_source=prepared['firmware_sources'][unit]
args=['transfer','--interface','usb','--unit',unit,
    '--grant-directory',str(grant),'--grant-sha256',sha((grant/'grant.json').read_bytes()),'--state',str(state),
    '--pack',str(root/'pack'),'--manifest-sha256',prepared['manifest_sha256'],
    '--trust-root',str(root/'trust.json'),'--trust-root-sha256',prepared['files']['trust.json'],
    '--admission',str(root/'admission.json'),'--admission-sha256',sha((root/'admission.json').read_bytes()),
    '--firmware-source',str(root/firmware_source['path']),
    '--firmware-source-sha256',firmware_source['sha256'],
    '--file-issued-epoch-us',str(issued),'--file-expires-epoch-us',str(end),'--part',str(part_index)]
args+=prepared['receiver_retirement']['cli_flags']
if prepared['profile_host']:args.append('--profile-host')
args+=['--device',request['by_id'][unit],'--usb-serial',prepared['expected_mcu'][unit]['usb_serial']]
write(grant/'PUBLIC_COMMAND.json',canonical(args))
child=None;output=bytearray();buf=bytearray();events=[];metrics=None;cli_result=None;forced=False
def stop(signum,frame):raise InterruptedError('supervisor_signalled')
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGHUP,stop)
failure=None;child_started=clock()
try:
    child=subprocess.Popen([sys.executable,'-I','-B',str(root/'sdk_entry.py'),str(os.getpid()),str(root),unit,str(part_index),*args],
        cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    while child.poll() is None or select.select([child.stdout],[],[],0)[0]:
        epoch,now=clock();assert issued<=epoch<end-5000000 and now<deadline-5000000,'original_public_scope_expired'
        if not select.select([child.stdout],[],[],.02)[0]:continue
        part=os.read(child.stdout.fileno(),65536)
        if not part and child.poll() is not None:break
        output.extend(part);buf.extend(part);assert len(output)<2000000
        while b'\n' in buf:
            line,_,tail=buf.partition(b'\n');buf[:]=tail
            try:value=json.loads(line)
            except ValueError:continue
            if value.get('sdk_qual_event')=='ready':
                events.append(value);print('MTQ_EVENT '+json.dumps(value),flush=True)
            elif value.get('sdk_qual_event')=='metrics':metrics=value['value']
            elif 'attempt_journal' in value:cli_result=value
    assert child.wait(timeout=2)==0 and metrics and metrics['completed'] and cli_result,'public_example_did_not_close_successfully'
except BaseException as error:
    failure=dict(type=type(error).__name__,reason=str(error)[:160])
finally:
    if child is not None:
        if child.poll() is None:
            forced=True;os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=2)
        child.stdout.close()
    # No payload or grant secret is included in this diagnostic log.
    write(grant/'PUBLIC_OUTPUT.log',bytes(output));output.clear();buf.clear()
result=dict(schema='meshticulum.public-sdk-bundle-part-endpoint.v2',unit=unit,part=part_index,complete=False,
    child_started=dict(epoch_us=child_started[0],monotonic_us=child_started[1]),finished_epoch_us=clock()[0],
    original_end_epoch_us=end,original_deadline_monotonic_us=deadline,child_terminal=child is not None and child.poll() is not None,
    returncode=None if child is None else child.returncode,forced_cleanup=forced,metrics=metrics,public_result=cli_result,
    failure=failure,radio_before=radio_before,runtime_environment=environment,global_quiescence_proven=False,old_paid_history_unchanged=True)
try:
    result['facts_after']=facts();result['facts_before']=before
    after=current_status(request['by_id'][unit]);result['radio_after']=after
    validate_counter_progress(
        prepared['expected_mcu'][unit],
        radio_before,
        after,
        prepared['policy']['unit_limits'][unit],
    )
    result['receive_drop_diagnostics']=dict(before=radio_before['dropped_receive'],
        after=after['dropped_receive'],delta=after['dropped_receive']-radio_before['dropped_receive'],diagnostic_only=True)
    if cli_result is None:
        assert failure is not None, 'missing_public_result_without_failure'
    elif plan['sender_unit']==unit:
        assert cli_result['sender_peer_receipt'] is True
        assert metrics['endpoint_complete']['monotonic_us']>=metrics['authenticated_receipt']['monotonic_us']
        if prepared['comparison_mode']=='queued':
            physical=metrics['flow_snapshot']['physical_queue']
            assert physical['pending']==physical['worker_pending']==0,'physical_results_not_drained'
    else:
        assert cli_result['receiver_chunk_verified'] is True
        assert cli_result['whole_file_verified'] is (part_index==prepared['part_count']-1)
        if part_index==prepared['part_count']-1:
            dest=state/'receiver/bundle.bin';raw=dest.read_bytes()
            assert len(raw)==prepared['useful_bytes'] and sha(raw)==prepared['useful_sha256']
            result['verified_file']=dict(bytes=len(raw),sha256=sha(raw),path=str(dest))
    result['complete']=failure is None and not forced and result['child_terminal'] and result['returncode']==0
except BaseException as error:result['postflight_error']=dict(type=type(error).__name__,reason=str(error)[:160])
write(grant/'ENDPOINT_RESULT.json',canonical(result));print('MTQ_RESULT '+json.dumps(result,sort_keys=True),flush=True)
