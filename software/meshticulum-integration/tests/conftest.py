# SPDX-License-Identifier: Apache-2.0
"""Integration controls consume fixtures from an explicit Meshticulum checkout.

Only the source test harness crosses this development boundary. Wheels and
device bundles never require or copy another repository's test tree.
"""
import os
from pathlib import Path
import tests

source = os.environ.get('MESHTICULUM_SOURCE')
if not source:
    raise RuntimeError('Set MESHTICULUM_SOURCE to the pinned Meshticulum source checkout for integration tests.')
fixtures = Path(source).resolve() / 'tests'
if not (fixtures / 'test_meshticulum_private_carrier.py').is_file():
    raise RuntimeError('Meshticulum source fixture checkout is incomplete.')
tests.__path__.append(str(fixtures))


import pytest


@pytest.fixture
def synthetic_8120_capture(monkeypatch):
    """Held files and a genuine empty SQLite DB; explicitly synthetic pins."""
    from lantern_meshticulum.bench import capture_8120 as model, retained_capture as retained
    from lantern_meshticulum.bench.history_metadata_fence import _stamp
    from lantern_meshticulum.bench.pilot_bundle import COMMITTED_CONTRACTS
    from lantern_meshticulum.bench.profiles import GFSK250_FAST_BUNDLE_KIND
    from lantern_meshticulum.bench.pilot_parent_caps import canonical
    import hashlib, sqlite3
    def install(state,unit,clock,*,boot=None):
        now,mono=clock();target=state/'parents'/model.PARENT;target.mkdir(mode=0o700)
        (target/'prepared-identities').mkdir(mode=0o700)
        for key,value in dict(START=now-1000,END=now-2,WHOLE_END=now-3,ABSOLUTE_END=now-1).items():
            monkeypatch.setattr(model,key,value)
        selected=boot or model.UNITS[unit]['boot'];sha=lambda raw:hashlib.sha256(raw).hexdigest()
        phase=dict(epoch_us=now-999,monotonic_us=mono-999,deadline_monotonic_us=mono-2)
        strict=canonical(dict(schema='synthetic-held-strict',unit=unit))
        legacy=canonical(dict(schema='synthetic-held-legacy',unit=unit))
        binding=canonical(dict(schema='meshticulum.private-resume-history-binding.v2',unit=unit,
            parent_id=model.PARENT,strict_sha256=sha(strict),legacy_sha256=sha(legacy),
            parent_identity=list(_stamp(target.stat()))[:4]))
        history=dict(previous=[dict(policy=dict(parent_id=p)) for p in retained.PAID_HISTORY],local_synthetic_capture=True)
        capture=dict(schema=COMMITTED_CONTRACTS[GFSK250_FAST_BUNDLE_KIND][6],unit=unit,parent_id=model.PARENT,
            history_binding_sha256=sha(binding),review_sha256=model.REVIEW_SHA256,
            facts=dict(unit=unit,host_boot_id=selected),phase_start_epoch_us=model.START,phase_end_epoch_us=model.END,
            native_phase=phase,actual_history_verified=True,global_debt_complete=False,
            original_unknown_actual_cost=None,refunds=0,history=history)
        raws={'capture.json':canonical(capture),'history-binding.json':binding,
            'history-identity-seal.json':strict,'legacy-grant-seal.json':legacy}
        if unit=='unit-c':
            db=sqlite3.connect(target/'native.sqlite');db.execute('PRAGMA journal_mode=WAL');db.close()
            raws['native.sqlite']=(target/'native.sqlite').read_bytes()
            monkeypatch.setattr(model,'EMPTY_DATABASE_SHA256',sha(raws['native.sqlite']))
            raws.update({'native.sqlite.owner':b'','native.sqlite.approval':b'',
                'owner.json':canonical(dict(synthetic=True)),'policy.json':canonical(dict(synthetic=True)),
                'prior.json':canonical(dict(synthetic=True))})
        for name,raw in raws.items():(target/name).write_bytes(raw);(target/name).chmod(0o600)
        row=dict(files={name:sha(raw) for name,raw in raws.items()},boot=selected,phase=phase,
            history_sha256=sha(canonical(history)))
        monkeypatch.setattr(model,'UNITS',dict(model.UNITS,**{unit:row}))
        monkeypatch.setattr(retained,'current_boot',lambda:selected)
        return row
    return install


@pytest.fixture
def synthetic_a658_capture(monkeypatch,synthetic_8120_capture):
    """Real private files, explicit modeled pins; never native evidence."""
    from lantern_meshticulum.bench import capture_a658 as model, retained_capture as retained
    from lantern_meshticulum.bench.history_metadata_fence import _stamp
    from lantern_meshticulum.bench.pilot_bundle import COMMITTED_CONTRACTS
    from lantern_meshticulum.bench.profiles import GFSK_MB1_BUNDLE_KIND
    from lantern_meshticulum.bench.pilot_parent_caps import canonical
    import hashlib

    def install(state, unit, clock, *, boot=None):
        synthetic_8120_capture(state,unit,clock,boot=boot or retained.UNADMITTED[unit]['boot'])
        now, mono = clock()
        monkeypatch.setattr(model, 'START', now-1000000)
        monkeypatch.setattr(model, 'END', now-1)
        monkeypatch.setattr(model, 'WHOLE_END', now-500000)
        monkeypatch.setattr(model, 'EXPIRY_COMPLETION_SHA256', 'e'*64)
        if unit == 'unit-a':
            return None
        target = state/'parents'/model.PARENT
        target.mkdir(mode=0o700)
        (target/'prepared-identities').mkdir(mode=0o700)
        phase = dict(epoch_us=now-999999,monotonic_us=mono-999999,deadline_monotonic_us=mono-1)
        selected_boot = boot or model.UNITS[unit]['boot']
        strict = canonical(dict(schema='synthetic-held-strict',unit=unit))
        legacy = canonical(dict(schema='synthetic-held-legacy',unit=unit))
        sha = lambda raw: hashlib.sha256(raw).hexdigest()
        binding = canonical(dict(schema='meshticulum.private-resume-history-binding.v2',unit=unit,
            parent_id=model.PARENT,strict_sha256=sha(strict),legacy_sha256=sha(legacy),
            parent_identity=list(_stamp(target.stat()))[:4]))
        history = dict(previous=[dict(policy=dict(parent_id=p)) for p in retained.PAID_HISTORY[:35]],
                       local_synthetic_capture=True)
        record = dict(schema=COMMITTED_CONTRACTS[GFSK_MB1_BUNDLE_KIND][6],unit=unit,parent_id=model.PARENT,
            history_binding_sha256=sha(binding),review_sha256=model.REVIEW_SHA256,
            facts=dict(unit=unit,host_boot_id=selected_boot),phase_start_epoch_us=model.START,
            phase_end_epoch_us=model.END,native_phase=phase,actual_history_verified=True,
            global_debt_complete=False,original_unknown_actual_cost=None,refunds=0,history=history)
        raws = {'capture.json':canonical(record),'history-binding.json':binding,
                'history-identity-seal.json':strict,'legacy-grant-seal.json':legacy}
        for name, raw in raws.items():
            (target/name).write_bytes(raw); (target/name).chmod(0o600)
        row = dict(files={name:sha(raw) for name,raw in raws.items()},boot=selected_boot,
                   phase=phase,history_sha256=sha(canonical(history)))
        monkeypatch.setattr(model,'UNITS',dict(model.UNITS,**{unit:row}))
        monkeypatch.setattr(retained,'current_boot',lambda:selected_boot)
        return row
    return install
