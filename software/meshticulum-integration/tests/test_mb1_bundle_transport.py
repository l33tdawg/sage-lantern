"""Source-only MB1 descriptor/MR1/receiver boundary; no native admission."""
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from meshticulum.bench.wire import WireError
from meshticulum.resources import byte_bundle as bb,knowledge_pack as kp,lamplighter as ll,resource
from lantern_meshticulum.bench import pilot_bundle_transport as mb1


@pytest.fixture
def published(tmp_path):
    payload=bytes(range(256))*390+bytes(range(160))
    key=Ed25519PrivateKey.generate()
    manifest,records,signature=bb.build(payload,pack_id='mb1-boundary',created_at='2026-10-13T00:00:00Z',
        private_key=key,exact32=True)
    mraw=kp.manifest_bytes(manifest);sraw=kp.canonical_json(signature);digest=kp.sha256_hex(mraw)
    trust=dict(schema='sage-foundation.knowledge-pack.trust.v1',keys=[dict(key_id=signature['key_id'],
        public_key_hex=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex(),status='active',pack_classes=['reference'])])
    chunks={digest:kp.chunk_bytes(record) for digest,record in records.items()}
    raw=mb1.prepare_descriptor(mraw,sraw,trust,digest,chunks=chunks)
    transport=mb1.MB1Transport(raw,kp.sha256_hex(raw),mraw,sraw,trust,digest)
    seed=tmp_path/'seed';(seed/'chunks').mkdir(parents=True)
    (seed/'manifest.json').write_bytes(mraw);(seed/'manifest.sig.json').write_bytes(sraw)
    for name,chunk in chunks.items():(seed/'chunks'/(name+'.json')).write_bytes(chunk)
    cache=ll.VerifiedCache(seed,trust,digest)
    return dict(payload=payload,manifest=manifest,mraw=mraw,sraw=sraw,digest=digest,trust=trust,chunks=chunks,
        raw=raw,transport=transport,cache=cache)


def descriptor(published,value):
    raw=kp.canonical_json(value)
    return mb1.MB1Transport(raw,kp.sha256_hex(raw),published['mraw'],published['sraw'],published['trust'],published['digest'])


def test_five_variable_MR1_replies_restore_exact_signed_chunks_and_cache_identity(published):
    p=published;t=p['transport'];total=0
    for index in range(5):
        part=t.part(index);request=t.request(index);response=t.response(index,p['cache'])
        assert resource.decode_request(request,public32=True)[1:]==(0,part['mb1_bytes'])
        canonical=t.decode_response(index,request,response)
        assert canonical==p['chunks'][part['chunk_content_address']]
        assert ll.chunk(canonical,p['manifest']['chunks'][index])['sha256']==part['chunk_content_address']
        assert len(response)==part['response_bytes']<32768
        total+=len(response)
    assert total==103345


def test_out_of_order_verified_MB1_keeps_receiver_whole_SHA_replay_and_canonical_storage(published,tmp_path):
    p=published;t=p['transport'];target=tmp_path/'receiver';target.mkdir(mode=0o700)
    with bb.Receiver(target,p['mraw'],p['sraw'],p['trust'],p['digest'],expires_epoch_us=10000000,
        clock=lambda:(1000000,2000000),boot_reader=lambda:'test-boot') as receiver:
        for index in (4,0,3,1,2):t.accept_response(receiver,index,t.request(index),t.response(index,p['cache']))
        with pytest.raises(ValueError):t.accept_response(receiver,0,t.request(0),t.response(0,p['cache']))
        done=receiver.finish()
        assert done['useful_decoded_bytes']==100000 and done['whole_file_sha256']==kp.sha256_hex(p['payload'])
        assert done['original_expires_epoch_us']==10000000
        for name,raw in p['chunks'].items():assert (target/'parts'/(name+'.json')).read_bytes()==raw
        assert (target/'bundle.bin').read_bytes()==p['payload']


