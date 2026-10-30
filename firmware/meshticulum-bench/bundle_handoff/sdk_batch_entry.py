"""Bounded whole-file public SDK child; no grant creation or deadline renewal.

The observation helper is the exact alpha.3 helper, hash-pinned below. Its
authenticated-receipt and first-DATA boundaries are unchanged. This adapter
adds process/part entry boundaries and explicit parent control barriers.
"""
import time

PROCESS_STARTED = dict(epoch_us=time.time_ns() // 1000,
                       monotonic_us=time.monotonic_ns() // 1000)

import contextlib
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import select
import signal
import stat
import sys

OBSERVER_SHA256 = '85aec01de10baa332f7cd8e4357bdc4f633bb661900b44a0876b5850eb0f4337'
MAX_PARTS = 42
MAX_REQUEST_BYTES = 1_048_576
MAX_CONTROL_BYTES = 1024
MAX_EVENT_BYTES = 1_048_576
MAX_WINDOW_US = 1_200_000_000
DIAGNOSTICS_NAME = 'SDK_BATCH_DIAGNOSTICS.json'
MAX_DIAGNOSTICS_BYTES = 1_048_576
DIAGNOSTICS_SECONDS = .1
REQUEST_KEYS = {'parts', 'order', 'scope_sha256', 'deadline_monotonic_us',
                'issued_epoch_us', 'expires_epoch_us'}


class ProtocolError(ValueError):
    """Fixed, payload-free protocol failure."""


def need(condition):
    if not condition:
        raise ProtocolError('invalid_batch_control')


def clock():
    return dict(epoch_us=time.time_ns() // 1000,
                monotonic_us=time.monotonic_ns() // 1000)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result)
            result[key] = value
        return result

    def constant(value):
        raise ProtocolError('invalid_batch_json')

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProtocolError('invalid_batch_json') from None


def validate_request(value):
    need(type(value) is dict and set(value) == REQUEST_KEYS)
    need(type(value['scope_sha256']) is str and
         re.fullmatch('[0-9a-f]{64}', value['scope_sha256']) is not None)
    for key in ('deadline_monotonic_us', 'issued_epoch_us', 'expires_epoch_us'):
        need(type(value[key]) is int and 0 < value[key] < 2**63)
    need(0 < value['expires_epoch_us'] - value['issued_epoch_us'] <= MAX_WINDOW_US)
    parts, order = value['parts'], value['order']
    need(type(parts) is list and 0 < len(parts) <= MAX_PARTS)
    need(type(order) is list and len(order) == len(parts))
    need(all(type(i) is int and 0 <= i < MAX_PARTS for i in order))
    need(len(set(order)) == len(order))
    for argv in parts:
        need(type(argv) is list and 0 < len(argv) <= 96)
        need(all(type(s) is str and 0 < len(s) <= 4096 and '\x00' not in s for s in argv))
    return value


class Guard:
    """The original supervisor deadline plus original wall-clock window."""
    def __init__(self, request, parent, *, now=clock, parent_reader=os.getppid):
        self.request, self.parent = request, parent
        self.now, self.parent_reader = now, parent_reader
        self.previous = None

    def __call__(self):
        need(self.parent_reader() == self.parent)
        at = self.now()
        r = self.request
        need(r['issued_epoch_us'] <= at['epoch_us'] < r['expires_epoch_us'])
        need(at['monotonic_us'] < r['deadline_monotonic_us'])
        # Both clocks independently expire the operation. Do not remap either
        # deadline here: wall/monotonic sampling skew is not new authority.
        need(r['deadline_monotonic_us'] - at['monotonic_us'] <= MAX_WINDOW_US)
        if self.previous is not None:
            need(at['epoch_us'] >= self.previous['epoch_us'] and
                 at['monotonic_us'] >= self.previous['monotonic_us'])
        self.previous = at
        return at


class LineReader:
    """Unbuffered pipe framing; every wait checks the same authority guard."""
    def __init__(self, fd):
        self.fd, self.buffer = fd, bytearray()

    def read(self, limit, guard):
        while True:
            guard()
            end = self.buffer.find(b'\n')
            if end >= 0:
                need(end <= limit)
                result = bytes(self.buffer[:end])
                del self.buffer[:end + 1]
                return strict_json(result)
            need(len(self.buffer) <= limit)
            if select.select([self.fd], [], [], .05)[0]:
                raw = os.read(self.fd, min(65536, limit + 1 - len(self.buffer)))
                need(bool(raw))
                self.buffer.extend(raw)

    def control(self, request, action, part, guard):
        value = self.read(MAX_CONTROL_BYTES, guard)
        need(type(value) is dict and set(value) == {'action', 'part', 'scope_sha256'})
        need(type(value['part']) is int and value['part'] == part and
             value['action'] == action and value['scope_sha256'] == request['scope_sha256'])


class PipeEmitter:
    def __init__(self, fd):
        self.fd = fd
        os.set_blocking(fd, False)

    def send(self, prefix, value, guard):
        raw = (prefix + ' ' + json.dumps(value, sort_keys=True, allow_nan=False,
                                       separators=(',', ':')) + '\n').encode()
        need(len(raw) <= MAX_EVENT_BYTES)
        pending = memoryview(raw)
        while pending:
            guard()
            if select.select([], [self.fd], [], .05)[1]:
                try:
                    count = os.write(self.fd, pending)
                except BlockingIOError:
                    continue
                need(count > 0)
                pending = pending[count:]


def parse_parts(bundle, request, unit):
    indexed = {}
    for argv in request['parts']:
        # argparse otherwise echoes user-supplied argument text on failure.
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            try:
                args = bundle.parser().parse_args(argv)
            except SystemExit:
                raise ProtocolError('invalid_part_arguments') from None
        need(args.command == 'transfer' and args.unit == unit)
        need(type(args.part) is int and args.part in request['order'] and args.part not in indexed)
        need(args.file_issued_epoch_us == request['issued_epoch_us'] and
             args.file_expires_epoch_us == request['expires_epoch_us'])
        need(args.interface != 'usb' or (args.device is not None and bool(args.usb_serial)))
        indexed[args.part] = args
    need(set(indexed) == set(request['order']))
    return [indexed[index] for index in request['order']]


def load_observer(root):
    raw = (root / 'sdk_entry.py').read_bytes()
    need(len(raw) <= 32768 and hashlib.sha256(raw).hexdigest() == OBSERVER_SHA256)
    namespace = {'__name__': 'pinned_alpha3_sdk_observer', '__file__': str(root / 'sdk_entry.py')}
    exec(compile(raw, str(root / 'sdk_entry.py'), 'exec'), namespace)
    return namespace['observe_session']


def error_types(error, depth=0, remaining=None):
    remaining = [64] if remaining is None else remaining
    remaining[0] -= 1
    name = type(error).__name__
    result = {'type': name if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,79}', name) else 'Exception'}
    module = sys.modules.get('meshticulum.bench.wire')
    if module is not None and type(error) is getattr(module, 'WireError', None):
        args = error.args
        if (type(args) is tuple and len(args) == 1 and type(args[0]) is str
                and re.fullmatch('[a-z0-9_]{1,100}', args[0])):
            result['code'] = args[0]
    if isinstance(error, BaseExceptionGroup) and depth < 4:
        result['errors'] = []
        for child in error.exceptions[:16]:
            if remaining[0] <= 0:
                break
            result['errors'].append(error_types(child, depth + 1, remaining))
        omitted = len(error.exceptions) - len(result['errors'])
        if omitted:
            result['omitted'] = omitted
    elif isinstance(error, BaseExceptionGroup):
        result['omitted'] = len(error.exceptions)
    return result


def _numbers(value, names):
    """Only explicit metadata fields; no arbitrary strings, keys or event bodies."""
    if type(value) is not dict:
        return {}
    return {name: value[name] for name in names if name in value and
            (type(value[name]) is bool or type(value[name]) is int and 0 <= value[name] < 2**63
             or value[name] is None)}


def _phase_report(value):
    if type(value) is not dict:
        return {}
    names = ('queue_call queue_pump queue_clock queue_reserve queue_serial_write '
             'queue_admission_wait queue_terminal_settle release_request release_roundtrip '
             'release_commit journal_transaction serial_write serial_wait_writable serial_os_write '
             'serial_pacing_sleep serial_read serial_read_wait serial_os_read flow_step '
             'flow_accounting flow_receive flow_dispatch flow_pump host_loop_sleep '
             'cipher_seal cipher_open').split()
    rows = value.get('phases')
    if (value.get('schema') != 'meshticulum.host-phase-timing.v1'
            or value.get('clock') != 'time.perf_counter_ns' or value.get('inclusive') is not True
            or type(rows) is not dict):
        return {}
    return dict(schema='meshticulum.host-phase-timing.v1', clock='time.perf_counter_ns', inclusive=True,
                phases={name: _numbers(rows[name], ('count', 'total_ns', 'min_ns', 'max_ns',
                    'invalid', 'dropped', 'saturated')) for name in names if name in rows})


def _flow_report(value):
    result = _numbers(value, ('complete', 'received_bytes', 'retransmits', 'acked_frames',
                              'duplicates', 'startup_pending', 'fast_retry_offers', 'fast_retransmits',
                              'host_phase_timings_unavailable'))
    if type(value) is not dict:
        return result
    epochs = value.get('repeat_retry_epochs')
    if type(epochs) is int and 0 <= epochs < 2**63:
        result['repeat_retry_epochs'] = epochs
    counters = ('pending_DATA_refused queue_UNKNOWN tx_QUEUED tx_RF_DONE tx_NOT_SENT '
                'tx_UNKNOWN tx_DRIVER_REFUSED rx_CONTROL rx_refused rx_DATA rx_ACK '
                'rx_ACK_startup rx_ACK_progress rx_ACK_fresh_credit rx_ACK_unchanged rx_ACK_ignored '
                'tx_ACK_RF_DONE_startup tx_ACK_RF_DONE_new tx_ACK_RF_DONE_unchanged '
                'final_ACK_reannouncement_requested final_ACK_reannouncement_satisfied '
                'final_ACK_reannouncement_budget_unavailable').split()
    result['counts'] = _numbers(value.get('counts'), counters)
    result['attempt_journal'] = _numbers(value.get('attempt_journal'),
        ('attempts', 'reserved_estimated_airtime_us', 'fenced', 'recovered_unknown'))
    result['physical_queue'] = _numbers(value.get('physical_queue'), ('pending', 'worker_pending'))
    result['host_phase_timings'] = _phase_report(value.get('host_phase_timings'))
    result['ack_events_retained_tail'] = _ack_events(value.get('events'))
    return result


def _ack_events(events):
    """At most 64 selected ACK metadata rows from the already bounded flow tail."""
    if type(events) is not list:
        return []
    result = []
    integers = ('mono_us', 'ack_seq', 'ack_sack', 'ack_credit', 'ack_revision',
                'acked_frames_delta', 'ack_burst_received')
    for event in events[-512:]:
        if (type(event) is not dict or type(event.get('outer_kind')) is not str
                or event['outer_kind'] != 'ACK'):
            continue
        kind = event.get('event')
        if type(kind) is not str:
            continue
        labels = {}
        if kind == 'rx' and type(event.get('ack_effect')) is str and event['ack_effect'] in (
                'startup', 'progress', 'fresh_credit', 'unchanged', 'ignored'):
            labels['ack_effect'] = event['ack_effect']
        elif (kind == 'tx' and type(event.get('ack_snapshot')) is str
              and event['ack_snapshot'] in ('startup', 'new', 'unchanged')):
            labels['ack_snapshot'] = event['ack_snapshot']
            if (type(event.get('ack_wait_reason')) is str and event['ack_wait_reason'] in
                    ('startup', 'full_train', 'complete', 'quiet', 'persistence')):
                labels['ack_wait_reason'] = event['ack_wait_reason']
            if (type(event.get('outcome')) is str and event['outcome'] in
                    ('RF_DONE', 'NOT_SENT', 'REFUSED', 'UNKNOWN')):
                labels['outcome'] = event['outcome']
        else:
            continue
        result.append(dict(event=kind, outer_kind='ACK', **labels, **{
            name: event[name] for name in integers if type(event.get(name)) is int
            and 0 <= event[name] < 2**63}))
    return result[-64:]


def diagnostic_report(report, request, unit, error):
    """Fixed failure-only projection, never the request, argv, runtime paths or raw trace."""
    need(type(request) is dict and type(request.get('scope_sha256')) is str
         and re.fullmatch('[0-9a-f]{64}', request['scope_sha256']) is not None)
    need(type(unit) is str and re.fullmatch('[A-Za-z0-9_-]{1,32}', unit) is not None)
    result = dict(schema='meshticulum.sdk-batch-diagnostics.v1', complete=False,
                  scope_sha256=request['scope_sha256'], unit=unit, failure=error_types(error),
                  observer_sha256=OBSERVER_SHA256, parts=[])
    for name in ('process_started', 'entry_started', 'entry_finished'):
        result[name] = _numbers(report.get(name), ('epoch_us', 'monotonic_us'))
    parts = report.get('parts')
    if type(parts) is list:
        result['parts'] = [_numbers(part, ('part', 'complete')) for part in parts[:MAX_PARTS]]
    value = report.get('last_part_metrics')
    if type(value) is dict:
        current = _numbers(value, ('part', 'completed', 'authority_or_transport_changed',
                                   'host_phase_timings_unavailable'))
        for name in ('part_invocation', 'entry_started', 'entry_finished', 'process_started',
                     'worker_ready', 'authenticated_receipt', 'frozen_flow_complete',
                     'endpoint_complete', 'flow_complete', 'receiver_verified', 'session_retired', 'cli_complete'):
            if name in value:
                current[name] = _numbers(value[name], ('epoch_us', 'monotonic_us'))
        for name in ('first_data', 'first_queued_data'):
            if name in value:
                current[name] = _numbers(value[name], ('worker_call_started_monotonic_us',
                    'worker_call_finished_monotonic_us', 'queue_submit_monotonic_us',
                    'queue_accept_monotonic_us', 'queue_accept_elapsed_us'))
        for name in ('flow_snapshot', 'flow_snapshot_before_close'):
            if name in value:
                current[name] = _flow_report(value[name])
        current['host_phase_timings'] = _phase_report(value.get('host_phase_timings'))
        result['last_part_metrics'] = current
    return result


class DiagnosticRoot:
    """Retained private directory identity; paths and symlink ancestors cannot redirect output."""
    def __init__(self, path):
        self.path = Path(path)
        need(self.path.is_absolute() and '..' not in self.path.parts)
        self.fd = self._open()
        self.identity = (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino)

    def _open(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open('/', flags)
        try:
            for component in self.path.parts[1:]:
                child = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                 and stat.S_IMODE(info.st_mode) == 0o700)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def check(self):
        fd = self._open()
        try:
            info, held = os.fstat(fd), os.fstat(self.fd)
            need((info.st_dev, info.st_ino) == self.identity == (held.st_dev, held.st_ino))
        finally:
            os.close(fd)

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)


@contextlib.contextmanager
def diagnostic_budget():
    """A separate 100 ms diagnostic timer; never modify an existing timer or RF authority."""
    need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    previous = signal.getsignal(signal.SIGALRM)
    end = time.monotonic_ns() + int(DIAGNOSTICS_SECONDS * 1_000_000_000)
    def expired(*unused):
        raise ProtocolError('batch_diagnostics_timeout')
    def check():
        need(time.monotonic_ns() < end)
    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, DIAGNOSTICS_SECONDS)
        yield check
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, previous)


