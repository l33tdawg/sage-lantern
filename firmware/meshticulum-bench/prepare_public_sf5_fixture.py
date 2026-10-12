"""Build fresh signed CC0 public data locally; no dispatch or ingestion."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

from source_core import core_source
CORE = core_source()
sys.path.insert(0,str(CORE/'src'))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from meshticulum.resources import knowledge_pack as kp, lamplighter as ll, resource

def sha(raw): return hashlib.sha256(raw).hexdigest()
def write(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())

def build(output):
    os.umask(0o077); output=Path(output).absolute()
    nonce=secrets.token_hex(16); created=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    notice='CC0-1.0 invented public throughput data. No private, personal, model or evaluation content. '
    chunks=[]
    for purpose,size in [('bulk',8164)]:
        fields=dict(title='Public '+purpose+' sample',licence='CC0-1.0',source='synthetic-public-throughput',
                    date=created[:10],tags=['public','synthetic','throughput'])
        identity=purpose+'-'+nonce; text=notice+nonce+' '
        if purpose=='bulk':
            fields['title']+=' '+notice+nonce+' '+('Public byte sample '*8)
            text='Public CC0 byte sequence. '+nonce+' '
        missing=size-len(kp.chunk_bytes(kp.build_chunk(identity,text,**fields)))
        assert missing>0
        text+=('📡'*(missing//4)+'x'*(missing%4)) if purpose=='bulk' else ('Invented public sample. '*1000)[:missing]
        record=kp.build_chunk(identity,text,**fields); assert len(kp.chunk_bytes(record))==size
        chunks.append((purpose,record))
    manifest=kp.build_manifest(pack_id='public-sf5-bulk-'+nonce,version=1,pack_class='reference',
        title='Synthetic public SF5 throughput sample',licence='CC0-1.0',
        provenance={'source':'synthetic-public-throughput'},created_at=created,
        chunks=[record for _,record in chunks],embedding_model='unused-public-fixture',embedding_dimension=1)
    raw=kp.manifest_bytes(manifest); digest=sha(raw)
    key=Ed25519PrivateKey.generate(); public=kp.public_key_hex(key)
    signature=kp.canonical_json(kp.sign_manifest(raw,key))+b'\n'; del key
    trust=dict(schema='sage.knowledge-pack.trust-root.v1',note=notice,keys=[dict(key_id=kp.key_id_for(public),
        algorithm='ed25519',public_key_hex=public,status='active',created_at=created,note='RAM-only synthetic publisher')])
    trust_raw=(json.dumps(trust,sort_keys=True,indent=2)+'\n').encode()
    output.mkdir(mode=0o700,exist_ok=False); pack=output/'pack'; pack.mkdir(mode=0o700)
    (pack/'chunks').mkdir(mode=0o700); (output/'mr1').mkdir(mode=0o700)
    write(pack/'manifest.json',raw); write(pack/'manifest.sig.json',signature); write(output/'trust.json',trust_raw)
    for _,record in chunks: write(pack/'chunks'/(record['sha256']+'.json'),kp.chunk_bytes(record))
    cache=ll.VerifiedCache(pack,trust,digest); rows=[]
    for purpose,record in chunks:
        piece=cache.piece(record['sha256'])
        request=resource.encode_request(cache.base+'chunks/'+record['sha256']+'.json',0,8164)
        reply=resource.BlockServer(cache.read).handle(request)
        magic,binding,status,total,offset=resource.RESPONSE.unpack(reply[:resource.RESPONSE.size])
        assert status==0 and total==len(piece) and offset==0 and reply[resource.RESPONSE.size:]==piece
        assert magic==resource.MAGIC and binding==hashlib.sha256(request).digest()[:16] and len(reply)<=8192
        req_path=output/'mr1'/(purpose+'.request.bin'); reply_path=output/'mr1'/(purpose+'.response.bin')
        write(req_path,request); write(reply_path,reply)
        rows.append(dict(purpose=purpose,sha256=record['sha256'],content_address_sha256=record['sha256'],
            chunk_bytes=len(piece),stored_chunk_sha256=sha(piece),request_bytes=len(request),response_bytes=len(reply),
            request_sha256=sha(request),response_sha256=sha(reply),request_path=str(req_path),response_path=str(reply_path)))
    result=dict(schema='meshticulum.synthetic-public-fixture.v1',pack=str(pack),trust=str(output/'trust.json'),
        manifest_sha256=digest,manifest_bytes=len(raw),signature_sha256=sha(signature),trust_sha256=sha(trust_raw),
        chunks=rows,public_only=True,licence='CC0-1.0',private_data=False,model_or_evaluation_content=False,
        publisher_private_key_persisted_or_exported=False,native_admission=False,rf_activation=False,
        dispatch_permission=False,ingestion=False)
    write(output/'FIXTURE.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode())
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(build(parser.parse_args().output),sort_keys=True))
