# SPDX-License-Identifier: Apache-2.0
"""Read-only scoped public journal evidence, never enrollment authority.

Only explicit protected public metadata files are opened. Existing owner locks
are acquired without creation. SQLite parses a bounded held-file byte image in
query-only memory; no native database is opened by SQLite, recovered or changed.
Parent reconciliation, live terminal ownership and physical/MCU observations
are omitted, so complete_global_debt and eligible_for_enrollment remain false.
"""
from dataclasses import dataclass, asdict
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import uuid

from meshticulum.contracts.identity import PeerID, SessionKey
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.public_block import PUBLIC32_MARKER, REPRESENTATION_MARKER, TRANSPORT_DESCRIPTOR_MARKER, public_mb1_service, public32_service, object_limit
from meshticulum.ip.wire import IpLimits, address, READY_MODE
from .pilot_parent_caps import Cost
from .pilot_ip_schema import IP_SCHEMAS, IP_V2, ip_metadata_grant
from .profiles import RADIO_ADMISSION, LEGACY_SF5_8_PARENT, LEGACY_SF5_8_ADMISSION,GFSK_NAME,GFSK250_NAME,profile_from_plan,admission_for,radio_route

MAX = 2**63-1
MAX_DB = 16*1024*1024
MAX_ROWS = 65536
FORMATS = ('MR1.request','MR1.response')
FILES = frozenset(('grant.json','active','provision.json','job.json','result.json',
    'attempts.sqlite','attempts.sqlite.owner','bytes.sqlite','bytes.sqlite.owner','wire.sqlite'))


class SnapshotError(RuntimeError):
    def __init__(self,reason='snapshot_unavailable'):
        self.reason=reason;super().__init__(reason)


def need(value,reason='invalid_public_metadata'):
    if not value:raise SnapshotError(reason)


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def integer(v,lo=0,hi=MAX):need(type(v) is int and lo<=v<=hi);return v
def hex_value(v,n):
    need(type(v) is str and len(v)==n and all(c in '0123456789abcdef'for c in v));return v
def boot(v):need(type(v) is str and str(uuid.UUID(v))==v and uuid.UUID(v).int!=0);return v
def identifier(v):hex_value(v,32);need(v!='0'*32);return v
def public_json(raw):
    def pairs(values):
        result={}
        for k,v in values:need(k not in result);result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(SnapshotError()))


@dataclass(frozen=True,slots=True)
class RunScope:
    run_id:str
    host_boot_id:str
    transport:str
    grant_sha256:str
    grant_context_sha256:str|None
    attempt_binding_sha256:str|None
    def __post_init__(self):
        identifier(self.run_id);boot(self.host_boot_id);need(self.transport in ('lora','tcp'))
        hex_value(self.grant_sha256,64)
        for v in (self.grant_context_sha256,self.attempt_binding_sha256):
            if v is not None:hex_value(v,64)


@dataclass(frozen=True,slots=True)
class Scope:
    schema:str
    unit:str
    current_host_boot_id:str
    runtime_sha256:str
    manifest_sha256:str
    observation_expires_epoch_us:int
    runs:tuple
    def __post_init__(self):
        need(self.schema=='meshticulum.public-debt-scope.v1' and self.unit in ('unit-a','unit-b','unit-c'))
        boot(self.current_host_boot_id);hex_value(self.runtime_sha256,64);hex_value(self.manifest_sha256,64)
        integer(self.observation_expires_epoch_us,1)
        need(type(self.runs) is tuple and 1<=len(self.runs)<=64 and all(type(r)is RunScope for r in self.runs)
             and len({r.run_id for r in self.runs})==len(self.runs))
    @classmethod
    def from_dict(cls,value):
        try:
            need(type(value)is dict and set(value)==set(cls.__dataclass_fields__) and type(value['runs'])is list)
            runs=[]
            for r in value['runs']:
                need(type(r)is dict and set(r)==set(RunScope.__dataclass_fields__));runs.append(RunScope(**r))
            return cls(**dict(value,runs=tuple(runs)))
        except Exception:raise SnapshotError('invalid_scope')from None