def publish_diagnostics(root, raw, check):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_DIAGNOSTICS_BYTES)
    check(); root.check()
    fd = None
    identity = None
    published = False
    try:
        fd = os.open(DIAGNOSTICS_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root.fd)
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        def same_file():
            check(); root.check()
            held = os.fstat(fd)
            entry = os.stat(DIAGNOSTICS_NAME, dir_fd=root.fd, follow_symlinks=False)
            need(identity == (held.st_dev, held.st_ino) == (entry.st_dev, entry.st_ino)
                 and stat.S_ISREG(held.st_mode) and held.st_uid == os.geteuid()
                 and stat.S_IMODE(held.st_mode) == 0o600 and held.st_nlink == 1)
        same_file()
        pending = memoryview(raw)
        while pending:
            check()
            written = os.write(fd, pending[:65536])
            need(type(written) is int and 0 < written <= min(65536, len(pending)))
            pending = pending[written:]
        same_file(); os.fsync(fd); check(); os.fsync(root.fd); same_file()
        published = True
    finally:
        if fd is not None:
            with contextlib.suppress(BaseException):
                os.close(fd)
        if identity is not None and not published:
            # Remove only our own entry, and do not start more I/O after timeout.
            with contextlib.suppress(BaseException):
                check()
                entry = os.stat(DIAGNOSTICS_NAME, dir_fd=root.fd, follow_symlinks=False)
                if identity == (entry.st_dev, entry.st_ino):
                    os.unlink(DIAGNOSTICS_NAME, dir_fd=root.fd)


