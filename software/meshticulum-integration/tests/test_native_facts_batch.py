# SPDX-License-Identifier: Apache-2.0
"""Local CLI-output controls; modeled OS facts, no systemctl/device calls."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from lantern_meshticulum.bench import node as bench
from lantern_meshticulum.ip import node as ip
from meshticulum.bench.wire import WireError

BOOT='00000000-0000-4000-8000-000000000001'
PROPERTIES=('Id','ActiveState','MainPID','InvocationID','ExecMainStartTimestampMonotonic')


def rows(unit):
    names=bench.PERSONAL if unit=='unit-c' else bench.PERSONAL[:3]+('sage-lantern-mesh-runtime.service',)
    return [dict(Id=name,ActiveState='active' if unit=='unit-c' else 'inactive',
        MainPID=str(index+100) if unit=='unit-c' else '0',InvocationID=hex(index+1)[2:]*32 if unit=='unit-c' else '',
        ExecMainStartTimestampMonotonic=str(index+1000) if unit=='unit-c' else '0') for index,name in enumerate(names)]


def output(values):
    # systemctl show emits property blocks separated by one blank line.
    return '\n\n'.join('\n'.join(key+'='+value for key,value in row.items()) for row in values)+'\n'


@pytest.fixture
def modeled(monkeypatch,tmp_path):
    board=tmp_path/'compatible'; board.write_bytes(b'rockchip,rk3588-orangepi-5-plus\0')
    boot=tmp_path/'boot_id'; boot.write_text(BOOT)
    lab=tmp_path/'lab'; lab.mkdir(); (lab/'enabled').touch()
    mapping={'/proc/device-tree/compatible':board,'/proc/sys/kernel/random/boot_id':boot,'/etc/sage-lamplighter-lab':lab}
    monkeypatch.setattr(bench,'Path',lambda value:mapping[str(value)])
    monkeypatch.setattr(ip,'Path',lambda value:mapping[str(value)])
    monkeypatch.setattr(bench.platform,'system',lambda:'Linux'); monkeypatch.setattr(bench.os,'geteuid',lambda:0)
    def configure(unit): (lab/'operator.json').write_text(json.dumps(dict(unit=unit)))
    return configure,board,lab


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_facts_single_fresh_call_preserves_exact_service_lifetime_fields(modeled,monkeypatch,unit):
    configure,_,_=modeled; configure(unit); values=rows(unit); calls=[]
    def show(command,**kwargs):
        calls.append(command)
        assert kwargs==dict(capture_output=True,text=True,timeout=5)
        assert command[:4]==['/usr/bin/systemctl','--no-pager','--all','show']
        assert command[4:8]==[row['Id'] for row in values]
        assert command[8:]==[part for key in PROPERTIES for part in ('-p',key)]
        return SimpleNamespace(returncode=0,stdout=output(values))
    monkeypatch.setattr(bench.subprocess,'run',show)
    first=ip._facts(unit)
    assert len(calls)==1 and first==dict(unit=unit,host_boot_id=BOOT,
        services={row['Id']:row['ActiveState'] for row in values},
        service_lifetimes={row['Id']:{k:row[k] for k in PROPERTIES[2:]} for row in values})
    values[0]['ExecMainStartTimestampMonotonic']='9000'
    second=ip._facts(unit)
    assert len(calls)==2 and first['service_lifetimes']!=second['service_lifetimes']
    assert bench.roles(unit)==second['services'] and len(calls)==3


@pytest.mark.parametrize('case',['nonzero','missing_unit','duplicate_unit','foreign_unit','duplicate_property',
    'missing_property','unframed','malformed','bad_pid','bad_lifetime','bad_invocation','oversize'])
def test_malformed_or_incomplete_batched_sample_refuses(modeled,monkeypatch,case):
    configure,_,_=modeled; configure('unit-c'); values=rows('unit-c'); code=0
    if case=='nonzero': code=1
    elif case=='missing_unit': values.pop()
    elif case=='duplicate_unit': values.append(deepcopy(values[0]))
    elif case=='foreign_unit': values[-1]['Id']='foreign.service'
    elif case=='missing_property': del values[0]['InvocationID']
    elif case=='bad_pid': values[0]['MainPID']='unknown'
    elif case=='bad_lifetime': values[0]['ExecMainStartTimestampMonotonic']='-1'
    elif case=='bad_invocation': values[0]['InvocationID']='invalid'
    raw=output(values)
    if case=='duplicate_property': raw=raw.replace('Id=', 'MainPID=0\nId=',1)
    elif case=='unframed': raw=raw.replace('\n\n','\n')
    elif case=='malformed': raw='unexpected line\n'+raw
    elif case=='oversize': raw='x'*65537
    monkeypatch.setattr(bench.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=code,stdout=raw))
    with pytest.raises(WireError): ip._facts('unit-c')


@pytest.mark.parametrize('case',['active','pid','wrong_board','wrong_unit','disabled'])
def test_original_carrier_and_c_board_gates_remain(modeled,monkeypatch,case):
    configure,board,lab=modeled; unit='unit-c' if case=='wrong_board' else 'unit-a'; configure(unit)
    values=rows(unit)
    if case=='active': values[0]['ActiveState']='active'
    elif case=='pid': values[0]['MainPID']='100'
    elif case=='wrong_board': board.write_bytes(b'other-board\0')
    elif case=='wrong_unit': configure('unit-b')
    else: (lab/'enabled').unlink()
    monkeypatch.setattr(bench.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=output(values)))
    with pytest.raises(WireError): ip._facts(unit)


@pytest.mark.parametrize('case',['nonlinux','nonroot','unknown_unit'])
def test_platform_root_and_unit_refuse_before_sampling(modeled,monkeypatch,case):
    if case=='nonlinux': monkeypatch.setattr(bench.platform,'system',lambda:'Darwin')
    elif case=='nonroot': monkeypatch.setattr(bench.os,'geteuid',lambda:501)
    monkeypatch.setattr(bench.subprocess,'run',lambda *a,**k:pytest.fail('unexpected command'))
    with pytest.raises(WireError): ip._facts('unknown' if case=='unknown_unit' else 'unit-a')
