import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from sage_lantern import mesh_voice_ipc
import test_lantern_idle_suspend_flow as flow


@pytest.fixture
def harness():
    value = flow.MockedPlatform(methodName="runTest")
    value.setUp()
    try:
        yield value
    finally:
        value.doCleanups()


def test_sleep_uses_supervisor_drain_before_other_stops(harness):
    appliance = harness.appliance()
    harness.receipts(appliance)
    harness.replace(flow.MODULE, "model_idle", return_value=True)
    harness.system.stop_verified.side_effect = lambda service: (harness.events.append(service), SimpleNamespace(confirmed=True))[1]
    with patch.object(mesh_voice_ipc, "managed", return_value=True), patch.object(
            mesh_voice_ipc, "root_control", side_effect=lambda *args: harness.events.append(args)):
        assert appliance.quiesce() is True
    assert harness.events[0] == ("drain", appliance.transition_id)
    assert flow.MODULE.VOICE not in harness.events
    assert flow.MODULE.CAMERA in harness.events
    harness.publish.assert_not_called()


def test_supervisor_unknown_blocks_suspend_and_private_stops(harness):
    appliance = harness.appliance()
    harness.receipts(appliance)
    with patch.object(mesh_voice_ipc, "managed", return_value=True), patch.object(
            mesh_voice_ipc, "root_control", side_effect=TimeoutError()):
        with pytest.raises(TimeoutError):
            appliance.quiesce()
    assert appliance.drained is False
    harness.system.stop_verified.assert_not_called()
    harness.kiosk.quiesce.assert_not_called()


def test_resume_starts_locked_auth_then_supervised_voice(harness):
    appliance = harness.appliance()
    calls = []
    harness.command.side_effect = lambda *args, **kwargs: calls.append(args[0]) or ""
    with patch.object(mesh_voice_ipc, "managed", return_value=True), patch.object(
            mesh_voice_ipc, "root_control", side_effect=lambda *args: calls.append(args)):
        assert appliance.start_locked() is True
    assert calls[0] == ["/usr/bin/systemctl", "start", flow.MODULE.AUTH]
    assert calls[1] == ("resume", appliance.transition_id)
    assert all(flow.MODULE.VOICE not in call for call in calls if isinstance(call, list))


@pytest.mark.parametrize("mode", ["0", "1", "invalid"])
def test_voice_main_explicit_facade_selection_without_elevation(mode):
    script = Path(__file__).parents[1] / "lantern/unit-a-voice-loop.py"
    spec = importlib.util.spec_from_file_location("mesh_voice_main_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    loop = SimpleNamespace(run=lambda: None, request_stop=lambda *args: None, drain_result={"drained": True})
    with patch.object(module.sys, "argv", [str(script)]), patch.dict(module.os.environ, {"SAGE_LANTERN_MESH_BROKER": mode}), \
            patch.object(module, "VoiceLoop", return_value=loop) as factory, patch.object(module.signal, "signal"):
        if mode == "invalid":
            with pytest.raises(SystemExit):
                module.main()
            factory.assert_not_called()
            return
        assert module.main() == 0
        kwargs = factory.call_args.kwargs
        if mode == "1":
            assert type(kwargs["mesh_provider"]) is mesh_voice_ipc.MeshVoiceFacade
            assert factory.call_args.args[0].drain_request == Path("/run/sage-lantern-mesh-control/voice-drain-request.json")
        else:
            assert kwargs == {}
