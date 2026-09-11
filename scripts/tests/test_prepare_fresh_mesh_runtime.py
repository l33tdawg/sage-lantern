import importlib.util
import json
import os
import copy
import subprocess
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


SCRIPT = Path(__file__).parents[1] / "lantern/prepare-fresh-mesh-runtime.py"
SPEC = importlib.util.spec_from_file_location("prepare_fresh_mesh_runtime", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def documents():
    config = dict(schema="sage.lantern.mesh-runtime.v1", unit_id="unit-a", voice_uid=994,
        endpoint="http://127.0.0.1:8080", chain_id="sage-personal-test", agent_id="12" * 32,
        identity_file=MODULE.PRIVATE_KEY, receiver_slot="12345678-1234-4234-8234-123456789abc",
        radio=dict(stable_path="/dev/serial/by-id/ReviewedRadio", serial="reviewed", vendor_id="1234",
            product_id="abcd", public_key="34" * 32), peers=[], approve_initial_open=True,
        acknowledge_reset=True, messaging_enabled=False, receive_while_locked=False,
        dispatch_enabled=False, dispatch_while_locked=False)
    initialized = dict(schema="sage.lantern.initialized.v1", unit_id="unit-a", chain_id=config["chain_id"],
        root_id="56" * 32, private_agent_id=config["agent_id"], public_agent_id="78" * 32,
        local_enrollment_committed=False, public_enrolled=False, vault_initialized=False, ready=False, provisioned=False)
    commissioned = dict(schema="sage.lantern.commissioned-bindings.v1", unit_id="unit-a",
        private_chain_id=config["chain_id"], private_agent_id=config["agent_id"], private_identity_path=MODULE.PRIVATE_KEY,
        public_agent_id=initialized["public_agent_id"], public_identity_path="/var/lib/sage-lantern/identity/lantern-public.key",
        public_enrolled=False, ready=False, provisioned=False)
    app = dict(schema="sage.lantern.fresh-app-install.v1", unit_id="unit-a", installed=True,
        lock_required=True, ready=False, provisioned=False)
    fingerprint = MODULE.LEGACY.fingerprint(config["radio"]["public_key"])
    bindings = dict(schema="sage.lantern.unit-plan.v1", unit_id="unit-a", service_account="sage-lantern",
        radio=dict(unit_id="unit-a", usb_serial="reviewed", vendor_id="1234", product_id="abcd",
            stable_path=config["radio"]["stable_path"], public_key=config["radio"]["public_key"], public_fingerprint=fingerprint))
    radio = dict(schema="sage.lantern.meshcore-radio-public.v1", unit_id="unit-a",
        usb={key: value for key, value in config["radio"].items() if key != "public_key"},
        radio_identity=dict(public_key=config["radio"]["public_key"], public_key_sha256=fingerprint))
    return config, initialized, commissioned, app, bindings, radio


def validate(documents):
    return MODULE.validate(*documents, radio_fingerprint=MODULE.LEGACY.fingerprint(documents[0]["radio"]["public_key"]),
        service_uid=994)


def policy(config):
    return dict(chain_id=config['chain_id'], data_dir='/var/lib/sage-lantern/sage-personal/data',
        agent_key_file='/var/lib/sage-lantern/sage-personal/agent.key', rest_addr='127.0.0.1:8080',
        encryption=dict(enabled=True), embedding=dict(provider='hash', dimension=768),
        federation=dict(enabled=False), quorum=dict(enabled=False), voter=dict(enabled=True, required=True, poll_interval='2s'),
        vendored_agent_bootstrap=dict(agent_key_file=MODULE.PRIVATE_KEY, home_domain='local-field-knowledge', clearance=1),
        private_media=dict(enabled=True, actor_bytes=1024, node_bytes=2048, actor_objects=10,
                           node_objects=20, min_free_bytes=4096))


def test_fresh_public_receipts_need_no_legacy_attestations(documents):
    binding = validate(documents)
    assert binding.unit_id == "unit-a" and binding.serial == "reviewed"
    assert "PROVISIONED" not in json.dumps(documents)


@pytest.mark.parametrize("index,field,value", [
    (0, "agent_id", "56" * 32), (0, "identity_file", "/var/lib/sage-lantern/identity/other.key"),
    (0, "voice_uid", 995), (0, "chain_id", "sage-personal-other"), (0, "acknowledge_reset", False),
    (0, "receive_while_locked", True), (0, "dispatch_while_locked", True),
    (1, "root_id", "12" * 32), (1, "public_agent_id", "00" * 32), (1, "ready", True),
    (1, "local_enrollment_committed", True), (1, "unit_id", "unit-b"),
    (2, "public_agent_id", "90" * 32), (2, "ready", 0),
    (3, "installed", 1), (3, "lock_required", False), (3, "unit_id", "unit-b"),
    (4, "service_account", "root"), (5, "unit_id", "unit-b")])
def test_changed_identity_policy_or_receipt_refuses(documents, index, field, value):
    documents[index][field] = value
    with pytest.raises((RuntimeError, ValueError)):
        validate(documents)


def test_radio_binding_must_match_app_installer(documents):
    documents[4]["radio"]["usb_serial"] = "different"
    with pytest.raises(RuntimeError):
        validate(documents)


@pytest.mark.parametrize("fault", [None, "manifest_pin", "app_pin", "commission_pin", "output", "pending",
                                 'current_sage_pin', 'current_sage_policy_pin', 'runtime', 'receipt',
                                 'genesis', 'config_changed'])
def test_immutable_provenance_and_current_policy_pins_are_mandatory(documents, monkeypatch, fault):
    config, initialized, commissioned, app, bindings, radio = documents
    raw = {"manifest": MODULE.encoded(config), "radio_inventory": MODULE.encoded(radio),
        "initialized": MODULE.encoded(initialized), "commissioned": MODULE.encoded(commissioned),
        "app": MODULE.encoded(app), "bindings": MODULE.encoded(bindings),
        "unit_metadata": b"UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n",
        'runtime': b'[sage]\n', 'current_sage_policy': MODULE.encoded(policy(config)),
        'current_sage': yaml.safe_dump(policy(config)).encode()}
    receipt = dict(schema='sage.lantern.commission-complete.v1', unit_id='unit-a',
        transaction_id='12345678-1234-4234-8234-123456789abc', plan_sha256='c' * 64,
        initial_config_sha256='d' * 64, manifest_sha256="a" * 64, output_sha256={
        'sage.yaml': 'e' * 64, 'lantern.toml': MODULE.sha(raw['runtime']),
        "initialized-public.json": MODULE.sha(raw["initialized"]), "bindings.json": MODULE.sha(raw["commissioned"])})
    if fault == "output":
        receipt["output_sha256"]["initialized-public.json"] = "b" * 64
    if fault == 'runtime':
        raw['runtime'] = b'changed'
    if fault == 'receipt':
        receipt['extra'] = True
    raw["commission"] = MODULE.encoded(receipt)
    names = dict(manifest="manifest.json", radio_inventory="radio.json", initialized="initialized-public.json",
        commissioned="commissioned-bindings.json", app="appliance-installed.json", bindings="appliance-bindings.json",
        unit_metadata="unit.env", commission="commissioning-complete.json", runtime='lantern.toml',
        current_sage_policy='policy.json')
    by_name = {filename: raw[name] for name, filename in names.items()}
    monkeypatch.setattr(MODULE.LEGACY, "read", lambda path, owner: by_name[path.name])
    monkeypatch.setattr(MODULE.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=994))
    monkeypatch.setattr(MODULE, 'current_yaml', lambda uid: raw['current_sage'])
    monkeypatch.setattr(MODULE, 'service_document', lambda *args: MODULE.encoded(
        {'chain_id': 'wrong-chain' if fault == 'genesis' else config['chain_id']}))
    def native_check(expected):
        if fault == 'config_changed':
            raw['current_sage'] += b'\n'
    monkeypatch.setattr(MODULE, 'native_config_check', native_check)
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: fault == 'pending')
    monkeypatch.setattr(MODULE.COMMISSION, 'commission_completion_check',
        lambda *args: pytest.fail('installer validator must not be repurposed'))
    args = SimpleNamespace(manifest=Path("/root/manifest.json"), radio_inventory=Path("/root/radio.json"),
        current_sage_policy=Path('/root/policy.json'),
        sage_binary_sha256='a' * 64,
        radio_fingerprint=MODULE.LEGACY.fingerprint(config["radio"]["public_key"]),
        **{name + "_sha256": MODULE.sha(raw[name]) for name in
           ("manifest", "radio_inventory", "commission", "app", "bindings", 'current_sage', 'current_sage_policy')})
    if fault and fault.endswith("_pin"):
        setattr(args, fault.removesuffix("_pin") + "_sha256", "f" * 64)
    if fault is None:
        assert MODULE.inputs(args)[1] == config
        assert MODULE.sha(raw['current_sage']) != receipt['output_sha256']['sage.yaml']
    else:
        with pytest.raises(RuntimeError):
            MODULE.inputs(args)


