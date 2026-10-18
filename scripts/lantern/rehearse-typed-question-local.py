#!/usr/bin/env python3
"""Rehearsal harness: answer one typed question with the installed question loop.

This is NOT the appliance path and it proves nothing about the locked-device
contract. It exists because the only authenticated client on a locked unit is
the panel itself: to exercise the installed bytes end to end before an owner
unlock, this harness runs `/usr/local/libexec/sage-lantern-voice-loop` as a
private process with two deliberate substitutions —

  * the device-lock gate is replaced by an always-authorized stub in a
    bootstrap `sitecustomize.py`, so a local operator can POST the control API
    without the owner PIN, and the access-epoch reader reports an unlocked
    device — which is what the protected conversation memory requires by
    design; and
  * `--alsa-device` points at a capture device that does not exist, so the
    spoken half of the lane is genuinely unusable for the whole run.

The typed lane must still open, review, submit and answer through the real
runtime, the real local model and the real personal SAGE. Every refusal, review
and lock behaviour of the shipped code is covered by
`scripts/tests/test_unit_a_voice_typed_question.py` instead; do not treat this
run as acceptance of the locked-device path.

Refuses to start while `sage-lantern-voice-loop.service` is active (the control
port is exclusive) and keeps every artifact under `--run-dir`.
"""

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import time
import uuid


VOICE = Path('/usr/local/libexec/sage-lantern-voice-loop')
VINTERPRETER = Path('/opt/sage-lantern/venv/bin/python')
SERVICE = 'sage-lantern-voice-loop.service'
ACCOUNT = 'sage-lantern'
BOOTSTRAP = (
    '"""Rehearsal-only device-lock stub; never part of an installed unit."""\n'
    'from sage_lantern import device_access as _device_access\n'
    '_device_access.lock_enabled = lambda **kwargs: False\n'
    '_device_access.authorized = lambda *args, **kwargs: True\n'
    '_device_access.device_unlocked = lambda **kwargs: True\n'
    '# The runtime gates protected conversation memory on an unlocked device\n'
    '# epoch; the rehearsal stands in for the owner unlock with a fixed epoch.\n'
    '_device_access.access_epoch = lambda **kwargs: "0" * 32\n'
)


