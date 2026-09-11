import array
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import uuid
from types import SimpleNamespace

import pytest

import sage_lantern.audio_readiness as module
from sage_lantern.voice_listener_ack import ListenerAcknowledgements
from test_storage_supervisor import setup


def waveform():
    return array.array("h", [200, -200] * 32000).tobytes()


@pytest.mark.parametrize("raw", [b"", bytes(module.BYTE_COUNT), waveform()[:-2], waveform() + b"xx",
    array.array("h", [32767, -32768] * 32000).tobytes(), array.array("h", [100] * 64000).tobytes()])
def test_incomplete_silent_clipped_or_constant_capture_refuses(raw):
    with pytest.raises(RuntimeError):
        module.samples(raw)


def test_only_aggregate_sample_statistics_leave_capture():
    assert module.samples(waveform()) == dict(frames=32000, channels=2, rate=16000,
        sample_format="S16_LE", rms=200, clipped=0)


@pytest.mark.parametrize("fault", [None, "expired", "future", "boot", "config", "runtime", "usb", "pending", "marker", "capture"])
def test_status_and_start_guard_reject_stale_or_changed_evidence(monkeypatch, fault):
    observed = dict(unit_id="unit-a", audio={"card": "bound"}, boot_id="boot", inputs={"config": "a" * 64}, services={"sage": "invocation", "sage-lantern-llm.service": {"ActiveState": "active"}}, hardware={"inode": 1})
    receipt = dict(schema="sage.lantern.audio-capture.v1", snapshot=observed, captured_at=100,
        expires_at=100 + module.TTL, capture=module.samples(waveform()))
    current = json.loads(json.dumps(observed))
    if fault == "boot":
        current["boot_id"] = "new"
    elif fault == "config":
        current["inputs"]["config"] = "b" * 64
    elif fault == "runtime":
        current["services"]["sage"] = "new"
    elif fault == "usb":
        current["hardware"]["inode"] = 2
    elif fault == "capture":
        receipt["capture"]["rms"] = 0
    commissioning = dict(schema="sage.lantern.audio-commissioned.v1", commissioning_id=str(uuid.uuid4()),
        binding=module.commissioned_binding(observed), capture_evidence_sha256=module.digest(receipt))
    marker = dict(schema="sage.lantern.fresh-voice-gate.v2", commission_sha256=module.digest(commissioning))
    if fault == "marker":
        marker["commission_sha256"] = "f" * 64
    monkeypatch.setattr(module, "snapshot", lambda: current)
    monkeypatch.setattr(module, "boottime", lambda: 700 if fault == "expired" else 99 if fault == "future" else 101)
    monkeypatch.setattr(module.os.path, "lexists", lambda path: fault == "pending")
    monkeypatch.setattr(module, "read_json", lambda path, **kwargs:
        receipt if path == module.RECEIPT else commissioning if path == module.COMMISSION else marker)
    assert module.capture_verified() is (fault is None)
    assert module.admission_verified() is (fault not in ("config", "pending", "marker", "capture"))


def test_guard_is_an_additive_condition_not_an_execstart_or_mesh_bypass():
    assert module.GUARD == ("[Service]\nEnvironment=ALSA_CONFIG_PATH=/etc/sage-lantern/audio-capture-alsa.conf\n"
        "ExecCondition=+/opt/sage-lantern/venv/bin/python -m sage_lantern.audio_readiness --admit\n")
    assert "ExecStart" not in module.GUARD and "ConditionPathExists=" not in module.GUARD


