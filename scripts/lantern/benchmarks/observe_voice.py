#!/usr/bin/env python3
"""Read-only numeric observer for owner-driven Ask benchmarks; submits nothing.

Variable speech, unknown prompt-cache state, and live owner retrieval are not
an identical-work engine benchmark. Never export transcript/response/detail.
No session token is read or passed. Existing completed state is skipped.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

from sage_lantern.inference_metrics import project as project_metrics


STATE = Path('/run/sage-lantern-voice/state.json')
FIELDS = ('stt', 'sage_context', 'llm', 'llm_first_token', 'first_text', 'tts', 'total')


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def project(value, question_sha256):
    if type(value) is not dict or value.get('schema') != 'sage.lantern.voice-state.v1':
        raise ValueError('invalid_voice_state')
    if value.get('phase') != 'complete':
        return None
    timings = value.get('timings_ms')
    if type(timings) is not dict:
        raise ValueError('missing_timings')
    transcript = value.get('transcript')
    metrics = project_metrics(value.get('inference_metrics'))
    identifier = value.get('session_id')
    try:
        identifier = str(uuid.UUID(identifier)) if type(identifier) is str and str(uuid.UUID(identifier)) == identifier else None
    except ValueError:
        identifier = None
    result = dict(schema='sage.lantern.owner-benchmark-observation.v1', session_id=identifier,
        timings_ms={name: timings[name] if numeric(timings.get(name)) else None for name in FIELDS},
        exact_question_bytes_match=type(transcript) is str and
            hashlib.sha256(transcript.encode()).hexdigest() == question_sha256,
        history_warning_present=bool(value.get('detail')),
        cache_class='unverified', fixed_audio=False, fixed_retrieval=False,
        prompt_tokens=metrics['prompt_tokens'], output_tokens=metrics['output_tokens'],
        cached_tokens=metrics['cached_tokens'], prompt_processed=metrics['prompt_processed'],
        model_prefill_ms=metrics['prefill_ms'], model_generation_ms=metrics['generation_ms'],
        first_text_scope='runtime_entry_to_first_nonwhitespace_delta', first_published_ui_ms=None)
    return result


def read_state():
    descriptor = os.open(STATE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 131072:
            raise ValueError('invalid_state_file')
        raw = os.read(descriptor, 131073)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError('state_changed')
        return json.loads(raw)
    finally:
        os.close(descriptor)


def identity(service='sage-lantern-voice-loop.service'):
    raw = subprocess.check_output(['/usr/bin/systemctl', 'show', service,
        '-p', 'MainPID', '-p', 'InvocationID', '-p', 'ActiveState', '--all'], timeout=5, text=True)
    result = dict(line.split('=', 1) for line in raw.splitlines())
    if result['ActiveState'] != 'active' or not result['MainPID'].isdecimal() or int(result['MainPID']) <= 0:
        raise ValueError('voice_not_active')
    return result


def resources():
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        name, value = line.split(':', 1)
        if name in ('MemAvailable', 'SwapTotal', 'SwapFree'):
            memory[name] = int(value.strip().split()[0]) * 1024
    temperatures = [int(path.read_text()) for path in Path('/sys/class/thermal').glob('thermal_zone*/temp')]
    return dict(memory_bytes=memory, maximum_temperature_millicelsius=max(temperatures) if temperatures else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question-sha256', required=True)
    parser.add_argument('--count', type=int, default=3)
    parser.add_argument('--seconds', type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.count <= 10 or not 1 <= args.seconds <= 1800:
        raise ValueError('invalid_bounds')
    baseline = identity()
    model = identity('sage-lantern-llm.service')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    initial = read_state()
    seen = {initial.get('session_id')}
    started = time.monotonic()
    count = 0
    print(json.dumps(dict(observer_started=True, boot_id=boot, voice=baseline, model=model, resources=resources())), flush=True)
    while time.monotonic() - started < args.seconds and count < args.count:
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != boot:
            raise ValueError('boot_changed')
        value = read_state()
        if identity('sage-lantern-llm.service') != model:
            raise ValueError('model_identity_changed')
        session = value.get('session_id')
        if session and session not in seen and value.get('phase') == 'complete':
            if identity() != baseline:
                raise ValueError('voice_identity_changed')
            row = project(value, args.question_sha256)
            count += 1
            seen.add(session)
            row.update(run=count, observed_monotonic_seconds=time.monotonic()-started,
                       model=model, resources=resources())
            print(json.dumps(row, allow_nan=False), flush=True)
        time.sleep(1)
    print(json.dumps(dict(observer_finished=True, completed_runs=count, requested_runs=args.count)), flush=True)


if __name__ == '__main__':
    main()
