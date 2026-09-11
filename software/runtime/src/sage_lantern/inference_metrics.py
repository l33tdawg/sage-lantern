"""Numeric-only projections; absent or invalid measurements remain unknown."""

import math


COUNTS = ('prompt_tokens', 'cached_tokens', 'output_tokens', 'prompt_processed')
TIMINGS = ('prefill_ms', 'generation_ms')


def project(value):
    source = value if type(value) is dict else {}
    result = {}
    for name in COUNTS + TIMINGS:
        item = source.get(name)
        valid = (type(item) is int and 0 <= item <= 2**53 - 1) if name in COUNTS else (
            type(item) in (int, float) and 0 <= item <= 2**53 - 1 and math.isfinite(item))
        result[name] = item if valid else None
    return result


def terminal_metrics(current, payload):
    result = project(current)
    usage = payload.get('usage')
    timings = payload.get('timings')
    if type(usage) is dict:
        for source, target in (('prompt_tokens', 'prompt_tokens'), ('completion_tokens', 'output_tokens')):
            if source in usage:
                result[target] = project({target: usage[source]})[target]
        details = usage.get('prompt_tokens_details')
        if type(details) is dict and 'cached_tokens' in details:
            result['cached_tokens'] = project({'cached_tokens': details['cached_tokens']})['cached_tokens']
    if type(timings) is dict:
        for source, target in (('prompt_n', 'prompt_processed'), ('prompt_ms', 'prefill_ms'),
                               ('predicted_ms', 'generation_ms')):
            if source in timings:
                result[target] = project({target: timings[source]})[target]
    return result
