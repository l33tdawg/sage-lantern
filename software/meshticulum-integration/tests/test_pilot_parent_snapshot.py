# SPDX-License-Identifier: Apache-2.0
"""Held-file/SQLite source controls; native UID and boot are modeled explicitly.

Fixture databases use the published source schemas. No authority, ParentCaps,
NativeCaps, driver, device or network constructor is used. These controls prove
local read-only behavior and scoped source correspondence, not native authority.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from meshticulum.contracts.identity import PeerID
from meshticulum.resources.resource import encode_request
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import Cost, UnitCaps, ParentPolicy, child_caps, DOMAIN
from lantern_meshticulum.bench import pilot_debt_snapshot as debt
from lantern_meshticulum.bench import pilot_parent_snapshot as module

BOOT = '00000000-0000-4000-8000-000000000001'
OTHER_BOOT = '00000000-0000-4000-8000-000000000002'
LIMIT = Cost(1024, 240000000, 8192, 4194304, 4194304)


class RootStat:
    def __init__(self, value):
        self.value, self.st_uid = value, 0
    def __getattr__(self, key):
        return getattr(self.value, key)


@pytest.fixture
def root_observation(monkeypatch):
    real_stat, real_fstat = os.stat, os.fstat
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    monkeypatch.setattr(os, 'stat', lambda *a, **k: RootStat(real_stat(*a, **k)))
    monkeypatch.setattr(os, 'fstat', lambda *a, **k: RootStat(real_fstat(*a, **k)))
    monkeypatch.setattr(os, 'lstat', lambda *a, **k: RootStat(real_stat(*a, follow_symlinks=False, **k)))


def write(path, value):
    path.write_bytes(value if type(value) is bytes else module.canonical(value))
    path.chmod(0o600)


def database(path, statements, rows):
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        for statement in statements:
            db.execute(statement)
        for query, values in rows:
            db.executemany(query, values)
        db.commit()
    finally:
        db.close()
    path.chmod(0o600)


def policy():
    units = tuple(UnitCaps(u, BOOT, PeerID('public-parent-observation-fixture', c*64), LIMIT,
        Cost(1, 1000, 1, 32, 32), c*64) for u, c in zip(('unit-a', 'unit-b'), 'ab'))
    return ParentPolicy('1'*32, BOOT, 90000000, 200000000, 40000000, 150000000,
                        units, LIMIT.plus(LIMIT), 'c'*64)


def fixture(tmp_path, transport='tcp', *, started=True, prepared=True, transform=None):
    p = policy()
    request = encode_request('/packs/'+'e'*64+'/chunks/'+'f'*64+'.json', 0, 16356)
    pair = pair_packages(campaign_id='d'*32, resource_sha256='e'*64, application_expires_epoch_us=180000000,
        sender='unit-a', receiver='unit-b', transport=transport, format='MR1.request',
        descriptor=dict(bytes=len(request), sha256=module.sha(request)), source=None, request=request,
        peers={u.unit: asdict(u.peer) for u in p.units}, boots={u.unit: u.host_boot_id for u in p.units},
        now_epoch_us=100000000, endpoints={'unit-a': ('127.0.0.1', 35001), 'unit-b': ('127.0.0.1', 35002)},
        radio_permission=dict(country='MY', antenna_confirmed=True))
    children = [child_caps(pair['packages'][u.unit], p).to_dict() for u in p.units]
    state = dict(policy=p, packages=pair['packages'], children=children,
                 controller=tmp_path/'public-controller', natives={}, allocation_id=None)
    state['controller'].mkdir(mode=0o700)
    if transform:
        transform(state)
    p = state['policy']
    raw = module.canonical(state['children'])
    aid = module.sha(DOMAIN+b'pair\0'+raw)
    state['allocation_id'] = aid
    controller = state['controller']/'caps.sqlite'
    database(controller, [
        'CREATE TABLE policy (id INTEGER PRIMARY KEY CHECK(id=1), body BLOB, digest TEXT)',
        'CREATE TABLE clock (id INTEGER PRIMARY KEY CHECK(id=1), epoch INTEGER, mono INTEGER)',
        'CREATE TABLE allocations (id TEXT PRIMARY KEY, run_id TEXT UNIQUE, body BLOB NOT NULL)'], [
        ('INSERT INTO policy VALUES(?,?,?)', [(1, module.canonical(p.to_dict()), p.digest)]),
        ('INSERT INTO clock VALUES(?,?,?)', [(1, 100000000, 50000000)]),
        ('INSERT INTO allocations VALUES(?,?,?)', [(aid, children[0]['metadata']['run_id'], raw)])])
    write(Path(str(controller)+'.owner'), b'')
    for child in children:
        unit = child['metadata']['unit']; root = tmp_path/unit; root.mkdir(mode=0o700)
        parents = root/'parents'; parents.mkdir(mode=0o700)
        directory = parents/p.parent_id; directory.mkdir(mode=0o700)
        path = directory/'native.sqlite'
        enrollment = dict(schema='meshticulum.native-parent-enrollment.v1', policy=p.to_dict(), unit=unit,
            host_boot_id=BOOT, original_epoch_us=100000000, original_monotonic_us=800000000,
            deadline_monotonic_us=900000000)
        resource = dict(resource_sha256='e'*64, application_expires_epoch_us=180000000,
            original_epoch_us=100000000, original_monotonic_us=800000000,
            application_deadline_monotonic_us=880000000)
        rid = module._native_hash(dict(parent_id=p.parent_id, unit=unit, resource_sha256='e'*64))
        claim = dict(child=child, controller_allocation_id=aid, resource_id=rid, resource=resource)
        cid = module._native_hash(claim)
        m = child['metadata']; plan = deepcopy(pair['packages'][unit]['grant'])
        plan['application_deadline_monotonic_us'] = 879999000
        receipt = dict(schema='meshticulum.native-parent-claim.v1', parent_id=p.parent_id, policy_digest=p.digest,
            controller_allocation_id=aid, native_claim_id=cid, unit=unit, host_boot_id=BOOT,
            run_id=m['run_id'], resource_sha256=m['resource_sha256'],
            application_expires_epoch_us=m['application_expires_epoch_us'], application_deadline_monotonic_us=879999000,
            child_caps=child, original_plan_sha256=m['plan_sha256'], native_plan_sha256=module.sha(module.canonical(plan)),
            secret_sha256=m['secret_sha256'], grant_context_sha256=m['grant_digest'], prepared=True)
        statements = [
            'CREATE TABLE enrollment(id INTEGER PRIMARY KEY CHECK(id=1),body BLOB NOT NULL)',
            'CREATE TABLE clock(id INTEGER PRIMARY KEY CHECK(id=1),epoch INTEGER,mono INTEGER)']
        for table in ('resources', 'claims', 'prepared', 'starts'):
            statements.append('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,body BLOB NOT NULL'+
                              (',run_id TEXT UNIQUE' if table == 'claims' else '')+')')
            for action in ('UPDATE', 'DELETE'):
                name = 'immutable_'+table+'_'+action
                statements.append('CREATE TRIGGER '+name+' BEFORE '+action+' ON '+table+
                                  " BEGIN SELECT RAISE(ABORT,'append_only'); END")
        rows = [('INSERT INTO enrollment VALUES(?,?)', [(1, module.canonical(enrollment))]),
                ('INSERT INTO clock VALUES(?,?,?)', [(1, 100000000, 800000000)]),
                ('INSERT INTO resources VALUES(?,?)', [(rid, module.canonical(resource))]),
                ('INSERT INTO claims VALUES(?,?,?)', [(cid, module.canonical(claim), m['run_id'])])]
        if prepared:
            rows.append(('INSERT INTO prepared VALUES(?,?)', [(cid, module.canonical(receipt))]))
        if started:
            rows.append(('INSERT INTO starts VALUES(?,?)', [(cid, module.canonical(dict(receipt,
                schema='meshticulum.native-parent-start.v1', started=True)))]))
        database(path, statements, rows)
        write(Path(str(path)+'.owner'), b'')
        write(Path(str(path)+'.approval'), (module._native_hash(enrollment)+'\n').encode())
        state['natives'][unit] = dict(state=root, path=path, claim=claim, claim_id=cid,
            receipt=receipt, plan=plan, child=child, enrollment=enrollment)
    return state


def scope(data, role='native', unit='unit-a', owner_uid=0):
    path = data['controller']/'caps.sqlite' if role == 'controller' else data['natives'][unit]['path']
    p = data['policy']
    return module.Scope('meshticulum.public-parent-snapshot-scope.v1', role, owner_uid, p.parent_id,
        p.digest, module.sha(path.read_bytes()), None if role == 'controller' else unit, BOOT, 250000000)


def collect(data, role='native', unit='unit-a', **kwargs):
    state = data['controller'] if role == 'controller' else data['natives'][unit]['state']
    return module.collect(state, scope(data, role, unit), clock=kwargs.pop('clock', lambda: (220000000, 1000000000)),
                          boot_reader=kwargs.pop('boot_reader', lambda: BOOT), **kwargs)


def files(directory):
    return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode, p.stat().st_size, p.stat().st_mtime_ns)
            for p in directory.rglob('*') if p.is_file()}


def legacy(data, unit='unit-a'):
    n = data['natives'][unit]; root = n['state']; runs = root/'runs'; runs.mkdir(mode=0o700)
    run = n['child']['metadata']['run_id']; directory = runs/run; directory.mkdir(mode=0o700)
    plan, receipt = n['plan'], n['receipt']; transport = n['child']['metadata']['transport']
    binding = receipt['grant_context_sha256'] if transport == 'tcp' else 'b'*64
    if transport == 'tcp':
        name = 'bytes.sqlite'
        database(directory/name, [
            'CREATE TABLE owner(id INTEGER PRIMARY KEY,binding BLOB,rx INTEGER,rx_pending INTEGER,rx_unknown INTEGER,fenced INTEGER)',
            'CREATE TABLE attempts(id INTEGER PRIMARY KEY,digest BLOB,bytes INTEGER,lane TEXT,written INTEGER,state TEXT)'], [
            ('INSERT INTO owner VALUES(?,?,?,?,?,?)', [(1, bytes.fromhex(binding), 10, 20, 30, 1)]),
            ('INSERT INTO attempts VALUES(?,?,?,?,?,?)', [(1, b'd'*32, 64, 'DATA', 64, 'KERNEL_ACCEPTED'),
                (2, b'd'*32, 100, 'ACK', 10, 'UNKNOWN')])])
    else:
        name = 'attempts.sqlite'
        database(directory/name, [
            'CREATE TABLE owner(id INTEGER PRIMARY KEY,binding BLOB,max_attempts INTEGER,max_airtime INTEGER,request INTEGER,fenced INTEGER)',
            'CREATE TABLE attempts(request INTEGER PRIMARY KEY,packet_sha BLOB,bytes INTEGER,estimated_us INTEGER,state TEXT,evidence TEXT)'], [
            ('INSERT INTO owner VALUES(?,?,?,?,?,?)', [(1, bytes.fromhex(binding), 256, 60000000, 2, 1)]),
            ('INSERT INTO attempts VALUES(?,?,?,?,?,?)', [(1, b'd'*32, 32, 100, 'RF_DONE', 'source fixture'),
                (2, b'd'*32, 32, 200, 'UNKNOWN', 'source fixture')])])
    write(directory/(name+'.owner'), b'')
    fields = 'only_row INTEGER PRIMARY KEY,binding BLOB,direction INTEGER,boot TEXT,'
    if transport == 'tcp':
        fields += 'original_deadline INTEGER,'
    fields += 'deadline INTEGER,epoch INTEGER,mono INTEGER,tx INTEGER,high INTEGER,bitmap BLOB,retired INTEGER'
    values = (1, bytes.fromhex(receipt['grant_context_sha256']), 0 if n['child']['metadata']['sends'] else 1, BOOT)
    if transport == 'tcp':
        values += (879999000,)
    values += (879998000, 170000000, 870000000, 3, 5, bytes(32), 1)
    database(directory/'wire.sqlite', ['CREATE TABLE state('+fields+')'], [
        ('INSERT INTO state VALUES('+','.join('?' for _ in values)+')', [values])])
    write(directory/'grant.json', plan); write(directory/'active', b'retired\n')
    write(directory/'provision.json', dict(schema='meshticulum.provisioned-public-pilot.v2', unit=unit,
        run_id=run, transport=transport, original_application_expires_epoch_us=180000000,
        original_application_deadline_us=879999000, parent=receipt))
    job = dict(schema='meshticulum.native-public-job.v1', unit=unit, host_boot_id=BOOT, run_id=run,
        grant_context_sha256=receipt['grant_context_sha256'], transport=transport, pid=1234,
        start_ticks=22, command_sha256='f'*64)
    write(directory/'job.json', job)
    write(directory/'result.json', dict(schema='meshticulum.ip-public-node-run.v1' if transport == 'tcp' else 'meshticulum.physical-service-run.v1',
        unit=unit, host_boot_id=BOOT, session_id=run, grant_context_sha256=receipt['grant_context_sha256'],
        service_format='MR1.request', original_application_expires_epoch_us=180000000,
        original_application_deadline_us=879999000))
    run_scope = debt.RunScope(run, BOOT, transport, module.sha((directory/'grant.json').read_bytes()),
        receipt['grant_context_sha256'], binding)
    return debt.Scope('meshticulum.public-debt-scope.v1', unit, BOOT, 'a'*64, 'b'*64, 250000000, (run_scope,)), directory


@pytest.mark.parametrize('transport', ['tcp', 'lora'])
def test_parent_layers_exact_full_caps_are_observed_without_mutation_or_double_sum(tmp_path, root_observation, transport):
    data = fixture(tmp_path, transport); before = files(tmp_path)
    controller = collect(data, 'controller'); natives = tuple(collect(data, unit=u) for u in ('unit-a', 'unit-b'))
    c = controller.to_dict(); n = natives[0].to_dict()
    assert c['scoped_rows_complete'] and n['scoped_rows_complete'], (c, n)
    cost = data['children'][0]['cost']
    assert c['detail']['units'][0]['full_prepaid_caps'] == cost
    assert n['detail']['full_prepaid_caps'] == cost and n['detail']['start_count'] == 1
    assert n['policy_expired_at_observation'] and not n['eligible_for_enrollment']
    relation = module.reconcile(controller, natives)
    assert len(relation['matched']) == 2 and relation['scoped_relation_complete']
    assert not relation['complete_global_debt'] and not relation['independent_debt_sum_permitted']
    assert not relation['prior_lineage_verified'] and not relation['serialized_cross_host_provenance_qualified']
    assert files(tmp_path) == before


@pytest.mark.parametrize('transport,expected', [('tcp', asdict(Cost(ip_frames=2, tx_stream_bytes=164, rx_stream_bytes=60))),
                                             ('lora', asdict(Cost(2, 300)))])
def test_optional_protected_legacy_reader_matches_exact_start_and_preserves_full_caps(tmp_path, root_observation, transport, expected):
    data = fixture(tmp_path, transport); legacy_scope, _ = legacy(data); before = files(tmp_path)
    result = collect(data, legacy_scope=legacy_scope).to_dict()
    assert result['scoped_rows_complete'] and result['legacy_scoped_rows_complete'], result
    link = result['legacy_relation']['links'][0]
    assert link['relation'] == 'same_endpoint_allocation' and link['actual_cost'] == expected
    assert link['full_prepaid_caps'] == data['children'][0]['cost']
    assert result['detail']['full_prepaid_caps'] == link['full_prepaid_caps']
    assert result['legacy_runs'][0]['journal']['has_unknown_or_pending']
    assert not result['refund_permitted'] and not result['actual_plus_prepaid_sum_permitted']
    assert files(tmp_path) == before


def test_no_authority_constructors_native_sqlite_connections_create_or_secret_reads(tmp_path, root_observation, monkeypatch):
    data = fixture(tmp_path); legacy_scope, directory = legacy(data)
    for name in ('secret.bin', 'sender.bin', 'receiver.bin'):
        write(directory/name, b'PRIVATE-DO-NOT-READ')
    real_open, real_connect = os.open, sqlite3.connect
    opened, connections = [], []
    def observed_open(path, flags, *args, **kwargs):
        opened.append((os.fspath(path), flags))
        return real_open(path, flags, *args, **kwargs)
    def observed_connect(path, *args, **kwargs):
        connections.append(path)
        return real_connect(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', observed_open); monkeypatch.setattr(sqlite3, 'connect', observed_connect)
    result = collect(data, legacy_scope=legacy_scope).to_dict()
    assert result['legacy_relation']['links'][0]['relation'] == 'same_endpoint_allocation'
    assert connections == [':memory:']*3
    assert all(flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_CREAT for _, flags in opened)
    assert all(Path(path).name not in ('secret.bin', 'sender.bin', 'receiver.bin') for path, _ in opened)
    assert 'PRIVATE-DO-NOT-READ' not in json.dumps(result)


@pytest.mark.parametrize('prepared,started', [(False, False), (True, False)])
def test_unprepared_or_unstarted_claim_remains_full_debt(tmp_path, root_observation, prepared, started):
    data = fixture(tmp_path, prepared=prepared, started=started)
    result = collect(data).to_dict()
    assert result['scoped_rows_complete'] and result['detail']['full_prepaid_caps'] == data['children'][0]['cost']
    assert result['detail']['start_count'] == 0 and result['detail']['prepared_count'] == int(prepared)
    relation = module.reconcile(collect(data, 'controller'), (collect(data),))
    assert not relation['scoped_relation_complete'] and relation['missing_native_units'] == ['unit-b']
    assert len(relation['missing_native_endpoints']) == 1 and not relation['refund_permitted']


def test_start_without_prepared_record_is_unknown_not_zero(tmp_path, root_observation):
    data = fixture(tmp_path, prepared=False, started=True); result = collect(data).to_dict()
    assert result['status'] == 'unavailable' and result['detail'] is None and result['reason'] == 'invalid_start'


@pytest.mark.parametrize('marker', [b'', b'uncertain public transaction'])
def test_pending_marker_retains_observed_rows_and_prevents_complete_relation(tmp_path, root_observation, marker):
    data = fixture(tmp_path); path = data['natives']['unit-a']['path']; write(Path(str(path)+'.pending'), marker)
    before = files(tmp_path); n = collect(data)
    assert n.to_dict()['pending_unknown'] is True and n.to_dict()['detail']['claim_count'] == 1
    result = module.reconcile(collect(data, 'controller'), (n, collect(data, unit='unit-b')))
    assert result['pending_or_unapproved_source'] and not result['scoped_relation_complete']
    assert files(tmp_path) == before


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_any_sidecar_refuses_without_recovery_or_mutation(tmp_path, root_observation, suffix):
    data = fixture(tmp_path); path = data['natives']['unit-a']['path']; write(Path(str(path)+suffix), b'not checkpointed')
    before = files(tmp_path); result = collect(data).to_dict()
    assert result['detail'] is None and result['reason'] == 'sqlite_sidecar_present'
    assert files(tmp_path) == before


def test_existing_owner_lock_must_be_available(tmp_path, root_observation):
    data = fixture(tmp_path); path = Path(str(data['natives']['unit-a']['path'])+'.owner')
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = collect(data).to_dict()
        assert result['detail'] is None and result['reason'] == 'owner_active'
    finally:
        os.close(fd)


@pytest.mark.parametrize('missing', ['native.sqlite', 'native.sqlite.owner', 'native.sqlite.approval'])
def test_missing_metadata_stays_unknown_and_is_never_created(tmp_path, root_observation, missing):
    data = fixture(tmp_path); path = data['natives']['unit-a']['path']; target = path.parent/missing
    pinned = scope(data); target.unlink(); before = files(tmp_path)
    result = module.collect(data['natives']['unit-a']['state'], pinned, clock=lambda: (220000000, 1000000000), boot_reader=lambda: BOOT).to_dict()
    assert result['status'] == 'unavailable' and result['detail'] is None
    assert files(tmp_path) == before and not target.exists()


def test_fully_rehashed_lower_stored_cost_still_fails_profile_maxima(tmp_path, root_observation):
    def lower(data):
        data['children'][0]['cost']['tx_stream_bytes'] = 1
    data = fixture(tmp_path, transform=lower)
    for role in ('controller', 'native'):
        result = collect(data, role).to_dict()
        assert result['detail'] is None and result['reason'] == 'invalid_full_caps'


@pytest.mark.parametrize('change', ['boot', 'direction', 'resource'])
def test_rehashed_pair_or_native_binding_substitution_refuses(tmp_path, root_observation, change):
    def substitute(data):
        m = data['children'][0]['metadata']
        if change == 'boot':
            m['host_boot_id'] = OTHER_BOOT
        elif change == 'direction':
            m['sends'] = 1
        else:
            m['resource_sha256'] = '9'*64
        data['children'][0]['digest'] = module.sha(DOMAIN+b'child\0'+module.canonical(m))
    data = fixture(tmp_path, transform=substitute)
    result = collect(data, 'controller').to_dict()
    assert not result['scoped_rows_complete'] and result['detail'] is None


@pytest.mark.parametrize('name', ['native.sqlite', 'native.sqlite.owner'])
def test_fifo_refused_before_blocking_open(tmp_path, root_observation, monkeypatch, name):
    data = fixture(tmp_path); pinned = scope(data); path = data['natives']['unit-a']['path'].parent/name
    path.unlink(); os.mkfifo(path, 0o600)
    original = os.open; fifo_opens = []
    def observed(path, flags, *args, **kwargs):
        if os.fspath(path) == name:
            fifo_opens.append(flags)
            raise OSError('test refuses potentially blocking FIFO open')
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', observed)
    report = module.collect(data['natives']['unit-a']['state'], pinned, clock=lambda: (220000000, 1000000000), boot_reader=lambda: BOOT).to_dict()
    assert report['reason'] == 'unprotected_metadata' and fifo_opens == []


@pytest.mark.parametrize('name', ['native.sqlite', 'native.sqlite.owner', 'native.sqlite.approval'])
def test_symlinked_or_unprotected_source_is_not_read(tmp_path, root_observation, name):
    data = fixture(tmp_path); pinned = scope(data); path = data['natives']['unit-a']['path'].parent/name
    saved = tmp_path/'unapproved-target'; path.rename(saved); path.symlink_to(saved)
    report = module.collect(data['natives']['unit-a']['state'], pinned, clock=lambda: (220000000, 1000000000), boot_reader=lambda: BOOT).to_dict()
    assert report['reason'] == 'unprotected_metadata' and report['detail'] is None


def test_final_parent_callback_mutation_invalidates_already_parsed_rows(tmp_path, root_observation, monkeypatch):
    data = fixture(tmp_path); parsed = False; original = module._Reader.read
    def returned(self, name, *args, **kwargs):
        nonlocal parsed
        value = original(self, name, *args, **kwargs)
        if name.endswith('.pending'):
            parsed = True
        return value
    monkeypatch.setattr(module._Reader, 'read', returned)
    def clock():
        nonlocal parsed
        if parsed:
            parsed = False
            write(Path(str(data['natives']['unit-a']['path'])+'.approval'), b'revoked')
        return 220000000, 1000000000
    result = collect(data, clock=clock).to_dict()
    assert result['status'] == 'unavailable' and result['detail'] is None and result['reason'] == 'file_changed'


def test_final_legacy_callback_mutation_preserves_parent_but_invalidates_actual_relation(tmp_path, root_observation, monkeypatch):
    data = fixture(tmp_path); legacy_scope, directory = legacy(data); parsed = False; original = debt._one
    def returned(*args):
        nonlocal parsed
        value = original(*args)
        assert value['status'] == 'scoped_rows_observed'
        parsed = True
        return value
    monkeypatch.setattr(debt, '_one', returned)
    def clock():
        nonlocal parsed
        if parsed:
            parsed = False; write(directory/'active', b'changed\n')
        return 220000000, 1000000000
    result = collect(data, legacy_scope=legacy_scope, clock=clock).to_dict()
    assert result['scoped_rows_complete'] and not result['legacy_scoped_rows_complete']
    assert result['legacy_runs'][0]['actual_cost'] is None and result['legacy_runs'][0]['reason'] == 'final_input_changed'
    assert result['legacy_relation']['links'][0]['relation'] == 'unresolved'
    assert result['detail']['full_prepaid_caps'] == data['children'][0]['cost']


def test_caller_dicts_or_report_mutation_cannot_manufacture_observed_provenance(tmp_path, root_observation):
    data = fixture(tmp_path); c, n = collect(data, 'controller'), collect(data)
    with pytest.raises(module.SnapshotError):
        module.reconcile(c.to_dict(), (n,))
    with pytest.raises(TypeError):
        module.Snapshot()
    changed = n.to_dict(); changed['detail']['full_prepaid_caps']['tx_stream_bytes'] = 0
    assert n.to_dict()['detail']['full_prepaid_caps']['tx_stream_bytes'] != 0
    fake = object.__new__(module.Snapshot)
    with pytest.raises(module.SnapshotError):
        module.reconcile(c, (fake,))


def test_controller_preserves_separate_current_uid_without_native_root_substitution(tmp_path):
    data = fixture(tmp_path); uid = os.geteuid(); pinned = scope(data, 'controller', owner_uid=uid)
    result = module.collect(data['controller'], pinned, clock=lambda: (220000000, 1000000000), boot_reader=lambda: BOOT).to_dict()
    assert result['scoped_rows_complete'] and result['owner_uid'] == uid
    with pytest.raises(module.SnapshotError):
        replace(scope(data), owner_uid=1234)


@pytest.mark.parametrize('change', ['boot', 'expiry', 'owner'])
def test_observation_refuses_current_clock_boot_or_owner_changes(tmp_path, root_observation, monkeypatch, change):
    data = fixture(tmp_path)
    if change == 'owner':
        monkeypatch.setattr(os, 'geteuid', lambda: 1234)
    result = collect(data, clock=lambda: (250000000 if change == 'expiry' else 220000000, 1000000000),
        boot_reader=lambda: OTHER_BOOT if change == 'boot' else BOOT).to_dict()
    assert result['detail'] is None and not result['scoped_rows_complete']


def test_exact_database_hash_pin_is_required(tmp_path, root_observation):
    data = fixture(tmp_path); pinned = replace(scope(data), database_sha256='f'*64)
    report = module.collect(data['natives']['unit-a']['state'], pinned, clock=lambda: (220000000, 1000000000), boot_reader=lambda: BOOT).to_dict()
    assert report['reason'] == 'database_hash_mismatch' and report['detail'] is None


def test_legacy_missing_and_parent_substitution_never_remove_full_prepaid_caps(tmp_path, root_observation):
    data = fixture(tmp_path); legacy_scope, directory = legacy(data)
    provision = json.loads((directory/'provision.json').read_bytes())
    provision['parent']['native_claim_id'] = '9'*64
    write(directory/'provision.json', provision)
    report = collect(data, legacy_scope=legacy_scope).to_dict()
    assert report['detail']['full_prepaid_caps'] == data['children'][0]['cost']
    assert report['legacy_relation']['links'][0]['relation'] == 'unresolved'
    assert report['legacy_relation']['links'][0]['reason'] == 'legacy_parent_identity_mismatch'


def rewrite(path, table, change):
    """Alter one source-model table while preserving its exact schema/triggers."""
    db = sqlite3.connect(path)
    try:
        triggers = db.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
        for name, _ in triggers:
            db.execute('DROP TRIGGER '+name)
        rows = db.execute('SELECT * FROM '+table).fetchall()
        db.execute('DELETE FROM '+table)
        for row in rows:
            edited = change(row)
            db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in edited)+')', edited)
        for _, sql in triggers:
            db.execute(sql)
        db.commit()
    finally:
        db.close()
    path.chmod(0o600)


def test_nested_receipt_boolean_alias_cannot_equal_integer_caps(tmp_path, root_observation):
    data = fixture(tmp_path)
    def alias(row):
        value = module.public_json(row[1]); value['child_caps']['cost']['radio_attempts'] = False
        return row[0], module.canonical(value)
    rewrite(data['natives']['unit-a']['path'], 'prepared', alias)
    report = collect(data).to_dict()
    assert report['detail'] is None and report['reason'] == 'invalid_receipt'


@pytest.mark.parametrize('field', ['object', 'profile', 'frames', 'peers'])
def test_real_public_plan_must_correspond_to_each_stored_descriptor_and_policy_peer(tmp_path, root_observation, field):
    def change(data):
        if field == 'peers':
            p = data['policy']; first = replace(p.units[0], peer=PeerID('public-parent-observation-fixture', '9'*64))
            data['policy'] = replace(p, units=(first, p.units[1]))
        for child in data['children']:
            m = child['metadata']
            if field == 'object':
                m['object']['sha256'] = '9'*64
            elif field == 'profile':
                m['profile']['spreading_factor'] = 8
            elif field == 'frames':
                m['cipher_frame_limit'] = 511
            else:
                m['policy_digest'] = data['policy'].digest
            child['digest'] = module.sha(DOMAIN+b'child\0'+module.canonical(m))
    data = fixture(tmp_path, 'lora', transform=change); legacy_scope, _ = legacy(data)
    report = collect(data, legacy_scope=legacy_scope).to_dict()
    assert report['scoped_rows_complete'] and report['legacy_scoped_rows_complete'], report
    link = report['legacy_relation']['links'][0]
    assert link['relation'] == 'unresolved'
    assert link['reason'] == ('legacy_peer_correspondence_mismatch' if field == 'peers' else 'legacy_descriptor_correspondence_mismatch')
    assert report['detail']['full_prepaid_caps'] == data['children'][0]['cost']


def test_same_named_real_column_schema_is_not_native_integer_state(tmp_path, root_observation):
    data = fixture(tmp_path); path = data['natives']['unit-a']['path']; db = sqlite3.connect(path)
    try:
        db.execute('ALTER TABLE clock RENAME TO old_clock')
        db.execute('CREATE TABLE clock(id INTEGER PRIMARY KEY CHECK(id=1),epoch REAL,mono INTEGER)')
        db.execute('INSERT INTO clock SELECT * FROM old_clock')
        db.execute('DROP TABLE old_clock'); db.commit()
    finally:
        db.close()
    report = collect(data).to_dict()
    assert report['detail'] is None and report['reason'] == 'invalid_ledger_schema'


def test_noncanonical_rehashed_row_is_not_accepted(tmp_path, root_observation):
    data = fixture(tmp_path); path = data['controller']/'caps.sqlite'
    def spaced(row):
        value = module.public_json(row[2]); raw = json.dumps(value).encode()
        return module.sha(DOMAIN+b'pair\0'+raw), row[1], raw
    rewrite(path, 'allocations', spaced)
    report = collect(data, 'controller').to_dict()
    assert report['detail'] is None and report['reason'] == 'invalid_canonical_row'


def test_prepared_deadline_cannot_extend_original_resource_attachment(tmp_path, root_observation):
    data = fixture(tmp_path)
    def extend(row):
        value = module.public_json(row[1]); value['application_deadline_monotonic_us'] = 880000001
        return row[0], module.canonical(value)
    rewrite(data['natives']['unit-a']['path'], 'prepared', extend)
    report = collect(data).to_dict()
    assert report['detail'] is None and report['reason'] == 'invalid_receipt'


def test_raw_extra_body_or_secret_fields_are_never_exported(tmp_path, root_observation):
    data = fixture(tmp_path)
    def injected(row):
        value = module.public_json(row[1]); value['arbitrary_private_body'] = 'PRIVATE-CONTENT'
        return row[0], module.canonical(value)
    rewrite(data['natives']['unit-a']['path'], 'prepared', injected)
    report = collect(data).to_dict()
    assert report['detail'] is None and 'PRIVATE-CONTENT' not in json.dumps(report)
