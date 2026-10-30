"""Fresh individual-run helpers. No historical scope is reused."""
import os,stat,json,hashlib,struct
from pathlib import Path
from campaign import need,digest,sha,canonical
ACK_GRACE_US=20_000_000
UNITS=("unit-a","unit-b")
DATA_BURST_POLICY=dict(schema="meshticulum.data-burst-policy.v1",handover="irrevocably-charged-at-queue-acceptance",max_depth=4,max_hold_us=2_000_000,inter_packet_rx_us=5_000,reverse_listen_us=100_000)
_DEFAULT_FLOW_WINDOW=object()

def data_burst_policy(depth=4,*,flow_window_frames=_DEFAULT_FLOW_WINDOW,
                      selective_repair_fill=_DEFAULT_FLOW_WINDOW,
                      selective_terminal_retry=_DEFAULT_FLOW_WINDOW):
    need(type(depth) is int and depth in (4,8),'invalid_train_depth')
    policy=dict(DATA_BURST_POLICY)
    if depth==8:
        policy.update(schema='meshticulum.data-burst-policy.v2',max_depth=8,train_frames=8)
    if flow_window_frames is not _DEFAULT_FLOW_WINDOW:
        need(type(flow_window_frames) is int and flow_window_frames==32,
             'invalid_flow_window_frames')
        need(depth==8,'logical_window_requires_train_eight')
        policy.update(schema='meshticulum.data-burst-policy.v3',flow_window_frames=32)
    if selective_repair_fill is not _DEFAULT_FLOW_WINDOW:
        need(type(selective_repair_fill) is bool and selective_repair_fill is True,
             'invalid_selective_repair_fill')
        need(depth==8 and type(flow_window_frames) is int and flow_window_frames==32,
             'repair_fill_requires_logical_window32')
        policy.update(schema='meshticulum.data-burst-policy.v4',selective_repair_fill=True)
    if selective_terminal_retry is not _DEFAULT_FLOW_WINDOW:
        need(type(selective_terminal_retry) is bool and selective_terminal_retry is True,
             'invalid_selective_terminal_retry')
        need(selective_repair_fill is True,'terminal_retry_requires_repair_fill')
        policy.update(schema='meshticulum.data-burst-policy.v5',selective_terminal_retry=True)
    return policy

def selected_data_burst(config):
    mode=config['comparison_mode'];need(mode in ('baseline','queued'),'invalid_mode')
    depth=config.get('train_depth',4)
    policy=data_burst_policy(depth)
    need(mode=='queued' or depth==4,'baseline_train_depth_changed')
    if 'flow_window_frames' in config:
        need(mode=='queued' and 'train_depth' in config and depth==8,
             'logical_window_requires_explicit_queued_eight')
        policy=data_burst_policy(depth,flow_window_frames=config['flow_window_frames'])
    if 'selective_repair_fill' in config:
        need(mode=='queued' and 'train_depth' in config and depth==8
             and 'flow_window_frames' in config,
             'repair_fill_requires_explicit_queued_window32')
        policy=data_burst_policy(depth,flow_window_frames=config['flow_window_frames'],
                                selective_repair_fill=config['selective_repair_fill'])
    if 'selective_terminal_retry' in config:
        need(mode=='queued' and 'train_depth' in config and depth==8
             and 'flow_window_frames' in config and 'selective_repair_fill' in config,
             'terminal_retry_requires_explicit_queued_repair_fill')
        policy=data_burst_policy(depth,flow_window_frames=config['flow_window_frames'],
                                selective_repair_fill=config['selective_repair_fill'],
                                selective_terminal_retry=config['selective_terminal_retry'])
    return policy if mode=='queued' else None

def operation_limits(prepared):
    policy=prepared['policy']
    return {role:dict(attempts=policy.get(role+'_operation_attempt_allowance',policy['operation_attempt_allowance']),
        estimated_airtime_us=policy.get(role+'_operation_estimated_airtime_allowance_us',policy['operation_estimated_airtime_allowance_us']))
        for role in ('sender','receiver')}

def validate_mode_binding(prepared, scope=None):
    mode=prepared['comparison_mode'];need(mode in ('baseline','queued'),'invalid_mode')
    value=prepared['data_burst']
    policies=(data_burst_policy(4),data_burst_policy(8),
              data_burst_policy(8,flow_window_frames=32),
              data_burst_policy(8,flow_window_frames=32,selective_repair_fill=True),
              data_burst_policy(8,flow_window_frames=32,selective_repair_fill=True,
                                selective_terminal_retry=True))
    need((value is None if mode=='baseline' else type(value) is dict and
          any(canonical(value)==canonical(policy) for policy in policies)),
         'mode_policy_changed')
    if scope is not None:
        need(scope['comparison_mode']==mode and canonical(scope['data_burst'])==canonical(value),'scope_mode_changed')
    return mode

