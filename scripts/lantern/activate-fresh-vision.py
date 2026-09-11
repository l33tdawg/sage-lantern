#!/usr/bin/env python3
"""R7-only guarded vision commissioning; read-only CLI until independent review.

Uses the real maintenance backend, but does not install any runtime wheel.
Model restart and two-second local capture need explicit operator approval.
Only generated color images reach the paused model's loopback endpoint.
Original expected inputs, unknown receipts and failure journals are retained.
No private photo, direct voice start, native restart or automatic rollback.
"""

import argparse
import base64
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import time
import types
import zlib


R7 = '6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440'
STATE = Path('/var/lib/sage-lantern-vision-activation')
MODEL = 'sage-lantern-llm.service'
VOICE = 'sage-lantern-voice-loop.service'
PROJECTOR = '/opt/sage-lantern/models/mmproj-BF16.gguf'
MODEL_FILE = '/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf'
PROJECTOR_SHA = '302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6'
MODEL_SHA = '00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4'
PROBE_PROMPT = 'Name the dominant color of this image. Answer with one color word only.'
CAMERA_HELPER = '/usr/local/libexec/sage-lantern-camera-server'
CAMERA_DEFAULT_SHA = '180a981c9f3eca4a02711e7c749477117de4511689ad26a59bedacf4f4ca7474'
CAMERA_DEFAULT_PATH = '/dev/v4l/by-id/usb-046d_0825_5E4BEE10-video-index0'


def require(value):
    if not value:
        raise ValueError('fresh_vision_activation_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def camera_binding(arguments, selected, helper_raw=None):
    values = []
    for index, argument in enumerate(arguments):
        if argument == b'--device':
            require(index + 1 < len(arguments))
            values.append(arguments[index + 1])
        elif argument.startswith(b'--device='):
            values.append(argument.split(b'=', 1)[1])
    if values:
        require(values == [selected.encode()])
        return
    require(len(arguments) == 2 and arguments[1] == CAMERA_HELPER.encode()
            and helper_raw is not None and sha(helper_raw) == CAMERA_DEFAULT_SHA
            and selected == CAMERA_DEFAULT_PATH)


def trusted(path, digest):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts and re.fullmatch('[0-9a-f]{64}', digest))
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and sha(raw) == digest and all(
            getattr(before, field) == getattr(after, field)
            for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


def color_png(color):
    require(color in ('red', 'blue'))
    pixel = b'\xff\x00\x00' if color == 'red' else b'\x00\x00\xff'
    def chunk(kind, raw):
        return struct.pack('!I', len(raw)) + kind + raw + struct.pack('!I', zlib.crc32(kind + raw))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 64, 64, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + pixel * 64) * 64)) + chunk(b'IEND', b''))


def candidate_argv(original):
    require(type(original) is list and original
            and original[0] == '/opt/sage-lantern/llama.cpp/llama-server'
            and all(type(arg) is str and re.fullmatch('[A-Za-z0-9_./:=+,-]+', arg) for arg in original)
            and '--model' in original and original[original.index('--model') + 1] == MODEL_FILE
            and '--host' in original and original[original.index('--host') + 1] == '127.0.0.1'
            and '--port' in original and original[original.index('--port') + 1] == '8081'
            and not any(arg.startswith(('--mmproj', '--image-max-tokens', '--no-mmproj-offload')) for arg in original))
    return original + ['--mmproj', PROJECTOR, '--no-mmproj-offload', '--image-max-tokens', '256']


def definition(argv):
    require(all(re.fullmatch('[A-Za-z0-9_./:=+,-]+', arg) for arg in argv))
    return ('[Service]\nExecStart=\nExecStart=' + ' '.join(argv) + '\n').encode()


def photo_argv(alsa):
    require(type(alsa) is str and re.fullmatch(r'hw:CARD=[A-Za-z0-9_]{1,32},DEV=[0-9]{1,2}', alsa))
    return ['/usr/local/libexec/sage-lantern-voice-loop', '--config', '/etc/sage-lantern/lantern.toml',
        '--alsa-device', alsa, '--drain-request', '/run/sage-lantern-mesh-control/voice-drain-request.json', '--photo-ready']