@pytest.mark.parametrize('field', sorted(MODULE.POLICY_FIELDS))
def test_current_yaml_must_match_each_reviewed_semantic_field(documents, field):
    expected = policy(documents[0])
    actual = copy.deepcopy(expected)
    actual[field] = 'changed'
    with pytest.raises(RuntimeError):
        MODULE.current_policy(yaml.safe_dump(actual).encode(), expected, documents[0])


@pytest.mark.parametrize('section,field,value', [
    ('encryption', 'enabled', False), ('encryption', 'enabled', 1),
    ('federation', 'enabled', True), ('federation', 'p2p_enabled', True),
    ('quorum', 'peers', ['remote']), ('quorum', 'tls_addr', '0.0.0.0:8443'),
    ('embedding', 'provider', 'openai-compatible'), ('embedding', 'dimension', 1),
    ('embedding', 'api_key', 'not-a-real-secret'), ('private_media', 'enabled', False),
    ('private_media', 'actor_bytes', True), ('private_media', 'node_bytes', 1),
    ('vendored_agent_bootstrap', 'agent_key_file', '/tmp/key')])
def test_pinned_policy_cannot_override_safety_constraints(documents, section, field, value):
    expected = policy(documents[0])
    expected[section][field] = value
    with pytest.raises(RuntimeError):
        MODULE.current_policy(yaml.safe_dump(expected).encode(), expected, documents[0])


