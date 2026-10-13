# SPDX-License-Identifier: Apache-2.0
"""Private management-input transport. This module grants no native authority."""
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import stat
import sys
import time

RAW_MAX = 2500000
COMPRESSED_MAX = 262144
DECODER_MEMORY_MAX = 33554432
DIRECTORY = 'meshticulum-bench-management-inputs'
SCHEMA = 'meshticulum.staged-management-input.v1'
ACTIONS = ('capture','enroll','guard','audit','observe-cache')


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def parameters(unit,action,parent,operation,nonce,end):
    need(unit in ('unit-a','unit-b','unit-c') and action in ACTIONS,'management_input_scope')
    need(type(parent) is str and re.fullmatch('[0-9a-f]{32}',parent)
         and type(operation) is str and re.fullmatch('[0-9a-f]{64}',operation)
         and type(nonce) is str and re.fullmatch('[0-9a-f]{32}',nonce)
         and type(end) is int and end>0,'management_input_binding')


def encode(value):
    need(type(value) is dict,'management_input_dictionary_required')
    raw=canonical(value);need(len(raw)<=RAW_MAX,'management_input_raw_bound')
    compressed=lzma.compress(raw,format=lzma.FORMAT_XZ,preset=6|lzma.PRESET_EXTREME)
    need(len(compressed)<=COMPRESSED_MAX,'management_input_compressed_bound')
    return compressed,sha(compressed),sha(raw)


def decode(compressed,compressed_sha256,raw_sha256):
    need(type(compressed) is bytes and len(compressed)<=COMPRESSED_MAX
         and sha(compressed)==compressed_sha256,'management_input_compressed_pin')
    decoder=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=DECODER_MEMORY_MAX)
    raw=decoder.decompress(compressed,max_length=RAW_MAX+1)
    need(len(raw)<=RAW_MAX and decoder.eof and not decoder.unused_data
         and sha(raw)==raw_sha256,'management_input_raw_pin')
    value=json.loads(raw);need(type(value) is dict,'management_input_dictionary_required')
    return value


def _stamp(value):
    return (value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid,
            value.st_nlink,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


class PrivateInput:
    """Hold the exact private directory and file; cleanup cannot delete a replacement."""
    def __init__(self,path,*,owner_uid,nonce,compressed_sha256,expected_identity=None,expected_directory_identity=None):
        self.parent_fd=self.fd=None;self.closed=False
        self.path=Path(path).absolute()
        need(type(owner_uid) is int and owner_uid>=0 and len(str(self.path))<=1024
             and self.path.parent.name==DIRECTORY
             and self.path.name=='rpc-'+nonce+'-'+compressed_sha256+'.xz','management_input_path')
        self.owner_uid=owner_uid
        self.ancestry=[]
        try:
            for parent in (self.path.parent,*self.path.parent.parents):
                info=parent.lstat()
                need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0,owner_uid)
                     and not info.st_mode&0o022 and parent.resolve()==parent,'management_input_ancestry')
                if parent==self.path.parent:
                    need(info.st_uid==owner_uid and stat.S_IMODE(info.st_mode)==0o700,'management_input_directory')
                self.ancestry.append((parent,(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)))
            self.parent_fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            self.fd=os.open(self.path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=self.parent_fd)
            first=os.fstat(self.fd)
            need(stat.S_ISREG(first.st_mode) and first.st_uid==owner_uid and first.st_nlink==1
                 and stat.S_IMODE(first.st_mode)==0o600 and first.st_size<=COMPRESSED_MAX,'management_input_file')
            self.identity=_stamp(first);self.check()
            if expected_identity is not None:
                need(tuple(expected_identity)==self.identity,'management_input_staged_identity_changed')
            if expected_directory_identity is not None:
                need(tuple(expected_directory_identity)==self.ancestry[0][1],'management_input_staged_directory_changed')
        except BaseException:
            self.close();raise
    def check(self):
        need(not self.closed and self.fd is not None and self.parent_fd is not None,'management_input_closed')
        need(_stamp(os.fstat(self.fd))==self.identity
             ==_stamp(os.stat(self.path.name,dir_fd=self.parent_fd,follow_symlinks=False)),
             'management_input_changed')
        for path,identity in self.ancestry:
            info=path.lstat()
            need((info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)==identity
                 and path.resolve()==path,'management_input_ancestry_changed')
        parent=os.fstat(self.parent_fd)
        need((parent.st_dev,parent.st_ino,parent.st_mode,parent.st_uid,parent.st_gid)==self.ancestry[0][1],
             'management_input_directory_changed')
    def read(self):
        self.check();raw=os.pread(self.fd,COMPRESSED_MAX+1,0)
        need(len(raw)==self.identity[6],'management_input_short_read');self.check();return raw
    def remove(self):
        self.check();os.unlink(self.path.name,dir_fd=self.parent_fd);os.fsync(self.parent_fd)
    def close(self):
        if self.closed:return
        for name in ('fd','parent_fd'):
            fd=getattr(self,name,None)
            if fd is not None:os.close(fd);setattr(self,name,None)
        self.closed=True


