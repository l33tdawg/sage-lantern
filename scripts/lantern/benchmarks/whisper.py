#!/usr/bin/env python3
"""Synthetic English ABBA benchmark; run as root with Python 3.11/3.12.

Never records a microphone. Temporarily stops the idle voice-loop service.
Synthetic English cannot establish Malay, Arabic, accent, or noisy-ASR quality.
Do not press PTT or use touch voice while running. SIGKILL/power loss cannot
restore the service; recover with systemctl start sage-lantern-voice-loop.service.
"""

import warnings

with warnings.catch_warnings():
    warnings.simplefilter('ignore', DeprecationWarning)
    import audioop
import http.client
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import tempfile
import time
import wave


WHISPER = Path('/opt/sage-lantern/bin/whisper-cli')
MODEL = Path('/opt/sage-lantern/models/ggml-base.bin')
PYTHON = Path('/opt/sage-lantern/venv/bin/python')
PIPER = Path('/opt/sage-lantern/models/en_US-lessac-medium.onnx')
PIPER_CONFIG = Path(str(PIPER) + '.json')
SYSTEMCTL = '/usr/bin/systemctl'
SERVICE = 'sage-lantern-voice-loop.service'
EXTRA = ['--beam-size', '1', '--best-of', '2', '--no-timestamps']
FIXTURES = (
    ('general', 'What is the Matrix movie about?'),
    ('photo', 'What does this text say?'),
    ('names', 'My name is Farid. I live in Kuala Lumpur.'),
)


def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)


def run(command, timeout, *, input_text=None, stdout=subprocess.DEVNULL):
    child = subprocess.Popen(
        [str(part) for part in command],
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=stdout, stderr=subprocess.DEVNULL, start_new_session=True,
        preexec_fn=child_limits,
    )
    try:
        child.communicate(
            input=None if input_text is None else input_text.encode('utf-8'),
            timeout=timeout,
        )
        return child.returncode
    except subprocess.TimeoutExpired:
        return 124
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def require_idle():
    connection = http.client.HTTPConnection('127.0.0.1', 3197, timeout=3)
    try:
        connection.request('GET', '/health')
        response = connection.getresponse()
        payload = response.read(4097)
        if response.status != 200 or len(payload) > 4096:
            raise RuntimeError('voice health unavailable')
        health = json.loads(payload)
        if health.get('touch_ready') is not True or health.get('busy') is not False:
            raise RuntimeError('voice not idle')
    finally:
        connection.close()


def convert(source, destination):
    with wave.open(str(source), 'rb') as wav:
        channels, width, rate, frames = (
            wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        )
        if channels not in (1, 2) or width != 2 or not 8000 <= rate <= 96000:
            raise RuntimeError('unsupported synthetic WAV')
        if not 0 < frames <= rate * 30 or wav.getcomptype() != 'NONE':
            raise RuntimeError('invalid synthetic WAV duration')
        data = wav.readframes(frames)
        if len(data) != frames * channels * width:
            raise RuntimeError('truncated synthetic WAV')
    if channels == 2:
        data = audioop.tomono(data, width, 0.5, 0.5)
    data, _ = audioop.ratecv(data, width, 1, rate, 16000, None)
    with wave.open(str(destination), 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(data)


def emit(fixture, variant, elapsed, transcript, returncode):
    print(json.dumps(dict(fixture=fixture, variant=variant, time=round(elapsed, 4),
                         transcript=transcript, returncode=returncode)), flush=True)


def interrupted(signum, frame):
    raise InterruptedError('benchmark interrupted')


def main():
    if os.geteuid() != 0:
        raise RuntimeError('root required')
    os.umask(0o077)
    for path in (WHISPER, MODEL, PYTHON, PIPER, PIPER_CONFIG):
        if not path.is_file():
            raise RuntimeError('missing installed asset')
    for path in (WHISPER, PYTHON, Path(SYSTEMCTL)):
        if not os.access(path, os.X_OK):
            raise RuntimeError('missing executable')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with tempfile.TemporaryDirectory(prefix='lantern-whisper-benchmark-') as directory:
        root = Path(directory)
        with (root / 'help').open('w+b') as output:
            child = subprocess.Popen([str(WHISPER), '--help'], stdout=output,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     start_new_session=True, preexec_fn=child_limits)
            try:
                child.wait(timeout=10)
                output.seek(0)
                help_text = output.read(262145)
                if child.returncode != 0 or len(help_text) > 262144:
                    raise RuntimeError('help check failed')
                if not all(flag.encode() in help_text for flag in EXTRA[::2]):
                    raise RuntimeError('benchmark flags unavailable')
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
        require_idle()
        if run([SYSTEMCTL, 'is-active', '--quiet', SERVICE], 10) != 0:
            raise RuntimeError('voice service not active')
        try:
            require_idle()
            if run([SYSTEMCTL, 'stop', SERVICE], 30) != 0:
                raise RuntimeError('voice stop failed')
            if run([SYSTEMCTL, 'is-active', '--quiet', SERVICE], 10) != 3:
                raise RuntimeError('voice stop not confirmed')
            failed = False
            for fixture, text in FIXTURES:
                original, audio = root / (fixture + '-source.wav'), root / (fixture + '.wav')
                if run([PYTHON, '-m', 'piper', '-m', PIPER, '-c', PIPER_CONFIG,
                        '-f', original], 90, input_text=text + '\n') != 0:
                    raise RuntimeError('synthetic generation failed')
                convert(original, audio)
                for index, variant in enumerate(('A', 'B', 'B', 'A')):
                    output_base = root / f'{fixture}-{index}'
                    command = [WHISPER, '-m', MODEL, '-f', audio, '-t', '6',
                               '-otxt', '-of', output_base, '-np']
                    if variant == 'B':
                        command += EXTRA
                    started = time.monotonic()
                    returncode = run(command, 30)
                    elapsed = time.monotonic() - started
                    transcript = ''
                    output = output_base.with_suffix('.txt')
                    if output.exists():
                        with output.open('rb') as handle:
                            data = handle.read(65537)
                        if len(data) > 65536:
                            raise RuntimeError('oversized synthetic transcript')
                        transcript = data.decode('utf-8', errors='replace').strip()
                    emit(fixture, variant, elapsed, transcript, returncode)
                    failed |= returncode != 0 or not transcript
            return int(failed)
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            if run([SYSTEMCTL, 'start', SERVICE], 90) != 0:
                raise RuntimeError('voice restore failed')
            deadline = time.monotonic() + 15
            while True:
                try:
                    require_idle()
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('restored voice health unavailable') from None
                    time.sleep(0.5)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        emit('benchmark', 'error', 0, '', 1)
        raise SystemExit(1)