@pytest.mark.parametrize("fault", [None, "active", "job", "pid", "occupied", "changed", "missing", "sage_inactive"])
def test_commissioning_allows_only_verified_inactive_model(monkeypatch, fault):
    service = "sage-lantern-personal-sage.service" if fault == "sage_inactive" else "sage-lantern-llm.service"
    identity = dict(LoadState="loaded", ActiveState="inactive", MainPID="0", InvocationID="", Job="")
    if fault == "active":
        identity.update(ActiveState="active", MainPID="42", InvocationID="a" * 32)
    if fault == "job":
        identity["Job"] = "42"
    if fault == "pid":
        identity["MainPID"] = "42"
    if fault == "missing":
        identity["LoadState"] = "not-found"
    events = []
    def show(name, deadline, fields):
        events.append("show")
        value = dict(identity)
        if fault == "changed" and events.count("show") == 2:
            value["ActiveState"] = "active"
        return value
    def inactive(system, name):
        assert name == "sage-lantern-llm.service"
        events.append("empty_cgroup")
        if fault == "occupied":
            raise RuntimeError
    monkeypatch.setattr(module, "inactive", inactive)
    system = SimpleNamespace(_show=show, clock=lambda: 0, _no_job=lambda value: value in ("", "0"))
    if fault in (None, "active"):
        assert module.commissioning_service(system, service) == identity
        assert ("empty_cgroup" in events) is (fault is None)
    else:
        with pytest.raises(RuntimeError):
            module.commissioning_service(system, service)


@pytest.mark.parametrize("active", [True, False])
def test_normal_admission_still_requires_active_model(monkeypatch, active):
    observed = dict(unit_id="unit-a", audio={}, inputs={},
                    services={"sage-lantern-llm.service": {"ActiveState": "active" if active else "inactive"}})
    monkeypatch.setattr(module, "snapshot", lambda: observed)
    monkeypatch.setattr(module, "commissioned_receipt", lambda: {"binding": module.commissioned_binding(observed)})
    monkeypatch.setattr(module.os.path, "lexists", lambda path: False)
    assert module.admission_verified() is active
    assert module.admission_verified(commissioning=True) is True


@pytest.mark.parametrize("fault", [None, "pin", "existing", "capture", "changed", "sleep", "running", "final_check"])
def test_explicit_validation_never_mints_gate_on_failure(tmp_path, monkeypatch, fault):
    observed = dict(unit_id="unit-a", audio={"card": "bound"}, inputs={"reviewed": "a" * 64})
    marker, receipt, pending = (tmp_path / name for name in ("marker", "receipt", "pending"))
    monkeypatch.setattr(module, "MARKER", marker)
    monkeypatch.setattr(module, "RECEIPT", receipt)
    monkeypatch.setattr(module, "PENDING", pending)
    monkeypatch.setattr(module, "COMMISSION", tmp_path / "commissioned")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.sys, "platform", "linux")
    checks = []
    def snapshot():
        checks.append("snapshot")
        return {"changed": True} if fault == "changed" and checks.count("snapshot") > 1 else observed
    def inactive(system, service):
        checks.append(service)
        if fault == "running":
            raise RuntimeError("synthetic active producer")
    def capture(value):
        checks.append("capture")
        assert pending.exists() and not marker.exists()
        if fault == "capture":
            raise RuntimeError("synthetic capture failure")
        return module.samples(waveform())
    monkeypatch.setattr(module, "snapshot", snapshot)
    monkeypatch.setattr(module, "inactive", inactive)
    monkeypatch.setattr(module, "record_audio", capture)
    monkeypatch.setattr(module, "SuspendSystem", lambda: SimpleNamespace(
        verify_sleep_idle=lambda: SimpleNamespace(confirmed=fault != "sleep")))
    times = iter((100, 102, 102))
    monkeypatch.setattr(module, "boottime", lambda: next(times))
    monkeypatch.setattr(module, "publish", lambda path, value, **kwargs: path.write_text(json.dumps(value)))
    monkeypatch.setattr(module, "capture_verified", lambda: fault != "final_check")
    monkeypatch.setattr(module, "admission_verified", lambda **kwargs: kwargs == {"commissioning": True})
    if fault == "existing":
        marker.write_bytes(b"legacy evidence must survive")
    expected = "f" * 64 if fault == "pin" else module.digest(observed)
    if fault is None:
        module.validate_capture(expected)
        assert receipt.exists() and marker.exists() and not pending.exists()
        assert json.loads(receipt.read_text())["capture"]["frames"] == 32000
    else:
        with pytest.raises(RuntimeError):
            module.validate_capture(expected)
        assert receipt.exists() is (fault == "final_check")
        assert marker.exists() is (fault in ("existing", "final_check"))
        assert pending.exists() is (fault in ("capture", "changed", "final_check"))
    assert all("start" not in entry and "restart" not in entry for entry in checks)


