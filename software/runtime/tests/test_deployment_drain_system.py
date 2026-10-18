import subprocess

import pytest

from sage_lantern.suspend_system import (BUSCTL, CGROUP, DEPLOYMENT_FENCE, FENCE, IDLE_SUSPEND, LLM,
                                         SYSTEMCTL, VLM)
from test_suspend_system import FakeSystem


MODEL_STOPS = (IDLE_SUSPEND, LLM, VLM)


class DeploymentSystem(FakeSystem):
    def __init__(self):
        super().__init__()
        for unit in MODEL_STOPS:
            self.units[unit] = dict(LoadState="loaded", ActiveState="active", Job="", MainPID="123",
                UnitFileState="enabled",
                InvocationID="a" * 32, KillMode="control-group", Slice="system.slice",
                ControlGroup="/system.slice/" + unit,
                Conditions=f'a(sbbsi) 1 "ConditionPathExists" false true "{DEPLOYMENT_FENCE if unit == IDLE_SUSPEND else FENCE}" 0')
            self.directories[CGROUP / "system.slice" / unit] = []

    def run(self, argv, *, timeout):
        if argv[0] == BUSCTL:
            for unit in MODEL_STOPS:
                encoded = "".join(character if character.isalnum() else f"_{ord(character):02x}" for character in unit)
                if argv[-3].endswith("/" + encoded):
                    self.calls.append((argv, timeout))
                    return subprocess.CompletedProcess(argv, 0, self.units[unit]["Conditions"].encode())
        if argv[0] == SYSTEMCTL and argv[1] == "stop" and argv[2] in MODEL_STOPS:
            self.calls.append((argv, timeout))
            self.units[argv[2]].update(ActiveState="inactive", MainPID="0")
            self.after_stop()
            return subprocess.CompletedProcess(argv, 0, b"")
        return super().run(argv, timeout=timeout)


@pytest.mark.parametrize("unit", MODEL_STOPS)
def test_exact_stop_and_read_only_recheck(unit):
    fake = DeploymentSystem()
    system = fake.adapter()
    assert system.deployment_stop_verified(unit, expected_pid=123, expected_invocation="a" * 32).confirmed
    mutations = [call for call, _ in fake.calls if call[1] == "stop"]
    assert mutations == [[SYSTEMCTL, "stop", unit]]
    count = len(fake.calls)
    assert system.deployment_inactive_verified(unit).confirmed
    assert not any(call[1] == "stop" for call, _ in fake.calls[count:])


@pytest.mark.parametrize("field,value", [("MainPID", "124"), ("InvocationID", "b" * 32),
    ("KillMode", "process"), ("Job", "22"), ("Conditions", 'a(sbbsi) 0')])
@pytest.mark.parametrize("unit", MODEL_STOPS)
def test_bad_identity_or_guard_denies_before_stop(unit, field, value):
    fake = DeploymentSystem()
    fake.units[unit][field] = value
    assert not fake.adapter().deployment_stop_verified(unit, expected_pid=123, expected_invocation="a" * 32).confirmed
    assert not any(call[1] == "stop" for call, _ in fake.calls)


@pytest.mark.parametrize("unit", MODEL_STOPS)
def test_descendant_process_blocks_join(unit):
    fake = DeploymentSystem()
    group = CGROUP / "system.slice" / unit
    fake.directories[group] = ["child"]
    fake.directories[group / "child"] = []
    fake.files[group / "child/cgroup.procs"] = b"999\n"
    assert not fake.adapter().deployment_stop_verified(unit, expected_pid=123, expected_invocation="a" * 32).confirmed


@pytest.mark.parametrize("unit", MODEL_STOPS)
def test_restart_invalidates_even_if_now_inactive(unit):
    fake = DeploymentSystem()
    system = fake.adapter()
    assert system.deployment_stop_verified(unit, expected_pid=123, expected_invocation="a" * 32).confirmed
    fake.units[unit]["InvocationID"] = "b" * 32
    assert not system.deployment_inactive_verified(unit).confirmed


def test_idle_stop_cannot_disrupt_model():
    fake = DeploymentSystem()
    fake.after_stop = lambda: fake.units[LLM].update(MainPID="456")
    assert not fake.adapter().deployment_stop_verified(IDLE_SUSPEND, expected_pid=123, expected_invocation="a" * 32).confirmed


def test_vision_stop_cannot_disrupt_the_chat_model():
    fake = DeploymentSystem()
    fake.after_stop = lambda: fake.units[LLM].update(MainPID="456")
    assert not fake.adapter().deployment_stop_verified(VLM, expected_pid=123, expected_invocation="a" * 32).confirmed


def test_chat_model_stop_cannot_disrupt_the_vision_model():
    fake = DeploymentSystem()
    fake.after_stop = lambda: fake.units[VLM].update(MainPID="456")
    assert not fake.adapter().deployment_stop_verified(LLM, expected_pid=123, expected_invocation="a" * 32).confirmed


def test_disabled_vision_unit_is_not_a_model_stop_target():
    fake = DeploymentSystem()
    fake.units[VLM].update(UnitFileState="disabled", ActiveState="inactive", MainPID="0")
    system = fake.adapter()
    assert system.deployment_stop_verified(IDLE_SUSPEND, expected_pid=123, expected_invocation="a" * 32).confirmed


def test_ordinary_stop_allowlist_unchanged():
    fake = DeploymentSystem()
    system = fake.adapter()
    assert not system.stop_verified(LLM).confirmed
    assert not system.stop_verified(IDLE_SUSPEND).confirmed
    assert not system.deployment_inactive_verified(LLM).confirmed
    assert fake.units[LLM]["ActiveState"] == "active"
