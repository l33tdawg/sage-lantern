# SPDX-License-Identifier: Apache-2.0
"""Public pilot orchestration guard; native start admission remains mandatory.

Run both independent metadata guards concurrently on distinct units, and wait
for both to finish before the first endpoint. Explicit LoRa sender-first mode
prepares its zero-credit receive loop before receiver startup ACKs; default and
TCP retain receiver-first order. Only the exact checked second launch remains. This adapter grants no native permission and changes no lifetime.
"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock


def install(operator, validate, *, lora_sender_first=False):
    if type(lora_sender_first) is not bool:
        raise RuntimeError('invalid_lora_startup_order')
    original = operator.start
    pending = {}
    launch_lock = Lock()

    def binding(pair):
        if (type(pair) is not dict
                or type(pair.get('sender')) is not str or type(pair.get('receiver')) is not str
                or pair.get('sender') not in ('unit-a', 'unit-b', 'unit-c')
                or pair.get('receiver') not in ('unit-a', 'unit-b', 'unit-c')
                or pair.get('sender') == pair.get('receiver')
                or type(pair.get('run_id')) is not str
                or len(pair['run_id']) != 32
                or any(c not in '0123456789abcdef' for c in pair['run_id'])):
            raise RuntimeError('invalid_pair_launch_binding')
        return hashlib.sha256(json.dumps(pair, sort_keys=True, separators=(',', ':'),
                                        allow_nan=False).encode()).digest()

    def checked_start(unit, pair):
        digest = binding(pair)
        key = pair['run_id']
        first, second = ((pair['sender'], pair['receiver']) if lora_sender_first and pair.get('transport')=='lora'
                         else (pair['receiver'], pair['sender']))
        if unit == first:
            if key in pending:
                raise RuntimeError('pair_launch_already_used')
            # A failed validation is also spent; no silent retry or reuse.
            pending[key] = ('refused', digest)
            endpoints = (('receiver', pair['receiver']), ('sender', pair['sender']))
            def checked_guard(role, endpoint):
                result = validate(endpoint)
                if result is False:
                    raise RuntimeError(role + '_guard_refused')
                if binding(pair) != digest:
                    raise RuntimeError('pair_changed_during_' + role + '_guard')
                return result
            failures = []
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='meshticulum-prelaunch-guard') as pool:
                guards = [pool.submit(checked_guard, role, endpoint) for role, endpoint in endpoints]
                # Observe both results even if either refuses. Executor shutdown
                # waits for both callbacks and their native client cleanup.
                for future in guards:
                    try:
                        future.result()
                    except BaseException as error:
                        failures.append(error)
            if failures:
                raise failures[0]
            if binding(pair) != digest:
                raise RuntimeError('pair_changed_during_parallel_guards')
            result = original(unit, pair)
            if binding(pair) != digest:
                raise RuntimeError('pair_changed_during_first_endpoint_launch')
            pending[key] = ('second_pending', digest)
            return result
        if unit != second or pending.get(key) != ('second_pending', digest):
            raise RuntimeError('second_pair_not_checked_or_rebound')
        pending[key] = ('spent', digest)
        # Original start still invokes the native entry that consumes the
        # one-use tombstone and checks policy, facts, expiry and radio ownership.
        return original(unit, pair)

    def start(unit, pair):
        # Serialize launch calls so concurrent caller pairs cannot run a second
        # guard on a unit already being checked by this adapter.
        with launch_lock:
            return checked_start(unit, pair)

    operator.start = start
    return start
