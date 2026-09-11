#!/usr/bin/env python3
"""Summarize dataless guarded benchmark evidence; never submit model requests.

Input is JSONL from a separately reviewed admitted workload collector. Missing
instrumentation stays null. Cold classes are never mixed with warm classes.
No prompts, responses, access tokens, or private context belong in this schema.
"""

import argparse
import json
import math
from pathlib import Path
import re
import statistics


TIMINGS = ('admission', 'stt', 'context', 'image_encoding', 'prefill', 'llm_ttft',
           'first_runtime_text', 'generation', 'total')
COUNTS = ('prompt_processed', 'prompt_cached', 'output')
RESOURCES = ('service_peak_bytes', 'host_available_min_bytes', 'swap_delta_bytes',
             'cpu_utilization_percent', 'cpu_frequency_min_khz', 'temperature_max_millicelsius')
PINS = ('config_sha256', 'workload_sha256', 'audio_sha256', 'source_fixture_sha256', 'rendered_prompt_sha256')
CACHE_CLASSES = ('first_after_process_start', 'resident_cache_miss', 'repeated_prefix_hit', 'unverified')


def require(value):
    if not value:
        raise ValueError('invalid_guarded_benchmark_evidence')


def unique(items):
    result = {}
    for name, value in items:
        require(name not in result)
        result[name] = value
    return result


def validate(row):
    require(type(row) is dict and set(row) == {'schema', 'run_id', 'configuration', 'cache_class',
        'guarded_admission_verified', 'outcome', 'synthetic_correct', 'stop_reason',
        'timings_ms', 'tokens', 'resources', *PINS})
    require(row['schema'] == 'sage.lantern.guarded-benchmark.v1')
    for field in ('run_id', 'configuration'):
        require(type(row[field]) is str and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', row[field]))
    for field in PINS:
        require(row[field] is None or type(row[field]) is str and re.fullmatch(r'[0-9a-f]{64}', row[field]))
    require(row['config_sha256'] is not None and row['workload_sha256'] is not None)
    require(row['cache_class'] in CACHE_CLASSES and row['guarded_admission_verified'] is True)
    require(row['outcome'] in ('complete', 'failed', 'cancelled', 'unknown'))
    require(row['synthetic_correct'] is None or type(row['synthetic_correct']) is bool)
    require(row['stop_reason'] in (None, 'stop', 'length', 'cancelled', 'error', 'unknown'))
    for field, keys in (('timings_ms', TIMINGS), ('tokens', COUNTS), ('resources', RESOURCES)):
        require(type(row[field]) is dict and set(row[field]) == set(keys))
        for name, value in row[field].items():
            require(value is None or type(value) in (int, float) and math.isfinite(value)
                    and (value >= 0 or name == 'swap_delta_bytes'))
            if field == 'tokens':
                require(value is None or type(value) is int)
    return row


def summarize(rows):
    groups = {}
    seen = set()
    for row in rows:
        validate(row)
        require(row['run_id'] not in seen)
        seen.add(row['run_id'])
        key = tuple(row[name] for name in ('configuration', 'cache_class', *PINS))
        groups.setdefault(key, []).append(row)
    result = []
    for key, runs in groups.items():
        complete = [row for row in runs if row['outcome'] == 'complete']
        measured = {}
        for name in TIMINGS:
            values = [row['timings_ms'][name] for row in complete if row['timings_ms'][name] is not None]
            measured[name] = dict(samples=len(values), median=statistics.median(values) if values else None,
                                  minimum=min(values) if values else None, maximum=max(values) if values else None)
        warm = key[1] in ('resident_cache_miss', 'repeated_prefix_hit')
        result.append(dict(binding=dict(zip(('configuration', 'cache_class', *PINS), key)),
            runs=runs, complete_count=len(complete), failed_or_incomplete_count=len(runs)-len(complete),
            three_warm_repeats_verified=warm and len(complete) >= 3,
            synthetic_correctness_verified=bool(complete) and all(row['synthetic_correct'] is True for row in complete),
            timings_ms=measured))
    return dict(schema='sage.lantern.guarded-benchmark-report.v1', groups=result,
                p95_estimated=False, speedup_claimed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    raw = args.evidence.read_bytes()
    require(len(raw) <= 8 * 1024 * 1024)
    rows = [json.loads(line, object_pairs_hook=unique) for line in raw.splitlines() if line.strip()]
    require(0 < len(rows) <= 1000)
    print(json.dumps(summarize(rows), sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
