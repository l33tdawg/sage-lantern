import json
import pytest
from sage_lantern.answer_suggestions import AnswerStream, split_answer, validate_suggestions
from sage_lantern.active_conversation import ActiveConversation, MAX_CONTENT_BYTES, ANSWER_EXCERPT
from sage_lantern.adapters import LlamaCppHTTP
from test_adapters import FragmentedResponse, sse_event
from unittest.mock import patch


@pytest.mark.parametrize('chunk_size', range(1, 35))
def test_stream_hides_trailer_at_every_chunk_boundary(chunk_size):
    answer = 'A complete answer.\n\nUseful context with 中文 and café.'
    suggestions = ['How does that work?', 'What example could I try?']
    raw = answer + '\n<followups>' + json.dumps(suggestions) + '</followups>'
    pieces = []
    stream = AnswerStream(pieces.append)
    for offset in range(0, len(raw), chunk_size): stream.feed(raw[offset:offset + chunk_size])
    stream.finish()
    assert ''.join(pieces) == answer
    assert split_answer(raw) == (answer, suggestions)


@pytest.mark.parametrize('raw', ['Plain answer', 'A\n\nB', 'A\n<follow', 'A <followups> is an inline example'])
def test_normal_text_and_unfinished_prefix_are_not_lost(raw):
    pieces = []; stream = AnswerStream(pieces.append)
    for c in raw: stream.feed(c)
    stream.finish()
    assert ''.join(pieces) == raw
    assert split_answer(raw) == (raw, [])


@pytest.mark.parametrize('value', [None, {}, 'question', [False], [['nested']], ['x'] * 3,
                                    ['a\nb'], ['<script>'], ['界' * 81], ['\ud800']])
def test_invalid_suggestions_are_optional_not_answer_failures(value):
    assert validate_suggestions(value) == []
    assert split_answer('Answer\n<followups>' + json.dumps(value) + '</followups>') == ('Answer', [])


@pytest.mark.parametrize('trailer', ['not JSON</followups>', '["q?"]', '["q?"]</followups>trailing', '[]</followups>\n<followups>[]</followups>'])
def test_malformed_trailer_keeps_answer_without_metadata(trailer):
    assert split_answer('Answer\n<followups>' + trailer) == ('Answer', [])


@pytest.mark.parametrize('body,expected', [
    ('- What else should I check?', ['What else should I check?']),
    ('- What else should I check?\n- How do I start?', ['What else should I check?', 'How do I start?']),
    ('* Why does that happen?', ['Why does that happen?']),
    ('• What next?\n• And after that?', ['What next?', 'And after that?']),
    ('1. First question?\n2) Second question?', ['First question?', 'Second question?']),
])
def test_markdown_list_trailer_is_accepted(body, expected):
    raw = 'Answer\n<followups>\n' + body + '\n</followups>'
    assert split_answer(raw) == ('Answer', expected)


@pytest.mark.parametrize('body', [
    'not a list',
    '- one?\n- two?\n- three?',
    '- <script>alert(1)</script>',
    '- ' + '界' * 81,
    '- valid?\ntrailing words',
])
def test_markdown_list_trailer_is_still_bounded(body):
    assert split_answer('Answer\n<followups>\n' + body + '\n</followups>') == ('Answer', [])


@pytest.mark.parametrize('chunk_size', range(1, 20))
def test_stream_hides_a_list_trailer_at_every_chunk_boundary(chunk_size):
    answer = 'A complete answer with enough detail.'
    raw = answer + '\n<followups>\n- What else should I check?\n- How do I start?\n</followups>'
    pieces = []
    stream = AnswerStream(pieces.append)
    for offset in range(0, len(raw), chunk_size):
        stream.feed(raw[offset:offset + chunk_size])
    stream.finish()
    assert ''.join(pieces) == answer
    assert split_answer(raw) == (answer, ['What else should I check?', 'How do I start?'])


def test_duplicates_and_whitespace():
    assert validate_suggestions(['  How? ', 'how?']) == ['How?']


