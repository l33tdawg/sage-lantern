"""Actual H44 controller/row inputs; no observations, authority or traffic."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(scope='module')
def actual():
    base=Path(os.environ['MESHTICULUM_H44_FIXTURE'])/'resume-history-preparation-44'
    def read(name,pin):
        raw=(base/name).read_bytes();assert hashlib.sha256(raw).hexdigest()==pin
        return json.loads(raw)
    receipt=read('RECEIPT.json','04f6e18c9f9425796dc31fb203b7d92d826b6ef775a597903b2466aaf91cbee4')
    rows={u:read(u+'.json',pin)['previous']for u,pin in receipt['payload_sha256'].items()}
    controllers={p:read(p+'/controller.json',pin)for p,pin in receipt['controller_sha256'].items()}
    assert receipt['local_controllers_reobserved']==len(controllers)==44
    assert receipt['controller_allocations_observed']==1 and receipt['native_children_observed']==0
    script=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/run_public_sf5_c_bulk_8k.py'
    sys.path.insert(0,str(script.parent))
    spec=importlib.util.spec_from_file_location('actual_retained_controller_control',script)
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    original=subprocess.check_output(['git','show','6a5316b972e46fe0f1e6cbc85b982ca5094f143c:firmware/meshticulum-bench/run_public_sf5_c_bulk_8k.py'],cwd=script.parents[2])
    function=next(n for n in ast.parse(original).body if isinstance(n,ast.FunctionDef)and n.name=='retained_controller_caps')
    namespace={k:getattr(runner,k)for k in ('need','canonical','sha','asdict')}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'original_retained_controller_caps','exec'),namespace)
    return rows,controllers,runner.retained_controller_caps,namespace['retained_controller_caps']


@pytest.mark.parametrize('unit',('unit-a','unit-b','unit-c'))
@pytest.mark.parametrize('index',range(44))
def test_all_actual44_controller_rows_preserve_full_paid_caps(actual,unit,index):
    rows,controllers,current,original=actual;row=deepcopy(rows[unit][index])
    observed=deepcopy(controllers[row['policy']['parent_id']]);before=deepcopy((row,observed))
    assert current(unit,row,observed)==row['paid_caps']
    assert (row,observed)==before
    if index<43:assert original(unit,row,observed)==row['paid_caps']
    elif unit=='unit-b':assert original(unit,row,observed)==row['paid_caps']
    else:
        assert row['actual_native_paid_caps']!=row['actual_controller_paid_caps']
        assert row['conservative_unused_enrollment_caps']!=row['conservative_unused_controller_caps']
        assert row['native_controller_debt_views_may_be_summed']is False
        with pytest.raises(RuntimeError,match='retained_unused_enrollment_caps_changed'):
            original(unit,row,observed)


@pytest.mark.parametrize('damage',('native_as_controller','controller_as_native','native_unused_as_controller',
    'controller_unused_as_native','full_caps_reduced','summed','owner_flattened','controller_refs_changed'))
def test_actual_d1_views_cannot_be_relabelled_or_refunded(actual,damage):
    rows,controllers,current,_=actual;unit='unit-a';row=deepcopy(rows[unit][-1]);observed=deepcopy(controllers[row['policy']['parent_id']])
    if damage=='native_as_controller':row['actual_controller_paid_caps']=deepcopy(row['actual_native_paid_caps'])
    elif damage=='controller_as_native':row['actual_native_paid_caps']=deepcopy(row['actual_controller_paid_caps'])
    elif damage=='native_unused_as_controller':row['conservative_unused_controller_caps']=deepcopy(row['conservative_unused_enrollment_caps'])
    elif damage=='controller_unused_as_native':row['conservative_unused_enrollment_caps']=deepcopy(row['conservative_unused_controller_caps'])
    elif damage=='full_caps_reduced':row['paid_caps']['radio_attempts']-=256
    elif damage=='summed':row['native_controller_debt_views_may_be_summed']=True
    elif damage=='owner_flattened':row['owner']=row['owner']['original_owner']
    else:row['controller_observed_endpoint_refs']=[]
    with pytest.raises(Exception):current(unit,row,observed)