def _write_nonce(directory_fd,record):
    name=record['operation_sha256']+'-'+record['nonce']+'.json'
    output=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory_fd)
    with os.fdopen(output,'wb') as stream:stream.write(canonical(record));stream.flush();os.fsync(stream.fileno())
    os.fsync(directory_fd)


def claim_nonce(directory,record):
    """Root-owned O_EXCL bookkeeping, separate from parent and attempt ledgers."""
    need(os.geteuid()==0,'management_input_root_required')
    path=Path(directory).absolute()
    need(path==Path('/run/meshticulum-bench/management-input-used'),'management_input_receipt_scope')
    for parent in (path.parent,*path.parent.parents):
        info=parent.lstat();need(stat.S_ISDIR(info.st_mode) and info.st_uid==0
            and not info.st_mode&0o022 and parent.resolve()==parent,'management_input_receipt_ancestry')
    try:path.mkdir(mode=0o700)
    except FileExistsError:pass
    info=path.lstat();need(stat.S_ISDIR(info.st_mode) and info.st_uid==0
        and stat.S_IMODE(info.st_mode)==0o700 and path.resolve()==path,'management_input_receipt_directory')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        need((os.fstat(fd).st_dev,os.fstat(fd).st_ino)==(info.st_dev,info.st_ino),'management_input_receipt_changed')
        _write_nonce(fd,record)
    finally:os.close(fd)


def consume(path,*,unit,action,parent_id,operation_sha256,nonce,compressed_sha256,raw_sha256,
            original_end_epoch_us,expected_stage_uid,expected_identity,expected_directory_identity):
    """Root entry supplies exact data/scope pins; dispatch still performs its own admission."""
    need(os.geteuid()==0,'management_input_root_required')
    sudo_uid=os.environ.get('SUDO_UID','0')
    need(type(sudo_uid) is str and re.fullmatch('[0-9]{1,10}',sudo_uid)
         and type(expected_stage_uid) is int and expected_stage_uid==int(sudo_uid),
         'management_input_stage_owner_changed')
    parameters(unit,action,parent_id,operation_sha256,nonce,original_end_epoch_us)
    need(all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in (compressed_sha256,raw_sha256)),
         'management_input_hashes')
    start_epoch=time.time_ns()//1000;start_mono=time.monotonic_ns()//1000
    need(0<original_end_epoch_us-start_epoch<=120000000,'management_input_original_window')
    deadline=start_mono+original_end_epoch_us-start_epoch
    def current():
        epoch,mono=time.time_ns()//1000,time.monotonic_ns()//1000
        need(start_epoch<=epoch<original_end_epoch_us and start_mono<=mono<deadline,'management_input_window_expired')
    current();held=PrivateInput(path,owner_uid=expected_stage_uid,nonce=nonce,compressed_sha256=compressed_sha256,
        expected_identity=expected_identity,expected_directory_identity=expected_directory_identity)
    removed=False
    try:
        raw=held.read();current();value=decode(raw,compressed_sha256,raw_sha256);current();held.check()
        parent=value.get('parent_id',value.get('policy',{}).get('parent_id'))
        need(action=='observe-cache' or parent==parent_id,'management_input_parent_changed')
        if 'unit' in value:need(value['unit']==unit,'management_input_unit_changed')
        if 'review_sha256' in value:need(value['review_sha256']==operation_sha256,'management_input_review_changed')
        claim_nonce('/run/meshticulum-bench/management-input-used',dict(schema='meshticulum.management-input-used.v1',
            unit=unit,action=action,parent_id=parent_id,operation_sha256=operation_sha256,nonce=nonce,
            compressed_sha256=compressed_sha256,raw_sha256=raw_sha256,original_end_epoch_us=original_end_epoch_us,
            input_bytes=len(raw),native_authority=False,native_state_writes=False))
        current();held.remove();removed=True;current();return value
    finally:
        original=sys.exc_info()[1]
        try:
            if not removed:held.remove()
        except BaseException as cleanup_error:
            if original is None:raise
            original.add_note('management_input_cleanup_unproved')
        finally:held.close()


