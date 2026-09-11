#!/usr/bin/env python3
"""Explicit, owned four-native-node PUBLIC ceremony. No pulls or timing shortcuts."""

import argparse
import base64
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid

from public_acceptance_fixture import (AcceptanceError, decimal, require, verify_memory, verify_network,
                                       verify_profile, verify_record_bindings)

LABEL = 'dev.lantern.public-acceptance'
NATIVE = '/app/sage-gui'
SIGNER = '/app/v119-governance-fixture'
DOMAIN = 'public-acceptance'
MAX_OUTPUT = 2 * 1024 * 1024


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_field')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(AcceptanceError('invalid_json')))
    except (ValueError, UnicodeError, RecursionError):
        raise AcceptanceError('invalid_json') from None


def image_id(value):
    require(type(value) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', value) is not None,
            'immutable_local_image_id_required')
    return value


def images_checked(images):
    require(type(images) is dict and set(images) == {'native', 'comet'}, 'two_images_required')
    return {role: image_id(value) for role, value in images.items()}


def native_provenance(version, fork):
    require(type(version) is str and re.fullmatch(r'public-test\.[0-9a-f]{16}', version) is not None,
            'reviewed_native_version_required')
    require(type(fork) is int and fork == 1, 'reviewed_native_fork_required')
    return {'version': version, 'fork': fork}


def diagnostic_ports(row):
    def bounded(value):
        if value is None:
            return None
        require(type(value) is dict and len(value) <= 32, 'invalid_diagnostic_ports')
        result = {}
        for port, bindings in value.items():
            require(type(port) is str and re.fullmatch(r'[0-9]{1,5}/(?:tcp|udp|sctp)', port) is not None,
                    'invalid_diagnostic_ports')
            require(bindings is None or type(bindings) is list and len(bindings) <= 8, 'invalid_diagnostic_ports')
            result[port] = None if bindings is None else []
            for binding in bindings or []:
                require(type(binding) is dict and set(binding) == {'HostIp', 'HostPort'}, 'invalid_diagnostic_ports')
                address, number = binding['HostIp'], binding['HostPort']
                require(type(address) is str and len(address) <= 45 and '%' not in address,
                        'invalid_diagnostic_ports')
                if address:
                    ipaddress.ip_address(address)
                require(type(number) is str and re.fullmatch(r'[0-9]{0,5}', number) is not None,
                        'invalid_diagnostic_ports')
                result[port].append({'HostIp': address, 'HostPort': number})
        return result
    try:
        return {'NetworkSettings.Ports': bounded(row.get('NetworkSettings', {}).get('Ports')),
                'HostConfig.PortBindings': bounded(row.get('HostConfig', {}).get('PortBindings'))}
    except (AcceptanceError, ValueError, TypeError, AttributeError):
        return {'unavailable': 'invalid_or_oversized_port_metadata'}


def compose_config(project, workspace, images):
    images = images_checked(images)
    require(re.fullmatch(r'lantern-public-[0-9a-f]{32}', project) is not None, 'invalid_project')
    require(Path(workspace).is_absolute(), 'absolute_owned_workspace_required')
    services = {}
    for index in range(4):
        services[f'node{index}'] = dict(image=images['native'], pull_policy='never',
            entrypoint=[NATIVE], command=['serve'], restart='no',
            user=f'{os.getuid()}:{os.getgid()}', read_only=True, tmpfs=['/tmp:rw,nosuid,nodev,size=128m'],
            cap_drop=['ALL'], security_opt=['no-new-privileges:true'],
            pids_limit=256, cpus=1, mem_limit='2g',
            labels={LABEL: project}, networks=['isolated'],
            environment={'SAGE_HOME': '/fixture', 'HOME': '/fixture', 'REST_ADDR': ':8080',
                         'SAGE_CMT_RPC_ADDR': 'tcp://0.0.0.0:26657', 'SAGE_EMBEDDING_PROVIDER': 'hash'},
            volumes=[f'{workspace}/node{index}:/fixture'])
    return dict(name=project, services=services, networks={'isolated': {'internal': True}})


