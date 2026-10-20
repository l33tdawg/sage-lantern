"""Actual held native-cap/source checks at the shortened worker entry boundary.

The authority callback is a source-only fault injector; no serial, SPI or RF
entry is invoked. The original fixture uses genuine owner/runtime files and DB.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench import node
from lantern_meshticulum.bench.phase_timing import enable
from tests.test_public32_single_verification import approval_case, protected_source, tmp_path, large_pack


def authorizer(index, authority, parent_current, authority_bound, *, public_service=True):
    source = Path(node.__file__).read_text()
    expressions = [n.value for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.keyword) and n.arg == 'authorize' and isinstance(n.value, ast.Lambda)]
    assert len(expressions) == 2
    return eval(compile(ast.Expression(body=expressions[index]), str(node.__file__), 'eval'),
                dict(authority=authority, parent_current=parent_current,
                     authority_bound=authority_bound, public_service=public_service))


@pytest.mark.parametrize('index', [0, 1], ids=['usb', 'hat'])
def test_one_full_live_native_check_per_dispatch_with_no_cached_approval(approval_case, index):
    approval, clock, caps, raw, path = approval_case
    events = []
    profile = enable(caps)
    bound = lambda: events.append('binding') or True
    authority = SimpleNamespace(live=lambda: events.append('authority') or True)
    def current():
        events.append('native')
        return caps.current() and bound()
    permit = authorizer(index, authority, current, bound)
    assert permit() is True
    assert events == ['binding', 'authority', 'native', 'binding']
    assert profile.snapshot()['phases']['native_current']['calls'] == 1
    assert profile.snapshot()['phases']['owner_reader']['calls'] == 1
    assert profile.snapshot()['phases']['runtime_reader']['calls'] == 1
    assert permit() is True
    assert profile.snapshot()['phases']['native_current']['calls'] == 2
    assert profile.snapshot()['phases']['owner_reader']['calls'] == 2
    assert profile.snapshot()['phases']['runtime_reader']['calls'] == 2
    # Every invocation rechecks the held bytes; an earlier True is never reused.
    target = path / 'owner.json'
    target.write_bytes(target.read_bytes() + b' ')
    assert permit() is False and caps._uncertain and approval._closed


@pytest.mark.parametrize('index', [0, 1], ids=['usb', 'hat'])
@pytest.mark.parametrize('fault', ['owner_bytes', 'owner_identity', 'owner_mode', 'runtime_bytes',
                                  'original_deadline', 'native_pending', 'sql_enrollment'])
def test_external_authority_callback_mutations_refuse_before_dispatch(approval_case, index, fault):
    approval, clock, caps, raw, path = approval_case
    paid_rows = {table: caps.db.execute('SELECT * FROM ' + table).fetchall()
                 for table in ('starts', 'claims', 'resources')}
    calls = []
    def change():
        calls.append('authority')
        if fault == 'owner_bytes':
            target = path / 'owner.json'; target.write_bytes(target.read_bytes() + b' ')
        elif fault == 'owner_identity':
            target = path / 'owner.json'; data = target.read_bytes(); target.unlink()
            target.write_bytes(data); target.chmod(0o600)
        elif fault == 'owner_mode':
            (path / 'owner.json').chmod(0o644)
        elif fault == 'runtime_bytes':
            target = approval.runtime.root / 'lantern_meshticulum/bench/pilot_resume_rpc.py'
            target.write_bytes(target.read_bytes() + b'\n')
        elif fault == 'original_deadline':
            clock.value = (approval._end, approval._phase_bounds[2])
        elif fault == 'native_pending':
            caps.pending.write_bytes(b'original unknown debt remains spent\n'); caps.pending.chmod(0o600)
        else:
            caps.db.execute('DROP TRIGGER IF EXISTS immutable_enrollment_UPDATE')
            caps.db.execute('UPDATE enrollment SET body=?', (b'{}',))
        return True
    permit = authorizer(index, SimpleNamespace(live=change), caps.current, lambda: True)
    dispatched = []
    if permit():
        dispatched.append(True)
    assert not dispatched and calls == ['authority']
    clock.value = None
    assert caps.current() is False
    # No start, claim or paid-cost transition is performed by this gate.
    assert {table: caps.db.execute('SELECT * FROM ' + table).fetchall()
            for table in paid_rows} == paid_rows


@pytest.mark.parametrize('index', [0, 1], ids=['usb', 'hat'])
def test_rebound_authority_refuses_without_entering_external_clock(index):
    events = []
    permit = authorizer(index, SimpleNamespace(live=lambda: events.append('clock') or True),
                        lambda: events.append('native') or True, lambda: False)
    assert permit() is False and events == []


@pytest.mark.parametrize('index', [0, 1], ids=['usb', 'hat'])
def test_binding_changed_during_authority_callback_is_freshly_refused(index):
    state = dict(current=True)
    events = []
    def live():
        state['current'] = False
        return True
    permit = authorizer(index, SimpleNamespace(live=live),
                        lambda: events.append('final') or state['current'], lambda: state['current'])
    assert permit() is False and events == ['final']


@pytest.mark.parametrize('index', [0, 1], ids=['usb', 'hat'])
def test_authority_refusal_does_not_dispatch_or_skip_future_native_checks(index):
    state = dict(live=False)
    events = []
    permit = authorizer(index, SimpleNamespace(live=lambda: state['live']),
                        lambda: events.append('fresh-native') or False, lambda: True)
    assert permit() is False and events == []
    state['live'] = True
    assert permit() is False and events == ['fresh-native']


@pytest.mark.parametrize('index', [0, 1], ids=['usb', 'hat'])
def test_nonpublic_worker_keeps_original_short_circuit_semantics(index):
    events = []
    permit = authorizer(index, SimpleNamespace(live=lambda: events.append('authority') or True),
                        lambda: events.append('native') or True,
                        lambda: pytest.fail('legacy bench never had a service binding'), public_service=False)
    assert permit() is True and events == ['authority', 'native']
