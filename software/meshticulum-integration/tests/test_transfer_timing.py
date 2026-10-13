"""Bounded source timing controls; neither radio measurements nor permission."""
from collections import deque
from types import SimpleNamespace
import threading

import pytest

from lantern_meshticulum.bench.phase_timing import AggregateTiming
from lantern_meshticulum.bench.transfer_timing import TransferTiming, install
from tests.test_bundle_approval_merge import case, data, protected_source, tmp_path


def event(kind='tx', outcome='RF_DONE', mono=1):
    return dict(event=kind, outer_kind='DATA', mono_us=mono, outcome=outcome)


def test_disabled_instrumentation_keeps_original_event_container():
    flow = SimpleNamespace(sends=True, events=deque(maxlen=512))
    original = flow.events
    assert install(flow, None) is None and flow.events is original


@pytest.mark.parametrize('sends', [False, True])
def test_event_order_capacity_records_and_first_attempt_are_preserved(sends):
    old = deque([{'event': 'old'}], maxlen=3)
    flow = SimpleNamespace(sends=sends, events=old)
    timing = install(flow, AggregateTiming())
    matching = 'tx' if sends else 'rx'
    other = event('rx' if sends else 'tx')
    refused = event(matching, 'REFUSED')
    later = event(matching, 'RF_DONE')
    for row in (other, refused, later):
        flow.events.append(row)
    assert flow.events.maxlen == 3 and list(flow.events) == [other, refused, later]
    assert flow.events[0] is other and flow.events[1] is refused
    report = timing.snapshot()
    assert report['markers']['first_data_event']['event'] == refused
    assert report['endpoint'] == ('sender' if sends else 'receiver')
    assert report['first_data_event_to_flow_complete'] is None
    assert report['markers']['first_data_event']['sampled_monotonic_us'] != refused['mono_us']
    if sends:
        assert 'attempt return' in report['first_data_basis']
        assert 'confirmed final ACK' in report['completion_basis']
    else:
        assert 'acceptance unproven' in report['first_data_basis']
        assert 'barrier separate' in report['completion_basis']


def test_no_data_and_partial_failure_do_not_fabricate_a_window():
    flow = SimpleNamespace(sends=True, events=deque(maxlen=512))
    timing = install(flow, AggregateTiming())
    timing.mark('ready_complete')
    flow.events.append(dict(event='tx', outer_kind='ACK', mono_us=1))
    timing.mark('cleanup_begin')
    timing.mark('cleanup_end')
    report = timing.snapshot()
    assert report['missing_markers'] == ['first_data_event', 'flow_complete_observed']
    assert report['first_data_event_to_flow_complete'] is report['ready_to_flow_complete'] is None
    assert report['local_cleanup']['observed_interval_us'] >= 0


def test_boundary_deltas_cover_genuine_current_checks_without_changing_authority(case):
    approval, clock, caps, raw = case
    profile = AggregateTiming()
    caps._phase_timing = approval._phase_timing = profile
    flow = SimpleNamespace(sends=True, events=deque(maxlen=512))
    timing = install(flow, profile)
    assert caps.current()
    timing.mark('ready_complete')
    flow.events.append(event())
    assert caps.current()
    assert caps.current()
    timing.mark('flow_complete_observed')
    first = timing.snapshot()
    assert first['first_data_event_to_flow_complete']['phases']['native_current']['calls'] == 2
    assert first['ready_to_flow_complete']['phases']['native_current']['calls'] == 2
    timing.mark('cleanup_begin')
    assert caps.current()
    timing.mark('cleanup_end')
    report = timing.snapshot()
    assert report['local_cleanup']['phases']['native_current']['calls'] == 1
    assert len(report['markers']) == 5
    assert report['missing_markers'] == []
    assert report['measured_physical_airtime_us'] is None
    assert report['additive_across_phases'] is False
    assert report['nested_elapsed_times_inclusive'] is True
    # Returned data cannot mutate retained samples or phase counters.
    report['markers']['first_data_event']['phases']['native_current']['calls'] = -100
    assert timing.snapshot()['first_data_event_to_flow_complete']['phases']['native_current']['calls'] == 2
    assert caps.current()


def test_boundary_samples_preserve_original_revocation_failure(case):
    approval, clock, caps, raw = case
    profile = AggregateTiming()
    caps._phase_timing = approval._phase_timing = profile
    timing = TransferTiming(profile, sends=True)
    timing.mark('ready_complete')
    timing.observe(event())
    path = caps._names()['approval']
    path.write_bytes(b'revoked\n')
    assert caps.current() is False
    timing.mark('cleanup_begin')
    caps.close()
    timing.mark('cleanup_end')
    report = timing.snapshot()
    assert 'flow_complete_observed' in report['missing_markers']
    assert report['first_data_event_to_flow_complete'] is None


def test_markers_are_fixed_bounded_and_cannot_be_overwritten():
    timing = TransferTiming(AggregateTiming(), sends=True)
    assert timing.mark('ready_complete') is True
    row = timing.snapshot()['markers']['ready_complete']
    for _ in range(1000):
        assert timing.mark('ready_complete') is False
        timing.observe(event())
    assert len(timing.snapshot()['markers']) == 2
    assert timing.snapshot()['markers']['ready_complete'] == row
    with pytest.raises(RuntimeError, match='unknown_transfer_timing_marker'):
        timing.mark('extra_packet')


@pytest.mark.parametrize('key', ['calls', 'elapsed_us'])
def test_regressed_counter_is_refused_instead_of_reporting_negative_cost(key):
    profile = AggregateTiming()
    profile._rows['native_current'][:] = [3, 10]
    timing = TransferTiming(profile, sends=True)
    timing.observe(event())
    profile._rows['native_current'][0 if key == 'calls' else 1] -= 1
    timing.mark('flow_complete_observed')
    with pytest.raises(RuntimeError, match='transfer_timing_counter_regressed'):
        timing.snapshot()


def test_observation_is_bound_to_the_original_thread():
    timing = TransferTiming(AggregateTiming(), sends=True)
    errors = []
    def foreign():
        try:
            timing.mark('ready_complete')
        except RuntimeError as error:
            errors.append(str(error))
    thread = threading.Thread(target=foreign)
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and errors == ['transfer_timing_owner_changed']
    assert timing.snapshot()['markers'] == {}
