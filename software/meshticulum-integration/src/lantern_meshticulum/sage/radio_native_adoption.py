# SPDX-License-Identifier: Apache-2.0
"""Inert private radio ownership adoption; no opener, ARM, grant or record writer.

An independently provisioned Core transport and locally signed protected radio
enrollment are mandatory. Adoption occurs inside the original owner lease only
after full prepayment and registered containment. MeshCore must already have
released its producer/client; this API never closes or resets that producer.
FD release is distinct from physical disarm and native qualification.
"""
import base64
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import threading

from nacl.signing import VerifyKey
from meshticulum.bench.authority import canonical as radio_canonical
from meshticulum.bench.linux_hat import LinuxHAT
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.wire import BenchGrant
from meshticulum.contracts.identity import PeerID, SessionKey, ServiceKind
from .forward_budget import ChildPlan, Cost
from .forward_native_owner import ForwardNativeOwner
from .mesh_inbox import _canonical, _id
from .workflow_store import WorkflowStore
from ..bench.profiles import RADIO_ADMISSION

SCHEMA = 'sage.lantern.native-forward-radio-adoption.v1'
DOMAIN = b'sage.lantern.native-forward-radio-adoption.v1/signature\0'
SCOPE = 'meshticulum_forward_radio_adopt'
SOURCE_MODULES = ('meshticulum.bench.serial_port', 'meshticulum.bench.linux_hat',
                  'meshticulum.bench.serial_worker', 'meshticulum.bench.hat_worker',
                  'meshticulum.bench.authority', 'meshticulum.bench.flow_runtime',
                  'meshticulum.bench.wire', 'meshticulum.bench.state',
                  'meshticulum.flow.flow', 'meshticulum.flow.flow_port',
                  'meshticulum.privacy.forward_session',
                  'lantern_meshticulum.sage.forward_native_owner',
                  'lantern_meshticulum.sage.forward_operation',
                  'lantern_meshticulum.sage.forward_budget',
                  'lantern_meshticulum.sage.workflow_store',
                  'lantern_meshticulum.sage.radio_native_adoption')


class RadioAdoptionError(RuntimeError):
    def __init__(self):super().__init__('native_radio_adoption_unavailable')


def require(value):
    if not value:raise RadioAdoptionError()


def source_pins():
    """Comparison metadata; only the protected signed record authorizes pins."""
    return {name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
            for name in SOURCE_MODULES}


def _identity(fd):
    require(type(fd) is int and fd >= 0)
    info = os.fstat(fd)
    return info.st_dev, info.st_ino, info.st_rdev, info.st_mode, info.st_uid


def _exclusive(identity):
    """Observe the existing kernel FLOCK; never acquire/upgrade ownership."""
    key = os.major(identity[0]), os.minor(identity[0]), identity[1]
    for line in Path('/proc/locks').read_text().splitlines():
        row=line.split()
        if len(row)!=8 or row[1:4]!=['FLOCK','ADVISORY','WRITE'] or row[4]!=str(os.getpid()):continue
        major,minor,inode=row[5].split(':')
        if (int(major,16),int(minor,16),int(inode))==key:return True
    return False


