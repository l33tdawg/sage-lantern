#!/usr/bin/env python3
"""Print fresh A–F vision candidates from trusted installed public configuration.

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
CPU_LLM = 'sage-lantern-llm-cpu.service'
VLM = 'sage-lantern-vlm.service'
SYSTEMD = '/etc/systemd/system/'
BINDINGS = '/etc/sage-lantern/appliance-bindings.json'
RECEIPT = '/etc/sage-lantern/appliance-installed.json'
METADATA = '/etc/sage-lantern/unit.env'
AUDIO_GUARD = SYSTEMD + VOICE + '.d/50-fresh-audio-validation.conf'
QUESTION_GATE = SYSTEMD + VOICE + '.d/60-local-question-lane.conf'
# The reviewed local question lane (D-019): a unit with no radio and no working
# capture path still runs the question service. Silent assignments reset the
# mesh start condition and the audio start condition; capture stays per-session.
# The reviewed bytes live in
# `software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf`;
# this constant is what the planner expects to find installed, and the test
# suite fails if it drifts from that file.
QUESTION_GATE_CONTENT = (
    '[Unit]\n'
    'ConditionPathExists=\n'
    'ConditionPathExists=/var/lib/sage-lantern/LOCAL_VOICE_READY\n'
    'ConditionPathExists=!/run/sage-lantern-sleep.fenced\n'
    '\n'
    '[Service]\n'
    'ExecCondition=\n'
    'Restart=on-failure\n'
)
ALSA_PATH = '/etc/sage-lantern/audio-capture-alsa.conf'
AUDIO_INPUTS = {
    AUDIO_GUARD: ('[Service]\nEnvironment=ALSA_CONFIG_PATH=' + ALSA_PATH + '\n'
                  'ExecCondition=+/opt/sage-lantern/venv/bin/python -m sage_lantern.audio_readiness --admit\n'),
    ALSA_PATH: ('pcm.hw {\n @args [ CARD DEV ]\n @args.CARD { type string }\n'
                ' @args.DEV { type integer }\n type hw\n card $CARD\n device $DEV\n}\n'),
}
# Reviewed local question-lane activations. Optional: a unit that has not been
# activated for a radio-less local profile simply does not carry them, and one
# that does must carry these exact bytes.
OPTIONAL_DROPINS = {
    QUESTION_GATE: QUESTION_GATE_CONTENT,
}
# Pin the serving contract: NPU text on 8081 (sage-lantern-llm.service) proxies
# photo questions to the NPU vision server on 8082 (sage-lantern-vlm.service,
# decision D-016). The retired CPU 4B fallback (sage-lantern-llm-cpu.service) is
# still shipped for bench comparison, so its bytes stay pinned, but it must never
# be enabled together with the vision unit — both draft port 8082. The text unit
# carries no dependency on either provider: the reviewed activation lane owns
# which 8082 server runs, so a disabled retired unit cannot be pulled in anyway.
BASE_HASHES = {
    VOICE: 'ac652a806133ed5b87e9a881034c907f79f4fad3af184f7e33e7b48596223975',
    LLM: '1c5888aedf9c02f6b15eeaf3351fcd7357fe7b5fd93af44c6e4de088c837aa7e',
    CPU_LLM: '875f9456cf6d0f79cd47f1a634811a160df25ddf80a036075d0222bf994614de',
    VLM: 'c472154bb41830202a61911a45ede9e747dd1867336bcc3b2369678cd6311d55',
}

MESH = ('[Unit]\nConditionPathExists=/run/sage-lantern-mesh-control/voice-start-authorized.json\n'
        '\n[Service]\nEnvironment=SAGE_LANTERN_MESH_BROKER=1\nRestart=no\n')
SLEEP_FENCE = '[Unit]\nConditionPathExists=!/run/sage-lantern-sleep.fenced\n'


def baseline_dropin(service):
    """Reviewed start-baseline drop-in for one resident model unit (D-016)."""
    return ('[Service]\nReadWritePaths=/run/sage-lantern-model\n'
            'ExecStartPost=+/opt/sage-lantern/venv/bin/python '
            '/usr/local/libexec/sage-lantern-idle-suspend --model-started ' + service + '\n')


SLEEP_VOICE = SYSTEMD + VOICE + '.d/90-sage-lantern-sleep.conf'
SLEEP_DROPIN_PATHS = (
    SLEEP_VOICE,
    SYSTEMD + LLM + '.d/90-sage-lantern-sleep.conf',
    SYSTEMD + LLM + '.d/90-sleep-baseline.conf',
    SYSTEMD + VLM + '.d/90-sage-lantern-sleep.conf',
    SYSTEMD + VLM + '.d/90-sleep-baseline.conf',
)


def voice_sleep(alsa):
    """The reviewed standard voice sleep drop-in for a unit's own binding.

    The sleep drop-in replaces the voice ExecStart, so it must keep the unit's
    own capture binding (`--config` plus `--alsa-device`): the runtime default
    is a placeholder device (`hw:CARD=Audio,DEV=0`) and dropping the binding
    silently points capture at a device that does not exist on the unit. These
    bytes are asserted against `install-idle-suspend-local.py`; drift is a
    defect.
    """
    return (SLEEP_FENCE + '[Service]\nExecStart=\n'
            'ExecStart=/usr/local/libexec/sage-lantern-voice-loop '
            '--config /etc/sage-lantern/lantern.toml --alsa-device ' + alsa +
            ' --photo-ready --drain-request /run/sage-lantern-voice-drain-request.json\n'
            'RuntimeDirectoryPreserve=yes\nTimeoutStopSec=30\n'
            'Environment=SAGE_LANTERN_INFERENCE_GUARD=/var/lib/sage-lantern/inference\n'
            'ReadWritePaths=/var/lib/sage-lantern/inference\n')


def cf_voice_sleep(alsa):
    """The C–F variant: the same bytes with the non-mesh broker choice explicit.

    `install-cf-idle-suspend-local.py` generates these bytes; drift is a defect.
    """
    return voice_sleep(alsa).replace(
        ' --drain-request /run/sage-lantern-voice-drain-request.json\n',
        ' --drain-request /run/sage-lantern-voice-drain-request.json\n'
        'Environment=SAGE_LANTERN_MESH_BROKER=0\n')


def sleep_dropins(alsa):
    """The reviewed sleep drop-ins for one unit's own capture binding."""
    return {
        SLEEP_VOICE: voice_sleep(alsa),
        SYSTEMD + LLM + '.d/90-sage-lantern-sleep.conf': SLEEP_FENCE,
        SYSTEMD + LLM + '.d/90-sleep-baseline.conf': baseline_dropin(LLM),
        SYSTEMD + VLM + '.d/90-sage-lantern-sleep.conf': SLEEP_FENCE,
        SYSTEMD + VLM + '.d/90-sleep-baseline.conf': baseline_dropin(VLM),
    }
