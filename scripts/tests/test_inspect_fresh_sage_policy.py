import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


SPEC = importlib.util.spec_from_file_location('inspect_fresh_policy',
    Path(__file__).parents[1] / 'lantern/inspect-fresh-sage-policy.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def policy():
    return dict(chain_id='sage-personal-test', data_dir='/var/lib/sage-lantern/sage-personal/data',
        agent_key_file='/var/lib/sage-lantern/sage-personal/agent.key', rest_addr='127.0.0.1:8080',
        encryption=dict(enabled=True), private_media=dict(enabled=True, actor_bytes=1024,
            node_bytes=2048, actor_objects=10, node_objects=20, min_free_bytes=4096),
        embedding=dict(provider='hash', dimension=768), federation=dict(enabled=False),
        quorum=dict(enabled=False, tls_addr='127.0.0.1:8443'), voter=dict(enabled=True, poll_interval='2s'),
        vendored_agent_bootstrap=dict(agent_key_file='/var/lib/sage-lantern/identity/lantern-agent.key',
            home_domain='local-field-knowledge', clearance=1))


def test_public_projection_is_exact_and_excludes_unknown_top_level_values():
    value = policy()
    value['unrelated'] = 'DO_NOT_EXPORT'
    assert MODULE.public_policy(MODULE.parse(yaml.safe_dump(value).encode())) == policy()


@pytest.mark.parametrize('key', ['api_key', 'passphrase', 'access-token', 'private_key', 'seed_hex', 'cookies'])
def test_credentials_anywhere_refuse_without_export(key):
    value = policy()
    value['unrelated'] = [{'nested': {key: 'DO_NOT_EXPORT'}}]
    with pytest.raises(ValueError, match='policy inspection refused'):
        MODULE.parse(yaml.safe_dump(value).encode())


@pytest.mark.parametrize('model', ['nomic-embed-text', 'DO_NOT_EXPORT'])
def test_nonempty_model_is_redacted_not_mistaken_for_a_hash_default(model):
    value = policy()
    value['embedding']['model'] = model
    summary = MODULE.schema(MODULE.parse(yaml.safe_dump(value).encode()))
    assert summary['embedding']['model_nonempty'] is True
    assert model not in json.dumps(summary)
    with pytest.raises(ValueError):
        MODULE.public_policy(value)


def test_unknown_nested_field_names_and_collection_contents_never_escape_schema():
    value = policy()
    value['quorum']['DO_NOT_EXPORT'] = 'sensitive'
    value['federation']['p2p_relay_addrs'] = ['DO_NOT_EXPORT']
    summary = MODULE.schema(value)
    assert summary['quorum']['unknown_fields'] == 1
    assert 'DO_NOT_EXPORT' not in json.dumps(summary)
    with pytest.raises(ValueError):
        MODULE.public_policy(value)


@pytest.mark.parametrize('raw', [b'key: 1\nkey: 2\n', b'key: &anchor true\nother: *anchor\n',
                               b'key: {enabled: true, enabled: false}\n', b'!!python/object:unknown {}'])
def test_ambiguous_or_executable_yaml_is_refused(raw):
    with pytest.raises((ValueError, yaml.YAMLError)):
        MODULE.parse(raw)


@pytest.mark.parametrize('mode,fault', [(mode, fault) for mode in (False, True)
    for fault in (None, 'pin', 'changed', 'credentials', 'malformed')])
def test_cli_is_pinned_readonly_and_suppresses_errors(monkeypatch, capsys, mode, fault):
    value = policy()
    if fault == 'credentials':
        value['password'] = 'DO_NOT_EXPORT'
    raw = yaml.safe_dump(value).encode() if fault != 'malformed' else b'key: [DO_NOT_EXPORT'
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=994))
    calls = []
    def read(uid):
        calls.append(uid)
        return raw + b'\n' if fault == 'changed' and len(calls) == 2 else raw
    monkeypatch.setattr(MODULE, 'read_config', read)
    result = MODULE.main(['--current-sage-sha256', 'f' * 64 if fault == 'pin' else digest]
        + (['--policy'] if mode else []))
    output = capsys.readouterr()
    if fault is None:
        assert result == 0 and calls == [994, 994]
        assert json.loads(output.out) == (policy() if mode else MODULE.schema(value))
    else:
        assert result == 1 and output.out == ''
        assert output.err == 'Policy inspection refused; no configuration values disclosed.\n'
    assert 'DO_NOT_EXPORT' not in output.out + output.err


def test_read_config_closes_descriptor_on_changed_file(monkeypatch):
    import stat
    file = SimpleNamespace(st_dev=1, st_ino=2, st_uid=994, st_mode=stat.S_IFREG | 0o600,
        st_nlink=1, st_size=4, st_mtime_ns=0, st_ctime_ns=0)
    changed = copy.copy(file)
    changed.st_ino = 3
    monkeypatch.setattr(Path, 'lstat', lambda path: changed if path == MODULE.CONFIG
        else SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0))
    monkeypatch.setattr(MODULE.os, 'open', lambda *args: 42)
    monkeypatch.setattr(MODULE.os, 'fstat', lambda descriptor: file)
    monkeypatch.setattr(MODULE.os, 'read', lambda *args: b'test')
    closed = []
    monkeypatch.setattr(MODULE.os, 'close', lambda descriptor: closed.append(descriptor))
    with pytest.raises(ValueError):
        MODULE.read_config(994)
    assert closed == [42]
