import io
import json
import os
from unittest.mock import patch

import pytest

from sage_lantern.adapters import LlamaCppHTTP
from sage_lantern.inference_guard import InferenceGuardError, begin, pending_count


@pytest.fixture
def directory(tmp_path):
    path = tmp_path.resolve() / "inference"
    path.mkdir(mode=0o700)
    return path


def count(directory):
    return pending_count(directory, owner_uid=os.geteuid())


def test_persist_reopen_complete_and_no_contents(directory):
    ticket = begin(directory)
    assert count(directory) == 1
    assert (directory / ticket.request_id).read_text() == ticket.request_id + "\n"
    assert (directory / ticket.request_id).stat().st_mode & 0o777 == 0o600
    ticket.complete()
    assert count(directory) == 0
    with pytest.raises(InferenceGuardError):
        ticket.complete()


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "mode", "garbage", "directory", "owner"])
def test_unsafe_entries_fail_closed(directory, fault):
    ticket = begin(directory)
    marker = directory / ticket.request_id
    if fault == "symlink":
        marker.unlink()
        marker.symlink_to("missing")
    elif fault == "hardlink":
        os.link(marker, directory.parent / "linked")
    elif fault == "mode":
        marker.chmod(0o644)
    elif fault == "garbage":
        marker.write_bytes(b"bad")
    elif fault == "directory":
        marker.unlink()
        marker.mkdir()
    else:
        with pytest.raises(InferenceGuardError):
            pending_count(directory, owner_uid=os.geteuid() + 1)
        return
    with pytest.raises(InferenceGuardError):
        count(directory)
    with pytest.raises(InferenceGuardError):
        begin(directory)


def test_directory_links_modes_missing_and_replacement(directory):
    alias = directory.parent / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(InferenceGuardError):
        begin(alias)
    directory.chmod(0o755)
    with pytest.raises(InferenceGuardError):
        begin(directory)
    directory.chmod(0o700)
    ticket = begin(directory)
    directory.rename(directory.with_name("old"))
    directory.mkdir(mode=0o700)
    with pytest.raises(InferenceGuardError):
        ticket.complete()
    with pytest.raises(InferenceGuardError):
        begin(directory / "missing")


def test_entry_bound(directory):
    with patch("sage_lantern.inference_guard.MAX_PENDING", 2):
        begin(directory)
        begin(directory)
        with pytest.raises(InferenceGuardError):
            begin(directory)
        assert count(directory) == 2


def test_fsync_before_admission_and_failure_retains_marker(directory):
    with patch("sage_lantern.inference_guard.os.fsync", side_effect=OSError):
        with pytest.raises(InferenceGuardError):
            begin(directory)
    assert count(directory) == 1


class Response(io.BytesIO):
    def __init__(self, raw, directory, close_failure=False):
        super().__init__(raw)
        self.directory = directory
        self.close_failure = close_failure

    def __exit__(self, *args):
        assert count(self.directory) == 1
        super().__exit__(*args)
        if self.close_failure:
            raise OSError("close failed")


def wire(stream, terminal=True):
    if stream:
        chunk = {"choices": [{"index": 0, "delta": {"content": "ready"}, "finish_reason": "stop" if terminal else None}]}
        return b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n"
    return json.dumps({"choices": [{"message": {"content": "ready"}, "finish_reason": "stop" if terminal else None}]}).encode()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("fault", [None, "terminal", "transport", "close", "callback", "death"])
