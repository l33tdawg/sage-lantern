"""Signed five-part observer contract; no device, grants or remote actions."""
import ast
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from meshticulum.resources import lamplighter as ll
from lantern_meshticulum.bench.public_cache_observer import _observe_local_anchor
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path

SOURCE=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench/run_public_gfsk125_bundle100k.py'
sha=lambda raw:hashlib.sha256(raw).hexdigest()


@pytest.fixture
def gate():
    tree=ast.parse(SOURCE.read_bytes())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    body=next(n for n in main.body if isinstance(n,ast.Try)).body
    start=next(i for i,n in enumerate(body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='absent' for t in n.targets))
    selected=body[start:start+4]
    assert isinstance(selected[-1],ast.For)
    assert any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='seed' for n in ast.walk(selected[-1]))
    # The real launcher reaches only management/STATUS inspection before this
    # guard. Seed, preparation, enrollment and RF remain after actual absence.
    for statement in body[:start]:
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
            and n.func.attr in ('seed','prepare','launch','arm','transmit') for n in ast.walk(statement))
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='native'
            for n in ast.walk(statement))
    code=compile(ast.Module(body=selected,type_ignores=[]),str(SOURCE),'exec')
    def run(observation,wanted):
        calls=[];result={}
        def need(ok,reason):
            if not ok:raise RuntimeError(reason)
        def native(unit,action,public):calls.append(('native',unit,action));return dict(observation=observation)
        operator=SimpleNamespace(seed=lambda v:calls.append(('seed',v['unit'])))
        env=dict(native=native,result=result,need=need,wanted=wanted,public={},operator=operator,cfg={},
            helpers=SimpleNamespace(seed_package=lambda cfg,unit,role,*args:dict(unit=unit)),
            piece=dict(path=SOURCE.parent,trust={},cache=SimpleNamespace(value={})),f=dict(manifest_sha256='f'*64))
        try:exec(code,env)
        except RuntimeError as error:return False,calls,result,str(error)
        return True,calls,result,None
    return run


@pytest.fixture
def signed_public(data):
    _,chunks,value,_=data
    raw=base64.b64decode(value['manifest_base64']);signature=base64.b64decode(value['signature_base64'])
    trust=json.dumps(value['trust'],sort_keys=True).encode()+b'\n'
    pins=dict(manifest=sha(raw),signature=sha(signature),trust=sha(trust))
    manifest=ll.manifest(raw,signature,value['trust'],pins['manifest'])
    wanted=[v['sha256'] for v in manifest['chunks']]
    assert len(wanted)==len(set(wanted))==5 and wanted!=sorted(wanted)
    return raw,signature,trust,pins,wanted,chunks


def test_actual_five_chunk_observer_accepts_unsorted_manifest_or_reordered_wanted(gate,signed_public,tmp_path):
    raw,signature,trust,pins,wanted,_=signed_public;anchor=tmp_path/'public';anchor.mkdir(mode=0o700)
    for order in (wanted,list(reversed(wanted)),wanted[2:]+wanted[:2]):
        actual=_observe_local_anchor(str(anchor.absolute()),raw,signature,trust,pins,tuple(order))
        assert actual['absent']==sorted(wanted) and actual['verified_present']==[]
        allowed,calls,result,error=gate(actual,order)
        assert allowed and calls==[('native','unit-a','observe-cache'),('seed','unit-c'),('seed','unit-a')]
        assert result['pre_run_cache_observation']==actual and error is None
        assert actual['native_authority'] is actual['dispatch_eligible'] is actual['writes'] is actual['radio_opened'] is False


@pytest.mark.parametrize('fault',['present','missing','extra','duplicate'])
def test_wrong_absence_vector_stops_before_seed_capture_or_enrollment(gate,signed_public,fault):
    *_,wanted,chunks=signed_public;observation=dict(absent=sorted(wanted),verified_present=[])
    if fault=='present':observation['verified_present']=[wanted[0]]
    elif fault=='missing':observation['absent'].pop()
    elif fault=='extra':observation['absent'].append('0'*64)
    else:observation['absent'][-1]=observation['absent'][0]
    allowed,calls,result,error=gate(observation,wanted)
    assert not allowed and error=='whole_fixture_already_present'
    assert calls==[('native','unit-a','observe-cache')]
    assert result['pre_run_cache_observation']==observation


def test_actual_present_signed_chunk_stops_before_seed(gate,signed_public,tmp_path):
    raw,signature,trust,pins,wanted,chunks=signed_public;anchor=tmp_path/'public';anchor.mkdir(mode=0o700)
    target=anchor/pins['manifest'];target.mkdir(mode=0o700);received=target/'received';received.mkdir(mode=0o700)
    store=received/'chunks';store.mkdir(mode=0o700)
    def write(path,data):path.write_bytes(data);path.chmod(0o600)
    write(target/'trust.json',json.dumps(json.loads(trust),sort_keys=True).encode()+b'\n')
    write(received/'manifest.json',raw);write(received/'manifest.sig.json',signature)
    write(store/(wanted[0]+'.json'),chunks[wanted[0]])
    actual=_observe_local_anchor(str(anchor.absolute()),raw,signature,trust,pins,tuple(wanted))
    assert actual['verified_present']==[wanted[0]] and len(actual['absent'])==4
    allowed,calls,_,_=gate(actual,wanted)
    assert allowed is False and calls==[('native','unit-a','observe-cache')]


def test_actual_observer_refuses_duplicate_requested_hash(signed_public,tmp_path):
    raw,signature,trust,pins,wanted,_=signed_public;anchor=tmp_path/'public';anchor.mkdir(mode=0o700)
    with pytest.raises(ValueError,match='invalid wanted chunks'):
        _observe_local_anchor(str(anchor.absolute()),raw,signature,trust,pins,tuple(wanted+[wanted[0]]))
