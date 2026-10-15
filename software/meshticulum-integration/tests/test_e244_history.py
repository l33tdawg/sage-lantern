"""Actual H45 rows and public bytes; local FD fixtures confer no native authority."""
from copy import deepcopy
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile

import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import fast_bundle_unstarted_history as history, pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy, canonical

METADATA_SHA='31b1cb6c2ec226d49ba9edddd0de7c53c7bef3d15e11698aaa353e57bf38b38d'


@pytest.fixture(scope='module')
def inputs():
    location=os.environ.get('MESHTICULUM_H45_FIXTURE')
    if not location:pytest.skip('MESHTICULUM_H45_FIXTURE must name the actual E244/H45 preparation lane')
    base=Path(location)
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value);return value
    old=load('e244_original_H44_assembler',base/'original_assemble_history44.py')
    prepared=load('e244_H45_inputs',base/'prepare_history45_inputs.py')
    values=prepared.inputs(old.read,old.load,old.need,old.sha,old.canonical,
        base.parent/'meshticulum-e244-public-metadata-supplement-v2-20261015/E244_PUBLIC_METADATA.json',METADATA_SHA)
    receipt=old.read(base/'resume-history-preparation-45/RECEIPT.json',history.UNMATERIALIZED_NEXT_HISTORY_SHA256)
    assert receipt['local_controllers_reobserved']==45 and receipt['original_history_receipt_sha256']==prepared.OLD_SHA
    assert receipt['native_prepared_counts']=={'unit-a':1,'unit-b':0,'unit-c':1} and receipt['native_starts']==0
    assert receipt['controller_allocations_observed']==1
    assert receipt['actual_unknown_cost']is None and receipt['global_debt_complete']is False and receipt['no_refund']is True
    assert receipt['prior_H44_original_run_complete']is False and receipt['prior_H43_original_run_complete']is True
    assert receipt['prior_H42_original_run_complete']is False and receipt['prior_H42_final_audit_timeout_retained']is True
    for unit,pin in receipt['payload_sha256'].items():
        payload=old.read(base/'resume-history-preparation-45'/(unit+'.json'),pin,2500000)
        assert canonical(payload['previous'][:-1])==canonical(values['histories'][unit]['previous'])
        assert payload['previous'][-1]==values['latest'][unit]
        assert payload['legacy_inventory']==values['histories'][unit]['legacy_inventory']
    return values


@pytest.fixture(params=('unit-a','unit-b','unit-c'))
def actual(request,inputs):
    values=deepcopy(inputs);unit=request.param;row=values['latest'][unit]
    return unit,row,ParentPolicy.from_dict(row['policy']),values['observed'][unit]['observation'],values


def test_actual_prepared_unmaterialized_rows_keep_raw_owner_and_full_reservation(actual):
    unit,row,policy,observed,values=actual
    raw=canonical(row['owner']);owner=rpc.retained_owner_view(row['owner'],policy,unit)
    assert owner==row['owner']['original_owner'] and canonical(row['owner'])==raw
    rpc.qualify_history_claims(policy,unit,observed,[],row['unmaterialized_prepared'])
    history.qualify_unmaterialized_costs(unit,policy,observed,row)
    prior=values['histories'][unit]['previous'][-1]['paid_caps'];extra={}if unit=='unit-b'else dict(radio_attempts=2560,estimated_airtime_us=600000000)
    assert row['paid_caps']=={key:value+extra.get(key,0)for key,value in prior.items()}
    assert row['runs']==row['retired_unstarted']==[] and row['preparation_sha256']=={}
    assert len(row['unmaterialized_prepared'])==(0 if unit=='unit-b'else 1)
    assert row['actual_native_paid_caps']==row['actual_controller_paid_caps']
    assert row['native_controller_debt_views_may_be_summed']is row['original_cleanup_proved']is False


@pytest.mark.parametrize('fault',('owner','native_debt','controller_debt','refund_caps','start','boolean_count','identity','materialized_run','epoch','monotonic'))
def test_no_refund_materialization_start_or_original_clock_substitution(actual,fault):
    unit,row,policy,observed,values=actual
    if fault=='owner':row['owner']=row['owner']['original_owner']
    elif fault=='native_debt':row['actual_native_paid_caps']['radio_attempts']-=1
    elif fault=='controller_debt':row['actual_controller_paid_caps']['radio_attempts']-=1
    elif fault=='refund_caps':row['paid_caps']['radio_attempts']-=1
    elif fault=='start':observed['detail']['start_count']=1
    elif fault=='boolean_count':observed['detail']['prepared_count']=bool(observed['detail']['prepared_count'])
    elif fault=='identity':row['preparation_sha256']={history.UNMATERIALIZED_RUN:'f'*64}
    elif fault=='materialized_run':row['runs']=[dict(run_id=history.UNMATERIALIZED_RUN)]
    elif fault=='epoch':observed['observation_original_epoch_us']=1792024207143315
    else:
        owner=row['owner']['original_owner'];observed['observation_original_monotonic_us']=owner['native_phase'][unit]['deadline_monotonic_us']+1792024207143316-owner['phase_end_epoch_us']-1
    with pytest.raises((WireError,RuntimeError,KeyError)):history.qualify_unmaterialized_costs(unit,policy,observed,row)


def test_pending_receipt_does_not_admit_even_complete_metadata(actual,monkeypatch):
    unit,row,policy,observed,values=actual
    monkeypatch.setattr(history,'UNMATERIALIZED_NEXT_HISTORY_SHA256',None)
    with pytest.raises(WireError,match='pending_e244'):history.qualify_unmaterialized_costs(unit,policy,observed,row)


