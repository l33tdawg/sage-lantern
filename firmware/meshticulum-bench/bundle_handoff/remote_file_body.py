# Appended to the pinned stage supervisor prefix, never imported alone.
assert request['action']=='run'
before=facts();safe_root(root);scope=request['scope'];scope_pin=sha(canonical(scope))
assert scope['prepared_id']==prepared['id'] and scope['prepared_sha256']==sha(canonical(prepared))
assert scope['core_commit']==prepared['core_commit'] and scope['policy']==prepared['policy']
assert scope['manifest_sha256']==prepared['manifest_sha256'] and scope['useful_bytes']==prepared['useful_bytes']
assert scope['campaign_id']==prepared['campaign_id'] and scope['fresh_scope'] is True
assert scope['order']==prepared['order'] and sorted(scope['order'])==list(range(prepared['part_count']))
validate_mode_binding(prepared,scope)
issued,end=scope['issued_epoch_us'],scope['expires_epoch_us'];epoch,mono=clock()
assert end-issued==prepared['policy']['scope_seconds']*1000000
assert issued<=epoch<end-prepared['part_count']*ACK_GRACE_US-5000000,'scope_not_launchable'
deadline=mono+end-epoch
write(root/'SUPERVISOR_CLOCK.json',canonical(dict(scope_sha256=scope_pin,host_boot_id=before['host_boot_id'],
    original_monotonic_us=mono,original_deadline_monotonic_us=deadline)))
for name,pin in prepared['files'].items():assert sha((root/name).read_bytes())==pin
import runpy
environment=runpy.run_path(str(root/'verify_environment.py'))['verify'](root,unit)
radio_before=current_status(request['by_id'][unit])
assert radio_before==request['current_mcu'][unit],'pre_arm_baseline_changed'
validate_counter_progress(prepared['expected_mcu'][unit],radio_before,radio_before,prepared['policy']['unit_limits'][unit])
state=root/'state';state.mkdir(mode=0o700);write(root/'SCOPE.json',canonical(scope))
all_args=[]
for part_index in scope['order']:
    grant=root/'grants'/str(part_index);grant.mkdir(mode=0o700,parents=True)
    for name in ('grant.json','secret.bin','active'):
        raw=base64.b64decode(request['grant_files'][str(part_index)][name],validate=True)
        assert sha(raw)==scope['grants'][unit][str(part_index)][name],'grant_pin_changed'
        write(grant/name,raw)
    plan=json.loads((grant/'grant.json').read_bytes())
    assert plan['unit']==unit and plan['issued_epoch_us']==issued and plan['expires_epoch_us']==end
    assert plan['object']['chunk_index']==part_index
    validate_plan_mode(plan,prepared)
    firmware=prepared['firmware_sources'][unit]
    args=['transfer','--interface','usb','--unit',unit,'--grant-directory',str(grant),
        '--grant-sha256',scope['grants'][unit][str(part_index)]['grant.json'],'--state',str(state),
        '--pack',str(root/'pack'),'--manifest-sha256',prepared['manifest_sha256'],
        '--trust-root',str(root/'trust.json'),'--trust-root-sha256',prepared['files']['trust.json'],
        '--admission',str(root/'admission.json'),'--admission-sha256',prepared['files']['admission.json'],
        '--firmware-source',str(root/firmware['path']),'--firmware-source-sha256',firmware['sha256'],
        '--file-issued-epoch-us',str(issued),'--file-expires-epoch-us',str(end),'--part',str(part_index),
        '--device',request['by_id'][unit],'--usb-serial',prepared['expected_mcu'][unit]['usb_serial']]
    args+=prepared['receiver_retirement']['cli_flags']
    if prepared['profile_host']:args.append('--profile-host')
    write(grant/'PUBLIC_COMMAND.json',canonical(args));all_args.append(args)
