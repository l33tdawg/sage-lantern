import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import unittest
import uuid
from unittest.mock import Mock, patch
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("voice_drain_tests", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VoiceDrainTests(unittest.TestCase):
    def test_listener_worker_must_join_before_http_drain_is_complete(self):
        for alive in (False, True):
            with self.subTest(alive=alive):
                worker = Mock()
                worker.is_alive.return_value = alive
                self.loop.listener_thread = worker
                result = self.drain()
                worker.join.assert_called_once()
                self.assertIs(result['checks']['http_drained'], not alive)
                self.assertIs(result['drained'], not alive)

    def test_listener_acknowledgement_stops_without_capture_or_unhandled_failure(self):
        import sage_lantern.voice_listener_ack as acknowledgement
        server = object()
        serving = Mock()
        serving.is_alive.return_value = True
        for fail in (False, True):
            self.loop.stop_requested.clear()
            def exchange(actual_server, actual_thread, stop):
                self.assertIs(actual_server, server)
                self.assertIs(actual_thread, serving)
                self.assertIs(stop, self.loop.stop_requested)
                stop.set()
                if fail:
                    raise RuntimeError('synthetic unavailable broker')
                return True
            with patch.object(acknowledgement, 'acknowledge', side_effect=exchange) as request:
                self.loop.acknowledge_listener(server, serving)
            request.assert_called_once()
        with patch.object(acknowledgement, 'acknowledge') as request:
            self.loop.acknowledge_listener(server, serving)
        request.assert_not_called()

    def test_protected_release_only_exact_true_can_certify_memory(self):
        for result in (False, None, 1, {"released": True}, True):
            with self.subTest(result=result):
                memory = SimpleNamespace(protected_storage=True, release=Mock(return_value=result),
                                         stop=Mock(return_value=True), close=Mock(return_value=True))
                self.loop.memory = memory
                receipt = self.drain()
                self.assertEqual(receipt["checks"]["memory_drained"], result is True)
                self.assertEqual(receipt["drained"], result is True)
                self.assertIs(self.loop.memory, memory)
                memory.stop.assert_not_called()
                memory.close.assert_not_called()
                self.assertGreater(memory.release.call_args.kwargs["timeout"], 0)
                self.assertLessEqual(memory.release.call_args.kwargs["timeout"], 0.1)

    def test_protected_missing_release_or_unknown_never_closes(self):
        for release in (None, Mock(side_effect=RuntimeError("unknown")), Mock(return_value=False)):
            self.loop.memory = SimpleNamespace(protected_storage=True, release=release,
                                               stop=Mock(return_value=True), close=Mock(return_value=True))
            receipt = self.drain()
            self.assertFalse(receipt["drained"])
            self.assertTrue(receipt["reconcile_required"])
            self.loop.memory.close.assert_not_called()
            self.loop.memory.stop.assert_not_called()

    def test_protected_queued_release_success_not_cached_for_repeated_drain(self):
        self.loop.memory = SimpleNamespace(protected_storage=True, release=Mock(side_effect=[True, False]),
                                           stop=Mock(), close=Mock())
        self.assertTrue(self.drain()["drained"])
        self.assertFalse(self.drain()["drained"])
        self.assertEqual(self.loop.memory.release.call_count, 2)
        self.loop.memory.close.assert_not_called()

    def test_protected_expired_deadline_never_calls_release_or_close(self):
        self.loop.memory = SimpleNamespace(protected_storage=True, release=Mock(return_value=True),
                                           stop=Mock(), close=Mock())
        self.loop.stop_started = MODULE.time.monotonic() - 1
        self.assertFalse(self.drain()["drained"])
        self.loop.memory.release.assert_not_called()
        self.loop.memory.close.assert_not_called()

    def test_protected_history_cannot_downgrade_to_legacy_stop(self):
        self.loop.history = SimpleNamespace(media_mode="protected")
        self.loop.memory = SimpleNamespace(stop=Mock(return_value=True), close=Mock())
        self.assertFalse(self.drain()["drained"])
        self.loop.memory.stop.assert_not_called()
        self.loop.memory.close.assert_not_called()

    def setUp(self):
        lock_policy = patch.object(MODULE, "lock_required", return_value=False)
        lock_policy.start()
        self.addCleanup(lock_policy.stop)
        self.loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, drain_timeout=0.1))
        self.server = Mock()
        self.server.drain.return_value = True
        self.server.mutation_uncertain = threading.Event()
        self.thread = Mock()
        self.thread.is_alive.return_value = False

    def drain(self):
        return self.loop.drain_shutdown(self.server, self.thread)

    def test_signal_fences_all_admissions_and_processing(self):
        _, owner = self.loop.control.start("request")
        self.loop.request_stop(signal.SIGTERM, None)
        self.assertIsNone(self.loop.control.take(True))
        self.assertFalse(self.loop.control.begin_processing(owner["session_id"]))
        for operation in (lambda: self.loop.control.start("new"),
                          lambda: self.loop.control.clear(),
                          lambda: self.loop.control.link_device(None)):
            with self.assertRaises(MODULE.ControlError):
                operation()
        with self.assertRaises(MODULE.DeviceLocked):
            self.loop.require_session_access(owner["session_id"])

    def test_idle_not_enough_memory_must_return_exact_true(self):
        for result in (False, None, 1, True):
            with self.subTest(result=result):
                self.loop.memory = Mock()
                self.loop.memory.stop.return_value = result
                receipt = self.drain()
                self.assertEqual(receipt["drained"], result is True)
                self.assertEqual(receipt["reconcile_required"], result is not True)
                timeout = self.loop.memory.stop.call_args.kwargs["timeout"]
                self.assertGreaterEqual(timeout, 0)
                self.assertLessEqual(timeout, 0.1)
                self.loop.memory.close.assert_not_called()

    def test_memory_exception_and_http_unknown_fail_closed(self):
        self.loop.memory = Mock()
        self.loop.memory.stop.side_effect = RuntimeError("private")
        self.assertFalse(self.drain()["drained"])
        self.loop.memory = None
        self.server.mutation_uncertain.set()
        receipt = self.drain()
        self.assertFalse(receipt["drained"])
        self.assertNotIn("private", json.dumps(receipt))

    def test_active_session_and_http_timeout_prevent_receipt_success(self):
        self.loop.device_session = ("private", None, False)
        self.server.drain.return_value = False
        receipt = self.drain()
        self.assertFalse(receipt["checks"]["session_finalized"])
        self.assertFalse(receipt["checks"]["http_drained"])
        self.assertFalse(receipt["drained"])

    def test_pending_owner_finalized_without_new_session(self):
        self.loop.control.start("queued")
        receipt = self.drain()
        self.assertTrue(receipt["drained"])
        self.assertFalse(self.loop.control.busy())
        self.assertFalse(receipt["llm_drained"])
        self.assertFalse(receipt["cgroup_verified"])

    def test_grandchild_group_survives_leader_exit(self):
        process = Mock(pid=45678)
        process.wait.return_value = 0
        self.loop.children[process.pid] = process
        with patch.object(self.loop, "group_absent", return_value=False), patch.object(MODULE.os, "killpg") as kill:
            receipt = self.drain()
        kill.assert_any_call(process.pid, signal.SIGTERM)
        kill.assert_any_call(process.pid, signal.SIGKILL)
        self.assertFalse(receipt["checks"]["child_groups_absent"])
        self.assertFalse(receipt["drained"])

    def test_reaped_group_can_drain(self):
        process = Mock(pid=45678)
        self.loop.children[process.pid] = process
        with patch.object(self.loop, "group_absent", return_value=True):
            self.assertTrue(self.drain()["drained"])
        process.wait.assert_called_once()

    def test_http_active_count_waits_for_admitted_worker(self):
        server = object.__new__(MODULE.VoiceHTTPServer)
        server.draining = threading.Condition()
        server.active_requests = 1
        self.assertFalse(server.drain(0))
        with server.draining:
            server.active_requests = 0
            server.draining.notify_all()
        self.assertTrue(server.drain(0))

    def test_receipt_is_instance_and_transition_bound_atomic_private(self):
        identity = {"instance_id": str(uuid.uuid4()), "boot_id": str(uuid.uuid4()),
                    "pid": 1234, "start_ticks": "5678"}
        transition = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            self.loop.args.drain_receipt = path
            with patch.object(self.loop, "process_identity", return_value=identity), patch.object(self.loop, "drain_transition", return_value=transition):
                self.assertTrue(self.drain()["drained"])
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt["transition_id"], transition)
            for key, value in identity.items():
                self.assertEqual(receipt[key], value)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_stale_request_never_writes_success(self):
        with tempfile.TemporaryDirectory() as directory:
            self.loop.args.drain_receipt = Path(directory) / "receipt.json"
            with patch.object(self.loop, "process_identity", return_value={}), patch.object(self.loop, "drain_transition", side_effect=ValueError("stale")):
                self.assertFalse(self.drain()["drained"])
            self.assertFalse(self.loop.args.drain_receipt.exists())

    def test_request_root_custody_and_matching_identity(self):
        identity = {"instance_id": str(uuid.uuid4()), "boot_id": str(uuid.uuid4()),
                    "pid": 1234, "start_ticks": "5678"}
        transition = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            self.loop.args.drain_request = path
            request = dict(identity, schema="sage.lantern.voice-drain-request.v1", transition_id=transition)
            path.write_text(json.dumps(request))
            path.chmod(0o644)
            metadata = path.stat()
            root_metadata = Mock(st_mode=metadata.st_mode, st_uid=0, st_nlink=1, st_size=metadata.st_size)
            with patch.object(MODULE.os, "fstat", return_value=root_metadata):
                self.assertEqual(self.loop.drain_transition(identity), transition)
                with self.assertRaises(ValueError):
                    self.loop.drain_transition(dict(identity, start_ticks="other"))
            root_metadata.st_uid = 1000
            with patch.object(MODULE.os, "fstat", return_value=root_metadata):
                with self.assertRaises(ValueError):
                    self.loop.drain_transition(identity)

    def test_deadline_includes_time_since_signal(self):
        self.loop.stop_started = MODULE.time.monotonic() - 1
        receipt = self.drain()
        self.assertFalse(receipt["checks"]["deadline_met"])
        self.assertFalse(receipt["drained"])

    def test_run_stops_orderly_and_drains_once(self):
        self.loop.write_state = Mock()
        self.loop.step = Mock(side_effect=self.loop.request_stop)
        self.loop.memory = Mock()
        self.loop.memory.stop.return_value = True
        self.loop.history = Mock()
        self.thread.is_alive.side_effect = [True, True, True, True, False]
        with patch.object(MODULE, "VoiceHTTPServer", return_value=self.server), patch.object(MODULE.threading, "Thread", return_value=self.thread):
            self.loop.run()
        self.loop.step.assert_called_once()
        self.loop.memory.stop.assert_called_once()
        self.server.shutdown.assert_called_once()
        self.server.server_close.assert_called_once()
        self.assertTrue(self.loop.drain_result["drained"])

    def test_active_http_worker_finishes_before_positive_drain(self):
        server = object.__new__(MODULE.VoiceHTTPServer)
        server.draining = threading.Condition()
        server.active_requests = 1
        server.slots = threading.BoundedSemaphore(1)
        server.slots.acquire()
        entered, release = threading.Event(), threading.Event()

        def handler(*args):
            entered.set()
            release.wait(2)

        with patch.object(MODULE.ThreadingHTTPServer, "process_request_thread", side_effect=handler):
            worker = threading.Thread(target=server.process_request_thread, args=(None, None))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(server.drain(0))
            finally:
                release.set()
                worker.join(2)
        self.assertTrue(server.drain(0))

    def test_instance_identity_includes_systemd_invocation(self):
        invocation = "a" * 32
        fields = ["S"] + ["0"] * 18 + ["456"]
        with patch.object(MODULE.Path, "read_text", side_effect=[str(uuid.uuid4()), "123 (voice worker) " + " ".join(fields)]), patch.dict(MODULE.os.environ, {"INVOCATION_ID": invocation}):
            identity = self.loop.process_identity()
        self.assertEqual(identity["invocation_id"], invocation)
        self.assertEqual(identity["start_ticks"], "456")
        self.assertEqual(identity["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()
