"""Synthetic local profile/admission controls; no device, scope or management."""
from contextlib import ExitStack
from copy import deepcopy
import json

import pytest

import campaign
import controller
from common import canonical
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.serial_worker import estimated_airtime_us
from meshticulum.bench.wire import WireError
from test_preamble_symbols import (POLICY, LEGACY_PROFILE, LEGACY_ADMISSION,
                                   admission, synthetic_authority)


@pytest.mark.parametrize('preamble', [12, 32])
def test_default_and_explicit66_keep_preceding_exact_profile_admission(preamble):
    profile = dict(LEGACY_PROFILE, preamble=preamble)
    allowed = dict(LEGACY_ADMISSION, waveforms=[[921375000,500000,5,5,0,preamble,66]])
    for option in ({}, {'sync_word':66}):
        actual, actual_allowed = controller.sf5_radio_parameters(
            POLICY, preamble_symbols=preamble, **option)
        assert canonical(actual) == canonical(profile)
        assert canonical(actual_allowed) == canonical(allowed)


@pytest.mark.parametrize('value', [None, True, False, 0, 1, 17, 19, 65, 67, 255,
                                  256, -18, 18.0, 66.0, '18', '66', [], {}])
def test_invalid_sync_refuses_before_sdk_evidence_or_output(tmp_path, monkeypatch, value):
    config = dict(schema='meshticulum.alpha3-individual-input.v1', sender='unit-b',
        receiver='unit-a', comparison_mode='queued', profile_host=True,
        preamble_symbols=32, sync_word=value)
    source = tmp_path/'input.json'; source.write_text(json.dumps(config))
    target = tmp_path/'uncreated'
    monkeypatch.setattr(controller, 'sdk', lambda *a: pytest.fail('SDK read'))
    monkeypatch.setattr(controller, 'write_new', lambda *a, **k: pytest.fail('output'))
    monkeypatch.setattr(controller, 'rpc', lambda *a, **k: pytest.fail('management'))
    with pytest.raises(ValueError, match='invalid_sync_word'):
        controller.prepare(source, target)
    with pytest.raises(ValueError, match='invalid_sync_word'):
        controller.sf5_radio_parameters(POLICY, sync_word=value)
    assert not target.exists()


@pytest.mark.parametrize('preamble', [12, 32])
def test_sync18_changes_only_sync_in_profile_admission_and_native_encoding(preamble):
    old, old_allowed = controller.sf5_radio_parameters(POLICY, preamble_symbols=preamble)
    new, new_allowed = controller.sf5_radio_parameters(POLICY, preamble_symbols=preamble, sync_word=18)
    assert new == dict(old, sync_word=18)
    expected = deepcopy(old_allowed); expected['waveforms'][0][6] = 18
    assert new_allowed == expected
    old_radio, new_radio = RadioProfile(**old), RadioProfile(**new)
    admission(new_allowed).check('MY', True, new_radio)
    before, after = old_radio.encode(board_id=2), new_radio.encode(board_id=2)
    assert len(before) == len(after) == 30
    assert [(i,a,b) for i,(a,b) in enumerate(zip(before,after)) if a != b] == [(13,66,18)]
    for size in range(1,256):
        assert estimated_airtime_us(old_radio,size) == estimated_airtime_us(new_radio,size)
        assert estimated_airtime_us(new_radio,size) == campaign.airtime_us(size,preamble_symbols=preamble)
    with pytest.raises(WireError, match='unsupported_admitted_bench_profile'):
        admission(old_allowed).check('MY', True, new_radio)
    with pytest.raises(WireError, match='unsupported_admitted_bench_profile'):
        admission(new_allowed).check('MY', True, old_radio)


@pytest.mark.parametrize('preamble', [12, 32])
def test_peer_grant_and_route_bind_sync_without_changing_costs_or_finite_limits(tmp_path,preamble):
    with ExitStack() as stack:
        pairs = {}
        for sync in (66,18):
            profile, allowed = controller.sf5_radio_parameters(
                POLICY, preamble_symbols=preamble, sync_word=sync)
            pair = []
            for unit in ('unit-a','unit-b'):
                auth = synthetic_authority(tmp_path/f'{sync}-{unit}', unit, profile, allowed)
                stack.callback(auth.close); pair.append(auth)
                assert auth.profile == RadioProfile(**profile)
                assert auth.route_record['profile'] == profile
                assert auth.deadline_us == 181000000 and auth.grant.max_frames == 384
                assert auth.contract.limits.max_reassembly_frames == 32
                assert auth.contract.limits.window_bytes == 6656
                assert auth.contract.limits.max_retry_attempts == 4
                assert auth.carrier_profile.min_rto_seconds == 2.22
            assert pair[0].grant.digest == pair[1].grant.digest
            pairs[sync] = pair
        old, new = pairs[66][0], pairs[18][0]
        assert old.grant.digest != new.grant.digest
        assert old.contract.route.profile_digest != new.contract.route.profile_digest
        assert old.carrier_profile.forward_seconds == new.carrier_profile.forward_seconds
        assert old.carrier_profile.reverse_control_seconds == new.carrier_profile.reverse_control_seconds
        assert old.data_burst_policy == new.data_burst_policy
        assert old.contract.limits == new.contract.limits
        assert old.operation_limits == new.operation_limits


@pytest.mark.parametrize('selected,admitted', [(18,66),(66,18)])
def test_mismatched_sync_grant_refuses_before_authority_start(tmp_path,selected,admitted):
    profile, _ = controller.sf5_radio_parameters(POLICY,preamble_symbols=32,sync_word=selected)
    _, allowed = controller.sf5_radio_parameters(POLICY,preamble_symbols=32,sync_word=admitted)
    path = tmp_path/'mismatched'
    with pytest.raises(WireError,match='unsupported_admitted_bench_profile'):
        synthetic_authority(path,'unit-a',profile,allowed)
    assert not (path/'started').exists()