class _Observation:
    def __init__(self,scope,clock,boot_reader):
        need(os.geteuid()==0,'requires_root');need(type(scope)is Scope and callable(clock)and callable(boot_reader))
        self.scope,self.clock,self.boot_reader=scope,clock,boot_reader
        self.binding=canonical(asdict(scope));self.owner=(os.getpid(),threading.get_ident())
        self.first=self.last=None;self.deadline=None
    def __call__(self):
        need(os.geteuid()==0 and self.owner==(os.getpid(),threading.get_ident())
             and canonical(asdict(self.scope))==self.binding,'observation_owner_changed')
        need(self.boot_reader()==self.scope.current_host_boot_id,'current_boot_changed')
        now=self.clock()
        need(type(now)is tuple and len(now)==2 and all(type(x)is int and 0<x<=MAX for x in now),'invalid_clock')
        need(os.geteuid()==0 and self.owner==(os.getpid(),threading.get_ident())
             and canonical(asdict(self.scope))==self.binding,'observation_owner_changed')
        need(self.boot_reader()==self.scope.current_host_boot_id,'current_boot_changed')
        if self.first is None:
            need(0<self.scope.observation_expires_epoch_us-now[0]<=60000000,'observation_expired_or_unbounded')
            self.first=now;self.deadline=now[1]+self.scope.observation_expires_epoch_us-now[0]
        need(now[0]<self.scope.observation_expires_epoch_us and now[1]<self.deadline,'observation_expired')
        if self.last is not None:need(all(a>=b for a,b in zip(now,self.last)),'observation_clock_regressed')
        self.last=now;return now


def _private(s,directory=False):
    need((stat.S_ISDIR(s.st_mode)if directory else stat.S_ISREG(s.st_mode))and s.st_uid==0
         and stat.S_IMODE(s.st_mode)==(0o700 if directory else 0o600)
         and (directory or s.st_nlink==1),'unprotected_metadata')
def _identity(s):return (s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


class _Reader:
    def __init__(self,state,run_id,observe):
        self.observe=observe;self.dirs=[];self.files={};self.run=state/'runs'/run_id
        try:
            for path in (state,state/'runs',self.run):
                observe();need(path.resolve()==path,'symlinked_state')
                fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
                try:s=os.fstat(fd);_private(s,True)
                except BaseException:os.close(fd);raise
                self.dirs.append((path,fd,_identity(s)))
            self.dirfd=self.dirs[-1][1];self.check()
        except BaseException:self.close();raise
    def check(self):
        for path,fd,ident in self.dirs:
            held,named=os.fstat(fd),path.lstat();_private(held,True);_private(named,True)
            # Directory size/timestamps may reflect an unrelated scope entry;
            # its inode/mode/owner identity still must be exact.
            need(_identity(held)[:4]==ident[:4]==_identity(named)[:4],'directory_changed')
        for name,(fd,ident,raw)in self.files.items():
            held=os.fstat(fd);named=os.stat(name,dir_fd=self.dirfd,follow_symlinks=False)
            _private(held);_private(named)
            need(_identity(held)==ident==_identity(named) and os.pread(fd,len(raw)+1,0)==raw,'file_changed')
    def read(self,name,limit):
        need(name in FILES,'file_outside_public_scope');self.observe();self.check()
        if name not in self.files:
            named=os.stat(name,dir_fd=self.dirfd,follow_symlinks=False);_private(named)
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,dir_fd=self.dirfd)
            try:
                s=os.fstat(fd);_private(s);need(s.st_size<=limit,'metadata_too_large')
                need(_identity(s)==_identity(named),'file_changed_before_open')
                raw=os.pread(fd,limit+1,0);need(len(raw)==s.st_size,'short_metadata_read')
                self.files[name]=(fd,_identity(s),raw)
            except BaseException:os.close(fd);raise
        self.observe();self.check();return self.files[name][2]
    def absent_sidecars(self,name):
        for suffix in ('-wal','-shm','-journal'):
            try:os.stat(name+suffix,dir_fd=self.dirfd,follow_symlinks=False)
            except FileNotFoundError:continue
            raise SnapshotError('sqlite_sidecar_present')
    @contextmanager
    def database(self,name,owner=False):
        self.absent_sidecars(name)
        if owner:
            self.read(name+'.owner',4096)
            fcntl.flock(self.files[name+'.owner'][0],fcntl.LOCK_EX|fcntl.LOCK_NB)
        raw=self.read(name,MAX_DB);self.absent_sidecars(name)
        need(len(raw)>=100 and raw[:16]==b'SQLite format 3\0' and raw[18]in(1,2)and raw[19]in(1,2),'invalid_sqlite_image')
        # A CLOSED, sidecar-free WAL database retains WAL header flags. Only
        # this isolated parser image is changed to rollback flags so deserialize
        # cannot try an external WAL. The native bytes/hashes remain untouched.
        image=bytearray(raw);image[18]=image[19]=1
        db=sqlite3.connect(':memory:',isolation_level=None)
        try:
            need(hasattr(db,'deserialize'),'sqlite_image_reader_unavailable');db.deserialize(bytes(image))
            db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
            need(db.execute('PRAGMA query_only').fetchone()==(1,))
            yield db
            self.observe();self.absent_sidecars(name);self.check()
        finally:db.close()
    def evidence(self):
        return {name:dict(device=ident[0],inode=ident[1],uid=ident[2],mode=stat.S_IMODE(ident[3]),
                         bytes=len(raw),sha256=sha(raw))for name,(_,ident,raw)in self.files.items()}
    def close(self):
        valid=True
        for _,(fd,ident,_)in self.files.items():
            try:
                if _identity(os.fstat(fd))[:4]==ident[:4]:os.close(fd)
                else:valid=False
            except OSError:valid=False
        for _,fd,ident in reversed(self.dirs):
            try:
                if _identity(os.fstat(fd))[:4]==ident[:4]:os.close(fd)
                else:valid=False
            except OSError:valid=False
        return valid


