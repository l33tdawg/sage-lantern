"""Source-only child/control tests. All SDK sessions and hardware are modeled."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('sdk_batch_entry_test_subject', HERE / 'sdk_batch_entry.py')
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
PIN = 'ab' * 32


def request(order=(4, 0), *, now=None):
    now = sdk.clock() if now is None else now
    issued, end = now['epoch_us'] - 1000, now['epoch_us'] + 10_000_000
    return dict(parts=[['transfer', '--part', str(i), '--unit', 'A',
                       '--file-issued-epoch-us', str(issued),
                       '--file-expires-epoch-us', str(end)] for i in reversed(order)],
                order=list(order), scope_sha256=PIN, issued_epoch_us=issued,
                expires_epoch_us=end, deadline_monotonic_us=now['monotonic_us'] + 9_000_000)


def control(action, part, **changes):
    return dict(action=action, part=part, scope_sha256=PIN, **changes)


class FakeBundle:
    """Exercise the actual adapter and pinned observer with no SDK/device I/O."""
    def __init__(self, *, sender=True, close_error=None, session_close_error=None, log_path=None):
        self.base = SimpleNamespace(run_object=self.run_object)
        self.sender, self.close_error = sender, close_error
        self.session_close_error, self.log_path = session_close_error, log_path
        self.events, self.sessions = [], []
        self.hardware = object()

    def log(self, event):
        self.events.append(event)
        if self.log_path:
            with open(self.log_path, 'a') as stream:
                stream.write(json.dumps(event) + '\n')

    @staticmethod
    def parser():
        p = argparse.ArgumentParser()
        p.add_argument('command')
        p.add_argument('--part', type=int, required=True)
        p.add_argument('--unit', required=True)
        p.add_argument('--file-issued-epoch-us', type=int, required=True)
        p.add_argument('--file-expires-epoch-us', type=int, required=True)
        p.set_defaults(interface='hat')
        return p

    def transfer_batch(self, parts):
        self.log(['owner_open'])
        try:
            for args in parts:
                yield dict(part=args.part, result=self.transfer(args, hardware=self.hardware))
        finally:
            self.log(['owner_close'])
            if self.close_error:
                raise self.close_error

    def transfer(self, args, *, hardware=None):
        assert hardware is self.hardware
        self.log(['validate', args.part])
        return self.base.run_object(args, None, None, None, b'data', None,
                                    self.factory, hardware=hardware)

    def factory(self, authority, worker, journal, cipher):
        owner = self
        part = worker.part

        class Flow:
            complete = False

            def absorb(self):
                self.complete = True

        class Endpoint:
            sends = owner.sender
            flow = Flow()
            events = []
            worker = SimpleNamespace(physical_snapshot=lambda: {'pending': 0})

            def step(self):
                return True

            def snapshot(self):
                return {'physical_queue': {'pending': 0, 'worker_pending': 0}}

        class Session:
            endpoint = Endpoint()
            completed_at = None

            def send(self, payload):
                owner.log(['send', part])

            def step(self):
                owner.log(['step', part])
                self.endpoint.flow.absorb()
                self.endpoint.events = [dict(event='tx', outer_kind='DATA',
                    worker_call_elapsed_us=1, mono_us=sdk.clock()['monotonic_us'], outcome='RF_DONE')]
                self.endpoint.step()
                if not owner.sender:
                    self.completed_at = 1
                return True

            def close(self):
                owner.log(['session_close', part])
                if owner.session_close_error:
                    raise owner.session_close_error

        session = Session()
        self.sessions.append(session)
        return session

    def run_object(self, args, raw, admission, version, payload, window, factory, *, hardware=None):
        assert hardware is self.hardware
        self.log(['arm', args.part])
        session = None
        try:
            session = factory(SimpleNamespace(sends=self.sender), SimpleNamespace(part=args.part), None, None)
            if self.sender:
                session.send(payload)
            session.step()
            return dict(sender_peer_receipt=self.sender, receiver_chunk_verified=not self.sender,
                        whole_file_verified=False)
        finally:
            if session is not None:
                session.close()
            self.log(['disarm', args.part])


class Controls:
    def __init__(self, values, model=None):
        self.values, self.model, self.seen = list(values), model, []

    def control(self, request, action, part, guard):
        guard()
        if self.model and action == 'go':
            assert ['send', part] not in self.model.events
            assert ['step', part] not in self.model.events
        self.seen.append((action, part))
        if not self.values:
            raise EOFError('private-input-must-not-appear')
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        sdk.need(value == (action, part))


def run(model, values, *, req=None, emit=None):
    req = request() if req is None else req
    messages = []
    collector = emit or (lambda prefix, value: messages.append((prefix, value)))
    report = sdk.run_batch(model, model.base, sdk.load_observer(HERE), req, 'A',
                          Controls(values, model), collector, lambda: None)
    return report, messages


def test_sender_arbitrary_order_fresh_sessions_cleanup_and_observation():
    model = FakeBundle()
    report, messages = run(model, [('go', 4), ('advance', 0), ('go', 0)])
    assert report['complete'] and report['parts'] == [dict(part=4, complete=True), dict(part=0, complete=True)]
    assert model.events == [['owner_open'], ['validate', 4], ['arm', 4], ['send', 4], ['step', 4],
        ['session_close', 4], ['disarm', 4], ['validate', 0], ['arm', 0], ['send', 0], ['step', 0],
        ['session_close', 0], ['disarm', 0], ['owner_close']]
    assert model.sessions[0] is not model.sessions[1]
    assert [(v['event'], v['part']) for _, v in messages] == [('ready', 4), ('part_done', 4), ('ready', 0), ('part_done', 0)]
    for _, message in messages:
        if message['event'] == 'part_done':
            metrics = message['metrics']
            assert metrics['part_invocation']['monotonic_us'] <= metrics['worker_ready']['monotonic_us']
            assert metrics['authenticated_receipt']['monotonic_us'] <= metrics['endpoint_complete']['monotonic_us']
            assert 'first_data' in metrics and metrics['completed']


def test_receiver_does_not_wait_for_go():
    model = FakeBundle(sender=False)
    report, messages = run(model, [('advance', 0)])
    assert report['complete'] and not any(e[0] == 'send' for e in model.events)
    assert all('receiver_verified' in value['metrics'] for _, value in messages if value['event'] == 'part_done')


@pytest.mark.parametrize('failure', [EOFError('secret'), KeyboardInterrupt(), SystemExit(143)])
def test_go_failure_closes_constructed_session_and_owner_without_send(failure):
    model = FakeBundle()
    with pytest.raises(type(failure)) as caught:
        run(model, [failure])
    assert caught.value is failure
    assert model.events == [['owner_open'], ['validate', 4], ['arm', 4], ['session_close', 4], ['disarm', 4], ['owner_close']]
    assert model.base.run_object == model.run_object  # hooks restored on cancellation


def test_go_failure_and_session_cleanup_failure_are_both_preserved():
    original, cleanup = KeyboardInterrupt(), RuntimeError('secret')
    model = FakeBundle(session_close_error=cleanup)
    with pytest.raises(BaseExceptionGroup) as caught:
        run(model, [original])
    assert caught.value.exceptions == (original, cleanup)
    assert model.events[-1] == ['owner_close']


def test_failed_part_observations_remain_in_failure_report():
    model, report = FakeBundle(), {}
    with pytest.raises(EOFError):
        sdk.run_batch(model, model.base, sdk.load_observer(HERE), request(), 'A',
                      Controls([], model), lambda *args: None, lambda: None, report=report)
    assert not report['complete']
    metrics = report['last_part_metrics']
    assert metrics['part'] == 4 and not metrics['completed']
    assert 'worker_ready' in metrics and 'flow_snapshot' in metrics
    assert 'entry_finished' in metrics


def test_advance_failure_does_not_start_next_part():
    model = FakeBundle()
    with pytest.raises(EOFError):
        run(model, [('go', 4)])
    assert ['validate', 0] not in model.events and model.events[-1] == ['owner_close']


@pytest.mark.parametrize('original', [EOFError('private'), KeyboardInterrupt(), SystemExit(143)])
def test_advance_error_and_owner_close_failure_are_both_preserved(original):
    cleanup = RuntimeError('private-close')
    model = FakeBundle(close_error=cleanup)
    with pytest.raises(BaseExceptionGroup) as caught:
        run(model, [('go', 4), original])
    assert caught.value.exceptions == (original, cleanup)
    assert ['validate', 0] not in model.events
    assert model.events[-1] == ['owner_close']


def test_final_hardware_close_failure_cannot_report_success():
    model = FakeBundle(close_error=RuntimeError('secret'))
    messages = []
    with pytest.raises(RuntimeError):
        run(model, [('go', 4), ('advance', 0), ('go', 0)], emit=lambda *v: messages.append(v))
    assert [v['event'] for _, v in messages] == ['ready', 'part_done', 'ready', 'part_done']
    assert model.events[-1] == ['owner_close']


@pytest.mark.parametrize('change', [
    lambda r: r.update(order=[4, 4]), lambda r: r.update(order=[True, 0]),
    lambda r: r.update(parts=[]), lambda r: r.update(parts=[['x']] * 43),
    lambda r: r.update(scope_sha256='private'), lambda r: r.update(deadline_monotonic_us=True),
    lambda r: r.update(extra='private'), lambda r: r.update(parts=[['x' * 4097], ['x']]),
    lambda r: r.update(expires_epoch_us=r['issued_epoch_us'] + sdk.MAX_WINDOW_US + 1),
])
def test_request_bounds(change):
    value = request()
    change(value)
    with pytest.raises(sdk.ProtocolError):
        sdk.validate_request(value)


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'[' * 2000])
def test_untrusted_json(raw):
    with pytest.raises(sdk.ProtocolError):
        sdk.strict_json(raw)


@pytest.mark.parametrize('value', [dict(action='advance', part=4, scope_sha256=PIN),
    dict(action='go', part=0, scope_sha256=PIN), dict(action='go', part=True, scope_sha256=PIN),
    dict(action='go', part=4, scope_sha256='cd' * 32),
    dict(action='go', part=4, scope_sha256=PIN, private='extra')])
def test_pipe_rejects_wrong_control(value):
    read, write = os.pipe()
    try:
        os.write(write, json.dumps(value).encode() + b'\n')
        with pytest.raises(sdk.ProtocolError):
            sdk.LineReader(read).control(request(), 'go', 4, lambda: None)
    finally:
        os.close(read); os.close(write)


def test_pipe_framing_bounds_and_eof():
    read, write = os.pipe()
    try:
        os.write(write, b'{}\n{}\n')
        reader = sdk.LineReader(read)
        assert reader.read(32, lambda: None) == reader.read(32, lambda: None) == {}
        os.close(write); write = None
        with pytest.raises(sdk.ProtocolError):
            reader.read(32, lambda: None)
    finally:
        os.close(read)
        if write is not None:
            os.close(write)
    read, write = os.pipe()
    try:
        os.write(write, b'x' * 33)
        with pytest.raises(sdk.ProtocolError):
            sdk.LineReader(read).read(32, lambda: None)
    finally:
        os.close(read); os.close(write)


@pytest.mark.parametrize('kind', ['wall_expired', 'mono_expired', 'parent_changed', 'wall_back', 'mono_back'])
def test_original_authority_guards(kind):
    at = dict(epoch_us=100_000, monotonic_us=100_000)
    req = request(now=at)
    guard = sdk.Guard(req, 123, now=lambda: at.copy(), parent_reader=lambda: 124 if kind == 'parent_changed' else 123)
    if kind == 'parent_changed':
        with pytest.raises(sdk.ProtocolError): guard()
        return
    guard()
    if kind == 'wall_expired': at['epoch_us'] = req['expires_epoch_us']
    if kind == 'mono_expired': at['monotonic_us'] = req['deadline_monotonic_us']
    if kind == 'wall_back': at['epoch_us'] -= 1
    if kind == 'mono_back': at['monotonic_us'] -= 1
    with pytest.raises(sdk.ProtocolError): guard()


def test_pipe_wait_rechecks_guard_without_input():
    read, write = os.pipe()
    calls = []
    def guard():
        calls.append(1)
        if len(calls) == 2:
            raise TimeoutError()
    try:
        with pytest.raises(TimeoutError): sdk.LineReader(read).read(32, guard)
        assert len(calls) == 2
    finally:
        os.close(read); os.close(write)


def test_argparse_rejects_mismatch_without_echoing_arguments(capsys):
    req = request()
    req['parts'][0] += ['--private-secret-text']
    with pytest.raises(sdk.ProtocolError): sdk.parse_parts(FakeBundle(), req, 'A')
    assert capsys.readouterr() == ('', '')


def test_pinned_observer_rejects_changed_source(tmp_path):
    (tmp_path / 'sdk_entry.py').write_text('raise RuntimeError("must not execute")')
    with pytest.raises(sdk.ProtocolError): sdk.load_observer(tmp_path)


def test_subprocess_main_barriers_and_final_close(tmp_path):
    """Real pipes and main; only Linux prctl and SDK hardware are modeled."""
    log = tmp_path / 'lifecycle.jsonl'
    verify = tmp_path / 'verify-count'
    (tmp_path / 'sdk_entry.py').write_bytes((HERE / 'sdk_entry.py').read_bytes())
    (tmp_path / 'verify_environment.py').write_text(
        'def verify(root,unit):\n    (root/"verify-count").write_text("1")\n    return {"verified":True}\n')
    driver = '''import importlib.util,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("batch_tests",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
sdk=m.sdk; model=m.FakeBundle(log_path=sys.argv[3]);sys.modules['radio_bundle']=model;sys.modules['radio_file']=model.base
sdk.contain=lambda parent:sdk.need(os.getppid()==parent)
sys.exit(sdk.main([str(os.getppid()),sys.argv[2],'A']))
'''
    child = subprocess.Popen([sys.executable, '-u', '-c', driver, str(Path(__file__)), str(tmp_path), str(log)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    reader = sdk.LineReader(child.stdout.fileno())
    def receive():
        # Production output has a prefix; read one bounded raw line here.
        raw = bytearray()
        while b'\n' not in raw:
            assert select.select([child.stdout], [], [], 3)[0], 'child event timeout'
            raw.extend(os.read(child.stdout.fileno(), 1))
        prefix, value = bytes(raw).split(b' ', 1)
        return prefix.decode(), json.loads(value)
    def send(value):
        child.stdin.write(json.dumps(value).encode() + b'\n')
    try:
        send(request())
        prefix, first = receive()
        assert prefix == 'MTQ_EVENT' and (first['event'], first['part']) == ('ready', 4)
        assert not select.select([child.stdout], [], [], .08)[0]
        assert ['send', 4] not in [json.loads(v) for v in log.read_text().splitlines()]
        send(control('go', 4))
        assert receive()[1]['event'] == 'part_done'
        assert not select.select([child.stdout], [], [], .08)[0]
        assert ['validate', 0] not in [json.loads(v) for v in log.read_text().splitlines()]
        send(control('advance', 0))
        assert receive()[1]['event'] == 'ready'
        send(control('go', 0))
        assert receive()[1]['event'] == 'part_done'
        prefix, final = receive()
        assert prefix == 'MTQ_RESULT' and final['complete']
        assert json.loads(log.read_text().splitlines()[-1]) == ['owner_close']
        assert verify.read_text() == '1'
        assert child.wait(timeout=3) == 0
        assert child.stderr.read() == b''
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=3)
        child.stdin.close(); child.stdout.close(); child.stderr.close()


def test_error_metadata_contains_types_not_private_messages():
    error = BaseExceptionGroup('private-scope-secret', [ValueError('/private/root/token'), KeyboardInterrupt()])
    raw = json.dumps(sdk.error_types(error))
    assert 'ValueError' in raw and 'KeyboardInterrupt' in raw
    assert 'private' not in raw and 'token' not in raw


def test_only_exact_wire_errors_retain_bounded_codes():
    from meshticulum.bench.wire import WireError
    class Derived(WireError):
        pass
    assert sdk.error_types(WireError('attempt_budget_exhausted'))['code'] == 'attempt_budget_exhausted'
    for error in (WireError('Private text'), WireError('/private/path'), WireError('x' * 101),
                  WireError(''), WireError('x', 'y'), Derived('safe_code'), ValueError('safe_code')):
        assert 'code' not in sdk.error_types(error)
    error = ExceptionGroup('private', [ExceptionGroup('private', [ValueError('secret')] * 16)] * 16)
    result = sdk.error_types(error)
    def nodes(value):
        return 1 + sum(nodes(child) for child in value.get('errors', []))
    assert nodes(result) <= 64 and 'private' not in json.dumps(result)


def failure_report():
    return dict(complete=False, argv=['private-argv'], runtime_environment={'prefix': '/private/path'},
        process_started={'epoch_us': 3, 'monotonic_us': 4, 'private': 'secret'},
        parts=[dict(part=4, complete=True, payload='private-body')],
        last_part_metrics=dict(part=4, completed=False, physical_diagnostics=[{'packet': 'private-packet'}],
            worker_ready={'epoch_us': 5, 'monotonic_us': 6},
            flow_snapshot=dict(received_bytes=16, retransmits=5, fast_retry_offers=2, fast_retransmits=1,
                counts={'tx_RF_DONE': 34, 'private_key': 123}, events=[{'secret': 'private-event'}],
                attempt_journal=dict(attempts=34, reserved_estimated_airtime_us=1329536,
                                     fenced=False, recovered_unknown=0, path='/private/journal'),
                physical_queue=dict(pending=0, worker_pending=0, events=['private-request']))))


def test_failure_file_is_durable_bounded_metadata_only(tmp_path, monkeypatch):
    calls = []
    fsync = sdk.os.fsync
    monkeypatch.setattr(sdk.os, 'fsync', lambda fd: (calls.append(os.fstat(fd).st_mode), fsync(fd))[1])
    root = sdk.DiagnosticRoot(tmp_path)
    try:
        assert sdk.retain_failure_diagnostics(root, failure_report(), request(), 'A', ValueError('private-error'))
    finally:
        root.close()
    path = tmp_path / sdk.DIAGNOSTICS_NAME
    raw = path.read_bytes()
    value = json.loads(raw)
    assert len(raw) <= sdk.MAX_DIAGNOSTICS_BYTES and b'private' not in raw
    assert value['scope_sha256'] == PIN and value['unit'] == 'A' and value['complete'] is False
    assert value['last_part_metrics']['flow_snapshot']['attempt_journal']['attempts'] == 34
    assert value['last_part_metrics']['flow_snapshot']['fast_retry_offers'] == 2
    assert value['last_part_metrics']['flow_snapshot']['fast_retransmits'] == 1
    assert len(calls) == 2 and stat.S_ISREG(calls[0]) and stat.S_ISDIR(calls[1])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_nlink == 1


def test_diagnostic_projection_retains_all_fixed_host_phases():
    from meshticulum.bench.host_timing import HostPhaseTimings
    report = failure_report()
    phases = HostPhaseTimings().snapshot()
    report['last_part_metrics']['host_phase_timings'] = phases
    result = sdk.diagnostic_report(report, request(), 'A', EOFError())
    assert result['last_part_metrics']['host_phase_timings'] == phases
    assert len(phases['phases']) == 26


def test_cipher_phase_projection_keeps_historical_absence_and_drops_unknown_data():
    from meshticulum.bench.host_timing import HostPhaseTimings
    current = HostPhaseTimings().snapshot()
    assert {'cipher_seal', 'cipher_open'} <= current['phases'].keys()
    historical = {**current, 'phases': {name: row for name, row in current['phases'].items()
        if name not in ('cipher_seal', 'cipher_open')}}
    assert len(historical['phases']) == 24
    assert sdk._phase_report(historical) == historical
    changed = {**current, 'phases': {**current['phases'], 'private_payload': {'value': 'secret'}}}
    changed['phases']['cipher_seal'] = {**changed['phases']['cipher_seal'],
        'count': 3, 'total_ns': 90, 'private_key': 'secret'}
    result = sdk._phase_report(changed)
    assert result['phases']['cipher_seal']['count'] == 3
    assert result['phases']['cipher_seal']['total_ns'] == 90
    assert 'secret' not in json.dumps(result) and 'private_payload' not in result['phases']


def test_failure_projection_retains_ack_effects_and_only_bounded_metadata():
    report = failure_report()
    flow = report['last_part_metrics']['flow_snapshot']
    flow['counts'].update(rx_ACK_startup=1, rx_ACK_progress=2, rx_ACK_fresh_credit=3,
        rx_ACK_unchanged=4, rx_ACK_ignored=5, tx_ACK_RF_DONE_startup=1,
        tx_ACK_RF_DONE_new=2, tx_ACK_RF_DONE_unchanged=3,
        final_ACK_reannouncement_requested=1, final_ACK_reannouncement_satisfied=1,
        final_ACK_reannouncement_budget_unavailable=1)
    flow['events'] = [dict(event='rx', outer_kind='ACK', mono_us=i, ack_effect='unchanged',
        acked_frames_delta=0, ack_seq=1, ack_sack=0, ack_credit=4, ack_revision=2,
        packet_sha256='private-packet', request='private-request', payload='private-data')
        for i in range(70)]
    flow['events'] += [dict(event='tx', outer_kind='ACK', ack_snapshot='new',
        ack_wait_reason='complete', outcome='RF_DONE', mono_us=71, ack_burst_received=1),
        dict(event='rx', outer_kind='DATA', payload='private-data'),
        dict(event='rx', outer_kind='ACK', ack_effect='private-reason')]
    value = sdk.diagnostic_report(report, request(), 'A', EOFError())
    actual = value['last_part_metrics']['flow_snapshot']
    assert actual['counts'] == {key: item for key, item in flow['counts'].items() if key != 'private_key'}
    assert actual['counts']['final_ACK_reannouncement_budget_unavailable'] == 1
    tail = actual['ack_events_retained_tail']
    assert len(tail) == 64 and tail[0]['mono_us'] == 7
    assert tail[-1] == dict(event='tx', outer_kind='ACK', ack_snapshot='new',
        ack_wait_reason='complete', outcome='RF_DONE', mono_us=71, ack_burst_received=1)
    assert 'private' not in json.dumps(value)


def test_ack_projection_does_not_invent_missing_or_invalid_fields():
    assert sdk._ack_events(None) == []
    assert sdk._ack_events([dict(event='rx', outer_kind='ACK')]) == []
    rows = sdk._ack_events([dict(event='rx', outer_kind='ACK', ack_effect='progress',
        acked_frames_delta=True, ack_seq=-1, ack_sack=2**63, ack_credit=float('nan'),
        ack_revision='private', mono_us=10)])
    assert rows == [dict(event='rx', outer_kind='ACK', ack_effect='progress', mono_us=10)]
    assert sdk._ack_events([dict(event='rx', outer_kind='ACK', ack_effect='unchanged')] + [{}] * 512) == []


def test_diagnostic_partial_writes_finish_within_same_file(tmp_path, monkeypatch):
    write = sdk.os.write
    calls = []
    def partial(fd, raw):
        calls.append(len(raw))
        return write(fd, raw[:37])
    monkeypatch.setattr(sdk.os, 'write', partial)
    root = sdk.DiagnosticRoot(tmp_path)
    try:
        assert sdk.retain_failure_diagnostics(root, failure_report(), request(), 'A', EOFError())
    finally:
        root.close()
    assert len(calls) > 1 and json.loads((tmp_path / sdk.DIAGNOSTICS_NAME).read_bytes())['scope_sha256'] == PIN


def test_elapsed_diagnostic_budget_does_not_create_file(tmp_path):
    root = sdk.DiagnosticRoot(tmp_path)
    def expired(): raise TimeoutError()
    try:
        with pytest.raises(TimeoutError): sdk.publish_diagnostics(root, b'{}', expired)
    finally:
        root.close()
    assert not (tmp_path / sdk.DIAGNOSTICS_NAME).exists()


@pytest.mark.parametrize('kind', ['existing', 'symlink', 'hardlink'])
def test_diagnostics_never_replace_existing_entries(tmp_path, kind):
    target = tmp_path / sdk.DIAGNOSTICS_NAME
    other = tmp_path / 'keep'
    other.write_bytes(b'unchanged')
    if kind == 'existing': target.write_bytes(b'unchanged')
    elif kind == 'symlink': target.symlink_to(other)
    else: os.link(other, target)
    root = sdk.DiagnosticRoot(tmp_path)
    try:
        assert not sdk.retain_failure_diagnostics(root, failure_report(), request(), 'A', EOFError())
    finally:
        root.close()
    assert other.read_bytes() == target.read_bytes() == b'unchanged'


def test_private_root_rejects_alias_escape_mode_owner_and_replacement(tmp_path, monkeypatch):
    actual = tmp_path / 'actual'; actual.mkdir(mode=0o700)
    alias = tmp_path / 'alias'; alias.symlink_to(actual, target_is_directory=True)
    for path in (alias, alias / 'sub', tmp_path / '..' / tmp_path.name, Path('relative')):
        with pytest.raises((OSError, sdk.ProtocolError)): sdk.DiagnosticRoot(path)
    actual.chmod(0o755)
    with pytest.raises(sdk.ProtocolError): sdk.DiagnosticRoot(actual)
    actual.chmod(0o700)
    uid = os.geteuid()
    with monkeypatch.context() as patch:
        patch.setattr(sdk.os, 'geteuid', lambda: uid + 1)
        with pytest.raises(sdk.ProtocolError): sdk.DiagnosticRoot(actual)
    root = sdk.DiagnosticRoot(actual)
    moved = tmp_path / 'moved'; actual.rename(moved); actual.mkdir(mode=0o700)
    try:
        assert not sdk.retain_failure_diagnostics(root, failure_report(), request(), 'A', EOFError())
    finally:
        root.close()
    assert not (actual / sdk.DIAGNOSTICS_NAME).exists() and not (moved / sdk.DIAGNOSTICS_NAME).exists()


@pytest.mark.parametrize('fault', ['write', 'file_fsync', 'directory_fsync', 'cleanup', 'oversize'])
def test_publication_faults_are_best_effort(tmp_path, monkeypatch, fault):
    root = sdk.DiagnosticRoot(tmp_path)
    fsync = sdk.os.fsync
    def fail(*args, **kwargs): raise OSError('private-failure')
    def sync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if (fault == 'directory_fsync') == directory: fail()
        return fsync(fd)
    if fault in ('write', 'cleanup'):
        monkeypatch.setattr(sdk.os, 'write', fail)
    if fault in ('file_fsync', 'directory_fsync'):
        monkeypatch.setattr(sdk.os, 'fsync', sync)
    if fault == 'cleanup': monkeypatch.setattr(sdk.os, 'unlink', fail)
    if fault == 'oversize': monkeypatch.setattr(sdk, 'MAX_DIAGNOSTICS_BYTES', 1)
    try:
        assert not sdk.retain_failure_diagnostics(root, failure_report(), request(), 'A', SystemExit(143))
    finally:
        root.close()
    if fault != 'cleanup': assert not (tmp_path / sdk.DIAGNOSTICS_NAME).exists()


def test_diagnostic_timer_interrupts_blocking_fsync_and_preserves_prior_timer(tmp_path, monkeypatch):
    root = sdk.DiagnosticRoot(tmp_path)
    previous = signal.getsignal(signal.SIGALRM)
    interrupted = []
    def slow(fd):
        try: time.sleep(1)
        except sdk.ProtocolError:
            interrupted.append(True)
            raise
    monkeypatch.setattr(sdk.os, 'fsync', slow)
    try:
        assert not sdk.retain_failure_diagnostics(root, failure_report(), request(), 'A', EOFError())
        assert interrupted == [True]
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        assert signal.getsignal(signal.SIGALRM) == previous
        signal.setitimer(signal.ITIMER_REAL, 10)
        assert not sdk.retain_failure_diagnostics(root, failure_report(), request(), 'A', EOFError())
        assert 9 < signal.getitimer(signal.ITIMER_REAL)[0] <= 10
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        root.close()


@pytest.mark.parametrize('fault', ['none', 'fsync', 'path', 'cleanup', 'publisher'])
def test_main_retains_original_exit_after_cleanup_when_parent_pipe_fails(tmp_path, monkeypatch, fault):
    model = FakeBundle(close_error=SystemExit(143))
    reader = Controls([('go', 4), ('advance', 0), ('go', 0)], model)
    reader.read = lambda *args: request()
    monkeypatch.setattr(sdk.sys, 'stdin', SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(sdk, 'LineReader', lambda fd: reader)
    monkeypatch.setattr(sdk, 'contain', lambda parent: None)
    monkeypatch.setattr(sdk.runpy, 'run_path', lambda path: {'verify': lambda *args: {}})
    observer = sdk.load_observer(HERE)
    monkeypatch.setattr(sdk, 'load_observer', lambda path: observer)
    monkeypatch.setitem(sys.modules, 'radio_bundle', model)
    monkeypatch.setitem(sys.modules, 'radio_file', model.base)
    def send(prefix, value, guard):
        if prefix == 'MTQ_RESULT': raise BrokenPipeError('private-pipe')
    monkeypatch.setattr(sdk, 'PipeEmitter', lambda fd: SimpleNamespace(send=send))
    publisher = sdk.retain_failure_diagnostics
    failures = []
    def retain(*args):
        failures.append(sdk.error_types(args[-1]))
        assert model.events[-1] == ['owner_close']
        if fault == 'publisher': raise RuntimeError('private-publisher')
        return publisher(*args)
    monkeypatch.setattr(sdk, 'retain_failure_diagnostics', retain)
    def fail(*args, **kwargs): raise OSError('private-write')
    if fault in ('fsync', 'cleanup'): monkeypatch.setattr(sdk.os, 'fsync', fail)
    if fault == 'cleanup': monkeypatch.setattr(sdk.os, 'unlink', fail)
    if fault == 'path': (tmp_path / sdk.DIAGNOSTICS_NAME).symlink_to(tmp_path / 'outside')
    assert sdk.main([str(os.getppid()), str(tmp_path), 'A']) == 143, (model.events, failures)
    if fault == 'none':
        value = json.loads((tmp_path / sdk.DIAGNOSTICS_NAME).read_bytes())
        assert value['failure']['type'] == 'SystemExit' and value['scope_sha256'] == PIN