@pytest.mark.parametrize('suffix', ['\nencryption: {enabled: false}\n', '\nother: &anchor true\nalias: *anchor\n',
                                  '\nother: {enabled: true, enabled: false}\n'])
def test_ambiguous_yaml_refused(documents, suffix):
    expected = policy(documents[0])
    with pytest.raises(RuntimeError):
        MODULE.current_policy((yaml.safe_dump(expected) + suffix).encode(), expected, documents[0])


@pytest.mark.parametrize('interval', ['100ms', '500ms', '2s', '60s', '60000ms', '1m'])
def test_reviewed_required_voter_accepts_bounded_polling(documents, interval):
    expected = policy(documents[0])
    expected['voter']['poll_interval'] = interval
    MODULE.current_policy(yaml.safe_dump(expected).encode(), expected, documents[0])


@pytest.mark.parametrize('interval', ['', '0s', '-2s', '+2s', '2s\n', ' 2s', '02s', '99ms',
                                    '61s', '2m', '9999999s', 'NaNs', 2, True, None])
def test_voter_interval_requires_explicit_bounded_duration(documents, interval):
    expected = policy(documents[0])
    expected['voter']['poll_interval'] = interval
    with pytest.raises(RuntimeError):
        MODULE.current_policy(yaml.safe_dump(expected).encode(), expected, documents[0])


