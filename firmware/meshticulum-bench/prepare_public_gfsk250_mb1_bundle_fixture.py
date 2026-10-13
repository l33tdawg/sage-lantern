"""Fresh signed100000-byte CC0 file fixture; no dispatch or radio authority."""
import argparse
from datetime import datetime,timezone
import hashlib,json,os,secrets,sys
from pathlib import Path
from source_core import core_source
from run_public_sf5_c_bulk_8k import read,sha,need
import tempfile,shutil
sys.path.insert(0,str(core_source()/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'software/meshticulum-integration/src'))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from meshticulum.resources import byte_bundle as bundle,knowledge_pack as kp,lamplighter as ll,resource
from lantern_meshticulum.bench.pilot_bundle_transport import MB1Transport,prepare_descriptor,KIND as TRANSPORT_KIND
from prepare_public_sf5_fixture import write


def build(output,*,baseline_fixture=None,baseline_fixture_sha256=None):
    os.umask(0o077);output=Path(output).absolute();nonce=secrets.token_hex(16)
    created=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload=secrets.token_bytes(100000)
    baseline_pin=None
    if baseline_fixture is not None:
        path=Path(baseline_fixture).absolute();fraw=read(path/'FIXTURE.json',50000)
        need(type(baseline_fixture_sha256) is str and sha(fraw)==baseline_fixture_sha256,'baseline_fixture_changed')
        record=json.loads(fraw)
        need(record['schema']=='meshticulum.synthetic-public100k-gfsk125-mb1-fixture.v1'
            and record['public_only'] is True and record['private_data'] is False
            and record['model_or_evaluation_content'] is False and record['whole_bytes']==100000,
            'baseline_public_whole_fixture_required')
        traw=read(path/'trust.json',16384);need(sha(traw)==record['trust_sha256'],'baseline_trust_changed')
        cache=ll.VerifiedCache(path/'pack',json.loads(traw),record['manifest_sha256'])
        need(sha(cache.signature)==record['signature_sha256'],'baseline_signature_changed')
        temporary=Path(tempfile.mkdtemp()).resolve();temporary.chmod(0o700)
        try:
            (temporary/'verified').mkdir(mode=0o700)
            with bundle.Receiver(temporary/'verified',cache.raw,read(path/'pack/manifest.sig.json',4096),
                json.loads(traw),record['manifest_sha256'],expires_epoch_us=60000000,
                clock=lambda:(1000000,1000000),boot_reader=lambda:'source-only-baseline') as receiver:
                for index,entry in enumerate(cache.value['chunks']):
                    receiver.accept(index,cache.piece(entry['sha256']))
                done=receiver.finish()
            payload=(temporary/'verified/bundle.bin').read_bytes()
            need(done['publisher_verified'] is True and len(payload)==100000
                and sha(payload)==record['whole_sha256'],'baseline_whole_content_changed')
            baseline_pin=baseline_fixture_sha256
        finally:shutil.rmtree(temporary)
    else:need(baseline_fixture_sha256 is None,'baseline_fixture_path_required')
    key=Ed25519PrivateKey.generate();public=kp.public_key_hex(key)
    manifest,chunks,signature=bundle.build(payload,pack_id='public-gfsk250-mb1-file-'+nonce,
        created_at=created,private_key=key,exact32=True);del key
    mraw=kp.manifest_bytes(manifest);sraw=kp.canonical_json(signature)+b'\n';digest=hashlib.sha256(mraw).hexdigest()
    trust=dict(schema='sage.knowledge-pack.trust-root.v1',keys=[dict(key_id=kp.key_id_for(public),algorithm='ed25519',
        public_key_hex=public,status='active',created_at=created,note='RAM-only CC0 synthetic file publisher')])
    traw=(json.dumps(trust,sort_keys=True,indent=2)+'\n').encode()
    output.mkdir(mode=0o700,exist_ok=False);pack=output/'pack';pack.mkdir(mode=0o700);(pack/'chunks').mkdir(mode=0o700)
    write(pack/'manifest.json',mraw);write(pack/'manifest.sig.json',sraw);write(output/'trust.json',traw)
    for name,record in chunks.items():write(pack/'chunks'/(name+'.json'),kp.chunk_bytes(record))
    cache=ll.VerifiedCache(pack,trust,digest);expected=bundle.descriptor(cache.value);rows=[]
    encoded=prepare_descriptor(mraw,sraw,trust,digest,chunks={name:kp.chunk_bytes(record) for name,record in chunks.items()})
    transport=MB1Transport(encoded,hashlib.sha256(encoded).hexdigest(),mraw,sraw,trust,digest)
    write(output/'TRANSPORT.json',encoded)
    for part in expected['parts']:
        request=transport.request(part['index']);response=transport.response(part['index'],cache)
        assert len(response)<32768
        rows.append(dict(index=part['index'],decoded_bytes=part['raw_bytes'],chunk_sha256=part['chunk_sha256'],
            request_bytes=len(request),response_bytes=len(response),response_sha256=hashlib.sha256(response).hexdigest()))
    result=dict(schema='meshticulum.synthetic-public100k-gfsk250-mb1-fixture.v1',public_only=True,private_data=False,
        transport_kind=TRANSPORT_KIND,transport_descriptor_sha256=hashlib.sha256(encoded).hexdigest(),
        model_or_evaluation_content=False,licence='CC0-1.0',native_admission=False,rf_activation=False,
        publisher_private_key_persisted_or_exported=False,manifest_sha256=digest,
        signature_sha256=hashlib.sha256(sraw).hexdigest(),trust_sha256=hashlib.sha256(traw).hexdigest(),
        whole_bytes=100000,whole_sha256=hashlib.sha256(payload).hexdigest(),parts=rows)
    if baseline_pin is not None:result['baseline_fixture_sha256']=baseline_pin
    write(output/'FIXTURE.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode())
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline-fixture',type=Path);parser.add_argument('--baseline-fixture-sha256')
    args=parser.parse_args();result=build(args.output,baseline_fixture=args.baseline_fixture,baseline_fixture_sha256=args.baseline_fixture_sha256);print(json.dumps(dict(fixture=str(result['manifest_sha256']),decoded_bytes=100000,parts=5,radio_opened=False)))
