"""Real protected-filesystem H20 capture/seal controls; no native authority or RF."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

import pytest
from lantern_meshticulum.bench import history_metadata_fence as fence
from lantern_meshticulum.bench.history_identity_seal import capture,SealedHistory

PARENT='parents/141c892d83b6064a800c11aab6444f28'
NESTED=PARENT+'/prepared-identities'
NAMES=('capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json')


@pytest.fixture
def metadata():
 base=Path(tempfile.mkdtemp(prefix='h20-seal-',dir=Path(__file__).parent));base.chmod(0o700)
 root=base/'state';root.mkdir(mode=0o700);(root/'parents').mkdir(mode=0o700)
 (root/PARENT).mkdir(mode=0o700);(root/NESTED).mkdir(mode=0o700)
 files={}
 for name in NAMES:
  path=root/PARENT/name;raw=('public metadata for '+name).encode();path.write_bytes(raw);path.chmod(0o600)
  files[PARENT+'/'+name]=hashlib.sha256(raw).hexdigest()
 dirs={'':['parents'],'parents':[PARENT.split('/')[-1]],PARENT:[*NAMES,'prepared-identities'],NESTED:[]}
 try:yield root,files,dirs
 finally:shutil.rmtree(base)


def test_four_verified_capture_metadata_files_and_empty_identities_produce_genuine_strict_seal(metadata):
 root,files,dirs=metadata
 before={p.name:p.read_bytes() for p in (root/PARENT).iterdir() if p.is_file()}
 raw,pin=capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
 assert hashlib.sha256(raw).hexdigest()==pin
 with SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin) as held:held.check()
 assert before=={p.name:p.read_bytes() for p in (root/PARENT).iterdir() if p.is_file()}
 assert not list((root/NESTED).iterdir())


@pytest.mark.parametrize('fault',['wrong_parent','unknown_dir','extra_metadata','extra_unlisted_file','nonempty_identity',
    'wrong_pin','changed_source','symlink','mode','missing_file','bad_seal_pin'])
def test_exact_h20_parent_allowance_refuses_wrong_or_changed_scope(metadata,fault):
 root,files,dirs=metadata;relative=PARENT+'/capture.json';path=root/relative
 if fault=='wrong_parent':
  other='parents/'+('f'*32);(root/PARENT).rename(root/other)
  files={k.replace(PARENT,other):v for k,v in files.items()};dirs={k.replace(PARENT,other):v for k,v in dirs.items()};dirs['parents']=['f'*32]
 elif fault=='unknown_dir':
  other='parents/'+('e'*32);(root/other).mkdir(mode=0o700);dirs['parents'].append('e'*32);dirs[other]=['prepared-identities'];(root/other/'prepared-identities').mkdir(mode=0o700);dirs[other+'/prepared-identities']=[]
 elif fault in ('extra_metadata','extra_unlisted_file'):
  new=root/PARENT/'secret.bin';new.write_bytes(b'never read');new.chmod(0o600)
  if fault=='extra_metadata':dirs[PARENT].append('secret.bin');files[PARENT+'/secret.bin']=hashlib.sha256(b'never read').hexdigest()
 elif fault=='nonempty_identity':
  name='a'*32+'.json';new=root/NESTED/name;new.write_bytes(b'{}');new.chmod(0o600);dirs[NESTED].append(name);files[NESTED+'/'+name]=hashlib.sha256(b'{}').hexdigest()
 elif fault=='wrong_pin':files[relative]='0'*64
 elif fault=='changed_source':path.write_bytes(b'changed after hash')
 elif fault=='symlink':path.rename(root/'external');path.symlink_to(root/'external')
 elif fault=='mode':path.chmod(0o644)
 elif fault=='missing_file':path.unlink()
 if fault=='bad_seal_pin':
  raw,_=capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
  with pytest.raises(fence.FenceError):SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256='0'*64)
 else:
  with pytest.raises((fence.FenceError,OSError)):capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)


def test_later_same_inode_metadata_change_refuses_retained_seal(metadata):
 root,files,dirs=metadata;raw,pin=capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
 with SealedHistory(root,owner_uid=os.geteuid(),seal_bytes=raw,expected_sha256=pin) as held:
  (root/PARENT/'history-binding.json').write_bytes(b'rebound')
  with pytest.raises(fence.FenceError):held.check()



def test_original_11e_whitelist_reproduces_exact_native_failure(metadata,monkeypatch):
 import importlib.util
 from lantern_meshticulum.bench import history_identity_seal as seal
 source=Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-h20-integration-pair-20261012/sage-lantern/software/meshticulum-integration/src/lantern_meshticulum/bench/history_metadata_fence.py')
 if not source.is_file():pytest.skip('immutable local11e source not packaged')
 spec=importlib.util.spec_from_file_location('original_11e_fence',source)
 original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
 monkeypatch.setattr(seal,'HistoricalMetadataFence',original.HistoricalMetadataFence)
 root,files,dirs=metadata
 with pytest.raises(original.FenceError,match='outside_retained_capture_metadata'):
  seal.capture(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
