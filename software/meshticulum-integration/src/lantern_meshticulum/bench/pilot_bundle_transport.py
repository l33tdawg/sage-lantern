# SPDX-License-Identifier: Apache-2.0
"""Explicit MB1 transport descriptor and verified canonical reconstruction.

This is a source boundary for a future native qualification kind. It does not
register that kind, supply authority, open a transport, issue a grant or relax
the existing exact32 native policy. Its descriptor hash must be bound by a
separately reviewed original owner/context before physical use. MR1 requests
still identify original signed canonical chunks; only the explicitly selected
response representation is MB1. Canonical receiver/cache formats do not change.
"""
import hashlib
import json
from typing import NamedTuple

from meshticulum.bench.wire import require
from meshticulum.resources import bundle_wire, byte_bundle, knowledge_pack as kp, lamplighter as ll, resource

KIND='mb1-byte-bundle-transport-v1'
SCHEMA='meshticulum.lantern-mb1-byte-bundle-transport.v1'
FIELDS=frozenset(('schema','transport_kind','manifest_sha256','signature_sha256',
    'whole_bytes','whole_sha256','parts'))


class _Part(NamedTuple):
    index:int
    chunk_content_address:str
    canonical_bytes:int
    canonical_bytes_sha256:str
    mb1_bytes:int
    mb1_sha256:str
    request_bytes:int
    request_sha256:str
    response_bytes:int
    response_sha256:str


def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _pin(value):return type(value) is str and kp.HEX64.fullmatch(value)


def _request(digest,part):
    return resource.encode_request('/packs/'+digest+'/chunks/'+part.chunk_content_address+'.json',
        0,part.mb1_bytes,public32=True)


def _response(request,wire):
    return resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,len(wire),0)+wire


def prepare_descriptor(manifest_raw,signature_raw,trust,manifest_sha256,*,chunks):
    """Prepare reviewable data pins only; no native state or clocks are created."""
    require(type(manifest_raw) is bytes and 0<len(manifest_raw)<=ll.MAX_MANIFEST
        and type(signature_raw) is bytes and 0<len(signature_raw)<=4096,'mb1_immutable_metadata_required')
    manifest=ll.manifest(manifest_raw,signature_raw,trust,manifest_sha256)
    whole=byte_bundle.descriptor(manifest)
    require(type(chunks) is dict and set(chunks)=={e['sha256'] for e in manifest['chunks']},
        'mb1_chunk_roster_changed')
    parts=[]
    for index,entry in enumerate(manifest['chunks']):
        raw=chunks[entry['sha256']];wire=bundle_wire.encode_chunk(raw,entry)
        part=_Part(index,entry['sha256'],len(raw),_sha(raw),len(wire),_sha(wire),0,'',0,'')
        request=_request(manifest_sha256,part);response=_response(request,wire)
        parts.append(part._replace(request_bytes=len(request),request_sha256=_sha(request),
            response_bytes=len(response),response_sha256=_sha(response))._asdict())
    return kp.canonical_json(dict(schema=SCHEMA,transport_kind=KIND,manifest_sha256=manifest_sha256,
        signature_sha256=_sha(signature_raw),whole_bytes=whole['bytes'],whole_sha256=whole['sha256'],parts=parts))


