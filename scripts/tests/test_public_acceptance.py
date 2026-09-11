"""Offline launcher/evidence validation only. No Docker or consensus execution."""

import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import signal
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'lantern'
sys.path.insert(0, str(SOURCE))
from public_acceptance_fixture import AcceptanceError, verify_memory, verify_network, verify_profile

SPEC = importlib.util.spec_from_file_location('public_acceptance_launcher', SOURCE / 'run-public-acceptance.py')
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)

IMAGES = {role: 'sha256:' + digit * 64 for role, digit in [('native', 'a'), ('comet', 'b')]}
PINS = {'version': 'public-test.a877e282aa1fdf5a', 'fork': 1}


class LauncherTests(unittest.TestCase):
    def test_port_diagnostics_preserve_empty_extra_and_wildcard_bindings(self):
        for ports in ({}, {'8080/tcp': None}, {'9999/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '12345'}]}):
            row = {'NetworkSettings': {'Ports': ports}, 'HostConfig': {'PortBindings': ports},
                   'Config': {'Env': ['SECRET=must-not-copy']}}
            result = LAUNCHER.diagnostic_ports(row)
            self.assertEqual(result, {'NetworkSettings.Ports': ports, 'HostConfig.PortBindings': ports})
            self.assertNotIn('SECRET', json.dumps(result))
        for ports in ({str(index) + '/tcp': None for index in range(33)},
                      {'8080/tcp': [{'HostIp': 'secret', 'HostPort': '123'}]}):
            self.assertEqual(LAUNCHER.diagnostic_ports({'NetworkSettings': {'Ports': ports}}),
                             {'unavailable': 'invalid_or_oversized_port_metadata'})

    def arguments(self, phase):
        result = ['--phase', phase]
        for role, value in IMAGES.items():
            result.extend(['--' + role + '-image', value])
        return result

    def test_config_has_no_shared_genesis_fixed_ports_or_external_network(self):
        config = LAUNCHER.compose_config('lantern-public-' + 'a' * 32, '/private/tmp/owned', IMAGES)
        self.assertEqual(len(config['services']), 4)
        self.assertEqual(config['networks'], {'isolated': {'internal': True}})
        for service in config['services'].values():
            self.assertEqual(service['pull_policy'], 'never')
            self.assertNotIn('build', service)
            self.assertNotIn('container_name', service)
            self.assertNotIn('privileged', service)
            self.assertNotIn('ports', service)
            for port in service.get('ports', []):
                self.assertEqual(port['host_ip'], '127.0.0.1')
                self.assertNotIn('published', port)
                self.assertIn(port['target'], (8080, 26657))
            for mount in service['volumes']:
                self.assertTrue(mount.startswith('/private/tmp/owned/'))

    def test_requires_immutable_images(self):
        for invalid in ['sage:latest', 'repo@sha256:' + 'a' * 64, 'sha256:' + 'z' * 64, '', True]:
            with self.subTest(invalid=invalid), self.assertRaises(AcceptanceError):
                LAUNCHER.image_id(invalid)

    def test_config_private_unique_and_contains_no_keys(self):
        with tempfile.TemporaryDirectory() as root, patch.object(tempfile, 'tempdir', root):
            first, second = LAUNCHER.build_config(IMAGES), LAUNCHER.build_config(IMAGES)
            self.assertNotEqual(first['project'], second['project'])
            self.assertNotEqual(first['workspace'], second['workspace'])
            directory = Path(first['workspace'])
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual({entry.name for entry in directory.iterdir()}, {'compose.json', 'owner.json'})
            self.assertEqual(LAUNCHER.load_owned(directory), first)
            for path in directory.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_or_symlink_file_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            sentinel = Path(root) / 'sentinel'
            sentinel.write_text('keep')
            link = Path(root) / 'link'
            link.symlink_to(sentinel)
            for path in (sentinel, link):
                with self.assertRaises(FileExistsError):
                    LAUNCHER.write_private(path, {})
            self.assertEqual(sentinel.read_text(), 'keep')

    def test_dryrun_and_unreviewed_run_never_execute_or_write(self):
        with patch.object(LAUNCHER, 'Docker', side_effect=AssertionError), \
                patch.object(tempfile, 'mkdtemp', side_effect=AssertionError):
            for phase, reviewed, code in [('dry-run', False, 0), ('run', False, 2)]:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    args = self.arguments(phase) + (['--reviewed-run'] if reviewed else [])
                    self.assertEqual(LAUNCHER.main(args), code)
                self.assertNotEqual(json.loads(output.getvalue())['acceptance'], 'passed')

    def test_preflight_inspects_only_local_images_never_pulls_or_passes(self):
        calls = []
        def probe(command):
            calls.append(command)
            if command[0] == 'info':
                return json.dumps({'NCPU': 4, 'MemTotal': 12 * 1024 ** 3})
            return json.dumps([{'Id': command[-1], 'Os': 'linux'}])
        with patch.object(LAUNCHER.shutil, 'disk_usage') as usage:
            usage.return_value.free = 16 * 1024 ** 3
            self.assertEqual(LAUNCHER.preflight(IMAGES, probe)['acceptance'], 'not_run')
        self.assertEqual([call[0] for call in calls], ['info', 'image', 'image'])

    def test_preflight_failure_not_fake_pass(self):
        for response in ['{}', 'bad json', '{"NCPU":1,"MemTotal":1}']:
            with self.subTest(response=response), self.assertRaises(AcceptanceError):
                LAUNCHER.preflight(IMAGES, lambda command: response)
        with patch.object(LAUNCHER.shutil, 'which', return_value=None), self.assertRaises(AcceptanceError):
            LAUNCHER.Docker()

    def test_docker_command_does_not_read_host_config(self):
        docker = LAUNCHER.Docker.__new__(LAUNCHER.Docker)
        docker.executable = '/synthetic/docker'
        docker.socket = Path('/synthetic/docker.sock')
        configurations = []
        def spawn(command, **options):
            environment = options['env']
            configuration = Path(environment['DOCKER_CONFIG'])
            configurations.append(configuration)
            self.assertEqual(environment['HOME'], str(configuration))
            self.assertNotEqual(configuration, Path.home())
            self.assertEqual(list(configuration.iterdir()), [])
            self.assertEqual(stat.S_IMODE(configuration.stat().st_mode), 0o700)
            self.assertEqual(set(environment), {'PATH', 'HOME', 'DOCKER_CONFIG', 'DOCKER_HOST'})
            self.assertEqual(command[:3], ['/synthetic/docker', '--host', 'unix:///synthetic/docker.sock'])
            from unittest.mock import Mock
            process = Mock(returncode=0)
            process.poll.return_value = 0
            return process
        with patch.object(LAUNCHER.subprocess, 'Popen', side_effect=spawn):
            self.assertEqual(docker(['info']), '')
        self.assertFalse(configurations[0].exists())

    def test_generator_is_resource_bounded_and_networkless(self):
        ceremony = LAUNCHER.Ceremony.__new__(LAUNCHER.Ceremony)
        ceremony.project = 'synthetic-project'
        commands = []
        ceremony.command = lambda command: commands.append(command)
        ceremony.ephemeral(IMAGES['comet'], 'cometbft', ['version'], [])
        command = commands[0]
        for option, value in (('--cpus', '1'), ('--memory', '2g'), ('--pids-limit', '256'), ('--network', 'none'), ('--pull', 'never')):
            self.assertEqual(command[command.index(option) + 1], value)

    def test_owner_marker_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as root, patch.object(tempfile, 'tempdir', root):
            plan = LAUNCHER.build_config(IMAGES)
            marker = Path(plan['workspace']) / 'owner.json'
            target = marker.with_name('other.json')
            marker.rename(target)
            marker.symlink_to(target)
            with self.assertRaises(AcceptanceError):
                LAUNCHER.load_owned(plan['workspace'])

    def test_strict_json_rejects_duplicates_and_constants(self):
        for text in ['{"key":1,"key":2}', '{"key":NaN}', 'not json']:
            with self.subTest(text=text), self.assertRaises(AcceptanceError):
                LAUNCHER.strict_json(text)

    def test_rpc_rejects_nonfixed_paths_before_connection(self):
        with patch.object(LAUNCHER.http.client, 'HTTPConnection', side_effect=AssertionError):
            for path in ['/status/other', '/status?redirect=1', '/validators?height=1', '/block?height=-1']:
                with self.subTest(path=path), self.assertRaises(AcceptanceError):
                    LAUNCHER.loopback_json(41000, path)


class ProtocolDouble:
    """Recorded command/RPC simulator. Its success is NOT real consensus evidence."""

    def __init__(self, ceremony):
        self.ceremony = ceremony
        self.commands = []
        self.version = 6
        self.height = 20
        self.approved = False
        self.lost_submit = False
        self.wrong_peer = False
        self.hold_upgrade = False
        self.clock = 0
        self.network_exists = False
        self.containers = {}
        self.submission = dict(memory_id='ce76d45e-e9a8-4b33-87f8-8715961e0b3f', tx_hash='c' * 64,
                               committed=True, committed_height=10, status='proposed')

    def __call__(self, args, timeout=30):
        self.commands.append(args)
        if args[:2] == ['network', 'create']:
            self.network_exists = True
            return 'd' * 64
        if args[:2] == ['run', '-d']:
            index = len(self.containers)
            name = args[args.index('--name') + 1]
            self.containers[name] = index
            return str(index + 1) * 64
        if args[0] == 'inspect':
            name = args[1]
            if name not in self.containers:
                name = next(name for name, index in self.containers.items() if args[1] == str(index + 1) * 64)
            index = self.containers[name]
            return json.dumps([{'Name': '/' + name, 'Image': IMAGES['native'], 'State': {'Running': True},
                'Config': {'Labels': {LAUNCHER.LABEL: self.ceremony.project}},
                'HostConfig': {'PortBindings': {}},
                'NetworkSettings': {'Ports': {'8080/tcp': None}}}])
        if args[:3] == ['container', 'ls', '-aq']:
            return '\n'.join(str(index + 1) * 64 for index in self.containers.values())
        if args[:2] == ['rm', '-f']:
            name = next(name for name, index in self.containers.items() if args[2] == str(index + 1) * 64)
            del self.containers[name]
            return ''
        if args[:3] == ['network', 'ls', '-q']:
            return 'd' * 64 if self.network_exists else ''
        if args[:2] == ['network', 'inspect']:
            return json.dumps([{'Labels': {LAUNCHER.LABEL: self.ceremony.project}, 'Name': self.ceremony.network}])
        if args[:2] == ['network', 'rm']:
            self.network_exists = False
            return ''
        assert args[0] == 'exec', args
        if args[2] == LAUNCHER.SIGNER and args[-1] == 'rpc':
            assert args[3] == '--path' and len(args) == 6
            return json.dumps(self.rpc(self.containers[args[1]], args[4]))
        if args[2] == LAUNCHER.NATIVE:
            target = int(args[args.index('--target') + 1])
            assert target == self.version + 1
            assert '--wait' not in args
            if not self.hold_upgrade:
                self.version = target
            return 'Synthetic successful native proposal'
        assert args[2] == LAUNCHER.SIGNER and '--local' in args and args[-1] == 'request'
        path = args[args.index('--path') + 1]
        method = args[args.index('--method') + 1]
        key = args[args.index('--key') + 1]
        role = 'operator' if key.endswith('/agent.key') else Path(key).stem
        body = json.loads(args[args.index('--body') + 1]) if '--body' in args else None
        if path == '/v1/agent/register':
            if role == 'heartbeat':
                self.height += 1
            return json.dumps({'agent_id': self.ceremony.actors[role], 'role': body['role']})
        if path.endswith('/policy'):
            assert method == 'PUT' and role == 'operator'
            assert body == dict(role='member', profile='standard', home_domain=LAUNCHER.DOMAIN, clearance=0, capabilities=0)
            self.approved = True
            return '{}'
        if path == '/v1/agent/me':
            return json.dumps(dict(agent_id=self.ceremony.actors['proposer'], role='member',
                enrollment_status='active' if self.approved else 'pending_review', registration_status='active',
                approval_required=not self.approved, can_read=True, can_write=self.approved,
                clearance=0, home_domain=LAUNCHER.DOMAIN))
        if path == '/v1/memory/submit':
            assert self.approved and role == 'proposer' and method == 'POST'
            assert body['classification'] == 0 and body['confidence_score'] == 0.5 and 'confidence' not in body
            assert json.loads(body['content'])['schema'] == 'sage.lantern.public-qa.v1'
            self.content = body['content']
            if self.lost_submit:
                raise AcceptanceError('docker_command_timeout')
            return json.dumps(self.submission)
        assert path == '/v1/memory/' + self.submission['memory_id'] and method == 'GET'
        return json.dumps(dict(memory_id=self.submission['memory_id'], submitting_agent=self.ceremony.actors['proposer'],
            domain_tag=LAUNCHER.DOMAIN, memory_type='inference', classification=0, content=self.content,
            content_hash=hashlib.sha256(self.content.encode()).hexdigest(), status='committed'))

    def rpc(self, port, path):
        index = port
        if path == '/status':
            return {'result': {'node_info': {'id': 'wrong' if self.wrong_peer else self.ceremony.node_ids[index],
                'network': self.ceremony.project}, 'sync_info': {'latest_block_height': str(self.height)}}}
        if path == '/abci_info':
            return {'result': {'response': {'app_version': str(self.version)}}}
        if path.startswith('/block?'):
            height = path.split('height=')[1]
            return {'result': {'block': {'header': {'height': height, 'chain_id': self.ceremony.project,
                'version': {'app': str(self.version)}, 'app_hash': 'a' * 64}}}}
        assert path.startswith('/validators?height=') and path.endswith('&per_page=100')
        height = path.split('height=')[1].split('&')[0]
        return {'result': {'block_height': height, 'count': '4', 'total': '4', 'validators': [
            {'pub_key': {'type': 'tendermint/PubKeyEd25519', 'value': base64.b64encode(bytes.fromhex(key)).decode()},
             'voting_power': str(power)} for key, power in self.ceremony.validators]}}

    def sleep(self, seconds):
        self.clock += seconds


class CeremonyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(tempfile, 'tempdir', self.temp.name):
            self.plan = LAUNCHER.build_config(IMAGES, PINS)
        self.ceremony = LAUNCHER.Ceremony(self.plan, None)
        self.ceremony.actors = {'operator': 'a' * 64, 'heartbeat': 'b' * 64, 'proposer': 'c' * 64}
        self.ceremony.node_ids = [str(index + 1) * 40 for index in range(4)]
        self.ceremony.validators = {(str(index + 1) * 64, 10) for index in range(4)}
        self.world = ProtocolDouble(self.ceremony)
        self.ceremony.docker = self.world
        self.ceremony.rpc = self.world.rpc
        self.ceremony.clock = lambda: self.world.clock
        self.ceremony.sleep = self.world.sleep
        self.ceremony.deadline = 43200

    def test_full_native_command_contract_and_owned_cleanup(self):
        self.ceremony.start()
        result = self.ceremony.run_protocol()
        self.assertEqual(result['acceptance'], 'passed')
        self.assertFalse(result['offline_proof'])
        upgrades = [int(args[args.index('--target') + 1]) for args in self.world.commands if '--target' in args]
        self.assertEqual(upgrades, list(range(7, 26)))
        launches = [args for args in self.world.commands if args[:2] == ['run', '-d']]
        self.assertEqual(len(launches), 4)
        for args in launches:
            self.assertNotIn('-p', args)
            self.assertNotIn('--publish', args)
            self.assertIn('never', args)
            self.assertNotIn('--privileged', args)
        self.assertTrue(self.ceremony.cleanup())
        self.assertFalse(self.world.containers or self.world.network_exists)

    def test_lost_submit_not_retried(self):
        self.ceremony.start()
        self.world.lost_submit = True
        with self.assertRaises(AcceptanceError):
            self.ceremony.run_protocol()
        self.assertEqual(sum('/v1/memory/submit' in args for args in self.world.commands), 1)

        self.assertEqual(self.ceremony.phase, 'proposal_unknown_until_receipt')
        self.assertTrue(self.ceremony.cleanup())

    def test_default_rpc_uses_exact_owned_exec_without_key(self):
        calls = []
        def docker(args, timeout=30):
            calls.append(args)
            self.assertLessEqual(timeout, 10)
            return '{"result":{}}'
        ceremony = LAUNCHER.Ceremony(self.plan, docker)
        self.assertEqual(ceremony.rpc(2, '/block?height=42'), {'result': {}})
        self.assertEqual(calls, [['exec', ceremony.name(2), LAUNCHER.SIGNER,
                                 '--path', '/block?height=42', 'rpc']])
        for index, path in [(True, '/status'), (4, '/status'), (0, '/broadcast_tx_commit')]:
            with self.assertRaises(AcceptanceError):
                ceremony.rpc(index, path)
        self.assertEqual(len(calls), 1)

    def test_full_protocol_through_default_container_rpc(self):
        self.ceremony.rpc = self.ceremony.container_rpc
        self.ceremony.start()
        result = self.ceremony.run_protocol()
        self.assertEqual(result['acceptance'], 'passed')
        queries = [args for args in self.world.commands if args[-1:] == ['rpc']]
        self.assertEqual({args[1] for args in queries}, {self.ceremony.name(index) for index in range(4)})
        self.assertTrue(all('--key' not in args for args in queries))
        self.assertTrue(self.ceremony.cleanup())

    def test_no_publication_guard_rejects_requested_and_effective_ports(self):
        for section, field in [('HostConfig', 'PortBindings'), ('NetworkSettings', 'Ports')]:
            def docker(args, timeout=30):
                raw = self.world(args, timeout)
                if args[0] == 'inspect':
                    rows = json.loads(raw)
                    rows[0][section][field] = {'8080/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '8080'}]}
                    return json.dumps(rows)
                return raw
            self.ceremony.docker = docker
            with self.assertRaisesRegex(AcceptanceError, 'unexpected_exposed_port'):
                self.ceremony.start()
    def test_late_acceptance_barrier_uses_max_then_waits_for_lagging_nodes(self):
        self.ceremony.start()
        reads = []
        def command(args, timeout=30):
            result = self.world(args, timeout)
            if '--path' in args and args[args.index('--path') + 1] == '/v1/memory/' + self.world.submission['memory_id']:
                reads.append(args[1])
                if len(reads) == 4:
                    self.world.height = 78
            return result
        def rpc(port, path):
            result = self.world.rpc(port, path)
            if path == '/status' and len(reads) == 4 and port == 0:
                result['result']['sync_info']['latest_block_height'] = str(max(80, self.world.height))
            if path.startswith('/block?'):
                self.assertEqual(len(reads), 4)
                self.assertGreaterEqual(self.world.height, 81)
                self.assertEqual(path, '/block?height=81')
            return result
        self.ceremony.docker, self.ceremony.rpc = command, rpc
        result = self.ceremony.run_protocol()
        evidence = result['evidence']
        self.assertEqual(evidence['acceptance_barrier_height'], 80)
        self.assertEqual(evidence['network']['application_state_height'], 80)
        self.assertEqual(evidence['network']['block_height'], 81)
        self.assertEqual(self.world.submission['committed_height'], 10)
        self.assertEqual(len(reads), 4)
        self.assertGreaterEqual(self.world.clock, 9)

    def test_wrong_node_identity_blocks_all_mutations(self):
        self.ceremony.start()
        self.world.wrong_peer = True
        with self.assertRaises(AcceptanceError):
            self.ceremony.run_protocol()
        self.assertFalse(any(args[0] == 'exec' for args in self.world.commands))

    def test_stalled_upgrade_is_bounded_and_not_reproposed(self):
        self.ceremony.start()
        self.world.hold_upgrade = True
        with self.assertRaises(AcceptanceError):
            self.ceremony.run_protocol()
        self.assertEqual(sum('--target' in args for args in self.world.commands), 1)
        self.assertLessEqual(self.world.clock, 1803)

    def test_cleanup_unknown_retains_owned_state(self):
        node = Path(self.plan['workspace']) / 'node0'
        node.mkdir()
        (node / 'synthetic.key').write_text('synthetic')
        self.ceremony.docker = lambda *args, **kwargs: (_ for _ in ()).throw(AcceptanceError('unavailable'))
        self.assertFalse(self.ceremony.cleanup())
        self.assertTrue(node.exists())

    def test_private_bounded_diagnostics_before_cleanup(self):
        self.ceremony.start()
        def diagnostic(args, timeout=30):
            if args[0] == 'logs':
                self.assertEqual(args[1:3], ['--tail', '200'])
                self.assertEqual(timeout, 5)
                return 'synthetic-only-log' * 20000
            return self.world(args, timeout)
        self.ceremony.docker = diagnostic
        self.assertEqual(self.ceremony.retain_diagnostics(), {'containers': 4, 'complete': True})
        for index in range(4):
            path = self.ceremony.workspace / (self.ceremony.name(index) + '.startup.log')
            self.assertEqual(path.stat().st_size, 262144)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            metadata = json.loads(path.with_name(self.ceremony.name(index) + '.state.json').read_text())
            self.assertEqual(metadata['ports_at_start_check'], metadata['ports_before_cleanup'])
            self.assertEqual(set(metadata['ports_before_cleanup']['NetworkSettings.Ports']),
                             {'8080/tcp'})
        self.assertTrue(self.ceremony.cleanup())
        self.assertTrue(path.exists())

    def test_diagnostics_refuse_foreign_image_without_reading_logs(self):
        self.ceremony.start()
        def diagnostic(args, timeout=30):
            self.assertNotEqual(args[0], 'logs')
            raw = self.world(args, timeout)
            if args[0] == 'inspect':
                rows = json.loads(raw)
                rows[0]['Image'] = 'sha256:' + 'f' * 64
                return json.dumps(rows)
            return raw
        self.ceremony.docker = diagnostic
        self.assertEqual(self.ceremony.retain_diagnostics(), {'containers': 0, 'complete': False})

    def test_execute_failure_still_tears_down_without_secret_error(self):
        def fail():
            raise RuntimeError('must not leak this synthetic secret')
        with patch.object(LAUNCHER, 'Ceremony', return_value=self.ceremony), \
                patch.object(self.ceremony, 'prepare'), patch.object(self.ceremony, 'run_protocol', side_effect=fail):
            result = LAUNCHER.execute(self.plan, self.world, 43200)
        self.assertEqual(result['acceptance'], 'failed')
        self.assertTrue(result['cleanup_confirmed'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertFalse(self.world.containers or self.world.network_exists)

    def test_execute_success_not_reported_until_teardown(self):
        with patch.object(LAUNCHER, 'Ceremony', return_value=self.ceremony), patch.object(self.ceremony, 'prepare'):
            result = LAUNCHER.execute(self.plan, self.world, 43200)
        self.assertEqual(result['acceptance'], 'passed')
        self.assertTrue(result['cleanup_confirmed'])
        self.assertTrue((Path(self.plan['workspace']) / 'result.json').exists())

    def test_reviewed_main_reaches_runner_not_a_blocked_facade(self):
        arguments = ['--phase', 'run', '--reviewed-run', '--native-image', IMAGES['native'],
                     '--comet-image', IMAGES['comet'], '--expected-native-version', PINS['version'],
                     '--expected-native-fork', '1']
        output = io.StringIO()
        with patch.object(LAUNCHER, 'Ceremony', return_value=self.ceremony), \
                patch.object(LAUNCHER, 'Docker', return_value=self.world), \
                patch.object(LAUNCHER, 'preflight', return_value={'acceptance': 'not_run'}), \
                patch.object(LAUNCHER, 'build_config', return_value=self.plan), \
                patch.object(self.ceremony, 'prepare'), contextlib.redirect_stdout(output):
            self.assertEqual(LAUNCHER.main(arguments), 0)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])['acceptance'], 'passed')
        self.assertEqual(sum('/v1/memory/submit' in args for args in self.world.commands), 1)

    def test_termination_handler_cleans_up_and_restores_handlers(self):
        previous = signal.getsignal(signal.SIGTERM)
        def terminate():
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        with patch.object(LAUNCHER, 'Ceremony', return_value=self.ceremony), \
                patch.object(self.ceremony, 'prepare'), patch.object(self.ceremony, 'run_protocol', side_effect=terminate):
            result = LAUNCHER.execute(self.plan, self.world, 43200)
        self.assertEqual(result['reason'], 'interrupted')
        self.assertTrue(result['cleanup_confirmed'])
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_cleanup_refuses_foreign_label(self):
        self.ceremony.start()
        def wrong_label(args, timeout=30):
            raw = self.world(args, timeout)
            if args[0] == 'inspect':
                decoded = json.loads(raw)
                decoded[0]['Config']['Labels'][LAUNCHER.LABEL] = 'foreign'
                return json.dumps(decoded)
            return raw
        self.ceremony.docker = wrong_label
        self.assertFalse(self.ceremony.cleanup())
        self.assertFalse(any(args[0] == 'rm' for args in self.world.commands))

    def test_prepare_only_uses_owned_generated_material(self):
        from nacl.signing import SigningKey
        calls = []
        def ephemeral(image, entrypoint, args, mounts):
            calls.append((entrypoint, args, mounts))
            workspace = Path(self.plan['workspace'])
            if args == ['version']:
                return f"sage-gui {PINS['version']} (commit synthetic, built 1970-01-01, max-app-v27)"
            if args[0] == 'testnet':
                self.assertEqual(args[args.index('--home') + 1], '/generated')
                validators = []
                for index in range(4):
                    key = SigningKey(bytes([index + 1]) * 32)
                    validators.append(dict(pub_key={'type': 'tendermint/PubKeyEd25519',
                        'value': base64.b64encode(key.verify_key.encode()).decode()}, power='10'))
                    config = workspace / f'genesis/node{index}/config'
                    config.mkdir(parents=True)
                    (config / 'priv_validator_key.json').write_text('synthetic generator output')
                for index in range(4):
                    (workspace / f'genesis/node{index}/config/genesis.json').write_text(json.dumps(
                        {'chain_id': 'fresh-generator-chain', 'validators': validators}))
                return ''
            if args[0] == 'show-node-id':
                node = mounts[-1].split('genesis/node')[1][0]
                return str(int(node) + 1) * 40
            seed = (workspace / 'node0/agent.key').read_bytes()
            return SigningKey(seed).verify_key.encode().hex()
        with patch.object(self.ceremony, 'ephemeral', side_effect=ephemeral):
            self.ceremony.prepare()
        self.assertEqual(self.ceremony.phase, 'prepared')
        self.assertEqual(len(set(self.ceremony.actors.values())), 3)
        workspace = Path(self.plan['workspace'])
        for index in range(4):
            home = workspace / f'node{index}'
            self.assertEqual((home / 'version.txt').read_text(), PINS['version'] + '\n')
            self.assertEqual((home / 'fork-version.txt').read_text(), '1\n')
            config = json.loads((home / 'config.yaml').read_bytes())
            self.assertEqual(len(config['quorum']['peers']), 3)
            self.assertFalse(config['federation']['enabled'])
            self.assertEqual(len((home / 'agent.key').read_bytes()), 32)
            self.assertEqual(stat.S_IMODE((home / 'agent.key').stat().st_mode), 0o600)
            self.assertEqual(json.loads((home / 'data/cometbft/config/genesis.json').read_bytes())['chain_id'],
                             self.plan['project'])
        self.assertTrue(all(str(workspace) in mount for _, _, mounts in calls for mount in mounts if 'src=' in mount))

    def test_bad_provenance_pins_rejected_before_execution(self):
        for version, fork in [('dev', 1), (PINS['version'], True), (PINS['version'], 2),
                              (None, 1), (PINS['version'], 0)]:
            with self.subTest(version=version, fork=fork):
                self.plan['native_provenance'] = {'version': version, 'fork': fork}
                with patch.object(self.ceremony, 'ephemeral') as command, self.assertRaises(AcceptanceError):
                    self.ceremony.prepare()
                command.assert_not_called()

    def test_wrong_binary_version_never_generates_or_stamps(self):
        with patch.object(self.ceremony, 'ephemeral', return_value='sage-gui dev (commit synthetic, built now, max-app-v27)') as command:
            with self.assertRaises(AcceptanceError):
                self.ceremony.prepare()
        self.assertEqual(command.call_count, 1)
        self.assertFalse((self.ceremony.workspace / 'node0').exists())

    def test_existing_home_markers_untouched_before_execution(self):
        home = self.ceremony.workspace / 'node3'
        home.mkdir()
        for name in ('version.txt', 'fork-version.txt'):
            (home / name).write_text('existing-sentinel')
        with patch.object(self.ceremony, 'ephemeral') as command, self.assertRaises(AcceptanceError):
            self.ceremony.prepare()
        command.assert_not_called()
        for name in ('version.txt', 'fork-version.txt'):
            self.assertEqual((home / name).read_text(), 'existing-sentinel')


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.validators = {(bytes([number]) * 32).hex(): 10 for number in range(1, 5)}
        sample = {'block': {'result': {'block': {'header': {'height': '20',
            'chain_id': 'synthetic-public-chain', 'app_hash': 'a' * 64, 'version': {'app': '25'}}}}},
            'validators': {'result': {'block_height': '20', 'count': '4', 'total': '4',
                'validators': [{'pub_key': {'type': 'tendermint/PubKeyEd25519',
                    'value': base64.b64encode(bytes.fromhex(key)).decode()}, 'voting_power': '10'}
                    for key in self.validators]}},
            'abci_info': {'result': {'response': {'app_version': '25'}}}}
        self.samples = [copy.deepcopy(sample) for _ in range(4)]
        self.profile = dict(agent_id='b' * 64, role='member', enrollment_status='active',
            registration_status='active', approval_required=False, can_read=True,
            can_write=True, clearance=0, home_domain='synthetic-public')
        self.submission = dict(memory_id='ce76d45e-e9a8-4b33-87f8-8715961e0b3f',
            tx_hash='c' * 64, committed=True, committed_height=10, status='proposed')
        self.record = dict(memory_id=self.submission['memory_id'], submitting_agent='b' * 64,
            domain_tag='synthetic-public', memory_type='inference', classification=0,
            content='Synthetic claim only', content_hash=hashlib.sha256(b'Synthetic claim only').hexdigest(),
            status='committed')

    def network(self):
        return verify_network(self.samples, chain_id='synthetic-public-chain',
                              expected_validators=set(self.validators.items()), acceptance_barrier_height=12)

    def memory(self):
        return verify_memory([copy.deepcopy(self.record) for _ in range(4)], self.submission,
            content='Synthetic claim only', agent_id='b' * 64, domain='synthetic-public', network=self.network())

    def test_bound_projection_is_not_offline_proof(self):
        self.assertEqual(self.network()['application_state_height'], 19)
        self.assertFalse(self.memory()['offline_proof'])

    def test_submission_height_alone_cannot_cover_later_acceptance(self):
        with self.assertRaises(AcceptanceError):
            verify_network(self.samples, chain_id='synthetic-public-chain',
                expected_validators=set(self.validators.items()), acceptance_barrier_height=20)
        for barrier in (None, True, 20):
            network = dict(self.network(), acceptance_barrier_height=barrier)
            with self.subTest(barrier=barrier), self.assertRaises(AcceptanceError):
                verify_memory([self.record] * 4, self.submission, content='Synthetic claim only',
                              agent_id='b' * 64, domain='synthetic-public', network=network)

    def test_current_abci_version_is_not_historical_header_version(self):
        self.samples[0]['abci_info']['result']['response']['app_version'] = '26'
        evidence = self.network()
        self.assertEqual(evidence['historical_app_version'], 25)
        self.assertEqual(evidence['current_app_versions'], [26, 25, 25, 25])
        self.samples[0]['block']['result']['block']['header']['version']['app'] = '24'
        with self.assertRaises(AcceptanceError):
            self.network()

    def test_height_chain_hash_and_count_must_match(self):
        for field, value in [('height', '21'), ('chain_id', 'other'), ('app_hash', 'd' * 64)]:
            before = copy.deepcopy(self.samples)
            self.samples[3]['block']['result']['block']['header'][field] = value
            with self.subTest(field=field), self.assertRaises(AcceptanceError):
                self.network()
            self.samples = before
        self.samples[3]['validators']['result']['total'] = '5'
        with self.assertRaises(AcceptanceError):
            self.network()

    def test_old_app_version_not_accepted(self):
        self.samples[0]['abci_info']['result']['response']['app_version'] = '23'
        with self.assertRaises(AcceptanceError):
            self.network()

    def test_mixed_app_versions_and_duplicate_validators_rejected(self):
        self.samples[0]['block']['result']['block']['header']['version']['app'] = '26'
        with self.assertRaises(AcceptanceError):
            self.network()
        self.samples[0]['block']['result']['block']['header']['version']['app'] = '25'
        validators = self.samples[0]['validators']['result']['validators']
        validators[1] = copy.deepcopy(validators[0])
        with self.assertRaises(AcceptanceError):
            self.network()

    def test_each_content_binding_and_lifecycle_required(self):
        for key, value in [('content', 'different'), ('classification', True), ('status', 'proposed'),
                           ('submitting_agent', 'd' * 64), ('content_hash', 'f' * 64),
                           ('domain_tag', 'other'), ('memory_type', 'fact')]:
            original = self.record[key]
            self.record[key] = value
            with self.subTest(key=key), self.assertRaises(AcceptanceError):
                self.memory()
            self.record[key] = original

    def test_submission_boolean_and_unobserved_height_rejected(self):
        for key, value in [('committed', 1), ('committed_height', True), ('committed_height', 20)]:
            original = self.submission[key]
            self.submission[key] = value
            with self.subTest(key=key), self.assertRaises(AcceptanceError):
                self.memory()
            self.submission[key] = original

    def test_no_admin_or_pending_enrollment_shortcut(self):
        verify_profile(self.profile, agent_id='b' * 64, domain='synthetic-public')
        for key, value in [('role', 'admin'), ('enrollment_status', 'pending_review'), ('clearance', False),
                           ('can_write', False), ('approval_required', True)]:
            profile = dict(self.profile, **{key: value})
            with self.subTest(key=key), self.assertRaises(AcceptanceError):
                verify_profile(profile, agent_id='b' * 64, domain='synthetic-public')


if __name__ == '__main__':
    unittest.main()
