"""Lossless storage and real held-file/NativeCaps controls; no native RF."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import zlib
import pytest
from lantern_meshticulum.bench import capture_storage as storage,pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_native_caps import _open_existing
from tests.test_fast_bundle import native,fast_data
from tests.test_pilot_bundle import data
from tests.test_public32_pilot import tmp_path,protected_source,facts,BOOT
from tests.test_pilot_resume_rpc_integration import local_scope
sha=lambda raw:hashlib.sha256(raw).hexdigest()


@pytest.fixture
def record():
    return storage.canonical(dict(schema='meshticulum.private-resume-actual-capture.v1',
        history={'old_public_rows':['unchanged metadata']*200},parent_id='a'*32,global_debt_complete=False,refunds=0))


def test_legacy_and_compact_canonical_identity_are_unchanged(record):
    assert storage.encode(record)==record and storage.decode(record,expected_sha256=sha(record))==record
    compact=storage.encode(record,storage.COMPACT)
    assert len(compact)<len(record) and storage.decode(compact,expected_sha256=sha(record))==record


@pytest.mark.parametrize('value',[None,True,0,'gzip-v1'])
def test_unknown_request_storage_refuses(value):
    with pytest.raises(RuntimeError):storage.selection(dict(capture_storage=value))


def test_compact_creation_requires_explicit_fast250_selection():
    assert storage.selection({})==storage.PLAIN
    assert storage.selection(dict(capture_storage=storage.COMPACT,qualification_kind='c-gfsk250-mb1-fast-bundle100k-v1'))==storage.COMPACT
    for kind in (None,'c-bulk32-v1','c-gfsk125-8k-batch-v1'):
        with pytest.raises(RuntimeError):storage.selection(dict(capture_storage=storage.COMPACT,qualification_kind=kind))


@pytest.mark.parametrize('fault',['codec','schema','extra','missing','bool_size','oversize','bad_hash',
    'bad_base64','truncated','trailing','concatenated','decoded_overflow','noncanonical_record','nested','duplicate'])
def test_compact_ambiguous_or_unbounded_representation_refuses(record,fault):
    value=json.loads(storage.encode(record,storage.COMPACT));packed=base64.b64decode(value['payload_base64'])
    if fault=='codec':value['codec']='unknown'
    elif fault=='schema':value['schema']='meshticulum.private-capture-storage.v2'
    elif fault=='extra':value['grant_permission']=True
    elif fault=='missing':del value['record_sha256']
    elif fault=='bool_size':value['record_bytes']=True
    elif fault=='oversize':value['record_bytes']=storage.RAW_MAX+1
    elif fault=='bad_hash':value['record_sha256']='f'*64
    elif fault=='bad_base64':value['payload_base64']='@@@'
    elif fault=='truncated':packed=packed[:-1]
    elif fault=='trailing':packed+=b'extra'
    elif fault=='concatenated':packed+=zlib.compress(record)
    elif fault=='decoded_overflow':value['record_bytes']=1;packed=zlib.compress(b'x'*(storage.RAW_MAX+1))
    elif fault=='noncanonical_record':
        altered=b'{ "unchanged" : true }';packed=zlib.compress(altered)
        value['record_bytes']=len(altered);value['record_sha256']=sha(altered)
    elif fault=='nested':
        altered=storage.canonical(value);packed=zlib.compress(altered)
        value['record_bytes']=len(altered);value['record_sha256']=sha(altered)
    if fault!='bad_base64':value['payload_base64']=base64.b64encode(packed).decode()
    raw=storage.canonical(value)
    if fault=='duplicate':raw=raw[:-1]+b',"codec":"zlib-base64"}'
    with pytest.raises(RuntimeError):storage.decode(raw)


@pytest.mark.parametrize('mode',[storage.PLAIN,storage.COMPACT])
def test_canonical_pin_mismatch_refuses(record,mode):
    with pytest.raises(RuntimeError,match='capture_canonical_pin_changed'):
        storage.decode(storage.encode(record,mode),expected_sha256='f'*64)


def test_reader_retains_stored_bytes_and_refuses_mutation(tmp_path,record):
    path=tmp_path/'capture.json';stored=storage.encode(record,storage.COMPACT)
    path.write_bytes(stored);path.chmod(0o600)
    with rpc.Reader() as reader:
        assert storage.decode(reader.read(path,storage.RAW_MAX))==record
        assert reader.held[0][3]==stored and reader.check()
        path.write_bytes(stored+b' ')
        with pytest.raises(RuntimeError):reader.check()


def test_selector_is_bound_in_actual_capture_request_guard(fast_data):
    from lantern_meshticulum.bench.pilot_bundle import public_context_commitment
    _,_,context,p=fast_data
    request=dict(parent_id='f'*32,review_sha256='e'*64,facts=dict(unit='unit-a'),qualification_kind=p.qualification_kind,
        bundle_context=context,bundle_capture_commitment=public_context_commitment(context,parent_id='f'*32),
        phase_start_epoch_us=p.issued_epoch_us,phase_end_epoch_us=p.expires_epoch_us+5000000,capture_storage=storage.COMPACT)
    function=next(n for n in ast.parse(Path(rpc.__file__).read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name=='capture_history')
    index=next(i for i,n in enumerate(function.body) if isinstance(n,ast.With))
    prefix=ast.FunctionDef(name='capture_prefix',args=function.args,body=function.body[:index]+[
        ast.Return(value=ast.Tuple(elts=[ast.Name(id='guard',ctx=ast.Load()),ast.Name(id='storage',ctx=ast.Load())],ctx=ast.Load()))],decorator_list=[])
    tree=ast.fix_missing_locations(ast.Module(body=[prefix],type_ignores=[]))
    env=dict(vars(rpc),clock=lambda:(p.issued_epoch_us,p.original_monotonic_us),phase_check=lambda *a,**k:None)
    exec(compile(tree,'actual-capture-selector-guard','exec'),env)
    runtime=type('SourceOnlyRuntime',(),{'check':lambda self:True})()
    guard,mode=env['capture_prefix']('unit-a',request,runtime)
    assert mode==storage.COMPACT and guard()
    request['capture_storage']=storage.PLAIN
    with pytest.raises(RuntimeError,match='capture_original_context_changed'):guard()


def compact_approval(n,unit):
    target=n['roots'][unit]/'parents'/n['policy'].parent_id;path=target/'capture.json';canonical=path.read_bytes()
    stored=storage.encode(canonical,storage.COMPACT);path.write_bytes(stored);path.chmod(0o600)
    assert json.loads((target/'owner.json').read_bytes())['capture_record_sha256']==sha(canonical)
    return _open_existing(n['roots'][unit],n['policy'].parent_id,facts(unit),n['clock']),path,stored


def test_real_approval_decodes_once_and_keeps_fresh_byte_checks(native,monkeypatch):
    calls=[];original=storage.decode
    monkeypatch.setattr(storage,'decode',lambda *a,**k:calls.append(True) or original(*a,**k))
    caps,path,stored=compact_approval(native,'unit-c')
    try:
        first=len(calls);assert first==1 and caps.current() and caps.current()
        assert len(calls)==first and caps._approve.reader._file_index[path][3]==stored
        assert caps.snapshot()['claims']==0
    finally:assert caps.close()


@pytest.mark.parametrize('fault',['bytes','replacement','revocation','deadline','services'])
def test_real_approval_keeps_mutation_and_authority_refusal(native,fault):
    caps,path,stored=compact_approval(native,'unit-a')
    try:
        assert caps.current()
        if fault=='bytes':path.write_bytes(stored[:-1]+b' ')
        elif fault=='replacement':
            new=path.with_name('replacement');new.write_bytes(stored);new.chmod(0o600);new.replace(path)
        elif fault=='revocation':
            marker=path.parent/'native.sqlite.pending';marker.write_bytes(b'no authority');marker.chmod(0o600)
        elif fault=='deadline':native['now'][0]=(native['policy'].expires_epoch_us,native['policy'].deadline_monotonic_us)
        else:caps._approve.facts['services']={'changed':True}
        assert caps.current() is False
    finally:caps.close()


def test_enrollment_preserves_canonical_capture_pin(local_scope):
    state,runtime,p,_,_=local_scope
    from tests.test_pilot_resume_rpc_integration import PARENT
    path=state/'parents'/PARENT/'capture.json';raw=path.read_bytes()
    path.write_bytes(storage.encode(raw,storage.COMPACT));path.chmod(0o600)
    assert rpc.enrolled_action('enroll','unit-a',p,runtime)['snapshot']['claims']==0
    assert p['owner']['capture_record_sha256']==sha(raw)


def test_retained_capture_keeps_physical_pins_and_decoded_scope(tmp_path,synthetic_8120_capture):
    from lantern_meshticulum.bench import capture_8120 as model,retained_capture as retained
    from lantern_meshticulum.bench.pilot_bundle import COMMITTED_CONTRACTS
    from lantern_meshticulum.bench.profiles import GFSK250_FAST_BUNDLE_KIND
    state=tmp_path/'state';state.mkdir(mode=0o700);(state/'parents').mkdir(mode=0o700);clock=lambda:(100000000,800000000)
    row=synthetic_8120_capture(state,'unit-a',clock,boot=BOOT)
    path=state/'parents'/model.PARENT/'capture.json';stored=storage.encode(path.read_bytes(),storage.COMPACT)
    path.write_bytes(stored);path.chmod(0o600);pins=dict(row['files'],**{'capture.json':sha(stored)})
    args=dict(expected_phase=row['phase'],expected_schema=COMMITTED_CONTRACTS[GFSK250_FAST_BUNDLE_KIND][6],
        expected_history_sha256=row['history_sha256'],absolute_end=model.ABSOLUTE_END)
    assert retained._artifact_present(state,'unit-a',clock,model.PARENT,pins,BOOT,model.START,model.END,model.REVIEW_SHA256,True,**args)
    with pytest.raises(RuntimeError):retained._artifact_present(state,'unit-a',clock,model.PARENT,row['files'],BOOT,
        model.START,model.END,model.REVIEW_SHA256,True,**args)
