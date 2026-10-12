"""Optional aggregate observation preserves the genuine source/SQLite gates."""
import threading

import pytest

from lantern_meshticulum.bench.phase_timing import AggregateTiming, call, enable
from tests.test_bundle_approval_merge import case, data, protected_source, tmp_path
from tests.test_pilot_native_caps import state, packet


def test_default_has_no_reporter_and_preserves_genuine_current(case):
    approval, clock, caps, raw = case
    assert not hasattr(caps, '_phase_timing')
    assert not hasattr(approval, '_phase_timing')
    assert caps.current() is True


def test_enabled_current_reports_same_calls_and_complete_reader_runtime_checks(case):
    approval, clock, caps, raw = case
    profile = enable(caps)
    assert caps.current() is True
    phases = profile.snapshot()['phases']
    expected = dict(native_current=1, native_available=9, native_validate=1,
                    sql_active_start=0, sql_enrollment=1, sql_claims=1, sql_resource=0,
                    approval_bound=11, bundle_context=4, owner_reader=1, runtime_reader=1)
    assert {name: value['calls'] for name, value in phases.items()} == expected
    assert all(set(value) == {'calls', 'elapsed_us'}
               and type(value['elapsed_us']) is int and value['elapsed_us'] >= 0 for value in phases.values())
    assert profile.snapshot()['nested_elapsed_times_inclusive'] is True


@pytest.mark.parametrize('mutation', ['deadline', 'policy', 'owner_file', 'runtime_file'])
def test_profile_does_not_change_second_clock_mutation_refusal(case, mutation):
    approval, clock, caps, raw = case
    profile = enable(caps)
    original_clock = clock.__class__.__call__
    def observed(self):
        result = original_clock(self)
        if self is clock and self.calls == 2 and mutation == 'deadline':
            return approval._end, approval._phase_bounds[2]
        return result
    def action(index):
        if index != 2: return
        if mutation == 'policy':
            object.__setattr__(approval.policy, 'expires_epoch_us', approval.policy.expires_epoch_us+1)
        elif mutation == 'owner_file':
            path = next(path for path, *_ in approval.reader.held if path.name == 'owner.json')
            path.write_bytes(path.read_bytes()+b' ')
        elif mutation == 'runtime_file':
            path = next(path for path, *_ in approval.runtime.reader.held if path.name != 'manifest.json')
            path.write_bytes(path.read_bytes()+b'\n')
    clock.action = action
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clock.__class__, '__call__', observed)
        with pytest.raises(Exception): approval(raw)
    clock.action = None
    assert approval._closed and caps.current() is False
    assert profile.snapshot()['phases']['approval_bound']['calls'] > 0


def test_profiled_sql_reads_keep_costs_live_and_warmed_row_mutations_refused(state):
    caps, observer, policy, path, root = state
    caps.prepare_claim(packet(policy, 'lora'))
    profile = AggregateTiming(); caps._phase_timing = profile
    assert caps.current()
    phases = profile.snapshot()['phases']
    assert phases['sql_enrollment']['calls'] == phases['sql_claims']['calls'] == phases['sql_resource']['calls'] == 1
    caps.db.execute('DROP TRIGGER immutable_resources_UPDATE')
    caps.db.execute('UPDATE resources SET body=?', (b'{}',))
    assert caps.current() is False and caps._uncertain
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone() == (1,)
    assert profile.snapshot()['phases']['native_current']['calls'] == 2


@pytest.mark.parametrize('committed', [False, True])
def test_profiled_unknown_commit_retains_the_original_debt_and_rollback_fence(state, monkeypatch, committed):
    caps, observer, policy, path, root = state
    caps._phase_timing = AggregateTiming()
    commit = caps._commit
    def lost_return():
        if committed: commit()
        raise OSError('original-commit-return-loss')
    monkeypatch.setattr(caps, '_commit', lost_return)
    with pytest.raises(Exception): caps.prepare_claim(packet(policy, 'lora'))
    assert caps.pending.exists() and caps._uncertain and not caps.current()
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone() == (int(committed),)
    assert not caps.db.in_transaction


def test_timing_never_suppresses_an_original_exception():
    profile = AggregateTiming()
    original = RuntimeError('original-failure')
    def failed(): raise original
    with pytest.raises(RuntimeError) as caught:
        call(profile, 'sql_claims', failed)
    assert caught.value is original
    assert profile.snapshot()['phases']['sql_claims']['calls'] == 1


def test_default_timing_call_is_exactly_one_original_call():
    observed = []
    assert call(None, 'sql_claims', lambda: observed.append(True) or 'unchanged') == 'unchanged'
    assert observed == [True]


def test_reporter_cannot_substitute_a_caller_clock():
    profile = AggregateTiming(); called = []
    profile._clock = profile._clock_ref = lambda: called.append(True) or 1
    with pytest.raises(RuntimeError): profile.start()
    assert called == []


def test_reporter_is_owned_by_one_thread_and_opens_nothing():
    profile = AggregateTiming(); results = []
    def foreign():
        try: profile.start()
        except RuntimeError: results.append('refused')
    thread = threading.Thread(target=foreign); thread.start(); thread.join(2)
    assert not thread.is_alive() and results == ['refused']
    assert all(value['calls'] == 0 for value in profile.snapshot()['phases'].values())
