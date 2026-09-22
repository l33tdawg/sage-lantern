import hashlib
import os
from pathlib import Path
import stat
import threading
import unittest
from unittest.mock import Mock, patch

from sage_lantern import mesh_serial_owner as module


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.release = Mock()
        self.client = object()
        self.scheduler = module.SerialScheduler(self.client, release=self.release, capacity=2)
        self.addCleanup(self.scheduler.close)

    def test_fifo_whole_operations_same_thread(self):
        observations = []
        def job(client):
            self.assertIs(client, self.client)
            observations.append(threading.get_ident())
            return len(observations)
        self.assertEqual(self.scheduler.submit(job).result(1), 1)
        self.assertEqual(self.scheduler.submit(job).result(1), 2)
        self.assertEqual(len(set(observations)), 1)
        self.assertNotEqual(observations[0], threading.get_ident())

    def test_fence_joins_producer_and_blocks_new_admission(self):
        running, finish, acquired, leave = (threading.Event() for _ in range(4))
        self.addCleanup(finish.set)
        self.addCleanup(leave.set)
        def operation(client):
            running.set()
            finish.wait(2)
        self.scheduler.submit(operation)
        self.assertTrue(running.wait(1))
        leases = []
        def reader():
            with self.scheduler.producer_fence() as lease:
                leases.append(lease)
                self.assertTrue(lease())
                acquired.set()
                leave.wait(2)
        thread = threading.Thread(target=reader)
        thread.start()
        self.assertFalse(acquired.wait(.03))
        with self.assertRaises(module.SerialOwnerError):
            self.scheduler.submit(lambda client: None)
        finish.set()
        self.assertTrue(acquired.wait(1))
        self.assertFalse(leases[0]())
        leave.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(leases[0]())
        self.assertEqual(self.scheduler.submit(lambda client: 7).result(1), 7)

    def test_close_timeout_preserves_owner_until_real_join(self):
        running, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        def operation(client):
            running.set()
            finish.wait(2)
        future = self.scheduler.submit(operation)
        self.assertTrue(running.wait(1))
        self.assertFalse(self.scheduler.close(.01))
        self.release.assert_not_called()
        with self.assertRaises(module.SerialOwnerError):
            self.scheduler.submit(lambda client: None)
        finish.set()
        future.result(1)
        self.assertTrue(self.scheduler.close(1))
        self.release.assert_called_once()

    def test_fence_timeout_fails_closed(self):
        running, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        def operation(client):
            running.set()
            finish.wait(2)
        self.scheduler.submit(operation)
        self.assertTrue(running.wait(1))
        with self.assertRaises(module.SerialOwnerError):
            with self.scheduler.producer_fence(.01):
                self.fail('not drained')
        self.release.assert_not_called()
        with self.assertRaises(module.SerialOwnerError):
            self.scheduler.submit(lambda client: None)
        finish.set()
        self.assertTrue(self.scheduler.close(1))

    def test_a_read_fence_that_cannot_drain_refuses_without_containing(self):
        """A busy serial owner refuses this read; it must not end the unit.

        The mailbox read is the caller: a producer holding the serial past the
        fence timeout means "this listing cannot be proven consistent right
        now", not "the runtime is contained until a restart". Unit A,
        2026-09-21: a dispatch job that overran held the fence past a mailbox
        read's timeout, containment latched, and every community route answered
        503. Containment stays the drain's behaviour, pinned by the test above.
        """
        running, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)

        def operation(client):
            running.set()
            finish.wait(2)

        active = self.scheduler.submit(operation)
        self.assertTrue(running.wait(1))
        with self.assertRaisesRegex(module.SerialOwnerError, 'producer_busy'):
            with self.scheduler.producer_fence(.01, contain_on_timeout=False):
                self.fail('a busy producer must not yield a lease')
        finish.set()
        active.result(1)
        # Nothing was contained: admission and a later fence both work again.
        self.assertEqual(self.scheduler.submit(lambda client: 'ok').result(1), 'ok')
        with self.scheduler.producer_fence() as lease:
            self.assertTrue(lease())
        self.assertTrue(self.scheduler.close(1))

    def test_close_revokes_lease_and_waits_for_reader(self):
        with self.scheduler.producer_fence() as lease:
            self.assertTrue(lease())
            self.assertFalse(self.scheduler.close(.01))
            self.assertFalse(lease())
            self.release.assert_not_called()
        self.assertTrue(self.scheduler.close(1))

    def test_timed_out_fence_retains_owner_after_all_producers_finish(self):
        running, finish, released = (threading.Event() for _ in range(3))
        self.addCleanup(finish.set)
        self.release.side_effect = released.set

        def producer(client):
            running.set()
            self.assertTrue(finish.wait(2))

        active = self.scheduler.submit(producer)
        self.assertTrue(running.wait(1))
        queued = self.scheduler.submit(lambda client: 'persisted')
        with self.assertRaisesRegex(module.SerialOwnerError, 'producer_drain_timeout'):
            with self.scheduler.producer_fence(.01):
                self.fail('unverified drain must not yield a lease')

        finish.set()
        active.result(1)
        self.assertEqual(queued.result(1), 'persisted')
        with self.scheduler._condition:
            self.assertTrue(self.scheduler._condition.wait_for(
                lambda: not self.scheduler._active and not self.scheduler._queue, timeout=1))
        self.assertFalse(released.wait(.05))
        self.assertTrue(self.scheduler._thread.is_alive())
        self.release.assert_not_called()
        with self.assertRaises(module.SerialOwnerError):
            self.scheduler.submit(lambda client: None)
        with self.assertRaises(module.SerialOwnerError):
            with self.scheduler.producer_fence():
                self.fail('containment must reject a new lease')

        self.assertTrue(self.scheduler.close(1))
        self.assertTrue(released.is_set())
        self.assertTrue(self.scheduler.close(1))
        self.release.assert_called_once()

    def test_bounded_queue_and_cancel_before_start(self):
        running, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        def operation(client):
            running.set()
            finish.wait(2)
        self.scheduler.submit(operation)
        self.assertTrue(running.wait(1))
        cancelled = Mock()
        future = self.scheduler.submit(cancelled)
        self.assertTrue(future.cancel())
        self.scheduler.submit(lambda client: None)
        with self.assertRaises(module.SerialOwnerError):
            self.scheduler.submit(lambda client: None)
        finish.set()
        self.assertTrue(self.scheduler.close(1))
        cancelled.assert_not_called()

    def test_uncertainty_propagates_no_retry(self):
        error = module.SerialOwnerError('send_unconfirmed')
        operation = Mock(side_effect=error)
        with self.assertRaises(module.SerialOwnerError) as caught:
            self.scheduler.submit(operation).result(1)
        self.assertIs(caught.exception, error)
        operation.assert_called_once()

    def test_reentrant_and_release_failure_not_drained(self):
        with self.assertRaises(module.SerialOwnerError):
            self.scheduler.submit(lambda client: self.scheduler.submit(lambda child: None)).result(1)
        self.release.side_effect = OSError('close failed')
        self.assertFalse(self.scheduler.close(1))

    def test_real_client_serial_commands_and_read_only_resync(self):
        from sage_lantern.meshcore_transport import FIRMWARE, MeshCoreClient, MeshCoreError
        public = bytes(range(32))
        info, identity = bytearray(82), bytearray(58)
        info[:2] = bytes((13, 13))
        info[60:60 + len(FIRMWARE)] = FIRMWARE.encode()
        identity[0], identity[4:36] = 5, public
        def frame(payload):
            return b'>' + len(payload).to_bytes(2, 'little') + payload
        incoming = bytearray(frame(info) + frame(identity) + frame(b'\x0a'))
        stream = Mock()
        def read(size, timeout):
            result = bytes(incoming[:size])
            del incoming[:size]
            return result
        stream.read.side_effect = read
        stream.write.side_effect = lambda data, timeout: len(data)
        client = MeshCoreClient(stream, expected_public_key=public)
        owner = module.SerialScheduler(client, release=lambda: None)
        self.addCleanup(owner.close)
        self.assertEqual(owner.submit(lambda device: device.handshake()).result(1).public_key, public)
        self.assertIsNone(owner.submit(lambda device: device.receive_next(authorize=lambda: True, persist=lambda message: True)).result(1))
        with self.assertRaises(MeshCoreError):
            owner.submit(lambda device: device.get_contacts(authorize=lambda: True)).result(1)
        writes = stream.write.call_count
        with self.assertRaises(MeshCoreError) as caught:
            owner.submit(lambda device: device.get_contacts(authorize=lambda: True)).result(1)
        # A read-only stall must not latch the radio: the contact list is
        # refreshed before every operation, so poisoning here is what took
        # messaging down for hours on unit A. The failure stays per-call and the
        # next command is still allowed to reach the wire.
        self.assertEqual(caught.exception.code, 'serial_unavailable')
        self.assertGreater(stream.write.call_count, writes)


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.public = bytes.fromhex('12' * 32)
        self.fingerprint = 'sha256:' + hashlib.sha256(self.public).hexdigest()
        self.document = dict(schema='sage.lantern.meshcore-radio-public.v1', unit_id='unit-a', usb=dict(stable_path='/dev/serial/by-id/usb-test-123', serial='123', vendor_id='1234', product_id='5678'), radio_identity=dict(public_key=self.public.hex(), public_key_sha256=self.fingerprint))

    def binding(self):
        return module.RadioBinding.from_inventory(self.document, unit_id='unit-a', expected_fingerprint=self.fingerprint)

    def test_binding(self):
        self.assertEqual(self.binding().public_key, self.public)
        self.document['unit_id'] = 'unit-b'
        with self.assertRaises(module.SerialOwnerError):
            self.binding()

    def test_unstable_or_wrong_fingerprint_rejected(self):
        self.document['usb']['stable_path'] = '/dev/ttyACM0'
        with self.assertRaises(module.SerialOwnerError):
            self.binding()
        self.document['usb']['stable_path'] = '/dev/serial/by-id/usb-test-123'
        self.document['radio_identity']['public_key_sha256'] = 'sha256:' + '0' * 64
        with self.assertRaises(module.SerialOwnerError):
            self.binding()

    def test_unsupported_platform_consumes_fd_no_device_open(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        with patch.object(module.sys, 'platform', 'darwin'), patch.object(module.os, 'open') as opener:
            with self.assertRaises(module.SerialOwnerError):
                module.adopt_serial_fd(read_fd, binding=self.binding(), lock_directory='/unused')
            opener.assert_not_called()
        with self.assertRaises(OSError):
            os.fstat(read_fd)

    def test_factory_configures_and_handshakes_no_modem_control(self):
        import fcntl
        import termios
        directory_info = Mock(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
        lock_info = Mock(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1)
        settings = [1, 1, 1, 1, 1, 1, [0] * 32]
        with patch.object(module.sys, 'platform', 'linux'), patch.object(module.os, 'geteuid', return_value=0), patch.object(module, '_verify_device', return_value=123), patch.object(Path, 'is_symlink', return_value=False), patch.object(Path, 'stat', return_value=directory_info), patch.object(module.os, 'fstat', return_value=lock_info), patch.object(module.os, 'open', return_value=42) as opener, patch.object(module.os, 'close') as close, patch.object(module.os, 'set_blocking'), patch.object(module.os, 'set_inheritable'), patch.object(fcntl, 'flock') as flock, patch.object(fcntl, 'ioctl') as ioctl, patch.object(termios, 'tcgetattr', return_value=settings), patch.object(termios, 'tcsetattr') as configure, patch.object(module, 'SerialFDStream') as stream, patch.object(module, 'MeshCoreClient') as client:
            scheduler = module.adopt_serial_fd(41, binding=self.binding(), lock_directory='/run/mesh-owner')
            self.assertTrue(scheduler.close(1))
            self.assertEqual(opener.call_count, 1)
            self.assertEqual(opener.call_args.args[0], Path('/run/mesh-owner/mesh-123.lock'))
            self.assertEqual(flock.call_count, 2)
            ioctl.assert_called_once_with(41, termios.TIOCEXCL)
            self.assertEqual(configure.call_args.args[1], termios.TCSANOW)
            self.assertEqual(settings[4:6], [termios.B115200] * 2)
            self.assertEqual(settings[2] & termios.HUPCL, 0)
            stream.assert_called_once_with(41)
            client.return_value.handshake.assert_called_once()
            self.assertEqual(close.call_count, 2)


if __name__ == '__main__':
    unittest.main()
