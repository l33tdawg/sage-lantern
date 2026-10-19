"""Transport tests: first event must reach the client before upstream EOF."""
from contextlib import contextmanager
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[2] / "software/npu/rkllm_server.py"
SPEC = importlib.util.spec_from_file_location("rkllm_proxy_test", SOURCE)
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


def handler():
    value = SERVER.Handler.__new__(SERVER.Handler)
    value.fallback_url = "http://127.0.0.1:8082"
    value.wfile = io.BytesIO()
    value.send_response = Mock()
    value.send_header = Mock()
    value.end_headers = Mock()
    value.close_connection = False
    return value


@contextmanager
def response(raw, content_type="text/event-stream; charset=utf-8"):
    upstream = io.BytesIO(raw)
    upstream.headers = {"Content-Type": content_type}
    upstream.status = 200
    yield upstream


class ProxyTests(unittest.TestCase):
    def test_first_frame_forwarded_before_upstream_reads_next_frame(self):
        h = handler()
        first = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        tail = 'data: {"text":"مرحبا, apa khabar?"}\n\ndata: [DONE]\n\n'.encode()
        class Incremental(io.BytesIO):
            headers = {"Content-Type": "text/event-stream"}
            status = 200
            def read(self, *args):
                raise AssertionError("stream must not use a blocking full-body read")
            def readline(self, size):
                if self.tell() == len(first):
                    self_outer.assertEqual(h.wfile.getvalue(), first)
                return super().readline(size)
        self_outer = self
        with patch.object(SERVER.urllib.request, "urlopen", return_value=Incremental(first + tail)):
            h._proxy(b'{}', stream=True)
        self.assertEqual(h.wfile.getvalue(), first + tail)
        self.assertTrue(h.close_connection)
        self.assertNotIn("Content-Length", [c.args[0] for c in h.send_header.call_args_list])

    def test_nonstream_response_stays_identical(self):
        h = handler()
        raw = '{"answer":"مرحبا"}'.encode()
        with patch.object(SERVER.urllib.request, "urlopen", return_value=response(raw, "application/json")):
            h._proxy(b'{}')
        self.assertEqual(h.wfile.getvalue(), raw)
        h.send_header.assert_any_call("Content-Length", str(len(raw)))

    def test_wrong_content_type_rejected_before_headers(self):
        h = handler()
        with patch.object(SERVER.urllib.request, "urlopen", return_value=response(b'{}', "application/json")):
            with self.assertRaisesRegex(SERVER.EngineError, "event stream"):
                h._proxy(b'{}', stream=True)
        h.send_response.assert_not_called()

    def test_interrupted_stream_does_not_invent_done_or_append_json(self):
        h = handler()
        first = b'data: {"delta":"partial"}\n\n'
        class Interrupted(io.BytesIO):
            headers = {"Content-Type": "text/event-stream"}
            status = 200
            def readline(self, size):
                if self.tell() == len(first):
                    raise OSError("fixture failure")
                return super().readline(size)
        with patch.object(SERVER.urllib.request, "urlopen", return_value=Interrupted(first)), patch.object(SERVER.sys, "stderr", io.StringIO()):
            h._proxy(b'{}', stream=True)
        self.assertEqual(h.wfile.getvalue(), first)
        h.send_response.assert_called_once_with(200)

    def test_oversized_line_stops_before_forwarding_it(self):
        h = handler()
        with patch.object(SERVER.urllib.request, "urlopen", return_value=response(b'x' * 33)), patch.object(SERVER, "MAX_PROXY_LINE_BYTES", 32), patch.object(SERVER.sys, "stderr", io.StringIO()):
            h._proxy(b'{}', stream=True)
        self.assertEqual(h.wfile.getvalue(), b'')

    def test_total_transport_limit_and_nonstream_limit(self):
        h = handler()
        with patch.object(SERVER.urllib.request, "urlopen", return_value=response(b'data: 1\n\ndata: 2\n\n')), patch.object(SERVER, "MAX_PROXY_RESPONSE_BYTES", 10), patch.object(SERVER.sys, "stderr", io.StringIO()):
            h._proxy(b'{}', stream=True)
        self.assertEqual(h.wfile.getvalue(), b'data: 1\n\n')
        h = handler()
        with patch.object(SERVER.urllib.request, "urlopen", return_value=response(b'x' * 33, "application/json")), patch.object(SERVER, "MAX_PROXY_RESPONSE_BYTES", 32):
            with self.assertRaisesRegex(SERVER.EngineError, "transport limit"):
                h._proxy(b'{}')
        h.send_response.assert_not_called()

    def test_client_disconnect_closes_upstream_without_retry(self):
        h = handler()
        h.wfile = Mock()
        h.wfile.write.side_effect = BrokenPipeError()
        upstream = io.BytesIO(b'data: first\n\ndata: [DONE]\n\n')
        upstream.headers = {"Content-Type": "text/event-stream"}
        upstream.status = 200
        with patch.object(SERVER.urllib.request, "urlopen", return_value=upstream) as open_call, patch.object(SERVER.sys, "stderr", io.StringIO()):
            h._proxy(b'{}', stream=True)
        open_call.assert_called_once()
        self.assertTrue(upstream.closed)

    def test_photo_dispatch_keeps_request_body_and_selects_stream(self):
        h = handler()
        body = {"stream": True, "max_tokens": 128, "messages": [{"role": "user", "content": [{"type": "text", "text": "question"}]}]}
        raw = json.dumps(body).encode()
        h.path = "/v1/chat/completions"
        h.headers = {"Content-Length": str(len(raw))}
        h.rfile = io.BytesIO(raw)
        h._proxy = Mock()
        h.do_POST()
        args, kwargs = h._proxy.call_args
        self.assertEqual(json.loads(args[0]), body)
        self.assertEqual(kwargs, {"stream": True})


if __name__ == '__main__':
    unittest.main()
