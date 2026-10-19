"""The audio evidence accepts the reviewed local question lane, nothing else."""

from sage_lantern import audio_readiness as module


REVIEWED = ("[Unit]\n"
            "ConditionPathExists=\n"
            "ConditionPathExists=/var/lib/sage-lantern/LOCAL_VOICE_READY\n"
            "ConditionPathExists=!/run/sage-lantern-sleep.fenced\n"
            "\n"
            "[Service]\n"
            "ExecCondition=\n"
            "Restart=on-failure\n")


def gate(tmp_path, monkeypatch, text):
    path = tmp_path / "60-local-question-lane.conf"
    path.write_text(text)
    monkeypatch.setattr(module, "QUESTION_GATE_PATH", path)
    monkeypatch.setattr(module, "trusted_bytes", lambda value: path.read_bytes())
    return path


def test_reviewed_local_question_gate_is_recognised(tmp_path, monkeypatch):
    path = gate(tmp_path, monkeypatch, REVIEWED)
    assert module.question_gate_present([str(path)]) is True


def test_absent_gate_or_another_path_is_not_a_replacement(tmp_path, monkeypatch):
    path = gate(tmp_path, monkeypatch, REVIEWED)
    assert module.question_gate_present(["/etc/systemd/system/sage-lantern-voice-loop.service.d/90-other.conf"]) is False
    path.unlink()
    assert module.question_gate_present([str(path)]) is False


def test_a_gate_that_changes_the_conditions_or_adds_a_line_refuses(tmp_path, monkeypatch):
    for text in (REVIEWED.replace("/var/lib/sage-lantern/LOCAL_VOICE_READY", "/tmp/anything"),
                 REVIEWED + "ExecStart=/bin/sh\n",
                 REVIEWED.replace("Restart=on-failure", "Restart=always"),
                 "[Service]\nExecCondition=\n"):
        path = gate(tmp_path, monkeypatch, text)
        assert module.question_gate_present([str(path)]) is False


def test_local_lane_constants_match_the_installed_drain_path():
    assert module.LOCAL_DRAIN == "/run/sage-lantern-voice-drain-request.json"
    assert module.MESH_DRAIN == "/run/sage-lantern-mesh-control/voice-drain-request.json"
    assert module.QUESTION_GATE_PATH.name == "60-local-question-lane.conf"