@pytest.mark.parametrize('fault',['payload','short','long','request','cross_piece'])
def test_tampered_transport_never_reaches_receiver_accept(published,tmp_path,fault):
    p=published;t=p['transport'];request=t.request(0);response=t.response(0,p['cache'])
    if fault=='payload':response=response[:-1]+bytes([response[-1]^1])
    elif fault=='short':response=response[:-1]
    elif fault=='long':response+=b'\0'
    elif fault=='request':request=t.request(1)
    else:response=t.response(1,p['cache'])
    target=tmp_path/'receiver';target.mkdir(mode=0o700)
    with bb.Receiver(target,p['mraw'],p['sraw'],p['trust'],p['digest'],expires_epoch_us=10000000,
        clock=lambda:(1000000,2000000),boot_reader=lambda:'test-boot') as receiver:
        with pytest.raises((ValueError,RuntimeError)):t.accept_response(receiver,0,request,response)
        assert list((target/'parts').iterdir())==[]


@pytest.mark.parametrize('fault',['kind','index_bool','response_length','request_hash','canonical_identity','unknown'])
def test_new_descriptor_pin_cannot_override_original_publisher_or_MR1_structure(published,fault):
    value=json.loads(published['raw']);row=value['parts'][0]
    if fault=='kind':value['transport_kind']='legacy-json'
    elif fault=='index_bool':row['index']=False
    elif fault=='response_length':row['response_bytes']+=1
    elif fault=='request_hash':row['request_sha256']='f'*64
    elif fault=='canonical_identity':row['chunk_content_address']=value['parts'][1]['chunk_content_address']
    else:value['unexpected']=1
    with pytest.raises((ValueError,RuntimeError)):descriptor(published,value)


def test_rebound_wire_hash_does_not_substitute_for_publisher_chunk_verification(published):
    p=published;t=p['transport'];reply=t.response(0,p['cache']);reply=reply[:-1]+bytes([reply[-1]^1])
    value=json.loads(p['raw']);row=value['parts'][0]
    row['response_sha256']=kp.sha256_hex(reply);row['mb1_sha256']=kp.sha256_hex(reply[resource.RESPONSE.size:])
    rebound=descriptor(p,value)
    with pytest.raises(ValueError):rebound.decode_response(0,rebound.request(0),reply)


@pytest.mark.parametrize('fault',['binding','status','total','offset'])
def test_even_a_rebound_response_hash_must_match_the_original_MR1_request(published,fault):
    p=published;t=p['transport'];reply=t.response(0,p['cache'])
    fields=list(resource.RESPONSE.unpack_from(reply))
    if fault=='binding':fields[1]=b'\0'*16
    elif fault=='status':fields[2]=1
    elif fault=='total':fields[3]-=1
    else:fields[4]=1
    reply=resource.RESPONSE.pack(*fields)+reply[resource.RESPONSE.size:]
    value=json.loads(p['raw']);value['parts'][0]['response_sha256']=kp.sha256_hex(reply)
    rebound=descriptor(p,value)
    with pytest.raises(WireError):rebound.decode_response(0,rebound.request(0),reply)


@pytest.mark.parametrize('fault',['expiry','boot','trust_revoked'])
def test_original_receiver_deadline_boot_and_live_publisher_trust_remain(published,tmp_path,fault):
    p=published;t=p['transport'];target=tmp_path/'receiver';target.mkdir(mode=0o700)
    now=[1000000,2000000];boot=['test-boot'];response=t.response(0,p['cache'])
    with bb.Receiver(target,p['mraw'],p['sraw'],p['trust'],p['digest'],expires_epoch_us=10000000,
        clock=lambda:tuple(now),boot_reader=lambda:boot[0]) as receiver:
        if fault=='expiry':now[:]=[10000000,11000000]
        elif fault=='boot':boot[0]='new-boot'
        else:p['trust']['keys'][0]['status']='revoked'
        with pytest.raises(ValueError):t.accept_response(receiver,0,t.request(0),response)
        assert list((target/'parts').iterdir())==[]