class MB1Transport:
    """Authenticated signed data plus an independently pinned transport hash.

    Method results supply no native permission. Receiver.accept retains its
    original owner, publisher trust, replay and whole-file deadline checks.
    """
    __slots__=('_raw','_pin','_manifest_raw','_signature_raw','_digest','_parts','_refs','_captured_refs')

    def __init__(self,raw,pin,manifest_raw,signature_raw,trust,manifest_sha256):
        require(type(manifest_raw) is bytes and 0<len(manifest_raw)<=ll.MAX_MANIFEST
            and type(signature_raw) is bytes and 0<len(signature_raw)<=4096,'mb1_immutable_metadata_required')
        require(type(raw) is bytes and 0<len(raw)<=65536 and _pin(pin) and _sha(raw)==pin,
            'mb1_transport_descriptor_pin_changed')
        value=json.loads(raw)
        require(type(value) is dict and set(value)==FIELDS and kp.canonical_json(value)==raw
            and value['schema']==SCHEMA and value['transport_kind']==KIND,
            'mb1_transport_descriptor_changed')
        manifest=ll.manifest(manifest_raw,signature_raw,trust,manifest_sha256)
        whole=byte_bundle.descriptor(manifest)
        require(value['manifest_sha256']==manifest_sha256 and value['signature_sha256']==_sha(signature_raw)
            and type(value['whole_bytes']) is int and value['whole_bytes']==whole['bytes']
            and value['whole_sha256']==whole['sha256'] and type(value['parts']) is list
            and len(value['parts'])==len(manifest['chunks']),'mb1_original_publisher_binding_changed')
        parts=[]
        for index,(row,entry) in enumerate(zip(value['parts'],manifest['chunks'])):
            require(type(row) is dict and set(row)==set(_Part._fields)
                and all(type(row[k]) is int for k in ('index','canonical_bytes','mb1_bytes','request_bytes','response_bytes'))
                and row['index']==index and row['chunk_content_address']==entry['sha256']
                and row['canonical_bytes']==entry['bytes']
                and bundle_wire.HEADER.size<row['mb1_bytes']<=kp.BUNDLE_CHUNK_BYTES
                and row['response_bytes']==row['mb1_bytes']+resource.RESPONSE.size
                and all(_pin(row[k]) for k in ('canonical_bytes_sha256','mb1_sha256','request_sha256','response_sha256')),
                'mb1_part_descriptor_changed')
            part=_Part(**row);request=_request(manifest_sha256,part)
            require(part.request_bytes==len(request) and part.request_sha256==_sha(request),
                'mb1_original_request_changed')
            parts.append(part)
        self._raw,self._pin,self._manifest_raw,self._signature_raw,self._digest=raw,pin,manifest_raw,signature_raw,manifest_sha256
        self._parts=tuple(parts)
        self._refs=(raw,pin,manifest_raw,signature_raw,manifest_sha256,self._parts)
        refs=self._refs;self._captured_refs=lambda:refs

    def _check(self):
        refs=self._captured_refs()
        require(type(refs) is tuple and len(refs)==6 and self._refs is refs and all(a is b for a,b in zip(
            (self._raw,self._pin,self._manifest_raw,self._signature_raw,self._digest,self._parts),refs)),
            'mb1_transport_rebound')

    def part(self,index):
        self._check()
        require(type(index) is int and 0<=index<len(self._parts),'mb1_unplanned_part')
        return self._parts[index]._asdict()

    def request(self,index):
        self.part(index)
        return _request(self._digest,self._parts[index])

    def response(self,index,cache):
        """Read a verified canonical cache and explicitly encode an MB1 reply."""
        self.part(index)
        require(type(cache) is ll.VerifiedCache and cache.digest==self._digest
            and cache.raw==self._manifest_raw and cache.signature==self._signature_raw,
            'mb1_publisher_cache_rebound')
        part=self._parts[index];raw=cache.piece(part.chunk_content_address)
        manifest=json.loads(self._manifest_raw)
        wire=bundle_wire.encode_chunk(raw,manifest['chunks'][index])
        response=_response(self.request(index),wire)
        self.decode_response(index,self.request(index),response)
        return response

    def decode_response(self,index,request,response):
        """Verify transport pins and return the original signed canonical bytes."""
        self.part(index);part=self._parts[index]
        require(type(request) is bytes and request==self.request(index)
            and type(response) is bytes and len(response)==part.response_bytes
            and _sha(response)==part.response_sha256,'mb1_response_hash_or_length_changed')
        magic,binding,status,total,offset=resource.RESPONSE.unpack_from(response)
        require((magic,binding,status,total,offset)==(resource.MAGIC,hashlib.sha256(request).digest()[:16],
            0,part.mb1_bytes,0),'mb1_response_request_binding_changed')
        wire=response[resource.RESPONSE.size:]
        require(len(wire)==part.mb1_bytes and _sha(wire)==part.mb1_sha256 and wire.startswith(bundle_wire.MAGIC),
            'mb1_wire_representation_changed')
        manifest=json.loads(self._manifest_raw)
        raw=bundle_wire.decode_chunk(wire,manifest['chunks'][index])
        require(len(raw)==part.canonical_bytes and _sha(raw)==part.canonical_bytes_sha256,
            'mb1_canonical_bytes_changed')
        record=ll.chunk(raw,manifest['chunks'][index]);kp.validate_pack_rules(manifest,[record])
        self._check();return raw

    def accept_response(self,receiver,index,request,response):
        """Decode before the unchanged protected receiver's accept operation.

        A genuine Receiver retains the original deadline and trust. This
        adapter supplies no native capture provenance or transport authority.
        """
        require(type(receiver) is byte_bundle.Receiver,'mb1_original_receiver_required')
        receiver._live();self._check()
        require(receiver.raw==self._manifest_raw and receiver.signature==self._signature_raw
            and receiver.digest==self._digest,'mb1_receiver_manifest_changed')
        raw=self.decode_response(index,request,response)
        receiver.accept(index,raw)
        self._check()
