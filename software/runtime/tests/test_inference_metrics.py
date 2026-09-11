import io
import json

import pytest

from sage_lantern.adapters import LlamaCppHTTP
from sage_lantern.inference_metrics import COUNTS, TIMINGS, project, terminal_metrics


def event(value):
    return ('data: ' + json.dumps(value) + '\n\n').encode()


def choice(content=None, finish=None, **extra):
    return dict(choices=[dict(index=0, delta={} if content is None else dict(content=content), finish_reason=finish)], **extra)


@pytest.mark.parametrize('value', [None, True, -1, float('nan'), float('inf'), '12', {}, [], 2**1000])
def test_invalid_metrics_are_null_not_zero(value):
    assert project({name: value for name in COUNTS + TIMINGS}) == dict.fromkeys(COUNTS + TIMINGS)


def test_projection_has_no_contents_and_separate_prompt_semantics():
    result = terminal_metrics(None, dict(usage=dict(prompt_tokens=20, completion_tokens=3,
        prompt_tokens_details=dict(cached_tokens=12), secret='PRIVATE'),
        timings=dict(prompt_n=8, prompt_ms=12.5, predicted_ms=50), prompt='SECRET'))
    assert result == dict(prompt_tokens=20, cached_tokens=12, output_tokens=3,
        prompt_processed=8, prefill_ms=12.5, generation_ms=50)
    assert 'PRIVATE' not in json.dumps(result) and 'SECRET' not in json.dumps(result)
    assert project({'prompt_tokens': 1.5})['prompt_tokens'] is None


@pytest.mark.parametrize('extra_chunk', [False, True])
def test_terminal_metrics_and_optional_trailing_usage(monkeypatch, extra_chunk):
    payload = event(choice('PRIVATE answer')) + event(choice(finish='stop',
        usage=dict(prompt_tokens=20, completion_tokens=3), timings=dict(prompt_n=8, prompt_ms=12.5)))
    if extra_chunk:
        payload += event(dict(choices=[], usage=dict(prompt_tokens_details=dict(cached_tokens=12))))
    payload += b'data: [DONE]\n\n'
    monkeypatch.delenv('SAGE_LANTERN_INFERENCE_GUARD', raising=False)
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: io.BytesIO(payload))
    model = LlamaCppHTTP('http://localhost', 'fixture')
    assert model.respond_stream('PRIVATE question', [], lambda text: None) == 'PRIVATE answer'
    assert model.last_metrics['prompt_tokens'] == 20
    assert model.last_metrics['cached_tokens'] == (12 if extra_chunk else None)
    assert model.last_metrics['generation_ms'] is None
    assert 'PRIVATE' not in json.dumps(model.last_metrics)


@pytest.mark.parametrize('fault', ['absent', 'malformed', 'early', 'cancel', 'no_text', 'no_terminal'])
def test_unknown_or_failed_requests_never_reuse_metrics(monkeypatch, fault):
    raw = event(choice('answer', usage={'prompt_tokens': 999})) if fault == 'early' else event(choice('answer'))
    if fault == 'no_text': raw = b''
    if fault != 'no_terminal':
        raw += event(choice(finish='stop', usage={'prompt_tokens': True}, timings={'prompt_ms': 'PRIVATE'})
                     if fault == 'malformed' else choice(finish='stop'))
    raw += b'data: [DONE]\n\n'
    monkeypatch.delenv('SAGE_LANTERN_INFERENCE_GUARD', raising=False)
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: io.BytesIO(raw))
    model = LlamaCppHTTP('http://localhost', 'fixture')
    model.last_metrics = {'prompt_tokens': 888}
    def delta(text):
        if fault == 'cancel': raise InterruptedError('cancelled')
    if fault in ('cancel', 'no_text', 'no_terminal'):
        with pytest.raises((RuntimeError, InterruptedError)):
            model.respond_stream('fixture', [], delta)
    else:
        model.respond_stream('fixture', [], delta)
    assert model.last_metrics == project(None)


def test_guard_completion_failure_does_not_publish_metrics(monkeypatch):
    from types import SimpleNamespace
    raw = event(choice('answer')) + event(choice(finish='stop', usage={'prompt_tokens': 20})) + b'data: [DONE]\n\n'
    model = LlamaCppHTTP('http://127.0.0.1:8081', 'fixture')
    def complete():
        raise InterruptedError('guard uncertain')
    monkeypatch.setattr(model, '_inference_admission', lambda: SimpleNamespace(complete=complete))
    monkeypatch.setattr(model, '_open_response', lambda *args: io.BytesIO(raw))
    with pytest.raises(InterruptedError):
        model.respond_stream('fixture', [], lambda text: None)
    assert model.last_metrics == project(None)
