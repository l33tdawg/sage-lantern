import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/lantern/cf-first-boot.py'
SPEC = importlib.util.spec_from_file_location('cf_first_boot', SCRIPT)
CF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CF)


def fixture():
    values = {
        'board_model': 'Orange Pi 5 Plus\0',
        'board_compatible': 'xunlong,orangepi-5-plus\0rockchip,rk3588\0',
        'memory': 'MemTotal:       16000000 kB\n',
        'os': 'ID=debian\nVERSION_CODENAME=bookworm\nVERSION_ID="12"\nSECRET=do-not-report\n',
        'kernel': 'Linux 6.1.43-rockchip aarch64 GNU/Linux\n',
        'storage': json.dumps({'blockdevices': [{'path': '/dev/nvme0n1', 'type': 'disk',
            'size': 256060514304, 'model': 'Test 256GB', 'serial': 'TEST-C', 'ro': False,
            'extra': 'do-not-report', 'children': [{'path': '/dev/nvme0n1p1', 'type': 'part', 'size': 200000000000}]}]}),
        'root_mount': '{"filesystems":[{"source":"/dev/mmcblk1p2","fstype":"ext4"}]}',
        'network': json.dumps([{'ifname': 'eth0', 'operstate': 'UP', 'address': 'do-not-report',
            'addr_info': [{'family': 'inet', 'local': '192.0.2.14', 'prefixlen': 24, 'scope': 'global'}]}]),
        'usb': 'Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n',
        'python': 'Python 3.11.2\n', 'node': 'v22.13.0\n', 'systemd': 'systemd 252 (252.38-1)\n+PAM\n',
        'compiler': 'c++ (Debian 12.2.0) 12.2.0\n', 'cmake': 'cmake version 3.25.1\n',
        'render_group': 'render:x:107:do-not-report\n', 'video_group': 'video:x:44:do-not-report\n',
        'python_base_modules': '{"venv":true,"ensurepip":true,"ssl":true}',
        'alsa_cards': ' 0 [rockchipi2s]: rockchip-i2s - onboard audio\n',
        'alsa_pcm': '00-00: capture fixture : capture 1\n',
    }
    values.update({name: [] for name in CF.GLOBS})
    values['npu_drivers'] = ['rknpu']
    return {name: {'status': 'pass', 'value': value} for name, value in values.items()}


