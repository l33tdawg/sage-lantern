#!/usr/bin/env python3
"""One-shot synthetic ANSWER inference via an explicitly coordinated loopback tunnel.

No SSH, retries, model selection, scoring, or training. See work/ab-quality-20260927/RUNNER.md.
"""
import argparse
import datetime as dt
import http.client
import importlib.util
import ipaddress
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import urlsplit

_spec = importlib.util.spec_from_file_location('frozen_answer_evaluator', Path(__file__).with_name('answer_holdout.py'))
eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval)
require, canonical, digest, document = eval.require, eval.canonical, eval.digest, eval.document
MANIFEST = '384c6da92c517018e4cec0c29c9a5b12559d7397b6913aa43c7f8adb3fc1ca66'
REQUESTS = '3e211066577ab7254c5f87e2b93e02b9d2948ac73b34406ca485d1bcf8bcfd1a'
ENGINE_SOURCE = '408eb358080dca83e533fb8e4b819c2792242e443fb0e6227ee3bf5e6a8e3cfc'
SERVER_SOURCE = '2d18fa2e17b3e9b3d70c3a38c93a28fbe34648297c3875e2df900e2dd144c968'
CONTRACT = 'rkllm-native5-pinned-engine-v1'
GENERATION = {'temperature': 0, 'max_tokens': 128, 'thinking': False}
MAX_BODY = 262144
MAX_ANSWER = 32768


def hash_value(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'invalid_digest')


def timestamp(value):
    require(type(value) is str, 'invalid_timestamp')
    result = dt.datetime.fromisoformat(value)
    require(result.tzinfo is not None, 'timestamp_timezone_required')
    return result.timestamp()


def endpoint_parts(endpoint):
    p = urlsplit(endpoint)
    require(p.scheme == 'http' and p.username is None and p.password is None and not p.query
            and not p.fragment and p.path in ('', '/') and p.port is not None, 'loopback_http_origin_required')
    require(p.hostname in ('127.0.0.1', '::1') and ipaddress.ip_address(p.hostname).is_loopback,
            'numeric_loopback_required')
    return p.hostname, p.port