@pytest.mark.parametrize('field', ['enabled', 'required'])
@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_required_voter_gate_cannot_be_disabled_or_coerced(documents, field, value):
    expected = policy(documents[0])
    expected['voter'][field] = value
    with pytest.raises(RuntimeError):
        MODULE.current_policy(yaml.safe_dump(expected).encode(), expected, documents[0])


@pytest.mark.parametrize('field', ['enabled', 'required', 'poll_interval'])
def test_voter_fields_cannot_fall_back_to_native_defaults(documents, field):
    expected = policy(documents[0])
    del expected['voter'][field]
    with pytest.raises(RuntimeError):
        MODULE.current_policy(yaml.safe_dump(expected).encode(), expected, documents[0])


def test_unknown_voter_override_refused(documents):
    expected = policy(documents[0])
    expected['voter']['override'] = True
    with pytest.raises(RuntimeError):
        MODULE.current_policy(yaml.safe_dump(expected).encode(), expected, documents[0])


@pytest.mark.parametrize('fault', [None, 'exit', 'timeout', 'binary_changed', 'pin', 'trust'])
def test_native_validator_is_pinned_readonly_and_environment_isolated(tmp_path, monkeypatch, fault):
    import sage_lantern.suspend_kiosk as kiosk
    binary = tmp_path / 'sage-gui'
    binary.write_bytes(b'reviewed synthetic executable')
    expected = MODULE.sha(binary.read_bytes())
    monkeypatch.setattr(MODULE, 'SAGE_BINARY', binary)
    def trusted(path):
        if fault == 'trust':
            raise RuntimeError('untrusted')
    monkeypatch.setattr(kiosk, '_trusted_file', trusted)
    calls = []
    def run(arguments, **kwargs):
        calls.append(arguments)
        assert arguments == ['/usr/sbin/runuser', '-u', 'sage-lantern', '--', str(binary), 'check-lantern-private-config']
        assert kwargs['env']['SAGE_HOME'] == '/var/lib/sage-lantern/sage-personal'
        assert kwargs['env']['SAGE_LANTERN_PRIVATE_LISTENERS'] == '1'
        assert kwargs['env']['SAGE_CMT_P2P_ADDR'] == 'tcp://127.0.0.1:26656'
        assert 'HTTP_PROXY' not in kwargs['env'] and 'PYTHONPATH' not in kwargs['env']
        assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['timeout'] == 10
        if fault == 'timeout':
            raise subprocess.TimeoutExpired(arguments, 10)
        if fault == 'binary_changed':
            binary.write_bytes(b'changed')
        return SimpleNamespace(returncode=1 if fault == 'exit' else 0)
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    monkeypatch.setenv('HTTP_PROXY', 'http://untrusted')
    if fault is None:
        MODULE.native_config_check(expected)
    else:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
            MODULE.native_config_check('f' * 64 if fault == 'pin' else expected)
    assert len(calls) == (0 if fault in ('trust', 'pin') else 1)


@pytest.mark.parametrize('fault', ['owner', 'mode', 'symlink', 'parent'])
def test_current_config_rejects_unsafe_service_path(monkeypatch, fault):
    def metadata(path):
        if path == MODULE.SAGE_YAML:
            return SimpleNamespace(st_mode=(stat.S_IFLNK if fault == 'symlink' else stat.S_IFREG)
                | (0o644 if fault == 'mode' else 0o600), st_uid=0 if fault == 'owner' else 994)
        return SimpleNamespace(st_mode=stat.S_IFDIR | (0o777 if fault == 'parent' else 0o755), st_uid=0)
    monkeypatch.setattr(Path, 'lstat', metadata)
    with pytest.raises(RuntimeError):
        MODULE.current_yaml(994)


