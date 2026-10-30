"""Offline signed small-parts fixture; no transport or radio authority.

Derived from public byte_bundle.build at candidate base46f34d7; explicit
part size exercises five handoffs without consuming a100k native budget.
"""
import base64
from meshticulum.resources import knowledge_pack as kp
SCHEMA = "meshticulum.public-byte-bundle.v1"

def build_bundle(payload, *, pack_id, created_at, private_key, part_bytes):
    kp.require(type(part_bytes) is int and 1 <= part_bytes <= 23900, "invalid fixture part size")
    exact32 = False
    kp.require(type(payload) is bytes and 1 <= len(payload) <= kp.BUNDLE_BYTES
               and type(exact32) is bool, 'invalid public bundle payload')
    whole = kp.sha256_hex(payload); count = (len(payload)+part_bytes-1)//part_bytes
    chunks, entries = {}, []
    for index in range(count):
        raw = payload[index*part_bytes:(index+1)*part_bytes]
        part = dict(schema='meshticulum.public-byte-bundle-part.v1',encoding='base64',index=index,
            parts=count,part_bytes=part_bytes,offset=index*part_bytes,raw_bytes=len(raw),raw_sha256=kp.sha256_hex(raw),
            bundle_bytes=len(payload),bundle_sha256=whole,payload_base64=base64.b64encode(raw).decode('ascii'),wire_padding='')
        record = kp.build_chunk('part-%06d'%index,'Public file bytes.',title='Public file part',
            source='SAGE Foundation',date=created_at[:10],licence='CC0-1.0',bundle_part=part)
        if exact32:
            part['wire_padding']='_'*(kp.BUNDLE_CHUNK_BYTES-len(kp.chunk_bytes(record)))
            record=kp.build_chunk(record['id'],record['text'],title=record['title'],source=record['source'],
                date=record['date'],licence=record['licence'],bundle_part=part)
            kp.require(len(kp.chunk_bytes(record))==kp.BUNDLE_CHUNK_BYTES,'exact32 padding mismatch')
        chunks[record['sha256']]=record
        entries.append(dict(index=index,offset=part['offset'],raw_bytes=len(raw),raw_sha256=part['raw_sha256'],
                            chunk_sha256=record['sha256']))
    descriptor=dict(schema=SCHEMA,bytes=len(payload),sha256=whole,part_bytes=part_bytes,parts=entries)
    manifest=kp.build_manifest(pack_id=pack_id,version=1,pack_class='reference',title='Public byte bundle',
        licence='CC0-1.0',provenance=dict(source='SAGE Foundation public test data',note=kp.canonical_json(descriptor).decode()),
        created_at=created_at,chunks=chunks.values(),embedding_model='none',embedding_dimension=1)
    raw=kp.manifest_bytes(manifest)
    return manifest,chunks,kp.sign_manifest(raw,private_key)

