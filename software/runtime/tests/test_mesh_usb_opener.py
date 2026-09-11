from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import stat
import unittest
from unittest.mock import Mock, patch

from sage_lantern import mesh_usb_opener as module
from sage_lantern.mesh_serial_owner import RadioBinding


class OpenerTests(unittest.TestCase):
    def setUp(self):
        self.binding = RadioBinding('unit-a', '/dev/serial/by-id/usb-fixture', '123', '1234', '5678', b'r' * 32)
        self.policy = module.USBStartupPolicy(True, True, 'quiescent_startup')
        self.lease = Mock(return_value=True)
        self.reconciled = Mock(return_value=True)
        self.in_fence = False
        self.node = module._USBNode(Path('/dev/ttyACM7'), 1, 2, 3)
        self.arguments = dict(binding=self.binding, lock_directory='/run/test-mesh', timeout=1)

    @contextmanager
    def fence(self):
        self.in_fence = True
        try:
            yield self.lease
        finally:
            self.in_fence = False

    def opener(self, **changes):
        arguments = dict(policy=self.policy, startup_fence=self.fence, journals_reconciled=self.reconciled)
        arguments.update(changes)
        return module.InitialUSBOpener(**arguments)

    @contextmanager
    def environment(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(module.sys, 'platform', 'linux'))
            stack.enter_context(patch.object(module.os, 'geteuid', return_value=0))
            stack.enter_context(patch.object(Path, 'is_symlink', return_value=False))
            stack.enter_context(patch.object(Path, 'stat', return_value=Mock(st_mode=stat.S_IFDIR | 0o700, st_uid=0)))
            inspect = stack.enter_context(patch.object(module, '_inspect', return_value=self.node))
            locks = stack.enter_context(patch.object(module, '_lock', side_effect=[51, 52]))
            opened = stack.enter_context(patch.object(module.os, 'open', return_value=53))
            closed = stack.enter_context(patch.object(module.os, 'close'))
            stack.enter_context(patch.object(module.os, 'fstat', return_value=Mock(st_mode=stat.S_IFCHR | 0o600, st_dev=1, st_ino=2, st_rdev=3)))
            adopt = stack.enter_context(patch.object(module, 'adopt_serial_fd'))
            yield inspect, locks, opened, closed, adopt

    def test_default_denied_without_open_or_platform_probe(self):
        with patch.object(module.os, 'open') as opened:
            with self.assertRaises(module.USBOpenError):
                module.InitialUSBOpener().open(**self.arguments)
            opened.assert_not_called()

    def test_explicit_literal_approval_and_phase_required(self):
        for policy in (module.USBStartupPolicy(True, False, 'provisioning'), module.USBStartupPolicy(1, True, 'provisioning'), module.USBStartupPolicy(True, True, 'running'), {'allow_initial_open': True}):
            with self.subTest(policy=policy), patch.object(module.os, 'open') as opened:
                with self.assertRaises(module.USBOpenError):
                    self.opener(policy=policy).open(**self.arguments)
                opened.assert_not_called()

    def test_platform_rejected(self):
        with patch.object(module.sys, 'platform', 'darwin'), patch.object(module.os, 'open') as opened:
            with self.assertRaises(module.USBOpenError):
                self.opener().open(**self.arguments)
            opened.assert_not_called()

    def test_unreconciled_or_unfenced_never_opens(self):
        for hook in (self.lease, self.reconciled):
            for value in (False, None, 1):
                with self.subTest(value=value), self.environment() as (_, _, opened, _, adopt):
                    hook.return_value = value
                    with self.assertRaises(module.USBOpenError):
                        self.opener().open(**self.arguments)
                    opened.assert_not_called()
                    adopt.assert_not_called()
            hook.return_value = True

    def test_success_has_correct_flags_checks_and_one_shot(self):
        with self.environment() as (inspect, locks, opened, closed, adopt):
            scheduler = Mock()
            def handoff(descriptor, **kwargs):
                self.assertTrue(self.in_fence)
                self.assertEqual(descriptor, 53)
                self.assertIs(kwargs['binding'], self.binding)
                self.assertIn(52, [call.args[0] for call in closed.call_args_list])
                return scheduler
            adopt.side_effect = handoff
            opener = self.opener()
            self.assertIs(opener.open(**self.arguments), scheduler)
            self.assertEqual(inspect.call_count, 3)
            self.assertEqual(locks.call_count, 2)
            opened.assert_called_once_with(self.node.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.assertEqual([call.args[0] for call in closed.call_args_list], [52, 51])
            scheduler.close.assert_not_called()
            self.assertFalse(self.in_fence)
            with self.assertRaises(module.USBOpenError):
                opener.open(**self.arguments)
            self.assertEqual(opened.call_count, 1)

    def test_existing_owner_blocks_before_open(self):
        with self.environment() as (_, locks, opened, closed, _):
            locks.side_effect = [51, BlockingIOError()]
            with self.assertRaises(BlockingIOError):
                self.opener().open(**self.arguments)
            opened.assert_not_called()
            closed.assert_called_once_with(51)

    def test_preopen_binding_swap(self):
        with self.environment() as (inspect, _, opened, _, adopt):
            inspect.side_effect = [self.node, module._USBNode(Path('/dev/ttyACM8'), 1, 9, 4)]
            with self.assertRaises(module.USBOpenError):
                self.opener().open(**self.arguments)
            opened.assert_not_called()
            adopt.assert_not_called()

    def test_postopen_binding_swap_closes_without_handshake(self):
        with self.environment() as (inspect, _, _, closed, adopt):
            inspect.side_effect = [self.node, self.node, module._USBNode(Path('/dev/ttyACM8'), 1, 9, 4)]
            with self.assertRaises(module.USBOpenError):
                self.opener().open(**self.arguments)
            adopt.assert_not_called()
            self.assertEqual([call.args[0] for call in closed.call_args_list], [53, 52, 51])

    def test_wrong_handshake_identity_no_retry_no_double_close(self):
        with self.environment() as (_, _, opened, closed, adopt):
            adopt.side_effect = RuntimeError('identity_mismatch')
            opener = self.opener()
            with self.assertRaises(RuntimeError):
                opener.open(**self.arguments)
            self.assertNotIn(53, [call.args[0] for call in closed.call_args_list])
            with self.assertRaises(module.USBOpenError):
                opener.open(**self.arguments)
            opened.assert_called_once()

    def test_revoked_after_handshake_closes_scheduler(self):
        with self.environment() as (_, _, _, _, adopt):
            self.reconciled.side_effect = [True, True, True, False]
            with self.assertRaises(module.USBOpenError):
                self.opener().open(**self.arguments)
            adopt.return_value.close.assert_called_once_with(timeout=1)

    def test_open_error_releases_locks_not_retried(self):
        with self.environment() as (_, _, opened, closed, adopt):
            opened.side_effect = OSError('no device')
            opener = self.opener()
            with self.assertRaises(OSError):
                opener.open(**self.arguments)
            adopt.assert_not_called()
            self.assertEqual([call.args[0] for call in closed.call_args_list], [52, 51])
            with self.assertRaises(module.USBOpenError):
                opener.open(**self.arguments)

    def test_inspection_usb_identity_wrong_fails_without_open(self):
        sysfs = Path('/sys/devices/usb1/device')
        with patch.object(Path, 'is_symlink', return_value=True), patch.object(Path, 'resolve', side_effect=[self.node.path, sysfs]), patch.object(Path, 'stat', return_value=Mock(st_mode=stat.S_IFCHR, st_dev=1, st_ino=2, st_rdev=3)), patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'read_text', side_effect=['1234', '5678', 'WRONG']), patch.object(module.os, 'open') as opened:
            with self.assertRaises(module.USBOpenError):
                module._inspect(self.binding)
            opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