def _peer(v):need(type(v)is dict and set(v)=={'chain_id','agent_id'});return PeerID(**v)
def _retained573_grant(p,scope,run,parent,observed_epoch_us):
    """Decode one original expired grant, never select a live RF waveform."""
    grants = {'unit-a': 'f1e8ca82787828131c8104a3784572c1e54aa80813ab704911b05c5201ad9d1a',
              'unit-c': '9a0dc6bff76e21a11b3343986cb5a6919c98819f275888dc679753ff3c69a1bc'}
    plans = {'unit-a': '9c5b224fbb232626eaf53764b9433e1b34d4a8170c6ddb2c0563528ddb26fa09',
             'unit-c': '3386e5456428f6f2222571f98c9d191dc627e17b1114604d18d71fd137d0a7a7'}
    bindings = {'unit-a': '305aec7636af4198dd95a60a490a4ba2df6343d2b30df3dedffaac004ae07ccc',
                'unit-c': '0f7fe6ee5effc940e2796c59f7ebe5e92a830ab00da28773e9d0ba5da6b2401b'}
    return (type(scope) is Scope and type(run) is RunScope and scope.unit in grants
        and run.run_id == '3ef93b13ec6966964c2f73d1dbd0a176' and run.transport == 'lora'
        and run.grant_sha256 == grants[scope.unit] and sha(canonical(p)) == plans[scope.unit]
        and run.grant_context_sha256 == 'bfca3db72cefc2c7c4129c29ecf1b2e34a7f639b27b66ee3bc2c187c570bcdf5'
        and run.attempt_binding_sha256 == bindings[scope.unit]
        and scope.runtime_sha256 == '2d72dbbe1cee7f3e6cb5480ab64977d3a98553feed1850d67c34a90ee38d7514'
        and scope.manifest_sha256 == '3476737fe14be9b1f34ff6ba938d1fd2db4a489c787a331e05b63d482fbab40e'
        and type(observed_epoch_us) is int and p['expires_epoch_us'] < observed_epoch_us
        and type(parent) is dict and parent.get('schema') == 'meshticulum.native-parent-claim.v1'
        and parent.get('parent_id') == LEGACY_SF5_8_PARENT
        and parent.get('policy_digest') == 'ffd58627e5fecbf342d6d0ff2473788c56a7ea38bf640103eb585fd7fe9ddcef'
        and parent.get('unit') == scope.unit and parent.get('run_id') == run.run_id
        and parent.get('host_boot_id') == run.host_boot_id and parent.get('prepared') is True
        and parent.get('native_plan_sha256') == plans[scope.unit])


