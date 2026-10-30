"""Pinned matched workloads: local temporary files only, no preparation or RF.

The option changes useful bytes, not signed-pack, session or grant identities.
These controls establish source behavior, not matched physical performance.
"""
import copy
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import controller


def input_config(path):
    return {'useful_input': {'path': str(path),
                             'sha256': controller.sha(path.read_bytes())}}


def test_pinned_useful_bytes_are_identical_and_input_is_not_modified(tmp_path):
    path=tmp_path/'previous-INPUT.bin'
    data=bytes(range(256))*93+b'full-part-tail'
    path.write_bytes(data)
    config=input_config(path);original=copy.deepcopy(config)
    before=path.stat()
    with patch.object(controller.secrets,'token_bytes',side_effect=AssertionError('no random payload')):
        assert controller.useful_data(config,len(data))==data
        assert controller.useful_data(config,len(data))==data
    assert config==original and path.read_bytes()==data
    assert path.stat().st_mtime_ns==before.st_mtime_ns
    assert set(tmp_path.iterdir())=={path}


def test_omission_preserves_random_generation_per_call():
    with patch.object(controller.secrets,'token_bytes',side_effect=[b'abc',b'def']) as random:
        assert controller.useful_data({},3)==b'abc'
        assert controller.useful_data({},3)==b'def'
    assert random.call_count==2 and all(call.args==(3,) for call in random.call_args_list)


@pytest.mark.parametrize('size',[1,1_000_000])
def test_exact_input_size_boundaries(tmp_path,size):
    path=tmp_path/'input';path.write_bytes(b'x'*size)
    assert controller.useful_data(input_config(path),size)==b'x'*size


@pytest.mark.parametrize('requested',[2,4])
def test_pinned_size_must_equal_useful_bytes(tmp_path,requested):
    path=tmp_path/'input';path.write_bytes(b'abc')
    with pytest.raises(ValueError,match='useful_input_size_mismatch'):
        controller.useful_data(input_config(path),requested)


@pytest.mark.parametrize('length',[0,1_000_001])
def test_empty_or_oversized_pinned_file_is_refused(tmp_path,length):
    path=tmp_path/'input';path.write_bytes(b'x'*length)
    with pytest.raises(ValueError,match='invalid_input_file'):
        controller.useful_data(input_config(path),1)


def test_changed_bytes_or_wrong_hash_are_refused(tmp_path):
    path=tmp_path/'input';path.write_bytes(b'abc');config=input_config(path)
    path.write_bytes(b'def')
    with pytest.raises(ValueError,match='input_pin_mismatch'):
        controller.useful_data(config,3)


def test_missing_pinned_file_is_refused(tmp_path):
    config={'useful_input':{'path':str(tmp_path/'absent'),'sha256':'a'*64}}
    with pytest.raises(FileNotFoundError):
        controller.useful_data(config,3)


@pytest.mark.parametrize('spec',[None,False,[],3,'input',{},
    {'path':'file'},{'sha256':'a'*64},{'path':'file','sha256':'a'*64,'extra':True}])
def test_exact_input_object_required(spec):
    with pytest.raises(ValueError,match='invalid_useful_input'):
        controller.useful_data({'useful_input':spec},3)


@pytest.mark.parametrize('path',[None,True,3,[],{},'', 'bad\x00path',Path('input')])
def test_json_string_path_required(path):
    with pytest.raises(ValueError,match='invalid_useful_input_path'):
        controller.useful_data({'useful_input':{'path':path,'sha256':'a'*64}},3)


@pytest.mark.parametrize('pin',[None,True,3,[],{},'', 'a'*63,'A'*64,'z'*64])
def test_exact_lowercase_digest_required(tmp_path,pin):
    path=tmp_path/'input';path.write_bytes(b'abc')
    with pytest.raises(ValueError,match='invalid_digest'):
        controller.useful_data({'useful_input':{'path':str(path),'sha256':pin}},3)


@pytest.mark.parametrize('kind',['directory','symlink','hardlink','fifo'])
def test_nonregular_or_aliased_input_is_refused(tmp_path,kind):
    target=tmp_path/'target';target.write_bytes(b'abc');path=tmp_path/'input'
    if kind=='directory':path.mkdir()
    elif kind=='symlink':path.symlink_to(target)
    elif kind=='hardlink':os.link(target,path)
    else:os.mkfifo(path)
    config={'useful_input':{'path':str(path),'sha256':controller.sha(b'abc')}}
    with pytest.raises(ValueError,match='invalid_(useful_)?input_file'):
        controller.useful_data(config,3)


def test_regular_path_replaced_by_fifo_cannot_block_open_or_reach_read(tmp_path):
    path=tmp_path/'input';path.write_bytes(b'abc');config=input_config(path)
    real_open=os.open;opened=[]
    def replace_before_open(name,flags,*args,**kwargs):
        assert Path(name)==path
        assert flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW
        path.unlink();os.mkfifo(path)
        fd=real_open(name,flags,*args,**kwargs);opened.append(fd)
        return fd
    with patch.object(controller.os,'open',side_effect=replace_before_open), \
            patch.object(controller.os,'read',side_effect=AssertionError('FIFO must fail before read')):
        with pytest.raises(ValueError,match='invalid_input_file'):
            controller.useful_data(config,3)
    assert len(opened)==1
    with pytest.raises(OSError):os.fstat(opened[0])


def test_replacement_after_open_is_detected_and_owned_descriptor_closes(tmp_path):
    path=tmp_path/'input';path.write_bytes(b'abc');config=input_config(path)
    real_read=os.read;opened=[]
    def replace_after_read(fd,limit):
        assert limit==1_000_001
        data=real_read(fd,limit);opened.append(fd)
        path.unlink();path.write_bytes(b'abc')
        return data
    with patch.object(controller.os,'read',side_effect=replace_after_read):
        with pytest.raises(ValueError,match='input_changed'):
            controller.useful_data(config,3)
    assert len(opened)==1
    with pytest.raises(OSError):os.fstat(opened[0])


@pytest.mark.parametrize('size',[True,False,0,-1,1.0,'3',None,1_000_001])
def test_workload_bounds_apply_before_random_generation(size):
    with patch.object(controller.secrets,'token_bytes',side_effect=AssertionError('invalid random allocation')):
        with pytest.raises(ValueError,match='invalid_integer|workload_too_large'):
            controller.useful_data({},size)