def retain_failure_diagnostics(root, report, request, unit, error):
    """Post-cleanup only; every diagnostic fault leaves the original failure untouched."""
    try:
        if root is None or request is None:
            return False
        with diagnostic_budget() as check:
            value = diagnostic_report(report, request, unit, error)
            raw = json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode() + b'\n'
            publish_diagnostics(root, raw, check)
        return True
    except BaseException:
        return False


@contextlib.contextmanager
def closing_iterator(iterator):
    """Retain a control/cancellation error even if closing held hardware fails."""
    try:
        yield iterator
    except BaseException as error:
        try:
            iterator.close()
        except BaseException as cleanup:
            raise BaseExceptionGroup('Batch operation and iterator cleanup failed',
                                     [error, cleanup]) from None
        raise
    else:
        iterator.close()


def run_batch(bundle, base, observer, request, unit, reader, emit, guard, *, now=clock,
              report=None):
    """Testable composition; production dependencies are imported once by main."""
    report = {} if report is None else report
    report.update(schema='meshticulum.public-sdk-batch-entry.v1', complete=False,
                  unit=unit, scope_sha256=request['scope_sha256'], parts=[],
                  process_started=PROCESS_STARTED, entry_started=now(),
                  observer_sha256=OBSERVER_SHA256,
                  timing_boundary='process start and per-part invocation are separate; authenticated receipt is before physical drain')
    guard()
    parts = parse_parts(bundle, request, unit)
    original_transfer, original_run = bundle.transfer, base.run_object
    current = None

    def observed_transfer(args, *args_tail, **kwargs):
        nonlocal current
        at = now()  # Before transfer validation, state reads, or hardware borrowing.
        current = dict(schema='meshticulum.public-sdk-part-metrics.v1', unit=unit,
                       part=args.part, part_invocation=at, entry_started=at,
                       process_started=PROCESS_STARTED, completed=False,
                       authority_or_transport_changed=False,
                       entry_boundary='in-process public transfer invocation; not process startup')
        guard()
        try:
            result = original_transfer(args, *args_tail, **kwargs)
            current['completed'] = True
            current['cli_complete'] = now()
            return result
        except BaseException as error:
            try:
                timing = getattr(error, 'host_phase_timings', None)
                if timing is not None:
                    current['host_phase_timings'] = timing
                if getattr(error, 'host_phase_timings_unavailable', False):
                    current['host_phase_timings_unavailable'] = True
            except BaseException:
                current['host_phase_timings_unavailable'] = True
            raise
        finally:
            current['entry_finished'] = now()

    def observed_run(*args, **kwargs):
        need(current is not None)
        # Public run_object's factory is positional today; also preserve the
        # keyword form and optional FileHardware forwarding without changing it.
        positional = list(args)
        if len(positional) > 6:
            factory = positional[6]
        else:
            factory = kwargs['factory']
        record = current

        def observed_factory(authority, worker, journal, cipher):
            session = factory(authority, worker, journal, cipher)
            try:
                def ready(value):
                    need(value['sdk_qual_event'] == 'ready' and value['part'] == record['part'])
                    guard()
                    emit('MTQ_EVENT', dict(event='ready', part=record['part'], unit=unit,
                         scope_sha256=request['scope_sha256'], at=value['at'],
                         part_invocation=record['part_invocation']))

                session = observer(session, record, unit=unit, part_index=record['part'],
                                   clock=now, emit=ready)
                if authority.sends:
                    reader.control(request, 'go', record['part'], guard)
                guard()
                return session
            except BaseException as error:
                # run_object registers session.close only after factory returns.
                # A failed GO wait must therefore retire this session here.
                try:
                    session.close()
                except BaseException as cleanup:
                    raise BaseExceptionGroup('Batch readiness and session cleanup failed',
                                             [error, cleanup]) from None
                raise

        if len(positional) > 6:
            positional[6] = observed_factory
        else:
            kwargs['factory'] = observed_factory
        return original_run(*positional, **kwargs)

    bundle.transfer, base.run_object = observed_transfer, observed_run
    try:
        with closing_iterator(bundle.transfer_batch(parts)) as iterator:
            for ordinal, args in enumerate(parts):
                if ordinal:
                    reader.control(request, 'advance', args.part, guard)
                guard()
                item = next(iterator)
                need(type(item) is dict and item['part'] == args.part and
                     current is not None and current['part'] == args.part and current['completed'])
                report['parts'].append(dict(part=args.part, complete=True))
                emit('MTQ_EVENT', dict(event='part_done', part=args.part, unit=unit,
                     scope_sha256=request['scope_sha256'], metrics=current, public_result=item['result']))
            # Resume the final yield: FileHardware.__exit__ must succeed before
            # the success record is emitted, not merely when all parts yielded.
            try:
                next(iterator)
            except StopIteration:
                pass
            else:
                raise ProtocolError('unexpected_extra_part')
        guard()
        report['complete'] = True
        return report
    finally:
        bundle.transfer, base.run_object = original_transfer, original_run
        report['entry_finished'] = now()
        if not report['complete'] and current is not None:
            # Preserve already-captured diagnostics on failure too. The current
            # part may have completed before an ADVANCE or owner-close failure;
            # its own completed flag states that distinction.
            report['last_part_metrics'] = current