def validate_plan_mode(plan, prepared):
    mode=validate_mode_binding(prepared)
    need(('data_burst' not in plan if mode=='baseline' else type(plan.get('data_burst')) is dict
          and canonical(plan['data_burst'])==canonical(prepared['data_burst'])),'grant_mode_changed')
    need(plan['operation_limits']==operation_limits(prepared),'operation_limit_changed')

def stage_remote_deadline(stage, prepared, epoch, mono):
    need(stage['prepared_sha256']==sha(canonical(prepared)) and stage['issued_epoch_us']<=epoch<stage['expires_epoch_us'],'stage_binding_or_expiry')
    need(0<stage['expires_epoch_us']-stage['issued_epoch_us']<=180_000_000,'stage_bound')
    return mono+stage['expires_epoch_us']-epoch

def check_stage_window(stage,epoch,mono,remote_start_mono,remote_end_mono,*,reserve_us=0):
    need(stage['issued_epoch_us']<=epoch<stage['expires_epoch_us']-reserve_us and remote_start_mono<=mono<remote_end_mono-reserve_us,'stage_expired')

def metric_distribution(events, key):
    """Keep absent queued worker durations missing, never coerce them to zero."""
    import statistics
    values = []
    missing = 0
    for event in events:
        value = event.get(key)
        if value is None:
            missing += 1
        else:
            need(type(value) is int and value >= 0, 'invalid_timing_value')
            values.append(value)
    return dict(measured_count=len(values), missing_count=missing,
                sum=sum(values) if values else None,
                median=statistics.median(values) if values else None)


