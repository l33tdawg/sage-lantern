import base64
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock


SCRIPT = Path(__file__).resolve().parents[2] / "software/npu/vlm_server.py"
SPEC = importlib.util.spec_from_file_location("vlm_server", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

PNG = b"\x89PNG\r\n\x1a\nfixture"
DATA_URI = "data:image/png;base64," + base64.b64encode(PNG).decode()


def message(role, content):
    return {"role": role, "content": content}


class StubEngine:
    def __init__(self):
        self.busy = False
        self.ready = {"max_context": 4096}
        self.calls = []

    def run(self, prompt, max_new_tokens, image_path=None):
        self.calls.append(dict(prompt=prompt, max_new_tokens=max_new_tokens,
                               image_path=image_path))
        return dict(ok=True, text="a described photo", vision_ms=123.4,
                    prefill_tokens=10, generate_tokens=5,
                    prefill_ms=100.0, generate_ms=200.0)


class RenderTests(unittest.TestCase):
    def test_system_policy_and_image_marker_position(self):
        prompt, image = MODULE.render_chat([
            message("system", "policy text"),
            message("user", [{"type": "text", "text": "what is this?"},
                             {"type": "image_url", "image_url": {"url": DATA_URI}}]),
        ])
        self.assertEqual(image, (PNG, ".png"))
        self.assertTrue(prompt.startswith("<|im_start|>system\npolicy text<|im_end|>\n"))
        self.assertIn("what is this?<image><|im_end|>", prompt)
        self.assertTrue(prompt.endswith(MODULE.ASSISTANT_PREFIX))

    def test_image_part_before_text_keeps_source_order(self):
        prompt, image = MODULE.render_chat([
            message("user", [{"type": "image_url", "image_url": {"url": DATA_URI}},
                             {"type": "text", "text": "desc"}])])
        self.assertEqual(image, (PNG, ".png"))
        self.assertIn("<image>desc<|im_end|>", prompt)

    def test_text_only_request_has_no_image(self):
        prompt, image = MODULE.render_chat([message("user", "hello")])
        self.assertIsNone(image)
        self.assertNotIn("<image>", prompt)

    def test_second_image_part_is_refused(self):
        with self.assertRaisesRegex(ValueError, "one image per request"):
            MODULE.render_chat([message("user", [
                {"type": "image_url", "image_url": {"url": DATA_URI}},
                {"type": "image_url", "image_url": {"url": DATA_URI}}])])

    def test_non_data_uri_is_refused(self):
        with self.assertRaisesRegex(ValueError, "data: URIs"):
            MODULE.render_chat([message("user", [
                {"type": "image_url", "image_url": {"url": "http://example/photo.png"}}])])

    def test_unsupported_role_and_empty_content_are_refused(self):
        with self.assertRaisesRegex(ValueError, "unsupported role"):
            MODULE.render_chat([message("tool", "x")])
        with self.assertRaisesRegex(ValueError, "empty"):
            MODULE.render_chat([message("user", [{"type": "text", "text": ""}])])

    def test_image_content_in_non_user_role_is_refused(self):
        with self.assertRaisesRegex(ValueError, "only user messages"):
            MODULE.render_chat([message("assistant", [
                {"type": "image_url", "image_url": {"url": DATA_URI}}])])


class PayloadTests(unittest.TestCase):
    def test_completion_payload_shape(self):
        payload = MODULE.completion_payload("model-x", "answer", dict(
            vision_ms=12.5, prefill_tokens=4, generate_tokens=2,
            prefill_ms=40.0, generate_ms=80.0), 1234)
        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["choices"][0]["message"]["content"], "answer")
        self.assertEqual(payload["usage"], {"prompt_tokens": 4, "completion_tokens": 2,
                                            "total_tokens": 6})
        self.assertEqual(payload["timings"]["vision_ms"], 12.5)
        self.assertEqual(payload["timings"]["predicted_per_token_ms"], 40.0)


class HandlerTests(unittest.TestCase):
    def request(self, body, engine):
        handler = MODULE.Handler.__new__(MODULE.Handler)
        raw = json.dumps(body).encode()
        handler.path = "/v1/chat/completions"
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.close_connection = False
        MODULE.Handler.engine = engine
        MODULE.Handler.model_name = "lantern-test"
        handler.do_POST()
        status = handler.send_response.call_args[0][0]
        return status, handler.wfile.getvalue()

    def test_photo_request_reaches_engine_and_temp_file_is_removed(self):
        engine = StubEngine()
        status, raw = self.request({"max_tokens": 32, "messages": [
            message("system", "policy"),
            message("user", [{"type": "text", "text": "q"},
                             {"type": "image_url", "image_url": {"url": DATA_URI}}])]},
            engine)
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertEqual(payload["choices"][0]["message"]["content"], "a described photo")
        call = engine.calls[0]
        self.assertEqual(call["max_new_tokens"], 32)
        self.assertIn("<image>", call["prompt"])
        self.assertTrue(call["image_path"].endswith(".png"))
        self.assertFalse(Path(call["image_path"]).exists())

    def test_text_request_runs_without_image(self):
        engine = StubEngine()
        status, _ = self.request({"messages": [message("user", "hello")]}, engine)
        self.assertEqual(status, 200)
        self.assertIsNone(engine.calls[0]["image_path"])

    def test_bad_image_request_is_rejected_before_the_engine(self):
        engine = StubEngine()
        status, raw = self.request({"messages": [message("user", [
            {"type": "image_url", "image_url": {"url": "data:image/gif;base64,AAAA"}}])]},
            engine)
        self.assertEqual(status, 400)
        self.assertIn("unsupported image type", json.loads(raw)["error"]["message"])
        self.assertEqual(engine.calls, [])


if __name__ == "__main__":
    unittest.main()
