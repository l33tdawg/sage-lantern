"""Generated provider canary against two isolated real REST/vault fixtures.

Only the Go fixture supplies loopback endpoints, temporary signing seeds and
temporary SQLite paths on stdin. Lifecycle, accounting, clocks and RF remain
models. Normal Noise generation is unchanged; no Noise key is exported or
copied for scanning, and its disk persistence is therefore unproved here.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from types import SimpleNamespace
import uuid

CONFIG_SCHEMA='lantern.meshticulum.native-provider-backend-input.v1'
RESULT_SCHEMA='lantern.meshticulum.native-provider-backend-canary.v1'
MAX_INPUT=16384
MAX_DISK_BYTES=32*1024*1024
CANARY_PREFIX=b'MESHTICULUM-GENERATED-CANARY-'


class RequestDiagnostics:
    """Non-authority aggregate counters; no headers, bodies, IDs or responses."""
    def __init__(self):
        self.phase='backend_setup';self.endpoint=-1;self.stage=-1
        self.counts={};self.last_failure=None;self.boundaries=[]
    def boundary(self,name,index=-1):
        self.phase=name;self.endpoint=index
        self.boundaries.append(dict(phase=name,endpoint=index,request_count=sum(self.counts.values())))
    def response(self,response):
        path=response.request.url.path
        route=path if path in ('/v1/messages/storage','/v1/workflows') else '/v1/workflows/{record}'
        key=(self.phase,self.endpoint,self.stage,response.request.method,route,response.status_code)
        self.counts[key]=self.counts.get(key,0)+1
        if response.status_code>=500:
            # Classify only exact static server titles; never retain a body.
            response.read()
            try:title=response.json().get('title')
            except Exception:title=None
            allowed=('Replay window saturated','Message storage unavailable','Access control unavailable')
            self.last_failure=dict(status=response.status_code,route=route,
                gate=title if title in allowed else 'unclassified_server_refusal',phase=self.phase,
                endpoint=self.endpoint,stage=self.stage)
    def snapshot(self):
        return dict(request_count=sum(self.counts.values()),rows=[dict(phase=k[0],endpoint=k[1],stage=k[2],
            method=k[3],route=k[4],status=k[5],count=v) for k,v in sorted(self.counts.items())],
            boundaries=list(self.boundaries),last_failure=self.last_failure)


def need(value):
    if not value:raise RuntimeError('native_backend_fixture_invalid')


def configuration(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_INPUT)
    def pairs(items):
        result={}
        for key,value in items:
            need(key not in result);result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs)
    need(type(value) is dict and set(value)=={'schema','first_url','second_url',
        'first_seed','second_seed','databases','child_timeout_seconds'} and value['schema']==CONFIG_SCHEMA)
    for field in ('first_url','second_url'):
        url=value[field]
        need(type(url) is str and re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}',url) is not None
            and 1<=int(url.rsplit(':',1)[1])<=65535)
    need(value['first_url']!=value['second_url'])
    for field in ('first_seed','second_seed'):
        need(type(value[field]) is str and re.fullmatch(r'[0-9a-f]{64}',value[field]) is not None)
    need(value['first_seed']!=value['second_seed'])
    need(type(value['child_timeout_seconds']) is int and 15<=value['child_timeout_seconds']<=1200)
    need(type(value['databases']) is list and len(value['databases'])==2)
    paths=[]
    for name in value['databases']:
        need(type(name) is str and Path(name).is_absolute() and Path(name).name=='workflows.db')
        path=Path(name);need(path.resolve(strict=True)==path)
        observed=path.lstat()
        need(stat.S_ISREG(observed.st_mode) and observed.st_uid==os.getuid() and observed.st_nlink==1)
        paths.append(path)
    need(paths[0]!=paths[1]);return value


def source_fixture_imports():
    """Explicitly add source fixtures; do not execute pytest's global conftest."""
    project=Path(__file__).resolve().parents[2]
    core=Path(os.environ['MESHTICULUM_SOURCE']).resolve(strict=True)
    runtime=Path(os.environ['LANTERN_NATIVE_RUNTIME_SOURCE']).resolve(strict=True)
    need((core/'tests/test_meshticulum_private_carrier.py').is_file()
        and (runtime/'sage_lantern/mesh_daemon.py').is_file())
    sys.path[:0]=[str(project),str(project/'src'),str(core/'src'),str(runtime)]
    import tests
    fixtures=str(core/'tests')
    if fixtures not in tests.__path__:tests.__path__.append(fixtures)
    from tests.integrations.sage import native_canary_helpers as helper
    return helper


