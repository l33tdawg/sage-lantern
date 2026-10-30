"""Pure profile/planning and temporary synthetic authority controls; no radio."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import math

import pytest

import campaign
import controller
from common import canonical, data_burst_policy
from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.serial_worker import estimated_airtime_us
from meshticulum.bench.wire import WireError


POLICY = dict(radio_lease_ms=60000, max_boot_attempts=1024,
              max_boot_estimated_airtime_us=300000000)
LEGACY_PROFILE = dict(frequency_hz=921375000, bandwidth_hz=500000,
    spreading_factor=5, coding_rate=5, chip_power_dbm=0, preamble=12,
    sync_word=66, lease_ms=60000, max_boot_attempts=1024,
    max_boot_estimated_airtime_us=300000000)
LEGACY_ADMISSION = dict(country='MY', waveforms=[[921375000,500000,5,5,0,12,66]],
    min_lease_ms=10000, max_lease_ms=180000, max_boot_attempts=1024,
    max_boot_estimated_airtime_us=300000000)


def admission(value):
    return RadioAdmission(**{**value, 'waveforms':tuple(tuple(x) for x in value['waveforms'])})


def test_omitted_and_explicit12_preserve_exact_profile_admission_and_cost_bytes():
    for args in ({}, {'preamble_symbols':12}):
        profile, allowed = controller.sf5_radio_parameters(POLICY, **args)
        assert canonical(profile) == canonical(LEGACY_PROFILE)
        assert canonical(allowed) == canonical(LEGACY_ADMISSION)
    # Retained pre-change cold-plan result, including float-ceil ACK rounding.
    expected = {'response_bytes':24565, 'data_frames':128, 'max_frames':161,
        'sender':{'attempts':161, 'host_reserved_us':6251745,
            'native_estimated_us':5537040, 'frozen_minimum_own_us':12547490,
            'frozen_minimum_shared_us':14649667},
        'receiver':{'attempts':64, 'host_reserved_us':835648,
            'native_estimated_us':701440, 'frozen_minimum_own_us':3338305,
            'frozen_minimum_shared_us':4173953},
        'extra_data_attempts':32, 'receiver_ack_allowance':64, 'source_estimates_only':True}
    for args in ({}, {'preamble_symbols':12}):
        assert canonical(campaign.part_cost(24565, extra_data_attempts=32,
            receiver_ack_allowance=64, **args)) == canonical(expected)


@pytest.mark.parametrize('symbols',[12,32])
def test_every_native_packet_estimate_and_paid_rounding_matches_selected_sdk(symbols):
    profile, allowed = controller.sf5_radio_parameters(POLICY,preamble_symbols=symbols)
    radio = RadioProfile(**profile)
    admission(allowed).check('MY',True,radio)
    for size in range(1,256):
        native = estimated_airtime_us(radio,size)
        assert campaign.airtime_us(size,preamble_symbols=symbols) == native
        assert campaign.reservation_us(size,preamble_symbols=symbols) == math.ceil(native*1.10)+1000
        if symbols==32:
            assert native-campaign.airtime_us(size)==1280
    assert campaign.reservation_us(70)*28==365596


@pytest.mark.parametrize('value',[None,True,False,0,8,13,31,33,64,12.0,32.0,'12','32',[],{}])
def test_unknown_or_coerced_selection_refuses_before_sdk_reads_or_output(tmp_path,monkeypatch,value):
    config=dict(schema='meshticulum.alpha3-individual-input.v1',sender='unit-b',receiver='unit-a',
        comparison_mode='queued',profile_host=True,preamble_symbols=value)
    path=tmp_path/'input.json';path.write_text(json.dumps(config));target=tmp_path/'uncreated'
    monkeypatch.setattr(controller,'sdk',lambda *a:pytest.fail('SDK/evidence before invalid option refusal'))
    monkeypatch.setattr(controller,'write_new',lambda *a,**k:pytest.fail('output before refusal'))
    monkeypatch.setattr(controller,'rpc',lambda *a,**k:pytest.fail('management action'))
    with pytest.raises(ValueError,match='invalid_preamble_symbols'):
        controller.prepare(path,target)
    assert not target.exists()
    with pytest.raises(ValueError,match='invalid_preamble_symbols'):
        campaign.part_cost(24565,preamble_symbols=value)


def test_explicit32_changes_only_waveform_preamble_and_requires_matching_admission():
    profile, allowed=controller.sf5_radio_parameters(POLICY,preamble_symbols=32)
    assert profile == dict(LEGACY_PROFILE,preamble=32)
    assert allowed == dict(LEGACY_ADMISSION,waveforms=[[921375000,500000,5,5,0,32,66]])
    with pytest.raises(WireError,match='unsupported_admitted_bench_profile'):
        admission(LEGACY_ADMISSION).check('MY',True,RadioProfile(**profile))
    with pytest.raises(WireError,match='unsupported_admitted_bench_profile'):
        admission(allowed).check('MY',True,RadioProfile(**LEGACY_PROFILE))


def test_longer_preamble_charges_data_control_ack_and_scheduler_cost_without_more_attempts():
    before=campaign.part_cost(24565,extra_data_attempts=31,receiver_ack_allowance=64)
    after=campaign.part_cost(24565,extra_data_attempts=31,receiver_ack_allowance=64,preamble_symbols=32)
    assert before['data_frames']==after['data_frames']==128
    for role in ('sender','receiver'):
        old,new=before[role],after[role]
        assert new['attempts']==old['attempts']
        assert new['native_estimated_us']-old['native_estimated_us']==1280*new['attempts']
        assert new['host_reserved_us']>old['host_reserved_us']
        assert new['frozen_minimum_own_us']>old['frozen_minimum_own_us']
        assert new['frozen_minimum_shared_us']>old['frozen_minimum_shared_us']
    assert after['sender']['attempts']==160  # includes final CONTROL
    budgets={u:dict(attempts=before[role]['attempts'],estimated_airtime_us=before[role]['host_reserved_us'])
             for u,role in [('unit-b','sender'),('unit-a','receiver')]}
    args=dict(sender='unit-b',receiver='unit-a',native={u:dict(attempts=0,estimated_airtime_us=0) for u in budgets},
        campaign_remaining=budgets,operation_caps=deepcopy(budgets),max_boot_attempts=1024,
        max_boot_airtime_us=300000000,max_frames=384,own_limit_us=300000000,shared_limit_us=600000000)
    assert campaign.admission_check([before],**args)['source_fit'] is True
    with pytest.raises(ValueError,match='campaign_remaining_insufficient'):
        campaign.admission_check([after],**args)


def synthetic_authority(path,unit,profile,allowed):
    """Synthetic local fixture; these host/peer IDs cannot authorize a device."""
    path.mkdir(mode=0o700)
    peers=[dict(chain_id='preamble-source-test',agent_id=c*64) for c in 'ab']
    boot='21f9c178-6090-40d9-9359-ecf1c7b950b1';payload=b'source only'
    plan=dict(schema='meshticulum.bench-run.v1',unit=unit,host_boot_id=boot,
        local_peer=peers[unit=='unit-b'],initiator=peers[0],responder=peers[1],
        session_id='11'*16,generation='22'*16,issued_epoch_us=9000000,expires_epoch_us=190000000,
        profile=profile,sender_unit='unit-a',country='MY',antenna_confirmed=True,max_frames=384,
        object=dict(sha256=hashlib.sha256(payload).hexdigest(),bytes=len(payload),chunk_index=0,
                    object_id=hashlib.sha256(payload).hexdigest()),
        data_burst=data_burst_policy(8,flow_window_frames=32,selective_repair_fill=True,
            selective_terminal_retry=True,scalar_fill_pair=True,selective_repeat_retry=True),
        operation_limits=dict(sender=dict(attempts=160,estimated_airtime_us=8000000),
                              receiver=dict(attempts=64,estimated_airtime_us=3000000)))
    for name,raw in [('grant.json',canonical(plan)),('secret.bin',b'K'*32),('active',b'active\n')]:
        p=path/name;p.write_bytes(raw);p.chmod(0o600)
    return RunAuthority(path,expected_unit=unit,host_boot_id=boot,clock=lambda:(10000000,1000000),
                        owner_probe=lambda:True,radio_admission=admission(allowed))


def test_real_sdk_peer_grants_bind_profile_and_route_without_changing_finite_limits(tmp_path):
    with ExitStack() as stack:
        pairs={}
        for symbols in (12,32):
            profile,allowed=controller.sf5_radio_parameters(POLICY,preamble_symbols=symbols)
            pair=[]
            for unit in ('unit-a','unit-b'):
                auth=synthetic_authority(tmp_path/f'{symbols}-{unit}',unit,profile,allowed)
                stack.callback(auth.close);pair.append(auth)
                assert auth.profile==RadioProfile(**profile)
                assert auth.route_record['profile']==profile
                assert auth.deadline_us==181000000 and auth.grant.max_frames==384
                assert auth.carrier_profile.min_rto_seconds==2.22
                assert auth.contract.limits.max_reassembly_frames==32
                assert auth.contract.limits.window_bytes==6656
                assert auth.contract.limits.max_retry_attempts==4
                assert auth.carrier_profile.forward_seconds == estimated_airtime_us(auth.profile,255)*1.10/1000000+.001
                assert auth.carrier_profile.reverse_control_seconds == estimated_airtime_us(auth.profile,70)*1.10/1000000+.001
            assert pair[0].grant.digest==pair[1].grant.digest
            pairs[symbols]=pair
        assert pairs[12][0].grant.digest!=pairs[32][0].grant.digest
        assert pairs[12][0].contract.route.profile_digest!=pairs[32][0].contract.route.profile_digest
        assert pairs[12][0].deadline_us==pairs[32][0].deadline_us
        assert pairs[32][0].carrier_profile.forward_seconds>pairs[12][0].carrier_profile.forward_seconds
        assert pairs[32][0].carrier_profile.reverse_control_seconds>pairs[12][0].carrier_profile.reverse_control_seconds


def test_mismatched32_grant_cannot_claim12_admission_or_start_authority(tmp_path):
    profile,_=controller.sf5_radio_parameters(POLICY,preamble_symbols=32)
    path=tmp_path/'mismatched'
    with pytest.raises(WireError,match='unsupported_admitted_bench_profile'):
        synthetic_authority(path,'unit-a',profile,LEGACY_ADMISSION)
    assert not (path/'started').exists()