def verify(root, ledger, candidate_id, pipeline_path, window_path, window_sha256, endpoint):
    root, ledger = Path(root), Path(ledger)
    hash_value(candidate_id); hash_value(window_sha256)
    require(digest(root / 'manifest.json') == MANIFEST, 'wrong_frozen_manifest')
    manifest, cases, requests = eval.verified_freeze(root)
    require(manifest['files']['requests.jsonl'] == REQUESTS and len(requests) == 44, 'wrong_frozen_requests')
    policy = (root / 'policy.txt').read_text()
    expected = []
    for case in cases:
        if case['dependency'] is None:
            messages = eval.render(case, policy)
            expected.append({'id': case['id'], 'messages': messages, 'request_sha256': eval.sha(canonical(messages))})
    require(requests == expected, 'request_render_or_hash_mismatch')
    registration_path = ledger / (candidate_id + '.registration.json')
    receipt = document(registration_path.read_bytes())
    candidate = receipt['candidate']
    require(set(candidate) == {'model_sha256', 'pipeline_sha256', 'generation', 'selected_without_holdout', 'heldout_used_for_training'}, 'candidate_fields')
    require(receipt['schema'] == 'sage.lantern.holdout-candidate.v1' and receipt['candidate_sha256'] == candidate_id
            and eval.sha(canonical(candidate)) == candidate_id and receipt['manifest_sha256'] == MANIFEST
            and receipt['requests_sha256'] == REQUESTS and receipt['score_once'] is True, 'registration_mismatch')
    require(candidate['generation'] == GENERATION and candidate['selected_without_holdout'] is True
            and candidate['heldout_used_for_training'] is False, 'candidate_generation_or_isolation_mismatch')
    for field in ('model_sha256', 'pipeline_sha256'): hash_value(candidate[field])
    require(not (ledger / (candidate_id + '.score-claimed.json')).exists(), 'candidate_already_scored')
    pipeline_raw = Path(pipeline_path).read_bytes()
    require(eval.sha(pipeline_raw) == candidate['pipeline_sha256'], 'pipeline_pin_mismatch')
    pipeline = document(pipeline_raw)
    require(set(pipeline) == {'schema', 'contract', 'model_sha256', 'model_name', 'engine_source_sha256',
        'engine_binary_sha256', 'server_sha256', 'librkllmrt_sha256', 'process_launch_sha256',
        'python_runtime_sha256', 'context_tokens'}, 'pipeline_fields')
    require(pipeline['schema'] == 'sage.lantern.inference-pipeline.v1' and pipeline['contract'] == CONTRACT,
            'unsupported_endpoint_settings_contract')
    require(pipeline['engine_source_sha256'] == ENGINE_SOURCE and pipeline['server_sha256'] == SERVER_SOURCE,
            'unsupported_endpoint_settings_sources')
    require(pipeline['model_sha256'] == candidate['model_sha256'], 'pipeline_model_mismatch')
    for field in pipeline:
        if field.endswith('_sha256'): hash_value(pipeline[field])
    require(type(pipeline['model_name']) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', pipeline['model_name']), 'invalid_model_name')
    require(type(pipeline['context_tokens']) is int and 512 <= pipeline['context_tokens'] <= 32768, 'invalid_context_tokens')
    window_raw = Path(window_path).read_bytes()
    require(eval.sha(window_raw) == window_sha256, 'window_pin_mismatch')
    window = document(window_raw)
    require(set(window) == {'schema', 'owner', 'candidate_sha256', 'pipeline_sha256', 'endpoint', 'observed_at',
        'expires_at', 'exclusive_idle_window', 'live_artifacts_match_pipeline', 'engine_build_matches_source',
        'model_selected_without_holdout', 'service_pid', 'engine_pid', 'observation_sha256'}, 'window_fields')
    require(window['schema'] == 'sage.lantern.inference-window.v1' and window['owner'] == 'backlog_audit', 'live_owner_required')
    require(window['candidate_sha256'] == candidate_id and window['pipeline_sha256'] == candidate['pipeline_sha256']
            and window['endpoint'] == endpoint, 'window_binding_mismatch')
    for field in ('exclusive_idle_window', 'live_artifacts_match_pipeline', 'engine_build_matches_source', 'model_selected_without_holdout'):
        require(window[field] is True, 'live_owner_attestation_required:' + field)
    for field in ('service_pid', 'engine_pid'): require(type(window[field]) is int and window[field] > 0, 'process_identity_required')
    hash_value(window['observation_sha256'])
    endpoint_parts(endpoint)
    now = time.time(); observed = timestamp(window['observed_at']); expires = timestamp(window['expires_at'])
    require(observed <= now < expires and now - observed <= 900 and 0 < expires - observed <= 5400,
            'window_stale_expired_or_too_long')
    require(timestamp(receipt['registered_at']) <= observed, 'registration_must_precede_window')
    return requests, pipeline, window, receipt


class TransportError(Exception):
    def __init__(self, code, raw=b''):
        super().__init__(code)
        self.raw = raw


def exchange(endpoint, path, payload, deadline):
    """No proxies/redirects/retries; timer bounds headers and slow body as well as idle IO."""
    host, port = endpoint_parts(endpoint)
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise TransportError('deadline_exceeded')
    connection = http.client.HTTPConnection(host, port, timeout=remaining)
    raw = bytearray(); response = None
    def abort():
        sock = connection.sock
        if sock is not None:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        connection.close()
    timer = threading.Timer(remaining, abort); timer.daemon = True; timer.start()
    try:
        body = None if payload is None else canonical(payload)
        connection.request('GET' if body is None else 'POST', path, body=body,
                           headers={'Content-Type': 'application/json', 'Connection': 'close'})
        response = connection.getresponse()
        # HTTPConnection may detach a Connection:close socket; retain it for absolute timeout.
        response_socket = getattr(getattr(response.fp, 'raw', None), '_sock', None)
        def abort_response():
            if response_socket is not None:
                try: response_socket.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        timer.cancel()
        timer = threading.Timer(max(0, deadline - time.monotonic()), abort_response)
        timer.daemon = True; timer.start()
        content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        while True:
            if time.monotonic() >= deadline: raise TransportError('deadline_exceeded', bytes(raw))
            piece = response.read1(min(4096, MAX_BODY + 1 - len(raw)))
            if not piece: break
            raw.extend(piece)
            if len(raw) > MAX_BODY: raise TransportError('response_body_limit', bytes(raw[:MAX_BODY]))
        if time.monotonic() >= deadline: raise TransportError('deadline_exceeded', bytes(raw))
        if response.length not in (None, 0): raise TransportError('truncated_http_body', bytes(raw))
        if response.status != 200: raise TransportError('http_status_' + str(response.status), bytes(raw))
        return content_type, bytes(raw)
    except TransportError: raise
    except (OSError, http.client.HTTPException) as exc:
        code = 'deadline_exceeded' if time.monotonic() >= deadline else 'transport_' + type(exc).__name__
        raise TransportError(code, bytes(raw)) from exc
    finally:
        timer.cancel()
        if response is not None: response.close()
        connection.close()


def usage_ok(usage):
    require(type(usage) is dict and type(usage.get('completion_tokens')) is int
            and 0 <= usage['completion_tokens'] <= 128, 'missing_or_excess_completion_usage')


def decode_answer(raw, content_type, model, stream, partial=False):
    text = ''; finished = False; done = False; usage = None
    try:
        if not stream:
            require(content_type == 'application/json', 'unexpected_content_type')
            data = document(raw); require(type(data) is dict and data.get('model') == model, 'response_model_mismatch')
            choices = data['choices']; require(type(choices) is list and len(choices) == 1 and type(choices[0]) is dict and choices[0]['index'] == 0, 'response_choices')
            choice = choices[0]; require(choice['finish_reason'] in ('stop', 'length'), 'missing_finish')
            require(type(choice['message']) is dict and set(choice['message']) == {'role', 'content'} and choice['message']['role'] == 'assistant', 'unexpected_message')
            text = choice['message']['content']; require(type(text) is str, 'invalid_answer_text')
            usage_ok(data.get('usage'))
        else:
            require(content_type == 'text/event-stream', 'unexpected_content_type')
            events = raw.decode('utf-8').replace('\r\n', '\n').split('\n\n')
            require(len(events) <= 2048, 'event_limit')
            for event in events:
                if not event: continue
                require(not done and event.startswith('data: ') and '\n' not in event, 'invalid_sse_event')
                value = event[6:]
                if value == '[DONE]': done = True; continue
                data = document(value); require(type(data) is dict and data.get('model') == model and 'error' not in data, 'response_model_or_error')
                choices = data['choices']; require(type(choices) is list, 'response_choices')
                if not choices:
                    require(finished and data.get('usage') == usage, 'unexpected_usage_event'); continue
                require(len(choices) == 1 and type(choices[0]) is dict and choices[0]['index'] == 0 and not finished, 'response_choices')
                choice = choices[0]; delta = choice['delta']
                require(type(delta) is dict and set(delta) <= {'role', 'content'}
                        and delta.get('role', 'assistant') == 'assistant', 'unexpected_delta')
                piece = delta.get('content', ''); require(type(piece) is str, 'invalid_answer_text')
                require(len((text + piece).encode()) <= MAX_ANSWER, 'answer_limit'); text += piece
                reason = choice.get('finish_reason')
                require(reason in (None, 'stop', 'length'), 'unexpected_finish')
                if reason is not None:
                    finished = True; usage = data.get('usage'); usage_ok(usage)
            require(done and finished, 'incomplete_stream')
        require(len(text.encode()) <= MAX_ANSWER, 'answer_limit')
        return text
    except (ValueError, KeyError, TypeError, IndexError):
        if partial: return text if type(text) is str and len(text.encode()) <= MAX_ANSWER else ''
        raise


def append_row(path, value):
    import os
    with Path(path).open('ab') as stream:
        stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())


