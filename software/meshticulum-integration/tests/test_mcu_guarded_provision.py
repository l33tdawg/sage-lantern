"""Real selected guarded preparation; declared local facts, no device or grant."""
from copy import deepcopy
import hashlib
import json
import os
import sqlite3

import pytest

from lantern_meshticulum.bench import mcu_artifact as mcu, pilot_provision, resume_parent_guard as guard
from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_mcu_management_guard import selected
from tests.test_fast_management_guard import (
    management_guard, open_fd_identities, public_namespace_precedes_historical_capture,
)
from tests.test_fast_bundle import native, fast_data, admit
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path, protected_source, clock, facts, PEERS, BOOT


def selected_request(n):
    expires,_=admit(n,0)
    context=n['context'];request,_=context.exchange(0)
    current=pair_packages(campaign_id=context.value['stages'][0]['campaign_id'],
        resource_sha256=context.value['manifest_sha256'],application_expires_epoch_us=expires,
        sender='unit-a',receiver='unit-c',transport='lora',format='MR1.request',
        descriptor=dict(bytes=len(request),sha256=hashlib.sha256(request).hexdigest()),
        source=None,request=request,peers=PEERS,boots={u:BOOT for u in PEERS},
        now_epoch_us=n['now'][0][0],radio_permission=dict(country='MY',antenna_confirmed=True),
        public32=True,profile='gfsk250',mb1_descriptor_sha256=context.value['transport_descriptor_sha256'],
        mcu_artifact_selector=mcu.SELECTOR)
    permit=n['parent'].prepay_pair(tuple(current['packages'].values()))
    children=n['parent'].consume(permit)
    for unit,package in current['packages'].items():
        package.update(schema=mcu.PAID_SCHEMA,parent=dict(schema='meshticulum.public-parent-allocation.v1',
            parent_id=n['policy'].parent_id,policy_digest=n['policy'].digest,allocation_id=permit.key,
            child=next(child for child in children if child.unit==unit).to_dict()))
    return current


def ledger(n,unit):
    parent=n['roots'][unit]/'parents'/n['policy'].parent_id
    with sqlite3.connect(parent/'native.sqlite') as db:
        rows={name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
              for name in ('claims','resources','prepared','starts')}
        assert rows==dict(claims=1,resources=1,prepared=1,starts=0)
        child=json.loads(db.execute('SELECT body FROM claims').fetchone()[0])['child']
        assert child['cost']==dict(radio_attempts=256,estimated_airtime_us=60000000,
            ip_frames=0,tx_stream_bytes=0,rx_stream_bytes=0)
    assert not (parent/'native.sqlite.pending').exists()


@pytest.mark.parametrize('unit',['unit-a','unit-c'])
def test_selected_genuine_guarded_prepare_preserves_raw_owner_and_provision(selected,unit):
    n,root,pin=selected;current=selected_request(n);parent=n['roots'][unit]/'parents'/n['policy'].parent_id
    owner=(parent/'owner.json').read_bytes();assert json.loads(owner)['schema']==mcu.OWNER_SCHEMA
    before=open_fd_identities()
    result=pilot_provision.provision(current['packages'][unit],state=n['roots'][unit],facts=facts(unit),clock=n['clock'])
    directory=n['roots'][unit]/'runs'/current['run_id'];provision=(directory/'provision.json').read_bytes()
    assert result['schema']==mcu.PROVISION_SCHEMA and result['mcu_artifact']==mcu.artifact()
    assert 'prepared_identity_sha256' in result and 'prepared_identity_sha256' not in json.loads(provision)
    with rpc.Runtime(root,pin) as runtime:
        with management_guard(n,unit,{current['run_id']:result['prepared_identity_sha256']},runtime) as live:
            assert live._own_raw['owner']==owner and live._pins['owner']==rpc.sha(owner)
            assert live.check()['claims']==live.check()['prepared']==1 and live.check()['starts']==0
    assert (parent/'owner.json').read_bytes()==owner and (directory/'provision.json').read_bytes()==provision
    assert open_fd_identities()==before and n['parent'].snapshot()['allocations']==1
    ledger(n,unit)


@pytest.mark.parametrize('fault',['legacy_schema','unknown_schema','other_artifact','missing_artifact','artifact_hash','parent'])
def test_selected_prepared_record_refuses_mismatched_schema_artifact_and_claim(selected,monkeypatch,fault):
    n,_,_=selected;current=selected_request(n);unit='unit-a';parent=n['roots'][unit]/'parents'/n['policy'].parent_id
    original=guard.record_prepared;owner=(parent/'owner.json').read_bytes()
    def changed(state,caps,preparation,held_staging_fd):
        path=state/'runs'/preparation.run_id/'provision.json';value=json.loads(path.read_bytes())
        if fault=='legacy_schema':value['schema']='meshticulum.provisioned-public-pilot.v2'
        elif fault=='unknown_schema':value['schema']='meshticulum.provisioned-public-mcu-pilot.v99'
        elif fault=='other_artifact':value['mcu_artifact']=mcu.artifact(mcu.LEGACY_SELECTOR)
        elif fault=='missing_artifact':value.pop('mcu_artifact')
        elif fault=='artifact_hash':value['mcu_artifact']['image_sha256']='0'*64
        else:value['parent']['native_claim_id']='0'*64
        path.write_bytes(canonical(value))
        return original(state,caps,preparation,held_staging_fd)
    monkeypatch.setattr(guard,'record_prepared',changed)
    before=open_fd_identities()
    with pytest.raises(Exception):
        pilot_provision.provision(current['packages'][unit],state=n['roots'][unit],facts=facts(unit),clock=n['clock'])
    assert not list((parent/'prepared-identities').iterdir())
    assert (parent/'owner.json').read_bytes()==owner and open_fd_identities()==before
    ledger(n,unit)