child=None;output=bytearray();buf=bytearray();controls=bytearray();pending=bytearray()
cli_result=None;failure=None;forced=False;events=[];child_started=clock()
def stop(signum,frame):raise InterruptedError('file_supervisor_signalled')
for signum in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(signum,stop)
try:
    child=subprocess.Popen([sys.executable,'-I','-B','-u',str(root/'sdk_batch_entry.py'),str(os.getpid()),str(root),unit],
        cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,bufsize=0)
    os.set_blocking(child.stdin.fileno(),False);os.set_blocking(child.stdout.fileno(),False)
    os.set_blocking(sys.stdin.fileno(),False)
    pending.extend(canonical(dict(parts=all_args,order=scope['order'],scope_sha256=scope_pin,
        deadline_monotonic_us=deadline,issued_epoch_us=issued,expires_epoch_us=end))+b'\n')
    while child.poll() is None or select.select([child.stdout],[],[],0)[0]:
        epoch,now=clock();assert issued<=epoch<end-5000000 and mono<=now<deadline-5000000,'original_scope_expired'
        reading,writing,_=select.select([child.stdout,sys.stdin],[child.stdin] if pending else [],[],.01)
        if sys.stdin in reading:
            raw=os.read(sys.stdin.fileno(),8192);assert raw,'management_stdin_closed'
            controls.extend(raw);assert len(controls)<16384,'control_input_bound'
            while b'\n' in controls:
                line,_,tail=controls.partition(b'\n');controls[:]=tail
                value=json.loads(line)
                assert set(value)=={'action','part','scope_sha256'} and value['scope_sha256']==scope_pin
                assert value['action'] in ('go','advance') and type(value['part']) is int and value['part'] in scope['order']
                pending.extend(canonical(value)+b'\n');assert len(pending)<16384,'control_forward_bound'
        if child.stdin in writing and pending:
            count=os.write(child.stdin.fileno(),pending);del pending[:count]
        if child.stdout in reading:
            raw=os.read(child.stdout.fileno(),65536)
            if not raw and child.poll() is not None:break
            output.extend(raw);buf.extend(raw);assert len(output)<2000000,'child_output_bound'
            while b'\n' in buf:
                line,_,tail=buf.partition(b'\n');buf[:]=tail
                if line.startswith(b'MTQ_EVENT '):
                    value=json.loads(line[10:]);assert value['scope_sha256']==scope_pin and value['unit']==unit
                    assert value['part'] in scope['order'] and value['event'] in ('ready','part_done')
                    events.append(value);print('MTQ_EVENT '+json.dumps(value),flush=True)
                elif line.startswith(b'MTQ_RESULT '):
                    assert cli_result is None,'duplicate_child_result';cli_result=json.loads(line[11:])
    assert child.wait(timeout=2)==0 and cli_result and cli_result['complete'],'batch_child_failed'
except BaseException as error:failure=dict(type=type(error).__name__,reason=str(error)[:160])
finally:
    if child is not None:
        if child.poll() is None:
            forced=True;os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=2)
        # Child exit alone cannot leave a descendant holding a device.
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        child.stdin.close();child.stdout.close()
    write(root/'PUBLIC_BATCH_OUTPUT.log',bytes(output));output.clear();pending.clear();controls.clear()
result=dict(schema='meshticulum.bundle-handoff-file-endpoint.v1',unit=unit,complete=False,
    scope_sha256=scope_pin,child_started=dict(epoch_us=child_started[0],monotonic_us=child_started[1]),
    child_terminal=child is not None and child.poll() is not None,returncode=None if child is None else child.returncode,
    forced_cleanup=forced,failure=failure,batch_result=cli_result,radio_before=radio_before,
    original_end_epoch_us=end,original_deadline_monotonic_us=deadline,events=events,
    runtime_environment=environment,facts_before=before,global_quiescence_proven=False)
try:
    result['facts_after']=facts();after=current_status(request['by_id'][unit]);result['radio_after']=after
    validate_counter_progress(prepared['expected_mcu'][unit],radio_before,after,prepared['policy']['unit_limits'][unit])
    assert after['boot_attempts']-radio_before['boot_attempts']<=prepared['run_budget'][unit]['attempts']
    assert after['boot_reserved_estimated_airtime_us']-radio_before['boot_reserved_estimated_airtime_us']<=prepared['run_budget'][unit]['estimated_airtime_us']
    done=[e for e in events if e['event']=='part_done']
    assert [e['part'] for e in done]==scope['order'],'part_sequence_incomplete'
    for ordinal,event in enumerate(done):
        public=event['public_result'];metrics=event['metrics']
        if unit==prepared['sender']:
            assert public['sender_peer_receipt'] is True
            assert metrics['endpoint_complete']['monotonic_us']>=metrics['authenticated_receipt']['monotonic_us']
            if prepared['comparison_mode']=='queued':
                physical=metrics['flow_snapshot']['physical_queue']
                assert physical['pending']==physical['worker_pending']==0,'physical_not_drained'
        else:
            assert public['receiver_chunk_verified'] is True
            assert public['whole_file_verified'] is (ordinal==len(done)-1)
    if unit==prepared['receiver']:
        raw=(state/'receiver/bundle.bin').read_bytes()
        assert len(raw)==prepared['useful_bytes'] and sha(raw)==prepared['useful_sha256']
        result['verified_file']=dict(bytes=len(raw),sha256=sha(raw))
    result['complete']=failure is None and not forced and result['child_terminal'] and result['returncode']==0
except BaseException as error:result['postflight_error']=dict(type=type(error).__name__,reason=str(error)[:160])
result['finished_epoch_us']=clock()[0]
write(root/'ENDPOINT_RESULT.json',canonical(result));print('MTQ_RESULT '+json.dumps(result,sort_keys=True),flush=True)
