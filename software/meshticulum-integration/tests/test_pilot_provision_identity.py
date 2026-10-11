# SPDX-License-Identifier: Apache-2.0
"""Real local preparation/FD controls; synthetic facts, no native qualification."""
import builtins
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

import pytest
from lantern_meshticulum.bench import pilot_provision, resume_parent_guard as guard
from lantern_meshticulum.bench import pilot_parent_snapshot as snapshots
from lantern_meshticulum.bench.history_identity_seal import capture as capture_history
from lantern_meshticulum.bench.legacy_grant_fence import capture as capture_legacy
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps, NativeFacts
from lantern_meshticulum.bench.pilot_parent_caps import canonical, ParentPolicy, ParentCaps, UnitCaps, Cost
from meshticulum.contracts.identity import PeerID
from lantern_meshticulum.ip.node import clock, _write
from tests.test_ip_node import facts, BOOT
from tests.test_pilot_provision import request as public_request
from tests.test_pilot_parent_snapshot import root_observation


@pytest.fixture
def setup(request):
    # The guard refuses writable ancestors; pytest's system temp is unsuitable.
    base = Path(tempfile.mkdtemp(prefix='.provision-control-', dir=Path(__file__).parent))
    root = base / 'state'; root.mkdir(mode=0o700)
    for name in ('parents', 'runs', 'campaigns'): (root/name).mkdir(mode=0o700)
    for identity in guard.HISTORY: (root/'parents'/identity).mkdir(mode=0o700)
    old = root/'runs'/('e'*32); old.mkdir(mode=0o700)
    grant = dict(schema='meshticulum.bench-run.v1',unit='unit-a',session_id='e'*32,
        host_boot_id=BOOT,issued_epoch_us=1,expires_epoch_us=2,
        profile=dict(max_boot_attempts=256,max_boot_estimated_airtime_us=60000000))
    raw=canonical(grant); _write(old/'grant.json',raw); _write(old/'active',b'retired\n')
    row=dict(run_id='e'*32,schema=grant['schema'],grant_sha256=hashlib.sha256(raw).hexdigest(),
        host_boot_id=BOOT,original_issued_epoch_us=1,original_expires_epoch_us=2,active_marker='retired',
        caps=dict(radio_attempts=256,radio_estimated_airtime_us=60000000,ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0))
    legacy,_=capture_legacy(root/'runs',owner_uid=os.geteuid(),expected_rows=[row],
        independently_verified=True,exact_namespace_gate=lambda: list((root/'runs').iterdir())==[old])
    dirs={'':['parents','runs','campaigns'],'parents':list(guard.HISTORY),'runs':['e'*32]}
    dirs.update({'parents/'+identity:[] for identity in guard.HISTORY})
    strict,_=capture_history(root,owner_uid=os.geteuid(),expected_files={},expected_directories=dirs)
    parent=root/'parents'/('1'*32); parent.mkdir(mode=0o700)
    _,binding=guard.install_seals(root,'1'*32,'unit-a',strict,legacy)
    legacy_mode=getattr(request,'param',None)=='legacy'
    declaration={} if legacy_mode else dict(history_binding_sha256=binding)
    if legacy_mode: (parent/'history-binding.json').unlink()
    large_mode=getattr(request,'param',None)=='large'
    padding=dict(inventory_fixture='x'*32581) if large_mode else {}
    prior=canonical(dict(declaration,local_control_only=True,**padding)); prior_sha=hashlib.sha256(prior).hexdigest()
    registry={u:dict(observation_sha256=prior_sha) for u in ('unit-a','unit-b')}
    epoch,mono=clock(); limit=Cost(1024,240000000,8192,8388608,8388608)
    units=tuple(UnitCaps(u,BOOT,PeerID('local-provision-control',p*64),limit,Cost(),prior_sha)
                for u,p in [('unit-a','a'),('unit-b','b')])
    policy=ParentPolicy('1'*32,BOOT,epoch-1000001,epoch+30000000,mono-1,mono+30000000,units,
                        limit.plus(limit),hashlib.sha256(canonical(registry)).hexdigest())
    for name,raw in [('owner.json',canonical(dict(declaration,policy_digest=policy.digest,
        prior_registry=registry,**padding))),('prior.json',prior),('policy.json',canonical(policy.to_dict()))]: _write(parent/name,raw)
    payload=public_request()
    pair=pair_packages(campaign_id='c'*32,resource_sha256='a'*64,application_expires_epoch_us=epoch+20000000,
        sender='unit-a',receiver='unit-b',transport='tcp',format='MR1.request',
        descriptor=dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest()),source=None,request=payload,
        peers={u.unit:asdict(u.peer) for u in units},boots={u.unit:BOOT for u in units},now_epoch_us=epoch,
        endpoints={'unit-a':('127.0.0.1',35001),'unit-b':('127.0.0.1',35002)},
        radio_permission=dict(country='MY',antenna_confirmed=True))
    (base/'controller').mkdir(mode=0o700)
    controller=ParentCaps.enroll(base/'controller'/'caps.sqlite',policy,clock=clock,boot_reader=lambda:BOOT)
    try:
        permit=controller.prepay_pair(list(pair['packages'].values())); children=controller.consume(permit)
        child=next(item for item in children if item.unit=='unit-a')
    finally: controller.close()
    native=NativeCaps.enroll(parent/'native.sqlite',policy,unit='unit-a',clock=clock,
        facts=lambda:NativeFacts('unit-a',BOOT,units[0].peer),approve=lambda _:True,prior_check=lambda _:True,
        history_binding_sha256=None if legacy_mode else binding)
    assert native.history_binding_sha256 == (None if legacy_mode else binding)
    native.close()
    value=pair['packages']['unit-a']; value.update(schema='meshticulum.public-pilot-package.v2',parent=dict(
        schema='meshticulum.public-parent-allocation.v1',parent_id=policy.parent_id,policy_digest=policy.digest,
        allocation_id=permit.key,child=child.to_dict()))
    try:
        yield root, value, parent
    finally:
        shutil.rmtree(base)


