"""Duty-cycle gate wiring: a denied budget stops dispatch, an allowed one proceeds."""

import threading
import time
import types

from sage_lantern.airtime_budget import AirtimeLedger, AirtimePolicy, AirtimeSample
from sage_lantern.mesh_runtime import DISPATCH_BURST, DISPATCH_RESERVE_SECONDS, MeshRuntime


def policy(**overrides):
    settings = dict(window_seconds=60.0, duty_cycle=0.5, frame_seconds=1.0, sample_max_age_seconds=30.0)
    settings.update(overrides)
    return AirtimePolicy(**settings)


class BurstOutbox:
    def __init__(self, state="queued"):
        self.calls = 0
        self.state = state

    def dispatch_one(self, message_id, **kwargs):
        self.calls += 1
        return types.SimpleNamespace(state=self.state, next_fragment=self.calls + 1, fragment_count=99)


def runtime(*, ledger=None, outbox=None):
    service = MeshRuntime()
    service._airtime = ledger
    service._airtime_deferrals = 0
    service._local = threading.local()
    service._local.background_deadline = None
    service._local.inventory = None
    service._access = lambda: True
    service.outbox = outbox
    return service


def fresh_sample(tx=0.0):
    return AirtimeSample(monotonic=time.monotonic(), tx_airtime_seconds=tx)


def test_dispatch_row_is_unchanged_without_a_policy():
    outbox = BurstOutbox()
    service = runtime(outbox=outbox)
    service._dispatch_row(types.SimpleNamespace(airtime_sample=None), "message")
    assert outbox.calls == DISPATCH_BURST


def test_reservation_is_a_noop_without_a_policy():
    service = runtime()
    with service._airtime_reservation(types.SimpleNamespace(airtime_sample=None)) as allowed:
        assert allowed is True


def test_reservation_denies_before_the_first_counter_sample():
    service = runtime(ledger=AirtimeLedger(policy()))
    with service._airtime_reservation(types.SimpleNamespace(airtime_sample=None)) as allowed:
        assert allowed is False
    assert service._airtime_deferrals == 1


def test_reservation_spends_only_after_an_allowed_attempt():
    ledger = AirtimeLedger(policy(duty_cycle=0.02))   # 1.2 s budget
    service = runtime(ledger=ledger)
    device = types.SimpleNamespace(airtime_sample=fresh_sample())
    with service._airtime_reservation(device) as allowed:
        assert allowed is True
    assert ledger.snapshot()["estimated_seconds"] == 1.0
    with service._airtime_reservation(device) as allowed:
        assert allowed is False
    assert ledger.snapshot()["estimated_seconds"] == 1.0


def test_dispatch_row_fails_closed_until_the_first_counter_sample():
    outbox = BurstOutbox()
    service = runtime(ledger=AirtimeLedger(policy()), outbox=outbox)
    service._dispatch_row(types.SimpleNamespace(airtime_sample=None), "message")
    assert outbox.calls == 0


def test_dispatch_row_sends_a_full_burst_inside_the_budget():
    outbox = BurstOutbox()
    service = runtime(ledger=AirtimeLedger(policy()), outbox=outbox)
    service._dispatch_row(types.SimpleNamespace(airtime_sample=fresh_sample()), "message")
    assert outbox.calls == DISPATCH_BURST


def test_dispatch_row_stops_when_the_budget_is_spent():
    outbox = BurstOutbox()
    service = runtime(ledger=AirtimeLedger(policy(duty_cycle=0.02)), outbox=outbox)   # 1.2 s budget
    service._dispatch_row(types.SimpleNamespace(airtime_sample=fresh_sample()), "message")
    assert outbox.calls == 1
    assert service._airtime_deferrals == 1


def test_dispatch_row_refuses_a_fragment_the_wake_cannot_finish():
    """The reserve must cover a fragment's own cost, not a scheduling margin.

    Unit A 2026-09-22: with the reserve at 1 s against a ~9.6 s fragment, a wake
    started a fragment inside its last second, died post-wire (``wire_frames``:
    1) and left every authorized route refused until the next cold start. A wake
    with less than one fragment's budget left must not start one.
    """
    outbox = BurstOutbox()
    service = runtime(outbox=outbox)
    device = types.SimpleNamespace(airtime_sample=None)
    service._local.background_deadline = time.monotonic() + DISPATCH_RESERVE_SECONDS - 0.5
    service._dispatch_row(device, "message")
    assert outbox.calls == 0
    service._local.background_deadline = time.monotonic() + DISPATCH_RESERVE_SECONDS + 0.5
    service._dispatch_row(device, "message")
    assert outbox.calls == DISPATCH_BURST


def test_dispatch_row_defers_when_the_firmware_counter_reports_the_budget_spent():
    outbox = BurstOutbox(state="awaiting_receipt")
    service = runtime(ledger=AirtimeLedger(policy()), outbox=outbox)   # 30 s budget
    device = types.SimpleNamespace(airtime_sample=fresh_sample())
    service._dispatch_row(device, "message")   # opens the window at counter 0
    assert outbox.calls == 1
    # The firmware reports its own TX (for example ACKs it sent by itself).
    device.airtime_sample = fresh_sample(tx=29.5)
    service._dispatch_row(device, "message")
    assert outbox.calls == 1


def test_successful_fragment_stays_queued_when_the_wake_budget_runs_out(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("sage_lantern.mesh_runtime.time.monotonic", lambda: clock[0])

    class TimedOutbox(BurstOutbox):
        def dispatch_one(self, message_id, **kwargs):
            result = super().dispatch_one(message_id, **kwargs)
            clock[0] += 2.0
            return result

    outbox = TimedOutbox()
    service = runtime(outbox=outbox)
    service._reconcile_pending = lambda: None
    service._public_gateway = service._public_mesh = None
    service._queued_rows().add("message")
    scans = []
    service.store = types.SimpleNamespace(scan=lambda **kwargs: scans.append(True) or [])
    service._next_scan_at = 130.0
    device = types.SimpleNamespace(airtime_sample=None)

    for expected_fragments in (1, 2):
        service._local.background_deadline = clock[0] + DISPATCH_RESERVE_SECONDS + 1.0
        service._dispatch_pending(device)
        assert outbox.calls == expected_fragments
        assert service._queued_rows() == {"message"}
    assert scans == []
