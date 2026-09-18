import json

import pytest

from sage_lantern.airtime_budget import (
    AirtimeLedger, AirtimePolicy, AirtimeSample, policy_from_settings,
)


def policy(**overrides):
    settings = dict(window_seconds=60.0, duty_cycle=0.5, frame_seconds=1.0, sample_max_age_seconds=30.0)
    settings.update(overrides)
    return AirtimePolicy(**settings)


def ledger(**overrides):
    return AirtimeLedger(policy(**overrides))


def sample(monotonic, tx, rx=0.0):
    return AirtimeSample(monotonic=monotonic, tx_airtime_seconds=tx, rx_airtime_seconds=rx)


def test_default_policy_is_a_one_percent_hour():
    default = AirtimePolicy()
    assert default.window_seconds == 3600
    assert default.duty_cycle == 0.01
    assert default.frame_seconds == 1.0


@pytest.mark.parametrize("overrides", [
    dict(window_seconds=59), dict(window_seconds=86401), dict(duty_cycle=0), dict(duty_cycle=1.01),
    dict(frame_seconds=0), dict(frame_seconds=61), dict(sample_max_age_seconds=9),
    dict(sample_max_age_seconds=3601),
])
def test_policy_rejects_out_of_range_settings(overrides):
    with pytest.raises(ValueError):
        policy(**overrides)


def test_policy_from_settings_defaults_and_validation():
    assert policy_from_settings({}) == AirtimePolicy()
    assert policy_from_settings({"duty_cycle": 0.1, "window_seconds": 600}).duty_cycle == 0.1
    with pytest.raises(ValueError):
        policy_from_settings({"duty_cycle": 0.01, "unknown": 1})
    with pytest.raises(ValueError):
        policy_from_settings("1%")


def test_allow_fails_closed_without_a_counter_sample():
    budget = ledger()
    assert budget.allow(now=10.0) is False
    assert budget.snapshot()["denied"] == 1


def test_allow_checks_without_spending_until_the_attempt_commits():
    budget = ledger()
    budget.observe(sample(0.0, 0.0))
    assert budget.allow(now=1.0) is True
    assert budget.allow(now=1.0) is True
    for _ in range(30):        # budget is 0.5 * 60 s; nothing was spent yet
        budget.spend()
    assert budget.allow(now=2.0) is False
    assert budget.snapshot()["estimated_seconds"] == 30.0


def test_firmware_counter_advance_consumes_the_budget():
    budget = ledger()
    budget.observe(sample(0.0, 0.0))
    assert budget.allow(now=1.0) is True
    budget.observe(sample(2.0, 29.0))   # firmware counted 29 s since the window opened
    assert budget.allow(now=3.0) is True    # 29 + 1 fits exactly
    budget.spend()
    assert budget.allow(now=3.0) is False   # 29 + 2 does not


def test_stale_or_backwards_sample_fails_closed():
    budget = ledger()
    budget.observe(sample(0.0, 0.0))
    assert budget.allow(now=31.0) is False
    assert budget.allow(now=-1.0) is False
    assert budget.snapshot()["denied_stale"] == 2


def test_window_rollover_restores_the_budget():
    budget = ledger()
    budget.observe(sample(0.0, 0.0))
    assert budget.allow(now=1.0) is True
    for _ in range(30):
        budget.spend()
    assert budget.allow(now=2.0) is False
    budget.observe(sample(61.0, 30.0))      # a fresh reading at the new window
    assert budget.allow(now=61.0) is True   # baseline resets to the current counter


def test_out_of_order_samples_are_ignored():
    budget = ledger()
    budget.observe(sample(5.0, 0.0))
    budget.observe(sample(10.0, 25.0))
    budget.observe(sample(7.0, 12.0))       # older than the stored sample, ignored
    assert budget.allow(now=10.5) is True   # window baseline comes from tx=25
    budget.observe(sample(11.0, 25.0))      # counter unchanged since the baseline
    assert budget.snapshot()["used_seconds"] == 0.0


def test_snapshot_is_bounded_json_and_never_carries_payloads():
    budget = ledger()
    budget.observe(sample(0.0, 3.0))
    assert budget.allow(now=1.0) is True
    budget.spend()
    snapshot = budget.snapshot()
    assert json.dumps(snapshot)
    assert set(snapshot) == {"event", "window_seconds", "duty_cycle", "frame_seconds", "budget_seconds",
                             "used_seconds", "estimated_seconds", "unsampled_seconds", "allowed", "denied",
                             "denied_stale", "last"}
    assert "payload" not in json.dumps(snapshot)
