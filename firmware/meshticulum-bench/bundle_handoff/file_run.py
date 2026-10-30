"""One-shot whole-file coordinator. No retries, resets or deadline renewal."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import threading


def run_file(api, prepared, password, scope_pin):
    a, p = api, prepared
    scope = json.loads(a.read_pinned(a.BASE/'SCOPE.json', scope_pin))
    a.need(scope['prepared_sha256'] == a.sha(a.canonical(p)), 'scope_prepared_changed')
    a.need(scope['order'] == p['order'], 'scope_order_changed')
    a.validate_mode_binding(p, scope)
    a.validate_window(scope, *a.clock(), a.boot_id(), reserve_us=p['part_count']*a.ACK_GRACE_US+5_000_000)
    directory = Path(p['campaign_directory'])
    claim = json.loads((directory/'CAMPAIGN_CLAIM_0.json').read_bytes())
    a.need(claim['scope_sha256'] == scope_pin and claim['reserved'] == p['run_budget'], 'claim_required')
    window = json.loads(a.read_pinned(directory/'CAMPAIGN_WINDOW.json', claim['campaign_window_sha256']))
    a.validate_window(window, *a.clock(), a.boot_id())
    a.write_new(a.BASE/'SCOPE_CONSUMED.json', dict(scope_sha256=scope_pin, consumed_epoch_us=a.clock()[0]))
    order = p['order']; rows = [dict(part=i, complete=False, endpoints={}) for i in order]
    commands = {u: queue.Queue() for u in a.UNITS}
    lock = threading.Lock(); aborted = threading.Event()
    ready = set(); done = set(); position = 0; errors = []; endpoints = {}

    def enqueue(unit, action, part):
        commands[unit].put(dict(action=action, part=part, scope_sha256=scope_pin))

    def event(unit, value):
        nonlocal position
        with lock:
            a.validate_window(scope, *a.clock(), a.boot_id())
            a.need(not aborted.is_set() and position < len(order), 'unexpected_late_event')
            index = order[position]
            a.need(value['scope_sha256'] == scope_pin and value['unit'] == unit
                   and type(value['part']) is int and value['part'] == index, 'event_binding_changed')
            kind = value['event']
            if kind == 'ready':
                a.need(unit not in ready and unit not in done, 'duplicate_ready')
                ready.add(unit)
                if ready == set(a.UNITS):
                    enqueue(p['sender'], 'go', index)
            elif kind == 'part_done':
                a.need(ready == set(a.UNITS) and unit not in done, 'unexpected_part_done')
                row = rows[position]
                endpoint = dict(complete=True, unit=unit, part=index, metrics=value['metrics'],
                    public_result=value['public_result'], child_started=value['metrics']['part_invocation'])
                row['endpoints'][unit] = endpoint
                a.write_new(a.BASE/f'part-{index}-{unit}-RESULT.json', endpoint)
                done.add(unit)
                if done == set(a.UNITS):
                    totals = a.paid_totals(rows[:position+1])
                    for u, cost in totals.items():
                        a.need(cost['journal_report_complete'] and not cost['fenced']
                               and cost['recovered_unknown'] == 0, 'unsettled_paid_debt')
                        a.need(cost['known_attempts'] <= p['run_budget'][u]['attempts']
                               and cost['known_reserved_estimated_airtime_us'] <= p['run_budget'][u]['estimated_airtime_us'], 'file_budget_exceeded')
                    row.update(complete=True, paid_totals=totals,
                               timing=a.part_timing(row['endpoints'][p['sender']]))
                    a.write_new(a.BASE/f'part-{index}-RESULT.json', row)
                    position += 1; ready.clear(); done.clear()
                    if position < len(order):
                        a.validate_window(scope, *a.clock(), a.boot_id(),
                            reserve_us=(len(order)-position)*a.ACK_GRACE_US+5_000_000)
                        for u in a.UNITS:
                            enqueue(u, 'advance', order[position])
            else:
                raise ValueError('unknown_event')

    def endpoint(unit):
        try:
            request = dict(action='run', prepared=p, scope=scope, unit=unit, by_id=p['by_id'],
                current_mcu=p['expected_mcu'], grant_files={str(i): {
                    n: base64.b64encode(a.read_pinned(a.BASE/'issued'/unit/str(i)/n, pin)).decode()
                    for n, pin in scope['grants'][unit][str(i)].items()} for i in order})
            transport = dict(p['units'][unit]); transport['python'] = p['staged_root']+'/venv/bin/python'
            def controls():
                a.need(not aborted.is_set(), 'peer_failed_stop')
                output = []
                while True:
                    try: output.append(commands[unit].get_nowait())
                    except queue.Empty: return output
            reply = a.rpc(transport, a.remote_source(), request, password=password,
                seconds=a.scope_rpc_seconds(scope, *a.clock()), event=lambda v: event(unit, v), control=controls)
            a.write_new(a.BASE/f'file-{unit}-RESULT.json', reply)
            with lock: endpoints[unit] = reply
            a.need(reply['complete'], 'file_endpoint_failed')
        except BaseException as error:
            aborted.set()
            with lock: errors.append(dict(unit=unit, type=type(error).__name__, reason=str(error)[:240]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(endpoint, u) for u in a.UNITS]
        for job in jobs: job.result()
    current = {u: None for u in a.UNITS}
    for unit, reply in endpoints.items():
        try:
            current[unit] = a.validate_counter_progress(p['expected_mcu'][unit], p['expected_mcu'][unit],
                reply['radio_after'], p['policy']['unit_limits'][unit])
        except BaseException as error:
            errors.append(dict(unit=unit, type=type(error).__name__, reason='postflight_native_unknown'))
    result = dict(schema='meshticulum.bundle-handoff-file-result.v1', complete=not errors
        and position == len(order) and len(endpoints) == 2 and all(e['complete'] for e in endpoints.values())
        and all(r['complete'] for r in rows),
        prepared_sha256=a.sha(a.canonical(p)), scope_sha256=scope_pin, parts=rows, endpoints=endpoints,
        errors=errors, paid_totals=a.paid_totals(rows), original_native=p['expected_mcu'],
        last_observed_native=current, prior_history_modified=False, useful_bytes=p['useful_bytes'],
        source_commit=p['core_commit'], wheel_sha256=p['candidate_wheel_sha256'],
        original_end_epoch_us=scope['expires_epoch_us'], finished_epoch_us=a.clock()[0])
    a.write_new(a.BASE/'RESULT.json', result)
    a.write_new(directory/'EXECUTION_CLOSED.json', dict(scope_sha256=scope_pin, complete=result['complete'],
        refunds=0, automatic_retry=False, finished_epoch_us=a.clock()[0]))
    return result