def verify_response(value, color):
    require(type(value) is dict and type(value.get('choices')) is list and len(value['choices']) == 1)
    choice = value['choices'][0]
    require(choice.get('finish_reason') == 'stop' and type(choice.get('message')) is dict)
    answer = choice['message'].get('content')
    require(type(answer) is str and len(answer) <= 128 and answer.strip().lower().rstrip('.') == color)
    usage = value.get('usage', {})
    return {name: usage.get(name) if type(usage.get(name)) is int and usage[name] >= 0 else None
            for name in ('prompt_tokens', 'completion_tokens')}


def probe(backend):
    from sage_lantern.inference_guard import begin
    pending = STATE / 'synthetic-pending'
    pending.mkdir(mode=0o700)
    backend.helper.sync(STATE)
    evidence = []
    for color in ('red', 'blue'):
        backend.current_after_auth()
        require(backend.helper.Recovery.model_ready(backend) == backend.model_pin)
        marker = begin(str(pending), require_empty=True)
        image = color_png(color)
        body = backend.helper.canonical(dict(model='local', stream=False, temperature=0, max_tokens=32,
            chat_template_kwargs=dict(enable_thinking=False),
            messages=[dict(role='user', content=[dict(type='text', text=PROBE_PROMPT),
                dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(image).decode()))])]))
        backend.record('synthetic_probe_intent', dict(fixture_sha256=sha(image), pending=marker.request_id))
        connection = http.client.HTTPConnection('127.0.0.1', 8081, timeout=90)
        started = time.monotonic()
        try:
            connection.request('POST', '/v1/chat/completions', body, {'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(65537)
            require(response.status == 200 and len(raw) <= 65536)
            usage = verify_response(backend.helper.decode(raw), color)
        finally:
            connection.close()
        backend.current_after_auth()
        require(backend.helper.Recovery.model_ready(backend) == backend.model_pin)
        require(marker.complete() is True)
        row = dict(fixture_sha256=sha(image), matched=True, elapsed_ms=round((time.monotonic()-started)*1000), **usage)
        backend.record('synthetic_probe_done', row)
        evidence.append(row)
    return evidence


def backend_class(base, defaults=None):
    class Vision(base.Maintenance):
        replacement_phase = 'vision_configure'

        def __init__(self, envelope):
            require(defaults is not None and type(envelope) is dict and set(envelope) == {
                    'schema', 'backend', 'defaults', 'maintenance', 'camera_path'}
                    and envelope['schema'] == 'sage.lantern.fresh-vision-activation.v1')
            require(type(envelope['camera_path']) is str and re.fullmatch(
                '/dev/v4l/by-id/[A-Za-z0-9_.:-]+-video-index[0-9]+', envelope['camera_path']))
            supplied = envelope['maintenance']
            require(supplied['installed_wheel'] == supplied['candidate_wheel']
                    and supplied['installed_wheel']['sha256'] == R7
                    and supplied['files'].get(PROJECTOR) == PROJECTOR_SHA
                    and supplied['files'].get(MODEL_FILE) == MODEL_SHA)
            self.original_envelope = copy.deepcopy(envelope)
            self.defaults = defaults
            self.default_raw = defaults.canonical(defaults.policy(envelope['camera_path']))
            self.default_existed = os.path.lexists(defaults.PATH)
            if self.default_existed:
                require(supplied['files'].get(str(defaults.PATH)) == sha(self.default_raw))
            self.camera_pin = None
            self.model_touched = False
            self.vision_probe = None
            self.photo_process_argv = None
            self.model_candidate = candidate_argv(supplied['model_argv'])
            self.definition_paths = {service: Path('/etc/systemd/system/' + service +
                '.d/95-lantern-local-vision.conf') for service in (MODEL, VOICE)}
            require(all(not os.path.lexists(path) for path in self.definition_paths.values()))
            super().__init__(copy.deepcopy(supplied), candidate_sha256=R7)
            require(stat.S_ISCHR(Path(envelope['camera_path']).stat().st_mode))
            self.camera_pin = self.camera_identity()
            self.capture_before = copy.deepcopy(self.expected['capture_snapshot'])
            self.voice_candidate = photo_argv(self.capture_before['audio']['alsa_device'])
            self.verify_camera()
            self.inference_empty()
            help_result = subprocess.run([self.model_candidate[0], '--help'],
                env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=15, check=True)
            require(len(help_result.stdout) <= 262144 and all(flag in help_result.stdout
                for flag in (b'--mmproj', b'--no-mmproj-offload', b'--image-max-tokens')))
            self.current()

        def ensure_default(self):
            self.current_after_auth()
            if self.default_existed:
                require(self.helper.public(self.defaults.PATH) == self.default_raw)
                return
            self.record('vision_default_intent', dict(sha256=sha(self.default_raw)))
            require(self.defaults.install(self.original_envelope['camera_path']) == self.default_raw)
            self.default_existed = True
            self.expected['files'][str(self.defaults.PATH)] = sha(self.default_raw)
            self.file_metadata[str(self.defaults.PATH)] = self.helper.metadata(self.defaults.PATH.lstat())
            self.current_after_auth()
            self.record('vision_default_done', dict(sha256=sha(self.default_raw)))

        def camera_identity(self):
            path = Path(self.original_envelope['camera_path'])
            require(re.fullmatch('/dev/video[0-9]+', str(path.resolve(strict=True))))
            link, device = path.lstat(), path.stat()
            require(link.st_uid == 0 and stat.S_ISLNK(link.st_mode) and stat.S_ISCHR(device.st_mode))
            return (link.st_dev, link.st_ino, link.st_mtime_ns, os.readlink(path),
                    device.st_dev, device.st_ino, device.st_rdev)

        def current(self):
            super().current()
            if self.camera_pin is not None:
                require(self.camera_identity() == self.camera_pin)

        def inference_empty(self):
            from sage_lantern.inference_guard import pending_count
            require(pending_count('/var/lib/sage-lantern/inference',
                owner_uid=self.expected['services'][VOICE]['process']['uid']) == 0)

        def verify_camera(self):
            camera = 'sage-lantern-camera.service'
            value = self.show(camera)
            require(self.process(value) == self.expected['services'][camera]['process'])
            args = Path('/proc', value['MainPID'], 'cmdline').read_bytes().split(b'\0')[:-1]
            raw = None
            if len(args) == 2 and args[1] == CAMERA_HELPER.encode():
                require(self.expected['files'].get(CAMERA_HELPER) == CAMERA_DEFAULT_SHA)
                raw = trusted(CAMERA_HELPER, CAMERA_DEFAULT_SHA)
            camera_binding(args, self.original_envelope['camera_path'], raw)

        def record(self, phase, details=None):
            if self.sequence == 0:
                self.helper.write(STATE / 'vision-expected.json', self.helper.canonical(self.original_envelope))
            self.sequence += 1
            self.helper.write(STATE / f'{self.sequence:02d}-{phase}.json', self.helper.canonical(dict(
                phase=phase, expected_sha256=sha(self.helper.canonical(self.original_envelope)),
                effective_inputs_sha256=sha(self.helper.canonical(self.expected)),
                historical_outcomes='unknown', details=details)))

        def add_definition(self, service, argv):
            self.current_after_auth()
            path = self.definition_paths[service]
            raw = definition(argv)
            require(service in self.stopped and not os.path.lexists(path))
            self.helper.parents(path)
            self.record('definition_' + service + '_intent', dict(path=str(path), sha256=sha(raw)))
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self.helper.sync(path.parent)
            require(self.helper.public(path) == raw)
            self.file_metadata[str(path)] = self.helper.metadata(path.lstat())
            self.expected['files'][str(path)] = sha(raw)
            original = self.expected['services'][service]['unit']
            original['DropInPaths'] = ' '.join(sorted([*original['DropInPaths'].split(), str(path)]))
            self.system._command(['daemon-reload'], self.system.clock() + 15)
            self.current_after_auth()
            self.record('definition_' + service + '_done', dict(path=str(path), sha256=sha(raw)))

        def install_pinned_runtime(self):
            self.verify_all_importers_empty()
            self.runtime_tool.verify_installed(self.old_wheel)
            require(self.new_wheel == self.old_wheel)
            self.inference_empty()
            self.ensure_default()
            self.installed = True
            self.inhibit(MODEL)
            self.model_touched = True
            self.record('model_stop_intent')
            self.system._command(['stop', MODEL], self.system.clock() + 60)
            self.empty(MODEL, self.show(MODEL))
            self.stopped.add(MODEL)
            self.current_after_auth()
            self.record('model_stop_done')
            self.add_definition(MODEL, self.model_candidate)
            self.expected['model_argv'] = self.model_candidate
            self.expected['model_files'] = sorted(set(self.expected['model_files']) |
                {str(self.definition_paths[MODEL]), PROJECTOR, MODEL_FILE})
            self.audio_tail.release(self, MODEL)
            self.start_model()
            self.vision_probe = probe(self)
            self.add_definition(VOICE, self.voice_candidate)
            self.expected['audio_inventory'][str(self.definition_paths[VOICE])] = sha(definition(self.voice_candidate))
            self.expected['capture_snapshot']['inputs'][str(self.definition_paths[VOICE])] = sha(definition(self.voice_candidate))
            value = self.system._show(MODEL, self.system.clock() + 5,
                tuple(self.capture_before['services'][MODEL]))
            require(value['MainPID'] == str(self.running[MODEL]['process']['pid'])
                    and value['InvocationID'] == self.running[MODEL]['invocation']
                    and value['ActiveState'] == 'active' and self.system._no_job(value['Job']))
            self.expected['capture_snapshot']['services'][MODEL] = value
            self.record('vision_configuration_verified', dict(runtime_changed=False,
                model_pin=self.model_pin, probe=self.vision_probe,
                capture_input_sha256=sha(self.helper.canonical(self.expected['capture_snapshot']))))

        def start_model(self):
            self.current_after_auth()
            require(self.collector.model_argv(self.system, self.helper) == self.model_candidate)
            self.record('model_start_intent')
            self.system._command(['start', '--no-block', MODEL], self.system.clock() + 5)
            deadline = self.system.clock() + 120
            observed = None
            while True:
                require(self.system.clock() < deadline)
                value = self.show(MODEL)
                require(value['ActiveState'] in ('activating', 'active'))
                if int(value['MainPID']) > 0:
                    process, observed = self.starting_process(MODEL, value, observed, deadline)
                    if process is None:
                        time.sleep(0.2)
                        continue
                    args = (base.PROC / value['MainPID'] / 'cmdline').read_bytes().split(b'\0')[:-1]
                    require(args == [arg.encode() for arg in self.model_candidate])
                    if value['ActiveState'] == 'active':
                        require(self.system._no_job(value['Job']))
                        self.running[MODEL] = dict(invocation=value['InvocationID'], process=process)
                        self.current_after_auth()
                        try:
                            ready = self.helper.Recovery.model_ready(self)
                        except ConnectionRefusedError:
                            ready = None
                        if ready is not None:
                            self.model_pin = ready
                            break
                time.sleep(0.2)
            self.current_after_auth()
            self.record('model_start_done', self.model_pin)

        def restored_identity(self, service, value, process):
            if service != VOICE:
                return super().restored_identity(service, value, process)
            require(self.vision_probe is not None)
            original = self.original_envelope['maintenance']['services'][VOICE]
            require(value['InvocationID'] != original['unit']['InvocationID']
                    and process['executable'] == original['process']['executable']
                    and process['uid'] == original['process']['uid'])
            args = Path('/proc', str(process['pid']), 'cmdline').read_bytes().split(b'\0')[:-1]
            require(args[-len(self.voice_candidate):] == [arg.encode() for arg in self.voice_candidate]
                    and len(args) in (len(self.voice_candidate), len(self.voice_candidate)+1))

        def retain_audio_failure(self, owner):
            result = super().retain_audio_failure(owner)
            if not self.model_touched:
                return result
            prior = signal.alarm(0)
            handlers = {number: signal.signal(number, signal.SIG_IGN) for number in (signal.SIGINT, signal.SIGTERM)}
            signal.alarm(90)
            result['model_contained'] = False
            try:
                require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
                path = self.guard_path(MODEL)
                if not os.path.lexists(path):
                    self.helper.write(path, self.guard())
                require(self.helper.public(path) == self.guard())
                self.guarded.add(MODEL)
                self.system._command(['daemon-reload'], self.system.clock() + 10)
                self.system._guard(MODEL, self.system.clock() + 5, str(STATE))
                self.system._command(['stop', MODEL], self.system.clock() + 60)
                value = self.show(MODEL)
                require(value['MainPID'] == '0' and value['ActiveState'] in ('inactive', 'failed')
                        and self.system._no_job(value['Job']))
                root = self.system._cgroup('/system.slice/' + MODEL)
                self.system._empty_tree(root, self.system.clock() + 5)
                if root.exists():
                    require(dict(line.split() for line in (root/'cgroup.events').read_text().splitlines()).get('populated') == '0')
                require(self.show(MODEL) == value)
                result['model_contained'] = True
            except BaseException:
                pass
            finally:
                signal.alarm(0)
                for number, handler in handlers.items():
                    signal.signal(number, handler)
                if prior:
                    signal.alarm(prior)
            try:
                self.record('vision_failure_retained', result)
            except BaseException:
                pass
            return result
    return Vision


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--backend', required=True)
    parser.add_argument('--backend-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approve-model-restart', action='store_true')
    parser.add_argument('--approve-microphone-capture', action='store_true')
    parser.add_argument('--wait-owner-seconds', type=int, default=300)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0)
    require(not args.apply or (args.approve_model_restart and args.approve_microphone_capture
                              and 1 <= args.wait_owner_seconds <= 300))
    path = Path(__file__).with_name('prepare-runtime-maintenance.py')
    require(str(path) == args.backend)
    base = types.ModuleType('vision_maintenance_base')
    base.__file__ = str(path)
    raw = trusted(path, args.backend_sha256)
    exec(compile(raw, str(path), 'exec'), base.__dict__)
    base.STATE = STATE
    expected_raw = trusted(args.expected, args.expected_sha256)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    envelope = json.loads(expected_raw, object_pairs_hook=unique)
    require(envelope['backend'] == dict(path=args.backend, sha256=args.backend_sha256))
    default_pin = envelope['defaults']
    require(type(default_pin) is dict and set(default_pin) == {'path', 'sha256'}
            and default_pin['path'] == str(Path(__file__).with_name('camera-vision-defaults.py')))
    defaults = types.ModuleType('vision_defaults')
    defaults.__file__ = default_pin['path']
    exec(compile(trusted(default_pin['path'], default_pin['sha256']), default_pin['path'], 'exec'), defaults.__dict__)
    def interrupted(signum, frame):
        raise RuntimeError('vision_activation_interrupted')
    for number in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted)
    signal.alarm(900)
    backend = backend_class(base, defaults)(envelope)
    try:
        require(not STATE.exists())
        if args.apply:
            require(args.approve_model_restart and args.approve_microphone_capture)
            result = base.prepare(backend, approved_capture=True, wait_seconds=args.wait_owner_seconds)
            result.update(vision_inference_verified=True, runtime_changed=False, synthetic_probe=backend.vision_probe)
        else:
            result = dict(preflight=True, activation_authorized=False, reconciliation=backend.reconcile(),
                          runtime_changed=False, candidate_model_argv=backend.model_candidate)
        print(json.dumps(result, sort_keys=True))
    finally:
        signal.alarm(0)
        backend.close()


if __name__ == '__main__':
    main()
