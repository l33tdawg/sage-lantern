# SPDX-License-Identifier: Apache-2.0
"""Exact public endpoint partitions only; no native enrollment or dispatch."""
import pytest
from lantern_meshticulum.bench.pilot_parent_caps import Cost
from lantern_meshticulum.bench.pilot_resume_rpc import endpoint_budget

RADIO=Cost(256,60000000)
TCP=Cost(ip_frames=2048,tx_stream_bytes=1048576,rx_stream_bytes=1048576)


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_original_resume_budget_is_unchanged(unit):
    expected=Cost() if unit=='unit-a' else Cost(512,120000000,16384,8388608,8388608)
    assert endpoint_budget(unit,None,RADIO,TCP)==expected


@pytest.mark.parametrize('unit,expected',[
    ('unit-a',Cost(512,120000000,4096,2097152,2097152)),
    ('unit-b',Cost(512,120000000,4096,2097152,2097152)),
    ('unit-c',Cost(1024,240000000))])
def test_twelve_endpoints_require_exact_mixed_and_bulk_partition(unit,expected):
    assert endpoint_budget(unit,'mixed-bulk-v1',RADIO,TCP)==expected


@pytest.mark.parametrize('kind',['foreign','',True,0,{}])
def test_unknown_modes_are_not_enable_flags(kind):
    with pytest.raises(RuntimeError):endpoint_budget('unit-c',kind,RADIO,TCP)


def test_unknown_unit_cannot_borrow_public_caps():
    with pytest.raises(RuntimeError):endpoint_budget('foreign','mixed-bulk-v1',RADIO,TCP)


@pytest.mark.parametrize('unit', ['unit-a','unit-b','unit-c'])
def test_explicit_c_bulk_8k_keeps_two_rf_endpoints_and_no_B_budget(unit):
    expected = Cost() if unit=='unit-b' else RADIO.plus(RADIO)
    assert endpoint_budget(unit,'c-bulk-8k-v1',RADIO,TCP)==expected
    assert endpoint_budget(unit,'c-bulk-8k-v1',RADIO,TCP)==endpoint_budget(unit,'c-bulk-v1',RADIO,TCP)


@pytest.mark.parametrize('kind', ['c-bulk-8k-v2','c-bulk-16k-v1','c-bulk-8k',True])
def test_unreviewed_small_bulk_modes_do_not_get_a_fallback_budget(kind):
    with pytest.raises(RuntimeError):endpoint_budget('unit-a',kind,RADIO,TCP)
