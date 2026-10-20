"""Opt-in lossless capture storage; interpretation grants no authority.

Readers retain the stored bytes and freshly check them at every existing gate.
The decoded canonical record, its schema and its SHA256 remain unchanged.
"""
import base64
import hashlib
import json
import re
import zlib

from .management_input import RAW_MAX

SCHEMA='meshticulum.private-capture-storage.v1'
PLAIN='plain-json-v1'
COMPACT='zlib-json-v1'
CODEC='zlib-base64'
_PREFIX='meshticulum.private-capture-storage.'


def need(value,reason):
    if not value:raise RuntimeError(reason)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def parsed(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            need(key not in result,'duplicate_capture_storage_key');result[key]=value
        return result
    try:
        value=json.loads(raw,object_pairs_hook=unique,
            parse_constant=lambda _:need(False,'invalid_capture_storage_number'))
    except (ValueError,UnicodeError):raise RuntimeError('invalid_capture_storage_JSON') from None
    need(type(value) is dict,'capture_storage_object_required')
    return value


def selection(request):
    """Only a future explicit fast250 request may create compact captures."""
    mode=request.get('capture_storage',PLAIN)
    need(type(mode) is str and mode in (PLAIN,COMPACT),'unknown_capture_storage_selection')
    if mode==COMPACT:
        need(request.get('qualification_kind')=='c-gfsk250-mb1-fast-bundle100k-v1',
            'compact_capture_requires_explicit_fast250')
    return mode


def encode(record,mode=PLAIN):
    need(type(record) is bytes and 0<len(record)<=RAW_MAX,'capture_record_size_bound')
    need(type(mode) is str and mode in (PLAIN,COMPACT),'unknown_capture_storage_selection')
    if mode==PLAIN:return record
    value=parsed(record)
    need(canonical(value)==record and not str(value.get('schema','')).startswith(_PREFIX),
        'canonical_capture_record_required')
    stored=canonical(dict(schema=SCHEMA,codec=CODEC,record_bytes=len(record),
        record_sha256=hashlib.sha256(record).hexdigest(),
        payload_base64=base64.b64encode(zlib.compress(record,6)).decode('ascii')))
    need(len(stored)<=RAW_MAX,'capture_storage_size_bound')
    return stored


def decode(stored,*,expected_sha256=None):
    """Decode once at admission; retain and recheck stored bytes afterward."""
    need(type(stored) is bytes and 0<len(stored)<=RAW_MAX,'capture_storage_size_bound')
    value=parsed(stored);schema=value.get('schema')
    if schema==SCHEMA:
        need(set(value)=={'schema','codec','record_bytes','record_sha256','payload_base64'}
            and value['codec']==CODEC and canonical(value)==stored,'invalid_capture_storage_envelope')
        size,pin,payload=value['record_bytes'],value['record_sha256'],value['payload_base64']
        need(type(size) is int and 0<size<=RAW_MAX and type(pin) is str
            and re.fullmatch('[0-9a-f]{64}',pin) and type(payload) is str,
            'invalid_capture_storage_binding')
        try:
            packed=base64.b64decode(payload,validate=True)
            need(base64.b64encode(packed).decode('ascii')==payload,'noncanonical_capture_storage_base64')
            decoder=zlib.decompressobj();record=decoder.decompress(packed,size+1)
        except (ValueError,UnicodeError,zlib.error):raise RuntimeError('invalid_capture_storage_stream') from None
        need(len(record)==size and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
            'capture_storage_stream_bound')
        need(hashlib.sha256(record).hexdigest()==pin,'capture_storage_record_hash_changed')
        decoded=parsed(record)
        need(canonical(decoded)==record and not str(decoded.get('schema','')).startswith(_PREFIX),
            'canonical_capture_record_required')
    else:
        need(not (type(schema) is str and schema.startswith(_PREFIX)),'unknown_capture_storage_schema')
        record=stored
    if expected_sha256 is not None:
        need(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}',expected_sha256)
            and hashlib.sha256(record).hexdigest()==expected_sha256,'capture_canonical_pin_changed')
    return record
