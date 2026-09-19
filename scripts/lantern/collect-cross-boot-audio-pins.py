#!/usr/bin/env python3
"""Target-local read-only expected-pin collector for one-shot audio recovery.

Verifies installed runtime source bytes against the exact reviewed R7 wheel
before importing runtime modules. SDK sources are observed root-controlled
installed inputs, not independently certified here. Hash-loads the reviewed
recovery helper without pycache. Acquires only existing kernel locks. Signed
workflow GETs use the existing on-device identity after real access checks;
neither keys nor workflow payloads leave the process. Prints expected JSON only.
No capture, state publication, service action, enablement or apply permission.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import types
import zipfile


R7 = '6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440'
RETRIEVAL_V1 = '36a94a18a83889ceb5860d40ace998410d1bc3fcfbb806af9731199f331306a3'
OWNER_LEDGERS_V2 = 'b0cfc4f94333960a579fda7e1c3b41bfcd4c7206c2a0441715af980133e36d17'
RADIO_OWNER_COMBINED = '2175cd19e275597860cdd877a1007156366667021d721766023a569d83bee519'
COMPACT_PROMPT_V1 = 'fd39c10773de0726e333b296a1dfc5dab3058753edfb5925dd2b46abfcd4ddd3'
RADIO_FOUNDATION_SOURCES = '9c2a317ae7c3779c023ef216f7c8ce730b2fea08f2c6a3650b96cfa699140f7b'
RADIO_DIRECTORY_SOURCES = 'd9ba5b90c315f902be77fbd23e515547a8332c533e7afcadfe911c4984b2bbf0'
RADIO_DIRECTORY_A_SOURCES = '1ca41a3400aff258767aff50f8cb26f87108150e69a833ce330d296244763a88'
RADIO_FOUNDATION_A_SOURCES = '0ef48243d2351fea35db7d4714bc3089d9ae032f394f4dd177bf939bb0a10ae8'
# Published 2026-09-18: the runtime wheel rebuilt to match the deployed bytes
# (answer suggestions + the 2026-09-12 messaging receive fix). Supersedes
# cf963bd8..., which described the maintained-audio wheel the units outgrew.
# It deliberately includes the ten AppleDouble `._*` files that the audio
# commissioning receipt binds as attested inputs.
DEPLOYED_MESSAGING_RUNTIME = '290c0e67ff391084dc11e47bda0058aca946286828f56d7a809b811d014f94fb'
# A unit release describes its own runtime. build-release.py replaces this line
# with the digest of the wheel it built from that unit's deployed bytes; the
# repository copy stays None and accepts only the digests published above.
RELEASE_RUNTIME = None


def require(value):
    if not value:
        raise ValueError('cross_boot_pin_collection_refused')


def trusted(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
            for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


def load_helper(path, digest):
    raw = trusted(path)
    require(hashlib.sha256(raw).hexdigest() == digest)
    module = types.ModuleType('reviewed_cross_boot_recovery')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def runtime_sources(helper, wheel, site):
    raw = trusted(wheel)
    require(helper.sha(raw) in (R7, RETRIEVAL_V1, OWNER_LEDGERS_V2, COMPACT_PROMPT_V1, RADIO_OWNER_COMBINED, RADIO_FOUNDATION_SOURCES, RADIO_FOUNDATION_A_SOURCES, RADIO_DIRECTORY_SOURCES, RADIO_DIRECTORY_A_SOURCES, DEPLOYED_MESSAGING_RUNTIME, RELEASE_RUNTIME, '355ade85f6fc2ee307a079261a532631b5799d5ac4c3e21d735c8fa644a81581', 'fd4828e004a91e516bb7fd24fdd603fa098389c908923247c693777c63919edc', 'cf963bd87e9a838ec926f2da80fc4aad3b0946b2ebdc1827a6228734ac0b214a'))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)))
        sources = {name for name in names if name.startswith('sage_lantern/') and name.endswith('.py')}
        require(sources and sources == {str(path.relative_to(site)) for path in (site / 'sage_lantern').rglob('*.py')})
        for name in sources:
            require('..' not in Path(name).parts and helper.sha(trusted(site / name)) == helper.sha(archive.read(name)))
    result = {str(path): helper.sha(trusted(path)) for package in ('sage_lantern', 'sage_sdk')
              for path in (site / package).rglob('*.py')}
    require(any('/sage_sdk/' in name for name in result))
    return result


def model_argv(system, helper):
    unit = ''.join(character if character.isalnum() else f'_{ord(character):02x}' for character in helper.MODEL)
    raw = system._execute(['/usr/bin/busctl', '--system', '--json=short', 'get-property',
        'org.freedesktop.systemd1', '/org/freedesktop/systemd1/unit/' + unit,
        'org.freedesktop.systemd1.Service', 'ExecStart'], system.clock() + 5)
    value = helper.decode(raw)
    require(type(value) is dict and set(value) == {'type', 'data'} and value['type'] == 'a(sasbttttuii)')
    require(type(value['data']) is list and len(value['data']) == 1)
    entry = value['data'][0]
    require(type(entry) is list and len(entry) == 10 and entry[2] is False)
    argv = entry[1]
    require(type(argv) is list and argv and all(type(arg) is str for arg in argv) and argv[0] == entry[0])
    if argv[0] == '/opt/sage-lantern/llama.cpp/llama-server':
        return _llama_cpp_argv(argv)
    # The NPU serving shape (productionised 2026-09-19): the stage-5 RKLLM
    # engine behind rkllm_server.py on the production port, with the CPU
    # llama.cpp retained as the vision fallback. Its flags are held to the same
    # public-only, pinned-path standard as the llama.cpp shape.
    require(argv[0] == '/usr/bin/python3' and len(argv) > 1
            and argv[1] == '/opt/sage-lantern/npu/libexec/rkllm_server.py')
    return _npu_server_argv(argv)


def _llama_cpp_argv(argv):
    index = 1
    seen = set()
    while index < len(argv):
        flag = argv[index]
        require(flag not in seen)
        seen.add(flag)
        if flag in {'--no-mmproj-offload', '--slots'}:
            index += 1
            continue
        require(flag in {'--model', '--mmproj', '--host', '--port', '--ctx-size', '--threads',
                         '--parallel', '--n-gpu-layers', '--image-max-tokens',
                         '--threads-batch', '--cache-ram'} and index + 1 < len(argv))
        value = argv[index + 1]
        if flag in {'--model', '--mmproj'}:
            require(value in {'/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf',
                              '/opt/sage-lantern/models/mmproj-BF16.gguf'})
        elif flag == '--host':
            require(value == '127.0.0.1')
        else:
            require(value.isascii() and value.isdecimal() and len(value) <= 6)
            if flag == '--port':
                require(value == '8081')
        index += 2
    require({'--model', '--host', '--port'} <= seen)
    return argv


def _npu_server_argv(argv):
    pinned = {'--engine': '/opt/sage-lantern/npu/bin/rkllm_engine',
              '--model': '/opt/sage-lantern/npu/models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm',
              '--host': '127.0.0.1', '--port': '8081',
              '--fallback-url': 'http://127.0.0.1:8082'}
    numeric = {'--max-new-tokens': 6}
    index = 2
    seen = set()
    while index < len(argv):
        flag = argv[index]
        require(flag not in seen)
        seen.add(flag)
        require(index + 1 < len(argv))
        value = argv[index + 1]
        if flag in pinned:
            require(value == pinned[flag])
        elif flag in numeric:
            require(value.isascii() and value.isdecimal() and 1 <= len(value) <= numeric[flag])
        elif flag == '--cpus':
            require(value in {'1', '2', '3', '4', '5', '6', '7', '8'})
        elif flag == '--context':
            require(value in {'1024', '2048', '4096'})
        elif flag == '--model-name':
            require(value.isascii() and 1 <= len(value) <= 32
                    and all(character.isalnum() or character in '-_.' for character in value))
        else:
            require(False)
        index += 2
    require({'--engine', '--model', '--host', '--port', '--model-name'} <= seen)
    return argv


def signed_inventory(helper, backend, expected):
    return _signed_scan(helper, backend, expected, inspection=backend.inspect_inventory)


def signed_observation(helper, backend, expected):
    """Inventory hash only; live producers mean this is never containment proof."""
    def observe(rows, store, identity, guard, config):
        from sage_lantern.maintenance_owner_ledgers import envelope
        from sage_lantern.workflow_store import KINDS
        require(type(rows) is list and 0 < len(rows) <= 4096)
        seen = set()
        for row in rows:
            envelope(row, expected['actor'])
            require(row['record_id'] not in seen and row['kind'] in KINDS)
            seen.add(row['record_id'])
        require(guard() is True)
        return dict(sha256=helper.sha(helper.canonical(rows)), admission=False)
    return _signed_scan(helper, backend, expected, inspection=observe)


def _signed_scan(helper, backend, expected, *, inspection):
    from sage_lantern import conversation_records
    from sage_lantern.device_access import access_epoch
    from sage_lantern.mesh_daemon import load_config, load_identity
    from sage_lantern.mesh_receiver import _validate as validate_receiver
    from sage_lantern.message_contacts import RecipientID
    from sage_lantern.message_storage import MessageStorageProbe
    from sage_lantern.workflow_store import WorkflowStore
    from sage_sdk import SageClient
    backend.current()
    access = access_epoch()
    require(access is not None)
    config = load_config()
    require(expected['actor'] == dict(chain_id=config['chain_id'], agent_id=config['agent_id']))
    identity, descriptor = load_identity(config)
    try:
        with SageClient(config['endpoint'], identity, timeout=5, trust_env=False) as client:
            probe = MessageStorageProbe(client, actor=RecipientID(**expected['actor']), endpoint=config['endpoint'])
            store = WorkflowStore(client, storage=probe)
            def guard():
                backend.current()
                require(access_epoch() == access)
                return True
            epoch = probe.snapshot().epoch
            rows = store.scan(limit=50, max_records=4096, access_check=guard)
            summary = inspection(rows, store, identity, guard, config)
            require(store.scan(limit=50, max_records=4096, access_check=guard) == rows
                    and probe.snapshot().epoch == epoch and guard())
            return epoch, summary['sha256']
    finally:
        os.close(descriptor)


def bootstrap_inventory(helper, system):
    from sage_lantern.deployment_bringup import ColdBrokerBootstrap, REQUIRED, OPTIONAL
    inventory = {name: helper.sha(trusted(name)) for name in REQUIRED}
    for service in (helper.BROKER, helper.VOICE):
        value = system._show(service, system.clock() + 5, ('DropInPaths', 'FragmentPath', 'NeedDaemonReload'))
        require(value['FragmentPath'] == str(helper.SYSTEMD / service) and value['NeedDaemonReload'] == 'no')
        paths = value['DropInPaths'].split()
        require(len(paths) == len(set(paths)))
        for name in paths:
            require(name in REQUIRED | OPTIONAL and name.startswith(str(helper.SYSTEMD / service) + '.d/'))
            inventory[name] = helper.sha(trusted(name))
    ColdBrokerBootstrap(inventory)
    return inventory


PROJECTOR = '/opt/sage-lantern/models/mmproj-BF16.gguf'


# The reviewed LLM swap of 2026-09-19: the NPU serving shape replaces the
# llama.cpp shape on the same unit. A release pin may move from the llama.cpp
# artifacts below to the NPU shape exactly once; every other model-file change
# still refuses, and after the swap the NPU paths are compared like any other
# baseline.
LLAMA_CPP_ARTIFACTS = {'/opt/sage-lantern/llama.cpp/llama-server',
                       '/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf',
                       '/opt/sage-lantern/models/mmproj-BF16.gguf'}
LLAMA_CPP_UNIT = '/etc/systemd/system/sage-lantern-llm.service'
LLAMA_CPP_DROPINS = {LLAMA_CPP_UNIT + '.d/' + name for name in
                     ('45-cpu-profile.conf', '46-vision.conf', 'zzzz-fresh-native-update.conf')}


def superseded_by_npu(argv, retained):
    """True when a retained pin holds only reviewed llama.cpp-era artifacts and
    the live unit now runs the reviewed NPU shape."""
    if type(argv) is not list or not argv or argv[0] != '/usr/bin/python3':
        return False
    names = set(retained)
    return bool(names) and names <= (LLAMA_CPP_ARTIFACTS | LLAMA_CPP_DROPINS | {LLAMA_CPP_UNIT})


def model_files(helper, argv, model, retained_model_files=None):
    paths = {model['FragmentPath'], *model['DropInPaths'].split()}
    require(argv.count('--model') == 1 and argv.index('--model') + 1 < len(argv))
    paths.add(argv[argv.index('--model') + 1])
    if argv and argv[0] == '/usr/bin/python3':
        # The NPU shape: the interpreter is a symlink and is not a pinned file.
        # The artifacts that matter are the server script and the engine beside
        # the shared model, and they are hashed instead.
        require(len(argv) > 1 and argv[1].endswith('.py'))
        paths.add(argv[1])
        require(argv.count('--engine') == 1)
        paths.add(argv[argv.index('--engine') + 1])
    else:
        paths.add(argv[0])
        if '--mmproj' in argv:
            require(argv.count('--mmproj') == 1 and argv.index('--mmproj') + 1 < len(argv))
            paths.add(argv[argv.index('--mmproj') + 1])
    if retained_model_files is not None:
        require(type(retained_model_files) is dict
            and all(type(name) is str and type(digest) is str
                and re.fullmatch('[0-9a-f]{64}', digest)
                for name, digest in retained_model_files.items())
            and (set(retained_model_files) in (paths, paths | {PROJECTOR})
                 or superseded_by_npu(argv, retained_model_files)))
        if superseded_by_npu(argv, retained_model_files):
            return {name: helper.file_hash(name) for name in sorted(paths)}
        paths = set(retained_model_files)
    files = {name: helper.file_hash(name) for name in sorted(paths)}
    if retained_model_files is not None:
        require(files == retained_model_files)
    return files


def collect(helper, source_pins, *, retained_model_files=None):
    from sage_lantern import audio_readiness
    from sage_lantern.deployment_bringup import ColdBrokerBootstrap
    from sage_lantern.mesh_daemon import load_config
    from sage_lantern.suspend_system import SuspendSystem
    system = SuspendSystem()
    inventory = bootstrap_inventory(helper, system)
    owner = ColdBrokerBootstrap(inventory)
    owner.inputs()
    for service in (helper.BROKER, helper.VOICE, helper.MODEL):
        owner.inactive(service)
    argv = model_argv(system, helper)
    model = system._show(helper.MODEL, system.clock() + 5, ('DropInPaths', 'FragmentPath', 'NeedDaemonReload'))
    require(model['FragmentPath'] == str(helper.SYSTEMD / helper.MODEL) and model['NeedDaemonReload'] == 'no')
    files = model_files(helper, argv, model, retained_model_files)
    config = load_config()
    expected = dict(schema='sage.lantern.cross-boot-audio.v1',
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), tree=helper.tree(helper.SUPERVISOR),
        inventory=inventory, runtime_sources=source_pins, files=files, model_argv=argv,
        commission_sha256=helper.sha(helper.canonical(audio_readiness.commissioned_receipt())),
        storage_epoch=None, workflow_sha256=None,
        actor=dict(chain_id=config['chain_id'], agent_id=config['agent_id']),
        historical_outcomes='unknown', old_boot_attribution='unverified')
    backend = helper.Recovery(expected)
    try:
        backend.lock()
        expected['storage_epoch'], expected['workflow_sha256'] = signed_inventory(helper, backend, expected)
        backend.reconcile()
        require(model_argv(system, helper) == argv)
        return expected
    finally:
        backend.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--helper-sha256', required=True)
    parser.add_argument('--runtime-wheel', type=Path, required=True)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and Path(sys.prefix) == Path('/opt/sage-lantern/venv'))
    def expired(signum, frame):
        raise TimeoutError('pin_collection_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(300)
    helper = load_helper(args.helper, args.helper_sha256)
    site = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    sources = runtime_sources(helper, args.runtime_wheel, site)
    expected = collect(helper, sources)
    require(runtime_sources(helper, args.runtime_wheel, site) == sources)
    require(hashlib.sha256(trusted(args.helper)).hexdigest() == args.helper_sha256)
    print(helper.canonical(expected).decode())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__, expected_pins_produced=False)), file=sys.stderr)
        raise SystemExit(2) from None
