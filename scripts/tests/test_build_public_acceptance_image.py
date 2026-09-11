"""Recipe tests only; no toolchain compilation, target execution or Docker."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / 'build-public-acceptance-image.py'
SPEC = importlib.util.spec_from_file_location('public_image_builder', PATH)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class ImageRecipeTests(unittest.TestCase):
    def test_native_and_helper_tags_separate(self):
        for target in BUILDER.TARGETS:
            command = BUILDER.build_command('go', target, '-buildid=')
            self.assertEqual(any('v119testfixture' in part for part in command), target == 'v119-governance-fixture')
            self.assertIn('-mod=readonly', command)
            self.assertIn('-buildvcs=false', command)
            self.assertIn('-trimpath', command)
            self.assertNotIn('run', command)

    def test_environment_offline_and_no_ambient_sage_configuration(self):
        with patch.dict(os.environ, {'SAGE_HOME': '/private-sage', 'SAGE_AGENT_KEY': '/secret.key',
                                    'GOFLAGS': '-tags=v119testfixture', 'GOPROXY': 'https://example.invalid',
                                    'HTTP_PROXY': 'https://example.invalid'}):
            env = BUILDER.environment('amd64')
        for key in ('SAGE_HOME', 'SAGE_AGENT_KEY', 'HTTP_PROXY'):
            self.assertNotIn(key, env)
        self.assertEqual(env['CGO_ENABLED'], '0')
        self.assertEqual(env['GOTOOLCHAIN'], 'local')
        self.assertEqual(env['GOPROXY'], 'off')
        self.assertEqual(env['GOENV'], 'off')
        self.assertEqual(env['GOFLAGS'], '')
        self.assertEqual(env['GOWORK'], 'off')
        self.assertEqual(env['GOVCS'], '*:off')
        self.assertNotIn('GOARM64', env)

    def test_docker_context_recipe_cannot_pull_base_or_execute_build_steps(self):
        recipe = BUILDER.dockerfile().decode()
        self.assertTrue(recipe.startswith('FROM scratch\n'))
        for forbidden in ('RUN ', 'ADD ', 'COPY . ', '--from=', '# syntax=', 'VOLUME'):
            self.assertNotIn(forbidden, recipe)
        self.assertIn('COPY sage-gui v119-governance-fixture cometbft /app/', recipe)

    def test_compile_requires_review_and_both_pins_before_any_tools(self):
        with patch.object(BUILDER, 'prepare', side_effect=AssertionError), contextlib.redirect_stderr(io.StringIO()):
            for args in (['--phase', 'compile', '--output', '/tmp/new'],
                         ['--phase', 'compile', '--reviewed-build', '--output', '/tmp/new']):
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    BUILDER.main(args)

    def test_graph_copies_only_declared_sources_and_embed_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            directory = root / 'cmd/sage-gui'
            directory.mkdir(parents=True)
            for name in ('go.mod', 'go.sum'):
                (root / name).write_text('synthetic metadata')
            (directory / 'main.go').write_text('package main')
            (directory / 'asset.html').write_text('synthetic public asset')
            (root / 'agent.key').write_text('must not copy')
            record = dict(Dir=str(directory), ImportPath='example/cmd/sage-gui',
                          GoFiles=['main.go'], EmbedFiles=['asset.html'])
            with patch.object(BUILDER.RELEASE, 'run', return_value=json.dumps(record)):
                sources, _, _ = BUILDER.graph(root, 'go', {}, 'sage-gui')
            self.assertEqual(set(sources), {'go.mod', 'go.sum', 'cmd/sage-gui/main.go', 'cmd/sage-gui/asset.html'})
            record['EmbedFiles'] = ['../../agent.key']
            with patch.object(BUILDER.RELEASE, 'run', return_value=json.dumps(record)), self.assertRaises(ValueError):
                BUILDER.graph(root, 'go', {}, 'sage-gui')

    def test_private_and_linked_inputs_rejected(self):
        for name in ('agent.key', '.sage/config.json', '.env', '../outside', '/absolute', 'backup.sqlite'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                BUILDER.RELEASE.relative_input(name)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / 'main.go'
            target.write_text('package main')
            link = root / 'link.go'
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                BUILDER.RELEASE.source_bytes(link)

    def test_static_elf_architecture_and_no_dynamic_loader(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'binary'
            header = bytearray(120)
            header[:7] = b'\x7fELF\x02\x01\x01'
            struct.pack_into('<H', header, 18, 183)
            struct.pack_into('<Q', header, 32, 64)
            struct.pack_into('<HH', header, 54, 56, 1)
            struct.pack_into('<I', header, 64, 1)
            path.write_bytes(header)
            BUILDER.verify_static_elf(path, 'arm64')
            with self.assertRaises(ValueError):
                BUILDER.verify_static_elf(path, 'amd64')
            for kind in (2, 3):
                struct.pack_into('<I', header, 64, kind)
                path.write_bytes(header)
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    BUILDER.verify_static_elf(path, 'arm64')


if __name__ == '__main__':
    unittest.main()