def _public_grant(p,scope,run,*,historical_parent=None,observed_epoch_us=None):
    need(type(p)is dict and p['host_boot_id']==run.host_boot_id);boot(p['host_boot_id'])
    deadline=p.get('application_deadline_monotonic_us');integer(deadline,1)
    issued,expires=p['issued_epoch_us'],p['expires_epoch_us'];integer(issued,1);integer(expires,1)
    need(issued<expires and expires-issued<=240000000)
    if run.transport=='lora':
        keys={'schema','unit','host_boot_id','local_peer','initiator','responder','session_id','generation',
              'issued_epoch_us','expires_epoch_us','profile','object','sender_unit','country','antenna_confirmed',
              'max_frames','service','application_deadline_monotonic_us'}
        if p.get('radio_modem') in (GFSK_NAME,GFSK250_NAME):keys.add('radio_modem')
        need(set(p)==keys and p['schema']=='meshticulum.service-run.v1' and p['unit']==scope.unit)
        local,a,b=_peer(p['local_peer']),_peer(p['initiator']),_peer(p['responder'])
        session=SessionKey(a,b,bytes.fromhex(identifier(p['session_id'])),bytes.fromhex(identifier(p['generation'])))
        need(p['sender_unit']in('unit-a','unit-b','unit-c'));sends=p['sender_unit']==scope.unit
        need(local==(a if sends else b));direction=0 if sends else 1
        profile=profile_from_plan(p,board_id=1)
        admission = (LEGACY_SF5_8_ADMISSION if _retained573_grant(
            p,scope,run,historical_parent,observed_epoch_us) else admission_for(profile))
        admission.check(p['country'],p['antenna_confirmed'],profile)
        service=p['service'];base_service={'kind','format','application_expires_epoch_us'}
        need(type(service)is dict and set(service)in(base_service,base_service|{PUBLIC32_MARKER},base_service|{PUBLIC32_MARKER,REPRESENTATION_MARKER,TRANSPORT_DESCRIPTOR_MARKER})
             and service['kind']=='knowledge' and service['format']in FORMATS)
        public32=public32_service(service);mb1=public_mb1_service(service)
        obj=p['object'];need(type(obj)is dict and set(obj)=={'bytes','sha256','object_id','chunk_index'})
        integer(obj['bytes'],1,object_limit(service));hex_value(obj['sha256'],64);hex_value(obj['object_id'],64);integer(obj['chunk_index'],0,63)
        if p.get('radio_modem') in (GFSK_NAME,GFSK250_NAME):need(scope.unit in ('unit-a','unit-c') and p['sender_unit'] in ('unit-a','unit-c'),'gfsk_public_debt_scope_changed')
        if public32:
            sender='unit-a' if service['format']=='MR1.request' else 'unit-c'
            need(p['sender_unit']==sender and scope.unit in ('unit-a','unit-c')
                 and (service['format']!='MR1.response' or mb1 is not None or obj['bytes']==32768),'invalid_public32_debt_grant')
        app=integer(service['application_expires_epoch_us'],1);need(issued<app<=expires)
        frames=integer(p['max_frames'],1,512)
        route=radio_route(p)
        context=dict(protocol='lantern.meshticulum.bench.v1',initiator=asdict(a),responder=asdict(b),
            session_id=session.session_id.hex(),session_generation=session.session_generation.hex(),service='bench_transfer',
            route=sha(canonical(route)),issued_epoch_us=issued,expires_epoch_us=expires,max_plaintext=208,max_frames=frames)
        caps=Cost(profile.max_boot_attempts,profile.max_boot_estimated_airtime_us);fmt=service['format']
    else:
        keys={'schema','host_boot_id','local_peer','session','format','object','route_binding_digest','issued_epoch_us',
              'expires_epoch_us','application_expires_epoch_us','application_deadline_monotonic_us',
              'initiator_address','responder_address','limits'}
        if p.get('schema')==IP_V2:keys.add('startup_mode')
        need(set(p)==keys and p['schema'] in IP_SCHEMAS and p['format']in FORMATS)
        s=p['session'];need(type(s)is dict and set(s)=={'initiator','responder','session_id','generation'})
        a,b=_peer(s['initiator']),_peer(s['responder']);local=_peer(p['local_peer'])
        session=SessionKey(a,b,bytes.fromhex(identifier(s['session_id'])),bytes.fromhex(identifier(s['generation'])))
        need(local in(a,b));direction=0 if local==a else 1
        need(type(p['limits'])is dict and set(p['limits'])==set(IpLimits.__dataclass_fields__))
        limits=IpLimits(**p['limits']);frames=limits.max_frames
        obj=p['object'];need(type(obj)is dict and set(obj)=={'bytes','sha256'})
        integer(obj['bytes'],1,limits.max_object_bytes);hex_value(obj['sha256'],64);hex_value(p['route_binding_digest'],64)
        app=integer(p['application_expires_epoch_us'],1);need(issued<app<=expires)
        ia,ra=address(tuple(p['initiator_address'])),address(tuple(p['responder_address']));need(ia!=ra)
        context=dict(protocol='meshticulum.ip.v1',transport='tcp',initiator=asdict(a),responder=asdict(b),
            session_id=session.session_id.hex(),session_generation=session.session_generation.hex(),service_kind='knowledge',
            format=p['format'],object_bytes=obj['bytes'],object_sha256=obj['sha256'],route_binding_digest=p['route_binding_digest'],
            issued_epoch_us=issued,expires_epoch_us=expires,application_expires_epoch_us=app,
            initiator_address=ia,responder_address=ra,limits=asdict(limits))
        if p['schema']==IP_V2:context=json.loads(ip_metadata_grant(p).context())
        caps=Cost(ip_frames=frames,tx_stream_bytes=limits.max_tx_stream_bytes,rx_stream_bytes=limits.max_rx_stream_bytes);fmt=p['format']
    need(session.session_id.hex()==run.run_id)
    context_hash=sha(canonical(context))
    if run.grant_context_sha256 is not None:need(run.grant_context_sha256==context_hash,'grant_context_changed')
    output=dict(context_sha256=context_hash,caps=asdict(caps),direction=direction,format=fmt,
                application_expires_epoch_us=app,application_deadline_monotonic_us=deadline,max_frames=frames)
    if run.transport=='tcp' and p['schema']==IP_V2:output['startup_mode']=p['startup_mode']
    if run.transport=='lora' and public32:
        # The512-frame cipher budget includes control/retry counters. Core's
        # inner service Shape separately caps marked responses at256 frames.
        output.update(public_response_max_bytes=32768,flow_max_frames=256 if fmt=='MR1.response' else 128)
    if run.transport=='lora' and mb1 is not None:
        output.update(public_resource_representation='MB1',public_transport_descriptor_sha256=mb1)
    return output


