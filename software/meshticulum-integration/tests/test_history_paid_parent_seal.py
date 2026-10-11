# SPDX-License-Identifier: Apache-2.0
"""Local exact-parent metadata seals; no debt interpretation or authority."""
import hashlib
from pathlib import Path

import pytest
from lantern_meshticulum.bench.history_metadata_fence import FenceError, HistoricalMetadataFence

PAID='parents/d60d8308326314a7753e651fa43a99e3'
NESTED=PAID+'/prepared-identities'
METADATA=('capture.json','history-binding.json','history-identity-seal.json','legacy-grant-seal.json')


def fixture(tmp_path,count=9):
    root=tmp_path/'state'; root.mkdir(mode=0o700)
    (root/'parents').mkdir(mode=0o700); (root/PAID).mkdir(mode=0o700); (root/NESTED).mkdir(mode=0o700)
    files={}; names=['%032x.json' % (index+1) for index in range(count)]
    for relative in [PAID+'/'+name for name in METADATA]+[NESTED+'/'+name for name in names]:
        path=root/relative; raw=b'{"local_metadata_fixture":true}\n'; path.write_bytes(raw); path.chmod(0o600)
        files[relative]=hashlib.sha256(raw).hexdigest()
    dirs={'':['parents'],'parents':[PAID.split('/')[-1]],PAID:list(METADATA)+['prepared-identities'],NESTED:names}
    return root,files,dirs


@pytest.mark.parametrize('count',[0,9])
def test_only_empty_A_or_nine_B_C_identity_namespaces_are_sealed(tmp_path,count):
    import os
    root,files,dirs=fixture(tmp_path,count)
    with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs) as fence:
        fence.check()
        assert len([name for name in files if name.startswith(NESTED+'/')])==count


@pytest.mark.parametrize('case',['eight','ten','missing_pin','wrong_pin','malformed','zero','extra',
    'foreign_parent','foreign_metadata','receiver','key','symlink'])
def test_exact_paid_parent_allowance_does_not_relax_other_namespaces(tmp_path,case):
    import os
    root,files,dirs=fixture(tmp_path,8 if case=='eight' else 10 if case=='ten' else 9)
    name=dirs[NESTED][0]; relative=NESTED+'/'+name
    if case=='missing_pin': files.pop(relative)
    elif case=='wrong_pin': files[relative]='0'*64
    elif case in ('malformed','zero','receiver','key'):
        new={'malformed':'not-a-run.json','zero':'0'*32+'.json','receiver':'receiver.bin','key':'secret.bin'}[case]
        (root/relative).rename(root/NESTED/new); dirs[NESTED][0]=new; files[NESTED+'/'+new]=files.pop(relative)
    elif case=='extra':
        path=root/NESTED/('f'*32+'.json'); path.write_bytes(b'{}'); path.chmod(0o600)
    elif case=='foreign_parent': dirs['parents/'+('f'*32)+'/prepared-identities']=[]
    elif case=='foreign_metadata':
        other='parents/'+('e'*32); (root/other).mkdir(mode=0o700)
        path=root/other/'capture.json'; path.write_bytes(b'{}'); path.chmod(0o600)
        dirs['parents'].append('e'*32); dirs[other]=['capture.json']; files[other+'/capture.json']=hashlib.sha256(b'{}').hexdigest()
    else:
        (root/relative).rename(root/'held'); (root/relative).symlink_to(root/'held')
    with pytest.raises(FenceError):
        HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
