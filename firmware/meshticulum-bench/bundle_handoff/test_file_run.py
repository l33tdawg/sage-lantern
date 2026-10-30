"""Source-only coordinator/transport controls; never SSH, hardware, or RF proof.

Run with ``python3 -m unittest -v test_file_run`` in this directory. The only
children are local Python pipe fixtures; every file is in TemporaryDirectory.
"""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import common
import controller
import file_run
import transport


class RunFixture:
    def __init__(self, directory, order=(2, 0, 1)):
        self.base = Path(directory)
        self.campaign = self.base / 'campaign'
        self.campaign.mkdir()
        self.now = (1_000_000, 2_000_000)
        self.native = {unit: dict(mcu_boot='model-boot', firmware='model-fw',
            usb_serial=unit, boot_attempts=10, boot_reserved_estimated_airtime_us=1000,
            dropped_receive=0) for unit in common.UNITS}
        self.prepared = dict(campaign_directory=str(self.campaign), order=list(order),
            part_count=len(order), sender='unit-a', receiver='unit-b',
            comparison_mode='queued', data_burst=dict(common.DATA_BURST_POLICY),
            run_budget={unit: dict(attempts=20*len(order), estimated_airtime_us=3_000_000*len(order))
                        for unit in common.UNITS},
            by_id={unit: '/source-only/' + unit for unit in common.UNITS},
            expected_mcu=self.native, units={unit: dict(unit=unit) for unit in common.UNITS},
            staged_root='/source-only', useful_bytes=3000, core_commit='a'*40,
            candidate_wheel_sha256='b'*64,
            policy=dict(unit_limits={unit: dict(max_boot_attempts=1024,
                max_boot_estimated_airtime_us=300_000_000) for unit in common.UNITS}))
        grants = {unit: {} for unit in common.UNITS}
        for unit in common.UNITS:
            for index in order:
                grants[unit][str(index)] = {}
                for name in ('grant.json', 'secret.bin', 'active'):
                    raw = (unit + ':' + str(index) + ':' + name).encode()
                    common.write_new(self.base/'issued'/unit/str(index)/name, raw, raw=True)
                    grants[unit][str(index)][name] = common.sha(raw)
        epoch, mono = self.now
        self.scope = dict(prepared_sha256=common.sha(common.canonical(self.prepared)),
            comparison_mode='queued', data_burst=dict(common.DATA_BURST_POLICY),
            order=list(order), grants=grants, issued_epoch_us=epoch,
            expires_epoch_us=epoch+180_000_000, original_monotonic_us=mono,
            original_deadline_monotonic_us=mono+180_000_000, controller_boot='local-model-boot')
        self.pin = common.sha(common.canonical(self.scope))
        common.write_new(self.base/'SCOPE.json', self.scope)
        window = dict(self.scope)
        common.write_new(self.campaign/'CAMPAIGN_WINDOW.json', window)
        common.write_new(self.campaign/'CAMPAIGN_CLAIM_0.json', dict(scope_sha256=self.pin,
            reserved=self.prepared['run_budget'], campaign_window_sha256=common.sha(common.canonical(window))))
        self.api = SimpleNamespace(**{name: getattr(common, name) for name in (
            'read_pinned', 'sha', 'canonical', 'need', 'validate_mode_binding',
            'validate_window', 'write_new', 'paid_totals', 'part_timing',
            'validate_counter_progress', 'ACK_GRACE_US', 'UNITS')})
        self.api.BASE = self.base
        self.api.clock = lambda: self.now
        self.api.boot_id = lambda: 'local-model-boot'
        self.api.remote_source = lambda: 'source-only fixture: not executable'
        self.api.scope_rpc_seconds = controller.scope_rpc_seconds
        self.calls = []
        self.calls_lock = threading.Lock()

    def event(self, unit, part, kind):
        value = dict(scope_sha256=self.pin, unit=unit, part=part, event=kind)
        if kind == 'part_done':
            value.update(metrics=dict(part_invocation=dict(epoch_us=1, monotonic_us=10),
                authenticated_receipt=dict(epoch_us=2, monotonic_us=20)),
                public_result=dict(attempt_journal=dict(attempts=1,
                    reserved_estimated_airtime_us=100, fenced=False, recovered_unknown=0)))
        return value

    def reply(self, unit, *, complete=True, native=True):
        value = dict(unit=unit, complete=complete)
        if native:
            value['radio_after'] = {**self.native[unit], 'boot_attempts': 13,
                                    'boot_reserved_estimated_airtime_us': 1300}
        return value

    def run(self, rpc):
        def record(row, source, request, **kwargs):
            with self.calls_lock:
                self.calls.append(request['unit'])
            assert source == 'source-only fixture: not executable'
            assert list(request['grant_files']) == [str(i) for i in self.prepared['order']]
            return rpc(row, source, request, **kwargs)
        self.api.rpc = record
        return file_run.run_file(self.api, self.prepared, None, self.pin)


