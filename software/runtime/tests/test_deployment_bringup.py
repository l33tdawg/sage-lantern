import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import sage_lantern.deployment_bringup as module
from sage_lantern.deployment_drain import DeploymentDrainError


def inventory():
    return {name: hashlib.sha256(module.AUDIO_INPUTS[name]).hexdigest()
            if name in module.AUDIO_INPUTS else "a" * 64 for name in module.REQUIRED}


@pytest.mark.parametrize("path", list(module.AUDIO_INPUTS))
@pytest.mark.parametrize("fault", ["missing", "mutated"])
def test_exact_audio_pair_required_before_actions(path, fault):
    value = inventory()
    if fault == "missing":
        del value[path]
    else:
        value[path] = hashlib.sha256(module.AUDIO_INPUTS[path] + b"\n").hexdigest()
    with pytest.raises(DeploymentDrainError):
        module.ColdBrokerBootstrap(value)


def test_audio_pair_is_required_not_optional():
    assert set(module.AUDIO_INPUTS) <= module.REQUIRED
    assert not set(module.AUDIO_INPUTS) & module.OPTIONAL
    assert module.ColdBrokerBootstrap(inventory()).inventory == inventory()


@pytest.mark.parametrize("fault", [None, "guard_bytes", "alsa_bytes", "loaded_guard_missing", "unknown_loaded_guard"])
@pytest.mark.parametrize('journal', [False, True])
def test_audio_closure_rechecks_actual_bytes_and_loaded_paths(monkeypatch, fault, journal):
    from sage_lantern import device_access, mesh_daemon
    raw = {name: b"synthetic reviewed input" for name in module.REQUIRED}
    raw.update(module.AUDIO_INPUTS)
    if journal:
        raw[module.RADIO_JOURNAL] = (Path(__file__).parents[2] / 'systemd/sage-lantern-mesh-runtime.service.d/25-radio-commission-journal.conf').read_bytes()
    radio = module.SYSTEMD + module.BROKER + ".d/20-radio.conf"
    raw[radio] = b"synthetic radio policy"
    owner = module.ColdBrokerBootstrap({name: hashlib.sha256(data).hexdigest() for name, data in raw.items()})
    if fault == "guard_bytes":
        raw[str(module.GUARD_PATH)] += b"\nExecCondition=\n"
    if fault == "alsa_bytes":
        raw[str(module.ALSA_PATH)] += b"\n"
    monkeypatch.setattr(mesh_daemon.MeshDaemon, "handle", lambda *args: "verify-deployment-active")
    monkeypatch.setattr(module, "read_public", lambda path: raw[path])
    monkeypatch.setattr(mesh_daemon, "load_config", lambda: object())
    monkeypatch.setattr(mesh_daemon, "device_policy", lambda config: raw[radio].decode())
    monkeypatch.setattr(device_access, "_marker_state", lambda path: True)
    monkeypatch.setattr(module.os.path, "lexists", lambda path: journal and str(path) == module.RADIO_JOURNAL)
    def show(service, deadline, fields):
        paths = {name for name in owner.inventory if name.startswith(module.SYSTEMD + service + ".d/")}
        if service == module.VOICE:
            if fault == "loaded_guard_missing":
                paths.remove(str(module.GUARD_PATH))
            if fault == "unknown_loaded_guard":
                paths.add(module.SYSTEMD + module.VOICE + ".d/51-arbitrary.conf")
        return dict(FragmentPath=module.SYSTEMD + service, LoadState="loaded",
                    NeedDaemonReload="no", Restart="no", DropInPaths=" ".join(sorted(paths)))
    owner.system = SimpleNamespace(clock=lambda: 0, _show=show,
                                   verify_sleep_idle=lambda: SimpleNamespace(confirmed=True))
    if fault is None:
        assert owner.inputs() is True
    else:
        with pytest.raises(DeploymentDrainError):
            owner.inputs()


@pytest.mark.parametrize("fault", ["missing", "unknown", "hash"])
def test_inventory_is_bounded(fault):
    value = inventory()
    if fault == "missing":
        value.pop(next(iter(value)))
    elif fault == "unknown":
        value["/var/lib/sage-lantern/identity/private.key"] = "a" * 64
    else:
        value[next(iter(value))] = "z" * 64
    with pytest.raises(DeploymentDrainError):
        module.ColdBrokerBootstrap(value)


@pytest.mark.parametrize('digest', ['0' * 64, hashlib.sha256(b'[Service]\nReadWritePaths=/\n').hexdigest()])
def test_radio_journal_optional_path_never_accepts_unreviewed_hash(digest):
    value = inventory()
    value[module.RADIO_JOURNAL] = digest
    with pytest.raises(DeploymentDrainError):
        module.ColdBrokerBootstrap(value)


def test_legacy_inventory_refuses_hidden_installed_journal(monkeypatch):
    owner = module.ColdBrokerBootstrap(inventory())
    monkeypatch.setattr(module.os.path, 'lexists', lambda path: str(path) == module.RADIO_JOURNAL)
    with pytest.raises(DeploymentDrainError):
        owner.inputs()


