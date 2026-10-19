#!/usr/bin/env python3
"""One-shot account setup inside a freshly prepared C–F SD image."""
import json
import os
from pathlib import Path
import pwd
import subprocess

CONFIG = Path('/etc/lantern-firstboot.json')
DONE = Path('/var/lib/lantern-base-provision/firstboot-complete')

def main():
    if DONE.exists():
        return
    model = Path('/proc/device-tree/model').read_bytes().rstrip(b'\0').decode()
    if model != 'Orange Pi 5 Plus':
        raise SystemExit('This provisioning image is for Orange Pi 5 Plus only')
    cfg = json.loads(CONFIG.read_text())
    user = cfg['username']
    try:
        pwd.getpwnam(user)
    except KeyError:
        subprocess.run(['useradd', '--create-home', '--shell', '/bin/bash', user], check=True)
    subprocess.run(['usermod', '-aG', 'sudo,audio,video,render,plugdev', user], check=True)
    subprocess.run(['chpasswd', '--encrypted'], input=f"root:{cfg['password_hash']}\n{user}:{cfg['password_hash']}\n", text=True, check=True)
    for name in ('root', user):
        subprocess.run(['chage', '-d', '1', '-M', '-1', name], check=True)
    subprocess.run(['ssh-keygen', '-A'], check=True)
    subprocess.run(['hostnamectl', 'set-hostname', cfg['hostname']], check=True)
    # Disable the interactive Armbian first-login wizard only after account setup.
    Path('/root/.not_logged_in_yet').unlink(missing_ok=True)
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(cfg['hostname'] + '\n')
    CONFIG.unlink()
    os.sync()

if __name__ == '__main__':
    main()