def test_capture_consent_is_required_before_any_observation(monkeypatch):
    monkeypatch.setattr(module.sys, "argv", ["audio", "--capture", "--expected-inputs-sha256", "a" * 64])
    monkeypatch.setattr(module, "validate_capture", lambda expected: pytest.fail("capture without consent"))
    assert module.main() == 1


@pytest.mark.parametrize("fault", [None, "timeout", "recorder_exit", "cgroup", "still_active"])
def test_capture_command_is_bounded_private_and_joined(monkeypatch, fault):
    events = []
    chunks = iter(waveform()[offset:offset + 8192] for offset in range(0, module.BYTE_COUNT, 8192))
    process = SimpleNamespace(pid=123456, returncode=None,
        stdout=SimpleNamespace(fileno=lambda: 123, close=lambda: events.append("stdout_closed")))
    def wait(timeout):
        process.returncode = 1 if fault == "recorder_exit" else 0
        return process.returncode
    process.wait = wait
    process.poll = lambda: process.returncode
    def popen(command, **kwargs):
        events.append(command)
        assert "--pipe" in command and "--property=RuntimeMaxSec=6" in command
        assert "--setenv=ALSA_CONFIG_PATH=" + str(module.ALSA_PATH) in command
        assert "--property=User=sage-lantern" in command and "--property=PrivateNetwork=yes" in command
        assert "--property=DevicePolicy=closed" in command
        assert "--property=DeviceAllow=/dev/snd/pcmC2D0c rw" in command
        assert command[-5:] == ["-t", "raw", "-d", "2", "-"]
        assert kwargs["stdin"] == module.subprocess.DEVNULL and kwargs["stderr"] == module.subprocess.DEVNULL
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C"}
        return process
    class Selector:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def register(self, *args):
            pass
        def select(self, timeout):
            assert 0 < timeout <= 10
            return [] if fault == "timeout" else [True]
    def empty(group, deadline):
        events.append("cgroup_checked")
        if fault == "cgroup":
            raise RuntimeError("synthetic populated cgroup")
    state = dict(LoadState="not-found", ActiveState="inactive", MainPID="0", Job="")
    if fault == "still_active":
        state.update(LoadState="loaded", ActiveState="active", MainPID="9")
    system = SimpleNamespace(clock=lambda: 100, _show=lambda *args: state,
        _command=lambda arguments, deadline: events.append(arguments), _no_job=lambda value: value == "",
        _cgroup=lambda value: value, _empty_tree=empty)
    monkeypatch.setattr(module, "SuspendSystem", lambda: system)
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(module.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(module.os, "read", lambda *args: next(chunks, b""))
    monkeypatch.setattr(module.os, "killpg", lambda pid, signal: events.append("killed_client"))
    monkeypatch.setattr(module.time, "monotonic", lambda: 100)
    observed = dict(audio={"alsa_device": "hw:CARD=Audio,DEV=0"}, hardware={"nodes": {"/dev/snd/pcmC2D0c": {}}})
    if fault is None:
        assert module.record_audio(observed)["frames"] == 32000
    else:
        with pytest.raises(RuntimeError):
            module.record_audio(observed)
    assert "stdout_closed" in events
    assert ("killed_client" in events) is (fault == "timeout")
    assert ("cgroup_checked" in events) is (fault != "still_active")


@pytest.mark.parametrize("fault", [None, "card", "serial", "vendor", "pcm_type", "other_usb"])
def test_card_capture_node_and_usb_are_the_same_bound_device(tmp_path, monkeypatch, fault):
    def mapped(path):
        return tmp_path / str(path).lstrip("/")
    control = mapped("/dev/snd/controlC2")
    pcm = mapped("/dev/snd/pcmC2D0c")
    control.parent.mkdir(parents=True)
    control.touch()
    pcm.touch()
    stable = mapped("/dev/snd/by-id/usb-reviewed")
    stable.parent.mkdir()
    stable.symlink_to(control)
    card = mapped("/proc/asound/card2/id")
    card.parent.mkdir(parents=True)
    card.write_text("Other" if fault == "card" else "Audio")
    usb = mapped("/sys/devices/usb1/1-2")
    usb.mkdir(parents=True)
    for name, value in (("idVendor", "1234"), ("idProduct", "abcd"), ("serial", "reviewed")):
        (usb / name).write_text("different" if fault == "serial" and name == "serial"
            else "ffff" if fault == "vendor" and name == "idVendor" else value)
    for number in (2, 3):
        path = mapped(f"/sys/dev/char/116:{number}/device")
        path.parent.mkdir(parents=True)
        if fault == "other_usb" and number == 3:
            other = mapped("/sys/devices/usb1/1-3")
            other.mkdir()
            for name in ("idVendor", "idProduct", "serial"):
                (other / name).write_text((usb / name).read_text())
            path.symlink_to(other)
        else:
            path.symlink_to(usb)
    original = Path.lstat
    def lstat(path):
        if path in (control, pcm):
            return SimpleNamespace(st_mode=stat.S_IFREG if fault == "pcm_type" and path == pcm else stat.S_IFCHR,
                st_dev=1, st_ino=2 if path == control else 3, st_rdev=os.makedev(116, 2 if path == control else 3))
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(module, "Path", mapped)
    audio = dict(stable_path="/dev/snd/by-id/usb-reviewed", usb_serial="reviewed", vendor_id="1234",
        product_id="abcd", alsa_device="hw:CARD=Audio,DEV=0")
    if fault is None:
        result = module.hardware(audio)
        assert result["card_id"] == "Audio" and len(result["nodes"]) == 2
    else:
        with pytest.raises(RuntimeError):
            module.hardware(audio)


@pytest.mark.parametrize("fault", [None, "uid", "binding", "epoch", "phase", "producer",
                                 "listener", "second_listener", "numeric_true", "wrong_type", "empty"])
def test_operational_probe_is_live_storage_bound_not_recent_capture(setup, monkeypatch, fault):
    supervisor, backend, state = setup
    backend.uid = 1001
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    monkeypatch.setattr(module, "admission_verified", lambda: fault != "binding")
    monkeypatch.setattr(module, "capture_verified", lambda: pytest.fail("recent capture cannot gate operation"))
    calls = []
    acknowledgement = ListenerAcknowledgements()
    def listener(cache, owner, *, pid, uid, propagate_busy=False):
        assert owner is supervisor and cache is acknowledgement
        assert pid == 901 and uid == backend.instance.uid
        calls.append((pid, uid))
        if fault == "numeric_true":
            return 1
        return fault != "listener" and not (fault == "second_listener" and len(calls) == 2)
    if fault != "empty":
        monkeypatch.setattr(ListenerAcknowledgements, "verified", listener)
    if fault == "wrong_type":
        acknowledgement = SimpleNamespace(verified=lambda *args, **kwargs: True)
    if fault == "epoch":
        state["snapshot"] = replace(state["snapshot"], vault_generation="2")
    elif fault == "phase":
        supervisor.phase = "recovery_required"
    elif fault == "producer":
        backend.instance = replace(backend.instance, pid=999)
    request = dict(op="verify-audio-operational", nonce=str(uuid.uuid4()))
    if fault is None:
        assert module.operational_broker_request(supervisor, request, peer_uid=1001,
                                                listener_ack=acknowledgement) == {
            "result": {"nonce": request["nonce"], "ready": True}}
        assert len(calls) == 2
    else:
        with pytest.raises(RuntimeError):
            module.operational_broker_request(supervisor, request,
                peer_uid=1002 if fault == "uid" else 1001, listener_ack=acknowledgement)
    assert backend.events == ["start_voice"]


@pytest.mark.parametrize("fault", [None, "nonce", "legacy", "numeric_true", "unavailable"])
def test_operational_client_requires_fresh_root_reply(monkeypatch, fault):
    import sage_lantern.mesh_voice_ipc as ipc
    sent, closed = [], []
    connection = SimpleNamespace(settimeout=lambda value: None, close=lambda: closed.append(True))
    def connect():
        if fault == "unavailable":
            raise RuntimeError("synthetic offline broker")
        return connection
    def receive(connection, limit):
        if fault == "legacy":
            return {"result": True}
        return {"result": {"nonce": str(uuid.uuid4()) if fault == "nonce" else sent[0]["nonce"],
                           "ready": 1 if fault == "numeric_true" else True}}
    monkeypatch.setattr(ipc, "connect", connect)
    monkeypatch.setattr(ipc, "send", lambda connection, request: sent.append(request))
    monkeypatch.setattr(ipc, "receive", receive)
    assert module.operational_ready() is (fault is None)
    assert closed == ([] if fault == "unavailable" else [True])


def test_operational_requires_explicit_ack_argument(setup):
    supervisor, backend, state = setup
    with pytest.raises(TypeError, match="listener_ack"):
        module.operational_broker_request(supervisor,
            dict(op="verify-audio-operational", nonce=str(uuid.uuid4())), peer_uid=0)


@pytest.mark.parametrize("fault", [None, "expired", "changed_during_probe"])
def test_operational_consumes_real_bound_ack_cache(setup, monkeypatch, fault):
    from sage_lantern import voice_listener_ack
    supervisor, backend, state = setup
    backend.uid = 1001
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    producer = backend.instance
    acknowledgement = ListenerAcknowledgements()
    acknowledgement._ack = (voice_listener_ack.binding(supervisor, pid=producer.pid, uid=producer.uid),
                            producer.pid, producer.uid, now[0])
    if fault == "expired":
        now[0] += voice_listener_ack.MAX_AGE
    calls = []
    def admission():
        calls.append(True)
        if fault == "changed_during_probe" and len(calls) == 2:
            backend.instance = replace(backend.instance, pid=999)
        return True
    monkeypatch.setattr(module, "admission_verified", admission)
    request = dict(op="verify-audio-operational", nonce=str(uuid.uuid4()))
    if fault is None:
        assert module.operational_broker_request(supervisor, request, peer_uid=0,
            listener_ack=acknowledgement)["result"]["ready"] is True
    else:
        with pytest.raises(RuntimeError):
            module.operational_broker_request(supervisor, request, peer_uid=0, listener_ack=acknowledgement)


@pytest.mark.parametrize("fault", [None, "deadline", "broker_changed"])
def test_operational_io_never_holds_supervisor_locks(setup, monkeypatch, fault):
    supervisor, backend, state = setup
    backend.uid = 1001
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    clock = [100]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def unlocked():
        assert not supervisor._mutex._is_owned()
        assert supervisor._control.acquire(blocking=False)
        supervisor._control.release()
    def admission():
        unlocked()
        return True
    def listener(*args, **kwargs):
        unlocked()
        if fault == "deadline":
            clock[0] = 105
        elif fault == "broker_changed":
            supervisor.broker_id = "d" * 32
        return True
    original_epoch = supervisor._epoch
    def epoch():
        unlocked()
        return original_epoch()
    monkeypatch.setattr(supervisor, "_epoch", epoch)
    monkeypatch.setattr(module, "admission_verified", admission)
    monkeypatch.setattr(ListenerAcknowledgements, "verified", listener)
    acknowledgement = ListenerAcknowledgements()
    request = dict(op="verify-audio-operational", nonce=str(uuid.uuid4()))
    if fault is None:
        assert module.operational_broker_request(supervisor, request, peer_uid=1001,
                                                listener_ack=acknowledgement)["result"]["ready"] is True
    else:
        with pytest.raises(RuntimeError):
            module.operational_broker_request(supervisor, request, peer_uid=1001,
                                             listener_ack=acknowledgement)