UNIT_ROOTS = (
    '/etc/systemd/system.control', '/run/systemd/system.control', '/run/systemd/transient',
    '/run/systemd/generator.early', '/etc/systemd/system', '/etc/systemd/system.attached',
    '/run/systemd/system', '/run/systemd/system.attached', '/run/systemd/generator',
    '/usr/local/lib/systemd/system', '/usr/lib/systemd/system', '/lib/systemd/system',
    '/run/systemd/generator.late',
)
DROPIN_DIRS = (VOICE + '.d', LLM + '.d', CPU_LLM + '.d', VLM + '.d', 'sage-lantern-voice-.service.d',
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
                if entry.name in BASE_HASHES:
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
                        require(relative in expected_dropin_paths() | set(SLEEP_DROPIN_PATHS)
                                | OPTIONAL_DROPINS.keys() | {AUDIO_GUARD},
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
    require(unit_id in ('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'), 'unsupported unit')
    require(type(fresh_audio) is bool, 'invalid audio profile')
    files = files if files is not None else InstalledFiles()
    inventory = files.inventory()
    sleep_paths = set(SLEEP_DROPIN_PATHS) & inventory.keys()
    require(not sleep_paths or sleep_paths == set(SLEEP_DROPIN_PATHS), 'incomplete reviewed sleep overrides')
    alsa_input = optional_alsa(files)
    guarded_audio = AUDIO_GUARD in inventory
    require(guarded_audio == (alsa_input is not None), 'incomplete reviewed audio inputs')
    require(not fresh_audio or guarded_audio, 'fresh audio inputs required')
    audio_paths = set(AUDIO_INPUTS) if guarded_audio else set()
    question_paths = OPTIONAL_DROPINS.keys() & inventory.keys()
    names = {BINDINGS, RECEIPT, METADATA, *expected_dropin_paths(),
             *sleep_paths, *audio_paths, *question_paths,
             *(SYSTEMD + service for service in BASE_HASHES)}
    inputs = {name: files.read(name) for name in sorted(names)}
    for name in audio_paths:
        require(inputs[name] == AUDIO_INPUTS[name].encode(), 'audio input mismatch')
    for name in question_paths:
        require(inputs[name] == OPTIONAL_DROPINS[name].encode(), 'local question lane override mismatch')
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
    # The reviewed sleep overrides carry the unit's own capture binding, so
    # their expected bytes are computed from the binding this unit just
    # validated; a third variant of the voice drop-in is still refused.
    reviewed = sleep_dropins(alsa)
    for name in sleep_paths:
        allowed = {reviewed[name]}
        if name == SLEEP_VOICE:
            allowed.add(cf_voice_sleep(alsa))
        require(inputs[name] in {variant.encode() for variant in allowed}, 'sleep override mismatch')
    for service, expected in BASE_HASHES.items():
        require(digest(inputs[SYSTEMD + service]) == expected, 'unreviewed base service')
    base = voice_base(alsa)
    require(inputs[SYSTEMD + VOICE + '.d/30-unit-audio.conf'] == base.encode(), 'unit audio override mismatch')
    require(inputs[SYSTEMD + VOICE + '.d/40-mesh-broker.conf'] == MESH.encode(), 'mesh broker override mismatch')
    voice = base.removesuffix('\n') + ' --photo-ready\n'
    # The vision lane is a reviewed unit in its own right (pin above), so unlike
    # the retired CPU ExecStart there is no override to propose; the plan records
    # the enable/keep-disabled intent instead.
    vision_engine = '/opt/sage-lantern/npu/bin/vlm_engine'
    vision_encoder = '/opt/sage-lantern/npu/models/qwen3.5-0.8b_vision_rk3588.rknn'
    vision_llm = '/opt/sage-lantern/npu/models/qwen3.5-0.8b_w8a8_rk3588.rkllm'
    require(inventory == files.inventory() and optional_alsa(files) == alsa_input
            and all(files.read(name) == data for name, data in inputs.items()),
            'installed configuration changed during planning')
    return dict(schema='sage.lantern.fresh-vision-plan.v1', mode='plan-only', unit_id=unit_id,
                audio_profile='commissioned-guard' if guarded_audio else 'pre-commissioning',
                readiness_verified=False, activation_authorized=False, installed_files_written=False,
                input_sha256={name: digest(data) for name, data in inputs.items()},
                preserve=sorted(expected_dropin_paths() | sleep_paths | audio_paths | question_paths),
                voice_sleep_profile=('non-mesh-broker'
                                     if inputs.get(SLEEP_VOICE) == cf_voice_sleep(alsa).encode()
                                     else 'standard'),
                local_question_lane=QUESTION_GATE in question_paths,
                enable=[VLM],
                keep_disabled=[CPU_LLM],
                candidates=[dict(destination=SYSTEMD + service + '.d/' +
                                 ('95-lantern-local-vision.conf' if sleep_paths else '90-lantern-local-vision.conf'),
                                 content=content, sha256=digest(content.encode()), owner='root:root', mode='0644')
                            for service, content in ((VOICE, voice),)],
                serving_profile='npu-text-8081-npu-vision-8082',
                evidence_requirements={
                    'npu_model': dict(path='/opt/sage-lantern/npu/models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm',
                        sha256='d3721fc8104b39aba7fc39c272806015a947074dbba7da00091a1747e73c1c3a'),
                    'vision_encoder': dict(path=vision_encoder,
                        sha256='e96a3f016163e7cf7ae516ed9878ccad7c576c658d71a60333f5cb154680fd81'),
                    'vision_llm': dict(path=vision_llm,
                        sha256='ba85edac97e8f4ed2cdf0d94a3744b5609385b4706c45677f55cdaeb465ce054'),
                    'engine': dict(path=vision_engine,
                        sha256='must be measured from the NPU build receipt and bound to actual inference evidence'),
                    'required': ['Operator-verified per-unit capture hardware and local voice acceptance',
                                 'Runtime-matched NPU vision pair (rkllm-toolkit 1.3.0 against the installed 1.3.0 runtime); a 1.2.3-era conversion must never be substituted',
                                 'Installer-built vision engine bound by hash to the NPU build receipt and to successful photo inference evidence',
                                 'Photo acceptance must traverse NPU 8081 (fallback proxy) into the NPU vision server on 8082; text must remain on the 2B NPU',
                                 'The retired CPU 4B fallback (sage-lantern-llm-cpu.service) must stay disabled and inactive and must never be enabled together with sage-lantern-vlm.service — both draft port 8082, and the text unit must be observed not to pull it in by dependency',
                                 'Historical R7 CPU-only activation helper is not an installer for this serving profile',
                                 'Both resident model units participate in the reviewed sleep contract: sage-lantern-llm.service (8081) and sage-lantern-vlm.service (8082) each publish a root start baseline and must survive a sleep cycle as the same instance, with the drain wait covering both model servers',
                                 'Independent effective systemd configuration and loaded-state verification',
                                 'Exclusive deployment coordination and revalidation of all input hashes and overrides',
                                 'Broker-owned voice drain/admission/resume and coordinated model-consumer maintenance',
                                 'Preserve mesh authorization conditions, broker environment and Restart=no',
                                 'Preserve sleep fences, inference guard and every root model-baseline hook; later vision ExecStart retains fresh audio and broker arguments',
                                 'Record the exact power supply used for the acceptance run (bring-your-own-power, D-015) per board profile',
                                 'Separate guarded deployment, rollback and native acceptance; no grants or markers supplied']})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--unit-id', choices=('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'), required=True)
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