class Relay:
    """Both RPCs are live; one deterministic driver invokes their callbacks."""
    def __init__(self, fixture, testcase, fault=None):
        self.fixture, self.testcase, self.fault = fixture, testcase, fault
        self.condition = threading.Condition()
        self.callbacks = {}
        self.finished = False
        self.error = None
        self.seen_controls = []

    def drain(self, unit):
        values = self.callbacks[unit]['control']()
        self.seen_controls.extend((unit, value) for value in values)
        return values

    def expected(self, action, part):
        return dict(action=action, part=part, scope_sha256=self.fixture.pin)

    def __call__(self, row, source, request, **callbacks):
        unit = request['unit']
        with self.condition:
            self.callbacks[unit] = callbacks
            self.condition.notify_all()
            if not self.condition.wait_for(lambda: len(self.callbacks) == 2, timeout=2):
                raise TimeoutError('source fixture peer did not start')
        if unit == self.fixture.prepared['sender']:
            try:
                self.drive()
            except BaseException as error:
                self.error = error
                raise
            finally:
                with self.condition:
                    self.finished = True
                    self.condition.notify_all()
        else:
            with self.condition:
                if not self.condition.wait_for(lambda: self.finished, timeout=3):
                    raise TimeoutError('source fixture relay did not finish')
        return self.fixture.reply(unit, complete=self.error is None)

    def drive(self):
        f, check = self.fixture, self.testcase
        sender, receiver = f.prepared['sender'], f.prepared['receiver']
        for position, part in enumerate(f.prepared['order']):
            emit = lambda unit, kind: self.callbacks[unit]['event'](f.event(unit, part, kind))
            emit(receiver, 'ready')
            check.assertEqual(self.drain(sender), [])
            check.assertEqual(self.drain(receiver), [])
            if self.fault == 'duplicate_ready':
                emit(receiver, 'ready')
            if self.fault == 'wrong_scope':
                value = f.event(sender, part, 'ready'); value['scope_sha256'] = 'f'*64
                self.callbacks[sender]['event'](value)
            if self.fault == 'wrong_part':
                value = f.event(sender, part+10, 'ready')
                self.callbacks[sender]['event'](value)
            emit(sender, 'ready')
            check.assertEqual(self.drain(sender), [self.expected('go', part)])
            check.assertEqual(self.drain(receiver), [])
            emit(sender, 'part_done')
            check.assertEqual(self.drain(sender), [])
            check.assertEqual(self.drain(receiver), [])
            if self.fault == 'duplicate_done':
                emit(sender, 'part_done')
            if self.fault == 'expiry':
                f.now = (f.scope['expires_epoch_us'], f.scope['original_deadline_monotonic_us'])
            emit(receiver, 'part_done')
            expected = ([] if position+1 == len(f.prepared['order']) else
                        [self.expected('advance', f.prepared['order'][position+1])])
            check.assertEqual(self.drain(sender), expected)
            check.assertEqual(self.drain(receiver), expected)