def real_backend_factory(config,diagnostics):
    from sage_sdk.auth import AgentIdentity
    from sage_sdk.client import SageClient
    import httpx
    urls={config['first_seed']:config['first_url'],config['second_seed']:config['second_url']}
    def backend(key):
        endpoint=urls[key.encode().hex()]
        client=SageClient(endpoint,AgentIdentity(key),timeout=2,trust_env=False)
        client._client.event_hooks['response'].append(diagnostics.response)
        try:
            # The new path uses ordinary socket HTTP, never MockTransport.
            need(type(client._client._transport) is httpx.HTTPTransport)
            value=client.message_storage_status().model_dump(mode='json')
            need(value['agent_id']==client._identity.agent_id
                and type(value['instance_id']) is str and re.fullmatch(r'[0-9a-f]{64}',value['instance_id']) is not None
                and type(value['vault_generation']) is str
                and re.fullmatch(r'[1-9][0-9]{0,19}',value['vault_generation']) is not None
                and 0<int(value['vault_generation'])<2**64
                and value['encryption_expected'] is True and value['vault_active'] is True
                and value['stable'] is True)
            # Only lifecycle fields are modeled. The helper's probe freshly
            # reads the actual service instance/vault epoch on every guard.
            state=SimpleNamespace(live=True,instance_id=value['instance_id'],
                expected_storage_epoch=value['instance_id']+':'+value['vault_generation'])
            return state,client,endpoint
        except BaseException:
            client._client.close();raise
    return backend


def stamp(value):
    return tuple(getattr(value,name) for name in ('st_dev','st_ino','st_mode','st_uid','st_gid',
        'st_nlink','st_size','st_mtime_ns','st_ctime_ns'))


def scan_disk(databases,seeds,canary):
    """Read only exact temporary main+populated-WAL files; emit no scan values."""
    secrets=[canary,CANARY_PREFIX]
    for seed in seeds:secrets.extend((seed,seed.hex().encode(),base64.b64encode(seed)))
    count=0
    for name in databases:
        for suffix in ('','-wal'):
            path=Path(name+suffix);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            try:
                before=os.fstat(fd)
                need(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and before.st_nlink==1
                    and 0<before.st_size<=MAX_DISK_BYTES and stamp(path.lstat())==stamp(before)
                    and (suffix!='-wal' or before.st_size>32))
                body=os.pread(fd,before.st_size+1,0)
                need(len(body)==before.st_size and stamp(os.fstat(fd))==stamp(before)
                    and stamp(path.lstat())==stamp(before))
                need(all(secret not in body for secret in secrets));count+=1
            finally:os.close(fd)
    return count


