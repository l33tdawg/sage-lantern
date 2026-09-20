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
              name=None):
    texts = TEXTS if texts is None else texts
    chunks = []
    for index, text in enumerate(texts, start=1):
        chunks.append(pack_format.build_chunk(
            f'my-{index:03d}', text, title=f'Fact {index}', source='curated:test',
            date='2026-09-20', embedding=[0.5, 0.25, 0.0] if embedded else None,
            embedding_model='nomic-embed-text-v1.5' if embedded else None))
    manifest = pack_format.build_manifest(
        pack_id=pack_id, version=version, title='Test pack', licence='CC-BY-4.0',
        provenance={'source': 'curated:test'}, created_at='2026-09-20T00:00:00Z', chunks=chunks,
        embedding_model='nomic-embed-text-v1.5', embedding_dimension=3)
    directory = tmp_path / (name or f'{pack_id}-v{version}')
    pack_format.write_pack(directory, manifest, {record['sha256']: record for record in chunks})
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
