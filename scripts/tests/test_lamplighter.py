import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lantern'))
import knowledge_pack as kp
import lamplighter as ll
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class LamplighterTests(unittest.TestCase):
    def setUp(self):
        batching = patch.object(ll, 'BATCH_SIZE', 2)
        batching.start()
        self.addCleanup(batching.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        key = Ed25519PrivateKey.generate()
        public = kp.public_key_hex(key)
        self.trust = {'schema': 'sage.knowledge-pack.trust-root.v1', 'note': 'test only',
            'keys': [{'key_id': kp.key_id_for(public), 'algorithm': 'ed25519',
                'public_key_hex': public, 'status': 'active',
                'created_at': '2026-09-27T00:00:00Z', 'note': 'synthetic'}]}
        self.chunks = [kp.build_chunk(str(i), 'Synthetic fact ' + str(i),
            licence='CC0-1.0', source='synthetic', date='2026-09-27') for i in range(6)]
        self.manifest = kp.build_manifest(pack_id='synthetic-lab', version=1,
            pack_class='reference', title='Synthetic test', licence='CC0-1.0',
            provenance={'source': 'synthetic'}, created_at='2026-09-27T00:00:00Z',
            chunks=self.chunks, embedding_model='test', embedding_dimension=1)
        raw = kp.manifest_bytes(self.manifest)
        self.digest = ll.sha(raw)
        self.source = self.root / 'source'
        kp.write_pack(self.source, self.manifest, {c['sha256']: c for c in self.chunks}, kp.sign_manifest(raw, key))
        self.peers = []
        for _ in range(2):
            server = ll.server('127.0.0.1', 0, self.source, self.trust)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.peers.append('http://127.0.0.1:' + str(server.server_port))

    def test_multisource_and_resume(self):
        dest = self.root / 'received'
        first = ll.fetch(self.peers, self.digest, dest, self.trust, max_chunks=2)
        self.assertFalse(first['complete'])
        self.assertEqual(first['downloaded'], 2)
        result = ll.fetch(self.peers, self.digest, dest, self.trust)
        self.assertTrue(result['complete'])
        self.assertEqual((result['reused'], result['downloaded']), (2, 4))
        self.assertTrue(all(n > 0 for n in result['chunks_by_peer'].values()))
        self.assertFalse(result['sage_imported'])

    def test_progress_pause_and_resume_preserve_verified_chunks(self):
        events = []
        paused = [False]
        def progress(event):
            events.append(event)
            if event.get('downloaded', 0) >= 2:
                paused[0] = True
        dest = self.root / 'paused'
        with self.assertRaises(InterruptedError):
            ll.fetch(self.peers, self.digest, dest, self.trust,
                     progress=progress, cancelled=lambda: paused[0])
        self.assertEqual(len(list((dest / 'chunks').glob('*.json'))), 2)
        self.assertFalse((dest / 'transfer-receipt.json').exists())
        result = ll.fetch(self.peers, self.digest, dest, self.trust)
        self.assertEqual(result['reused'], 2)
        self.assertTrue(result['complete'])
        self.assertEqual(events[-1]['verified'], 2)

    def test_corrupt_peer_fallback(self):
        original = ll.get
        def read(peer, path, limit, **kwargs):
            if peer == self.peers[0] and '/chunks/' in path:
                return b'corrupted'
            return original(peer, path, limit, **kwargs)
        with patch.object(ll, 'get', side_effect=read):
            result = ll.fetch(self.peers, self.digest, self.root / 'received', self.trust)
        self.assertTrue(result['complete'])
        self.assertEqual(result['chunks_by_peer'][self.peers[0]], 0)
        self.assertGreater(result['failed_peer_attempts'], 0)

    def test_damaged_cache_refetched(self):
        dest = self.root / 'received'
        ll.fetch(self.peers, self.digest, dest, self.trust, max_chunks=2)
        next((dest / 'chunks').iterdir()).write_bytes(b'bad')
        result = ll.fetch(self.peers, self.digest, dest, self.trust)
        self.assertEqual(result['reused'], 1)
        self.assertTrue(result['complete'])

    def test_revoked_manifest_rejected_before_destination(self):
        self.trust['keys'][0]['status'] = 'revoked'
        dest = self.root / 'received'
        with self.assertRaises(ValueError):
            ll.fetch(self.peers, self.digest, dest, self.trust)
        self.assertFalse(dest.exists())

    def test_serve_allowlist_no_traversal_or_private_files(self):
        for path in ('/../manifest.json', '/etc/passwd', '/packs/' + self.digest + '/../secret', '/catalogue.json?x=1'):
            with self.assertRaises(OSError):
                ll.get(self.peers[0], path, 4096)

    def test_no_public_peers_or_redirect_credentials(self):
        for peer in ('http://8.8.8.8:80', 'http://user:pass@127.0.0.1:80', 'http://localhost:80', 'http://0.0.0.0:80'):
            with self.assertRaises(ValueError):
                ll.peer_url(peer)

    def test_symlink_cache_refused(self):
        dest = self.root / 'received'
        dest.mkdir()
        (dest / 'chunks').symlink_to(self.source / 'chunks')
        with self.assertRaises(ValueError):
            ll.fetch(self.peers, self.digest, dest, self.trust)

    def test_interruption_leaves_verified_chunks_for_resume(self):
        original = ll.get
        seen = []
        def fail(peer, path, limit, **kwargs):
            if '/chunks/' in path:
                seen.append(path)
                if len(set(seen)) > 1:
                    raise OSError('peer unavailable')
            return original(peer, path, limit, **kwargs)
        dest = self.root / 'received'
        with patch.object(ll, 'get', side_effect=fail), self.assertRaises(ValueError):
            ll.fetch(self.peers, self.digest, dest, self.trust)
        result = ll.fetch(self.peers, self.digest, dest, self.trust)
        self.assertEqual(result['reused'], 2)
        self.assertTrue(result['complete'])


if __name__ == '__main__':
    unittest.main()