def read_pinned(path, pin, limit=4_000_000):
    digest(pin)
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        a = os.fstat(fd)
        need(stat.S_ISREG(a.st_mode) and a.st_nlink == 1 and 0 < a.st_size <= limit, 'invalid_input_file')
        raw = os.read(fd, limit + 1)
        b = os.fstat(fd)
        stamp = lambda x: (x.st_dev, x.st_ino, x.st_mode, x.st_uid, x.st_gid,
                           x.st_nlink, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        need(stamp(a) == stamp(b) == stamp(path.lstat()) and len(raw) == a.st_size, 'input_changed')
        need(sha(raw) == pin, 'input_pin_mismatch')
        return raw
    finally:
        os.close(fd)


def write_new(path, value, *, raw=False):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(value if raw else canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def validate_counter_progress(original, previous, after, policy):
    for key in ('mcu_boot', 'firmware', 'usb_serial'):
        need(after[key] == previous[key] == original[key], 'mcu_identity_changed')
    # This boot-lifetime diagnostic counts discarded/partial RX, not accepted
    # application bytes. Preserve every increment; receipt/integrity checks
    # still decide whether a part completed and may advance the coordinator.
    need(all(type(row['dropped_receive']) is int and row['dropped_receive'] >= 0
             for row in (original, previous, after))
         and original['dropped_receive'] <= previous['dropped_receive'] <= after['dropped_receive'],
         'mcu_receive_drop_counter_regressed_or_invalid')
    for key, cap in (('boot_attempts', 'max_boot_attempts'),
                     ('boot_reserved_estimated_airtime_us', 'max_boot_estimated_airtime_us')):
        need(type(after[key]) is int and original[key] <= previous[key] <= after[key] <= policy[cap],
             'mcu_debt_regressed_or_cap_exceeded')
    return after


def validate_window(scope, now_epoch, now_mono, boot, *, reserve_us=0):
    need(type(now_epoch) is int and type(now_mono) is int, 'integer_clock_required')
    need(boot == scope['controller_boot'], 'controller_boot_changed')
    need(scope['issued_epoch_us'] <= now_epoch < scope['expires_epoch_us'] - reserve_us
         and scope['original_monotonic_us'] <= now_mono < scope['original_deadline_monotonic_us'] - reserve_us,
         'original_scope_expired_or_insufficient')


def part_timing(row):
    metrics = row['metrics']
    end = metrics['authenticated_receipt']['monotonic_us']
    start = row['child_started']['monotonic_us']
    need(end > start, 'invalid_sender_receipt_timing')
    result = {'sender_startup_to_authenticated_receipt_us': end - start}
    if 'session_retired' in metrics:
        retirement=metrics['session_retired']['monotonic_us']
        need(retirement>=end, 'session_retired_before_authenticated_receipt')
        result['authenticated_receipt_to_session_retired_us']=retirement-end
        result['sender_startup_to_session_retired_us']=retirement-start
    if 'cli_complete' in metrics:
        cli=metrics['cli_complete']['monotonic_us']
        need(cli>=metrics.get('session_retired',{'monotonic_us':end})['monotonic_us'], 'cli_completed_before_retirement')
        result['sender_startup_to_cli_complete_us']=cli-start
    if 'first_data' in metrics:
        data = metrics['first_data']
        need(start <= data['worker_call_started_monotonic_us'] <= data['worker_call_finished_monotonic_us'] <= end,
             'invalid_first_data_timing')
        result['first_DATA_worker_call_to_authenticated_receipt_us'] = end - data['worker_call_started_monotonic_us']
        result['first_DATA_worker_call_started_monotonic_us'] = data['worker_call_started_monotonic_us']
    if 'first_queued_data' in metrics:
        data = metrics['first_queued_data']
        begin, accepted = data['queue_submit_monotonic_us'], data['queue_accept_monotonic_us']
        need(type(begin) is int and type(accepted) is int and start <= begin <= accepted <= end,
             'invalid_first_queued_data_timing')
        result['first_queued_DATA_submit_to_authenticated_receipt_us'] = end - begin
        result['first_queued_DATA_submit_monotonic_us'] = begin
    if 'endpoint_complete' in metrics:
        closed = metrics['endpoint_complete']['monotonic_us']
        need(closed >= end, 'endpoint_completed_before_authenticated_receipt')
        result['authenticated_receipt_to_drained_endpoint_complete_us'] = closed - end
    return result


def paid_totals(parts):
    totals={unit:dict(known_attempts=0,known_reserved_estimated_airtime_us=0,
        unreported_parts=[],fenced=False,recovered_unknown=0) for unit in UNITS}
    for index,part in enumerate(parts):
        for unit in UNITS:
            row=part.get('endpoints',{}).get(unit,{})
            journal=(row.get('public_result') or {}).get('attempt_journal')
            if journal is None:
                journal=(row.get('metrics') or {}).get('flow_snapshot',{}).get('attempt_journal')
            if journal is None:
                totals[unit]['unreported_parts'].append(part['part'])
                continue
            need(type(journal['attempts']) is int and journal['attempts']>=0
                 and type(journal['reserved_estimated_airtime_us']) is int
                 and journal['reserved_estimated_airtime_us']>=0, 'invalid_paid_attempt_totals')
            totals[unit]['known_attempts']+=journal['attempts']
            totals[unit]['known_reserved_estimated_airtime_us']+=journal['reserved_estimated_airtime_us']
            totals[unit]['fenced']=totals[unit]['fenced'] or journal.get('fenced',False)
            totals[unit]['recovered_unknown']+=journal.get('recovered_unknown',0)
    for row in totals.values():
        row['journal_report_complete']=bool(parts) and not row['unreported_parts']
        row['total_attempts']=row['known_attempts'] if row['journal_report_complete'] else None
        row['total_reserved_estimated_airtime_us']=row['known_reserved_estimated_airtime_us'] if row['journal_report_complete'] else None
    return totals


def receive_drop_totals(prepared, parts):
    """Diagnostic only: missing/invalid samples never become zero loss."""
    totals={}
    for unit in UNITS:
        baseline=prepared['expected_mcu'][unit]['dropped_receive']; rows=[]; unknown=[]
        previous=baseline; known_delta=0
        for index,part in enumerate(parts):
            endpoint=part.get('endpoints',{}).get(unit,{})
            before=(endpoint.get('radio_before') or {}).get('dropped_receive')
            after=(endpoint.get('radio_after') or {}).get('dropped_receive')
            valid=(type(before) is int and type(after) is int and 0<=previous==before<=after)
            delta=after-before if valid else None
            rows.append(dict(part=index,before=before,after=after,delta=delta,valid=valid))
            if valid:known_delta+=delta;previous=after
            else:unknown.append(index)
        complete=bool(parts) and not unknown
        totals[unit]=dict(
            baseline=baseline,latest=previous,known_delta=known_delta,
            total_delta=known_delta if complete else None,
            samples_complete=complete,unreported_or_invalid_parts=unknown,
            parts=rows,diagnostic_only=True,
        )
    return totals