def _table(db,name,columns):
    need(db.execute('SELECT type FROM sqlite_master WHERE name=?',(name,)).fetchone()==('table',),'invalid_journal_schema')
    text={'boot','state','lane','evidence'};blobs={'binding','packet_sha','digest','bitmap'}
    expected=tuple((c,'TEXT'if c in text else'BLOB'if c in blobs else'INTEGER',int(i==0))for i,c in enumerate(columns))
    actual=tuple((r[1],r[2].upper(),r[5])for r in db.execute('PRAGMA table_info('+name+')'))
    need(actual==expected,'invalid_journal_schema')
def _rows(db,sql):
    rows=db.execute(sql).fetchmany(MAX_ROWS+1);need(len(rows)<=MAX_ROWS,'journal_too_large');return rows


def _journal(reader,run,grant):
    need(run.attempt_binding_sha256 is not None,'attempt_binding_unavailable')
    radio=run.transport=='lora';name='attempts.sqlite'if radio else'bytes.sqlite'
    with reader.database(name,owner=True)as db:
        if radio:
            _table(db,'owner',('id','binding','max_attempts','max_airtime','request','fenced'))
            _table(db,'attempts',('request','packet_sha','bytes','estimated_us','state','evidence'))
            owner=db.execute('SELECT hex(binding),max_attempts,max_airtime,request,fenced FROM owner WHERE id=1').fetchone()
            need(db.execute('SELECT COUNT(*) FROM owner').fetchone()==(1,)and owner is not None)
            need(owner[0].lower()==run.attempt_binding_sha256 and owner[1:3]==
                 (grant['caps']['radio_attempts'],grant['caps']['estimated_airtime_us']),'radio_binding_or_caps_changed')
            integer(owner[1],1);integer(owner[2],1);integer(owner[3]);integer(owner[4],0,1)
            rows=_rows(db,'SELECT request,hex(packet_sha),bytes,estimated_us,state,evidence FROM attempts ORDER BY request')
            result=[]
            for request,packet,size,estimate,state,evidence in rows:
                integer(request,1,owner[3]);hex_value(packet.lower(),64);integer(size,1,255);integer(estimate,1,owner[2])
                need(state in('RESERVED','RF_DONE','REFUSED','NOT_SENT','UNKNOWN')and(evidence is None or type(evidence)is str))
                result.append(dict(request=request,packet_sha256=packet.lower(),bytes=size,estimated_us=estimate,
                                   state=state,evidence_sha256=None if evidence is None else sha(evidence.encode())))
            cost=Cost(radio_attempts=len(rows),estimated_airtime_us=sum(r[3]for r in rows));fenced=owner[4]
            owner_view=dict(binding=owner[0].lower(),max_attempts=owner[1],max_airtime_us=owner[2],request=owner[3],fenced=bool(fenced))
        else:
            _table(db,'owner',('id','binding','rx','rx_pending','rx_unknown','fenced'))
            _table(db,'attempts',('id','digest','bytes','lane','written','state'))
            owner=db.execute('SELECT hex(binding),rx,rx_pending,rx_unknown,fenced FROM owner WHERE id=1').fetchone()
            need(db.execute('SELECT COUNT(*) FROM owner').fetchone()==(1,)and owner is not None)
            need(owner[0].lower()==run.attempt_binding_sha256==grant['context_sha256'],'ip_binding_changed')
            for v in owner[1:4]:integer(v)
            integer(owner[4],0,1);rows=_rows(db,'SELECT id,hex(digest),bytes,lane,written,state FROM attempts ORDER BY id');result=[]
            for token,packet,size,lane,written,state in rows:
                integer(token,1);hex_value(packet.lower(),64);integer(size,1,4100);integer(written,0,size)
                need((lane in('DATA','ACK')or(lane=='READY'and grant.get('startup_mode')==READY_MODE
                      and grant['direction']==0 and size==83))
                     and state in('RESERVED','KERNEL_ACCEPTED','NOT_SENT','UNKNOWN'))
                need(state!='KERNEL_ACCEPTED'or written==size);need(state!='NOT_SENT'or written==0)
                result.append(dict(attempt_id=token,packet_sha256=packet.lower(),reserved_bytes=size,lane=lane,written_bytes=written,state=state))
            cost=Cost(ip_frames=len(rows),tx_stream_bytes=sum(r[2]for r in rows),rx_stream_bytes=sum(owner[1:4]));fenced=owner[4]
            owner_view=dict(binding=owner[0].lower(),rx_settled=owner[1],rx_pending=owner[2],rx_unknown=owner[3],fenced=bool(fenced))
        need(cost.within(Cost(**grant['caps'])),'journal_caps_exceeded')
        return dict(owner=owner_view,attempts=result,rows_sha256=sha(canonical(result)),actual_cost=asdict(cost),
                    has_unknown_or_pending=bool(fenced or any(r['state']in('RESERVED','UNKNOWN')for r in result)
                        or(not radio and(owner[2]or owner[3]))))


