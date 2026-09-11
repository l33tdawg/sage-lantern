import base64
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("photo_history_integration", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PhotoHistoryIntegrationTests(unittest.TestCase):
    def test_socket_retrieval_restart_opt_out_and_delete(self):
        frame = b"\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        scan = b"\x01\x01\x00\x00\x3f\x00"
        original = b"\xff\xd8\xff\xc0\x00\x0b" + frame + b"\xff\xda\x00\x08" + scan + b"synthetic\xff\xd9"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve() / "history"
            history = MODULE.LocalHistory(directory)
            session_id = str(uuid.uuid4())
            history.record(history.consent(), session_id, {"transcript": "Synthetic test", "response": "Synthetic answer"}, original=original)
            reopened = MODULE.LocalHistory(directory)
            server = MODULE.ThreadingHTTPServer(("127.0.0.1", 0), MODULE.VoiceHandler)
            server.control = MODULE.VoiceControl(30)
            server.history = reopened
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def request(action, body, origin="http://127.0.0.1:3100"):
                    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                    try:
                        connection.request("POST", action, body=json.dumps(body), headers={
                            "Host": "127.0.0.1:3197", "Origin": origin,
                            "Content-Type": "application/json", "X-Lantern-Voice": "control",
                        })
                        response = connection.getresponse()
                        self.assertEqual(response.getheader("Cache-Control"), "no-store")
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()

                status, photo = request("/history/photo", {"session_id": session_id})
                self.assertEqual(status, 200)
                self.assertEqual(base64.b64decode(photo["image_base64"]), original)
                self.assertEqual(request("/history/photo", {"session_id": session_id}, "http://untrusted.invalid")[0], 403)
                self.assertEqual(request("/history/settings", {"enabled": False})[0], 200)
                self.assertEqual(request("/history/photo", {"session_id": session_id})[0], 200)
                self.assertEqual(request("/history/delete", {"session_id": session_id})[0], 200)
                self.assertEqual(request("/history/photo", {"session_id": session_id})[0], 404)
                self.assertFalse((directory / "photos" / f"{session_id}.media").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