class Docker:
    """Explicit local Unix endpoint; bounded output, duration and no inherited proxy/context."""

    def __init__(self):
        self.executable = shutil.which('docker')
        require(self.executable is not None, 'docker_unavailable')
        candidates = [Path('/var/run/docker.sock'), Path.home() / '.docker/run/docker.sock']
        self.socket = next((path for path in candidates if path.exists() and stat.S_ISSOCK(path.stat().st_mode)), None)
        require(self.socket is not None, 'local_docker_unavailable')

    def __call__(self, command, timeout=30):
        try:
            with tempfile.TemporaryDirectory(prefix='lantern-docker-config-') as configuration, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                environment = {'PATH': os.defpath, 'HOME': configuration, 'DOCKER_CONFIG': configuration,
                               'DOCKER_HOST': f'unix://{self.socket}'}
                process = subprocess.Popen([self.executable, '--host', f'unix://{self.socket}', *command],
                    env=environment, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT if command[:1] == ['logs'] else errors)
                deadline = time.monotonic() + timeout
                try:
                    while process.poll() is None:
                        require(time.monotonic() < deadline, 'docker_command_timeout')
                        require(os.fstat(output.fileno()).st_size <= MAX_OUTPUT
                                and os.fstat(errors.fileno()).st_size <= MAX_OUTPUT, 'docker_output_limit')
                        time.sleep(0.05)
                    require(process.returncode == 0, 'docker_command_failed')
                    output.seek(0)
                    data = output.read(MAX_OUTPUT + 1)
                    require(len(data) <= MAX_OUTPUT, 'docker_output_limit')
                    return data.decode('utf-8')
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
        except (OSError, UnicodeError):
            raise AcceptanceError('docker_unavailable') from None


def preflight(images, probe):
    images_checked(images)
    try:
        info = strict_json(probe(['info', '--format', '{{json .}}']))
        require(type(info['NCPU']) is int and info['NCPU'] >= 4, 'insufficient_docker_cpu')
        require(type(info['MemTotal']) is int and info['MemTotal'] >= 12 * 1024 ** 3, 'insufficient_docker_memory')
        require(shutil.disk_usage(tempfile.gettempdir()).free >= 8 * 1024 ** 3, 'insufficient_workspace_disk')
        for role, value in images.items():
            try:
                result = strict_json(probe(['image', 'inspect', value]))
            except AcceptanceError:
                raise AcceptanceError(role + '_image_unavailable') from None
            require(type(result) is list and len(result) == 1 and result[0]['Id'] == value, 'local_image_mismatch')
            require(result[0]['Os'] == 'linux', 'linux_image_required')
        return {'preflight': 'passed', 'acceptance': 'not_run', 'image_execution_checked': False}
    except (KeyError, TypeError):
        raise AcceptanceError('invalid_docker_metadata') from None


def write_private(path, value):
    data = value if type(value) is bytes else json.dumps(value, sort_keys=True, indent=2).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def build_config(images, provenance=None):
    images_checked(images)
    if provenance is not None:
        provenance = native_provenance(provenance.get('version'), provenance.get('fork'))
    project = 'lantern-public-' + uuid.uuid4().hex
    workspace = Path(tempfile.mkdtemp(prefix=project + '-')).resolve()
    try:
        plan = {'schema': 'lantern.public-test-owner.v2', 'project': project,
                'workspace': str(workspace), 'images': images, 'acceptance': 'not_run'}
        if provenance is not None:
            plan['native_provenance'] = provenance
        write_private(workspace / 'owner.json', plan)
        write_private(workspace / 'compose.json', compose_config(project, workspace, images))
    except BaseException:
        shutil.rmtree(workspace)
        raise
    return plan


def load_owned(path):
    workspace = Path(path)
    require(workspace.is_absolute() and workspace.resolve() == workspace, 'unsafe_workspace')
    info = workspace.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_workspace')
    marker = workspace / 'owner.json'
    info = marker.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 4096, 'unsafe_owner_marker')
    descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        require(os.fstat(source.fileno()) == info, 'owner_marker_changed')
        plan = strict_json(source.read(4097))
    require(plan.get('schema') == 'lantern.public-test-owner.v2' and plan.get('workspace') == str(workspace),
            'wrong_workspace_owner')
    compose_config(plan['project'], workspace, plan['images'])
    require(workspace.name.startswith(plan['project'] + '-'), 'wrong_workspace_name')
    return plan