def test_legacy_JSON_server_receiver_and_admission_kinds_are_unchanged(published,tmp_path):
    from lantern_meshticulum.bench.pilot_parent_caps import BUNDLE_KINDS,PUBLIC32_KINDS
    p=published;t=p['transport'];entry=p['manifest']['chunks'][0]
    request=resource.encode_request('/packs/'+p['digest']+'/chunks/'+entry['sha256']+'.json',0,32740,public32=True)
    old=resource.BlockServer(p['cache'].read,public32=True).handle(request)
    assert len(old)==32768 and old[resource.RESPONSE.size:]==p['chunks'][entry['sha256']]
    with pytest.raises(WireError):t.decode_response(0,request,old)
    assert mb1.KIND not in BUNDLE_KINDS and mb1.KIND not in PUBLIC32_KINDS
    target=tmp_path/'legacy';target.mkdir(mode=0o700)
    with bb.Receiver(target,p['mraw'],p['sraw'],p['trust'],p['digest'],expires_epoch_us=10000000,
        clock=lambda:(1000000,2000000),boot_reader=lambda:'test-boot') as receiver:
        receiver.accept(0,old[resource.RESPONSE.size:])
        assert (target/'parts'/(entry['sha256']+'.json')).read_bytes()==p['chunks'][entry['sha256']]


def test_descriptor_input_pin_and_immutable_internal_bindings_cannot_change(published):
    p=published
    with pytest.raises(WireError):mb1.MB1Transport(p['raw'],'0'*64,p['mraw'],p['sraw'],p['trust'],p['digest'])
    with pytest.raises(WireError):mb1.MB1Transport(p['raw'],kp.sha256_hex(p['raw']),bytearray(p['mraw']),p['sraw'],p['trust'],p['digest'])
    p['transport']._parts=tuple(reversed(p['transport']._parts))
    with pytest.raises(WireError):p['transport'].request(0)


def test_existing_generic_one_million_byte_limit_verifies_shuffled_whole_file(tmp_path):
    # Generic source capability only; physical native100k policy stays unchanged.
    assert kp.BUNDLE_BYTES==1000000
    payload=bytes(range(256))*3906+bytes(range(64));assert len(payload)==1000000
    key=Ed25519PrivateKey.generate()
    manifest,records,signature=bb.build(payload,pack_id='mb1-million',created_at='2026-10-13T00:00:00Z',
        private_key=key,exact32=True)
    mraw=kp.manifest_bytes(manifest);sraw=kp.canonical_json(signature);digest=kp.sha256_hex(mraw)
    trust=dict(schema='sage-foundation.knowledge-pack.trust.v1',keys=[dict(key_id=signature['key_id'],
        public_key_hex=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex(),status='active',pack_classes=['reference'])])
    chunks={name:kp.chunk_bytes(record) for name,record in records.items()}
    raw=mb1.prepare_descriptor(mraw,sraw,trust,digest,chunks=chunks)
    transport=mb1.MB1Transport(raw,kp.sha256_hex(raw),mraw,sraw,trust,digest)
    seed=tmp_path/'million-seed';(seed/'chunks').mkdir(parents=True)
    (seed/'manifest.json').write_bytes(mraw);(seed/'manifest.sig.json').write_bytes(sraw)
    for name,chunk in chunks.items():(seed/'chunks'/(name+'.json')).write_bytes(chunk)
    cache=ll.VerifiedCache(seed,trust,digest)
    target=tmp_path/'million-receiver';target.mkdir(mode=0o700)
    assert len(manifest['chunks'])==42
    order=[(i*13+7)%42 for i in range(42)]
    assert sorted(order)==list(range(42)) and order!=list(range(42))
    with bb.Receiver(target,mraw,sraw,trust,digest,expires_epoch_us=10000000,
        clock=lambda:(1000000,2000000),boot_reader=lambda:'test-boot') as receiver:
        for index in order:
            transport.accept_response(receiver,index,transport.request(index),transport.response(index,cache))
        done=receiver.finish()
        assert done['publisher_verified'] is True and done['useful_decoded_bytes']==1000000
        assert done['whole_file_sha256']==kp.sha256_hex(payload)
        assert done['parts']==42 and done['serialized_chunk_bytes']==42*32740
        assert (target/'bundle.bin').read_bytes()==payload
        assert all((target/'parts'/(name+'.json')).read_bytes()==chunk for name,chunk in chunks.items())