def test_long_answers_retain_latest_user_corrections_with_explicit_excerpts():
    chat = ActiveConversation()
    questions = ['Use Birch Hall for 18 guests.', 'Correction: make that 21 guests.', 'What tables do we need?', 'Use round tables.']
    for index, question in enumerate(questions):
        assert chat.commit(chat.prepare('owner'), str(index), question, 'Useful context. 中文 ' * 180)
    messages = chat.prepare('owner').messages()
    assert [row['content'] for row in messages[::2]] == questions
    assert all(row['content'].endswith(ANSWER_EXCERPT) for row in messages[1::2])
    assert sum(len(row['content'].encode('utf-8')) for row in messages) <= MAX_CONTENT_BYTES
    assert all(turn.answer == 'Useful context. 中文 ' * 180 for turn in chat.turns)


def test_one_large_answer_cannot_erase_the_question():
    chat = ActiveConversation()
    assert chat.commit(chat.prepare('owner'), 'source', 'Explain yeast.', 'Detailed answer. ' * 300)
    assert chat.prepare('owner').messages()[0]['content'] == 'Explain yeast.'
    assert chat.prepare('owner').messages()[1]['content'].endswith(ANSWER_EXCERPT)


def test_adapter_only_publishes_answer_and_resets_suggestions_on_next_request():
    model = LlamaCppHTTP('http://localhost', 'fixture', experimental_answer_suggestions=True)
    raw = 'Complete answer.\n<followups>["What can I try?"]</followups>'
    wire = [sse_event(piece) for piece in raw] + [sse_event(finish='stop'), b'data: [DONE]\n\n']
    pieces = []
    with patch('urllib.request.urlopen', return_value=FragmentedResponse(wire)):
        assert model.respond_stream('Explain this', [], pieces.append) == 'Complete answer.'
    assert ''.join(pieces) == 'Complete answer.'
    assert model.last_suggestions == ['What can I try?']
    with patch('urllib.request.urlopen', side_effect=TimeoutError), pytest.raises(TimeoutError):
        model.respond_stream('Next', [], pieces.append)
    assert model.last_suggestions == []


@pytest.mark.parametrize('raw', ['<followups>["Next?"]</followups>', '\n<followups>["Next?"]</followups>', '  \n<followups>'])
def test_metadata_only_is_never_an_answer(raw):
    pieces = []
    stream = AnswerStream(pieces.append)
    for c in raw: stream.feed(c)
    stream.finish()
    assert pieces == []
    assert split_answer(raw) == ('', [])


def test_experimental_policy_is_opt_in_and_default_does_not_interpret_answer_markup():
    model = LlamaCppHTTP('http://localhost', 'fixture')
    policy = json.loads(model._request('Explain', []).data)['messages'][0]['content']
    assert '1–3 short sentences' in policy
    assert '<followups>' not in policy
    raw = 'A code example:\n<followups>["Example"]</followups>'
    pieces = []
    wire = [sse_event(raw), sse_event(finish='stop'), b'data: [DONE]\n\n']
    with patch('urllib.request.urlopen', return_value=FragmentedResponse(wire)):
        assert model.respond_stream('Show markup', [], pieces.append) == raw
    assert ''.join(pieces) == raw
    assert model.last_suggestions == []
    experimental = LlamaCppHTTP('http://localhost', 'fixture', experimental_answer_suggestions=True)
    policy = json.loads(experimental._request('Explain', []).data)['messages'][0]['content']
    assert '2–4 short paragraphs' in policy
    assert '<followups>' in policy


def test_runtime_config_requires_explicit_boolean_opt_in(tmp_path):
    from pathlib import Path
    from sage_lantern.config import RuntimeConfig
    from sage_lantern.cli import build_orchestrator
    template = (Path(__file__).resolve().parents[2] / 'config/lantern.example.toml').read_text().replace('static_items = []', 'static_items = ["Fixture"]').replace('/var/lib/sage-lantern', str(tmp_path))
    path = tmp_path / 'lantern.toml'
    path.write_text(template)
    assert RuntimeConfig.load(path).experimental_answer_suggestions is False
    path.write_text(template.replace('[llm]', '[llm]\nexperimental_answer_suggestions = true'))
    config = RuntimeConfig.load(path)
    assert config.experimental_answer_suggestions is True
    assert build_orchestrator(config).llm.experimental_answer_suggestions is True
    path.write_text(template.replace('[llm]', '[llm]\nexperimental_answer_suggestions = "true"'))
    with pytest.raises(ValueError, match='must be a boolean'):
        RuntimeConfig.load(path)
