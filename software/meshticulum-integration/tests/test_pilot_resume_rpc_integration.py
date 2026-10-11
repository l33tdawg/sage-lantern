# SPDX-License-Identifier: Apache-2.0
"""Real local NativeCaps/guard integration with explicit modeled external facts.

NOT native qualification: boot/services/runtime and old typed-history collection
are modeled boundaries. Local protected files, SQLite enrollment/approval,
immutable seals, real NativeCaps and ResumeParentGuard execute unchanged.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.history_identity_seal import capture
from lantern_meshticulum.bench.legacy_grant_fence import capture as legacy_capture
from lantern_meshticulum.bench.resume_parent_guard import install_seals
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,UnitCaps,Cost,canonical
from meshticulum.contracts.identity import PeerID

BOOT = '11111111-1111-4111-8111-111111111111'
PARENT, OLD = 'b'*32, 'c'*32


class LocalRuntimeModel:
    """Explicit source-control stub, never an installed runtime attestation."""
    def __init__(self,root):
        self.root = root/('runtime-'+('a'*16)); self.root.mkdir(mode=0o700)
        self.reader = rpc.Reader(); self.checks = 0
        rpc.write_new(self.root,'manifest.json',b'{"local_control_only":true}')
    def check(self): self.checks += 1; return self.reader.check()


@pytest.fixture
def local_scope(monkeypatch,request):
    root = Path(tempfile.mkdtemp(prefix='rpc-integration-',dir=Path(__file__).parent)); root.chmod(0o700)
    request.addfinalizer(lambda:shutil.rmtree(root,ignore_errors=True))
    state = root/'state'; state.mkdir(mode=0o700)
    for name in ('parents','runs'): (state/name).mkdir(mode=0o700)
    for parent in rpc.HISTORY: (state/'parents'/parent).mkdir(mode=0o700)
    old = state/'runs'/OLD; old.mkdir(mode=0o700)
    g = dict(schema='meshticulum.bench-run.v1',unit='unit-a',session_id=OLD,host_boot_id=BOOT,
        issued_epoch_us=1,expires_epoch_us=2,profile=dict(max_boot_attempts=256,max_boot_estimated_airtime_us=60000000))
    rpc.write_new(old,'grant.json',canonical(g)); rpc.write_new(old,'active',b'retired\n')
    row = dict(run_id=OLD,schema=g['schema'],grant_sha256=rpc.sha(canonical(g)),host_boot_id=BOOT,
        original_issued_epoch_us=1,original_expires_epoch_us=2,active_marker='retired',actual_cost=None,
        caps=dict(radio_attempts=256,radio_estimated_airtime_us=60000000,ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0))
    directories = {'':['parents','runs'],'parents':list(rpc.HISTORY),'runs':[OLD]}
    directories.update({'parents/'+p:[] for p in rpc.HISTORY})
    strict,_ = capture(state,owner_uid=os.geteuid(),expected_files={},expected_directories=directories)
    legacy,_ = legacy_capture(state/'runs',owner_uid=os.geteuid(),expected_rows=[row],
        independently_verified=True,exact_namespace_gate=lambda:sorted(os.listdir(state/'runs')) == [OLD])
    target = state/'parents'/PARENT; target.mkdir(mode=0o700)
    _,binding = install_seals(state,PARENT,'unit-a',strict,legacy)
    runtime = LocalRuntimeModel(root); now,mono = rpc.clock(); end = now+300000000
    facts = dict(unit='unit-a',host_boot_id=BOOT,services={},service_lifetimes={})
    inventory = dict(rows=[row],complete_global_debt=False,actual_totals=None)
    paid = asdict(Cost(256,60000000)); history = dict(previous=[dict(paid_caps=paid)],legacy_inventory=inventory)
    phase = dict(epoch_us=now,monotonic_us=mono,deadline_monotonic_us=mono+300000000)
    saved = dict(schema='meshticulum.private-resume-actual-capture.v1',unit='unit-a',parent_id=PARENT,
        history=history,history_binding_sha256=binding,review_sha256='d'*64,facts=facts,native_phase=phase,
        phase_start_epoch_us=now,phase_end_epoch_us=end,actual_history_verified=True)
    rpc.write_new(target,'capture.json',canonical(saved))
    prior = dict(schema='meshticulum.public-resume-conservative-prior.v1',history_binding_sha256=binding,
        legacy_inventory=inventory,full_caps=paid,complete_global_debt=False,actual_cost=None,refunds=0)
    registry = {u:dict(observation_sha256=rpc.sha(canonical(prior)),full_caps=paid) for u in ('unit-a','unit-b')}
    units = tuple(UnitCaps(u,BOOT,PeerID('meshticulum-public-pilot-v1',rpc.sha(
        ('meshticulum-public-pilot-v1/'+u+'/'+BOOT).encode())),Cost(**paid),Cost(**paid),
        rpc.sha(canonical(prior))) for u in registry)
    policy = ParentPolicy(PARENT,BOOT,now,end-5000000,mono,mono+295000000,units,
        Cost(**paid).plus(Cost(**paid)),rpc.sha(canonical(registry)))
    owner = dict(schema='meshticulum.finite-public-resume-owner.v1',policy_digest=policy.digest,
        authorization=dict(human_statement='run it bro',count_authorized=1,public_only=True,private_production=False),
        review_sha256='d'*64,history_binding_sha256=binding,capture_record_sha256=rpc.sha(canonical(saved)),
        prior_registry=registry,facts={'unit-a':facts},native_phase={'unit-a':phase},phase_start_epoch_us=now,
        phase_end_epoch_us=end,runtime_sha256='a'*64,manifest_sha256=rpc.sha(b'{"local_control_only":true}'),
        rf_record_sha256='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9',
        rf_record=dict(country='MY',C_transmission_allowed=True,A_B_transmission_allowed=True,
            units={u:dict(antenna_connected=True) for u in ('unit-a','unit-b','unit-c')}),rf_profile=asdict(rpc.profile_for('sf7-500')))
    calls = []
    def modeled_history(reader,unit,value,guard,**scope):
        calls.append(('explicit_modeled_old_typed_collection',scope)); guard()
        assert value == history and unit == 'unit-a'; return {},{},[],[]
    def modeled_collect(state_path,scope,**kwargs):
        # Actual stored enrollment bytes are inspected; root-only typed
        # Snapshot provenance is deliberately NOT asserted by this model.
        raw = (state_path/'parents'/PARENT/'native.sqlite').read_bytes()
        assert rpc.sha(raw) == scope.database_sha256
        calls.append(('explicit_modeled_root_collector',scope.parent_id))
        class Result:
            def to_dict(self):
                return dict(status='scoped_rows_observed',scoped_rows_complete=True,pending_unknown=False,
                    detail=dict(approval_bytes_match_enrollment=True,enrollment=dict(history_binding_sha256=binding),
                        claim_count=0,prepared_count=0,start_count=0,claims=[]),local_control_only=True,native_qualification=False)
        return Result()
    monkeypatch.setattr(rpc,'STATE',state); monkeypatch.setattr(rpc,'boot',lambda:BOOT)
    # The RPC is Linux-only. Model its proc FD inventory with this local
    # process's real /dev/fd entries; descriptor capacity has separate controls.
    original_listdir=rpc.os.listdir
    monkeypatch.setattr(rpc.os,'listdir',lambda path:original_listdir(
        '/dev/fd' if str(path)=='/proc/self/fd' else path))
    monkeypatch.setattr(rpc,'native_facts',lambda unit:dict(facts)); monkeypatch.setattr(rpc,'full_history',modeled_history)
    monkeypatch.setattr(rpc,'collect',modeled_collect)
    # Deliberately JSON round-trip: actual native RPC receives lists, while
    # ParentPolicy.to_dict retains tuples. This exercises the real callback.
    payload = json.loads(canonical(dict(policy=policy.to_dict(),owner=owner,prior=prior,
        preparation_sha256={},review_sha256='d'*64)))
    try: yield state,runtime,payload,calls,binding
    finally: runtime.reader.close(); shutil.rmtree(root)


def test_actual_enroll_guard_audit_under_explicit_local_models(local_scope):
    state,runtime,p,calls,binding = local_scope
    first = rpc.enrolled_action('enroll','unit-a',p,runtime)
    assert first['snapshot']['claims'] == first['snapshot']['prepared'] == first['snapshot']['starts'] == 0
    guard = rpc.enrolled_action('guard','unit-a',p,runtime)
    audit = rpc.enrolled_action('audit','unit-a',p,runtime)
    for action, actual in (('enroll',first),('guard',guard),('audit',audit)):
        wrapped = rpc.response_metadata(action,'unit-a',actual)
        assert wrapped['schema']==rpc.CONTRACT_SCHEMA and wrapped['action']==action
        assert wrapped['global_debt_complete'] is False and all(wrapped[k]==v for k,v in actual.items())
    assert first['snapshot'] == guard['snapshot'] == audit['snapshot']
    assert not audit['snapshot']['uncertain'] and audit['terminal_observation']['local_control_only']
    assert len([c for c in calls if c[0]=='explicit_modeled_old_typed_collection']) == 2
    assert runtime.checks > 0
    # Open genuine protected enrollment; classification must be v2 and bound.
    import sqlite3
    db = sqlite3.connect(state/'parents'/PARENT/'native.sqlite')
    try:
        enrollment = json.loads(db.execute('SELECT body FROM enrollment WHERE id=1').fetchone()[0])
        assert enrollment['schema'] == 'meshticulum.native-parent-enrollment.v2'
        assert enrollment['history_binding_sha256'] == binding
    finally: db.close()


def test_enroll_refuses_wrong_independent_capture_pin(local_scope):
    _,runtime,p,_,_ = local_scope; p['owner']['capture_record_sha256'] = 'f'*64
    with pytest.raises(RuntimeError,match='actual_capture_record_changed'):
        rpc.enrolled_action('enroll','unit-a',p,runtime)


def test_guard_refuses_stale_actual_approval_bytes(local_scope):
    state,runtime,p,_,_ = local_scope
    rpc.enrolled_action('enroll','unit-a',p,runtime)
    approval = state/'parents'/PARENT/'native.sqlite.approval'
    approval.write_bytes(b'0'*64+b'\n')
    with pytest.raises(RuntimeError): rpc.enrolled_action('guard','unit-a',p,runtime)


@pytest.mark.parametrize('attack',['stale_approval','wrong_tag','missing_start','omitted_run','altered_plan','unresolved'])
def test_terminal_qualification_rejects_explicit_modeled_bad_collect_results(attack):
    # Explicitly modeled typed output exercises qualification predicates only;
    # it supplies no Snapshot or native authority and runs no collector.
    observed = dict(detail=dict(approval_bytes_match_enrollment=True,enrollment=dict(history_binding_sha256='a'*64),
        claim_count=1,prepared_count=1,start_count=1,claims=[dict(child=dict(run_id=OLD))]),
        legacy_relation=dict(scoped_linkage_complete=True,native_claims_without_scoped_actuals=[],
            links=[dict(run_id=OLD,relation='same_endpoint_allocation',no_refund=True,separate_debt_sum_permitted=False)]))
    if attack == 'stale_approval': observed['detail']['approval_bytes_match_enrollment'] = False
    elif attack == 'wrong_tag': observed['detail']['enrollment']['history_binding_sha256'] = 'f'*64
    elif attack == 'missing_start': observed['detail']['start_count'] = 0
    elif attack == 'omitted_run': observed['legacy_relation']['native_claims_without_scoped_actuals'] = [OLD]
    else:
        observed['legacy_relation']['scoped_linkage_complete'] = False
        observed['legacy_relation']['links'][0].update(relation='unresolved',reason='legacy_plan_correspondence_mismatch' if attack=='altered_plan' else 'legacy_rows_unavailable')
    with pytest.raises(RuntimeError): rpc.qualify_terminal(observed,dict(history_binding_sha256='a'*64),{OLD:'b'*64})
