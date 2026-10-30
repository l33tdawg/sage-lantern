"""Source estimator correspondence; no elapsed-time or RF success proof."""
import importlib.util
import math
from pathlib import Path

from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.serial_worker import estimated_airtime_us


def test_sf5_native_and_paid_estimates_match_sdk_for_every_packet_length():
    path = Path(__file__).with_name('campaign.py')
    spec = importlib.util.spec_from_file_location('handoff_costs', path)
    costs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(costs)
    profile = RadioProfile(921375000, 500000, 5, 5, 0, 12, 0x42, 60000, 1024, 300000000)
    for size in range(1, 256):
        native = estimated_airtime_us(profile, size)
        assert costs.airtime_us(size) == native
        assert costs.reservation_us(size) == math.ceil(native * 1.10) + 1000
    assert costs.reservation_us(70) * 28 == 365596
