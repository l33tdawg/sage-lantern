# SPDX-License-Identifier: Apache-2.0
"""Five optional counter boundaries; no permission, transport or packet log."""
from collections import deque
import os
import threading

from .phase_timing import AggregateTiming


_MARKERS = ('ready_complete', 'first_data_event', 'flow_complete_observed',
            'cleanup_begin', 'cleanup_end')


class TransferTiming:
    """Counter samples are taken now, never backdated to a driver event.

    A sender DATA event follows worker.transmit(), including a refused attempt.
    A receiver event follows authentication/absorb and need not advance a flow.
    Only sender flow completion indicates the confirmed final ACK. Nested
    elapsed counters remain inclusive wall times, not additive CPU/airtime.
    """
    def __init__(self, profile, *, sends):
        if type(profile) is not AggregateTiming or type(sends) is not bool:
            raise RuntimeError('invalid_transfer_timing')
        self._profile = profile
        self._sends = sends
        self._owner = (os.getpid(), threading.get_ident())
        self._markers = {}

    def _check(self):
        if self._owner != (os.getpid(), threading.get_ident()):
            raise RuntimeError('transfer_timing_owner_changed')
        if (type(self._profile) is not AggregateTiming or type(self._sends) is not bool
                or type(self._markers) is not dict
                or any(name not in _MARKERS for name in self._markers)):
            raise RuntimeError('transfer_timing_state_changed')

    def mark(self, name, *, event=None):
        self._check()
        if name not in _MARKERS:
            raise RuntimeError('unknown_transfer_timing_marker')
        if name in self._markers:
            return False
        counters = self._profile.snapshot()['phases']
        sampled = self._profile.start() // 1000
        if self._markers and sampled < next(reversed(self._markers.values()))['sampled_monotonic_us']:
            raise RuntimeError('transfer_timing_clock_regressed')
        row = dict(sampled_monotonic_us=sampled, phases=counters)
        if event is not None:
            row['event'] = {key: event[key] for key in ('event', 'outer_kind', 'mono_us', 'outcome') if key in event}
        self._markers[name] = row
        return True

    def observe(self, event):
        if 'first_data_event' in self._markers:
            return
        kind = 'tx' if self._sends else 'rx'
        if type(event) is dict and event.get('event') == kind and event.get('outer_kind') == 'DATA':
            self.mark('first_data_event', event=event)

    def _delta(self, first, last):
        if first not in self._markers or last not in self._markers:
            return None
        before, after = self._markers[first], self._markers[last]
        elapsed = after['sampled_monotonic_us'] - before['sampled_monotonic_us']
        if elapsed < 0 or set(before['phases']) != set(after['phases']):
            raise RuntimeError('transfer_timing_counter_regressed')
        phases = {}
        for name, initial in before['phases'].items():
            final = after['phases'][name]
            values = {key: final[key] - initial[key] for key in ('calls', 'elapsed_us')}
            if any(type(value) is not int or value < 0 for value in values.values()):
                raise RuntimeError('transfer_timing_counter_regressed')
            phases[name] = values
        return dict(observed_interval_us=elapsed, phases=phases)

    def snapshot(self):
        self._check()
        # Copy all records; the report is data and never an authority callback.
        markers = {name: dict(sampled_monotonic_us=row['sampled_monotonic_us'],
                    phases={key: dict(value) for key, value in row['phases'].items()},
                    **({'event': dict(row['event'])} if 'event' in row else {}))
                   for name, row in self._markers.items()}
        return dict(schema='meshticulum.native-transfer-timing.v1',
            clock_basis='host_monotonic_us', nested_elapsed_times_inclusive=True,
            additive_across_phases=False, measured_physical_airtime_us=None,
            endpoint='sender' if self._sends else 'receiver',
            first_data_basis=('first DATA attempt return, including refused attempts' if self._sends
                              else 'first authenticated DATA after absorb; flow acceptance unproven'),
            completion_basis=('confirmed final ACK observed' if self._sends else 'reassembly observed; final ACK barrier separate'),
            cleanup_excludes_facts_and_result_write=True, markers=markers,
            missing_markers=[name for name in _MARKERS if name not in markers],
            first_data_event_to_flow_complete=self._delta('first_data_event', 'flow_complete_observed'),
            ready_to_flow_complete=self._delta('ready_complete', 'flow_complete_observed'),
            local_cleanup=self._delta('cleanup_begin', 'cleanup_end'))


class FirstDataEvents(deque):
    """Preserve the existing bounded event deque and observe DATA once."""
    def __init__(self, original, timing):
        if type(original) is not deque or type(timing) is not TransferTiming:
            raise RuntimeError('invalid_transfer_event_observer')
        super().__init__(original, maxlen=original.maxlen)
        self._timing = timing

    def append(self, event):
        super().append(event)
        self._timing.observe(event)


def install(flow, profile):
    """Called after admitted flow construction only when profiling is enabled."""
    if profile is None:
        return None
    timing = TransferTiming(profile, sends=flow.sends)
    flow.events = FirstDataEvents(flow.events, timing)
    return timing
