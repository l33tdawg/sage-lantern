import argparse
from email.message import Message
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import threading
import tempfile
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sage_lantern.community_service import CommunityError, CommunityService


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("voice_community_tests", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CommunityHTTPTests(unittest.TestCase):
    def setUp(self):
        policy = patch.object(MODULE, "lock_required", return_value=False)
        policy.start()
        self.addCleanup(policy.stop)
        self.authorized = True
        verifier = patch.object(MODULE, "token_authorized", side_effect=lambda token: self.authorized and token == "synthetic-session")
        verifier.start()
        self.addCleanup(verifier.stop)
        self.control = MODULE.VoiceControl(30)
        self.service = Mock()
        self.service.dispatch.return_value = {"items": [], "more": False}
        self.service.close.return_value = True
        self.control.community = self.service
        self.server = SimpleNamespace(control=self.control, draining=threading.Condition(),
                                      active_community_mutations=0, mutation_uncertain=threading.Event())

    def request(self, path="/community/status", payload=None, *, token="synthetic-session", origin="http://127.0.0.1:3100", raw=None):
        body = json.dumps({} if payload is None else payload).encode() if raw is None else raw
        handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
        handler.path = path
        handler.headers = Message()
        for key, value in {"Host": "127.0.0.1:3197", "Origin": origin,
                           "Content-Type": "application/json", "X-Lantern-Voice": "control",
                           "X-Lantern-Session": token, "Content-Length": str(len(body))}.items():
            handler.headers[key] = value
        handler.server = self.server
        handler.rfile = io.BytesIO(body)
        handler.respond = Mock()
        handler.do_POST()
        return handler.respond.call_args.args

    def test_auth_mandatory_even_with_lock_marker_off(self):
        self.assertEqual(self.request(token="")[0], 403)
        self.assertEqual(self.request(token="wrong")[0], 403)
        self.service.dispatch.assert_not_called()

    def test_radio_status_uses_existing_auth_without_mutation(self):
        self.assertEqual(self.request('/community/radio/status', token='wrong')[0], 403)
        self.service.dispatch.assert_not_called()
        self.assertEqual(self.request('/community/radio/status')[0], 200)
        self.assertEqual(self.server.active_community_mutations, 0)
        self.assertFalse(self.server.mutation_uncertain.is_set())

    def test_radio_status_lock_during_dispatch_suppresses_response(self):
        def revoked(*args, **kwargs):
            self.authorized = False
            return {'private': 'must not return'}
        self.service.dispatch.side_effect = revoked
        self.assertEqual(self.request('/community/radio/status')[0], 403)
        self.assertFalse(self.server.mutation_uncertain.is_set())

    def test_share_status_route_uses_existing_auth_gate(self):
        source = str(uuid.uuid4())
        self.assertEqual(self.request("/community/shares/status", {"source_ids": [source]}, token="wrong")[0], 403)
        self.service.dispatch.assert_not_called()
        self.service.dispatch.return_value = {"items": [{"source_id": source, "state": "unknown"}]}
        self.assertEqual(self.request("/community/shares/status", {"source_ids": [source]})[0], 200)

    def test_protected_history_source_exact_and_revocation_guarded(self):
        source = str(uuid.uuid4())
        self.control.community_access("synthetic-session")
        history = SimpleNamespace(protected_storage=True, items=Mock(return_value=[dict(
            session_id=source, transcript="Older question", response="Older answer", has_photo=True)]))
        self.control.protected_history = history
        self.assertEqual(self.control.community_source(source), dict(
            question="Older question", answer="Older answer", has_photo=True))
        def revoke():
            self.authorized = False
            return [dict(session_id=source, transcript="Older question", response="Older answer", has_photo=True)]
        history.items.side_effect = revoke
        with self.assertRaises(CommunityError):
            self.control.community_source(source)
        self.authorized = True
        history.protected_storage = False
        history.items.reset_mock()
        with self.assertRaises(CommunityError):
            self.control.community_source(source)
        history.items.assert_not_called()

    def test_strict_route_origin_and_body_limits(self):
        for path in ("/community/unknown", "/community/status?all=1", "/community/status/", "/v1/messages"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request(origin="http://untrusted.invalid")[0], 403)
        self.assertEqual(self.request(raw=b" " * 32769)[0], 400)
        self.assertEqual(self.request(raw=b'{"x":1,"x":2}')[0], 400)
        self.service.dispatch.assert_not_called()

    def test_default_service_advertises_no_messaging_or_sharing(self):
        self.control.community = CommunityService()
        code, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(body["schema"], "sage.lantern.community.v1")
        self.assertFalse(body["messaging"]["available"])
        self.assertFalse(body["sharing"]["available"])

    def test_voice_owned_dictation_readiness_independent_of_send_providers(self):
        loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, drain_timeout=0.1))
        self.server.control = loop.control
        self.assertFalse(self.request()[1]["dictation"]["available"])
        server, thread = Mock(), Mock()
        thread.is_alive.return_value = True
        server.shutdown.side_effect = lambda: setattr(thread.is_alive, "return_value", False)
        server.drain.return_value = True
        server.mutation_uncertain = threading.Event()
        loop.write_state = Mock()
        observations = []

        def step():
            code, body = self.request()
            self.assertEqual(code, 200)
            observations.append(body)
            loop.request_stop()
            self.assertFalse(loop.dictation_available())

        loop.step = step
        with patch.object(MODULE, "VoiceHTTPServer", return_value=server), patch.object(MODULE.threading, "Thread", return_value=thread):
            loop.run()
        self.assertEqual(len(observations), 1)
        self.assertTrue(observations[0]["dictation"]["available"])
        self.assertFalse(observations[0]["messaging"]["available"])
        self.assertFalse(observations[0]["sharing"]["available"])
        self.assertFalse(loop.dictation_available())

    def test_readiness_rejects_dead_controller_or_missing_worker(self):
        loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30))
        loop.serving = True
        loop.touch_ready = True
        loop.controller_thread = Mock()
        loop.controller_thread.is_alive.return_value = False
        self.assertFalse(loop.dictation_available())
        loop.controller_thread.is_alive.return_value = True
        self.assertTrue(loop.dictation_available())
        loop.message_transcribe = None
        self.assertFalse(loop.dictation_available())

    def source_loop(self, directory, *, photo=False):
        loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, state=Path(directory) / "state.json"))
        _, credentials = loop.control.start(str(uuid.uuid4()), device_token="synthetic-session")
        session_id = credentials["session_id"]
        loop.control.take(False)
        loop.control.command("stop", session_id, credentials["token"])
        loop.control.begin_processing(session_id)
        if photo:
            loop.control.owner["image_digest"] = "synthetic-photo-digest"
        loop.device_session = (session_id, "synthetic-session", True)
        loop.control.community_access("synthetic-session")
        return loop, session_id

    def test_completed_source_callback_has_exact_snapshot_and_photo_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            loop, source_id = self.source_loop(directory, photo=True)
            loop.write_state("processing", session_id=source_id, transcript="Question", response="partial")
            with self.assertRaises(CommunityError):
                loop.control.community_source(source_id)
            loop.write_state("complete", session_id=source_id, transcript="Question", response="Answer")
            snapshot = loop.control.community_source(source_id)
            self.assertEqual(snapshot, {"question": "Question", "answer": "Answer", "has_photo": True})
            snapshot["answer"] = "changed externally"
            self.assertEqual(loop.control.community_source(source_id)["answer"], "Answer")
            self.assertTrue(json.loads(loop.args.state.read_text())["has_photo"])
            with self.assertRaises(CommunityError):
                loop.control.community_source(str(uuid.uuid4()))
            self.assertEqual(loop.control.community._source(source_id)["answer"], "Answer")

    def test_source_cleared_by_new_capture_clear_lock_and_suspend(self):
        for operation in ("answer", "message", "physical", "clear", "lock", "suspend"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                loop, source_id = self.source_loop(directory)
                loop.write_state("complete", session_id=source_id, transcript="Question", response="Answer")
                loop.control.finish(source_id)
                if operation in {"answer", "message"}:
                    loop.control.start(str(uuid.uuid4()), device_token="synthetic-session", purpose=operation)
                elif operation == "physical":
                    loop.control.take(True)
                elif operation == "clear":
                    loop.control.clear()
                elif operation == "lock":
                    loop.control.revoke_device()
                else:
                    loop.control.quiesce()
                with self.assertRaises(CommunityError):
                    loop.control.community_source(source_id)

    def test_source_bounds_and_wrong_session_never_become_shareable(self):
        for question, answer in (("x" * 4097, "Answer"), ("Question", "x" * 12001), ("Question", "bad\x00text")):
            with tempfile.TemporaryDirectory() as directory:
                loop, source_id = self.source_loop(directory)
                loop.write_state("complete", session_id=source_id, transcript=question, response=answer)
                with self.assertRaises(CommunityError):
                    loop.control.community_source(source_id)
        with tempfile.TemporaryDirectory() as directory:
            loop, source_id = self.source_loop(directory)
            loop.write_state("complete", session_id=str(uuid.uuid4()), transcript="Question", response="Answer")
            with self.assertRaises(CommunityError):
                loop.control.community_source(source_id)
    def test_stable_hashed_session_and_epoch_fence_no_ptt_link(self):
        self.assertEqual(self.request()[0], 200)
        first = self.service.dispatch.call_args
        callback = first.args[2]
        self.assertTrue(callback())
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(first.kwargs["session_id"], self.service.dispatch.call_args.kwargs["session_id"])
        self.assertEqual(first.kwargs["session_id"], hashlib.sha256(b"synthetic-session").hexdigest())
        self.assertNotIn("synthetic-session", str(first))
        self.assertIsNone(self.control.device_token)
        self.control.revoke_device()
        self.assertFalse(callback())
        self.assertEqual(self.request()[0], 200)
        self.assertFalse(callback())

    def test_post_dispatch_revoke_never_returns_private_payload(self):
        def dispatch(*args, **kwargs):
            self.control.revoke_device()
            return {"text": "private payload"}
        self.service.dispatch.side_effect = dispatch
        code, body = self.request()
        self.assertEqual(code, 403)
        self.assertNotIn("private payload", str(body))

    def test_http_mutation_count_and_unknown_outcome_sticky(self):
        def dispatch(*args, **kwargs):
            self.assertEqual(self.server.active_community_mutations, 1)
            raise CommunityError("outcome_unknown")
        self.service.dispatch.side_effect = dispatch
        self.assertEqual(self.request("/community/messages/send", {})[0], 503)
        self.assertEqual(self.server.active_community_mutations, 0)
        self.assertTrue(self.server.mutation_uncertain.is_set())

    def test_disabled_provider_error_not_fabricated_mutation_uncertainty(self):
        self.service.dispatch.side_effect = CommunityError("community_unavailable")
        self.assertEqual(self.request("/community/messages/send", {})[0], 503)
        self.assertFalse(self.server.mutation_uncertain.is_set())

    def test_rendered_receipt_is_tracked_as_a_mutation(self):
        def dispatch(*args, **kwargs):
            self.assertEqual(self.server.active_community_mutations, 1)
            raise CommunityError("outcome_unknown")
        self.service.dispatch.side_effect = dispatch
        self.assertEqual(self.request("/community/messages/rendered", {})[0], 503)
        self.assertEqual(self.server.active_community_mutations, 0)
        self.assertTrue(self.server.mutation_uncertain.is_set())

    def test_share_refresh_tracks_inflight_and_latches_unknown(self):
        for failure in (None, CommunityError("outcome_unknown"), RuntimeError("private diagnostic")):
            with self.subTest(failure=type(failure).__name__):
                self.server.mutation_uncertain.clear()
                def dispatch(*args, **kwargs):
                    self.assertEqual(self.server.active_community_mutations, 1)
                    if failure:
                        raise failure
                    return {"state": "queued"}
                self.service.dispatch.side_effect = dispatch
                code, body = self.request("/community/shares/refresh", {})
                self.assertEqual(code, 503 if failure else 200)
                self.assertEqual(self.server.active_community_mutations, 0)
                self.assertEqual(self.server.mutation_uncertain.is_set(), failure is not None)
                self.assertNotIn("private diagnostic", str(body))

    def test_share_refresh_lock_and_inspect_preserve_uncertainty(self):
        self.assertEqual(self.request("/community/shares/refresh", {}, token="wrong")[0], 403)
        self.service.dispatch.assert_not_called()
        self.server.mutation_uncertain.set()
        def inspect(*args, **kwargs):
            self.assertEqual(self.server.active_community_mutations, 0)
            return {"state": "pending"}
        self.service.dispatch.side_effect = inspect
        self.assertEqual(self.request("/community/shares/inspect", {})[0], 200)
        self.assertTrue(self.server.mutation_uncertain.is_set())

    def test_share_refresh_authority_revoked_during_dispatch_refuses_result(self):
        def dispatch(*args, **kwargs):
            self.assertEqual(self.server.active_community_mutations, 1)
            self.control.revoke_device()
            return {"state": "queued"}
        self.service.dispatch.side_effect = dispatch
        self.assertEqual(self.request("/community/shares/refresh", {})[0], 403)
        self.assertEqual(self.server.active_community_mutations, 0)

    def test_unexpected_error_and_oversized_response_are_sanitized(self):
        self.service.dispatch.side_effect = RuntimeError("private secret")
        code, body = self.request("/community/shares/submit", {})
        self.assertEqual(code, 503)
        self.assertNotIn("private secret", str(body))
        self.assertTrue(self.server.mutation_uncertain.is_set())
        self.service.dispatch.side_effect = None
        self.service.dispatch.return_value = {"text": "x" * 262144}
        self.assertEqual(self.request()[0], 503)

    def test_idle_community_session_does_not_depend_on_ptt_link(self):
        self.request()
        callback = self.service.dispatch.call_args.args[2]
        self.control.revoke_device(clear_community=False)
        self.control.expire_community_access()
        self.assertTrue(callback())
        self.authorized = False
        self.control.community_check_at = 0
        self.control.expire_community_access()
        self.assertFalse(callback())
        self.assertIsNone(self.control.community_token)

    def test_safety_close_requires_literal_true_and_has_receipt_gate(self):
        for result in (None, 1, False, True):
            with self.subTest(result=result):
                loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, drain_timeout=0.1))
                loop.control.community = Mock()
                loop.control.community.close.return_value = result
                server, thread = Mock(), Mock()
                server.drain.return_value = True
                server.mutation_uncertain = threading.Event()
                thread.is_alive.return_value = False
                receipt = loop.drain_shutdown(server, thread)
                self.assertEqual(receipt["checks"]["community_drained"], result is True)
                self.assertEqual(receipt["drained"], result is True)
                loop.control.community.lock.assert_called()
                self.assertGreaterEqual(loop.control.community.close.call_args.kwargs["timeout"], 0)

    def test_lock_failure_denies_drain_and_new_dispatch(self):
        self.service.lock.side_effect = RuntimeError("private")
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.request()[0], 503)
        self.service.dispatch.assert_not_called()
        self.assertTrue(self.control.community_cleanup_failed)

    def test_final_writer_checks_auth_before_and_after_bytes(self):
        for valid_at_send in (False, True):
            with self.subTest(valid_at_send=valid_at_send):
                handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
                handler.server = self.server
                handler.headers = Message()
                handler.wfile = io.BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                handler.community_access_check = Mock(side_effect=[valid_at_send, False])
                handler.respond(200, {"text": "private payload"})
                self.assertEqual(handler.community_access_check.call_count, 2)
                self.assertEqual(b"private payload" in handler.wfile.getvalue(), valid_at_send)
                self.assertIsNone(self.control.community_token)


if __name__ == "__main__":
    unittest.main()
