"""Real temporary retired ledgers; all keys, boots and clocks are source models."""
from copy import deepcopy
from dataclasses import replace

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc, pilot_parent_snapshot as snapshot
from tests.test_pilot_resume_rpc_rollover import actual_rows, BOOT
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_lora_comparison_runner import runner
from tests.test_gfsk250_mb1_partial_history import observed_original
from tests.test_e244_history import inputs as h45_inputs
from tests.test_fast_360_history import pinned_inputs as h46_inputs

NEW_BOOT='22222222-2222-4222-8222-222222222222'
WRONG_BOOT='33333333-3333-4333-8333-333333333333'
AFTER_REBOOT=(201000000,1000)


def test_real_retired_writer_history_passes_after_reboot_without_rebasing(actual_rows,monkeypatch,request):
    state,policy,observed,started,unstarted,pins,database_sha=actual_rows
    original_policy=rpc.canonical(policy.to_dict())
    monkeypatch.setattr(rpc,'boot',lambda:NEW_BOOT)
    with rpc.Reader() as reader:
        assert len(rpc.latest_preparation_files(reader,'unit-b',policy,observed,pins))==9
        evidence=rpc.retired_unstarted(reader,'unit-b',policy,observed,unstarted[0],lambda:AFTER_REBOOT)
    assert evidence['actual_cost'] is None and evidence['full_prepaid_caps']['ip_frames']==2048
    assert evidence['no_refund'] is True and evidence['physical_quiescence_observed'] is False
    request.getfixturevalue('root_observation')
    scope=snapshot.Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,
        policy.parent_id,policy.digest,database_sha,'unit-b',NEW_BOOT,230000000)
    current=snapshot.collect(state,scope,clock=lambda:AFTER_REBOOT,boot_reader=lambda:NEW_BOOT).to_dict()
    assert current['status']=='scoped_rows_observed' and current['scoped_rows_complete'] is True
    assert current['policy_expired_at_observation'] is True and current['pending_unknown'] is False
    assert current['current_host_boot_id']==NEW_BOOT
    assert current['detail']['enrollment']['host_boot_id']==policy.unit('unit-b').host_boot_id==BOOT
    assert current['detail']==observed['detail']
    assert rpc.canonical(policy.to_dict())==original_policy
    assert current['complete_global_debt'] is current['eligible_for_enrollment'] is current['refund_permitted'] is False


@pytest.mark.parametrize('fault',['live_epoch','same_boot_live_mono','grant','policy_boot','caps','pin','active'])
def test_retired_writer_refuses_live_or_changed_original_evidence(actual_rows,monkeypatch,fault):
    state,policy,observed,started,unstarted,pins,_=actual_rows
    row=unstarted[0];run=row['run_id'];sample=AFTER_REBOOT
    monkeypatch.setattr(rpc,'boot',lambda:NEW_BOOT)
    if fault=='live_epoch':sample=(170000000,1000)
    elif fault=='same_boot_live_mono':monkeypatch.setattr(rpc,'boot',lambda:BOOT)
    elif fault=='grant':
        path=state/'runs'/run/'grant.json';value=rpc.parse(path.read_bytes())
        value['host_boot_id']=NEW_BOOT;path.write_bytes(rpc.canonical(value))
    elif fault=='policy_boot':
        policy=replace(policy,units=tuple(replace(u,host_boot_id=NEW_BOOT)if u.unit=='unit-b'else u for u in policy.units))
    elif fault=='caps':
        observed=deepcopy(observed)
        next(c for c in observed['detail']['claims']if c['child']['run_id']==run)['child']['full_caps']['ip_frames']+=1
    elif fault=='pin':row=dict(row,grant_sha256='0'*64)
    else:(state/'runs'/run/'active').write_bytes(b'active\n')
    with rpc.Reader() as reader,pytest.raises(RuntimeError):
        rpc.retired_unstarted(reader,'unit-b',policy,observed,row,lambda:sample)


@pytest.mark.parametrize('fault',['wrong_current_boot','ledger_bytes','pending'])
def test_real_snapshot_keeps_current_boot_ledger_and_pending_guards(actual_rows,monkeypatch,request,fault):
    state,policy,observed,started,unstarted,pins,database_sha=actual_rows
    request.getfixturevalue('root_observation')
    database=state/'parents'/policy.parent_id/'native.sqlite'
    original=database.read_bytes()
    try:
        if fault=='ledger_bytes':database.write_bytes(original+b'changed')
        elif fault=='pending':rpc.write_new(database.parent,'native.sqlite.pending',policy.digest.encode())
        scope=snapshot.Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,
            policy.parent_id,policy.digest,database_sha,'unit-b',NEW_BOOT,230000000)
        value=snapshot.collect(state,scope,clock=lambda:AFTER_REBOOT,
            boot_reader=lambda:WRONG_BOOT if fault=='wrong_current_boot'else NEW_BOOT).to_dict()
        if fault=='pending':assert value['pending_unknown'] is True
        else:assert value['status']=='unavailable' and value['scoped_rows_complete'] is False
        assert value['eligible_for_enrollment'] is value['refund_permitted'] is False
    finally:
        if fault=='ledger_bytes':database.write_bytes(original)


@pytest.mark.parametrize('original,current,sample,expected',[
    (BOOT,BOOT,(201000000,901000000),True),
    (BOOT,BOOT,AFTER_REBOOT,False),
    (BOOT,NEW_BOOT,AFTER_REBOOT,True),
    (BOOT,NEW_BOOT,(180000000,1000),False),
])
def test_retired_clock_never_maps_or_renews_original_deadline(original,current,sample,expected):
    assert rpc.historical_clock_expired(original,180000000,880000000,sample,current) is expected