class NativeRadioAdoption:
    def __init__(self, owner, child, directory, transport):
        require(type(owner) is ForwardNativeOwner and type(child) is ChildPlan
                and child.carrier == 'lora' and type(transport) in (ExclusiveSerial, LinuxHAT))
        require(owner._composition is not None and type(owner._composition.store) is WorkflowStore)
        self.owner, self.child, self.transport = owner, child, transport
        self.store = owner._composition.store
        require(self.store.client is self.store.storage.client is owner.resource.probe.client)
        self.directory = Path(directory)
        require(self.directory.is_absolute() and '..' not in self.directory.parts)
        self._refs = (owner, child, transport, self.store, self.store.client, self.store.storage,
                      self.directory, owner.resource, owner.authority, owner.native_context)
        self._process = os.getpid(), threading.get_ident()
        self._busy = self._adopted = self._closed = self._uncertain = False
        self._child = None;self._highest = 0;self._record = None;self._files = {};self._directory_fd = None
        self._last_time=None;self._child_value=_canonical(child.to_dict())
        self._names = ('fd','lock_fd') if type(transport) is ExclusiveSerial else ('spi_fd','chip_fd','lines_fd','lock_fd')
        self._fds = tuple((name, getattr(transport,name), _identity(getattr(transport,name))) for name in self._names)
        device = self._fds[0][2]
        require(stat.S_ISCHR(device[3]) and device[4] == os.geteuid())
        lock = self._fds[-1][2]
        require(stat.S_ISREG(lock[3]) and stat.S_IMODE(lock[3]) == 0o600 and lock[4] == os.geteuid())
        self._methods = tuple((name, getattr(type(transport),name)) for name in
                              (('read','write','close','monotonic') if type(transport) is ExclusiveSerial
                               else ('transfer','close','monotonic','reset','receive_switch')))
        self._fd_refs,self._method_refs=self._fds,self._methods
        self.record_id = _id(SCHEMA,[owner.local.to_dict(),owner.intention.operation_id,child.slot])
        try:
            self._guard();self._read_grant()
            self._binding = _canonical(dict(context_digest=owner.context.digest.hex(),exchange_approval=owner._approval,
                child=child.to_dict(),config_revision=owner.config_revision,broker_id=owner.broker_id,
                storage_epoch=owner.storage_epoch,directory=str(self.directory),grant_files=self._file_hashes,
                transport=type(transport).__name__,device_fds=[[n,list(identity)] for n,_,identity in self._fds],
                sources=source_pins()))
            self._binding_ref = self._binding
            self._guard()
        except BaseException:
            self.release();raise

    def __repr__(self):return '<NativeRadioAdoption>'

    def _bound(self):
        require(not self._closed and not self._uncertain and self._process==(os.getpid(),threading.get_ident()))
        require(all(a is b for a,b in zip(self._refs,(self.owner,self.child,self.transport,self.store,
            self.store.client,self.store.storage,self.directory,self.owner.resource,self.owner.authority,self.owner.native_context))))
        require(self._fds is self._fd_refs and self._methods is self._method_refs
                and _canonical(self.child.to_dict())==self._child_value)
        require(all(getattr(self.transport,n)==fd and _identity(fd)==identity for n,fd,identity in self._fds))
        require(_exclusive(self._fds[0][2]) and _exclusive(self._fds[-1][2]))
        require(all(name not in self.transport.__dict__ and getattr(type(self.transport),name) is method
                    for name,method in self._methods))
        if hasattr(self,'_binding_ref'):require(self._binding is self._binding_ref)

    def _guard(self):
        self._bound();o=self.owner;h=o.resource
        o.phase_check(o.context,self.child.kind,self.child.sends);self._bound()
        require(o.authority.authorized(o.native_context,SCOPE,None) is True);self._bound();o._available()
        require(h._provider is None and getattr(h,'_coordinator',None) is None
                and not getattr(h,'_initializing',False) and getattr(h,'_radio_observer',None) is None
                and (getattr(h,'_forward_radio',None) is None or h._forward_radio is self))
        require(o._operation is not None and not o._operation._closed and not o._operation._uncertain
                and not o._operation.session._closed and o._operation.expected()==(self.child.kind,self.child.sends))
        now=o._clock();self._bound();o._available()
        require(type(now) is tuple and all(type(v) is int for v in now)
                and o.context.issued_epoch_us<=now[0]<self.child.application_expires_epoch_us
                and now[1]<self.child.application_deadline_monotonic_us
                and (self._last_time is None or all(a>=b for a,b in zip(now,self._last_time))))
        self._last_time=now
        require(self.child.host_boot_id==o.intention.host_boot_id
                and self.child.application_expires_epoch_us==o.context.expires_epoch_us
                and self.child.application_deadline_monotonic_us<=o.intention.application_deadline_monotonic_us)
        if type(self.transport) is LinuxHAT:require(self.transport.boot_id==self.child.host_boot_id)
        return True

    def _read_grant(self):
        for path in (self.directory,*self.directory.parents):
            info=path.lstat()
            require(path.resolve()==path and stat.S_ISDIR(info.st_mode) and info.st_uid in (0,os.geteuid())
                    and (not info.st_mode & 0o022 or info.st_uid==0 and info.st_mode & stat.S_ISVTX))
        self._directory_fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        info=os.fstat(self._directory_fd)
        require(stat.S_IMODE(info.st_mode)==0o700 and info.st_uid==os.geteuid())
        self._directory_identity=_identity(self._directory_fd);self._file_hashes={};values={}
        try:
            for name,maximum in (('grant.json',16384),('secret.bin',32),('active',7)):
                self._guard();fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self._directory_fd)
                info=os.fstat(fd);self._files[name]=(fd,_identity(fd))
                require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600
                        and info.st_uid==os.geteuid() and info.st_nlink==1)
                raw=os.pread(fd,maximum+1,0);require(0<len(raw)<=maximum)
                values[name]=raw;self._file_hashes[name]=hashlib.sha256(raw).hexdigest();self._guard()
            require(values['active']==b'active\n')
            self._validate_plan(json.loads(values['grant.json']),values['secret.bin'])
            self._file_refs=tuple(self._files.items());self._hash_refs=tuple(sorted(self._file_hashes.items()))
            self._directory_ref=self._directory_fd
        except BaseException:
            self.release();raise

    def _validate_plan(self,p,secret):
        c=self.child;o=self.owner;first=o.context.initiator if c.kind%2 else o.context.responder
        second=o.context.responder if c.kind%2 else o.context.initiator
        peer=lambda value:PeerID(value.chain_id,value.public_key.hex())
        require(type(p) is dict and set(p)=={'schema','unit','host_boot_id','local_peer','initiator','responder',
            'session_id','generation','issued_epoch_us','expires_epoch_us','profile','object','sender_unit','country',
            'antenna_confirmed','max_frames','service','application_deadline_monotonic_us'})
        require(p['schema']=='meshticulum.service-run.v1' and p['host_boot_id']==c.host_boot_id
            and PeerID(**p['local_peer'])==peer(o.local)
            and PeerID(**p['initiator'])==peer(first) and PeerID(**p['responder'])==peer(second)
            and (p['sender_unit']==p['unit'])==c.sends
            and p['issued_epoch_us']==o.context.issued_epoch_us and p['expires_epoch_us']==o.context.expires_epoch_us
            and p['service']==dict(kind=ServiceKind.MESSAGE.value,format='MPF2.record',application_expires_epoch_us=o.context.expires_epoch_us)
            and p['application_deadline_monotonic_us']==c.application_deadline_monotonic_us
            and p['object']['bytes']==c.packet_bytes and p['object']['sha256']==c.packet_sha256)
        profile=RadioProfile(**p['profile']);RADIO_ADMISSION.check(p['country'],p['antenna_confirmed'],profile)
        require(hashlib.sha256(radio_canonical(asdict(profile))).hexdigest()==c.profile_digest
                and c.cost==Cost(profile.max_boot_attempts,profile.max_boot_estimated_airtime_us)
                and p['max_frames']==c.cipher_frame_limit)
        route={key:p[key] for key in ('initiator','responder','session_id','generation','profile','object','sender_unit','country','service')}
        grant=BenchGrant(SessionKey(peer(first),peer(second),bytes.fromhex(p['session_id']),bytes.fromhex(p['generation'])),
            'bench_transfer',hashlib.sha256(radio_canonical(route)).digest(),p['issued_epoch_us'],p['expires_epoch_us'],208,p['max_frames'],secret)
        require(grant.digest.hex()==c.grant_digest)

    def _assets(self):
        require(tuple(self._files.items())==self._file_refs and tuple(sorted(self._file_hashes.items()))==self._hash_refs
                and self._directory_fd==self._directory_ref)
        require(_identity(self._directory_fd)==self._directory_identity)
        info=self.directory.lstat()
        require((info.st_dev,info.st_ino)==self._directory_identity[:2])
        for name,(fd,identity) in self._files.items():
            require(_identity(fd)==identity)
            named=os.stat(name,dir_fd=self._directory_fd,follow_symlinks=False)
            require((named.st_dev,named.st_ino)==identity[:2] and named.st_nlink==1)
            require(hashlib.sha256(os.pread(fd,16385,0)).hexdigest()==self._file_hashes[name])
        require(source_pins()==json.loads(self._binding)['sources'])

    def _observe(self):
        self._guard();self._assets()
        record=self.store.get(self.record_id,kind='mesh_outbound',access_check=self._guard)
        self._guard();value=record['payload'];body=value['body']
        require(set(value)=={'schema','body','signature'} and value['schema']==SCHEMA)
        VerifyKey(self.owner.local.public_key).verify(DOMAIN+_canonical(body),base64.b64decode(value['signature'],validate=True))
        require(set(body)=={'record_id','revision','binding','state'} and body['record_id']==record['record_id']==self.record_id
                and type(body['revision']) is int and body['revision']==record['revision']>0
                and _canonical(body['binding'])==self._binding and body['state']=='active')
        raw=_canonical(body)
        require(body['revision']>=self._highest and (body['revision']!=self._highest or raw==self._record))
        self._highest,self._record=body['revision'],raw;self._assets();self._guard();return True

    def adopt(self,resource):
        require(resource is self.owner.resource and not self._busy and not self._adopted)
        self._busy=True
        try:
            self._observe();o=self.owner;op=o._operation
            require(o._held and o._refs[-1].locked() and op is not None and op._busy)
            matches=[c for c in o._registry._children if c.owner is o and c.plan is self.child
                     and c.opening and c.open_thread==threading.get_ident() and not c.unknown]
            require(len(matches)==1 and any(a.plan is self.child and not a.bound for a in op.budget._permits.values()))
            require(getattr(resource,'_forward_radio',None) is None)
            self._child=matches[0];resource._forward_radio=self;self._adopted=True
            self._observe();return self
        except BaseException:
            self._uncertain=True;self.release();raise
        finally:self._busy=False

    def current(self):
        if self._busy:self._uncertain=True;return False
        self._busy=True
        try:
            require(self._adopted and self.owner.resource._forward_radio is self)
            self.owner._registry._child(self._child);return self._observe()
        except Exception:self._uncertain=True;return False
        finally:self._busy=False

    def release(self):
        """Close only retained owned FDs; no RF-disarm or completion inference."""
        if self._process!=(os.getpid(),threading.get_ident()):return False
        if self._closed:return not self._uncertain
        ok=True
        if self._adopted:
            transport=self._refs[2];resource=self._refs[7]
            for name,fd,identity in self._fd_refs:
                try:
                    require(getattr(transport,name)==fd and _identity(fd)==identity)
                    os.close(fd)
                except Exception:ok=False
                finally:
                    if getattr(transport,name)==fd:setattr(transport,name,None)
            if getattr(resource,'_forward_radio',None) is self and ok:resource._forward_radio=None
        for _,(fd,identity) in getattr(self,'_file_refs',tuple(self._files.items())):
            try:
                require(_identity(fd)==identity);os.close(fd)
            except Exception:ok=False
        self._files.clear()
        directory_fd=getattr(self,'_directory_ref',self._directory_fd)
        if directory_fd is not None:
            try:
                require(_identity(directory_fd)==self._directory_identity);os.close(directory_fd)
            except Exception:ok=False
            self._directory_fd=None
        self._closed=True;self._uncertain=self._uncertain or not ok
        return ok

    def report(self):
        return dict(adopted=self._adopted,released=self._closed,uncertain=self._uncertain,
                    physical_disarmed=None,native_qualified=False)