@pytest.mark.parametrize("residual", ["socket", "grant", "state", "voice"])
def test_never_adopts_residual_coldboot_state(tmp_path, monkeypatch, residual):
    owner = module.ColdBrokerBootstrap(inventory())
    monkeypatch.setattr(owner, "inputs", lambda: True)
    monkeypatch.setattr(owner, "inactive", lambda service: pytest.fail("residual state must reject first"))
    socket, grant, state = (tmp_path / name for name in ("socket", "grant", "state"))
    monkeypatch.setattr(module, "SOCKET", socket)
    monkeypatch.setattr(module, "START", grant)
    monkeypatch.setattr(module, "STATE", state)
    original = module.os.path.lexists
    if residual == "voice":
        monkeypatch.setattr(module.os.path, "lexists", lambda path:
            str(path) == "/run/sage-lantern-voice" or original(path))
    elif residual == "state":
        state.symlink_to(tmp_path, target_is_directory=True)
    else:
        (socket if residual == "socket" else grant).write_bytes(b"unresolved")
    with pytest.raises(DeploymentDrainError):
        owner.fresh()


@pytest.mark.parametrize("fault", [None, "preflight", "second_preflight", "start", "evidence", "inputs",
                                 "late_success", "late_inputs", "interrupted", "containment"])
def test_start_is_single_broker_action_with_durable_intent(tmp_path, monkeypatch, fault):
    owner = module.ColdBrokerBootstrap(inventory())
    record = tmp_path / "intent.json"
    monkeypatch.setattr(module, "RECORD", record)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    calls = []
    now = [0.0]
    def fresh():
        calls.append("fresh")
        if fault == "preflight" or (fault == "second_preflight" and record.exists()):
            raise DeploymentDrainError()
        return True
    def command(arguments, deadline):
        assert json.loads(record.read_text())["phase"] == "intent"
        calls.append(arguments)
        if fault == "start":
            raise DeploymentDrainError()
    def active(**kwargs):
        assert kwargs["deadline"] == module.START_SECONDS
        calls.append("active")
        if fault == "interrupted":
            raise InterruptedError()
        if fault == "late_success":
            now[0] = 31
        if fault == "evidence":
            now[0] = 31
            raise DeploymentDrainError()
        return {"synthetic": True}
    def inputs(**kwargs):
        assert kwargs["deadline"] == module.START_SECONDS
        if fault in ("inputs", "containment"):
            raise DeploymentDrainError()
        if fault == "late_inputs":
            now[0] = 31
        return True
    monkeypatch.setattr(owner, "fresh", fresh)
    monkeypatch.setattr(owner, "active", active)
    monkeypatch.setattr(owner, "inputs", inputs)
    owner.system = SimpleNamespace(clock=lambda: now[0], _command=command)
    monkeypatch.setattr(owner, "contain", lambda: calls.append("contain") or fault != "containment")
    monkeypatch.setattr(module, "read_json", lambda path, **kwargs: json.loads(path.read_text()))
    monkeypatch.setattr(module, "publish", lambda path, value: path.write_text(json.dumps(value)))
    real_path = module.Path
    class BootPath:
        def read_text(self):
            return "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(module, "Path", lambda path: BootPath() if path == "/proc/sys/kernel/random/boot_id" else real_path(path))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    if fault is None:
        result = owner.start()
        assert result["broker_active"] and not result["capture_verified"] and not result["inference_verified"]
        assert json.loads(record.read_text())["phase"] == "complete"
        with pytest.raises(FileExistsError):
            owner.start()
    else:
        with pytest.raises((DeploymentDrainError, InterruptedError)):
            owner.start()
        assert record.exists() is (fault != "preflight")
        if record.exists():
            failure = json.loads(record.read_text())
            assert failure["phase"] == "failed"
            assert failure["recovery_required"] is True
            assert failure["containment_verified"] is (fault not in ("second_preflight", "containment"))
    actions = [value for value in calls if isinstance(value, list)]
    assert actions == ([] if fault in ("preflight", "second_preflight") else [["start", "--no-block", module.BROKER]])
    assert calls.count("contain") == (0 if fault in (None, "preflight", "second_preflight") else 1)


def test_legacy_daemon_refuses_before_any_file_or_service_action(monkeypatch):
    from sage_lantern import mesh_daemon
    owner = module.ColdBrokerBootstrap(inventory())
    monkeypatch.setattr(mesh_daemon.MeshDaemon, "handle", lambda *args: None)
    monkeypatch.setattr(module, "read_public", lambda path: pytest.fail("legacy daemon must reject first"))
    with pytest.raises(DeploymentDrainError):
        owner.inputs()