def test_adapter_lifecycle(directory, monkeypatch, stream, fault):
    monkeypatch.setenv("SAGE_LANTERN_INFERENCE_GUARD", str(directory))

    def open_response(*args, **kwargs):
        assert count(directory) == 1
        if fault == "transport":
            raise OSError("unconfirmed")
        if fault == "death":
            raise KeyboardInterrupt
        return Response(wire(stream, fault != "terminal"), directory, fault == "close")

    def delta(value):
        if fault == "callback":
            raise RuntimeError("cancelled")

    model = LlamaCppHTTP("http://127.0.0.1:8081", "test")
    with patch.object(LlamaCppHTTP, "_open_response", open_response):
        if fault is None or (fault == "callback" and not stream):
            result = model.respond_stream("private question", [], delta) if stream else model.respond("private question", [])
            assert result == "ready"
            assert count(directory) == 0
        else:
            with pytest.raises((OSError, RuntimeError, KeyboardInterrupt)):
                if stream:
                    model.respond_stream("private question", [], delta)
                else:
                    model.respond("private question", [])
            assert count(directory) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_bad_guard_denies_http(directory, monkeypatch, stream):
    monkeypatch.setenv("SAGE_LANTERN_INFERENCE_GUARD", "")
    with patch("urllib.request.urlopen") as upstream, pytest.raises(InferenceGuardError):
        model = LlamaCppHTTP("http://127.0.0.1:8081", "test")
        if stream:
            model.respond_stream("question", [], lambda value: None)
        else:
            model.respond("question", [])
    upstream.assert_not_called()


def test_guarded_transport_fixed_and_proxy_free(directory, monkeypatch):
    monkeypatch.setenv("SAGE_LANTERN_INFERENCE_GUARD", str(directory))
    with patch("urllib.request.build_opener") as builder:
        builder.return_value.open.return_value = Response(wire(False), directory)
        assert LlamaCppHTTP("http://127.0.0.1:8081", "test").respond("question", []) == "ready"
        proxy, redirects = builder.call_args.args
        assert proxy.proxies == {}
        with pytest.raises(RuntimeError):
            redirects.redirect_request(None, None, 302, "", {}, "http://elsewhere")
        with pytest.raises(RuntimeError):
            LlamaCppHTTP("http://localhost:8081", "test").respond("question", [])
        assert builder.call_count == 1


def test_duplicate_terminal_field_preserves_marker(directory, monkeypatch):
    monkeypatch.setenv("SAGE_LANTERN_INFERENCE_GUARD", str(directory))
    raw = b'{"choices":[{"finish_reason":null,"finish_reason":"stop","message":{"content":"ready"}}]}'
    with patch.object(LlamaCppHTTP, "_open_response", return_value=Response(raw, directory)):
        with pytest.raises(RuntimeError):
            LlamaCppHTTP("http://127.0.0.1:8081", "test").respond("question", [])
    assert count(directory) == 1


@pytest.mark.parametrize("reason", ["stop", "length"])
def test_terminal_empty_answer_clears_before_semantic_error(directory, monkeypatch, reason):
    monkeypatch.setenv("SAGE_LANTERN_INFERENCE_GUARD", str(directory))
    raw = json.dumps({"choices": [{"finish_reason": reason, "message": {"content": ""}}]}).encode()
    with patch.object(LlamaCppHTTP, "_open_response", return_value=Response(raw, directory)):
        with pytest.raises(RuntimeError, match="empty response"):
            LlamaCppHTTP("http://127.0.0.1:8081", "test").respond("question", [])
    assert count(directory) == 0


@pytest.mark.parametrize("done", [True, False])
@pytest.mark.parametrize("close_failure", [True, False])
def test_stream_length_requires_done_and_closed_context(directory, monkeypatch, done, close_failure):
    monkeypatch.setenv("SAGE_LANTERN_INFERENCE_GUARD", str(directory))
    raw = wire(True).replace(b'"stop"', b'"length"')
    if not done:
        raw = raw.replace(b"data: [DONE]\n\n", b"")
    deltas = []
    with patch.object(LlamaCppHTTP, "_open_response", return_value=Response(raw, directory, close_failure)):
        with pytest.raises((RuntimeError, OSError)):
            LlamaCppHTTP("http://127.0.0.1:8081", "test").respond_stream("question", [], deltas.append)
    assert deltas == []
    assert count(directory) == (0 if done and not close_failure else 1)