def _wire(reader,run,grant,result):
    with reader.database('wire.sqlite')as db:
        fields=('only_row','binding','direction','boot')+ (('original_deadline',)if run.transport=='tcp'else())+('deadline','epoch','mono','tx','high','bitmap','retired')
        _table(db,'state',fields);need(db.execute('SELECT COUNT(*) FROM state').fetchone()==(1,))
        row=db.execute('SELECT * FROM state WHERE only_row=1').fetchone();need(row is not None)
        value=dict(zip(fields,row));need(type(value['binding'])is bytes and value['binding'].hex()==grant['context_sha256']
            and value['direction']==grant['direction']and value['boot']==run.host_boot_id)
        integer(value['only_row'],1,1);integer(value['direction'],0,1);integer(value['retired'],0,1)
        for k in('deadline','epoch','mono'):integer(value[k],1)
        for k in('tx','high'):integer(value[k],0,grant['max_frames'])
        need(type(value['bitmap'])is bytes and len(value['bitmap'])==32 and value['retired']==1,'wire_not_retired')
        if run.transport=='tcp':
            integer(value['original_deadline'],1)
            need(value['deadline']<=value['original_deadline']<=grant['application_deadline_monotonic_us']
                 and value['original_deadline']==result['original_application_deadline_us'],'ip_original_deadline_changed')
        value['binding']=value['binding'].hex();value['bitmap']=value['bitmap'].hex();return value