class CoordinatorTests(unittest.TestCase):
    def test_out_of_order_single_rpc_and_both_endpoint_barriers(self):
        with tempfile.TemporaryDirectory() as directory:
            f = RunFixture(directory)
            relay = Relay(f, self)
            result = f.run(relay)
            if relay.error is not None:
                raise relay.error
            self.assertTrue(result['complete'])
            self.assertEqual([p['part'] for p in result['parts']], [2, 0, 1])
            self.assertEqual(Counter(f.calls), Counter({'unit-a': 1, 'unit-b': 1}))
            self.assertEqual([v['part'] for u, v in relay.seen_controls
                              if u == 'unit-a' and v['action'] == 'advance'], [0, 1])
            for unit in common.UNITS:
                self.assertEqual(result['paid_totals'][unit]['known_attempts'], 3)
                self.assertEqual(result['paid_totals'][unit]['known_reserved_estimated_airtime_us'], 300)
                self.assertEqual(result['last_observed_native'][unit]['boot_attempts'], 13)
            closed = json.loads((f.campaign/'EXECUTION_CLOSED.json').read_bytes())
            self.assertEqual((closed['complete'], closed['refunds'], closed['automatic_retry']), (True, 0, False))
            with self.assertRaises(FileExistsError):
                f.run(relay)
            self.assertEqual(len(f.calls), 2)

    def test_bad_or_duplicate_events_abort_without_advance(self):
        for fault in ('duplicate_ready', 'duplicate_done', 'wrong_scope', 'wrong_part', 'expiry'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                f = RunFixture(directory)
                relay = Relay(f, self, fault=fault)
                result = f.run(relay)
                self.assertIsInstance(relay.error, ValueError)
                self.assertFalse(result['complete'])
                self.assertTrue(result['errors'])
                self.assertFalse(any(v['action'] == 'advance' for _, v in relay.seen_controls))
                self.assertEqual(Counter(f.calls), Counter({'unit-a': 1, 'unit-b': 1}))
                self.assertFalse((f.base/'part-0-RESULT.json').exists())
                self.assertIn(2, result['paid_totals']['unit-b']['unreported_parts'])
                self.assertEqual(json.loads((f.campaign/'EXECUTION_CLOSED.json').read_bytes())['refunds'], 0)

    def test_failed_structured_reply_is_retained_and_missing_native_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            f = RunFixture(directory)
            def rpc(row, source, request, **callbacks):
                unit = request['unit']
                reply = f.reply(unit, complete=False, native=unit == 'unit-a')
                reply['failure'] = dict(reason='modeled failed part with retained paid state')
                return reply
            result = f.run(rpc)
            self.assertFalse(result['complete'])
            self.assertEqual(set(result['endpoints']), set(common.UNITS))
            self.assertEqual(result['last_observed_native']['unit-a']['boot_attempts'], 13)
            self.assertIsNone(result['last_observed_native']['unit-b'])
            self.assertEqual(result['paid_totals']['unit-a']['unreported_parts'], [2, 0, 1])
            self.assertIsNone(result['paid_totals']['unit-a']['total_attempts'])
            self.assertEqual(result['original_native']['unit-a']['boot_attempts'], 10)

    def test_scope_expired_before_rpc_does_not_consume_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            f = RunFixture(directory)
            f.now = (f.scope['expires_epoch_us'], f.scope['original_deadline_monotonic_us'])
            with self.assertRaisesRegex(ValueError, 'original_scope_expired'):
                f.run(lambda *a, **k: self.fail('expired scope launched RPC'))
            self.assertFalse(f.calls)
            self.assertFalse((f.base/'SCOPE_CONSUMED.json').exists())


class IssuanceTests(unittest.TestCase):
    def test_fresh_window_issues_once_and_preserves_role_caps_and_original_end(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); campaign = base/'campaign'; campaign.mkdir()
            common.write_new(base/'STAGED.json', {unit: dict(complete=True) for unit in common.UNITS})
            chunks = [dict(sha256=common.sha(str(i).encode())) for i in range(2)]
            common.write_new(base/'prepared/pack/manifest.json', dict(chunks=chunks))
            responses = []
            for index, entry in enumerate(chunks):
                raw = ('part-'+str(index)).encode()
                common.write_new(base/'prepared/pack/chunks'/(entry['sha256']+'.json'), raw, raw=True)
                responses.append(b'R'+raw)
            policy = dict(scope_seconds=180, max_frames=128, operation_attempt_allowance=20,
                operation_estimated_airtime_allowance_us=3_000_000,
                sender_operation_attempt_allowance=30, receiver_operation_attempt_allowance=10)
            p = dict(id='model-prepared', campaign_directory=str(campaign), run_index=0,
                campaign_id='new-source-only-campaign', historical_evidence=[dict(sha256='d'*64)],
                run_budget={'unit-a': dict(attempts=60, estimated_airtime_us=6_000_000),
                            'unit-b': dict(attempts=20, estimated_airtime_us=6_000_000)},
                policy=policy, comparison_mode='queued', data_burst=dict(common.DATA_BURST_POLICY),
                sender='unit-a', receiver='unit-b', units={u: dict(host_boot_id=u+'-boot') for u in common.UNITS},
                peers={u: dict(chain_id='source-only', agent_id=u) for u in common.UNITS},
                profile=dict(model_only=True), manifest_sha256='a'*64, object_id='b'*64,
                useful_bytes=10, useful_sha256='c'*64, core_commit='d'*40, part_count=2, order=[1,0],
                parts=[dict(response_bytes=len(raw), response_sha256=common.sha(raw)) for raw in responses])
            fake_bundle = SimpleNamespace(response_for=lambda digest, entry, raw: b'R'+raw,
                service_for=lambda entry, response, end: dict(model_service=True, expires_epoch_us=end))
            now = (5_000_000, 9_000_000)
            with patch.object(controller, 'BASE', base), patch.object(controller, 'clock', return_value=now), \
                    patch.object(controller, 'boot_id', return_value='source-controller-boot'), \
                    patch.dict(sys.modules, {'radio_bundle': fake_bundle}), \
                    patch.object(controller, 'rpc', side_effect=AssertionError('issuance must not open network')):
                issued = controller.issue(p)
                scope = json.loads((base/'SCOPE.json').read_bytes())
                window = json.loads((campaign/'CAMPAIGN_WINDOW.json').read_bytes())
                claim = json.loads((campaign/'CAMPAIGN_CLAIM_0.json').read_bytes())
                self.assertEqual(issued['expires_epoch_us'], now[0]+180_000_000)
                self.assertEqual(scope['expires_epoch_us'], window['expires_epoch_us'])
                self.assertEqual(scope['original_deadline_monotonic_us'], now[1]+180_000_000)
                self.assertEqual(claim['reserved'], p['run_budget'])
                self.assertEqual(claim['refunds'], 0)
                common.validate_window(window, *now, 'source-controller-boot')
                with self.assertRaisesRegex(ValueError, 'original_scope_expired'):
                    common.validate_window(window, *now, 'source-controller-boot', reserve_us=180_000_000)
                sessions = []
                for index in range(2):
                    plans = [json.loads((base/'issued'/u/str(index)/'grant.json').read_bytes()) for u in common.UNITS]
                    self.assertEqual(plans[0]['session_id'], plans[1]['session_id'])
                    sessions.append(plans[0]['session_id'])
                    for plan in plans:
                        self.assertEqual(plan['issued_epoch_us'], now[0])
                        self.assertEqual(plan['expires_epoch_us'], now[0]+180_000_000)
                        self.assertEqual(plan['operation_limits']['sender']['attempts'], 30)
                        self.assertEqual(plan['operation_limits']['receiver']['attempts'], 10)
                self.assertNotEqual(*sessions)
                with self.assertRaises(FileExistsError):
                    controller.issue(p)
                self.assertEqual(json.loads((campaign/'CAMPAIGN_CLAIM_0.json').read_bytes()), claim)

    def test_prepare_refuses_bad_roles_or_mode_before_source_or_target_access(self):
        for change in ({'sender': 'unit-a', 'receiver': 'unit-a'},
                       {'comparison_mode': 'unapproved'}, {'profile_host': 1}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                config = dict(schema='meshticulum.alpha3-individual-input.v1', sender='unit-a',
                    receiver='unit-b', comparison_mode='queued', profile_host=True)
                config.update(change)
                path = base/'config.json'; path.write_bytes(common.canonical(config))
                with patch.object(controller, 'sdk', side_effect=AssertionError('must validate first')):
                    with self.assertRaises(ValueError):
                        controller.prepare(path, base/'output')
                self.assertFalse((base/'output').exists())


class TransportTests(unittest.TestCase):
    row = dict(ssh=['ssh', '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes', 'source-only.invalid'],
               python='/source-only/python', sudo=False)

    def local_processes(self, script):
        """Replace Popen before rpc runs: the requested SSH argv is never executed."""
        processes = []
        real_popen = subprocess.Popen
        def local(argv, **kwargs):
            self.assertEqual(argv[0], 'ssh')
            self.assertIn('StrictHostKeyChecking=yes', argv)
            self.assertIn('ForwardAgent=no', argv)
            process = real_popen([sys.executable, '-I', '-u', '-c', script], **kwargs)
            processes.append(process)
            return process
        return processes, patch.object(transport.subprocess, 'Popen', side_effect=local)

    def test_fragmented_stdout_and_control_round_trip(self):
        script = """import json, os, sys, time
os.write(1, b'MTQ_'); time.sleep(.01); os.write(1, b'READY\\n')
request=json.loads(sys.stdin.readline())
print('MTQ_EVENT '+json.dumps({'event':'ready','part':2}), flush=True)
command=json.loads(sys.stdin.readline())
assert command=={'action':'go','part':2}
print('MTQ_RESULT '+json.dumps({'complete':True,'echo':request['value']}), flush=True)
"""
        children, patched = self.local_processes(script)
        queued = []; seen = []
        def event(value):
            seen.append(value); queued.append(dict(action='go', part=value['part']))
        def controls():
            values = queued[:]; queued.clear(); return values
        with patched:
            result = transport.rpc(self.row, 'never executed', dict(value='source fixture'),
                seconds=5, event=event, control=controls)
        self.assertEqual(result, dict(complete=True, echo='source fixture'))
        self.assertEqual(seen, [dict(event='ready', part=2)])
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].returncode, 0)
        self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)

    def test_event_rejection_terminates_and_joins_local_child(self):
        script = """import json, sys, time
print('MTQ_READY',flush=True); json.loads(sys.stdin.readline())
print('MTQ_EVENT '+json.dumps({'event':'wrong-scope'}),flush=True)
time.sleep(30)
"""
        children, patched = self.local_processes(script)
        def reject(value):
            raise ValueError('modeled event binding rejection')
        started = time.monotonic()
        with patched, self.assertRaisesRegex(ValueError, 'event binding rejection'):
            transport.rpc(self.row, 'never executed', {}, seconds=5, event=reject)
        self.assertLess(time.monotonic()-started, 3)
        self.assertIsNotNone(children[0].poll())
        self.assertNotEqual(children[0].returncode, 0)
        self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)

    def test_local_deadline_terminates_and_joins_silent_child(self):
        script = """import json, sys, time
print('MTQ_READY',flush=True); json.loads(sys.stdin.readline()); time.sleep(30)
"""
        children, patched = self.local_processes(script)
        started = time.monotonic()
        with patched, self.assertRaisesRegex(AssertionError, 'local_management_deadline'):
            transport.rpc(self.row, 'never executed', {}, seconds=.15)
        self.assertLess(time.monotonic()-started, 3)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)

    def test_eof_without_structured_result_fails(self):
        children, patched = self.local_processes("print('MTQ_READY',flush=True)")
        with patched, self.assertRaisesRegex(RuntimeError, 'no_structured_remote_result'):
            transport.rpc(self.row, 'never executed', {}, seconds=5)
        self.assertEqual(children[0].returncode, 0)
        self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)


if __name__ == '__main__':
    unittest.main()