def run(args):
    requests, pipeline, window, receipt = verify(args.freeze, args.ledger, args.candidate_sha256,
        args.pipeline, args.window, args.window_sha256, args.endpoint)
    require(1 <= args.timeout <= 300, 'timeout_must_be_1_to_300_seconds')
    require(1 <= args.total_timeout <= 3600, 'total_timeout_must_be_1_to_3600_seconds')
    output = Path(args.output); require(not output.exists(), 'output_already_exists')
    claim = {'schema': 'sage.lantern.holdout-inference-claim.v1', 'candidate_sha256': args.candidate_sha256,
        'manifest_sha256': MANIFEST, 'requests_sha256': REQUESTS, 'runner_sha256': digest(__file__),
        'window_sha256': args.window_sha256, 'pipeline_sha256': receipt['candidate']['pipeline_sha256'],
        'stream': args.stream, 'per_request_timeout': args.timeout, 'total_timeout': args.total_timeout,
        'started_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'output': str(output.resolve()),
        'provenance_is_owner_attested_not_remote_attestation': True}
    eval.new_file(Path(args.ledger) / (args.candidate_sha256 + '.inference-claimed.json'), canonical(claim))
    output.mkdir(parents=True)
    for name, value in (('claim.json', claim), ('pipeline.json', pipeline), ('window.json', window), ('registration.json', receipt)):
        eval.new_file(output / name, canonical(value))
    overall = time.monotonic() + args.total_timeout; stopped = None; attempted_count = 0
    for request in requests:
        answer = ''; error = None; raw = b''; attempted = False
        if stopped:
            error = 'unattempted_after:' + stopped
        else:
            try:
                deadline = min(overall, time.monotonic() + args.timeout,
                               time.monotonic() + timestamp(window['expires_at']) - time.time())
                require(deadline > time.monotonic(), 'window_or_total_deadline_expired')
                # A GET is not provenance: it only checks availability/context and immediate idle state.
                kind, body = exchange(args.endpoint, '/slots', None, deadline)
                slots = document(body)
                require(kind == 'application/json' and type(slots) is list and len(slots) == 1
                        and type(slots[0]) is dict and slots[0].get('is_processing') is False
                        and slots[0].get('n_ctx') == pipeline['context_tokens'], 'endpoint_not_idle_or_context_mismatch')
                payload = {'model': pipeline['model_name'], 'messages': request['messages'],
                           'max_tokens': 128, 'temperature': 0, 'stream': args.stream,
                           'chat_template_kwargs': {'enable_thinking': False}}
                append_row(output / 'attempts.jsonl', {'id': request['id'], 'request_sha256': request['request_sha256'],
                    'payload_sha256': eval.sha(canonical(payload)), 'event': 'post_intent',
                    'at': dt.datetime.now(dt.timezone.utc).isoformat()})
                attempted = True; attempted_count += 1
                kind, raw = exchange(args.endpoint, '/v1/chat/completions', payload, deadline)
                answer = decode_answer(raw, kind, pipeline['model_name'], args.stream)
            except (TransportError, ValueError, KeyError, TypeError, IndexError, KeyboardInterrupt) as exc:
                if isinstance(exc, TransportError): raw = exc.raw
                error = str(exc) if isinstance(exc, (ValueError, TransportError)) else type(exc).__name__
                error = error[:200] or type(exc).__name__
                if raw:
                    answer = decode_answer(raw, 'text/event-stream' if args.stream else 'application/json',
                                           pipeline['model_name'], args.stream, partial=True)
                stopped = error
        if attempted:
            eval.new_file(output / (request['id'] + '.response-body'), raw)
        append_row(output / 'responses.jsonl', {'id': request['id'], 'answer': answer, 'error': error,
            'candidate_sha256': args.candidate_sha256, 'request_sha256': request['request_sha256']})
        append_row(output / 'attempts.jsonl', {'id': request['id'], 'event': 'result', 'attempted': attempted, 'error': error})
    result = {'requests': len(requests), 'attempted': attempted_count, 'stopped': stopped,
        'responses_sha256': digest(output / 'responses.jsonl'), 'attempts_sha256': digest(output / 'attempts.jsonl'),
        'requires_live_owner_idle_check': stopped is not None,
        'no_automatic_resume_retry_or_score': True}
    eval.new_file(output / 'result.json', canonical(result)); return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('freeze', 'ledger', 'candidate-sha256', 'pipeline', 'window', 'window-sha256', 'endpoint', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--total-timeout', type=float, default=1800)
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()
    print(canonical(run(args)).decode(), end='')


if __name__ == '__main__': main()