def loopback_json(port, path):
    require(type(port) is int and 1024 <= port <= 65535, 'invalid_owned_port')
    require(type(path) is str and re.fullmatch(
        r'/(?:status|abci_info|block\?height=[1-9][0-9]{0,15}|validators\?height=[1-9][0-9]{0,15}&per_page=100)',
        path) is not None, 'invalid_rpc_path')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    deadline = time.monotonic() + 5
    try:
        connection.request('GET', path, headers={'Connection': 'close'})
        response = connection.getresponse()
        require(response.status == 200, 'rpc_unavailable')
        raw = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'rpc_deadline')
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            part = response.read1(min(65536, MAX_OUTPUT + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
            require(len(raw) <= MAX_OUTPUT, 'rpc_output_limit')
        return strict_json(raw)
    except (OSError, http.client.HTTPException):
        raise AcceptanceError('rpc_unavailable') from None
    finally:
        connection.close()


class Ceremony:
    def __init__(self, plan, docker, *, timeout=43200, rpc=None, clock=time.monotonic, sleep=time.sleep):
        self.plan, self.docker = plan, docker
        self.rpc = self.container_rpc if rpc is None else rpc
        self.workspace = Path(plan['workspace'])
        self.project, self.images = plan['project'], plan['images']
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + timeout
        self.ports = []
        self.startup_ports = {}
        self.network = self.project + '-network'
        self.phase = 'preparation'

    def command(self, args, timeout=90):
        remaining = self.deadline - self.clock()
        require(remaining > 0, 'ceremony_timeout')
        return self.docker(args, timeout=min(timeout, remaining))

    def name(self, index):
        return f'{self.project}-node{index}'

    def ephemeral(self, image, entrypoint, args, mounts):
        return self.command(['run', '--pull', 'never', '--rm', '--name', self.project + '-generator',
            '--label', f'{LABEL}={self.project}', '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--cpus', '1', '--memory', '2g', '--pids-limit', '256',
            '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
            *mounts, '--entrypoint', entrypoint, image, *args])

    def prepare(self):
        from nacl.signing import SigningKey
        pins = self.plan.get('native_provenance', {})
        pins = native_provenance(pins.get('version'), pins.get('fork'))
        require(not any(os.path.lexists(self.workspace / name) for name in
                        ['genesis', 'result.json', *[f'node{index}' for index in range(4)]]),
                'workspace_already_used')
        self.phase = 'verify_native_binary'
        version = self.ephemeral(self.images['native'], NATIVE, ['version'], []).strip()
        require(re.fullmatch(r'sage-gui ' + re.escape(pins['version'])
                            + r' \(commit [^\s,]+, built [^\s,]+, max-app-v[0-9]+\)', version) is not None
                and 'fixture' not in version.lower(), 'production_native_binary_required')
        self.phase = 'generate_owned_genesis'
        require(not (self.workspace / 'genesis').exists(), 'workspace_already_used')
        (self.workspace / 'genesis').mkdir(mode=0o700)
        self.ephemeral(self.images['comet'], 'cometbft',
            ['testnet', '--home', '/generated', '--v', '4', '--o', '/generated', '--hostname-prefix', 'node', '--populate-persistent-peers'],
            ['--mount', f'type=bind,src={self.workspace}/genesis,dst=/generated'])
        for path in (self.workspace / 'genesis').rglob('*'):
            require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'unsafe_generated_file')
            require(path.stat().st_nlink == 1 or path.is_dir(), 'unsafe_generated_file')
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        genesis = strict_json((self.workspace / 'genesis/node0/config/genesis.json').read_bytes())
        require(len(genesis['validators']) == 4 and not genesis.get('app_state'), 'unexpected_genesis')
        genesis['chain_id'] = self.project
        self.validators = {(base64.b64decode(item['pub_key']['value'], validate=True).hex(), int(item['power']))
                           for item in genesis['validators']}
        require(len(self.validators) == 4, 'duplicate_validator')
        node_ids = []
        for index in range(4):
            identifier = self.ephemeral(self.images['comet'], 'cometbft',
                ['show-node-id', '--home', '/generated'],
                ['--mount', f'type=bind,src={self.workspace}/genesis/node{index},dst=/generated,readonly']).strip()
            require(re.fullmatch(r'[0-9a-f]{40}', identifier) is not None, 'invalid_generated_node_id')
            node_ids.append(identifier)
        require(len(set(node_ids)) == 4, 'duplicate_node_id')
        self.node_ids = node_ids
        keys = {role: SigningKey.generate() for role in ('operator', 'heartbeat', 'proposer')}
        self.actors = {role: key.verify_key.encode().hex() for role, key in keys.items()}
        for index in range(4):
            home = self.workspace / f'node{index}'
            home.mkdir(mode=0o700)
            write_private(home / 'version.txt', (pins['version'] + '\n').encode())
            write_private(home / 'fork-version.txt', (str(pins['fork']) + '\n').encode())
            (home / 'data').mkdir(mode=0o700)
            shutil.copytree(self.workspace / f'genesis/node{index}', home / 'data/cometbft')
            generated_genesis = home / 'data/cometbft/config/genesis.json'
            generated_genesis.unlink()
            write_private(generated_genesis, genesis)
            (home / 'identities').mkdir(mode=0o700)
            write_private(home / 'agent.key', bytes(keys['operator']))
            for role in ('heartbeat', 'proposer'):
                write_private(home / 'identities' / (role + '.key'), bytes(keys[role]))
            write_private(home / 'config.yaml', dict(data_dir='/fixture/data', agent_key_file='/fixture/agent.key',
                rest_addr=':8080', embedding={'provider': 'hash', 'dimension': 768},
                federation={'enabled': False, 'p2p_enabled': False, 'p2p_relay_addrs': []},
                voter={'enabled': True, 'required': True},
                quorum={'enabled': True, 'p2p_addr': 'tcp://0.0.0.0:26656',
                        'peers': [f'{node_ids[peer]}@node{peer}:26656' for peer in range(4) if peer != index]}))
        self.phase = 'verify_signed_client'
        identifier = self.ephemeral(self.images['native'], SIGNER,
            ['--key', '/fixture/agent.key', 'identity'],
            ['--mount', f'type=bind,src={self.workspace}/node0,dst=/fixture,readonly']).strip()
        require(identifier == self.actors['operator'], 'signed_client_identity_mismatch')
        write_private(self.workspace / 'genesis-evidence.json', dict(chain_id=self.project,
            node_ids=self.node_ids, validators=sorted(self.validators), native_version=version,
            genesis_sha256=hashlib.sha256(json.dumps(genesis, sort_keys=True).encode()).hexdigest()))
        self.phase = 'prepared'

    def retain_diagnostics(self):
        retained = 0
        try:
            ids = self.docker(['container', 'ls', '-aq', '--filter', f'label={LABEL}={self.project}'], timeout=5).split()
            require(len(ids) <= 5, 'diagnostic_container_limit')
            for identifier in ids:
                require(re.fullmatch(r'[0-9a-f]{12,64}', identifier) is not None, 'invalid_container_id')
                row = strict_json(self.docker(['inspect', identifier], timeout=5))[0]
                name = row['Name'].lstrip('/')
                require(row['Config']['Labels'].get(LABEL) == self.project
                        and name in [self.name(index) for index in range(4)]
                        and row['Image'] == self.images['native'], 'diagnostic_owner_mismatch')
                state = row['State']
                metadata = {key: state.get(key) for key in ('Running', 'ExitCode', 'OOMKilled', 'Dead')}
                metadata['ports_at_start_check'] = self.startup_ports.get(name)
                metadata['ports_before_cleanup'] = diagnostic_ports(row)
                write_private(self.workspace / (name + '.state.json'), metadata)
                logs = self.docker(['logs', '--tail', '200', identifier], timeout=5).encode('utf-8')
                write_private(self.workspace / (name + '.startup.log'), logs[-262144:])
                retained += 1
        except Exception:
            return {'containers': retained, 'complete': False}
        return {'containers': retained, 'complete': True}

    def start(self):
        self.command(['network', 'create', '--internal', '--label', f'{LABEL}={self.project}', self.network])
        for index in range(4):
            self.command(['run', '-d', '--pull', 'never', '--name', self.name(index),
                '--label', f'{LABEL}={self.project}', '--network', self.network, '--network-alias', f'node{index}',
                '--user', f'{os.getuid()}:{os.getgid()}', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
                '--pids-limit', '256', '--cpus', '1', '--memory', '2g', '--log-driver', 'local',
                '--log-opt', 'max-size=10m', '--log-opt', 'max-file=2',
                '--mount', f'type=bind,src={self.workspace}/node{index},dst=/fixture',
                '-e', 'HOME=/fixture', '-e', 'SAGE_HOME=/fixture', '-e', 'REST_ADDR=:8080',
                '-e', 'SAGE_CMT_RPC_ADDR=tcp://0.0.0.0:26657', '-e', 'SAGE_EMBEDDING_PROVIDER=hash',
                '--entrypoint', NATIVE, self.images['native'], 'serve'])
            inspected = strict_json(self.command(['inspect', self.name(index)]))[0]
            require(inspected['Config']['Labels'][LABEL] == self.project
                    and inspected['Image'] == self.images['native']
                    and inspected['State']['Running'] is True, 'container_binding_mismatch')
            self.startup_ports[self.name(index)] = diagnostic_ports(inspected)
            published = {port: binding for port, binding in inspected['NetworkSettings']['Ports'].items() if binding}
            require(not published and not inspected['HostConfig']['PortBindings'], 'unexpected_exposed_port')
            self.ports.append(index)
        require(len(set(self.ports)) == 4, 'duplicate_rpc_endpoint')
        self.phase = 'started'

    def container_rpc(self, index, path):
        require(type(index) is int and 0 <= index < 4, 'invalid_owned_node')
        require(type(path) is str and re.fullmatch(
            r'/(?:status|abci_info|block\?height=[1-9][0-9]{0,15}|validators\?height=[1-9][0-9]{0,15}&per_page=100)',
            path) is not None, 'invalid_rpc_path')
        return strict_json(self.command(['exec', self.name(index), SIGNER, '--path', path, 'rpc'], timeout=10))

    def signed(self, index, role, method, path, body=None):
        require(role in ('operator', 'heartbeat', 'proposer'), 'invalid_synthetic_signer')
        key = '/fixture/agent.key' if role == 'operator' else f'/fixture/identities/{role}.key'
        args = ['exec', self.name(index), SIGNER, '--key', key, '--local', '--method', method, '--path', path]
        if body is not None:
            args += ['--body', json.dumps(body, separators=(',', ':'), ensure_ascii=False)]
        return strict_json(self.command([*args, 'request']))

    def register(self, role, member=True):
        result = self.signed(0, role, 'POST', '/v1/agent/register',
            dict(name='public-test-' + role, role='member' if member else 'admin', provider='isolated-public-test'))
        require(result.get('agent_id') == self.actors[role], 'registration_identity_mismatch')
        return result

    def wait(self, check, seconds=1800, heartbeat=False):
        end = min(self.deadline, self.clock() + seconds)
        while self.clock() < end:
            try:
                result = check()
                if result:
                    return result
            except (AcceptanceError, KeyError, TypeError):
                pass
            if heartbeat:
                self.register('heartbeat')
            self.sleep(3)
        raise AcceptanceError('phase_timeout')

    def versions(self):
        return [int(self.rpc(port, '/abci_info')['result']['response']['app_version']) for port in self.ports]

    def run_protocol(self):
        def identities_ready():
            observed = [self.rpc(port, '/status')['result']['node_info'] for port in self.ports]
            require([item['id'] for item in observed] == self.node_ids
                    and all(item['network'] == self.project for item in observed), 'wrong_live_network')
            return True
        self.wait(identities_ready, seconds=180)
        require(max(self.versions()) <= 6, 'unexpected_initial_version')
        operator = self.register('operator', member=False)
        require(operator.get('role') == 'admin', 'legacy_operator_registration_failed')
        self.register('heartbeat')
        self.phase = 'legacy_watchdog'
        print(json.dumps({'phase': self.phase, 'acceptance': 'running'}), flush=True)
        self.wait(lambda: self.versions() == [6] * 4, heartbeat=True)
        for target in range(7, 26):
            self.phase = f'upgrade_{target}'
            print(json.dumps({'phase': self.phase, 'acceptance': 'running'}), flush=True)
            require(self.versions() == [target - 1] * 4, 'upgrade_sequence_changed')
            self.command(['exec', self.name(0), NATIVE, 'upgrade', 'propose', '--target', str(target),
                          '--yes', '--rpc', 'http://127.0.0.1:26657', '--agent-key', '/fixture/agent.key'], timeout=120)
            self.wait(lambda: self.versions() == [target] * 4, heartbeat=True)
        self.phase = 'ordinary_enrollment'
        self.register('proposer')
        profile = self.signed(0, 'proposer', 'GET', '/v1/agent/me')
        require(profile.get('agent_id') == self.actors['proposer']
                and profile.get('approval_required') is True, 'pending_enrollment_not_observed')
        self.signed(0, 'operator', 'PUT', f"/v1/dashboard/network/access/agents/{self.actors['proposer']}/policy",
            dict(role='member', profile='standard', home_domain=DOMAIN, clearance=0, capabilities=0))
        def profile_ready():
            for index in range(4):
                verify_profile(self.signed(index, 'proposer', 'GET', '/v1/agent/me'),
                               agent_id=self.actors['proposer'], domain=DOMAIN)
            return True
        self.wait(profile_ready, seconds=120, heartbeat=True)
        self.phase = 'proposal_unknown_until_receipt'
        content = json.dumps({'schema': 'sage.lantern.public-qa.v1', 'envelope_id': str(uuid.uuid4()),
            'chain_id': self.project, 'domain': DOMAIN, 'author': self.actors['proposer'], 'classification': 0,
            'source_class': 'generated-unverified', 'question': 'Synthetic isolated acceptance test?',
            'answer': 'This is test data, not a factual knowledge endorsement.'}, sort_keys=True, separators=(',', ':'))
        write_private(self.workspace / 'intent.json', dict(content=content, author=self.actors['proposer'],
            chain_id=self.project, domain=DOMAIN, content_hash=hashlib.sha256(content.encode()).hexdigest()))
        submission = self.signed(0, 'proposer', 'POST', '/v1/memory/submit',
            dict(content=content, memory_type='inference', domain_tag=DOMAIN, confidence_score=0.5, classification=0))
        write_private(self.workspace / 'submission.json', submission)
        identifier = submission.get('memory_id')
        require(type(identifier) is str and str(uuid.UUID(identifier)) == identifier, 'invalid_memory_id')
        self.phase = 'canonical_acceptance'
        def accepted():
            records = [self.signed(index, 'proposer', 'GET', f'/v1/memory/{identifier}') for index in range(4)]
            verify_record_bindings(records, submission, content=content,
                                   agent_id=self.actors['proposer'], domain=DOMAIN)
            return records
        records = self.wait(accepted, seconds=300, heartbeat=True)
        barrier = max(self.observed_heights())
        write_private(self.workspace / 'acceptance-barrier.json', dict(post_acceptance_observed_height=barrier))
        self.phase = 'post_acceptance_barrier'
        network = self.wait(lambda: self.observe_network(barrier), seconds=180, heartbeat=True)
        evidence = dict(verify_memory(records, submission, content=content,
            agent_id=self.actors['proposer'], domain=DOMAIN, network=network), network=network)
        self.phase = 'accepted'
        return dict(acceptance='passed', evidence=evidence, offline_proof=False)

    def observed_heights(self):
        statuses = [self.rpc(port, '/status')['result'] for port in self.ports]
        require([item['node_info']['id'] for item in statuses] == self.node_ids
                and all(item['node_info']['network'] == self.project for item in statuses), 'live_identity_changed')
        return [decimal(item['sync_info']['latest_block_height']) for item in statuses]

    def observe_network(self, acceptance_barrier_height):
        require(type(acceptance_barrier_height) is int and acceptance_barrier_height > 0,
                'invalid_acceptance_barrier')
        height = acceptance_barrier_height + 1
        require(min(self.observed_heights()) >= height, 'acceptance_barrier_not_reached')
        observations = [dict(block=self.rpc(port, f'/block?height={height}'),
            validators=self.rpc(port, f'/validators?height={height}&per_page=100'),
            abci_info=self.rpc(port, '/abci_info')) for port in self.ports]
        return verify_network(observations, chain_id=self.project, expected_validators=self.validators,
                              acceptance_barrier_height=acceptance_barrier_height)

    def cleanup(self):
        """Known names and exact owner labels only; unknown Docker outcome retains all files."""
        clean = True
        try:
            ids = self.docker(['container', 'ls', '-aq', '--filter', f'label={LABEL}={self.project}']).split()
            for identifier in ids:
                require(re.fullmatch(r'[0-9a-f]{12,64}', identifier) is not None, 'invalid_container_id')
                row = strict_json(self.docker(['inspect', identifier]))[0]
                require(row['Config']['Labels'].get(LABEL) == self.project
                        and row['Name'].lstrip('/') in [self.name(index) for index in range(4)] + [self.project + '-generator'],
                        'cleanup_owner_mismatch')
                self.docker(['rm', '-f', identifier], timeout=60)
            network_ids = self.docker(['network', 'ls', '-q', '--filter', f'label={LABEL}={self.project}']).split()
            for identifier in network_ids:
                require(re.fullmatch(r'[0-9a-f]{12,64}', identifier) is not None, 'invalid_network_id')
                row = strict_json(self.docker(['network', 'inspect', identifier]))[0]
                require(row['Labels'].get(LABEL) == self.project and row['Name'] == self.network, 'cleanup_owner_mismatch')
                self.docker(['network', 'rm', identifier])
            require(not self.docker(['container', 'ls', '-aq', '--filter', f'label={LABEL}={self.project}']).strip()
                    and not self.docker(['network', 'ls', '-q', '--filter', f'label={LABEL}={self.project}']).strip(),
                    'cleanup_incomplete')
        except Exception:
            clean = False
        if clean:
            for name in ['genesis', *[f'node{index}' for index in range(4)]]:
                path = self.workspace / name
                if path.exists():
                    require(not path.is_symlink(), 'unsafe_cleanup_path')
                    shutil.rmtree(path)
        return clean


def execute(plan, docker, timeout):
    ceremony = Ceremony(plan, docker, timeout=timeout)
    result = {'acceptance': 'failed'}
    previous = {}
    def interrupted(signum, frame):
        raise AcceptanceError('interrupted')
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, interrupted)
    try:
        ceremony.prepare()
        ceremony.start()
        result = ceremony.run_protocol()
    except Exception as error:
        result = {'acceptance': 'failed', 'phase': ceremony.phase,
                  'reason': str(error) if isinstance(error, AcceptanceError) else 'ceremony_failed'}
    finally:
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        if result.get('acceptance') != 'passed':
            try:
                result['diagnostics'] = ceremony.retain_diagnostics()
            except Exception:
                result['diagnostics'] = {'containers': 0, 'complete': False}
        try:
            result['cleanup_confirmed'] = ceremony.cleanup()
        except Exception:
            result['cleanup_confirmed'] = False
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if not result['cleanup_confirmed']:
        result['acceptance'] = 'failed'
        result['reason'] = 'cleanup_unconfirmed'
    write_private(Path(plan['workspace']) / 'result.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['build-config', 'dry-run', 'preflight', 'run', 'cleanup'], default='dry-run')
    parser.add_argument('--native-image')
    parser.add_argument('--comet-image')
    parser.add_argument('--expected-native-version')
    parser.add_argument('--expected-native-fork', type=int)
    parser.add_argument('--workspace')
    parser.add_argument('--reviewed-run', action='store_true')
    parser.add_argument('--timeout-seconds', type=int, default=43200)
    args = parser.parse_args(argv)
    try:
        require(300 <= args.timeout_seconds <= 86400, 'invalid_timeout')
        if args.phase == 'cleanup':
            require(args.reviewed_run and args.workspace is not None, 'parent_review_required')
            plan = load_owned(args.workspace)
            clean = Ceremony(plan, Docker()).cleanup()
            print(json.dumps({'cleanup_confirmed': clean, 'acceptance': 'not_run'}))
            return 0 if clean else 2
        require(args.workspace is None, 'run_requires_fresh_workspace')
        images = images_checked({'native': args.native_image, 'comet': args.comet_image})
        provenance = None
        if args.phase == 'run' or args.expected_native_version is not None or args.expected_native_fork is not None:
            provenance = native_provenance(args.expected_native_version, args.expected_native_fork)
        if args.phase == 'dry-run':
            result = {'acceptance': 'not_run', 'starts_services': False, 'generates_keys': False,
                      'stages': ['owned genesis', 'four native nodes', 'legacy watchdog to v6',
                                 'production upgrades v7-v25', 'native ordinary approval',
                                 'PUBLIC proposal and four-node canonical observation', 'owned teardown']}
        elif args.phase == 'build-config':
            result = build_config(images, provenance)
        else:
            require(args.phase != 'run' or args.reviewed_run, 'parent_review_required')
            docker = Docker()
            result = preflight(images, docker)
            if args.phase == 'run':
                plan = build_config(images, provenance)
                print(json.dumps({'workspace': plan['workspace'], 'project': plan['project'], 'acceptance': 'running'}), flush=True)
                result = execute(plan, docker, args.timeout_seconds)
        print(json.dumps(result, sort_keys=True))
        return 2 if result.get('acceptance') == 'failed' else 0
    except (AcceptanceError, OSError, KeyError, TypeError, ValueError) as error:
        print(json.dumps({'acceptance': 'failed', 'reason': str(error) if isinstance(error, AcceptanceError) else 'invalid_configuration'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
