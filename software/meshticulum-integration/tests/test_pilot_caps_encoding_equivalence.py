"""Actual local native caps with modeled facts; no device or network calls."""
from dataclasses import fields
import json
from pathlib import Path
import sys
import pytest

from lantern_meshticulum.bench.pilot_native_caps import NativeCaps
from lantern_meshticulum.bench.pilot_parent_caps import canonical,Cost
from tests.test_pilot_native_caps import policy,Observers,prepared


@pytest.mark.parametrize('binding',[None,'b'*64])
def test_every_fresh_approval_gets_identical_canonical_binding_for_v1_v2(tmp_path,binding):
    p=policy();obs=Observers(p);directory=tmp_path/'parent';directory.mkdir(mode=0o700);seen=[];caps=None
    def approve(raw):
        value=json.loads(raw)
        assert raw==canonical(value)
        if caps is not None:assert raw==canonical(dict(enrollment=caps._enrollment,phase=value['phase']))
        seen.append(value['phase']);return obs.approve(raw)
    caps=NativeCaps.enroll(directory/'native.sqlite',p,unit='unit-a',clock=obs.clock,facts=obs.facts,
        approve=approve,prior_check=obs.prior,history_binding_sha256=binding)
    try:
        package,plan,receipt=prepared(caps,p,'lora');caps.consume_start(receipt['run_id'],plan,__import__('base64').b64decode(package['secret_base64']))
        before=caps.snapshot();approval_count,clock_count=obs.approvals,obs.calls
        for _ in range(10):assert caps.current() is True
        assert obs.approvals-approval_count==10 and obs.calls-clock_count==20 and caps.snapshot()==before
        assert seen[-10:]==['current']*10
    finally:caps.close()


@pytest.mark.parametrize('binding',[None,'b'*64])
def test_live_enrollment_mutation_during_approval_is_not_hidden_by_encoding(tmp_path,binding):
    p=policy();obs=Observers(p);directory=tmp_path/'parent';directory.mkdir(mode=0o700)
    caps=NativeCaps.enroll(directory/'native.sqlite',p,unit='unit-a',clock=obs.clock,facts=obs.facts,
        approve=obs.approve,prior_check=obs.prior,history_binding_sha256=binding)
    try:
        obs.approve_hook=lambda *args:caps._enrollment.update(deadline_monotonic_us=caps._enrollment['deadline_monotonic_us']+1)
        assert caps.current() is False and caps._uncertain is True
    finally:obs.approve_hook=None;caps.close()


def test_direct_cost_fields_cover_the_entire_serialized_contract():
    assert tuple(f.name for f in fields(Cost))==('radio_attempts','estimated_airtime_us','ip_frames','tx_stream_bytes','rx_stream_bytes')
