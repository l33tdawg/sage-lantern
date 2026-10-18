#!/usr/bin/env python3
"""Prepare a fresh Orange Pi 5 Plus SD image on a Linux host (including C).

Requires a verified pristine .img, root and loop/mount tools. Does not flash a card.
Prompts for a bootstrap password; only its salted hash is written to the image.
Use one image per unit. Original vendor first-run creates fresh SSH host keys.
"""
import argparse
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

SOURCE_SHA256 = '4cdec691799521f88ada6d4e5888c067360b03297cb0fe2b351b2090e021e221'
SOURCE_BYTES = 6501171200
SOURCE_FS_UUID = '711b054e-2429-49e9-bcd4-2f26583514ae'

def validate_options(unit, username, address, gateway, mac, dns):
    if unit not in 'cdef' or len(unit) != 1:
        raise ValueError('unit must be c, d, e or f')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,30}', username) or username in ('root', 'nobody'):
        raise ValueError('Invalid regular username')
    if address:
        network = ipaddress.IPv4Interface(address)
        if not gateway or not mac or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', mac):
            raise ValueError('Static address requires gateway and the exact Ethernet MAC')
        if ipaddress.IPv4Address(gateway) not in network.network:
            raise ValueError('Gateway must be on the configured subnet')
        for value in dns:
            ipaddress.ip_address(value)
    elif gateway or mac or dns:
        raise ValueError('gateway/mac/dns require --address')

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def customize(root, unit, username, password_hash, address=None, gateway=None, mac=None, dns=()):
    if 'BOARD=orangepi5-plus' not in (root / 'etc/armbian-release').read_text().splitlines():
        raise ValueError('Image is not for Orange Pi 5 Plus')
    if (root / 'var/lib/sage-lantern').exists():
        raise ValueError('Image contains Lantern private state')
    hostname = 'lantern-' + unit
    script_dir = Path(__file__).resolve().parent
    for source, destination in [('cf-sd-firstboot.py', 'usr/local/sbin/lantern-firstboot'),
                                ('cf-nvme-setup.py', 'usr/local/sbin/lantern-nvme-setup'),
                                ('cf-enable-nvme-boot.py', 'usr/local/sbin/lantern-enable-nvme-boot')]:
        dst = root / destination
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(script_dir / source, dst)
        dst.chmod(0o755)
    config = root / 'etc/lantern-firstboot.json'
    config.write_text(json.dumps(dict(username=username, hostname=hostname, password_hash=password_hash)) + '\n')
    config.chmod(0o600)
    (root / 'etc/hostname').write_text(hostname + '\n')
    hosts = root / 'etc/hosts'
    lines = [line for line in hosts.read_text().splitlines() if not re.match(r'^127\.0\.1\.1\s', line)]
    hosts.write_text('\n'.join(lines) + '\n127.0.1.1\t' + hostname + '\n')
    service = root / 'etc/systemd/system/lantern-firstboot.service'
    service.write_text('''[Unit]
Description=Lantern initial account setup
After=local-fs.target
Before=ssh.service ssh.socket getty.target display-manager.service
ConditionPathExists=/etc/lantern-firstboot.json

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/sbin/lantern-firstboot
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
''')
    wants = root / 'etc/systemd/system/multi-user.target.wants'
    wants.mkdir(parents=True, exist_ok=True)
    (wants / service.name).symlink_to('../lantern-firstboot.service')
    # Prevent an account-setup failure from exposing the image's default login.
    for unit_name in ('ssh.service', 'ssh.socket'):
        drop = root / 'etc/systemd/system' / (unit_name + '.d')
        drop.mkdir(exist_ok=True)
        (drop / 'lantern-firstboot.conf').write_text('[Unit]\nRequires=lantern-firstboot.service\nAfter=lantern-firstboot.service\n')
    ssh = root / 'etc/ssh/sshd_config.d'
    ssh.mkdir(exist_ok=True)
    (ssh / '00-lantern-bootstrap.conf').write_text('PasswordAuthentication yes\nPermitRootLogin yes\n')
    # These are the pristine image's keys, never a running appliance's keys.
    for path in (root / 'etc/ssh').glob('ssh_host_*'):
        path.unlink()
    (root / 'etc/default/armbian-firstrun').write_text('OPENSSHD_REGENERATE_HOST_KEYS=false\n')
    (root / 'etc/machine-id').write_text('')
    dbus_id = root / 'var/lib/dbus/machine-id'
    if dbus_id.exists() or dbus_id.is_symlink():
        dbus_id.unlink()
    dbus_id.symlink_to('/etc/machine-id')
    if address:
        # JSON is valid YAML; exact MAC prevents duplicate static IPs on two NICs.
        net = {'network': {'version': 2, 'renderer': 'NetworkManager', 'ethernets': {
            'lantern-wired': {'match': {'macaddress': mac.lower()}, 'dhcp4': False,
                'addresses': [address], 'routes': [{'to': 'default', 'via': gateway}],
                'nameservers': {'addresses': list(dns)}}}}}
        netpath = root / 'etc/netplan'
        for path in netpath.glob('*.yaml'):
            path.unlink()
        config = netpath / '10-lantern.yaml'
        config.write_text(json.dumps(net, indent=2) + '\n')
        config.chmod(0o600)
    return hostname