def guarded(parent):
    assert (parent / 'history-binding.json').is_file()


def ledger(parent, value):
    with sqlite3.connect(parent / 'native.sqlite') as db:
        claims = db.execute('SELECT body FROM claims').fetchall()
        assert len(claims) == 1
        assert json.loads(claims[0][0])['child']['cost'] == value['parent']['child']['cost']
        assert db.execute('SELECT COUNT(*) FROM prepared').fetchone() == (1,)
        assert db.execute('SELECT COUNT(*) FROM starts').fetchone() == (0,)
    db.close()


def run(root, value):
    return pilot_provision.provision(value, state=root, facts=facts('unit-a'), clock=clock)


def test_guard_capture_uses_same_finalized_preparation_and_original_fd(setup, monkeypatch):
    root, value, parent = setup; guarded(parent)
    record_prepared = guard.record_prepared
    observed = {}
    original_open = os.open
    def open_original(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if Path(path).name.startswith('.pilot-') and flags & os.O_DIRECTORY:
            observed.update(staging_fd=fd, staging_identity=(os.fstat(fd).st_dev, os.fstat(fd).st_ino))
        return fd
    monkeypatch.setattr(pilot_provision.os, 'open', open_original)
    def capture(state, caps, preparation, held_staging_fd):
        assert type(caps) is NativeCaps and preparation._owner is caps
        assert caps._preparations[id(preparation)][-1] is True
        held = os.fstat(held_staging_fd)
        assert held_staging_fd == observed['staging_fd']
        assert (held.st_dev, held.st_ino) == observed['staging_identity']
        assert held.st_ino == (root / 'runs' / preparation.run_id).lstat().st_ino
        record = record_prepared(state, caps, preparation, held_staging_fd)
        observed.update(fd=held_staging_fd, record=record)
        return record
    monkeypatch.setattr(guard, 'record_prepared', capture)
    result = run(root, value)
    directory = root / 'runs' / result['run_id']
    persisted = json.loads((directory / 'provision.json').read_bytes())
    record = json.loads((parent / 'prepared-identities' / (result['run_id'] + '.json')).read_bytes())
    assert record == observed['record']
    assert record['directory_identity'][:2] == [directory.stat().st_dev, directory.stat().st_ino]
    assert result['prepared_identity_sha256'] == hashlib.sha256(canonical(record)).hexdigest()
    assert 'prepared_identity_sha256' not in persisted
    assert persisted['parent'] == result['parent']
    with pytest.raises(OSError): os.fstat(observed['fd'])
    ledger(parent, value)


@pytest.mark.parametrize('when', ['rename', 'hook'])
def test_original_directory_replacement_refuses_without_refund(setup, monkeypatch, when):
    root, value, parent = setup; guarded(parent)
    rename, capture = os.rename, guard.record_prepared
    def swap(target):
        target.rename(root / 'retained-original')
        target.mkdir(mode=0o700)
    if when == 'rename':
        def replace(source, target, *args, **kwargs):
            rename(source, target, *args, **kwargs)
            if Path(source).name.startswith('.pilot-'): swap(Path(target))
        monkeypatch.setattr(pilot_provision.os, 'rename', replace)
    else:
        def replace(state, caps, preparation, held_staging_fd):
            record = capture(state, caps, preparation, held_staging_fd)
            swap(root / 'runs' / preparation.run_id)
            return record
        monkeypatch.setattr(guard, 'record_prepared', replace)
    with pytest.raises(Exception, match='pilot_prepared_directory_rebound'):
        run(root, value)
    assert (root / 'retained-original' / 'provision.json').is_file()
    ledger(parent, value)


@pytest.mark.parametrize('missing', ['module', 'function'])
def test_missing_guard_import_refuses_retaining_paid_preparation(setup, monkeypatch, missing):
    root, value, parent = setup; guarded(parent)
    if missing == 'function': monkeypatch.delattr(guard, 'record_prepared')
    else:
        original = builtins.__import__
        def refuse(name, *args, **kwargs):
            if name == 'resume_parent_guard': raise ImportError('missing_reviewed_guard')
            return original(name, *args, **kwargs)
        monkeypatch.setattr(builtins, '__import__', refuse)
    with pytest.raises(ImportError): run(root, value)
    ledger(parent, value)
    assert (root / 'runs' / value['grant']['session']['session_id'] / 'provision.json').is_file()
    assert not list((parent / 'prepared-identities').iterdir())


@pytest.mark.parametrize('setup',['legacy'],indirect=True)
def test_unguarded_original_parent_does_not_call_capture(setup, monkeypatch):
    root, value, parent = setup
    def refuse(*args, **kwargs): raise AssertionError('unexpected_guard')
    monkeypatch.setattr(guard, 'record_prepared', refuse)
    result = run(root, value)
    assert 'prepared_identity_sha256' not in result
    assert not list((parent / 'prepared-identities').iterdir())
    ledger(parent, value)


@pytest.mark.parametrize('setup',[None,'large'],indirect=True)
@pytest.mark.parametrize('changed', [False, True])
def test_actual_prepared_child_full_guard_and_digest_refusal(setup, changed):
    root,value,parent=setup; result=run(root,value)
    policy=ParentPolicy.from_dict(json.loads((parent/'policy.json').read_bytes()))
    pins={key:hashlib.sha256((parent/name).read_bytes()).hexdigest() for key,name in
          [('owner_sha256','owner.json'),('prior_sha256','prior.json'),('binding_sha256','history-binding.json')]}
    digest=result['prepared_identity_sha256'] if not changed else '0'*64
    kwargs=dict(preparation_sha256={result['run_id']:digest},management_phase='prelaunch',clock=clock,
        facts=lambda:NativeFacts('unit-a',BOOT,policy.unit('unit-a').peer),approve=lambda _:True,runtime_check=lambda:True)
    if changed:
        with pytest.raises(guard.FenceError,match='captured_preparation_digest_changed'):
            guard.ResumeParentGuard(root,policy,'unit-a',**pins,**kwargs)
    else:
        with guard.ResumeParentGuard(root,policy,'unit-a',**pins,**kwargs) as current:
            assert current.check()['claims']==1
    ledger(parent,value)


@pytest.mark.parametrize('case', ['missing_binding','missing_owner','missing_prior','owner_declaration',
    'prior_declaration','wrong_owner','wrong_prior','invalid_digest','duplicate','corrupt','symlink','mode','delete_all'])
def test_guarded_declaration_cannot_downgrade_and_debt_is_retained(setup, case):
    root,value,parent=setup
    path=parent/('owner.json' if case in ('missing_owner','owner_declaration','wrong_owner','invalid_digest','duplicate')
                 else 'prior.json' if case in ('missing_prior','prior_declaration','wrong_prior') else 'history-binding.json')
    if case=='delete_all':
        for name in ('owner.json','prior.json','history-binding.json'): (parent/name).unlink()
    elif case.startswith('missing'): path.unlink()
    elif case in ('owner_declaration','prior_declaration','wrong_owner','wrong_prior','invalid_digest'):
        data=json.loads(path.read_bytes())
        if case.endswith('declaration'): del data['history_binding_sha256']
        else: data['history_binding_sha256']='0'*64 if case!='invalid_digest' else None
        path.write_bytes(canonical(data))
    elif case=='duplicate': path.write_bytes(b'{"history_binding_sha256":"a","history_binding_sha256":"b"}')
    elif case=='corrupt': path.write_bytes(b'not JSON')
    elif case=='symlink': path.rename(parent/'retained-binding'); path.symlink_to(parent/'retained-binding')
    else: path.chmod(0o644)
    with pytest.raises((ValueError,OSError)): run(root,value)
    ledger(parent,value)
    assert not list((parent/'prepared-identities').iterdir())


@pytest.mark.parametrize('tamper',[None,'missing_tag','invalid_tag','downgrade'])
def test_genuine_guarded_enrollment_readonly_snapshot_validation(setup,request,tamper):
    root,value,parent=setup; run(root,value)
    path=parent/'native.sqlite'; policy=ParentPolicy.from_dict(json.loads((parent/'policy.json').read_bytes()))
    with sqlite3.connect(path) as db:
        original=json.loads(db.execute('SELECT body FROM enrollment').fetchone()[0])
        binding=original['history_binding_sha256']
        assert original['schema']=='meshticulum.native-parent-enrollment.v2'
        assert (parent/'native.sqlite.approval').read_bytes()==(snapshots._native_hash(original)+'\n').encode()
        if tamper:
            if tamper!='invalid_tag': del original['history_binding_sha256']
            else: original['history_binding_sha256']='invalid'
            if tamper=='downgrade': original['schema']='meshticulum.native-parent-enrollment.v1'
            db.execute('UPDATE enrollment SET body=?',(canonical(original),))
    db.close()
    # Model root observation only AFTER real local enrollment/provision closes.
    request.getfixturevalue('root_observation')
    scope=snapshots.Scope('meshticulum.public-parent-snapshot-scope.v1','native',0,
        policy.parent_id,policy.digest,hashlib.sha256(path.read_bytes()).hexdigest(),'unit-a',BOOT,clock()[0]+10000000)
    report=snapshots.collect(root,scope,clock=clock,boot_reader=lambda:BOOT).to_dict()
    if tamper in ('missing_tag','invalid_tag'): assert report['status']=='unavailable'
    elif tamper=='downgrade':
        assert report['detail'] is not None, report.get('reason')
        assert report['detail']['approval_bytes_match_enrollment'] is False
    else:
        assert report['status']=='scoped_rows_observed', report.get('reason')
        assert report['detail']['enrollment']['history_binding_sha256']==binding
        assert report['detail']['approval_bytes_match_enrollment'] is True
        assert report['detail']['prepared_count']==1
    assert report['eligible_for_enrollment'] is False