@pytest.mark.parametrize("fault", [None, "sleep_guard", "llm_inactive", "llm_changed", "cgroup"])
def test_fresh_runs_backend_prerequisites_before_intent(tmp_path, monkeypatch, fault):
    from sage_lantern.suspend_system import LLM, FENCE
    owner = module.ColdBrokerBootstrap(inventory())
    record = tmp_path / "intent.json"
    monkeypatch.setattr(module, "RECORD", record)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os.path, "lexists", lambda path: False)
    monkeypatch.setattr(owner, "inputs", lambda: True)
    events = []
    monkeypatch.setattr(owner, "inactive", lambda service: events.append(service))
    system = owner.system
    system.uid = lambda: 0
    system.platform = lambda: "linux"
    system.read_file = lambda path, limit: b"cpu memory\n"
    llm_reads = []
    def show(service, deadline, fields):
        if service == LLM:
            llm_reads.append(service)
            return dict(LoadState="loaded", ActiveState="inactive" if fault == "llm_inactive" else "active",
                        Job="0", MainPID="222" if fault == "llm_changed" and len(llm_reads) > 1 else "111")
        assert service == module.VOICE
        return dict(LoadState="loaded", ActiveState="inactive", Job="0", ControlGroup="",
                    KillMode="control-group", Slice="system.slice")
    monkeypatch.setattr(system, "_show", show)
    def execute(argv, deadline):
        assert argv[-1] == "Conditions"
        events.append("guard")
        if fault == "sleep_guard":
            return 'a(sbbsi) 0'
        return 'a(sbbsi) 1 "ConditionPathExists" false true "' + str(FENCE) + '" 0'
    monkeypatch.setattr(system, "_execute", execute)
    def empty(group, deadline):
        events.append("empty")
        if fault == "cgroup":
            raise ValueError()
    monkeypatch.setattr(system, "_empty_tree", empty)
    monkeypatch.setattr(system, "_command", lambda *args: pytest.fail("must not start services"))
    if fault is None:
        assert owner.fresh() is True
        assert events[:2] == [module.BROKER, module.VOICE]
        assert "guard" in events and "empty" in events
        assert len(llm_reads) == 2
    else:
        with pytest.raises(DeploymentDrainError):
            owner.start()
    assert not record.exists()


@pytest.mark.parametrize("fault", [None, "broker", "voice", "grant", "stop_reply"])
def test_cancellation_waits_for_broker_before_final_voice_stop(monkeypatch, fault):
    owner = module.ColdBrokerBootstrap(inventory())
    now = [0.0]
    events = []
    running = {module.BROKER: True, module.VOICE: False}
    broker_observations = 0
    def command(arguments, deadline):
        assert arguments[:2] == ["stop", "--no-block"]
        service = arguments[-1]
        events.append(("stop", service))
        if service == module.VOICE and fault != "voice":
            running[service] = False
        if fault == "stop_reply":
            raise TimeoutError()
    def inactive(service, deadline):
        nonlocal broker_observations
        events.append(("verify", service))
        if service == module.BROKER:
            broker_observations += 1
            if broker_observations == 1:
                running[module.VOICE] = True
                raise DeploymentDrainError()
            if fault != "broker":
                running[service] = False
        module.require(not running[service])
    owner.system = SimpleNamespace(clock=lambda: now[0], _command=command)
    monkeypatch.setattr(owner, "inactive", inactive)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(module.os.path, "lexists", lambda path: fault == "grant")
    assert owner.contain() is (fault in (None, "stop_reply"))
    assert events[0] == ("stop", module.BROKER)
    assert events.count(("stop", module.VOICE)) == 1
    assert events.index(("stop", module.VOICE)) > events.index(("verify", module.BROKER))
    assert now[0] <= module.CONTAIN_SECONDS
    assert not any(event[0] == "start" for event in events)


@pytest.mark.parametrize("fault", [None, "job", "pid", "active", "cgroup", "changed", "deadline"])
def test_inactive_evidence_requires_empty_stable_cgroup(monkeypatch, fault):
    owner = module.ColdBrokerBootstrap(inventory())
    value = dict(ActiveState="inactive", MainPID="0", Job="", ControlGroup="",
                 Slice="system.slice", KillMode="control-group")
    if fault == "job":
        value["Job"] = "123"
    if fault == "pid":
        value["MainPID"] = "42"
    if fault == "active":
        value["ActiveState"] = "active"
    observations = 0
    def show(*args):
        nonlocal observations
        observations += 1
        return {**value, "MainPID": "999"} if fault == "changed" and observations == 2 else value
    def empty(group, deadline):
        assert group == "/system.slice/" + module.BROKER
        if fault == "cgroup":
            raise ValueError("populated")
    owner.system = SimpleNamespace(clock=lambda: 10 if fault == "deadline" else 0,
        _show=show, _no_job=lambda value: value == "", _cgroup=lambda value: value, _empty_tree=empty)
    if fault is None:
        owner.inactive(module.BROKER, deadline=5)
        assert observations == 2
    else:
        with pytest.raises((DeploymentDrainError, ValueError)):
            owner.inactive(module.BROKER, deadline=5)
