"""Caller contract doubles only; protected atomic intent backing tested separately."""

import base64
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid
import threading

import pytest


SPEC = importlib.util.spec_from_file_location("protected_media_voice", Path(__file__).resolve().parents[1] / "lantern/unit-a-voice-loop.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
JPEG = (b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00\xff\xd9")
DIGEST = hashlib.sha256(JPEG).hexdigest()


@pytest.fixture
def rig(tmp_path):
    directory = tmp_path / "history"
    MODULE.LocalHistory(directory, media_mode="legacy").consent()
    fixture = SimpleNamespace(live=True, calls=[], items=[], intents=[], photos={}, fail=None,
                              after_put=None, after_get=None)

    class Memory:
        last_error = None
        def __init__(self):
            self.store = self
            self.ticket = "ticket"
        def items(self):
            return json.loads(json.dumps(fixture.items))
        def consent(self):
            return self.ticket
        def approve(self, enabled, *, history_generation):
            self.ticket = history_generation if enabled else None
        def suppress(self, identifiers):
            fixture.items[:] = [item for item in fixture.items if item["session_id"] not in identifiers]
        def reserve_media(self, session_id, digest, *, ticket, item, access_check):
            assert access_check() is True and ticket == self.ticket
            fixture.calls.append("reserve")
            if fixture.fail == "reserve":
                raise RuntimeError()
            intent = dict(item=item, ticket=ticket, photo_reference=dict(object_id=session_id, digest=digest,
                                                                backend="sage.private-media.v1"))
            fixture.intents.append(intent)
            return intent
        def commit_media(self, intent, *, access_check):
            assert access_check() is True
            fixture.calls.append("commit")
            if fixture.fail == "commit" or intent["ticket"] != self.ticket:
                raise RuntimeError()
            item = intent["item"]
            fixture.items.append(dict(session_id=item["session_id"], created_at=item["created_at"],
                transcript=item["question"], response=item["answer"], has_photo=True, photo_reference=intent["photo_reference"]))
            return True
        def record(self, ticket, item):
            fixture.calls.append("text_record")
        def correction_operation(self, *args, **kwargs):
            raise AssertionError("media fixture does not execute corrections")

    class Media:
        def put(self, identifier, original, *, access_check):
            assert access_check() is True and fixture.intents
            fixture.calls.append("put")
            fixture.photos[identifier] = original
            if fixture.after_put:
                fixture.after_put()
            if fixture.fail == "put":
                raise RuntimeError()
            return DIGEST
        def get(self, identifier, *, access_check, expected_digest):
            assert access_check() is True and expected_digest == DIGEST
            fixture.calls.append("get")
            if fixture.after_get:
                fixture.after_get()
            return fixture.photos[identifier]

    fixture.memory, fixture.media = Memory(), Media()
    fixture.history = MODULE.LocalHistory(directory, fixture.memory, media_mode="protected", media_store=fixture.media)
    fixture.guard = lambda: fixture.live
    fixture.identifier = str(uuid.uuid4())
    fixture.save = lambda: fixture.history.record(fixture.history.consent(), fixture.identifier,
        {"transcript": "question", "response": "answer"}, original=JPEG, access_check=fixture.guard)
    yield fixture


def test_reserve_put_atomic_link_order_and_no_filesystem(rig):
    with patch.object(MODULE, "HistoryMediaStore", side_effect=AssertionError("plaintext fallback")):
        rig.save()
        assert rig.calls == ["reserve", "put", "commit"]
        response = rig.history.request("photo", {"session_id": rig.identifier}, access_check=rig.guard)
    assert base64.b64decode(response["image_base64"]) == JPEG
    assert not rig.history.media_directory.exists()


@pytest.mark.parametrize("failure", ["reserve", "put", "commit"])
def test_uncertainty_never_links_or_falls_back(rig, failure):
    rig.fail = failure
    with pytest.raises(MODULE.ControlError):
        rig.save()
    assert not rig.items and not rig.history.media_directory.exists()
    if failure == "reserve":
        assert "put" not in rig.calls
    else:
        assert rig.intents


def test_absent_atomic_intent_interface_blocks_before_put(rig):
    rig.memory.reserve_media = None
    with pytest.raises(MODULE.ControlError):
        rig.save()
    assert not rig.photos


def test_delete_discards_link_not_immutable_media_or_legacy_files(rig):
    rig.save()
    rig.history.media_directory.mkdir()
    legacy = rig.history.media_directory / "preserve.media"
    legacy.write_bytes(b"legacy untouched")
    response = rig.history.request("delete", {"session_id": rig.identifier}, access_check=rig.guard)
    assert response["photo_removal"] == "history_link_only"
    assert rig.photos[rig.identifier] == JPEG and legacy.read_bytes() == b"legacy untouched"
    with pytest.raises(MODULE.ControlError):
        rig.history.request("photo", {"session_id": rig.identifier}, access_check=rig.guard)


def test_legacy_link_without_reference_does_not_fallback(rig):
    rig.items.append(dict(session_id=rig.identifier, has_photo=True))
    with pytest.raises(MODULE.ControlError) as error:
        rig.history.request("photo", {"session_id": rig.identifier}, access_check=rig.guard)
    assert error.value.code == "legacy_history_photo_unavailable" and not rig.calls


@pytest.mark.parametrize("change", [dict(backend="filesystem"), dict(object_id=str(uuid.uuid4())),
    dict(digest="invalid"), dict(extra=True)])
def test_bad_reference_rejected_before_get(rig, change):
    rig.save()
    rig.items[0]["photo_reference"].update(change)
    with pytest.raises(MODULE.ControlError):
        rig.history.request("photo", {"session_id": rig.identifier}, access_check=rig.guard)
    assert "get" not in rig.calls


def test_lock_after_put_leaves_intent_unlinked(rig):
    rig.after_put = lambda: setattr(rig, "live", False)
    with pytest.raises(MODULE.DeviceLocked):
        rig.save()
    assert rig.intents and not rig.items and "commit" not in rig.calls


@pytest.mark.parametrize("event", ["lock", "suppress"])
def test_read_last_moment_guard_and_suppression(rig, event):
    rig.save()
    rig.after_get = (lambda: setattr(rig, "live", False)) if event == "lock" else rig.items.clear
    with pytest.raises((MODULE.DeviceLocked, MODULE.ControlError)):
        rig.history.request("photo", {"session_id": rig.identifier}, access_check=rig.guard)


def test_protected_reads_require_explicit_true_guard(rig):
    rig.save()
    for guard in (None, lambda: None, lambda: False):
        with pytest.raises(MODULE.DeviceLocked):
            rig.history.request("photo", {"session_id": rig.identifier}, access_check=guard)


def test_optout_stops_put_and_preserves_existing_photo(rig):
    rig.save()
    before = list(rig.calls)
    rig.history.request("settings", {"enabled": False})
    rig.save()
    assert rig.calls == before
    assert rig.history.request("photo", {"session_id": rig.identifier}, access_check=rig.guard)


def test_protected_mode_never_accepts_missing_provider(rig):
    with pytest.raises(ValueError):
        MODULE.LocalHistory(rig.history.directory, rig.memory, media_mode="protected")
    with pytest.raises(ValueError):
        MODULE.LocalHistory(rig.history.directory, rig.memory, media_store=rig.media)
    with pytest.raises(ValueError):
        MODULE.VoiceLoop(SimpleNamespace(max_seconds=30, history_dir=rig.history.directory), history_media_mode="protected")


def test_photo_optout_saves_text_without_media_intent(rig):
    rig.history.request("settings", {"photos_enabled": False})
    rig.save()
    assert rig.calls == ["text_record"] and not rig.intents and not rig.photos


def test_http_injects_token_guard_even_without_legacy_lock_flag(rig):
    rig.save()
    server = MODULE.ThreadingHTTPServer(("127.0.0.1", 0), MODULE.VoiceHandler)
    server.control = MODULE.VoiceControl(30)
    server.history = rig.history
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(MODULE, "lock_required", return_value=False), patch.object(
                MODULE, "token_authorized", side_effect=lambda token: token == "synthetic-token" and rig.live):
            def request(token):
                connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                try:
                    connection.request("POST", "/history/photo", json.dumps({"session_id": rig.identifier}),
                        headers={"Host": "127.0.0.1:3197", "Origin": "http://127.0.0.1:3100",
                                 "Content-Type": "application/json", "X-Lantern-Voice": "control",
                                 "X-Lantern-Session": token})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            assert request("invalid")[0] == 403
            assert request("synthetic-token")[0] == 200
            rig.after_get = lambda: setattr(rig, "live", False)
            assert request("synthetic-token")[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_service_wrapped_media_read_blocks_drain_and_denies_poststop_return(rig):
    from sage_lantern.conversation_service import ConversationService
    rig.save()
    service = ConversationService(rig.memory, None, media_store=rig.media)
    rig.history.media_store = service.media_store
    entered, release = threading.Event(), threading.Event()
    errors, results = [], []
    def pause():
        entered.set()
        assert release.wait(3)
    rig.after_get = pause
    def read():
        try:
            results.append(rig.history.request("photo", {"session_id": rig.identifier}, access_check=rig.guard))
        except MODULE.ControlError as error:
            errors.append(error.code)
    with patch("sage_lantern.device_access.device_unlocked", return_value=True):
        thread = threading.Thread(target=read)
        thread.start()
        try:
            assert entered.wait(3)
            assert service.stop(timeout=0.01) is False
        finally:
            release.set()
            thread.join(3)
        assert not thread.is_alive() and not results and errors == ["history_unavailable"]
        assert service.stop(timeout=0) is True
        with pytest.raises(MODULE.ControlError):
            rig.history.request("photo", {"session_id": rig.identifier}, access_check=rig.guard)


def test_injected_protected_service_selects_tracked_media(rig):
    rig.memory.protected_storage = True
    rig.memory.media_store = rig.media
    loop = MODULE.VoiceLoop(SimpleNamespace(max_seconds=30, history_dir=rig.history.directory),
                            history_memory=rig.memory)
    assert loop.history.media_mode == "protected" and loop.history.media_store is rig.memory.media_store
    assert loop.control.history_media_check == loop.history.require_media
    assert loop.control.correction_journal == rig.memory.correction_operation
    assert not rig.history.media_directory.exists()


@pytest.mark.parametrize("failure", ["media", "methods", "directory", "raw_override"])
def test_protected_injected_dependencies_never_fallback(rig, failure):
    rig.memory.protected_storage = True
    rig.memory.media_store = rig.media
    args = SimpleNamespace(max_seconds=30, history_dir=rig.history.directory)
    extra = {}
    if failure == "media":
        rig.memory.media_store = None
    elif failure == "methods":
        rig.memory.media_store = object()
    elif failure == "directory":
        args.history_dir = None
    else:
        extra["history_media_store"] = object()
    with pytest.raises(ValueError):
        MODULE.VoiceLoop(args, history_memory=rig.memory, **extra)


def test_protected_factory_runs_without_legacy_conversation_directory(rig, tmp_path):
    config_path = tmp_path / "synthetic.toml"
    config_path.touch()
    config = SimpleNamespace(protected_storage=True, conversation_dir=None)
    rig.memory.protected_storage = True
    rig.memory.media_store = rig.media
    with patch("sage_lantern.config.RuntimeConfig.load", return_value=config), patch(
            "sage_lantern.conversation_service.ConversationService.from_config", return_value=rig.memory) as factory:
        loop = MODULE.VoiceLoop(SimpleNamespace(max_seconds=30, history_dir=rig.history.directory, config=config_path))
    factory.assert_called_once_with(config)
    assert loop.history.media_mode == "protected" and loop.history.media_store is rig.media


@pytest.mark.parametrize("injected", [True, False])
def test_protected_config_rejects_legacy_service(rig, tmp_path, injected):
    config_path = tmp_path / "synthetic.toml"
    config_path.touch()
    config = SimpleNamespace(protected_storage=True, conversation_dir=None)
    rig.memory.media_store = rig.media
    with patch("sage_lantern.config.RuntimeConfig.load", return_value=config), patch(
            "sage_lantern.conversation_service.ConversationService.from_config", return_value=rig.memory):
        with pytest.raises(ValueError):
            MODULE.VoiceLoop(SimpleNamespace(max_seconds=30, history_dir=rig.history.directory, config=config_path),
                            history_memory=rig.memory if injected else None)


def test_legacy_factory_configuration_unchanged(rig, tmp_path):
    config_path = tmp_path / "synthetic.toml"
    config_path.touch()
    config = SimpleNamespace(conversation_dir=tmp_path / "legacy")
    with patch("sage_lantern.config.RuntimeConfig.load", return_value=config), patch(
            "sage_lantern.conversation_service.ConversationService.from_config", return_value=rig.memory) as factory:
        loop = MODULE.VoiceLoop(SimpleNamespace(max_seconds=30, history_dir=rig.history.directory, config=config_path))
    factory.assert_called_once_with(config)
    assert loop.history.media_mode == "legacy" and loop.history.media_store is None
