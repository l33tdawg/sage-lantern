import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('install_knowledge_pack',
                                              ROOT / 'lantern/install-knowledge-pack.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
pack_format = installer.knowledge_pack

TEXTS = ['Kuala Lumpur and Ipoh are about 205 kilometres apart.',
         'Merdeka 118 is 678.9 metres tall.']


class Page:
    def __init__(self, memories, has_more):
        self.memories = memories
        self.has_more = has_more


class Response:
    def __init__(self, memory_id, replay=False, status='success'):
        self.memory_id = memory_id
        self.idempotent_replay = replay
        self.status = status


class FakeClient:
    """A personal node small enough to reason about, with the SDK's real shapes."""

    def __init__(self, forget_status='deprecated', owned=('local-field-knowledge',)):
        self.memories = []
        self.proposals = []
        self.forgets = []
        self.embeds = []
        self.owned = list(owned)
        self.forget_status = forget_status
        self.status = 'success'
        self.force_replay = False
        self.fail_content = None
        self._next = 0

    def owned_domains(self, cursor=None, limit=50):
        return SimpleNamespace(domains=list(self.owned), next_cursor=None, has_more=False)

    def list_memories(self, limit=50, offset=0, domain=None, tag=None, **_):
        rows = [memory for memory in self.memories
                if (domain is None or memory['domain'] == domain) and tag in memory['tags']]
        page = rows[offset:offset + limit]
        return Page([SimpleNamespace(memory_id=row['memory_id'], content=row['content'])
                     for row in page], has_more=offset + len(page) < len(rows))

    def propose(self, content, memory_type, domain_tag, confidence, embedding=None, tags=None, **_):
        if self.fail_content is not None and content == self.fail_content:
            raise RuntimeError('node refused the write')
        self.proposals.append({'content': content, 'type': memory_type, 'domain': domain_tag,
                               'confidence': confidence, 'embedding': embedding, 'tags': list(tags or [])})
        for memory in self.memories:
            if memory['content'] == content and memory['domain'] == domain_tag:
                return Response(memory['memory_id'], replay=True)
        identifier = f'mem-{self._next}'
        self._next += 1
        self.memories.append({'memory_id': identifier, 'content': content, 'domain': domain_tag,
                              'tags': set(tags or [])})
        return Response(identifier, replay=self.force_replay, status=self.status)

    def forget(self, memory_id, reason=None):
        self.forgets.append((memory_id, reason))
        if self.forget_status == 'deprecated':
            self.memories = [memory for memory in self.memories
                             if memory['memory_id'] != memory_id]
        return {'status': self.forget_status}

    def embed(self, text):
        self.embeds.append(text)
        return [0.5, 0.25, 0.0]


def make_pack(tmp_path, *, version=1, texts=None, embedded=False, pack_id='malaysia',
              name=None, pack_class='reference', rows=None, parent=None, write_delta_from=None,
              confidence_bp=None):
    rows = rows if rows is not None else [{'id': f'my-{index:03d}', 'text': text,
                                           'title': f'Fact {index}'}
                                          for index, text in enumerate(
                                              TEXTS if texts is None else texts, start=1)]
    if confidence_bp is None:
        confidence_bp = pack_format.BRIEF_CONFIDENCE_BP if pack_class == 'brief' \
            else pack_format.REFERENCE_CONFIDENCE_BP
    chunks = []
    for row in rows:
        chunks.append(pack_format.build_chunk(
            row['id'], row['text'], title=row.get('title', ''), tags=row.get('tags', ()),
            source=row.get('source', 'curated:test'), date=row.get('date', '2026-09-20'),
            confidence_bp=row.get('confidence_bp', confidence_bp),
            embedding=[0.5, 0.25, 0.0] if embedded else None,
            embedding_model='nomic-embed-text-v1.5' if embedded else None))
    manifest = pack_format.build_manifest(
        pack_id=pack_id, version=version, pack_class=pack_class, title='Test pack',
        licence='CC-BY-4.0',
        provenance={'source': 'curated:test'}, created_at='2026-09-20T00:00:00Z', chunks=chunks,
        embedding_model='nomic-embed-text-v1.5', embedding_dimension=3, parent=parent)
    directory = tmp_path / (name or f'{pack_id}-v{version}')
    delta = pack_format.delta_document(write_delta_from.manifest, manifest) \
        if write_delta_from is not None else None
    pack_format.write_pack(directory, manifest, {record['sha256']: record for record in chunks},
                           delta=delta)
    return pack_format.load_pack(directory, allow_unsigned=True)


def test_install_proposes_every_chunk_with_pack_tags_then_skips_on_rerun(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    first = installer.install(client, pack, domain=None)
    assert first['chunks'] == 2 and first['installed'] == 2 and first['skipped'] == 0
    assert first['failures'] == 0
    assert len(client.proposals) == 2
    for proposal in client.proposals:
        assert proposal['domain'] == 'local-field-knowledge' and proposal['type'] == 'fact'
        assert proposal['confidence'] == 0.95
        assert proposal['content'] in TEXTS
        assert pack.tag in proposal['tags'] and 'sage-knowledge-pack' in proposal['tags']
        assert any(tag.startswith('chunk:') for tag in proposal['tags'])
        assert any(tag.startswith('chunk-id:') for tag in proposal['tags'])
    assert first['domain'] == 'local-field-knowledge'
    second = installer.install(client, pack, domain=None)
    assert second['installed'] == 0 and second['skipped'] == 2
    assert len(client.proposals) == 2


def test_coverage_reports_missing_then_complete(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    empty = installer.coverage(client, pack, domain=None)
    assert empty['complete'] is False and empty['missing'] == 2 and empty['present'] == 0
    installer.install(client, pack, domain=None)
    full = installer.coverage(client, pack, domain=None)
    assert full['complete'] and full['present'] == 2 and full['missing'] == 0


def test_recorded_embedding_is_used_and_device_embedding_is_avoided(tmp_path):
    pack = make_pack(tmp_path, embedded=True)
    client = FakeClient()
    installer.install(client, pack, domain=None)
    assert client.embeds == []
    assert client.proposals[0]['embedding'] == [0.5, 0.25, 0.0]


def test_unembedded_pack_asks_the_node_to_embed(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    installer.install(client, pack, domain=None)
    assert client.embeds == TEXTS


def test_node_side_content_replay_is_reported_not_double_counted(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    client.force_replay = True
    summary = installer.install(client, pack, domain=None)
    assert summary['installed'] == 0 and summary['replays'] == 2
    assert installer.coverage(client, pack, domain=None)['complete']


def test_indeterminate_commit_stops_the_install(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    client.status = 'indeterminate'
    with pytest.raises(installer.PackInstallError) as error:
        installer.install(client, pack, domain=None)
    assert 'reconcile' in str(error.value)


def test_refused_chunk_is_reported_and_leaves_the_pack_short(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    client.fail_content = TEXTS[1]
    summary = installer.install(client, pack, domain=None)
    assert summary['installed'] == 1 and summary['failures'] == 1
    assert summary['chunks_detail'][1]['error'].startswith('RuntimeError')
    assert installer.coverage(client, pack, domain=None)['missing'] == 1


def test_retire_reports_parked_challenges_rather_than_claiming_removal(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient(forget_status='parked')
    installer.install(client, pack, domain=None)
    summary = installer.retire(client, pack.tag, domain=None, reason='test retire')
    assert summary['memories'] == 2 and summary['parked'] == 2 and summary['retired'] == 0
    assert len(client.forgets) == 2
    assert installer.coverage(client, pack, domain=None)['complete']


def test_retire_removes_a_whole_pack_in_one_operation(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    installer.install(client, pack, domain=None)
    summary = installer.retire(client, pack.tag, domain=None, reason='test retire')
    assert summary['retired'] == 2 and summary['outcomes']['deprecated'] == 2
    assert client.memories == []
    assert installer.coverage(client, pack, domain=None)['missing'] == 2


def test_pack_versions_are_tag_scoped(tmp_path):
    first = make_pack(tmp_path, version=1, texts=[TEXTS[0]])
    second = make_pack(tmp_path, version=2, texts=[TEXTS[1]])
    client = FakeClient()
    installer.install(client, first, domain=None)
    installer.install(client, second, domain=None)
    assert (first.tag, second.tag) == ('pack:malaysia-v1', 'pack:malaysia-v2')
    installer.retire(client, first.tag, domain=None, reason='drop v1')
    assert len(client.memories) == 1
    assert installer.coverage(client, second, domain=None)['complete']


def test_brief_chunks_install_as_dated_observations(tmp_path):
    pack = make_pack(tmp_path, pack_class='brief', texts=[TEXTS[0]])
    client = FakeClient()
    summary = installer.install(client, pack, domain=None)
    assert summary['class'] == 'brief'
    proposal = client.proposals[0]
    assert proposal['type'] == 'observation' and proposal['confidence'] == 0.8
    assert proposal['content'] == f'As of 2026-09-20, per curated:test: {TEXTS[0]}'


def roll_packs(tmp_path):
    first = make_pack(tmp_path, version=1, pack_class='brief', rows=[
        {'id': 'wb-1', 'text': 'Event one.'},
        {'id': 'wb-2', 'text': 'Event two.'}])
    second = make_pack(
        tmp_path, version=2, pack_class='brief',
        rows=[{'id': 'wb-2', 'text': 'Event two.'},
              {'id': 'wb-3', 'text': 'Event three.'}],
        parent={'version': 1, 'manifest_sha256': pack_format.manifest_digest(first.manifest)},
        write_delta_from=first)
    return first, second


def test_roll_installs_the_successor_then_retires_the_chunks_it_dropped(tmp_path):
    first, second = roll_packs(tmp_path)
    client = FakeClient()
    installer.install(client, first, domain=None)
    summary = installer.roll(client, second, first, domain=None)
    assert summary['phase'] == 'rolled' and summary['delta'] == 'checked-against-manifests'
    assert (summary['added'], summary['removed'], summary['unchanged']) == (1, 1, 1)
    assert summary['install']['installed'] == 1 and summary['install']['skipped'] == 1
    assert summary['retire']['memories'] == 1 and summary['retire']['deprecated'] == 1
    assert len(client.proposals) == 3 and client.proposals[2]['content'].endswith('Event three.')
    assert sorted(memory['content'] for memory in client.memories) == [
        'As of 2026-09-20, per curated:test: Event three.',
        'As of 2026-09-20, per curated:test: Event two.']
    assert installer.coverage(client, second, domain=None)['complete']


def test_rolling_the_same_window_twice_converges(tmp_path):
    first, second = roll_packs(tmp_path)
    client = FakeClient()
    installer.install(client, first, domain=None)
    installer.roll(client, second, first, domain=None)
    again = installer.roll(client, second, first, domain=None)
    assert again['phase'] == 'rolled'
    assert again['install']['installed'] == 0 and again['install']['skipped'] == 2
    assert again['retire']['memories'] == 0
    assert len(client.forgets) == 1


def test_roll_failure_leaves_the_old_window_alone(tmp_path):
    first, second = roll_packs(tmp_path)
    client = FakeClient()
    installer.install(client, first, domain=None)
    client.fail_content = 'As of 2026-09-20, per curated:test: Event three.'
    summary = installer.roll(client, second, first, domain=None)
    assert summary['phase'] == 'roll-incomplete' and summary['retire'] is None
    assert client.forgets == []
    assert len(client.memories) == 2
    assert installer.coverage(client, first, domain=None)['complete']


def test_roll_dry_run_writes_nothing(tmp_path):
    first, second = roll_packs(tmp_path)
    client = FakeClient()
    installer.install(client, first, domain=None)
    proposals, memories = len(client.proposals), len(client.memories)
    summary = installer.roll(client, second, first, domain=None, dry_run=True)
    assert summary['phase'] == 'roll-dry-run'
    assert summary['install']['installed'] == 0 and summary['install']['failures'] == 0
    assert summary['retire']['memories'] == 1 and summary['retire']['dry_run'] is True
    assert client.forgets == []
    assert len(client.proposals) == proposals and len(client.memories) == memories


def test_roll_refuses_a_previous_pack_that_is_not_the_pinned_parent(tmp_path):
    first, second = roll_packs(tmp_path)
    stranger = make_pack(tmp_path, version=1, pack_id='indonesia', name='indonesia-v1')
    client = FakeClient()
    with pytest.raises(installer.PackInstallError) as error:
        installer.roll(client, second, stranger, domain=None)
    assert 'is pack indonesia' in str(error.value)
    other_same_version = make_pack(tmp_path, version=1, texts=[TEXTS[1]],
                                   name='malaysia-v1-other')
    with pytest.raises(installer.PackInstallError) as error:
        installer.roll(client, second, other_same_version, domain=None)
    assert 'names v1' in str(error.value)
    assert client.forgets == [] and client.proposals == []


def test_roll_refuses_a_delta_that_contradicts_the_manifests(tmp_path):
    first, second = roll_packs(tmp_path)
    delta_path = second.directory / 'delta-v1-to-v2.json'
    document = json.loads(delta_path.read_text())
    document['removed'] = []
    delta_path.write_text(json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n')
    client = FakeClient()
    installer.install(client, first, domain=None)
    with pytest.raises(pack_format.PackFormatError) as error:
        installer.roll(client, second, first, domain=None)
    assert 'contradicts the manifests' in str(error.value)
    assert len(client.memories) == 2


def test_migrate_copies_then_retires_the_source_domain(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    installer.install(client, pack, domain=None)
    summary = installer.migrate(client, pack, source_domain=None, dest_domain='public-knowledge')
    assert summary['phase'] == 'copy-complete-retire-pending'
    assert summary['dest_complete'] and summary['dest_present'] == 2
    assert summary['retire']['retired'] == 2
    assert installer.coverage(client, pack, domain='public-knowledge')['complete']
    assert {memory['domain'] for memory in client.memories} == {'public-knowledge'}


def test_migrate_leaves_the_source_alone_when_the_copy_is_incomplete(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    installer.install(client, pack, domain=None)
    client.fail_content = TEXTS[1]
    summary = installer.migrate(client, pack, source_domain=None, dest_domain='public-knowledge')
    assert summary['phase'] == 'copy-incomplete'
    assert summary['retire'] is None and summary['dest_present'] == 1
    assert installer.coverage(client, pack, domain=None)['complete']


def test_migrate_is_a_no_op_within_one_domain(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    installer.install(client, pack, domain=None)
    summary = installer.migrate(client, pack, source_domain=None, dest_domain=None)
    assert summary['phase'] == 'copy-complete'
    assert client.forgets == []
    assert len(client.memories) == 2


def test_ledger_records_what_was_written(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    summary = installer.install(client, pack, domain=None)
    ledger = tmp_path / 'ledgers' / 'malaysia.json'
    installer.write_ledger(ledger, summary)
    document = json.loads(ledger.read_text())
    assert document['schema'] == 'sage.knowledge-pack.ledger.v1'
    assert document['pack'] == 'malaysia' and document['version'] == 1
    assert set(document['chunks']) == set(pack.chunks)
    assert all(row['memory_id'] and row['written'] for row in document['chunks'].values())


def test_conflicting_operations_are_refused_before_any_client(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(tmp_path), '--check',
                                      '--migrate-to', 'public-knowledge'])
    with pytest.raises(installer.PackInstallError):
        installer.main()
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(tmp_path), '--check',
                                      '--dry-run'])
    with pytest.raises(installer.PackInstallError):
        installer.main()
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(tmp_path),
                                      '--roll-from', str(tmp_path), '--retire'])
    with pytest.raises(installer.PackInstallError) as error:
        installer.main()
    assert '--roll-from does not combine' in str(error.value)


def test_presence_is_scoped_to_the_resolved_domain(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient()
    installer.install(client, pack, domain='some-other-domain')
    home = installer.coverage(client, pack, domain=None)
    assert home['domain'] == 'local-field-knowledge'
    assert home['missing'] == 2 and home['present'] == 0
    assert installer.coverage(client, pack, domain='some-other-domain')['complete']


def test_ambiguous_home_domain_is_refused_rather_than_guessed(tmp_path):
    pack = make_pack(tmp_path)
    client = FakeClient(owned=('local-field-knowledge', 'public-knowledge'))
    with pytest.raises(installer.PackInstallError) as error:
        installer.install(client, pack, domain=None)
    assert 'pass --domain explicitly' in str(error.value)
    with pytest.raises(installer.PackInstallError):
        installer.retire(client, pack.tag, domain=None, reason='test')
    summary = installer.install(client, pack, domain='public-knowledge')
    assert summary['installed'] == 2 and summary['domain'] == 'public-knowledge'


def sign_pack(directory):
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    signature = pack_format.sign_manifest((directory / 'manifest.json').read_bytes(), key)
    (directory / 'manifest.sig.json').write_bytes(pack_format.canonical_json(signature) + b'\n')
    return key


def write_root(path, keys):
    path.write_text(json.dumps({'schema': pack_format.SCHEMA_TRUST_ROOT, 'note': 'test root',
                                'keys': keys}))
    return path


def entry(public_hex, status='active'):
    return {'key_id': pack_format.key_id_for(public_hex), 'algorithm': 'ed25519',
            'public_key_hex': public_hex, 'status': status,
            'created_at': '2026-09-20T00:00:00Z', 'note': ''}


def test_verify_contacting_the_node_is_a_failure(tmp_path, monkeypatch, capsys):
    pack = make_pack(tmp_path)
    monkeypatch.setattr(installer, 'build_client',
                        lambda args: pytest.fail('--verify must not build a client'))
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(pack.directory), '--verify',
                                      '--allow-unsigned'])
    assert installer.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['verified'] is True and summary['signed'] is False
    assert summary['unsigned_accepted'] is True


def test_verify_refuses_an_unsigned_pack_by_default(tmp_path, monkeypatch):
    pack = make_pack(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(pack.directory), '--verify'])
    with pytest.raises(pack_format.PackFormatError) as error:
        installer.main()
    assert 'unsigned packs are not accepted' in str(error.value)


def test_verify_accepts_a_foundation_signed_pack_and_names_the_signer(tmp_path, monkeypatch, capsys):
    pack = make_pack(tmp_path)
    key = sign_pack(pack.directory)
    root = write_root(tmp_path / 'pack-trust-root.json',
                      [entry(pack_format.public_key_hex(key))])
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(pack.directory), '--verify',
                                      '--trust-root', str(root)])
    assert installer.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['signed'] is True and summary['key_status'] == 'active'
    assert summary['key_id'] == pack_format.key_id_for(pack_format.public_key_hex(key))
    assert summary['active_keys'] == [summary['key_id']]


def test_verify_refuses_a_revoked_foundation_key(tmp_path, monkeypatch):
    pack = make_pack(tmp_path)
    key = sign_pack(pack.directory)
    root = write_root(tmp_path / 'pack-trust-root.json',
                      [entry(pack_format.public_key_hex(key), status='revoked')])
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(pack.directory), '--verify',
                                      '--trust-root', str(root)])
    with pytest.raises(pack_format.PackFormatError) as error:
        installer.main()
    assert 'revoked' in str(error.value)


def test_install_from_a_delivery_archive_through_the_client(tmp_path, monkeypatch):
    pack = make_pack(tmp_path)
    archive = pack_format.write_archive(pack.directory, tmp_path / 'malaysia-v1.tar.gz')
    client = FakeClient()
    monkeypatch.setattr(installer, 'build_client', lambda args: client)
    ledger = tmp_path / 'ledger.json'
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(archive['archive']),
                                      '--allow-unsigned', '--ledger', str(ledger)])
    assert installer.main() == 0
    assert len(client.proposals) == 2
    assert set(json.loads(ledger.read_text())['chunks']) == set(pack.chunks)


def test_roll_from_the_command_line_writes_a_ledger(tmp_path, monkeypatch, capsys):
    first, second = roll_packs(tmp_path)
    client = FakeClient()
    installer.install(client, first, domain=None)
    ledger = tmp_path / 'ledger.json'
    monkeypatch.setattr(installer, 'build_client', lambda args: client)
    monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(second.directory),
                                      '--roll-from', str(first.directory), '--allow-unsigned',
                                      '--ledger', str(ledger)])
    assert installer.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['phase'] == 'rolled' and summary['class'] == 'brief'
    assert summary['unsigned_accepted'] is True
    recorded = json.loads(ledger.read_text())
    assert set(recorded['chunks']) == set(second.chunks)
    assert {row['confidence_bp'] for row in recorded['chunks'].values()} == {8000}


def test_verify_rejects_ledger_and_registration_flags(tmp_path, monkeypatch):
    pack = make_pack(tmp_path)
    for extra in (['--ledger', str(tmp_path / 'x.json')], ['--register-domain']):
        monkeypatch.setattr(sys, 'argv', ['install-knowledge-pack.py', str(pack.directory),
                                          '--verify', '--allow-unsigned', *extra])
        with pytest.raises(installer.PackInstallError) as error:
            installer.main()
        assert 'writes nothing' in str(error.value)
