import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    'knowledge_pack', Path(__file__).resolve().parents[1] / 'lantern/knowledge_pack.py')
pack_format = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack_format)

CREATED = '2026-09-20T00:00:00Z'
PROVENANCE = {'source': 'curated:test-fixture'}


def chunk(identifier, text, **overrides):
    return pack_format.build_chunk(identifier, text, source='curated:test-fixture',
                                   date='2026-09-20', **overrides)


def build(tmp_path, name='pack-v1', *, version=1, chunks=None, created=CREATED,
          embedding_model='nomic-embed-text-v1.5', embedding_dimension=768, signature=None):
    chunks = [chunk('one', 'Kuala Lumpur and Ipoh are about 205 kilometres apart.'),
              chunk('two', 'Merdeka 118 is 678.9 metres tall.')] if chunks is None else chunks
    manifest = pack_format.build_manifest(
        pack_id='malaysia', version=version, title='Test pack', licence='CC-BY-4.0',
        provenance=PROVENANCE, created_at=created, chunks=chunks,
        embedding_model=embedding_model, embedding_dimension=embedding_dimension)
    directory = tmp_path / name
    pack_format.write_pack(directory, manifest, {record['sha256']: record for record in chunks},
                           signature=signature)
    return directory


def test_canonical_json_sorts_keys_and_refuses_floats_and_non_ascii_keys():
    assert pack_format.canonical_json({'b': 1, 'a': 'x'}) == b'{"a":"x","b":1}'
    with pytest.raises(pack_format.PackFormatError):
        pack_format.canonical_json({'a': 1.5})
    with pytest.raises(pack_format.PackFormatError):
        pack_format.canonical_json({'a': [1, {'b': 2.5}]})
    with pytest.raises(pack_format.PackFormatError):
        pack_format.canonical_json({'caf\u00e9': 'x'})


def test_content_address_ignores_embedding_provenance():
    plain = chunk('one', 'Text that does not move.')
    embedded = pack_format.build_chunk('one', 'Text that does not move.',
                                       source='curated:test-fixture', date='2026-09-20',
                                       embedding=[0.0] * 8, embedding_model='other-model')
    assert plain['sha256'] == embedded['sha256']


def test_vector_round_trip_is_float32_exact_and_dimension_checked():
    values = [0.125, -2.5, 1e-8, 0.0]
    encoded = pack_format.encode_vector(values)
    decoded = pack_format.decode_vector(encoded, len(values))
    assert decoded == [0.125, -2.5, pytest.approx(1e-8, abs=1e-14), 0.0]
    with pytest.raises(pack_format.PackFormatError):
        pack_format.decode_vector(encoded, 3)


@pytest.mark.parametrize('identifier,text', [('Bad Slug', 'text'), ('one', '   '),
                                             ('one', 'x' * 4001)])
def test_chunk_validation_refuses_bad_rows(identifier, text):
    with pytest.raises(pack_format.PackFormatError):
        chunk(identifier, text)


def test_manifest_is_sorted_addressable_and_delta_computes_per_chunk():
    first = [chunk('b', 'Second chunk.'), chunk('a', 'First chunk.')]
    v1 = pack_format.build_manifest(pack_id='malaysia', version=1, title='T', licence='CC-BY-4.0',
                                    provenance=PROVENANCE, created_at=CREATED, chunks=first,
                                    embedding_model='m', embedding_dimension=768)
    assert [entry['id'] for entry in v1['chunks']] == ['a', 'b']
    second = [chunk('a', 'First chunk.'), chunk('c', 'Third chunk.')]
    v2 = pack_format.build_manifest(pack_id='malaysia', version=2, title='T', licence='CC-BY-4.0',
                                    provenance=PROVENANCE, created_at=CREATED, chunks=second,
                                    embedding_model='m', embedding_dimension=768,
                                    parent={'version': 1,
                                            'manifest_sha256': pack_format.manifest_digest(v1)})
    delta = pack_format.delta_document(v1, v2)
    pack_format.validate_delta(delta)
    assert delta['unchanged'] == 1
    assert len(delta['added']) == 1 and len(delta['removed']) == 1
    assert delta['added'] == sorted(delta['added'])
    assert delta['added_bytes'] == next(entry['bytes'] for entry in v2['chunks']
                                        if entry['sha256'] == delta['added'][0])