def stats(s):
    return dict(dev=s.st_dev,inode=s.st_ino,type=stat.S_IFMT(s.st_mode),mode=stat.S_IMODE(s.st_mode),
        uid=s.st_uid,gid=s.st_gid,nlink=s.st_nlink,bytes=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns)


@pytest.fixture
def owned(actual,monkeypatch):
    unit,row,policy,observed,values=actual
    with tempfile.TemporaryDirectory(prefix='e244-fd-control-',dir=Path(__file__).parent)as directory:
        state=Path(directory)/'state';parent=state/'parents'/history.UNMATERIALIZED_PARENT
        (parent/'prepared-identities').mkdir(parents=True,mode=0o700);(state/'runs').mkdir(mode=0o700)
        contents={str(parent.relative_to(state))+'/'+name:canonical(value)for name,value in
            (('policy.json',row['policy']),('owner.json',row['owner']),('prior.json',row['prior_metadata']))}
        contents.update(values['selected_public'][unit])
        # Unavailable capture/SQL bytes are explicitly local filename fixtures.
        # They are not reconstructed protected state or observation evidence.
        for name in ('capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json',
                     'native.sqlite','native.sqlite.owner','native.sqlite.approval'):
            contents[str(parent.relative_to(state))+'/'+name]=b'local inert metadata filename fixture\n'
        for name,raw in contents.items():
            path=state/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(raw);path.chmod(0o600)
        for name,record in row['unmaterialized_staging'].items():
            target=state/'runs'/name
            for leaf,value in record['opaque_leaf_stats'].items():
                path=target/leaf;path.write_bytes(b'\0'*value['bytes']);path.chmod(0o600)
        for path in state.rglob('*'):
            if path.is_dir():path.chmod(0o700)
        state.chmod(0o700)
        files={name:hashlib.sha256(raw).hexdigest()for name,raw in contents.items()}
        model_files=deepcopy(history.UNMATERIALIZED_PUBLIC_FILES);model_staging=deepcopy(history.UNMATERIALIZED_STAGING)
        model_files[unit]={name:files[name]for name in model_files[unit]}
        for name,record in model_staging[unit].items():
            target=state/'runs'/name;record['identity']=stats(target.stat())
            record['opaque_leaf_stats']={leaf:stats((target/leaf).stat())for leaf in record['opaque_leaf_stats']}
        monkeypatch.setattr(history,'UNMATERIALIZED_PUBLIC_FILES',model_files)
        monkeypatch.setattr(history,'UNMATERIALIZED_STAGING',model_staging)
        row['public_metadata_sha256']=model_files[unit];row['unmaterialized_staging']=model_staging[unit]
        directories={'':sorted(os.listdir(state))}
        for path in state.rglob('*'):
            if path.is_dir():directories[str(path.relative_to(state))]=sorted(os.listdir(path))
        yield unit,row,state,files,directories


def test_real_reader_and_sealed_history_keep_staging_public_bytes_and_opaque_stat_only(owned,monkeypatch):
    from lantern_meshticulum.bench.history_identity_seal import capture,SealedHistory
    unit,row,state,files,directories=owned
    original=rpc.Reader.read_held;seen=[]
    def public(self,path,maximum,**kwargs):
        assert path.name not in ('secret.bin','sender.bin');seen.append(path);return original(self,path,maximum,**kwargs)
    monkeypatch.setattr(rpc.Reader,'read_held',public)
    with rpc.Reader()as reader:
        assert history.unmaterialized_metadata(reader,state,unit,row)==row['public_metadata_sha256']
        reader.check()
    assert reader._closed and seen
    for fd in reader._tokens:
        with pytest.raises(OSError):os.fstat(fd)
    raw,pin=capture(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
    with SealedHistory(state,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin)as held:
        held.check();assert not any(p.endswith(('secret.bin','sender.bin'))for p in held._fence._fds)
    assert held._closed and not held._fence._fds


@pytest.mark.parametrize('fault',('child_directory','identity','extra_stage','foreign_capture'))
def test_actual_missing_run_and_exact_fence_addition_refuse_unrelated_metadata(owned,fault):
    from lantern_meshticulum.bench.history_metadata_fence import FenceError
    from lantern_meshticulum.bench.history_identity_seal import capture
    unit,row,state,files,directories=owned
    relative='parents/'+history.UNMATERIALIZED_PARENT
    if fault=='child_directory':
        (state/'runs'/history.UNMATERIALIZED_RUN).mkdir(mode=0o700)
        with rpc.Reader()as reader,pytest.raises(WireError,match='missing_materialization'):
            history.unmaterialized_metadata(reader,state,unit,row)
        return
    name=(relative+'/prepared-identities/'+history.UNMATERIALIZED_RUN+'.json'if fault=='identity'else
          relative+'/fast-stage-1.json'if fault=='extra_stage'else'parents/'+('f'*32)+'/capture.json')
    path=state/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(b'{}');path.chmod(0o600)
    files[name]=hashlib.sha256(b'{}').hexdigest();directories[path.parent.relative_to(state).as_posix()]=sorted(os.listdir(path.parent))
    if fault=='foreign_capture':directories['parents']=sorted(os.listdir(state/'parents'))
    with pytest.raises(FenceError):capture(state,owner_uid=os.geteuid(),expected_files=files,expected_directories=directories)
