#!/usr/bin/env python3
"""Require guarded vision commissioning for an explicitly selected camera.

This default is a provisioning obligation, never a readiness marker or capture
authorization. Camera-less units have no such obligation and no camera claim.
Only the guarded vision driver writes model/voice definitions after its probe.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat


PATH = Path('/etc/sage-lantern/vision-default.json')


def require(value):
    if not value:
        raise ValueError('camera_vision_default_refused')


def policy(device):
    require(type(device) is str and re.fullmatch('/dev/v4l/by-id/[A-Za-z0-9_.:-]+-video-index[0-9]+', device))
    return dict(schema='sage.lantern.camera-vision-default.v1', camera_device=device,
        requested_by_default=True, readiness_verified=False,
        model=dict(path='/opt/sage-lantern/models/qwen3.5-4b-q4_k_m.gguf',
                   sha256='00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4'),
        projector=dict(path='/opt/sage-lantern/models/mmproj-BF16.gguf',
                       sha256='302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6'),
        activation='guarded-model-probe-then-audio-commissioning-and-broker-start')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def inspect(device):
    raw = canonical(policy(device))
    for parent in PATH.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    require(not os.path.lexists(PATH))
    return raw


def install(device):
    raw = inspect(device)
    descriptor = os.open(PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o644)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(PATH.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--device')
    selection.add_argument('--no-camera', action='store_true')
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args(argv)
    require(not (args.inspect and args.install))
    if args.no_camera:
        require(not args.inspect and not args.install)
        result = dict(camera_configured=False, vision_required=False, readiness_verified=False)
    elif args.install or args.inspect:
        require(os.geteuid() == 0)
        raw = install(args.device) if args.install else inspect(args.device)
        result = dict(policy_sha256=hashlib.sha256(raw).hexdigest(),
                      installed=args.install, vision_required=True, readiness_verified=False)
    else:
        result = policy(args.device)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
