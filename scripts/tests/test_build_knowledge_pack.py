import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('build_knowledge_pack',
                                              ROOT / 'lantern/build-knowledge-pack.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
pack_format = builder.knowledge_pack

JSONL = '\n'.join([
    json.dumps({'id': 'my-001', 'text': 'Kuala Lumpur and Ipoh are about 205 kilometres apart.'}),
    json.dumps({'id': 'my-002', 'text': 'Merdeka 118 is 678.9 metres tall.',
                'tags': ['landmarks'], 'date': '2026-09-19'}),
]) + '\n'

MARKDOWN = """# Malaysian practical facts

## Kuala Lumpur to Ipoh by road
Tags: distances, roads
Source: curated:owner-notes

About 205 kilometres by road, roughly two hours by car.

## Merdeka 118
<!-- id: merdeka -->

678.9 metres tall, the tallest building in Malaysia.
"""


def build(monkeypatch, source, out, *extra, name='source.jsonl'):
    path = out.parent / name
    path.write_text(source if isinstance(source, str) else '', 'utf-8')
    arguments = ['build-knowledge-pack.py', str(path), '--out', str(out), '--pack-id', 'malaysia',
                 '--version', '1', '--title', 'Malaysia', '--licence', 'CC-BY-4.0',
                 '--source-label', 'curated:test', '--created-at', '2026-09-20T00:00:00Z',
                 '--default-date', '2026-09-20', *extra]
    monkeypatch.setattr(sys, 'argv', arguments)
    builder.main()
    return pack_format.load_pack(out, allow_unsigned=True)


def test_jsonl_build_writes_a_verifiable_pack(tmp_path, monkeypatch):
    pack = build(monkeypatch, JSONL, tmp_path / 'pack-v1')
    assert pack.pack_id == 'malaysia' and pack.version == 1
    assert [entry['id'] for entry in pack.manifest['chunks']] == ['my-001', 'my-002']
    assert pack.manifest['pack']['licence'] == 'CC-BY-4.0'
    assert pack.manifest['pack']['provenance'] == {'source': 'curated:test'}
    assert pack.manifest['pack']['created_at'] == '2026-09-20T00:00:00Z'
    second = pack.chunks[pack.manifest['chunks'][1]['sha256']]
    assert second['tags'] == ['landmarks'] and second['date'] == '2026-09-19'
    assert pack.manifest['totals']['embedded_chunks'] == 0


def test_rebuild_is_byte_identical(tmp_path, monkeypatch):
    first = build(monkeypatch, JSONL, tmp_path / 'a', name='one.jsonl')
    second = build(monkeypatch, JSONL, tmp_path / 'b', name='two.jsonl')
    assert first.raw == second.raw
    for digest, record in first.chunks.items():
        assert pack_format.chunk_bytes(record) == pack_format.chunk_bytes(second.chunks[digest])


def test_markdown_source_derives_ids_titles_and_metadata(tmp_path, monkeypatch):
    pack = build(monkeypatch, MARKDOWN, tmp_path / 'pack-md', name='facts.md')
    entries = {entry['id']: entry for entry in pack.manifest['chunks']}
    assert sorted(entries) == ['kuala-lumpur-to-ipoh-by-road', 'merdeka']
    assert entries['kuala-lumpur-to-ipoh-by-road']['title'] == 'Kuala Lumpur to Ipoh by road'
    record = pack.chunks[entries['kuala-lumpur-to-ipoh-by-road']['sha256']]
    assert record['tags'] == ['distances', 'roads']
    assert record['source'] == 'curated:owner-notes'
    assert record['text'].startswith('About 205 kilometres by road')
    assert pack.manifest['pack']['title'] == 'Malaysia'


def test_markdown_heading_supplies_a_missing_title(tmp_path, monkeypatch):
    arguments = ['build-knowledge-pack.py', str(tmp_path / 'facts.md'), '--out', str(tmp_path / 'p'),
                 '--pack-id', 'malaysia', '--version', '1', '--licence', 'CC-BY-4.0',
                 '--source-label', 'curated:test', '--created-at', '2026-09-20T00:00:00Z']
    (tmp_path / 'facts.md').write_text(MARKDOWN, 'utf-8')
    monkeypatch.setattr(sys, 'argv', arguments)
    builder.main()
    pack = pack_format.load_pack(tmp_path / 'p', allow_unsigned=True)
    assert pack.manifest['pack']['title'] == 'Malaysian practical facts'


def test_unknown_jsonl_field_is_refused(tmp_path, monkeypatch):
    source = json.dumps({'id': 'my-001', 'text': 'Text.', 'confidence': 0.9}) + '\n'
    with pytest.raises(pack_format.PackFormatError) as error:
        build(monkeypatch, source, tmp_path / 'pack-unknown')
    assert 'unknown field' in str(error.value)


def test_parent_build_records_a_per_chunk_delta(tmp_path, monkeypatch):
    first = build(monkeypatch, JSONL, tmp_path / 'v1')
    revised = JSONL.replace('Merdeka 118 is 678.9 metres tall.',
                            'Merdeka 118 is 678.9 metres tall and has 118 storeys.') + \
        json.dumps({'id': 'my-003', 'text': 'Penang is on the northwest coast of Malaysia.'}) + '\n'
    second = build(monkeypatch, revised, tmp_path / 'v2', '--version', '2', '--parent', str(first.directory),
                   name='revised.jsonl')
    assert second.manifest['parent']['version'] == 1
    assert second.manifest['parent']['manifest_sha256'] == pack_format.sha256_hex(first.raw)
    delta = pack_format.load_delta(second.directory / 'delta-v1-to-v2.json')
    assert delta['unchanged'] == 1
    assert len(delta['added']) == 2 and len(delta['removed']) == 1
    assert delta['to_manifest_sha256'] == pack_format.sha256_hex(second.raw)


def test_parent_must_be_the_same_pack(tmp_path, monkeypatch):
    other = build(monkeypatch, JSONL, tmp_path / 'other', '--pack-id', 'indonesia')
    with pytest.raises(pack_format.PackFormatError) as error:
        build(monkeypatch, JSONL, tmp_path / 'v2', '--version', '2', '--parent', str(other.directory))
    assert 'is pack indonesia' in str(error.value)


def test_rebuild_into_the_same_directory_refuses_changed_content(tmp_path, monkeypatch):
    build(monkeypatch, JSONL, tmp_path / 'pack')
    changed = JSONL.replace('about 205 kilometres', 'about 210 kilometres')
    assert changed != JSONL
    with pytest.raises(pack_format.PackFormatError) as error:
        build(monkeypatch, changed, tmp_path / 'pack', name='changed.jsonl')
    assert 'does not list' in str(error.value)


def test_signing_key_produces_a_pinned_pack(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    key_path = tmp_path / 'signing.key'
    key_path.write_text(key.private_bytes(encoding=serialization.Encoding.Raw,
                                          format=serialization.PrivateFormat.Raw,
                                          encryption_algorithm=serialization.NoEncryption()).hex())
    directory = tmp_path / 'signed'
    build(monkeypatch, JSONL, directory, '--signing-key', str(key_path))
    signer = pack_format.public_key_hex(key)
    trusted = pack_format.load_pack(directory, trusted_public_hex=signer)
    assert trusted.signer == signer
    with pytest.raises(pack_format.PackFormatError):
        pack_format.load_pack(directory, trusted_public_hex='1' * 64)


def test_embed_depth_is_recorded_and_reused(tmp_path, monkeypatch):
    directory = tmp_path / 'embedded'
    arguments = ['build-knowledge-pack.py', str(tmp_path / 'source.jsonl'), '--out', str(directory),
                 '--pack-id', 'malaysia', '--version', '1', '--title', 'Malaysia',
                 '--licence', 'CC-BY-4.0', '--source-label', 'curated:test',
                 '--created-at', '2026-09-20T00:00:00Z', '--embed-dimension', '3',
                 '--embed-url', 'http://127.0.0.1:9/']
    (tmp_path / 'source.jsonl').write_text(JSONL, 'utf-8')
    monkeypatch.setattr(sys, 'argv', arguments)
    monkeypatch.setattr(builder, 'embed_text', lambda *a, **k: [0.5, -0.25, 0.0])
    builder.main()
    pack = pack_format.load_pack(directory, allow_unsigned=True)
    assert pack.manifest['embedding']['embedded_chunks'] == 2
    digest = pack.manifest['chunks'][0]['sha256']
    assert pack.vector(digest) == [0.5, -0.25, 0.0]


def test_embed_service_dimension_mismatch_refused(tmp_path, monkeypatch):
    arguments = ['build-knowledge-pack.py', str(tmp_path / 'source.jsonl'), '--out',
                 str(tmp_path / 'bad'), '--pack-id', 'malaysia', '--version', '1',
                 '--title', 'Malaysia', '--licence', 'CC-BY-4.0', '--source-label', 'curated:test',
                 '--created-at', '2026-09-20T00:00:00Z', '--embed-dimension', '768',
                 '--embed-url', 'http://127.0.0.1:9/']
    (tmp_path / 'source.jsonl').write_text(JSONL, 'utf-8')
    monkeypatch.setattr(sys, 'argv', arguments)
    monkeypatch.setattr(builder, 'embed_text', lambda *a, **k: [0.5, -0.25])
    with pytest.raises(pack_format.PackFormatError) as error:
        builder.main()
    assert 'dimensions' in str(error.value)