def test_new_grant_peers_follow_current_boot_without_changing_history(runner):
    import hashlib
    config={'units':{unit:{'host_boot_id':BOOT}for unit in ('unit-a','unit-b','unit-c')}}
    historical=runner.current_peers(config)
    before=deepcopy(historical)
    config['units']['unit-c']['host_boot_id']=NEW_BOOT
    current=runner.current_peers(config)
    assert current['unit-a']==historical['unit-a'] and current['unit-b']==historical['unit-b']
    assert current['unit-c']!=historical['unit-c'] and historical==before
    assert current['unit-c']==dict(chain_id='meshticulum-public-pilot-v1',agent_id=hashlib.sha256(
        ('meshticulum-public-pilot-v1/unit-c/'+NEW_BOOT).encode()).hexdigest())


@pytest.mark.parametrize('current,mono,expected',[(BOOT,880000000,True),(BOOT,1000,False),
    (NEW_BOOT,1000,True)])
def test_retired_observation_mono_keeps_same_boot_boundary(actual_rows,current,mono,expected):
    _,policy,observed,*_=actual_rows
    value=deepcopy(observed)
    value.update(current_host_boot_id=current,policy_expired_at_observation=True,
        observation_original_monotonic_us=mono)
    assert rpc.historical_monotonic_expired(policy,'unit-b',value,880000000)is expected
    assert value['observation_original_monotonic_us']==mono


@pytest.mark.parametrize('fault',['current_boot','boolean_mono','live_policy'])
def test_retired_observation_requires_typed_current_boot_clock_and_expiry(actual_rows,fault):
    _,policy,observed,*_=actual_rows
    value=deepcopy(observed)
    value.update(current_host_boot_id=NEW_BOOT,policy_expired_at_observation=True,
        observation_original_monotonic_us=1000)
    if fault=='current_boot':value['current_host_boot_id']='not-a-boot'
    elif fault=='boolean_mono':value['observation_original_monotonic_us']=True
    else:value['policy_expired_at_observation']=False
    with pytest.raises((RuntimeError,ValueError)):
        rpc.historical_monotonic_expired(policy,'unit-b',value,880000000)


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_shared_retired_context_and_paid_caps_keep_original_values_after_reboot(observed_original,unit):
    from lantern_meshticulum.bench import gfsk250_mb1_partial_history as history
    values,policy=observed_original
    observed=deepcopy(values['native'][unit]);row=values['history_rows'][unit]
    original=rpc.canonical(row)
    observed.update(current_host_boot_id=NEW_BOOT,observation_original_monotonic_us=1000)
    context=history.retained_context(row['owner'],policy,unit=unit,observation=observed)
    rpc.qualify_gfsk250_mb1_partial_costs(unit,policy,observed,row)
    assert context.transport is not None and rpc.canonical(row)==original
    assert observed['observation_original_monotonic_us']==1000


def test_pinned_retired_capture_accepts_new_boot_without_rebasing(tmp_path,monkeypatch,synthetic_8120_capture):
    from lantern_meshticulum.bench import retained_capture as retained,capture_8120
    from lantern_meshticulum.bench.pilot_bundle import COMMITTED_CONTRACTS
    from lantern_meshticulum.bench.profiles import GFSK250_FAST_BUNDLE_KIND
    tmp_path.chmod(0o700);(tmp_path/'parents').mkdir(mode=0o700)
    old_sample=(10000000,5000000)
    row=synthetic_8120_capture(tmp_path,'unit-c',lambda:old_sample,boot=BOOT)
    original=deepcopy(row)
    monkeypatch.setattr(retained,'current_boot',lambda:NEW_BOOT)
    assert retained._artifact_present(tmp_path,'unit-c',lambda:(10000001,1000),capture_8120.PARENT,
        row['files'],BOOT,capture_8120.START,capture_8120.END,capture_8120.REVIEW_SHA256,True,row['phase'],
        expected_schema=COMMITTED_CONTRACTS[GFSK250_FAST_BUNDLE_KIND][6],
        expected_history_sha256=row['history_sha256'],absolute_end=capture_8120.ABSOLUTE_END) is True
    assert row==original


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_actual_h45_unmaterialized_costs_survive_current_boot_change(h45_inputs,unit):
    from lantern_meshticulum.bench import fast_bundle_unstarted_history as history
    values=deepcopy(h45_inputs);row=values['latest'][unit]
    policy=rpc.ParentPolicy.from_dict(row['policy']);original=rpc.canonical(row)
    observed=values['observed'][unit]['observation']
    observed.update(current_host_boot_id=NEW_BOOT,observation_original_monotonic_us=1000)
    history.qualify_unmaterialized_costs(unit,policy,observed,row)
    assert rpc.canonical(row)==original and observed['observation_original_monotonic_us']==1000


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_actual_h46_complete_costs_survive_current_boot_change(h46_inputs,unit):
    _,_,_,_,native,latest=deepcopy(h46_inputs)
    row=latest[unit];policy=rpc.ParentPolicy.from_dict(row['policy']);original=rpc.canonical(row)
    observed=native[unit]['observation']
    observed.update(current_host_boot_id=NEW_BOOT,observation_original_monotonic_us=1000)
    rpc.qualify_gfsk250_mb1_partial_costs(unit,policy,observed,row)
    assert rpc.canonical(row)==original and observed['observation_original_monotonic_us']==1000