def service_active():
    result = subprocess.run(['/usr/bin/systemctl', 'is-active', SERVICE],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return result.stdout.strip() == 'active'


def rehearsal_config(source, destination, conversation_dir):
    """The installed config with the private conversation store redirected."""
    lines = Path(source).read_text(encoding='utf-8').splitlines()
    rewritten = []
    replaced = False
    for line in lines:
        if line.strip().startswith('conversation_dir'):
            rewritten.append(f'conversation_dir = "{conversation_dir}"')
            replaced = True
        else:
            rewritten.append(line)
    if not replaced:
        raise SystemExit('rehearsal_config_lacks_conversation_dir')
    destination.write_text('\n'.join(rewritten) + '\n', encoding='utf-8')
    os.chmod(destination, 0o644)
    return destination


def runtime_wrapper(work_dir, run_dir, runtime):
    """Capture the runtime child's stderr: the installed loop hides it by design.

    The wrapper lives outside the run directory because `/run` is mounted
    `noexec` on this image; the executable half belongs on the root filesystem.
    """
    work_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = work_dir / 'runtime-rehearsal'
    path.write_text('#!/bin/sh\n'
                    f'exec {runtime} "$@" 2>>{run_dir}/runtime-stderr.log\n')
    os.chmod(path, 0o755)
    return path


def control(path, payload, token):
    connection = http.client.HTTPConnection('127.0.0.1', 3197, timeout=10)
    try:
        body = json.dumps(payload).encode('utf-8')
        connection.request('POST', path, body=body, headers={
            'Content-Type': 'application/json', 'X-Lantern-Voice': 'control',
            'Origin': 'http://127.0.0.1:3100',
            'X-Lantern-Session': token, 'Content-Length': str(len(body)),
        })
        response = connection.getresponse()
        raw = response.read(1024 * 1024)
        return response.status, (json.loads(raw) if raw else None)
    finally:
        connection.close()


def published(state):
    try:
        value = json.loads(Path(state).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def wait_for(state, phase, *, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = published(state)
        if value.get('phase') == phase:
            return value
        time.sleep(0.2)
    raise SystemExit('rehearsal_timeout_waiting_for_' + phase + ': ' + json.dumps(published(state))[:400])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question', required=True)
    parser.add_argument('--image', type=Path,
                        help='JPEG attached to the typed question (photo lane rehearsal)')
    parser.add_argument('--run-dir', type=Path, default=Path('/run/sage-lantern-voice-rehearsal'))
    parser.add_argument('--work-dir', type=Path, default=Path('/var/lib/lantern-work/console-rehearsal'),
                        help='executable wrapper location; /run is noexec on this image')
    parser.add_argument('--config', type=Path, default=Path('/etc/sage-lantern/lantern.toml'))
    parser.add_argument('--runtime', type=Path, default=Path('/opt/sage-lantern/venv/bin/sage-lantern'))
    parser.add_argument('--capture-device', default='hw:CARD=absent,DEV=0',
                        help='a capture device that must not exist, so capture is impossible')
    parser.add_argument('--timeout', type=float, default=180.0)
    args = parser.parse_args(argv)
    if service_active():
        raise SystemExit('refusing_to_rehearse_while_the_installed_service_is_active')
    if not VOICE.is_file() or not VINTERPRETER.exists():
        raise SystemExit('rehearsal_requires_the_installed_question_loop')
    run_dir = args.run_dir
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(run_dir, 0o700)
    bootstrap = run_dir / 'bootstrap'
    bootstrap.mkdir(mode=0o700, exist_ok=True)
    (bootstrap / 'sitecustomize.py').write_text(BOOTSTRAP)
    state = run_dir / 'state.json'
    state.unlink(missing_ok=True)
    history = run_dir / 'history'
    history.mkdir(mode=0o700, exist_ok=True)
    conversation = run_dir / 'conversations-v1'
    conversation.mkdir(mode=0o700, exist_ok=True)
    config = rehearsal_config(args.config, run_dir / 'lantern-rehearsal.toml', conversation)
    account = pwd.getpwnam(ACCOUNT)
    wrapper = runtime_wrapper(args.work_dir, run_dir, args.runtime)
    for path in (run_dir, bootstrap, history, conversation):
        os.chown(path, account.pw_uid, account.pw_gid)
    os.chown(wrapper, 0, 0)
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(bootstrap)
    environment['PYTHONNOUSERSITE'] = '1'
    environment.pop('LANTERN_DEVICE_TOKEN', None)
    command = ['/usr/sbin/runuser', '-u', ACCOUNT, '--', str(VINTERPRETER), str(VOICE),
               '--config', str(config), '--runtime', str(wrapper), '--photo-ready',
               '--alsa-device', args.capture_device, '--state', str(state),
               '--history-dir', str(history), '--photo-dir', str(run_dir / 'photos'),
               '--audio-dir', str(run_dir / 'audio'), '--review-audio-dir', str(run_dir / 'questions'),
               '--message-audio-dir', str(run_dir / 'dictation')]
    log = (run_dir / 'loop.log').open('ab', buffering=0)
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, env=environment)
    try:
        token = 'r' * 43
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not published(state):
            if process.poll() is not None:
                raise SystemExit('rehearsal_loop_exited_' + str(process.returncode))
            time.sleep(0.2)
        attachment = {}
        if args.image is not None:
            raw = args.image.read_bytes()
            if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
                raise SystemExit('rehearsal_image_must_be_jpeg')
            attachment = {'image_base64': base64.b64encode(raw).decode('ascii')}
            if len(base64.b64decode(attachment['image_base64'])) != len(raw):
                raise SystemExit('rehearsal_image_encoding_failed')
        status, question = control('/question/start',
                                   {'request_id': str(uuid.uuid4()), **attachment}, token)
        if status != 201:
            raise SystemExit('question_start_refused_' + str(status) + '_' + json.dumps(question))
        review = wait_for(state, 'review', seconds=30)
        if review.get('transcript') != '' or review.get('input_source') != 'typed':
            raise SystemExit('unexpected_review_state_' + json.dumps(review))
        if args.image is not None and review.get('has_photo') is not True:
            raise SystemExit('typed_photo_not_held_' + json.dumps(review))
        started = time.monotonic()
        status, submitted = control('/review/submit', dict(session_id=question['session_id'],
                                                          review_id=review['review_id'],
                                                          request_id=str(uuid.uuid4()),
                                                          question=args.question), token)
        if status != 200 or submitted.get('status') != 'submitted':
            raise SystemExit('review_submit_refused_' + str(status) + '_' + json.dumps(submitted))
        complete = wait_for(state, 'complete', seconds=args.timeout)
        print(json.dumps({'rehearsal': True, 'question': complete.get('transcript'),
                          'answer': complete.get('response'), 'input_source': review.get('input_source'),
                          'seconds': round(time.monotonic() - started, 2),
                          'capture_device': args.capture_device,
                          'photo_attached': review.get('has_photo') is True,
                          'captured_audio_files': sorted(path.name for path in (run_dir / 'audio').glob('*')),
                          'review_audio_files': sorted(path.name for path in (run_dir / 'questions').glob('*')),
                          'service_active_during_run': service_active()}, sort_keys=True))
        return 0
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        log.close()


if __name__ == '__main__':
    raise SystemExit(main())