class FirstBootTests(unittest.TestCase):
    def test_complete_inventory_never_means_commissioned(self):
        result = CF.report('unit-c', fixture())
        self.assertEqual(result['failed_checks'], [])
        self.assertEqual(result['checks']['orange_pi_5_plus']['status'], 'pass')
        self.assertEqual(result['checks']['nvme_256gb_tier']['status'], 'pass')
        for key in ('ready', 'provisioned', 'write_authorized'):
            self.assertFalse(result[key])
        for key in ('peripheral_commissioning', 'npu_inference', 'identity_and_vault'):
            self.assertEqual(result['checks'][key]['status'], 'unknown')
        # NVMe presence never rewrites actual SD-root evidence.
        self.assertEqual(result['evidence']['root_mount']['value'][0]['source'], '/dev/mmcblk1p2')

    def test_wrong_board_is_failure_despite_matching_model_string(self):
        data = fixture()
        data['board_compatible']['value'] = 'radxa,rock-5b-plus\0rockchip,rk3588\0'
        self.assertEqual(CF.report('unit-d', data)['checks']['orange_pi_5_plus']['status'], 'fail')

    def test_no_probe_is_unknown_not_success_or_absence(self):
        result = CF.report('unit-e', {})
        self.assertEqual(result['failed_checks'], [])
        self.assertTrue(all(row['status'] == 'unknown' for row in result['checks'].values()))
        self.assertTrue(all(row['status'] == 'unknown' for row in result['evidence'].values()))

    def test_connected_storage_absence_and_small_ram_fail(self):
        data = fixture()
        data['storage']['value'] = '{"blockdevices":[]}'
        data['memory']['value'] = 'MemTotal: 8000000 kB\n'
        result = CF.report('unit-f', data)
        self.assertEqual(result['checks']['nvme_256gb_tier']['status'], 'fail')
        self.assertEqual(result['checks']['ram_16gb_tier']['status'], 'fail')
        self.assertEqual(result['checks']['peripheral_commissioning']['status'], 'unknown')

    def test_network_loopback_only_is_not_reachable_address(self):
        data = fixture()
        data['network']['value'] = '[{"ifname":"lo","addr_info":[{"local":"127.0.0.1","scope":"host"}]}]'
        self.assertEqual(CF.report('unit-c', data)['checks']['network_address']['status'], 'fail')

    def test_unqualified_os_and_old_exact_node_fail(self):
        data = fixture()
        data['os']['value'] = 'ID=ubuntu\nVERSION_CODENAME=noble\n'
        data['node']['value'] = 'v22.12.0\n'
        result = CF.report('unit-c', data)
        self.assertEqual(result['checks']['debian_bookworm_baseline']['status'], 'fail')
        self.assertEqual(result['checks']['node_baseline']['status'], 'fail')
        self.assertEqual(CF.COMMANDS['node'][0], '/usr/bin/node')

    def test_extra_os_network_storage_fields_are_not_reported(self):
        result = json.dumps(CF.report('unit-c', fixture()))
        self.assertNotIn('do-not-report', result)
        self.assertNotIn('SECRET', result)
        self.assertIn('TEST-C', result)  # Public hardware serial is deliberate inventory.

    def test_newer_python_still_fails_exact_offline_wheelhouse_requirement(self):
        data = fixture()
        data['python']['value'] = 'Python 3.12.4\n'
        self.assertEqual(CF.report('unit-c', data)['checks']['python_baseline']['status'], 'fail')

    def test_debian_derived_12_matches_existing_bootstrap(self):
        data = fixture()
        data['os']['value'] = 'ID=vendor\nID_LIKE="debian"\nVERSION_ID="12.1"\n'
        self.assertEqual(CF.report('unit-c', data)['checks']['debian_bookworm_baseline']['status'], 'pass')

    def test_malformed_command_output_is_unknown(self):
        data = fixture()
        for name in ('storage', 'network', 'memory', 'root_mount', 'usb'):
            data[name]['value'] = 'invalid'
        result = CF.report('unit-c', data)
        for name in ('storage', 'network', 'memory', 'root_mount', 'usb'):
            self.assertEqual(result['evidence'][name]['status'], 'unknown')

    def test_fixture_cli_does_not_probe_live_machine(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            path.write_text(json.dumps({'schema': 'sage.lantern.cf-first-boot-fixture.v1', 'probes': fixture()}))
            with patch.object(CF, 'collect', side_effect=AssertionError('must not probe')):
                with patch('sys.argv', [str(SCRIPT), '--unit-id', 'unit-c', '--fixture', str(path)]):
                    with patch('builtins.print') as printed:
                        self.assertEqual(CF.main(), 0)
            result = json.loads(printed.call_args.args[0])
            self.assertEqual(result['source'], 'fixture')
            self.assertFalse(result['ready'])

    def test_fixture_rejects_arbitrary_probe_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            path.write_text(json.dumps({'schema': 'sage.lantern.cf-first-boot-fixture.v1',
                'probes': {'/home/operator/.ssh/id_ed25519': {'status': 'pass', 'value': 'x'}}}))
            with self.assertRaises(ValueError):
                CF.read_fixture(path)

    def test_live_missing_tools_and_permissions_preserve_unknown(self):
        with patch('builtins.open', side_effect=PermissionError), \
             patch.object(CF.Path, 'iterdir', side_effect=PermissionError), \
             patch.object(CF.subprocess, 'run', side_effect=subprocess.TimeoutExpired('probe', 5)) as run:
            result = CF.collect()
        self.assertEqual(set(result), CF.PROBES)
        self.assertTrue(all(row['status'] == 'unknown' for row in result.values()))
        self.assertEqual(run.call_count, len(CF.COMMANDS))
        for call in run.call_args_list:
            self.assertIn(call.args[0], CF.COMMANDS.values())
            self.assertNotIn('shell', call.kwargs)
            self.assertEqual(call.kwargs['timeout'], 5)
            self.assertEqual(call.kwargs['env'], CF.ENV)

    def test_labels_outside_first_four_rejected(self):
        for name in ('unit-a', 'unit-b', 'unit-g', 'unit-h', '../unit-c'):
            with self.assertRaises(ValueError):
                CF.report(name, fixture())

    def test_missing_group_is_failure_but_failed_probe_is_unknown(self):
        data = fixture()
        data['render_group']['value'] = ''
        data['video_group'] = {'status': 'unknown'}
        result = CF.report('unit-c', data)
        self.assertEqual(result['checks']['render_group_present']['status'], 'fail')
        self.assertEqual(result['checks']['video_group_present']['status'], 'unknown')
        self.assertNotIn('gid', result['evidence']['render_group']['value'])

    def test_group_membership_is_never_disclosed_and_malformed_record_unknown(self):
        data = fixture()
        result = CF.report('unit-c', data)
        self.assertEqual(result['evidence']['render_group']['value'], {'present': True, 'gid': 107})
        self.assertNotIn('do-not-report', json.dumps(result))
        data['render_group']['value'] = 'video:x:44:user'
        self.assertEqual(CF.report('unit-c', data)['checks']['render_group_present']['status'], 'unknown')

    def test_missing_ensurepip_blocks_module_prerequisite(self):
        data = fixture()
        data['python_base_modules']['value'] = '{"venv":true,"ensurepip":false,"ssl":true}'
        self.assertEqual(CF.report('unit-c', data)['checks']['python_base_modules']['status'], 'fail')
        data['python_base_modules']['value'] = '{"venv":true,"ensurepip":"yes","ssl":true}'
        self.assertEqual(CF.report('unit-c', data)['checks']['python_base_modules']['status'], 'unknown')

    def test_non_usb_enumeration_does_not_qualify_i2s_csi_or_gnss(self):
        data = fixture()
        for key, value in {'i2s_drivers': ['rockchip-i2s-tdm'], 'camera_drivers': ['rkcif', 'rkisp'],
            'camera_sensor_drivers': ['imx519'], 'serial_devices': ['ttyS4'],
            'media_devices': ['media0', 'v4l-subdev0'], 'spi_devices': ['spi1.0'],
            'pps_devices': ['pps0']}.items():
            data[key]['value'] = value
        result = CF.report('unit-c', data)
        self.assertEqual(result['evidence']['camera_sensor_drivers']['value'], ['imx519'])
        self.assertEqual(result['checks']['peripheral_commissioning']['status'], 'unknown')
        self.assertFalse(result['ready'])

    def test_fixed_python_module_probe_is_isolated_and_no_bytecode(self):
        self.assertEqual(CF.COMMANDS['python_base_modules'][:4], ['/usr/bin/python3', '-I', '-B', '-c'])
        self.assertNotIn('arecord', {argv[0] for argv in CF.COMMANDS.values()})

    def test_getent_missing_group_exit_is_not_confused_with_missing_tool(self):
        from types import SimpleNamespace
        def run(argv, **kwargs):
            if argv == CF.COMMANDS['render_group']:
                return SimpleNamespace(returncode=2, stdout=b'')
            raise FileNotFoundError
        with patch('builtins.open', side_effect=PermissionError), \
             patch.object(CF.Path, 'iterdir', side_effect=PermissionError), \
             patch.object(CF.subprocess, 'run', side_effect=run):
            result = CF.collect()
        self.assertEqual(result['render_group'], {'status': 'pass', 'value': ''})
        self.assertEqual(result['video_group']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
