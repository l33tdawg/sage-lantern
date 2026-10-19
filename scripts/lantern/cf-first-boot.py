#!/usr/bin/env python3
"""Read-only Orange Pi 5 Plus C–F inventory. Never installs or commissions."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import sys

SCHEMA = 'sage.lantern.cf-first-boot.v1'
LIMIT = 1024 * 1024
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
FILES = {'board_model': '/proc/device-tree/model', 'board_compatible': '/proc/device-tree/compatible',
         'memory': '/proc/meminfo', 'os': '/etc/os-release',
         'alsa_cards': '/proc/asound/cards', 'alsa_pcm': '/proc/asound/pcm'}
COMMANDS = {
    'kernel': ['uname', '-srmo'],
    'storage': ['lsblk', '--json', '--bytes', '--paths', '--output', 'PATH,TYPE,SIZE,MODEL,SERIAL,RO'],
    'root_mount': ['findmnt', '--json', '--output', 'SOURCE,FSTYPE', '/'],
    'network': ['ip', '-j', 'address', 'show'],
    'usb': ['lsusb'],
    'python': ['/usr/bin/python3', '--version'],
    'node': ['/usr/bin/node', '--version'],
    'systemd': ['systemctl', '--version'],
    'compiler': ['/usr/bin/c++', '--version'],
    'cmake': ['/usr/bin/cmake', '--version'],
    'render_group': ['getent', 'group', 'render'],
    'video_group': ['getent', 'group', 'video'],
    'python_base_modules': ['/usr/bin/python3', '-I', '-B', '-c',
        'import importlib.util,json; print(json.dumps({n: importlib.util.find_spec(n) is not None '
        'for n in ("venv","ensurepip","ssl")}))'],
}
GLOBS = {'npu_devices': ('/dev', ('rknpu*',)),
         'npu_drivers': ('/sys/bus/platform/drivers', ('rknpu*',)),
         'npu_modules': ('/sys/module', ('rknpu*',)),
         'drm_devices': ('/sys/class/drm', ('renderD*',)),
         'video_devices': ('/sys/class/video4linux', ('video*',)),
         'audio_devices': ('/sys/class/sound', ('card*',)),
         'serial_devices': ('/dev', ('ttyACM*', 'ttyUSB*', 'ttyS*')),
         'media_devices': ('/dev', ('media[0-9]*', 'v4l-subdev[0-9]*')),
         'i2s_drivers': ('/sys/bus/platform/drivers', ('*i2s*', '*tdm*')),
         'camera_drivers': ('/sys/bus/platform/drivers', ('*cif*', '*isp*', '*csi*')),
         'camera_sensor_drivers': ('/sys/bus/i2c/drivers', ('imx519', 'ov13850', 'ov13855')),
         'spi_devices': ('/sys/bus/spi/devices', ('spi*',)),
         'pps_devices': ('/sys/class/pps', ('pps*',)),
         'input_devices': ('/sys/class/input', ('event*',))}
PROBES = set(FILES) | set(COMMANDS) | set(GLOBS)


def unavailable(reason):
    return {'status': 'unknown', 'reason': reason}


def collect():
    """Fixed read-only sources. No shell, configuration sourcing or credential reads."""
    probes = {}
    for name, location in FILES.items():
        try:
            with open(location, 'rb') as stream:
                raw = stream.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError('oversize')
            probes[name] = {'status': 'pass', 'value': raw.decode('utf-8')}
        except (OSError, UnicodeError, ValueError):
            probes[name] = unavailable('file unavailable or unreadable')
    for name, argv in COMMANDS.items():
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=5, env=ENV, check=False)
            if name in ('render_group', 'video_group') and result.returncode == 2 and not result.stdout:
                # getent exit 2 means the requested group was not found;
                # tool failure/missing permission still stays unknown below.
                probes[name] = {'status': 'pass', 'value': ''}
            elif result.returncode != 0 or len(result.stdout) > LIMIT:
                probes[name] = unavailable('command failed or output too large')
            else:
                probes[name] = {'status': 'pass', 'value': result.stdout.decode('utf-8')}
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            probes[name] = unavailable('command missing, timed out or unreadable')
    for name, (directory, patterns) in GLOBS.items():
        try:
            base = Path(directory)
            # Explicit enumeration distinguishes unreadable/missing directories from no matches.
            entries = list(base.iterdir())
            probes[name] = {'status': 'pass', 'value': sorted({entry.name for entry in entries
                if any(entry.match(pattern) for pattern in patterns)})}
        except OSError:
            probes[name] = unavailable('enumeration unavailable')
    return probes


def read_fixture(path):
    with open(path, 'rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('fixture too large')
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('schema') != 'sage.lantern.cf-first-boot-fixture.v1':
        raise ValueError('invalid fixture schema')
    probes = value.get('probes')
    if not isinstance(probes, dict) or not set(probes) <= PROBES:
        raise ValueError('invalid fixture probes')
    for name, probe in probes.items():
        if not isinstance(probe, dict) or probe.get('status') not in ('pass', 'unknown'):
            raise ValueError('invalid fixture observation')
        if probe['status'] == 'pass':
            expected = list if name in GLOBS else str
            if not isinstance(probe.get('value'), expected):
                raise ValueError('invalid fixture value')
    return probes


def report(unit_id, probes, source='live'):
    if unit_id not in ('unit-c', 'unit-d', 'unit-e', 'unit-f'):
        raise ValueError('unit must be C, D, E or F')
    evidence = {}

    def item(name, parser=lambda x: x):
        probe = probes.get(name, {})
        if probe.get('status') != 'pass':
            evidence[name] = unavailable('probe unavailable')
            return None
        try:
            value = parser(probe['value'])
            evidence[name] = {'status': 'pass', 'value': value}
            return value
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            evidence[name] = unavailable('probe output could not be interpreted')
            return None

    def nonempty(raw):
        value = raw.strip('\x00\n ')
        if not value:
            raise ValueError('empty')
        return value[:512]

    item('board_model', nonempty)
    compatible = item('board_compatible', lambda raw: [x for x in raw.strip('\0\n').split('\0') if x])
    def memory(raw):
        match = re.search(r'^MemTotal:\s+(\d+)\s+kB$', raw, re.M)
        return int(match.group(1)) * 1024
    ram = item('memory', memory)
    def os_fields(raw):
        fields = {}
        for line in raw.splitlines():
            key, sep, val = line.partition('=')
            if sep and key in ('ID', 'ID_LIKE', 'VERSION_ID', 'VERSION_CODENAME', 'PRETTY_NAME'):
                fields[key] = val.strip('"\'')[:256]
        if not fields.get('ID'):
            raise ValueError('missing OS ID')
        return fields
    os_info = item('os', os_fields)
    kernel = item('kernel', nonempty)
    def disks(raw):
        def rows(devices):
            result = []
            for disk in devices:
                size = disk.get('size')
                if size is not None:
                    size = int(size)
                    if size < 0:
                        raise ValueError('negative capacity')
                row = {key: disk.get(key) for key in ('path', 'type', 'model', 'serial', 'ro')}
                row['size_bytes'] = size
                result.append(row)
                result.extend(rows(disk.get('children', [])))
            return result
        return rows(json.loads(raw)['blockdevices'])
    storage = item('storage', disks)
    item('root_mount', lambda raw: [{key: row.get(key) for key in ('source', 'fstype')}
                                  for row in json.loads(raw)['filesystems']])
    def addresses(raw):
        return [{'interface': row.get('ifname'), 'state': row.get('operstate'),
                 'addresses': [{'family': addr.get('family'), 'address': addr.get('local'),
                                'prefix_length': addr.get('prefixlen'), 'scope': addr.get('scope')}
                               for addr in row.get('addr_info', [])]} for row in json.loads(raw)]
    network = item('network', addresses)
    def usb_devices(raw):
        result = []
        for line in raw.splitlines():
            match = re.fullmatch(r'Bus (\d+) Device (\d+): ID ([0-9a-fA-F]{4}):([0-9a-fA-F]{4})(?: (.*))?', line)
            if not match:
                raise ValueError('invalid lsusb output')
            result.append(dict(bus=match[1], device=match[2], vendor_id=match[3], product_id=match[4],
                               description=(match[5] or '')[:256]))
        return result
    item('usb', usb_devices)
    python = item('python', nonempty)
    node = item('node', nonempty)
    systemd = item('systemd', lambda raw: nonempty(raw.splitlines()[0]))
    compiler = item('compiler', lambda raw: nonempty(raw.splitlines()[0]))
    cmake = item('cmake', lambda raw: nonempty(raw.splitlines()[0]))
    def group(raw, expected):
        if raw == '':
            return {'present': False}
        fields = raw.strip().split(':')
        if len(fields) != 4 or fields[0] != expected or not fields[2].isdigit():
            raise ValueError('invalid group record')
        # Account membership is not needed for first-boot device-group evidence.
        return {'present': True, 'gid': int(fields[2])}
    groups = {name: item(name + '_group', lambda raw, name=name: group(raw, name))
              for name in ('render', 'video')}
    def base_modules(raw):
        value = json.loads(raw)
        if type(value) is not dict or set(value) != {'venv', 'ensurepip', 'ssl'} or any(
                type(child) is not bool for child in value.values()):
            raise ValueError('invalid module evidence')
        return value
    modules = item('python_base_modules', base_modules)
    def alsa(raw):
        # Kernel ALSA inventory only; never opens a capture/playback device.
        return [line[:512] for line in raw.splitlines()[:256]]
    item('alsa_cards', alsa)
    item('alsa_pcm', alsa)
    for name in GLOBS:
        item(name, lambda value: [x for x in value if isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_.:-]+', x)])

    checks = {}
    def check(name, passed, detail):
        checks[name] = {'status': 'unknown' if passed is None else ('pass' if passed else 'fail'), 'detail': detail}
    check('orange_pi_5_plus', None if not compatible else 'xunlong,orangepi-5-plus' in compatible,
          'Exact device-tree compatible must identify Orange Pi 5 Plus; model text is supporting evidence.')
    check('linux_arm64', None if not kernel else bool(re.search(r'^Linux\s', kernel) and re.search(r'\baarch64\b', kernel)),
          'Existing base installer needs Linux ARM64.')
    check('ram_16gb_tier', None if ram is None else ram >= 14 * 1024**3,
          'At least 14 GiB OS-visible memory expected for the 16 GB tier; reserved memory reduces MemTotal.')
    nvme = None if storage is None else [row for row in storage if row['type'] == 'disk'
             and re.fullmatch(r'/dev/nvme\d+n\d+', str(row['path']))]
    check('nvme_256gb_tier', None if nvme is None else any((row['size_bytes'] or 0) >= 250_000_000_000 for row in nvme),
          'An NVMe disk of at least 250 decimal GB must enumerate; this does not identify a safe write target or prove NVMe boot.')
    check('debian_bookworm_baseline', None if os_info is None else
          (os_info.get('ID') == 'debian' or 'debian' in os_info.get('ID_LIKE', '').split())
          and (os_info.get('VERSION_CODENAME') == 'bookworm'
               or bool(re.fullmatch(r'12(?:\..*)?', os_info.get('VERSION_ID', '')))),
          'Matches the bootstrap Debian/derived Bookworm/12 OS check; board/image qualification remains separate.')
    def version_at_least(value, pattern, required):
        if value is None:
            return None
        match = re.match(pattern, value)
        return None if not match else tuple(int(x) for x in match.groups()) >= required
    python_match = re.match(r'Python (\d+)\.(\d+)', python or '')
    check('python_baseline', None if not python_match else python_match.groups() == ('3', '11'),
          'The existing offline wheelhouse requires /usr/bin/python3 version 3.11 exactly.')
    check('node_baseline', version_at_least(node, r'v(\d+)\.(\d+)', (22, 13)), '/usr/bin/node must be at least 22.13; a shell-only alternate Node path is insufficient.')
    check('systemd_tools', None if systemd is None else systemd.startswith('systemd '), 'Tool presence does not prove systemd is PID 1 or services are installed.')
    check('native_compiler_present', None if compiler is None else True,
          '/usr/bin/c++ --version succeeded; actual ARM64 NPU/llama/Whisper compilation is still required.')
    check('cmake_present', None if cmake is None else cmake.startswith('cmake version '),
          '/usr/bin/cmake is required for the offline native source builds.')
    check('python_base_modules', None if modules is None else all(modules.values()),
          'venv, ensurepip and ssl module discovery is required; this neither creates a venv nor proves target wheel imports.')
    for name, value in groups.items():
        check(name + '_group_present', None if value is None else value['present'],
              'The NPU service requires the existing ' + name + ' group; group presence does not grant or prove device access.')
    check('network_address', None if network is None else any(addr.get('scope') == 'global' for row in network for addr in row['addresses']),
          'A global-scope address is recorded; reachability, SSH and Internet access are not tested.')
    for name, detail in {
        'npu_inference': 'NPU device/driver/module names are inventory only; matching RKLLM runtime and actual inference remain unverified.',
        'peripheral_commissioning': 'USB/audio/video/serial/input enumeration is not radio, GNSS, camera, microphone or touch acceptance. Unboxed or disconnected peripherals may be absent.',
        'software_installation': 'C–F bindings and Orange Pi image qualification are separate work. No installation/readiness claim is made.',
        'identity_and_vault': 'No SAGE, vault, SSH key, token or private identity files were inspected.',
        'power_and_thermal': 'Power supply, cooling, enclosure fit and sustained load remain bench checks.',
    }.items():
        check(name, None, detail)
    return {'schema': SCHEMA, 'unit_id': unit_id, 'unit_id_is_operator_label': True,
            'source': source, 'collected_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'read_only': True, 'ready': False, 'provisioned': False, 'write_authorized': False,
            'evidence': evidence, 'checks': checks,
            'failed_checks': [key for key, value in checks.items() if value['status'] == 'fail'],
            'unknown_checks': [key for key, value in checks.items() if value['status'] == 'unknown']}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--unit-id', choices=('unit-c', 'unit-d', 'unit-e', 'unit-f'), required=True)
    parser.add_argument('--fixture', type=Path, help='Offline synthetic probe JSON; performs no live probes')
    args = parser.parse_args()
    try:
        probes = read_fixture(args.fixture) if args.fixture else collect()
        print(json.dumps(report(args.unit_id, probes, 'fixture' if args.fixture else 'live'), indent=2, sort_keys=True))
    except (OSError, ValueError, TypeError) as exc:
        # Do not echo file contents, command output or paths from malformed inputs.
        print(json.dumps({'schema': SCHEMA, 'error': type(exc).__name__, 'ready': False}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
