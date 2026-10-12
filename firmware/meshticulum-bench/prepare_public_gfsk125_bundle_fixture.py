"""Fresh signed100000-byte CC0 file fixture; no dispatch or radio authority."""
import argparse
from datetime import datetime,timezone
import hashlib,json,os,secrets,sys
from pathlib import Path
from source_core import core_source
sys.path.insert(0,str(core_source()/'src'))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from meshticulum.resources import byte_bundle as bundle,knowledge_pack as kp,lamplighter as ll,resource
from prepare_public_sf5_fixture import write


def build(output):
    os.umask(0o077);output=Path(output).absolute();nonce=secrets.token_hex(16)
    created=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload=secrets.token_bytes(100000);key=Ed25519PrivateKey.generate();public=kp.public_key_hex(key)
    manifest,chunks,signature=bundle.build(payload,pack_id='public-gfsk125-file-'+nonce,
        created_at=created,private_key=key,exact32=True);del key
    mraw=kp.manifest_bytes(manifest);sraw=kp.canonical_json(signature)+b'\n';digest=hashlib.sha256(mraw).hexdigest()
    trust=dict(schema='sage.knowledge-pack.trust-root.v1',keys=[dict(key_id=kp.key_id_for(public),algorithm='ed25519',
        public_key_hex=public,status='active',created_at=created,note='RAM-only CC0 synthetic file publisher')])
    traw=(json.dumps(trust,sort_keys=True,indent=2)+'\n').encode()
    output.mkdir(mode=0o700,exist_ok=False);pack=output/'pack';pack.mkdir(mode=0o700);(pack/'chunks').mkdir(mode=0o700)
    write(pack/'manifest.json',mraw);write(pack/'manifest.sig.json',sraw);write(output/'trust.json',traw)
    for name,record in chunks.items():write(pack/'chunks'/(name+'.json'),kp.chunk_bytes(record))
    cache=ll.VerifiedCache(pack,trust,digest);expected=bundle.descriptor(cache.value);rows=[]
    for part in expected['parts']:
        request=resource.encode_request(cache.base+'chunks/'+part['chunk_sha256']+'.json',0,32740,public32=True)
        response=resource.BlockServer(cache.read,public32=True).handle(request)
        assert len(response)==32768
        rows.append(dict(index=part['index'],decoded_bytes=part['raw_bytes'],chunk_sha256=part['chunk_sha256'],
            request_bytes=len(request),response_bytes=len(response),response_sha256=hashlib.sha256(response).hexdigest()))
    result=dict(schema='meshticulum.synthetic-public100k-gfsk125-fixture.v1',public_only=True,private_data=False,
        model_or_evaluation_content=False,licence='CC0-1.0',native_admission=False,rf_activation=False,
        publisher_private_key_persisted_or_exported=False,manifest_sha256=digest,
        signature_sha256=hashlib.sha256(sraw).hexdigest(),trust_sha256=hashlib.sha256(traw).hexdigest(),
        whole_bytes=100000,whole_sha256=hashlib.sha256(payload).hexdigest(),parts=rows)
    write(output/'FIXTURE.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode())
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    result=build(parser.parse_args().output);print(json.dumps(dict(fixture=str(result['manifest_sha256']),decoded_bytes=100000,parts=5,radio_opened=False)))