def _parent_reference(parent,scope,run,grant,p):
    if parent is None:return None
    keys={'schema','parent_id','policy_digest','controller_allocation_id','native_claim_id','unit','host_boot_id','run_id',
        'resource_sha256','application_expires_epoch_us','application_deadline_monotonic_us','child_caps',
        'original_plan_sha256','native_plan_sha256','secret_sha256','grant_context_sha256','prepared'}
    need(type(parent)is dict and set(parent)==keys and parent['schema']=='meshticulum.native-parent-claim.v1'
         and parent['prepared']is True and parent['unit']==scope.unit and parent['host_boot_id']==run.host_boot_id
         and parent['run_id']==run.run_id and parent['grant_context_sha256']==grant['context_sha256']
         and parent['application_expires_epoch_us']==grant['application_expires_epoch_us']
         and parent['application_deadline_monotonic_us']==grant['application_deadline_monotonic_us'],'invalid_parent_reference')
    identifier(parent['parent_id'])
    for key in('policy_digest','controller_allocation_id','native_claim_id','resource_sha256','original_plan_sha256',
               'native_plan_sha256','secret_sha256','grant_context_sha256'):hex_value(parent[key],64)
    need(parent['native_plan_sha256']==sha(canonical(p)),'parent_plan_changed')
    child=parent['child_caps'];need(type(child)is dict and set(child)=={'metadata','cost','digest'}
                                  and type(child['cost'])is dict and set(child['cost'])==set(asdict(Cost())))
    cost=Cost(**child['cost']);need(asdict(cost)==grant['caps'],'parent_caps_changed');hex_value(child['digest'],64)
    # Never return child.metadata or any arbitrary nested source dictionary.
    return {key:parent[key]for key in('schema','parent_id','policy_digest','controller_allocation_id','native_claim_id',
        'unit','host_boot_id','run_id','resource_sha256','original_plan_sha256','native_plan_sha256','grant_context_sha256')}|dict(
        full_caps=asdict(cost),child_descriptor_sha256=child['digest'],verified_against_parent_ledger=False)


def _one(state,scope,run,observe,retained):
    output=dict(run_id=run.run_id,host_boot_id=run.host_boot_id,transport=run.transport,status='unavailable',actual_cost=None,
        admitted_caps=None,conservative_unknown_caps=None,parent_reference=None,parent_prepayment_verified=False,
        live_terminal_ownership_observed=False,physical_quiescence_observed=False)
    reader=None
    try:
        reader=_Reader(state,run.run_id,observe)
        retained.append((output,reader))
        raw=reader.read('grant.json',16384);need(sha(raw)==run.grant_sha256,'grant_file_changed')
        p=public_json(raw)
        # Hold and check the original retired provision before selecting the
        # narrowly pinned historical decoder. It is validated against the grant
        # below and against the genuine native parent ledger by its caller.
        need(reader.read('active',16)==b'retired\n','run_not_retired')
        provision=public_json(reader.read('provision.json',32768))
        observed=observe()
        grant=_public_grant(p,scope,run,historical_parent=provision.get('parent'),
                            observed_epoch_us=observed[0])
        output['grant']=grant;output['admitted_caps']=grant['caps']
        output['conservative_unknown_caps']=grant['caps']
        need(run.grant_context_sha256 is not None,'grant_context_unavailable')
        job=public_json(reader.read('job.json',4096));result=public_json(reader.read('result.json',300000))
        need(provision['schema']in('meshticulum.provisioned-public-pilot.v1','meshticulum.provisioned-public-pilot.v2')
            and provision['unit']==scope.unit and provision['run_id']==run.run_id and provision['transport']==run.transport)
        need(provision['original_application_expires_epoch_us']==grant['application_expires_epoch_us']
             and provision['original_application_deadline_us']==grant['application_deadline_monotonic_us'])
        need(set(job)=={'schema','unit','host_boot_id','run_id','grant_context_sha256','transport','pid','start_ticks','command_sha256'}
            and job['schema']=='meshticulum.native-public-job.v1'and job['unit']==scope.unit and job['host_boot_id']==run.host_boot_id
            and job['run_id']==run.run_id and job['transport']==run.transport and job['grant_context_sha256']==grant['context_sha256'])
        integer(job['pid'],2);integer(job['start_ticks'],1);hex_value(job['command_sha256'],64)
        need(result['schema']==('meshticulum.physical-service-run.v1'if run.transport=='lora'else'meshticulum.ip-public-node-run.v1')
            and result['unit']==scope.unit and result['host_boot_id']==run.host_boot_id and result['session_id']==run.run_id
            and result['grant_context_sha256']==grant['context_sha256']and result['service_format']==grant['format']
            and result['original_application_expires_epoch_us']==grant['application_expires_epoch_us'])
        integer(result['original_application_deadline_us'],1,grant['application_deadline_monotonic_us'])
        output['terminal_metadata']=job # Historical metadata, not a process observation.
        output['parent_reference']=_parent_reference(provision.get('parent'),scope,run,grant,p)
        journal=_journal(reader,run,grant);wire=_wire(reader,run,grant,result)
        output.update(status='scoped_rows_observed',actual_cost=journal['actual_cost'],journal=journal,wire=wire)
        observe();reader.check();output['files']=reader.evidence()
        return output
    except Exception as error:
        output['reason']=error.reason if isinstance(error,SnapshotError)else('missing_public_input'if isinstance(error,FileNotFoundError)else'public_input_unavailable')
        output['actual_cost']=None
        if reader is not None:output['files']=reader.evidence()
        return output