@pytest.mark.parametrize("fault", [None, "usb", "storage", "identity"])
def test_observations_use_local_identity_signed_storage_and_no_radio_open(documents, monkeypatch, fault):
    import sage_sdk
    import sage_lantern.mesh_daemon as daemon
    import sage_lantern.mesh_usb_opener as usb
    import sage_lantern.message_storage as storage
    binding = validate(documents)
    config = documents[0]
    node = SimpleNamespace(path=Path("/dev/ttyACM0"), device=1, inode=2, rdev=3)
    events = []
    def inspect(value):
        events.append("inspect")
        if fault == "usb":
            raise RuntimeError("synthetic USB mismatch")
        return node
    def identity(value):
        events.append("identity")
        if fault == "identity":
            raise RuntimeError("synthetic key mismatch")
        return SimpleNamespace(agent_id=config["agent_id"]), 12345
    class Client:
        def __init__(self, endpoint, identity, **kwargs):
            assert endpoint == "http://127.0.0.1:8080" and kwargs == dict(timeout=5, trust_env=False)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            events.append("close_client")
    class Probe:
        def __init__(self, client, **kwargs):
            assert kwargs["actor"].agent_id == config["agent_id"]
        def snapshot(self):
            events.append("signed_snapshot")
            if fault == "storage":
                raise RuntimeError("synthetic unavailable storage")
            return storage.MessageStorageSnapshot("a" * 64, "1")
    monkeypatch.setattr(usb, "_inspect", inspect)
    monkeypatch.setattr(daemon, "load_identity", identity)
    monkeypatch.setattr(sage_sdk, "SageClient", Client)
    monkeypatch.setattr(storage, "MessageStorageProbe", Probe)
    monkeypatch.setattr(MODULE.os, "close", lambda descriptor: events.append(("close_key", descriptor)))
    monkeypatch.setattr(MODULE, "Path", lambda path: SimpleNamespace(read_text=lambda: "synthetic-boot"))
    if fault is None:
        result = MODULE.observe(config, binding)
        assert result["storage_epoch"] == "a" * 64 + ":1" and result["usb_node"]["inode"] == 2
        assert events.count("signed_snapshot") == 2
    else:
        with pytest.raises(RuntimeError):
            MODULE.observe(config, binding)
    assert (("close_key", 12345) in events) is (fault not in ("usb", "identity"))


@pytest.mark.parametrize("fault", [None, "inputs", "observation", "final_observation"])
def test_prepare_seals_only_public_artifacts_and_retains_partial_output(documents, tmp_path, monkeypatch, fault):
    config = documents[0]
    binding = validate(documents)
    raw = {"manifest": MODULE.encoded(config)}
    calls = []
    def inputs(args):
        calls.append("inputs")
        if fault == "inputs" and calls.count("inputs") > 1:
            raise RuntimeError("synthetic changed receipt")
        return raw, config, binding
    def observe(config, binding):
        calls.append("observe")
        if fault == "observation" or (fault == "final_observation" and calls.count("observe") == 3):
            raise RuntimeError("synthetic changed epoch")
        return dict(boot_id="synthetic", storage_epoch="a" * 64 + ":1")
    monkeypatch.setattr(MODULE, "inputs", inputs)
    monkeypatch.setattr(MODULE, "observe", observe)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.sys, "platform", "linux")
    monkeypatch.setattr(MODULE.LEGACY, "directory", lambda path, owner: os.open(path, os.O_RDONLY | os.O_DIRECTORY))
    output = tmp_path / "fresh"
    if fault is None:
        MODULE.prepare(SimpleNamespace(output_dir=output, sage_binary_sha256='a' * 64))
        receipt = json.loads((output / "PREPARED.json").read_bytes())
        assert receipt["private_identity_verified"] and not receipt["capture_verified"] and not receipt["activated"]
        assert set(path.name for path in output.iterdir()) == {"PREPARED.json", "20-radio.conf", "mesh-runtime.json"}
        with pytest.raises(FileExistsError):
            MODULE.prepare(SimpleNamespace(output_dir=output, sage_binary_sha256='a' * 64))
    else:
        with pytest.raises(RuntimeError):
            MODULE.prepare(SimpleNamespace(output_dir=output, sage_binary_sha256='a' * 64))
        assert not (output / "PREPARED.json").exists()
        assert output.exists() is (fault == "final_observation")