def cleanup(path,*,nonce,compressed_sha256,expected_stage_uid,expected_identity,expected_directory_identity):
    """Explicit exact-inode housekeeping; never dispatches or renews an input window."""
    need(os.geteuid()==0,'management_input_root_required')
    sudo_uid=os.environ.get('SUDO_UID','0')
    need(re.fullmatch('[0-9]{1,10}',sudo_uid) and type(expected_stage_uid) is int
         and expected_stage_uid==int(sudo_uid),'management_input_stage_owner_changed')
    try:
        held=PrivateInput(path,owner_uid=expected_stage_uid,nonce=nonce,compressed_sha256=compressed_sha256,
            expected_identity=expected_identity,expected_directory_identity=expected_directory_identity)
    except FileNotFoundError:
        target=Path(path).absolute()
        need(target.parent.name==DIRECTORY and target.name=='rpc-'+nonce+'-'+compressed_sha256+'.xz',
             'management_input_path')
        parent=target.parent
        for ancestor in (parent,*parent.parents):
            info=ancestor.lstat()
            need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0,expected_stage_uid)
                 and not info.st_mode&0o022 and ancestor.resolve()==ancestor,'management_input_ancestry')
        fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            info=os.fstat(fd)
            need((info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)==tuple(expected_directory_identity)
                 and stat.S_IMODE(info.st_mode)==0o700 and info.st_uid==expected_stage_uid
                 and (parent.lstat().st_dev,parent.lstat().st_ino)==(info.st_dev,info.st_ino),
                 'management_input_staged_directory_changed')
            try:os.stat(target.name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:return True
            raise RuntimeError('management_input_changed')
        finally:os.close(fd)
    try:held.remove();return False
    finally:held.close()


# Separate from the unchanged100000-byte child and700000-byte public-seed contracts.
# No PTY or sudo is used to stage this binary input.
STAGE_SOURCE=r'''import hashlib,json,os,re,stat,sys,time
from pathlib import Path
unit,action,parent,operation,nonce,pin,raw_pin,end=sys.argv[1:];end=int(end)
assert unit in ('unit-a','unit-b','unit-c') and action in ('capture','enroll','guard','audit','observe-cache')
assert all(re.fullmatch('[0-9a-f]{64}',v) for v in (operation,pin,raw_pin))
assert all(re.fullmatch('[0-9a-f]{32}',v) for v in (parent,nonce))
start=time.time_ns()//1000;mono=time.monotonic_ns()//1000;deadline=mono+end-start
assert 0<end-start<=120000000
def current():
 now=time.time_ns()//1000;m=time.monotonic_ns()//1000
 assert start<=now<end and mono<=m<deadline
current();data=sys.stdin.buffer.read(262145)
assert len(data)<=262144 and hashlib.sha256(data).hexdigest()==pin
root=Path.home()/'meshticulum-bench-management-inputs'
for p in (root.parent,*root.parent.parents):
 s=p.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid in (0,os.geteuid()) and not s.st_mode&0o022 and p.resolve()==p
root.mkdir(mode=0o700,exist_ok=True);s=root.lstat()
assert stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700 and root.resolve()==root
directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
try:
 assert (os.fstat(directory).st_dev,os.fstat(directory).st_ino)==(s.st_dev,s.st_ino)
 current();name='rpc-'+nonce+'-'+pin+'.xz'
 fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory)
 with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
 os.fsync(directory);current()
 info=os.stat(name,dir_fd=directory,follow_symlinks=False)
 identity=(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
 print(json.dumps(dict(schema='meshticulum.staged-management-input.v1',unit=unit,action=action,parent_id=parent,
  operation_sha256=operation,nonce=nonce,path=str(root/name),compressed_sha256=pin,raw_sha256=raw_pin,
  original_end_epoch_us=end,owner_uid=os.geteuid(),bytes=len(data),file_identity=identity,
  directory_identity=(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid))),flush=True)
finally:os.close(directory)
'''

STAGE_SHA256=sha(STAGE_SOURCE.encode())