def run(config,diagnostics):
    helper=source_fixture_imports()
    from nacl.signing import SigningKey
    from meshticulum.privacy.mesh_envelope import open_envelope
    from lantern_meshticulum.sage import forward_native_provider as provider
    from lantern_meshticulum.sage.workflow_store import WorkflowNotFound
    seeds=[bytes.fromhex(config[name]) for name in ('first_seed','second_seed')]
    keys=[SigningKey(seed) for seed in seeds]
    checks={}
    with tempfile.TemporaryDirectory(prefix='meshticulum-native-backend-carrier-') as directory:
        with helper.generated_canary_pair(provider,backend_factory=real_backend_factory(config,diagnostics),
                keys=keys,phase_observer=diagnostics.boundary) as pair:
            original=(pair.context.expires_epoch_us,pair.intention.application_deadline_monotonic_us)
            # A plaintext fixture probe exercises vault encryption itself;
            # encrypted MPF2 delivery alone cannot demonstrate encryption at rest.
            sender=pair.owners[0];store=sender._composition.store;identifier=str(uuid.uuid4())
            payload=dict(fixture_only=True,generated_canary=pair.content)
            store.put(identifier,'conversation_control',0,payload,access_check=sender.access_check)
            need(store.get(identifier,kind='conversation_control',access_check=sender.access_check)['payload']==payload)
            checks['real_vault_plaintext_probe_roundtrip']=True
            attempts=[]
            for index in range(5):
                diagnostics.stage=index;diagnostics.boundary('modeled_stage')
                row=helper.modeled_stage(Path(directory),pair,index,replay=index==4)
                need(row['modeled_transport'] is True and row['physical_qualified'] is False
                    and all(type(v) is int and v>0 for v in row['attempt_counts']))
                if index==4:need(row['replay_refusals']>0)
                attempts.extend(row['attempt_counts'])
            checks['five_prepaid_modeled_stages_and_replay_refusal']=True
            diagnostics.boundary('recipient_settlement');decision=pair.operations[1].settle();need(decision.status=='admitted')
            receiver=pair.owners[1];incoming=receiver._composition.store
            record=incoming.get(decision.record_id,kind='mesh_inbound',access_check=receiver.access_check)
            opened=open_envelope(record['payload']['wire'],keys[1],expected_sender=pair.peers[0],
                expected_recipient=pair.peers[1],now=pair.clock()[0]//1000000)
            need(opened.content==pair.content);checks['real_rest_recipient_admission_and_decryption']=True
            try:store.get(decision.record_id,kind='mesh_inbound',access_check=sender.access_check)
            except WorkflowNotFound:pass
            else:raise RuntimeError('native_backend_fixture_actor_isolation_failed')
            checks['real_rest_actor_isolation']=True
            need((pair.context.expires_epoch_us,pair.intention.application_deadline_monotonic_us)==original)
            need(all(source.actual_usage is None for source in pair.sources))
            need(all(p.retire() is True for p in pair.providers))
            need(all(source.closed for source in pair.sources) and all(op.session._closed for op in pair.operations))
            checks['original_modeled_clocks_unknown_usage_and_cleanup_preserved']=True
            scan_count=scan_disk(config['databases'],seeds,pair.content.encode())
            checks['temporary_main_and_populated_wal_scans']=scan_count==4
            return dict(schema=RESULT_SCHEMA,complete=True,checks=checks,scan_file_count=scan_count,
                modeled_lifecycle=True,modeled_accounting=True,modeled_clock=True,modeled_transport=True,
                real_rest_backend=True,real_temporary_vault=True,native_accounting_qualified=False,
                native_owner_consent_qualified=False,private_transport_qualified=False,
                private_production_enabled=False,radio_opened=False,memory_erasure_qualified=False,
                noise_key_encodings_scanned=False,noise_key_disk_persistence_qualified=False,
                source_attempt_count=sum(attempts),canary_sha256=hashlib.sha256(pair.content.encode()).hexdigest(),
                request_diagnostics=diagnostics.snapshot())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-input',action='store_true')
    args=parser.parse_args()
    diagnostics=RequestDiagnostics()
    try:
        config=configuration(sys.stdin.buffer.read(MAX_INPUT+1))
        if args.check_input:
            value=dict(schema=CONFIG_SCHEMA,input_valid=True,execution_started=False)
        else:value=run(config,diagnostics)
        print(json.dumps(value,sort_keys=True,separators=(',',':')))
    except BaseException as error:
        # Never print SDK exceptions, stdin, generated canary or private keys.
        frames=[];trace=error.__traceback__
        while trace is not None:
            frames.append(dict(file=Path(trace.tb_frame.f_code.co_filename).name,line=trace.tb_lineno));trace=trace.tb_next
        print(json.dumps(dict(schema=RESULT_SCHEMA,complete=False,error_type=type(error).__name__,
            frames=frames[-8:],request_diagnostics=diagnostics.snapshot(),
            private_transport_qualified=False,radio_opened=False),sort_keys=True))
        raise SystemExit(1) from None


if __name__=='__main__':main()
