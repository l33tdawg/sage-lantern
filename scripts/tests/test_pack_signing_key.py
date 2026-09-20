import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('pack_signing_key',
                                              ROOT / 'lantern/pack-signing-key.py')
keytool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keytool)
pack_format = keytool.knowledge_pack

CREATED = '2026-09-20T00:00:00Z'


def arguments(**overrides):
    values = {'out': None, 'key': None, 'force': False, 'note': 'Foundation pack key 1',
              'created_at': CREATED, 'status': 'active', 'trust_root': ROOT / 'lantern/pack-trust-root.json'}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_generate_writes_a_0600_hex_key_outside_the_repository(tmp_path):
    destination = tmp_path / 'foundation' / 'pack-signing-1.key'
    result = keytool.generate(arguments(out=destination))
    assert destination.is_file()
    assert oct(destination.stat().st_mode & 0o777) == '0o600'
    assert len(destination.read_text().strip()) == 64
    entry = result['trust_root_entry']
    assert set(entry) == pack_format.TRUST_KEY_FIELDS
    assert entry['status'] == 'active' and entry['created_at'] == CREATED
    pack_format.validate_trust_root({'schema': pack_format.SCHEMA_TRUST_ROOT, 'note': 'test',
                                     'keys': [entry]})
    assert entry['key_id'] == pack_format.key_id_for(entry['public_key_hex'])
    assert pack_format.public_key_hex(pack_format.load_signing_key(destination)) == \
        entry['public_key_hex']


def test_generate_refuses_to_put_a_key_in_the_repository(tmp_path):
    with pytest.raises(pack_format.PackFormatError) as error:
        keytool.generate(arguments(out=ROOT / 'scripts' / 'lantern' / 'accidental.key'))
    assert 'inside the repository' in str(error.value)
    assert not (ROOT / 'scripts' / 'lantern' / 'accidental.key').exists()


def test_generate_refuses_to_overwrite_without_force(tmp_path):
    destination = tmp_path / 'pack-signing-1.key'
    first = keytool.generate(arguments(out=destination))['trust_root_entry']['public_key_hex']
    with pytest.raises(pack_format.PackFormatError) as error:
        keytool.generate(arguments(out=destination))
    assert 'already exists' in str(error.value)
    second = keytool.generate(arguments(out=destination, force=True))['trust_root_entry']
    assert second['public_key_hex'] != first


def test_entry_reads_a_backup_and_reproduces_the_same_key_id(tmp_path):
    original = tmp_path / 'pack-signing-1.key'
    generated = keytool.generate(arguments(out=original))['trust_root_entry']
    backup = tmp_path / 'backups' / 'pack-signing-1.key'
    backup.parent.mkdir()
    backup.write_bytes(original.read_bytes())
    entry = keytool.entry(arguments(key=backup))['trust_root_entry']
    assert entry == generated
    assert entry['key_id'] == generated['key_id']


def test_check_reports_membership_and_status_against_a_trust_root(tmp_path):
    key_file = tmp_path / 'pack-signing-1.key'
    generated = keytool.generate(arguments(out=key_file))['trust_root_entry']
    retired = dict(generated, status='retired')
    root = tmp_path / 'pack-trust-root.json'
    root.write_text(json.dumps({'schema': pack_format.SCHEMA_TRUST_ROOT, 'note': 'test',
                                'keys': [retired]}))
    result = keytool.check(arguments(key=key_file, trust_root=root))
    assert result['in_trust_root'] is True and result['status'] == 'retired'
    assert result['active_keys'] == []
    empty = keytool.check(arguments(key=key_file))
    assert empty['in_trust_root'] is False and empty['status'] is None


def test_shipped_trust_root_is_valid_and_deliberately_empty():
    root = pack_format.load_trust_root(ROOT / 'lantern/pack-trust-root.json')
    assert root['keys'] == []
    assert pack_format.active_key_ids(root) == []


def test_trust_root_validation_refuses_a_mismatched_key_id():
    key = pack_format.key_id_for('a' * 64)
    entry = {'key_id': key, 'algorithm': 'ed25519', 'public_key_hex': 'b' * 64, 'status': 'active',
             'created_at': CREATED, 'note': ''}
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.validate_trust_root({'schema': pack_format.SCHEMA_TRUST_ROOT, 'note': '',
                                         'keys': [entry]})
    assert 'not derived' in str(error.value)


def test_trust_root_validation_refuses_unknown_status_and_duplicates():
    key = 'a' * 64
    entry = {'key_id': pack_format.key_id_for(key), 'algorithm': 'ed25519', 'public_key_hex': key,
             'status': 'trusted', 'created_at': CREATED, 'note': ''}
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.validate_trust_root({'schema': pack_format.SCHEMA_TRUST_ROOT, 'note': '',
                                         'keys': [entry]})
    assert 'status must be one of' in str(error.value)
    entry['status'] = 'active'
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.validate_trust_root({'schema': pack_format.SCHEMA_TRUST_ROOT, 'note': '',
                                         'keys': [entry, dict(entry)]})
    assert 'repeats key' in str(error.value)


def test_load_trust_root_refuses_a_malformed_file_rather_than_degrading(tmp_path):
    broken = tmp_path / 'pack-trust-root.json'
    broken.write_text('{"schema": "sage.knowledge-pack.trust-root.v1", "keys": []}')
    with pytest.raises(pack_format.PackFormatError):
        pack_format.load_trust_root(broken)