def collect(state,scope,*,clock,boot_reader):
    """Collect explicit scoped rows, with no complete-debt/enrollment claim.

    Binding fields are hex of the existing 32-byte digest itself, never a second
    SHA256(binding). grant_sha256 alone hashes the raw grant.json file bytes.
    """
    observation=_Observation(scope,clock,boot_reader);first=observation();state=Path(state).absolute()
    retained=[]
    try:
        results=[_one(state,scope,run,observation,retained)for run in scope.runs]
        last=observation()
        # All readers/locks survive the FINAL caller callback, so it cannot
        # mutate an earlier run and leave that run's evidence marked complete.
        for result,reader in retained:
            try:
                reader.check()
                for name in reader.files:
                    if name.endswith('.sqlite'):reader.absent_sidecars(name)
            except Exception:
                result['actual_cost']=None;result['status']='unavailable';result['reason']='final_input_changed'
    finally:
        for result,reader in retained:
            if not reader.close():
                result['actual_cost']=None;result['status']='unavailable';result['reason']='reader_cleanup_unavailable'
    known=Cost()
    for result in results:
        if result['actual_cost']is not None:known=known.plus(Cost(**result['actual_cost']))
    return dict(schema='meshticulum.public-debt-snapshot.v1',unit=scope.unit,current_host_boot_id=scope.current_host_boot_id,
        scope_canonical_sha256=sha(canonical(asdict(scope))),declared_runtime_sha256=scope.runtime_sha256,
        runtime_archive_observed=False,declared_manifest_sha256=scope.manifest_sha256,
        sqlite_parser=dict(source='bounded held-file bytes',native_database_opened_by_sqlite=False,
            mode='isolated query-only memory image',wal_header_flags_normalized_only_in_memory=True,
            native_bytes_modified=False,sidecars='WAL/shm/rollback journal refused',
            consistency='per-file identity/byte checks and existing locks; no cross-file atomic or physical proof'),
        original_observation_epoch_us=first[0],original_observation_monotonic_us=first[1],
        observation_expires_epoch_us=scope.observation_expires_epoch_us,observation_deadline_monotonic_us=observation.deadline,
        final_observation_epoch_us=last[0],final_observation_monotonic_us=last[1],runs=results,
        known_scoped_actual_cost=asdict(known)if any(r['actual_cost']is not None for r in results)else None,
        scoped_rows_complete=all(r['actual_cost']is not None for r in results),
        complete_global_debt=False,eligible_for_enrollment=False,parent_accounting_included=False,
        parent_prepayment_overlap_resolved=False,MCU_observations=None,current_terminal_ownership=None,
        database_writes=False,device_opens=False,omitted=['authoritative controller/native parent/prior lineage',
            'complete public benchmark history beyond explicit allowlist','current terminal/physical/MCU facts','independent owner approval'])