def terminate(signum, frame):
    raise SystemExit(128 + signum)


def contain(parent):
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, terminate)
    # Linux production child only. Install before SDK imports or any device use,
    # then check the parent race after arming the parent-death signal.
    need(ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) == 0)
    need(os.getppid() == parent)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    report = dict(complete=False, process_started=PROCESS_STARTED)
    emitter = PipeEmitter(sys.stdout.fileno())
    diagnostic_root = request = diagnostic_binding = None
    unit = None
    try:
        need(len(argv) == 3 and argv[0].isdigit() and int(argv[0]) > 1)
        parent, root, unit = int(argv[0]), Path(argv[1]), argv[2]
        need(re.fullmatch('[A-Za-z0-9_-]{1,32}', unit) is not None)
        diagnostic_root = DiagnosticRoot(root)
        contain(parent)
        reader = LineReader(sys.stdin.fileno())
        startup_end = time.monotonic_ns() // 1000 + 5_000_000

        def startup_guard():
            need(os.getppid() == parent and time.monotonic_ns() // 1000 < startup_end)

        request = validate_request(reader.read(MAX_REQUEST_BYTES, startup_guard))
        diagnostic_binding = dict(scope_sha256=request['scope_sha256'])
        guard = Guard(request, parent)
        guard()
        environment = runpy.run_path(str(root / 'verify_environment.py'))['verify'](root, unit)
        observer = load_observer(root)
        sys.path.insert(0, str(root / 'examples'))
        import radio_bundle
        import radio_file
        need(radio_bundle.base is radio_file)
        report['runtime_environment'] = environment
        run_batch(radio_bundle, radio_file, observer, request, unit, reader,
                  lambda prefix, value: emitter.send(prefix, value, guard), guard, report=report)
        emitter.send('MTQ_RESULT', report, guard)
        return 0
    except BaseException as error:
        status = error.code if isinstance(error, SystemExit) and type(error.code) is int else 1
        report['complete'] = False
        report['failure'] = error_types(error)
        report['entry_finished'] = clock()
        # run_batch has already unwound iterator/session cleanup. Keep a local
        # metadata projection even if the parent has stopped consuming stdout.
        with contextlib.suppress(BaseException):
            retain_failure_diagnostics(diagnostic_root, report, diagnostic_binding, unit, error)
        # Failure metadata is bounded and excludes exception messages/argv.
        # This diagnostic budget cannot authorize another SDK operation.
        diagnostic_end = time.monotonic_ns() + 100_000_000
        try:
            emitter.send('MTQ_RESULT', report,
                         lambda: need(time.monotonic_ns() < diagnostic_end))
        except BaseException:
            pass
        return status
    finally:
        if diagnostic_root is not None:
            with contextlib.suppress(BaseException):
                diagnostic_root.close()


if __name__ == '__main__':
    sys.exit(main())