def test_manifest_refuses_unlicensed_or_unsourced_packs():
    records = [chunk('one', 'Text.')]
    with pytest.raises(pack_format.PackFormatError):
        pack_format.build_manifest(pack_id='malaysia', version=1, title='T', licence='',
                                   provenance=PROVENANCE, created_at=CREATED, chunks=records,
                                   embedding_model='m', embedding_dimension=768)
    with pytest.raises(pack_format.PackFormatError):
        pack_format.build_manifest(pack_id='malaysia', version=1, title='T', licence='CC-BY-4.0',
                                   provenance={'curator': 'nobody'}, created_at=CREATED,
                                   chunks=records, embedding_model='m', embedding_dimension=768)


def test_load_pack_round_trip_and_summary(tmp_path):
    directory = build(tmp_path)
    pack = pack_format.load_pack(directory, allow_unsigned=True)
    assert pack.pack_id == 'malaysia' and pack.version == 1
    assert pack.tag == 'pack:malaysia-v1'
    assert len(pack.chunks) == 2
    assert pack.vector(next(iter(pack.chunks))) is None
    summary = pack.summary()
    assert summary['chunks'] == 2 and summary['embedded_chunks'] == 0
    assert summary['manifest_sha256'] == pack_format.sha256_hex(pack.raw)
    assert pack_format.manifest_digest(pack.manifest) == pack_format.sha256_hex(pack.raw)


def test_load_pack_refuses_tampered_chunk_store(tmp_path):
    directory = build(tmp_path)
    target = next((directory / 'chunks').glob('*.json'))
    record = json.loads(target.read_text())
    record['text'] = 'Kuala Lumpur and Ipoh are about 999 kilometres apart.'
    target.write_text(json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n')
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.load_pack(directory, allow_unsigned=True)
    assert any(fragment in str(error.value) for fragment in
               ('content address does not match', 'does not hash to its name', 'misnamed'))


def test_load_pack_refuses_stray_and_missing_chunks(tmp_path):
    directory = build(tmp_path)
    (directory / 'chunks' / ('a' * 64 + '.json')).write_text('{}\n')
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.load_pack(directory, allow_unsigned=True)
    assert 'does not list' in str(error.value)

    stale = build(tmp_path, name='stale')
    removed = sorted((stale / 'chunks').glob('*.json'))[0]
    removed.unlink()
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.load_pack(stale, allow_unsigned=True)
    assert 'is missing' in str(error.value)


def test_load_pack_refuses_edited_or_unsigned_manifests(tmp_path):
    directory = build(tmp_path)
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['pack']['title'] = 'Edited after publication'
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.load_pack(directory, allow_unsigned=True)
    assert 'canonical form' in str(error.value)

    unsigned = build(tmp_path, name='unsigned')
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.load_pack(unsigned)
    assert 'unsigned packs are not accepted' in str(error.value)


def test_signature_verifies_and_pins_the_signer(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ed25519

    directory = build(tmp_path, name='to-sign')
    key = ed25519.Ed25519PrivateKey.generate()
    raw = (directory / 'manifest.json').read_bytes()
    signature = pack_format.sign_manifest(raw, key)
    (directory / 'manifest.sig.json').write_bytes(pack_format.canonical_json(signature) + b'\n')
    signer = pack_format.public_key_hex(key)
    assert pack_format.load_pack(directory, trusted_public_hex=signer).signer == signer

    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.load_pack(directory, trusted_public_hex='0' * 64)
    assert 'not the trusted key' in str(error.value)


def test_signature_refuses_a_manifest_swapped_under_it(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ed25519

    directory = build(tmp_path, name='swapped')
    key = ed25519.Ed25519PrivateKey.generate()
    signature = pack_format.sign_manifest((directory / 'manifest.json').read_bytes(), key)
    (directory / 'manifest.sig.json').write_bytes(pack_format.canonical_json(signature) + b'\n')
    manifest = json.loads((directory / 'manifest.json').read_text())
    manifest['pack']['title'] = 'A different pack'
    (directory / 'manifest.json').write_bytes(pack_format.manifest_bytes(manifest))
    with pytest.raises(pack_format.PackFormatError) as error:
        pack_format.load_pack(directory)
    assert 'different manifest' in str(error.value)


def test_parent_must_be_an_earlier_version_of_the_same_pack(tmp_path):
    records = [chunk('one', 'Text.')]
    with pytest.raises(pack_format.PackFormatError):
        pack_format.build_manifest(pack_id='malaysia', version=1, title='T', licence='L',
                                   provenance=PROVENANCE, created_at=CREATED, chunks=records,
                                   embedding_model='m', embedding_dimension=768,
                                   parent={'version': 1,
                                           'manifest_sha256': 'a' * 64})
