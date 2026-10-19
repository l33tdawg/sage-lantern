import argparse
from contextlib import ExitStack
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import wave


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py"
SPEC = importlib.util.spec_from_file_location("unit_a_voice_loop", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VoiceCaptureTests(unittest.TestCase):
    def setUp(self):
        lock_policy = patch.object(MODULE, "lock_required", return_value=False)
        lock_policy.start()
        self.addCleanup(lock_policy.stop)

    def capture(self, *, stopped=True, detail=b"", valid=True, code=1):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            if valid:
                with wave.open(str(path), "wb") as recording:
                    recording.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                    recording.writeframes(b"\0" * 64000)
            else:
                path.write_bytes(b"invalid" * 100)
            loop = MODULE.VoiceLoop(argparse.Namespace(
                alsa_device="hw:CARD=Audio,DEV=0", min_seconds=0.35, max_seconds=30,
            ))
            loop.ptt = Mock(return_value="up")
            loop.write_state = Mock()
            loop.stop_recording = Mock(return_value=stopped)
            process = Mock(returncode=code, stderr=io.BytesIO(detail))
            with patch.object(MODULE.subprocess, "Popen", return_value=process), patch.object(
                MODULE.time, "monotonic", side_effect=[0, 1],
            ):
                return loop.capture("test", path)

    def test_accepts_clean_requested_stop_with_valid_wav(self):
        self.assertTrue(self.capture())

    def test_rejects_spontaneous_exit_one(self):
        with self.assertRaisesRegex(RuntimeError, "recorder exit 1"):
            self.capture(stopped=False)

    def test_preserves_device_error(self):
        with self.assertRaisesRegex(RuntimeError, "No such device"):
            self.capture(detail=b"No such device")

    def test_rejects_invalid_wav_on_exit_one(self):
        with self.assertRaisesRegex(RuntimeError, "recorder exit 1"):
            self.capture(valid=False)

    def test_accepts_normal_exit(self):
        self.assertTrue(self.capture(code=0))

    def test_does_not_signal_exited_process(self):
        process = Mock()
        process.poll.return_value = 1
        self.assertFalse(MODULE.VoiceLoop.stop_recording(process))
        process.send_signal.assert_not_called()

    def test_graceful_stop(self):
        process = Mock()
        process.poll.return_value = None
        self.assertTrue(MODULE.VoiceLoop.stop_recording(process))
        process.send_signal.assert_called_once_with(signal.SIGINT)

    def test_forced_stop_is_not_graceful(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("arecord", 5), 0]
        self.assertFalse(MODULE.VoiceLoop.stop_recording(process))
        process.terminate.assert_called_once()

    def test_unresponsive_recorder_is_killed_and_reaped(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("arecord", 5), subprocess.TimeoutExpired("arecord", 3), 0]
        self.assertFalse(MODULE.VoiceLoop.stop_recording(process))
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 3)

    def test_touch_stop_accepts_but_cancel_lease_and_maximum_discard(self):
        for action in ("stop", "cancel", "lease", "maximum"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "input.wav"
                with wave.open(str(path), "wb") as recording:
                    recording.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                    recording.writeframes(b"\0" * 64000)
                loop = MODULE.VoiceLoop(argparse.Namespace(
                    alsa_device="mock-audio", min_seconds=0.35, max_seconds=2 if action == "maximum" else 30,
                ))
                loop.ptt = Mock(side_effect=AssertionError("Touch must not use physical PTT state"))
                loop.write_state = Mock()
                loop.stop_recording = Mock(return_value=True)
                process = Mock(returncode=1, stderr=io.BytesIO())
                process.poll.return_value = None
                now = [0]
                with patch.object(MODULE.time, "monotonic", side_effect=lambda: now[0]):
                    _, credentials = loop.control.start("test-request")
                    loop.control.take(False)

                    def advance(_delay):
                        now[0] = 5 if action == "lease" else 2 if action == "maximum" else 1
                        if action in {"stop", "cancel"}:
                            loop.control.command(action, credentials["session_id"], credentials["token"])

                    with patch.object(MODULE.subprocess, "Popen", return_value=process) as popen, patch.object(MODULE.time, "sleep", side_effect=advance):
                        self.assertEqual(loop.capture(credentials["session_id"], path), action == "stop")
                        popen.assert_called_once()
                loop.stop_recording.assert_called_once_with(process)
                self.assertTrue(process.stderr.closed)

    def test_state_write_error_stops_recorder_and_closes_pipe(self):
        loop = MODULE.VoiceLoop(argparse.Namespace(alsa_device="mock", max_seconds=30, min_seconds=0.35))
        loop.write_state = Mock(side_effect=OSError("state unavailable"))
        loop.stop_recording = Mock(return_value=True)
        process = Mock(stderr=io.BytesIO())
        with patch.object(MODULE.subprocess, "Popen", return_value=process):
            with self.assertRaises(OSError):
                loop.capture("test", Path("unused.wav"))
        loop.stop_recording.assert_called_once_with(process)
        self.assertTrue(process.stderr.closed)

    def test_capture_limit_refuses_release_and_recorder_exit_at_deadline(self):
        for source, completion, elapsed in (
            ("physical", "release", 30.0),
            ("physical", "release", 30.04),
            ("physical", "exit", 30.0),
            ("touch", "stop", 30.04),
        ):
            with self.subTest(source=source, completion=completion, elapsed=elapsed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "input.wav"
                with wave.open(str(path), "wb") as recording:
                    recording.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                    recording.writeframes(b"\1\0" * 32000)
                loop = MODULE.VoiceLoop(argparse.Namespace(alsa_device="mock", min_seconds=0.35, max_seconds=30))
                loop.write_state = Mock()
                loop.stop_recording = Mock(return_value=True)
                now = [0.0]
                process = Mock(returncode=1, stderr=io.BytesIO())
                process.poll.side_effect = lambda: 0 if completion == "exit" and now[0] else None
                loop.ptt = Mock(side_effect=lambda: "up" if now[0] else "down")
                with patch.object(MODULE.time, "monotonic", side_effect=lambda: now[0]):
                    if source == "touch":
                        _, credentials = loop.control.start("test-request")
                        session = loop.control.take(False)
                    else:
                        session = loop.control.take(True)
                    session_id = session["session_id"]

                    def advance(_delay):
                        if source == "touch":
                            if now[0] < 27:
                                now[0] += 3
                                loop.control.command("keepalive", session_id, credentials["token"])
                                return
                            now[0] = 29.99
                            loop.control.command("stop", session_id, credentials["token"])
                        now[0] = elapsed

                    with patch.object(MODULE.subprocess, "Popen", return_value=process), patch.object(MODULE.time, "sleep", side_effect=advance):
                        self.assertFalse(loop.capture(session_id, path))
                    self.assertEqual(loop.control.action(session_id), "cancel")
                    self.assertFalse(loop.control.begin_processing(session_id))
                loop.stop_recording.assert_called_once_with(process)
                self.assertTrue(process.stderr.closed)

    def test_physical_release_before_limit_preserves_audio_despite_slow_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            with wave.open(str(path), "wb") as recording:
                recording.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                recording.writeframes(b"\1\0" * 32000)
            loop = MODULE.VoiceLoop(argparse.Namespace(alsa_device="mock", min_seconds=0.35, max_seconds=30))
            loop.write_state = Mock()
            now = [0.0]
            loop.ptt = Mock(side_effect=lambda: "up" if now[0] else "down")
            process = Mock(returncode=1, stderr=io.BytesIO())
            process.poll.return_value = None

            def stop(_process):
                now[0] += 3
                return True

            def advance(_delay):
                now[0] = 29.0

            loop.stop_recording = Mock(side_effect=stop)
            with patch.object(MODULE.time, "monotonic", side_effect=lambda: now[0]):
                session_id = loop.control.take(True)["session_id"]
                with patch.object(MODULE.subprocess, "Popen", return_value=process), patch.object(MODULE.time, "sleep", side_effect=advance):
                    self.assertTrue(loop.capture(session_id, path))
                self.assertTrue(loop.control.begin_processing(session_id))
            self.assertTrue(process.stderr.closed)

    def test_capture_refuses_full_limit_wav_after_early_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            with wave.open(str(path), "wb") as recording:
                recording.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                recording.writeframes(b"\1\0" * (2 * 16000 * 30))
            loop = MODULE.VoiceLoop(argparse.Namespace(alsa_device="mock", min_seconds=0.35, max_seconds=30))
            loop.ptt = Mock(return_value="up")
            loop.write_state = Mock()
            loop.stop_recording = Mock(return_value=True)
            process = Mock(returncode=1, stderr=io.BytesIO())
            process.poll.return_value = None
            now = [0.0]
            with patch.object(MODULE.time, "monotonic", side_effect=lambda: now[0]):
                session_id = loop.control.take(True)["session_id"]

                def released(_session_id):
                    now[0] = 29.0
                    return "physical"

                with patch.object(loop.control, "source", side_effect=released), patch.object(MODULE.subprocess, "Popen", return_value=process):
                    self.assertFalse(loop.capture(session_id, path))
                self.assertEqual(loop.control.action(session_id), "cancel")
                self.assertFalse(loop.control.begin_processing(session_id))
            self.assertTrue(process.stderr.closed)


class VoiceEventTests(unittest.TestCase):
    def setUp(self):
        lock_policy = patch.object(MODULE, "lock_required", return_value=False)
        lock_policy.start()
        self.addCleanup(lock_policy.stop)
        self.loop = MODULE.VoiceLoop(argparse.Namespace(
            max_seconds=30, runtime=Path("/mock/runtime"), config=Path("/mock/config"), processing_timeout=20,
        ))
        self.loop.write_state = Mock()
        self.result = {"transcript": "Where?", "response": "Kuala Lumpur", "timings_ms": {"ttft": 120}}

    def event(self, value):
        return json.dumps(value).encode() + b"\n"

    def delta(self, text):
        return self.event({"type": "delta", "text": text, "transcript": "Where?"})

    def final(self):
        return self.event({"type": "result", "result": self.result})

    def progress(self, stage):
        return self.event({"type": "progress", "stage": stage})

    def execute(self, chunks, *, returncode=0, ready=True, waits=None, error=None, idle_polls=0):
        process = Mock(pid=12345)
        process.stdout.fileno.return_value = 123
        process.wait.return_value = returncode
        if waits is not None:
            process.wait.side_effect = waits
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [True] if ready else []
        pending = iter(chunks)
        now = [0]
        if waits is not None:
            pending_waits = iter(waits)

            def wait(**kwargs):
                outcome = next(pending_waits)
                if isinstance(outcome, subprocess.TimeoutExpired):
                    now[0] += 21
                    raise outcome
                return outcome

            process.wait.side_effect = wait
        polls = [0]
        def select(_timeout):
            polls[0] += 1
            if 1 < polls[0] <= idle_polls + 1:
                now[0] += _timeout
                return []
            if not ready:
                now[0] += _timeout
            return [True] if ready else []
        selector.select.side_effect = select

        def read(_descriptor, maximum):
            value = next(pending, b"")
            if isinstance(value, tuple):
                now[0], value = value
            self.assertLessEqual(len(value), maximum)
            return value

        def launch(arguments, **kwargs):
            self.assertEqual(arguments, ["/mock/runtime", "voice", "--config", "/mock/config", "--audio", "input.wav", "--session-id", "session", "--events"])
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["bufsize"], 0)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            return process

        with ExitStack() as stack:
            stack.enter_context(patch.object(MODULE.tempfile, "TemporaryFile", side_effect=AssertionError("Runtime stderr must not create a temporary file")))
            stack.enter_context(patch.object(MODULE.subprocess, "Popen", side_effect=launch))
            stack.enter_context(patch.object(MODULE.selectors, "DefaultSelector", return_value=selector))
            stack.enter_context(patch.object(MODULE.os, "read", side_effect=read))
            nonblocking = stack.enter_context(patch.object(MODULE.os, "set_blocking"))
            kill = stack.enter_context(patch.object(MODULE.os, "killpg"))
            stack.enter_context(patch.object(MODULE.time, "monotonic", side_effect=lambda: now[0]))
            if error:
                with self.assertRaisesRegex(RuntimeError, error):
                    self.loop.process("session", Path("input.wav"))
                kill.assert_any_call(process.pid, signal.SIGTERM)
                kill.assert_any_call(process.pid, signal.SIGKILL)
                self.assertFalse(any(call.args[0] == "complete" for call in self.loop.write_state.call_args_list))
            else:
                self.loop.process("session", Path("input.wav"))
                self.assertTrue(all(call.args[1] == 0 for call in kill.call_args_list))
            nonblocking.assert_called_once_with(123, False)
        process.stdout.close.assert_called_once()
        self.assertNotIn("private stderr", str(self.loop.write_state.call_args_list))
        return process

    def test_short_answer_flushes_on_completion_without_token_dribble(self):
        self.result['inference_metrics'] = {'prompt_tokens': 20, 'cached_tokens': 12,
            'prefill_ms': 4.5, 'output_tokens': True, 'text': 'PRIVATE METRIC'}
        self.execute([
            (0.01, self.delta("Kuala")),
            (0.02, self.delta(" ")),
            (0.12, self.delta("Lumpur")),
            (0.13, self.final()),
        ])
        calls = self.loop.write_state.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["processing", "processing", "complete"])
        self.assertEqual(calls[0].kwargs["progress_stage"], "starting")
        self.assertEqual(calls[1].kwargs["progress_stage"], "answering")
        self.assertEqual(calls[1].kwargs["response"], "")
        self.assertEqual(calls[-1].kwargs["response"], "Kuala Lumpur")
        self.assertEqual(calls[-1].kwargs["timings_ms"], {"ttft": 120})
        metrics = calls[-1].kwargs['inference_metrics']
        self.assertEqual(metrics['prompt_tokens'], 20)
        self.assertEqual(metrics['cached_tokens'], 12)
        self.assertEqual(metrics['prefill_ms'], 4.5)
        self.assertIsNone(metrics['output_tokens'])
        self.assertNotIn('PRIVATE', json.dumps(metrics))

    def test_no_speech_error_stops_pipeline(self):
        self.execute([self.progress("transcribing"), b'{"type":"error","code":"no_spoken_question"}\n'], error="no_spoken_question")

    def test_unknown_error_is_not_exposed(self):
        self.execute([b'{"type":"error","code":"private detail"}\n'], error="invalid voice event")

    def test_batches_three_initial_then_five_complete_words(self):
        self.execute([self.delta(word) for word in ["One ", "two ", "three ", "four ", "five ", "six ", "seven ", "eight "]] + [self.final()])
        updates = self.loop.write_state.call_args_list[1:-1]
        self.assertEqual([call.kwargs["response"] for call in updates], ["", "One two three ", "One two three four five six seven eight "])

    def test_sentence_and_slow_token_flush(self):
        self.execute([(0, self.delta("Hello.")), (0.1, self.delta(" slow")), (1.4, self.delta(" word")), self.final()])
        updates = self.loop.write_state.call_args_list[1:-1]
        self.assertEqual([call.kwargs["response"] for call in updates], ["Hello.", "Hello. slow word"])

    def test_non_space_script_has_bounded_batch(self):
        self.execute([self.delta("文" * 80), self.final()])
        self.assertEqual(self.loop.write_state.call_args_list[1].kwargs["response"], "文" * 80)

    def test_idle_selector_flushes_pending_text_without_another_delta(self):
        self.execute([self.delta("unfinished"), self.final()], idle_polls=7)
        calls = self.loop.write_state.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["processing", "processing", "processing", "complete"])
        self.assertEqual(calls[2].kwargs["response"], "unfinished")
        self.assertEqual(calls[2].kwargs["detail"], "Answering…")

    def test_real_content_is_published_within_170ms_during_upstream_pause(self):
        published = []
        self.loop.write_state.side_effect = lambda *args, **kwargs: published.append(
            (MODULE.time.monotonic(), args[0], kwargs.get('response', '')))
        self.execute([self.delta("unfinished"), self.final()], idle_polls=7)
        visible = [(when, text) for when, phase, text in published
                   if phase == 'processing' and text.strip()]
        self.assertEqual(len(visible), 1)
        self.assertLessEqual(visible[0][0], 0.17)
        self.assertEqual(visible[0][1], 'unfinished')
        self.assertEqual(published[-1][2], self.result['response'])

    def test_paused_non_space_unicode_keeps_exact_content(self):
        text = '你好، مرحبا'
        self.execute([self.delta(text), self.final()], idle_polls=7)
        visible = [call.kwargs['response'] for call in self.loop.write_state.call_args_list
                   if call.args[0] == 'processing' and call.kwargs.get('response')]
        self.assertEqual(visible, [text])

    def test_split_unicode_json_lines_and_unterminated_final(self):
        first = self.delta("Kuala ")
        self.execute([first[:10], first[10:] + self.delta("Lumpur"), self.final().rstrip(b"\n")])
        self.assertEqual(self.loop.write_state.call_args.kwargs["response"], "Kuala Lumpur")

    def test_malformed_missing_final_and_bad_result_fail_closed(self):
        cases = [
            ([b"not json\n"], "invalid voice event"),
            ([self.delta("partial")], "omitted final result"),
            ([self.event({"type": "result", "result": {}})], "invalid voice event"),
            ([self.event({"type": "delta", "text": 4, "transcript": "hi"})], "invalid voice event"),
            ([self.final(), self.delta("late")], "invalid voice event"),
            ([self.final(), self.final()], "invalid voice event"),
        ]
        for chunks, error in cases:
            with self.subTest(error=error, chunks=len(chunks)):
                self.loop.write_state.reset_mock()
                self.execute(chunks, error=error)

    def test_nonzero_exit_does_not_publish_complete(self):
        self.execute([self.delta("partial"), self.final()], returncode=1, error="exited unsuccessfully")

    def test_selector_timeout_and_wait_timeout_reap_group(self):
        self.execute([], ready=False, error="timed out")
        self.loop.write_state.reset_mock()
        self.execute([self.final()], waits=[subprocess.TimeoutExpired("runtime", 20), 0, 0], error="timed out")

    def test_deadline_applies_even_when_output_keeps_arriving(self):
        self.execute([(21, self.delta("too late"))], error="timed out")

    def test_line_and_accumulated_response_limits(self):
        with patch.object(MODULE, "MAX_EVENT_BYTES", 256):
            self.execute([b"x" * 257], error="size limit")
            self.loop.write_state.reset_mock()
            self.execute([self.delta("x" * 100)] * 3, error="invalid voice event")

    def test_processing_owner_stays_busy_during_stream(self):
        owner = self.loop.control.take(True)
        self.assertTrue(self.loop.control.begin_processing(owner["session_id"]))

        def observe(*args, **kwargs):
            self.assertTrue(self.loop.control.busy())
            with self.assertRaises(MODULE.ControlError):
                self.loop.control.start("competing-request")

        self.loop.write_state.side_effect = observe
        self.execute([self.delta("answer"), self.final()])
        self.assertTrue(self.loop.control.busy())

    def test_valid_progress_branches_and_optional_stages(self):
        for branch in ("photo_analysis", "generating"):
            for tail in ([], ["answering"], ["speaking"], ["answering", "speaking"]):
                with self.subTest(branch=branch, tail=tail):
                    self.loop.write_state.reset_mock()
                    stages = ["transcribing", "recalling", branch, *tail]
                    self.execute([self.progress(stage) for stage in stages] + [self.final()])
                    calls = self.loop.write_state.call_args_list[:-1]
                    self.assertEqual([call.kwargs["progress_stage"] for call in calls], ["starting", *stages])
                    for call in calls:
                        self.assertEqual(call.args, ("processing",))
                        self.assertEqual(call.kwargs["session_id"], "session")
                        self.assertEqual(call.kwargs["transcript"], "")
                        self.assertEqual(call.kwargs["response"], "")

    def test_progress_preserves_answer_and_flushes_pending_before_speaking(self):
        self.execute([
            self.progress("transcribing"), self.progress("recalling"), self.progress("generating"),
            self.progress("answering"), self.delta("Hello."), self.delta(" buffered"),
            self.progress("speaking"), self.final(),
        ])
        calls = self.loop.write_state.call_args_list
        speaking = next(call for call in calls if call.kwargs.get("progress_stage") == "speaking")
        self.assertEqual(speaking.kwargs["transcript"], "Where?")
        self.assertEqual(speaking.kwargs["response"], "Hello. buffered")
        self.assertEqual(speaking.kwargs["session_id"], "session")
        self.assertEqual(calls[-2], speaking)

    def test_whitespace_delta_does_not_preempt_real_answering_progress(self):
        self.execute([
            self.progress("transcribing"), self.progress("recalling"), self.progress("generating"),
            self.delta(" \n\t" * 30), self.progress("answering"),
            self.delta("Visible answer."), self.final(),
        ])
        calls = self.loop.write_state.call_args_list
        stages = [call.kwargs.get("progress_stage") for call in calls]
        self.assertEqual(stages, ["starting", "transcribing", "recalling", "generating", "answering", "answering", None])
        self.assertEqual(calls[4].kwargs["response"], " \n\t" * 30)
        self.assertEqual(calls[5].kwargs["response"], " \n\t" * 30 + "Visible answer.")
        self.assertEqual(calls[5].kwargs["transcript"], "Where?")

    def test_whitespace_only_legacy_stream_does_not_invent_answering(self):
        self.execute([self.delta(" " * 100), self.final()], idle_polls=7)
        self.assertEqual([call.kwargs.get("progress_stage") for call in self.loop.write_state.call_args_list],
                         ["starting", None])

    def test_invalid_repeated_skipped_and_backward_progress_is_rejected(self):
        prefix = [self.progress("transcribing"), self.progress("recalling"), self.progress("generating")]
        invalid = [
            [self.progress("recalling")],
            [self.progress("transcribing"), self.progress("generating")],
            [self.progress("transcribing"), self.progress("transcribing")],
            [*prefix, self.progress("photo_analysis")],
            [*prefix, self.progress("answering"), self.progress("generating")],
            [*prefix, self.delta("answer"), self.progress("answering")],
            [*prefix, self.progress("speaking"), self.delta("late")],
            [self.delta("answer"), self.progress("transcribing")],
            [self.final(), self.progress("transcribing")],
            [self.progress("transcribing"), self.delta("premature")],
        ]
        for chunks in invalid:
            with self.subTest(chunks=chunks):
                self.loop.write_state.reset_mock()
                self.execute(chunks, error="invalid voice event")

    def test_progress_schema_is_strict_and_sanitized(self):
        for event in (
            {"type": "progress"}, {"type": "progress", "stage": []},
            {"type": "progress", "stage": None}, {"type": "progress", "stage": "private-stage"},
            {"type": "progress", "stage": "transcribing", "session_id": "other-session"},
            {"type": "progress", "stage": "transcribing", "response": "private-answer"},
        ):
            with self.subTest(event=event):
                self.loop.write_state.reset_mock()
                self.execute([self.event(event)], error="invalid voice event")
                self.assertNotIn("private-", str(self.loop.write_state.call_args_list))
        self.loop.write_state.reset_mock()
        self.execute([b'{"type":"progress","stage":"private-stage","stage":"transcribing"}\n'],
                     error="invalid voice event")

    def test_idle_wait_never_invents_progress_and_next_session_resets(self):
        self.execute([self.final()], idle_polls=7)
        self.assertEqual([call.kwargs.get("progress_stage") for call in self.loop.write_state.call_args_list],
                         ["starting", None])
        self.loop.write_state.reset_mock()
        self.execute([self.progress("transcribing"), self.progress("recalling"),
                      self.progress("generating"), self.delta("One two three "), self.final()])
        self.assertEqual(self.loop.write_state.call_args_list[0].kwargs["progress_stage"], "starting")
        self.assertEqual(self.loop.write_state.call_args_list[-2].kwargs["progress_stage"], "answering")


if __name__ == "__main__":
    unittest.main()
