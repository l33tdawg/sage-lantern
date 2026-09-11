import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('observe_voice', Path(__file__).resolve().parents[1] /
    'lantern/benchmarks/observe_voice.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_observer_exports_only_numeric_evidence_not_private_text():
    value = dict(schema='sage.lantern.voice-state.v1', phase='complete', transcript='synthetic',
                 response='PRIVATE_RESPONSE', detail='PRIVATE_WARNING', token='SECRET', timings_ms={'total': 12})
    result = MODULE.project(value, hashlib.sha256(b'synthetic').hexdigest())
    raw = json.dumps(result)
    assert 'PRIVATE' not in raw and 'SECRET' not in raw
    assert result['exact_question_bytes_match'] is True
    assert result['history_warning_present'] is True
    assert result['timings_ms']['total'] == 12
    assert result['timings_ms']['stt'] is None
    assert result['cache_class'] == 'unverified' and result['fixed_audio'] is False


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '12', None])
def test_invalid_metrics_never_become_zero(value):
    result = MODULE.project(dict(schema='sage.lantern.voice-state.v1', phase='complete',
                                  transcript='different', timings_ms={'total': value}), 'a' * 64)
    assert result['timings_ms']['total'] is None
    assert result['exact_question_bytes_match'] is False


def test_nonterminal_state_is_not_a_success():
    assert MODULE.project(dict(schema='sage.lantern.voice-state.v1', phase='thinking'), 'a' * 64) is None


def test_observer_projects_metrics_and_opaque_id_without_contents():
    result = MODULE.project(dict(schema='sage.lantern.voice-state.v1', phase='complete',
        session_id='11111111-1111-4111-8111-111111111111', transcript='PRIVATE QUESTION',
        response='PRIVATE ANSWER', timings_ms={'first_text': 123},
        inference_metrics=dict(prompt_tokens=20, cached_tokens=12, output_tokens=3,
            prompt_processed=8, prefill_ms=5.5, generation_ms=12, text='SECRET')), 'a' * 64)
    assert result['prompt_tokens'] == 20 and result['cached_tokens'] == 12
    assert result['prompt_processed'] == 8 and result['model_prefill_ms'] == 5.5
    assert result['first_published_ui_ms'] is None
    assert result['first_text_scope'] == 'runtime_entry_to_first_nonwhitespace_delta'
    assert 'PRIVATE' not in json.dumps(result) and 'SECRET' not in json.dumps(result)
    assert result['session_id'] == '11111111-1111-4111-8111-111111111111'


@pytest.mark.parametrize('changed', [False, True])
def test_observer_binds_model_identity_without_exporting_state(monkeypatch, capsys, changed):
    monkeypatch.setattr('sys.argv', ['observer', '--question-sha256', 'a' * 64, '--count', '1', '--seconds', '5'])
    monkeypatch.setattr(MODULE.Path, 'read_text', lambda *args, **kwargs: 'boot')
    monkeypatch.setattr(MODULE, 'resources', lambda: {})
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: None)
    states = iter([{'session_id': 'initial'}, dict(schema='sage.lantern.voice-state.v1', phase='complete',
        session_id='11111111-1111-4111-8111-111111111111', transcript='PRIVATE', response='SECRET', timings_ms={})])
    monkeypatch.setattr(MODULE, 'read_state', lambda: next(states))
    model_calls = []
    def identity(service='sage-lantern-voice-loop.service'):
        if service == 'sage-lantern-llm.service':
            model_calls.append(service)
            return {'MainPID': '99' if changed and len(model_calls) > 1 else '77', 'InvocationID': 'model'}
        return {'MainPID': '55', 'InvocationID': 'voice'}
    monkeypatch.setattr(MODULE, 'identity', identity)
    if changed:
        with pytest.raises(ValueError, match='model_identity_changed'):
            MODULE.main()
    else:
        MODULE.main()
    raw = capsys.readouterr().out
    assert 'PRIVATE' not in raw and 'SECRET' not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    if changed:
        assert len(rows) == 1
    else:
        assert rows[1]['model'] == {'MainPID': '77', 'InvocationID': 'model'}
