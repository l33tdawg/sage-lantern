#!/usr/bin/env bash
set -euo pipefail
exec python3 - "$@" <<'PY'
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time

STATE = Path('/var/lib/sage-lantern')
SYSTEMD = Path('/etc/systemd/system')
MODEL = Path('/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf')
PROJECTOR = Path('/opt/sage-lantern/models/mmproj-BF16.gguf')
BINARY = Path('/opt/sage-lantern/llama.cpp/llama-server')
VOICE = Path('/usr/local/libexec/sage-lantern-voice-loop')
MODEL_SHA = '00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4'
PROJECTOR_SHA = '302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6'
LLM_SERVICE = 'sage-lantern-llm.service'
VOICE_SERVICE = 'sage-lantern-voice-loop.service'
DROPIN = '90-lantern-local-vision.conf'


def run(*command):
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=240)


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Required regular artifact missing: {path}')
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def check_artifacts():
    hashes = dict(model_sha256=digest(MODEL), projector_sha256=digest(PROJECTOR), llama_server_sha256=digest(BINARY))
    if hashes['model_sha256'] != MODEL_SHA or hashes['projector_sha256'] != PROJECTOR_SHA:
        raise ValueError('Pinned model/projector SHA-256 mismatch')
    environment = dict(os.environ, LD_LIBRARY_PATH=str(BINARY.parent))
    help_result = subprocess.run([str(BINARY), '--help'], check=True, capture_output=True, text=True, timeout=30, env=environment)
    help_text = help_result.stdout + help_result.stderr
    for flag in ('--mmproj', '--no-mmproj-offload', '--image-max-tokens', '--parallel', '--n-gpu-layers'):
        if flag not in help_text.split():
            raise ValueError(f'Installed llama-server does not advertise {flag}')
    return hashes


def check_evidence(hashes):
    if not (STATE / 'LOCAL_VOICE_READY').is_file():
        raise ValueError('LOCAL_VOICE_READY is required')
    marker = STATE / 'LOCAL_VISION_VALIDATED'
    metadata = marker.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022 or metadata.st_size > 8192:
        raise ValueError('LOCAL_VISION_VALIDATED must be a root-owned, non-writable-by-others JSON file <=8192 bytes')
    evidence = json.loads(marker.read_text())
    if not isinstance(evidence, dict) or evidence.get('validated') is not True:
        raise ValueError('Explicit validated:true inference evidence is required')
    if any(evidence.get(key) != value for key, value in hashes.items()):
        raise ValueError('Validated inference hashes do not match installed artifacts')
    if not VOICE.is_file() or '--photo-ready' not in VOICE.read_text():
        raise ValueError('Installed voice wrapper lacks --photo-ready support')


def desired_dropins():
    return {
        LLM_SERVICE: (
            '[Service]\nExecStart=\n'
            f'ExecStart={BINARY} --model {MODEL} --host 127.0.0.1 --port 8081 '
            f'--ctx-size 4096 --threads 6 --parallel 1 --n-gpu-layers 0 '
            f'--mmproj {PROJECTOR} --no-mmproj-offload --image-max-tokens 256\n'
            'MemoryMax=5G\nMemorySwapMax=0\n'
        ),
        VOICE_SERVICE: f'[Service]\nExecStart=\nExecStart={VOICE} --photo-ready\n',
    }


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.vision-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def restart_services():
    run('systemctl', 'daemon-reload')
    run('systemctl', 'restart', LLM_SERVICE)
    for attempt in range(90):
        try:
            run('curl', '--fail', '--silent', '--max-time', '2', 'http://127.0.0.1:8081/health')
            break
        except subprocess.CalledProcessError:
            time.sleep(2)
    else:
        raise RuntimeError('Model health timed out; this is not an inference validation')
    run('systemctl', 'start', VOICE_SERVICE)
    run('systemctl', 'is-active', '--quiet', LLM_SERVICE, VOICE_SERVICE)


def configure(enable):
    desired = desired_dropins()
    paths = {service: SYSTEMD / f'{service}.d' / DROPIN for service in desired}
    for service, path in paths.items():
        if path.is_symlink():
            raise ValueError(f'Refusing symlink drop-in: {path}')
        if enable:
            for other in path.parent.glob('*.conf'):
                if other != path and any(line.strip().startswith('ExecStart=') for line in other.read_text().splitlines()):
                    raise ValueError(f'Existing ExecStart override needs operator review: {other}')
            if not (SYSTEMD / service).is_file():
                raise ValueError(f'Expected existing unit: {service}')
    previous = {service: path.read_text() if path.exists() else None for service, path in paths.items()}
    STATE.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='vision-dropins-backup-', dir=STATE))
    for service, path in paths.items():
        if path.parent.exists():
            shutil.copytree(path.parent, backup / f'{service}.d', symlinks=True)
    print(f'Drop-in backup: {backup}', flush=True)
    run('systemctl', 'stop', VOICE_SERVICE)
    try:
        for service, path in paths.items():
            if enable:
                atomic_write(path, desired[service])
            else:
                path.unlink(missing_ok=True)
        restart_services()
    except Exception:
        try:
            run('systemctl', 'stop', VOICE_SERVICE)
        except Exception as stopping:
            print(f'Could not stop voice during recovery: {stopping}', file=sys.stderr)
        for service, path in paths.items():
            if previous[service] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, previous[service])
        try:
            restart_services()
        except Exception as recovery:
            print(f'Recovery failed; inspect services and backup {backup}: {recovery}', file=sys.stderr)
        raise
    print('Optional vision enabled.' if enable else 'Own vision drop-ins removed; other overrides preserved.')


def main():
    parser = argparse.ArgumentParser(description='Optional single-server vision activation; no downloads, no inference. Run during an idle maintenance window. --enable requires LOCAL_VISION_VALIDATED JSON: validated:true, model_sha256, projector_sha256, llama_server_sha256. --disable restores underlying service configuration, not validation markers.')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check-only', action='store_true')
    modes.add_argument('--enable', action='store_true')
    modes.add_argument('--disable', action='store_true')
    args = parser.parse_args()
    if platform.machine() != 'aarch64':
        raise ValueError('Expected the Unit A ARM64 host')
    if not args.check_only and os.geteuid() != 0:
        raise ValueError('Run configuration changes with sudo')
    if args.disable:
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / '.vision-installer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            configure(False)
        return
    hashes = check_artifacts()
    print(json.dumps(hashes, sort_keys=True))
    if args.check_only:
        print('Artifacts and advertised flags verified; inference readiness is NOT established.')
        return
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / '.vision-installer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_evidence(hashes)
        configure(True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'vision installer: {error}', file=sys.stderr)
        sys.exit(1)
PY