def set_boot_uuid(root, root_uuid):
    value = str(uuid.UUID(root_uuid))
    path = root / 'boot/armbianEnv.txt'
    lines = [line for line in path.read_text().splitlines() if not line.startswith('rootdev=')]
    path.write_text('\n'.join(lines) + '\nrootdev=UUID=' + value + '\n')
    fstab = root / 'etc/fstab'
    old = fstab.read_text()
    if SOURCE_FS_UUID not in old:
        raise ValueError('Unexpected pristine fstab root UUID')
    fstab.write_text(old.replace(SOURCE_FS_UUID, value))

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--unit', choices=list('cdef'), required=True)
    ap.add_argument('--username', default='sage')
    ap.add_argument('--address', help='Optional unused static IPv4/prefix; otherwise DHCP')
    ap.add_argument('--gateway')
    ap.add_argument('--mac', help='Exact Ethernet MAC for static addressing')
    ap.add_argument('--dns', nargs='*', default=[])
    args = ap.parse_args()
    validate_options(args.unit, args.username, args.address, args.gateway, args.mac, args.dns)
    if os.geteuid() != 0 or not Path('/dev/loop-control').exists():
        raise SystemExit('Run on Linux as root; C can build these images after NVMe migration')
    mounted = subprocess.check_output(['findmnt', '-rn', '-o', 'UUID'], text=True).split()
    if SOURCE_FS_UUID in mounted:
        raise SystemExit('The source image UUID is mounted on this host. Finish SPI/NVMe boot first to avoid duplicate device aliases.')
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit('Output exists; refusing to overwrite it')
    pending = Path(str(args.output) + '.incomplete')
    if pending.exists() or pending.is_symlink():
        raise SystemExit('An incomplete build exists; inspect it before retrying')
    if args.source.stat().st_size != SOURCE_BYTES or digest(args.source) != SOURCE_SHA256:
        raise SystemExit('Source differs from the verified pristine Armbian 26.8.2 image')
    password = getpass.getpass('Bootstrap password for root and ' + args.username + ': ')
    if not password or password != getpass.getpass('Repeat password: '):
        raise SystemExit('Passwords empty or do not match')
    pw_hash = subprocess.check_output(['openssl', 'passwd', '-6', '-stdin'], input=password + '\n', text=True).strip()
    del password
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.source.open('rb') as src, pending.open('xb') as dst:
        pending.chmod(0o600)
        shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
    loop = subprocess.check_output(['losetup', '--find', '--show', '--partscan', str(pending)], text=True).strip()
    try:
        part = Path(loop + 'p1')
        for _ in range(50):
            if part.exists():
                break
            time.sleep(.1)
        # Never leave all fleet SDs sharing the vendor image's filesystem and
        # GPT identities. This also avoids ambiguous udev links while building.
        root_uuid = str(uuid.uuid4())
        subprocess.run(['tune2fs', '-U', root_uuid, str(part)], check=True)
        subprocess.run(['sfdisk', '--disk-id', loop, str(uuid.uuid4())], check=True)
        subprocess.run(['sfdisk', '--part-uuid', loop, '1', str(uuid.uuid4())], check=True)
        subprocess.run(['udevadm', 'settle'], check=True)
        with tempfile.TemporaryDirectory(prefix='lantern-image-') as mount:
            subprocess.run(['mount', str(part), mount], check=True)
            try:
                hostname = customize(Path(mount), args.unit, args.username, pw_hash,
                                     args.address, args.gateway, args.mac, args.dns)
                set_boot_uuid(Path(mount), root_uuid)
                os.sync()
            finally:
                subprocess.run(['umount', mount], check=True)
    finally:
        subprocess.run(['losetup', '-d', loop], check=True)
    sha = digest(pending)
    os.link(pending, args.output)  # Publish without overwriting any existing file.
    pending.unlink()
    Path(str(args.output) + '.sha256').write_text(sha + '  ' + args.output.name + '\n')
    print(json.dumps({'image': str(args.output), 'sha256': sha, 'hostname': hostname,
                      'username': args.username, 'network': args.address or 'DHCP',
                      'nvme': 'SSH in, inspect with sudo lantern-nvme-setup, then explicitly apply',
                      'status': 'image prepared; physical first boot still requires verification'}, indent=2))

if __name__ == '__main__':
    main()
