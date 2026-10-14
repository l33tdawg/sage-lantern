"""Genuine local Guard/Runtime/NativeCaps; declared clocks, facts, and no radio."""
from copy import deepcopy
import json
import os
from types import MappingProxyType

import pytest

from lantern_meshticulum.bench import mcu_artifact as mcu
from lantern_meshticulum.bench import pilot_native_caps as native_caps
from lantern_meshticulum.bench import pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from lantern_meshticulum.bench.resume_parent_guard import ResumeParentGuard
from tests.test_fast_management_guard import (
    management_guard, open_fd_identities, public_namespace_precedes_historical_capture,
)
from tests.test_fast_bundle import native, fast_data, admit
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path, protected_source, clock, facts, BOOT


@pytest.fixture
def selected(native,protected_source,monkeypatch):
    """Local model scope only; original guards execute without a native grant."""
    from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
    monkeypatch.setattr(mcu,'PENDING_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256=rpc.sha(b'declared source-only IRQ management-guard scope'),
        history_receipt_sha256=rpc.sha(b'declared source-only IRQ management-guard history'),
        preceding_parent=PAID_HISTORY[-1])))
    n=native;root,_=protected_source
    path=root/'manifest.json';manifest=json.loads(path.read_bytes())
    manifest.update(mcu_artifact=mcu.artifact(),radio_profile_name='gfsk250',
        native_operation_kind=mcu.KIND,firmware=mcu.FIRMWARE,firmware_by_unit={'unit-a':mcu.FIRMWARE})
    path.write_bytes(canonical(manifest));pin=rpc.sha(path.read_bytes())
    bindings=mcu.require_selector_scope(mcu.KIND)
    for unit,directory in n['roots'].items():
        path=directory/'parents'/n['policy'].parent_id/'owner.json'
        owner=json.loads(path.read_bytes())
        owner.update(core_commit=mcu.CORE,manifest_sha256=pin,
            mb1_bundle_scope_sha256=bindings['scope_sha256'],
            history_receipt_sha256=bindings['history_receipt_sha256'])
        path.write_bytes(canonical(mcu.owner_envelope(owner,mcu.SELECTOR)))
    return n,root,pin


@pytest.mark.parametrize('unit',['unit-a','unit-c'])
def test_selected_genuine_guard_constructor_keeps_envelope_and_fresh_current(selected,unit):
    n,root,pin=selected;directory=n['roots'][unit]/'parents'/n['policy'].parent_id
    raw=(directory/'owner.json').read_bytes();before=open_fd_identities()
    with rpc.Runtime(root,pin) as runtime:
        with management_guard(n,unit,{},runtime) as guard:
            assert guard._own_raw['owner']==raw and guard._pins['owner']==rpc.sha(raw)
            assert json.loads(raw)['schema']==mcu.OWNER_SCHEMA
            assert type(guard.caps._approve._protected) is native_caps._Public32Approval
            assert guard.caps._approve._protected.mcu_artifact==mcu.artifact()
            assert guard.caps.current() is True
            assert guard.check()['claims']==guard.check()['prepared']==guard.check()['starts']==0
            assert guard.caps.db.execute('SELECT COUNT(*) FROM starts').fetchone()==(0,)
    assert open_fd_identities()==before and (directory/'owner.json').read_bytes()==raw
    assert n['parent'].snapshot()['allocations']==0


def test_selected_stage_resource_remains_fresh_under_real_management_guard(selected):
    n,root,pin=selected;admit(n,0)
    with rpc.Runtime(root,pin) as runtime:
        with management_guard(n,'unit-a',{},runtime) as guard:
            assert guard.check()['starts']==0 and guard.caps.current() is True
            path=n['roots']['unit-a']/'parents'/n['policy'].parent_id/'fast-stage-0.json'
            path.write_bytes(path.read_bytes()+b'\n')
            with pytest.raises(Exception):guard.check()
            assert guard.caps.db.execute('SELECT COUNT(*) FROM starts').fetchone()==(0,)
            with pytest.raises(Exception):guard.close()


@pytest.mark.parametrize('fault',['extra_envelope','artifact','inner_policy','inner_kind','source','scope','history','flat_in_selected'])
def test_selected_bad_envelope_or_pairing_refuses_before_native_start(selected,fault):
    n,root,pin=selected;directory=n['roots']['unit-a']/'parents'/n['policy'].parent_id
    path=directory/'owner.json';value=json.loads(path.read_bytes())
    if fault=='extra_envelope':value['unexpected']='declared source fixture'
    elif fault=='artifact':value['mcu_artifact']['image_sha256']='0'*64
    elif fault=='inner_policy':value['original_owner']['policy_digest']='0'*64
    elif fault=='inner_kind':value['original_owner']['qualification_kind']='c-bulk32-v1'
    elif fault=='scope':value['original_owner']['mb1_bundle_scope_sha256']='0'*64
    elif fault=='history':value['original_owner']['history_receipt_sha256']='0'*64
    elif fault=='flat_in_selected':value=value['original_owner']
    else:
        path=root/'lantern_meshticulum/bench/resume_parent_guard.py'
        path.write_bytes(path.read_bytes()+b'\n# declared source drift\n')
    if fault!='source':path.write_bytes(canonical(value))
    before=open_fd_identities()
    if fault=='source':
        with pytest.raises(Exception):rpc.Runtime(root,pin)
    else:
        with rpc.Runtime(root,pin) as runtime:
            with pytest.raises(Exception):management_guard(n,'unit-a',{},runtime)
    assert open_fd_identities()==before
    assert not (directory/'native.sqlite.pending').exists()
    assert n['parent'].snapshot()['allocations']==0
