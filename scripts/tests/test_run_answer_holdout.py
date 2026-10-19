"""Offline-only fake HTTP; synthetic test fixtures are not the heldout corpus."""
import argparse
import copy
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('runner', ROOT/'scripts/lantern/benchmarks/run_answer_holdout.py')
R = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(R)
H = R.eval


def complete(text='synthetic answer'):
    return {'model': 'test-native', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text},
             'finish_reason': 'stop'}], 'usage': {'completion_tokens': 3}}


def sse(text='synthetic answer', finish=True, done=True):
    values = [{'model': 'test-native', 'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]}]
    if finish:
        values.append({'model': 'test-native', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'completion_tokens': 3}})
        values.append({'model': 'test-native', 'choices': [], 'usage': {'completion_tokens': 3}})
    raw = b''.join(b'data: ' + H.canonical(v).strip() + b'\n\n' for v in values)
    return raw + (b'data: [DONE]\n\n' if done else b'')


class FakeServer:
    def __init__(self, mode='json'):
        self.posts = []; self.mode = mode; self.busy = False
        outer = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_GET(self):
                self.respond(200, 'application/json', H.canonical([{'id': 0, 'is_processing': outer.busy, 'n_ctx': 2048}]))
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length'])); outer.posts.append(H.document(body))
                if outer.mode == 'timeout':
                    time.sleep(0.2)
                if outer.mode == 'trickle':
                    self.send_response(200); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', '100'); self.end_headers()
                    try:
                        for _ in range(100): self.wfile.write(b' '); self.wfile.flush(); time.sleep(0.02)
                    except (BrokenPipeError, ConnectionResetError): pass
                    return
                if outer.mode == 'redirect':
                    self.send_response(307); self.send_header('Location', 'http://example.com'); self.send_header('Content-Length', '0'); self.end_headers(); return
                if outer.mode == 'oversize': raw = b'x' * (R.MAX_BODY + 1); kind = 'application/json'
                elif outer.mode in ('stream', 'partial'):
                    raw = sse(finish=outer.mode != 'partial'); kind = 'text/event-stream'
                else: raw = H.canonical(complete()); kind = 'application/json'
                self.respond(200, kind, raw)
            def respond(self, status, kind, raw):
                try:
                    self.send_response(status); self.send_header('Content-Type', kind)
                    self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.endpoint = 'http://127.0.0.1:' + str(self.server.server_port)
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        corpus = self.root/'data'; corpus.mkdir(); (corpus/'train.jsonl').write_text('{"text":"unrelated training datum"}\n')
        cases = []
        for index in range(44):
            cases.append({'id': f'fixture-{index:02}', 'family': 'test', 'phrasing': 'template-covered', 'entities': [f'Testmarker{index:02}'],
                'question': f'What is synthetic test {index}?', 'context': [], 'conversation': [], 'gold': 'fixturegold',
                'required': ['fixturegold'], 'forbidden': [], 'exact': None, 'message_status': None, 'dependency': None})
        (self.root/'cases.jsonl').write_bytes(b''.join(H.canonical(c) for c in cases))
        (self.root/'policy.txt').write_text('Test only synthetic policy.')
        self.freeze = self.root/'frozen'; H.freeze(self.root/'cases.jsonl', self.root/'policy.txt', corpus, self.freeze)
        manifest = H.document((self.freeze/'manifest.json').read_bytes())
        for name, value in [('MANIFEST', H.digest(self.freeze/'manifest.json')), ('REQUESTS', manifest['files']['requests.jsonl'])]:
            p = patch.object(R, name, value); p.start(); self.addCleanup(p.stop)
        self.pipeline = {'schema': 'sage.lantern.inference-pipeline.v1', 'contract': R.CONTRACT,
            'model_sha256': 'a'*64, 'model_name': 'test-native', 'engine_source_sha256': R.ENGINE_SOURCE,
            'engine_binary_sha256': 'b'*64, 'server_sha256': R.SERVER_SOURCE, 'librkllmrt_sha256': 'c'*64,
            'process_launch_sha256': 'd'*64, 'python_runtime_sha256': 'e'*64, 'context_tokens': 2048}
        self.pipeline_path = self.root/'pipeline.json'; self.pipeline_path.write_bytes(H.canonical(self.pipeline))
        candidate = {'model_sha256': 'a'*64, 'pipeline_sha256': H.digest(self.pipeline_path), 'generation': R.GENERATION,
                     'selected_without_holdout': True, 'heldout_used_for_training': False}
        self.candidate_path = self.root/'candidate.json'; self.candidate_path.write_bytes(H.canonical(candidate))
        self.ledger = self.root/'ledger'; self.receipt = H.register(self.freeze, self.candidate_path, self.ledger)
        self.window_path = self.root/'window.json'

    def args(self, endpoint='http://127.0.0.1:12345', stream=False):
        now = dt.datetime.now(dt.timezone.utc)
        self.window = {'schema': 'sage.lantern.inference-window.v1', 'owner': 'backlog_audit',
            'candidate_sha256': self.receipt['candidate_sha256'], 'pipeline_sha256': self.receipt['candidate']['pipeline_sha256'],
            'endpoint': endpoint, 'observed_at': now.isoformat(), 'expires_at': (now+dt.timedelta(minutes=10)).isoformat(),
            'exclusive_idle_window': True, 'live_artifacts_match_pipeline': True, 'engine_build_matches_source': True,
            'model_selected_without_holdout': True, 'service_pid': 10, 'engine_pid': 11, 'observation_sha256': 'f'*64}
        self.window_path.write_bytes(H.canonical(self.window))
        return argparse.Namespace(freeze=str(self.freeze), ledger=str(self.ledger), candidate_sha256=self.receipt['candidate_sha256'],
            pipeline=str(self.pipeline_path), window=str(self.window_path), window_sha256=H.digest(self.window_path),
            endpoint=endpoint, output=str(self.root/'out'), timeout=2, total_timeout=30, stream=stream)

    def verify(self, args):
        return R.verify(args.freeze, args.ledger, args.candidate_sha256, args.pipeline, args.window, args.window_sha256, args.endpoint)

    def server(self, mode='json'):
        server = FakeServer(mode); self.addCleanup(server.close); return server

    def test_valid_pins_and_render(self): self.assertEqual(len(self.verify(self.args())[0]), 44)

    def test_freeze_mutation_before_network(self):
        args = self.args(); (self.freeze/'requests.jsonl').write_text('{}\n')
        with patch.object(R, 'exchange') as exchange:
            with self.assertRaisesRegex(ValueError, 'frozen_file_changed'): R.run(args)
            exchange.assert_not_called()

    def test_pipeline_mutation_before_network(self):
        args = self.args(); self.pipeline_path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'pipeline_pin'): self.verify(args)

    def test_wrong_candidate_registration(self):
        args = self.args(); args.candidate_sha256 = '0'*64
        with self.assertRaises(FileNotFoundError): self.verify(args)

    def test_owner_window_mutation(self):
        args = self.args(); self.window_path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'window_pin'): self.verify(args)

    def test_window_expiry(self):
        args = self.args(); self.window['expires_at'] = self.window['observed_at']
        self.window_path.write_bytes(H.canonical(self.window)); args.window_sha256 = H.digest(self.window_path)
        with self.assertRaisesRegex(ValueError, 'window_stale'): self.verify(args)

    def test_no_false_owner_attestation(self):
        for field in ('exclusive_idle_window', 'live_artifacts_match_pipeline', 'engine_build_matches_source'):
            args = self.args(); self.window[field] = False
            self.window_path.write_bytes(H.canonical(self.window)); args.window_sha256 = H.digest(self.window_path)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'attestation_required'): self.verify(args)

    def test_remote_url_credentials_and_redirect_targets_rejected(self):
        for url in ('http://localhost:12', 'https://127.0.0.1:12', 'http://127.0.0.2:12', 'http://name:pass@127.0.0.1:12',
                    'http://127.0.0.1:12/v1', 'http://127.0.0.1:12?token=x', 'http://127.0.0.1'):
            with self.subTest(url=url), self.assertRaises(ValueError): R.endpoint_parts(url)

    def test_complete_nonstream_all44_exact_payloads_and_single_claim(self):
        server = self.server(); args = self.args(server.endpoint); result = R.run(args)
        self.assertEqual(result['attempted'], 44); self.assertIsNone(result['stopped'])
        requests = H.rows(self.freeze/'requests.jsonl')
        for request, payload in zip(requests, server.posts):
            self.assertEqual(payload, {'model': 'test-native', 'messages': request['messages'], 'max_tokens': 128,
                'temperature': 0, 'stream': False, 'chat_template_kwargs': {'enable_thinking': False}})
            self.assertNotIn('fixturegold', json.dumps(payload))
        responses = H.rows(Path(args.output)/'responses.jsonl')
        self.assertEqual(len(responses), 44); self.assertTrue(all(x['error'] is None for x in responses))
        args.output = str(self.root/'another-output')
        with self.assertRaises(FileExistsError): R.run(args)
        self.assertEqual(len(server.posts), 44)

    def test_stream_all44(self):
        server = self.server('stream'); result = R.run(self.args(server.endpoint, True))
        self.assertEqual(result['attempted'], 44); self.assertIsNone(result['stopped'])

    def test_partial_stream_stops_and_keeps_all_rows(self):
        server = self.server('partial'); args = self.args(server.endpoint, True); result = R.run(args)
        self.assertEqual(len(server.posts), 1); self.assertEqual(result['attempted'], 1)
        rows = H.rows(Path(args.output)/'responses.jsonl'); self.assertEqual(len(rows), 44)
        self.assertEqual(rows[0]['answer'], 'synthetic answer'); self.assertEqual(rows[0]['error'], 'incomplete_stream')
        self.assertTrue(all(x['error'].startswith('unattempted_after:') for x in rows[1:]))

    def test_busy_before_post_blocks_every_case(self):
        server = self.server(); server.busy = True; result = R.run(self.args(server.endpoint))
        self.assertEqual(server.posts, []); self.assertEqual(result['attempted'], 0)

    def test_redirect_not_followed_and_no_retry(self):
        server = self.server('redirect'); result = R.run(self.args(server.endpoint))
        self.assertEqual(result['attempted'], 1); self.assertEqual(len(server.posts), 1); self.assertEqual(result['stopped'], 'http_status_307')

    def test_oversize_http_response_bounded(self):
        server = self.server('oversize'); result = R.run(self.args(server.endpoint))
        self.assertEqual(result['stopped'], 'response_body_limit'); self.assertEqual(len(server.posts), 1)

    def test_http_timeout_bounded_without_retry(self):
        server = self.server('timeout')
        with self.assertRaisesRegex(R.TransportError, 'deadline_exceeded'):
            R.exchange(server.endpoint, '/v1/chat/completions', {}, time.monotonic()+0.05)
        self.assertEqual(len(server.posts), 1)

    def test_slow_trickle_is_bounded_by_absolute_deadline(self):
        server = self.server('trickle'); started = time.monotonic()
        with self.assertRaisesRegex(R.TransportError, 'deadline_exceeded'):
            R.exchange(server.endpoint, '/v1/chat/completions', {}, time.monotonic()+0.1)
        self.assertLess(time.monotonic()-started, 0.5); self.assertEqual(len(server.posts), 1)

    def test_timeout_in_run_preserves_failed_and_unattempted_rows(self):
        args = self.args()
        def fake(endpoint, path, payload, deadline):
            if payload is None: return 'application/json', H.canonical([{'is_processing': False, 'n_ctx': 2048}])
            raise R.TransportError('deadline_exceeded', sse(finish=False, done=False))
        with patch.object(R, 'exchange', side_effect=fake) as exchange:
            args.stream = True; result = R.run(args); self.assertEqual(exchange.call_count, 2)
        rows = H.rows(Path(args.output)/'responses.jsonl')
        self.assertEqual(rows[0]['error'], 'deadline_exceeded'); self.assertEqual(rows[0]['answer'], 'synthetic answer')
        self.assertEqual(len(rows), 44); self.assertTrue(result['requires_live_owner_idle_check'])

    def test_usage_overrun_and_missing_finish_rejected(self):
        for field in ('usage', 'finish'):
            data = complete()
            if field == 'usage': data['usage']['completion_tokens'] = 129
            else: data['choices'][0]['finish_reason'] = None
            with self.subTest(field=field), self.assertRaises(ValueError): R.decode_answer(H.canonical(data), 'application/json', 'test-native', False)

    def test_unknown_model_tool_output_and_excess_answer_rejected(self):
        for variant in ('model', 'tool', 'size'):
            data = complete()
            if variant == 'model': data['model'] = 'other-model'
            if variant == 'tool': data['choices'][0]['message']['tool_calls'] = []
            if variant == 'size': data['choices'][0]['message']['content'] = 'x'*(R.MAX_ANSWER+1)
            with self.subTest(variant=variant), self.assertRaises(ValueError): R.decode_answer(H.canonical(data), 'application/json', 'test-native', False)

    def test_stream_requires_done_and_finish_and_usage(self):
        for raw in (sse(done=False), sse(finish=False), sse().replace(b'"completion_tokens":3', b'"completion_tokens":129')):
            with self.subTest(raw=raw), self.assertRaises(ValueError): R.decode_answer(raw, 'text/event-stream', 'test-native', True)

    def test_late_token_after_finish_refused(self):
        raw = sse().replace(b'data: [DONE]\n\n', sse(finish=False))
        with self.assertRaises(ValueError): R.decode_answer(raw, 'text/event-stream', 'test-native', True)

    def test_malformed_slots_retains_all44_failures(self):
        args = self.args()
        with patch.object(R, 'exchange', return_value=('application/json', b'[null]')) as exchange:
            result = R.run(args)
        self.assertEqual(exchange.call_count, 1); self.assertEqual(result['attempted'], 0)
        rows = H.rows(Path(args.output)/'responses.jsonl')
        self.assertEqual(len(rows), 44); self.assertTrue(all(row['error'] for row in rows))

    def test_malformed_completions_have_bounded_failure_rows(self):
        args = self.args()
        with patch.object(R, 'exchange', side_effect=[
                ('application/json', H.canonical([{'is_processing': False, 'n_ctx': 2048}])),
                ('application/json', b'[]')]):
            result = R.run(args)
        self.assertEqual(result['attempted'], 1)
        rows = H.rows(Path(args.output)/'responses.jsonl')
        self.assertEqual(len(rows), 44); self.assertEqual(rows[0]['error'], 'response_model_mismatch')

    def test_malformed_nested_shapes_rejected(self):
        for data in (None, [], 3, {'model': 'test-native', 'choices': [None]},
                {'model': 'test-native', 'choices': {'0': None}},
                {'model': 'test-native', 'choices': [{'index': 0, 'finish_reason': 'stop', 'message': None}]}):
            with self.subTest(data=data), self.assertRaises((ValueError, KeyError, TypeError)):
                R.decode_answer(H.canonical(data), 'application/json', 'test-native', False)
            raw = b'data: ' + H.canonical(data).strip() + b'\n\n'
            with self.subTest(stream=data), self.assertRaises((ValueError, KeyError, TypeError)):
                R.decode_answer(raw, 'text/event-stream', 'test-native', True)

    def test_generation_source_contract_cannot_be_arbitrary(self):
        args = self.args(); self.pipeline['server_sha256'] = '0'*64
        self.pipeline_path.write_bytes(H.canonical(self.pipeline))
        candidate = copy.deepcopy(self.receipt['candidate']); candidate['pipeline_sha256'] = H.digest(self.pipeline_path)
        self.candidate_path.write_bytes(H.canonical(candidate)); receipt = H.register(self.freeze, self.candidate_path, self.ledger)
        args.candidate_sha256 = receipt['candidate_sha256']
        with self.assertRaisesRegex(ValueError, 'settings_sources'): self.verify(args)


if __name__ == '__main__': unittest.main()
