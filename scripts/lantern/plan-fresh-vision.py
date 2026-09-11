#!/usr/bin/env python3
"""Print fresh A/B vision candidates from trusted installed public configuration.

Usage: sudo python3 scripts/lantern/plan-fresh-vision.py --unit-id unit-a
No output files, artifact execution, hardware probes, service actions or readiness
markers. This plan is not an effective-systemd-state or inference attestation.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


VOICE = 'sage-lantern-voice-loop.service'
LLM = 'sage-lantern-llm.service'
SYSTEMD = '/etc/systemd/system/'
BINDINGS = '/etc/sage-lantern/appliance-bindings.json'
RECEIPT = '/etc/sage-lantern/appliance-installed.json'
METADATA = '/etc/sage-lantern/unit.env'
AUDIO_GUARD = SYSTEMD + VOICE + '.d/50-fresh-audio-validation.conf'
ALSA_PATH = '/etc/sage-lantern/audio-capture-alsa.conf'
AUDIO_INPUTS = {
    AUDIO_GUARD: ('[Service]\nEnvironment=ALSA_CONFIG_PATH=' + ALSA_PATH + '\n'
                  'ExecCondition=+/opt/sage-lantern/venv/bin/python -m sage_lantern.audio_readiness --admit\n'),
    ALSA_PATH: ('pcm.hw {\n @args [ CARD DEV ]\n @args.CARD { type string }\n'
                ' @args.DEV { type integer }\n type hw\n card $CARD\n device $DEV\n}\n'),
}
BASE_HASHES = {
    VOICE: 'ac652a806133ed5b87e9a881034c907f79f4fad3af184f7e33e7b48596223975',
    LLM: 'f02105521106a94dff64eb4f1158c7736a4daf807da36e0e8f3d88309a8f11f4',
}
MESH = ('[Unit]\nConditionPathExists=/run/sage-lantern-mesh-control/voice-start-authorized.json\n'
        '\n[Service]\nEnvironment=SAGE_LANTERN_MESH_BROKER=1\nRestart=no\n')
SLEEP_FENCE = '[Unit]\nConditionPathExists=!/run/sage-lantern-sleep.fenced\n'
SLEEP_DROPINS = {
    SYSTEMD + VOICE + '.d/90-sage-lantern-sleep.conf': (
        SLEEP_FENCE + '[Service]\nExecStart=\n'
        'ExecStart=/usr/local/libexec/sage-lantern-voice-loop --photo-ready'
        ' --drain-request /run/sage-lantern-voice-drain-request.json\n'
        'RuntimeDirectoryPreserve=yes\nTimeoutStopSec=30\n'
        'Environment=SAGE_LANTERN_INFERENCE_GUARD=/var/lib/sage-lantern/inference\n'
        'ReadWritePaths=/var/lib/sage-lantern/inference\n'),
    SYSTEMD + LLM + '.d/90-sage-lantern-sleep.conf': SLEEP_FENCE,
    SYSTEMD + LLM + '.d/90-sleep-baseline.conf': (
        '[Service]\nReadWritePaths=/run/sage-lantern-model\n'
        'ExecStartPost=+/opt/sage-lantern/venv/bin/python '
        '/usr/local/libexec/sage-lantern-idle-suspend --model-started\n'),
}
UNIT_ROOTS = (
    '/etc/systemd/system.control', '/run/systemd/system.control', '/run/systemd/transient',
    '/run/systemd/generator.early', '/etc/systemd/system', '/etc/systemd/system.attached',
    '/run/systemd/system', '/run/systemd/system.attached', '/run/systemd/generator',
    '/usr/local/lib/systemd/system', '/usr/lib/systemd/system', '/lib/systemd/system',
    '/run/systemd/generator.late',
)
DROPIN_DIRS = (VOICE + '.d', LLM + '.d', 'sage-lantern-voice-.service.d',
               'sage-lantern-.service.d', 'sage-.service.d', 'service.d')
MAX_FILE = 65536


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class InstalledFiles:
    def __init__(self, root=Path('/')):
        self.root = root

    def checked(self, name):
        path = self.root / name.lstrip('/')
        parts = (self.root, *reversed(tuple(path.parents)[:len(path.relative_to(self.root).parts) - 1]), path)
        for component in parts:
            info = component.lstat()
            require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
                    and not info.st_mode & 0o022, 'untrusted installed path')
            if component != path:
                require(stat.S_ISDIR(info.st_mode), 'invalid installed parent')
        return path

    def read(self, name):
        path = self.checked(name)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and before.st_uid == 0 and not before.st_mode & 0o022
                    and before.st_size <= MAX_FILE, 'unsafe installed file')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                data = stream.read(MAX_FILE + 1)
            require(len(data) <= MAX_FILE and len(data) == before.st_size
                    and identity(before) == identity(os.fstat(descriptor)) == identity(path.lstat()),
                    'installed file changed or oversized')
            return data
        finally:
            os.close(descriptor)

    def inventory(self):
        observed = {}
        for directory in UNIT_ROOTS:
            if directory == '/lib/systemd/system' and (self.root / 'lib').is_symlink():
                require((self.root / 'lib').lstat().st_uid == 0
                        and (self.root / 'lib').resolve() == self.root / 'usr/lib', 'unreviewed lib alias')
                continue
            try:
                parent = self.checked(directory)
            except FileNotFoundError:
                continue
            require(parent.is_dir(), 'invalid systemd directory')
            entries = list(parent.iterdir())
            require(len(entries) <= 4096, 'systemd inventory bound')
            observed[directory] = identity(parent.lstat())
            for entry in entries:
                if entry.name in (VOICE, LLM):
                    name = directory + '/' + entry.name
                    require(directory == SYSTEMD.rstrip('/'), 'unreviewed service shadow')
                    observed[name] = digest(self.read(name))
                if entry.name in DROPIN_DIRS:
                    name = directory + '/' + entry.name
                    path = self.checked(name)
                    require(path.is_dir(), 'invalid drop-in directory')
                    children = list(path.iterdir())
                    require(len(children) <= 32, 'drop-in inventory bound')
                    observed[name] = identity(path.lstat())
                    for child in children:
                        relative = name + '/' + child.name
                        require(relative in expected_dropin_paths() | SLEEP_DROPINS.keys() | {AUDIO_GUARD},
                                'unknown systemd override')
                        observed[relative] = digest(self.read(relative))
        return observed


def expected_dropin_paths():
    return {SYSTEMD + VOICE + '.d/' + name for name in ('30-unit-audio.conf', '40-mesh-broker.conf')}


def document(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=unique)
    require(type(value) is dict, 'invalid public document')
    return value


def voice_base(alsa):
    return ('[Service]\nExecStart=\nExecStart=/usr/local/libexec/sage-lantern-voice-loop '
            '--config /etc/sage-lantern/lantern.toml --alsa-device ' + alsa +
            ' --drain-request /run/sage-lantern-mesh-control/voice-drain-request.json\n')


def optional_alsa(files):
    try:
        return files.read(ALSA_PATH)
    except FileNotFoundError:
        return None


def plan(unit_id, files=None, *, fresh_audio=False):
    require(unit_id in ('unit-a', 'unit-b'), 'unsupported unit')
    require(type(fresh_audio) is bool, 'invalid audio profile')
    files = files if files is not None else InstalledFiles()
    inventory = files.inventory()
    sleep_paths = SLEEP_DROPINS.keys() & inventory.keys()
    require(not sleep_paths or sleep_paths == SLEEP_DROPINS.keys(), 'incomplete reviewed sleep overrides')
    alsa_input = optional_alsa(files)
    guarded_audio = AUDIO_GUARD in inventory
    require(guarded_audio == (alsa_input is not None), 'incomplete reviewed audio inputs')
    require(not fresh_audio or guarded_audio, 'fresh audio inputs required')
    audio_paths = set(AUDIO_INPUTS) if guarded_audio else set()
    names = {BINDINGS, RECEIPT, METADATA, *expected_dropin_paths(),
             *sleep_paths, *audio_paths, *(SYSTEMD + service for service in BASE_HASHES)}
    inputs = {name: files.read(name) for name in sorted(names)}
    for name in sleep_paths:
        require(inputs[name] == SLEEP_DROPINS[name].encode(), 'sleep override mismatch')
    for name in audio_paths:
        require(inputs[name] == AUDIO_INPUTS[name].encode(), 'audio input mismatch')
    binding, receipt = document(inputs[BINDINGS]), document(inputs[RECEIPT])
    require(binding.get('schema') == 'sage.lantern.unit-plan.v1'
            and receipt.get('schema') == 'sage.lantern.fresh-app-install.v1'
            and binding.get('unit_id') == receipt.get('unit_id') == unit_id
            and receipt.get('installed') is True and receipt.get('lock_required') is True,
            'fresh installed unit mismatch')
    require(inputs[METADATA] == f'UNIT_ID={unit_id}\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n'.encode(),
            'unit metadata mismatch')
    account = binding.get('operator_account')
    require(type(account) is str and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', account)
            and account not in ('root', 'sage-lantern', 'nobody'), 'invalid operator binding')
    require(type(binding.get('audio')) is dict, 'missing audio binding')
    alsa = binding['audio'].get('alsa_device')
    require(type(alsa) is str and re.fullmatch(r'hw:CARD=[A-Za-z0-9_]{1,32},DEV=[0-9]{1,2}', alsa),
            'invalid ALSA binding')
    for service, expected in BASE_HASHES.items():
        require(digest(inputs[SYSTEMD + service]) == expected, 'unreviewed base service')
    base = voice_base(alsa)
    require(inputs[SYSTEMD + VOICE + '.d/30-unit-audio.conf'] == base.encode(), 'unit audio override mismatch')
    require(inputs[SYSTEMD + VOICE + '.d/40-mesh-broker.conf'] == MESH.encode(), 'mesh broker override mismatch')
    voice = base.removesuffix('\n') + ' --photo-ready\n'
    model = '/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf'
    projector = '/opt/sage-lantern/models/mmproj-BF16.gguf'
    binary = '/opt/sage-lantern/llama.cpp/llama-server'
    llm = ('[Service]\nExecStart=\nExecStart=' + binary + ' --model ' + model +
           ' --host 127.0.0.1 --port 8081 --ctx-size 4096 --threads 6 --parallel 1 --n-gpu-layers 0'
           ' --mmproj ' + projector + ' --no-mmproj-offload --image-max-tokens 256\n'
           'MemoryMax=5G\nMemorySwapMax=0\n')
    require(inventory == files.inventory() and optional_alsa(files) == alsa_input
            and all(files.read(name) == data for name, data in inputs.items()),
            'installed configuration changed during planning')
    return dict(schema='sage.lantern.fresh-vision-plan.v1', mode='plan-only', unit_id=unit_id,
                audio_profile='commissioned-guard' if guarded_audio else 'pre-commissioning',
                readiness_verified=False, activation_authorized=False, installed_files_written=False,
                input_sha256={name: digest(data) for name, data in inputs.items()},
                preserve=sorted(expected_dropin_paths() | sleep_paths | audio_paths),
                candidates=[dict(destination=SYSTEMD + service + '.d/' +
                                 ('95-lantern-local-vision.conf' if sleep_paths else '90-lantern-local-vision.conf'),
                                 content=content, sha256=digest(content.encode()), owner='root:root', mode='0644')
                            for service, content in ((VOICE, voice), (LLM, llm))],
                evidence_requirements={
                    'model': dict(path=model, sha256='00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4'),
                    'projector': dict(path=projector, sha256='302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6'),
                    'binary': dict(path=binary, sha256='must be measured and bound to actual inference evidence'),
                    'required': ['Operator-verified per-unit capture hardware and local voice acceptance',
                                 'Matching model/projector/binary hashes and successful photo inference evidence',
                                 'Binary support for every proposed flag; health alone is insufficient',
                                 'Independent effective systemd configuration and loaded-state verification',
                                 'Exclusive deployment coordination and revalidation of all input hashes and overrides',
                                 'Broker-owned voice drain/admission/resume and coordinated model-consumer maintenance',
                                 'Preserve mesh authorization conditions, broker environment and Restart=no',
                                 'Preserve sleep fences, inference guard and root model-baseline hook; later vision ExecStart retains fresh audio and broker arguments',
                                 'Separate guarded deployment, rollback and native acceptance; no grants or markers supplied']})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--unit-id', choices=('unit-a', 'unit-b'), required=True)
    parser.add_argument('--fresh-audio', action='store_true',
                        help='Require the exact durable audio admission guard and ALSA input pair')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(plan(args.unit_id, fresh_audio=args.fresh_audio), sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        print('Fresh vision plan refused: installed configuration is missing, untrusted or incompatible.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
