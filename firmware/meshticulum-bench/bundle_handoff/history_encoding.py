"""Bounded local-only lossless history reconstruction; remote retains opaque bytes.

Five fixed operations reproduce frozen historical bytes: literal, USTAR,
xz32, gzip9 and slice. No stored source is executed, no extraction occurs,
and no external path is read during verification. Every intermediate and
original output must match its exact expected size and SHA256. Codec-version
or recompression drift refuses; cross-version portability is not claimed.
"""
import ctypes
import ctypes.util
import gzip
import hashlib
import io
import json
import lzma
from pathlib import Path,PurePosixPath
import platform
import re
import tarfile
import zlib

MAX_NODES=2048
MAX_DEPTH=12
MAX_NODE_BYTES=128*1024*1024
MAX_GENERATED_BYTES=512*1024*1024
MAX_STORED_BLOB=8_000_000
MEMLIMIT=128*1024*1024
MAX_CODEC_INPUT_BYTES=256*1024*1024
MAX_CODEC_NODES=8
MAX_ENCODED_BYTES=16_000_000
CHUNK_BYTES=4*1024*1024
MAX_INDEX_BYTES=2_000_000
MAX_JSON_DEPTH=32
SCHEMA='meshticulum.opaque-history-reconstruction.v1'
DAG_SCHEMA='meshticulum.fixed-history-DAG.v1'
NESTED_INDEX_SHA='183e3ac9cb26fb5e3b5c51d795031cfc7a9d51277692ed979703547c768928c8'
NESTED_XZ_SHA='2b7c6636cc298187d9c2985b831b27398b6b109ad843dc880611903bd6e680e4'
ARCHIVE_MAP_SHA='af5c7067662c4f0be59a79048da2d450de02d3889143439daf6f2d7f99c4471d'
sha=lambda b:hashlib.sha256(b).hexdigest()
canonical=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def need(value):
    if not value:raise ValueError('bounded_dag_refused')


def checked_name(name):
    need(type(name) is str and len(name)<=100 and bool(name) and '\\' not in name and '\x00' not in name)
    path=PurePosixPath(name)
    need(not path.is_absolute() and '..' not in path.parts and '.' not in name.split('/')
         and str(path)==name)


def ustar(members):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for name,payload in members:
            checked_name(name)
            info=tarfile.TarInfo(name);info.size=len(payload);info.mode=0o600
            info.uid=info.gid=info.mtime=0;info.uname=info.gname=''
            archive.addfile(info,io.BytesIO(payload))
    return stream.getvalue()


def frozen_gzip(raw):
    stream=io.BytesIO()
    with gzip.GzipFile(fileobj=stream,mode='wb',filename='',mtime=0,compresslevel=9) as zipped:
        zipped.write(raw)
    return stream.getvalue()


def node_bytes(node,outputs,blobs):
    common={'op','bytes','sha256'};op=node.get('op')
    fields={'literal':{'blob'},'ustar':{'members'},'xz32':{'source'},'gzip9':{'source'},
            'slice':{'source','offset'}}
    need(type(op) is str and op in fields and set(node)==common|fields[op])
    need(type(node['bytes']) is int and 0<node['bytes']<=MAX_NODE_BYTES
         and type(node['sha256']) is str and re.fullmatch('[0-9a-f]{64}',node['sha256']))
    if op=='literal':
        need(type(node['blob']) is str and node['blob'] in blobs and node['bytes']<=MAX_STORED_BLOB)
        value=blobs[node['blob']]
        need(type(value) is bytes and sha(value)==node['blob'])
    elif op=='ustar':
        rows=node['members'];need(type(rows) is list and 1<=len(rows)<=500)
        names=[];members=[]
        for row in rows:
            need(type(row) is dict and set(row)=={'name','node'} and row['node'] in outputs)
            checked_name(row['name']);names.append(row['name'])
            need(len(outputs[row['node']])<=MAX_STORED_BLOB)
            members.append((row['name'],outputs[row['node']]))
        need(names==sorted(set(names)))
        need(sum(len(value) for _,value in members)+1024*len(rows)+10240<=MAX_NODE_BYTES)
        value=ustar(members)
    else:
        need(node['source'] in outputs)
        source=outputs[node['source']]
        if op=='slice':
            need(type(node['offset']) is int and 0<=node['offset']
                 and node['offset']+node['bytes']<=len(source)
                 and node['bytes']<=MAX_STORED_BLOB)
            value=source[node['offset']:node['offset']+node['bytes']]
        elif op=='xz32':
            value=lzma.compress(source,format=lzma.FORMAT_XZ,check=lzma.CHECK_SHA256,
                filters=[dict(id=lzma.FILTER_LZMA2,preset=3,dict_size=32*1024*1024)])
        else:value=frozen_gzip(source)
    need(len(value)==node['bytes'] and sha(value)==node['sha256'])
    return value


def reconstruct(nodes,blobs):
    need(type(nodes) is list and 1<=len(nodes)<=MAX_NODES)
    outputs={};depths={};generated=0;codec_input=0;codec_nodes=0
    for row in nodes:
        need(type(row) is dict and set(row)=={'id','node'} and type(row['node']) is dict
             and type(row['id']) is str and row['id']==sha(canonical(row['node']))
             and row['id'] not in outputs)
        node=row['node'];op=node.get('op')
        need(type(op) is str and op in ('literal','ustar','xz32','gzip9','slice'))
        if op=='ustar':
            need(type(node.get('members')) is list and 1<=len(node['members'])<=500)
            need(all(type(m) is dict and set(m)=={'name','node'} and type(m['node']) is str
                     for m in node['members']))
        elif op in ('xz32','gzip9','slice'):need(type(node.get('source')) is str)
        deps=([m['node'] for m in node.get('members',[])] if op=='ustar'
              else [node.get('source')] if op in ('xz32','gzip9','slice') else [])
        need(all(dep in outputs for dep in deps))  # Reject forward edges/cycles.
        depth=1+max((depths[d] for d in deps),default=0);need(depth<=MAX_DEPTH)
        # Bound declared output accumulation before allocating the next node.
        need(type(node.get('bytes')) is int)
        generated+=node['bytes'];need(generated<=MAX_GENERATED_BYTES)
        if op in ('xz32','gzip9'):
            codec_nodes+=1;codec_input+=len(outputs[node['source']])
            need(codec_nodes<=MAX_CODEC_NODES and codec_input<=MAX_CODEC_INPUT_BYTES)
        outputs[row['id']]=node_bytes(node,outputs,blobs);depths[row['id']]=depth
    return outputs,dict(nodes=len(nodes),maximum_depth=max(depths.values()),
        generated_intermediate_bytes=generated,stored_blob_bytes=sum(map(len,blobs.values())))


class Builder:
    def __init__(self):self.nodes=[];self.blobs={};self.ids=set();self.values={}
    def add(self,op,value,**fields):
        node=dict(op=op,bytes=len(value),sha256=sha(value),**fields);key=sha(canonical(node))
        if key not in self.ids:self.nodes.append(dict(id=key,node=node));self.ids.add(key)
        self.values[key]=value
        return key
    def literal(self,value):
        need(0<len(value)<=MAX_STORED_BLOB)
        digest=sha(value);self.blobs[digest]=value
        return self.add('literal',value,blob=digest)
    def tar(self,members):
        rows=[dict(name=name,node=key) for name,key in sorted(members)]
        return self.add('ustar',ustar([(r['name'],self.values[r['node']]) for r in rows]),members=rows)
    def slice(self,source,offset,size):
        return self.add('slice',self.values[source][offset:offset+size],source=source,offset=offset)


def parse(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_STORED_BLOB)
    depth=0;quoted=False;escaped=False
    for byte in raw:
        if quoted:
            if escaped:escaped=False
            elif byte==92:escaped=True
            elif byte==34:quoted=False
        elif byte==34:quoted=True
        elif byte in (91,123):
            depth+=1;need(depth<=MAX_JSON_DEPTH)
        elif byte in (93,125):
            depth-=1;need(depth>=0)
    need(depth==0 and not quoted)
    def pairs(items):
        result={}
        for key,value in items:
            need(key not in result);result[key]=value
        return result
    try:
        return json.loads(raw,object_pairs_hook=pairs,
            parse_constant=lambda _:need(False))
    except (ValueError,TypeError,RecursionError,UnicodeError) as error:
        raise ValueError('bounded_dag_json_refused') from error


def digest(value):
    need(type(value) is str and re.fullmatch('[0-9a-f]{64}',value))


def checked_index(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_INDEX_BYTES)
    index=parse(raw)
    need(type(index) is dict and set(index)=={'schema','files','pin_mismatches',
        'global_historical_debt_complete','historical_unknown_actual_cost','install_authority','rf_authority'}
        and index['schema']=='meshticulum.app-upgrade-local-history-index.v1'
        and index['pin_mismatches']==[] and index['global_historical_debt_complete'] is False
        and index['historical_unknown_actual_cost'] is None
        and index['install_authority'] is False and index['rf_authority'] is False)
    rows=index['files'];need(type(rows) is list and 1<=len(rows)<=500)
    paths=[]
    for row in rows:
        need(type(row) is dict and set(row)=={'path','sha256','bytes'}
            and type(row['path']) is str and 0<len(row['path'])<=4096
            and '\x00' not in row['path'] and Path(row['path']).is_absolute()
            and str(Path(row['path']))==row['path']
            and '..' not in Path(row['path']).parts
            and type(row['bytes']) is int and 0<row['bytes']<=MAX_STORED_BLOB)
        digest(row['sha256']);paths.append(row['path'])
    need(paths==sorted(set(paths)) and sum(r['bytes'] for r in rows)<=MAX_NODE_BYTES)
    return rows


def codec_versions():
    library=ctypes.CDLL(ctypes.util.find_library('lzma'))
    library.lzma_version_string.restype=ctypes.c_char_p
    return dict(python=platform.python_version(),zlib_compile=zlib.ZLIB_VERSION,
        zlib_runtime=zlib.ZLIB_RUNTIME_VERSION,
        reported_liblzma=library.lzma_version_string().decode('ascii'),
        liblzma_version_probe="ctypes.find_library('lzma')",
        builtin_lzma_linkage_verified=False)


def ungzip_bounded(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_ENCODED_BYTES)
    decoder=zlib.decompressobj(31)
    output=decoder.decompress(raw,32_000_000+1)
    need(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
        and len(output)<=32_000_000)
    return output


def make_document(index_raw,read):
    rows=checked_index(index_raw);by_path={row['path']:row for row in rows};originals={}
    for row in rows:
        raw=read(row['path'],row['sha256'])
        need(type(raw) is bytes and len(raw)==row['bytes'] and sha(raw)==row['sha256'])
        originals[row['path']]=raw
    def unique_input(pin):
        values=[originals[row['path']] for row in rows if row['sha256']==pin]
        need(len(values)==1);return values[0]
    nested=parse(unique_input(NESTED_INDEX_SHA))
    mapping=parse(unique_input(ARCHIVE_MAP_SHA))
    need(type(mapping) is dict and set(mapping)=={'schema','source_only','rf_authorized',
        'upgrade_deployment','original_archives_unchanged','units','global_historical_debt_complete',
        'historical_unknown_actual_cost','refunds'}
        and mapping['schema']=='meshticulum.local-lossless-archive-history.v1'
        and mapping['source_only'] is True and mapping['rf_authorized'] is False
        and mapping['original_archives_unchanged'] is True
        and mapping['upgrade_deployment']=='c68a9c0f78c0411ebe91ccbc053ce499'
        and mapping['global_historical_debt_complete'] is False
        and mapping['historical_unknown_actual_cost'] is None
        and type(mapping['refunds']) is int and mapping['refunds']==0
        and type(mapping['units']) is dict and set(mapping['units'])=={'unit-a','unit-b'})
    piece_paths=[]
    for row in mapping['units'].values():
        need(type(row) is dict and set(row)=={'path','bytes','sha256','chunks'}
            and type(row['bytes']) is int and 0<row['bytes']<=MAX_ENCODED_BYTES
            and type(row['chunks']) is list and len(row['chunks'])==4)
        digest(row['sha256'])
        for piece in row['chunks']:
            need(type(piece) is dict and set(piece)=={'path','bytes','sha256'}
                and piece['path'] in by_path and piece==by_path[piece['path']]
                and piece['bytes']<=CHUNK_BYTES)
            piece_paths.append(piece['path'])
    need(len(set(piece_paths))==8)
    b=Builder();original_nodes={}
    for path,raw in originals.items():
        if path not in piece_paths:original_nodes[path]=b.literal(raw)
    nested_rows=nested['files']
    need(type(nested_rows) is list and len(nested_rows)==264)
    for row in nested_rows:need(row['path'] in by_path and row==by_path[row['path']])
    old_tar=b.tar([(f'{i:04d}.bin',original_nodes[row['path']]) for i,row in enumerate(nested_rows)])
    old_encoded=lzma.compress(b.values[old_tar],format=lzma.FORMAT_XZ,check=lzma.CHECK_SHA256,
        filters=[dict(id=lzma.FILTER_LZMA2,preset=3,dict_size=32*1024*1024)])
    need(sha(old_encoded)==NESTED_XZ_SHA and len(old_encoded)<=MAX_ENCODED_BYTES)
    old_xz=b.add('xz32',old_encoded,source=old_tar)
    old_parts={f'history/stream-{i:04d}.xz.part':b.slice(old_xz,start,min(CHUNK_BYTES,len(old_encoded)-start))
        for i,start in enumerate(range(0,len(old_encoded),CHUNK_BYTES))}
    for _,row in sorted(mapping['units'].items()):
        archive_raw=b''.join(originals[piece['path']] for piece in row['chunks'])
        need(len(archive_raw)==row['bytes'] and sha(archive_raw)==row['sha256'])
        tar_raw=ungzip_bounded(archive_raw);members=[];names=set()
        with tarfile.open(fileobj=io.BytesIO(tar_raw),mode='r:') as archive:
            for info in archive:
                checked_name(info.name)
                need(info.isfile() and info.name not in names and len(names)<1024
                    and 0<info.size<=MAX_STORED_BLOB and info.mode==0o600
                    and info.uid==info.gid==info.mtime==0 and not info.uname and not info.gname
                    and not info.pax_headers)
                names.add(info.name);payload=archive.extractfile(info).read(info.size+1)
                need(len(payload)==info.size)
                key=old_parts.get(info.name)
                if key is not None:need(b.values[key]==payload)
                else:key=b.literal(payload)
                members.append((info.name,key))
        need(len(members)==94 and set(old_parts)<=names)
        tar_node=b.tar(members);need(b.values[tar_node]==tar_raw)
        rebuilt=frozen_gzip(tar_raw);need(rebuilt==archive_raw)
        archive_node=b.add('gzip9',rebuilt,source=tar_node);offset=0
        for piece in row['chunks']:
            key=b.slice(archive_node,offset,piece['bytes']);offset+=piece['bytes']
            need(b.values[key]==originals[piece['path']]);original_nodes[piece['path']]=key
        need(offset==len(rebuilt))
    need(set(original_nodes)==set(originals) and len(b.nodes)<=MAX_NODES)
    document=dict(schema=DAG_SCHEMA,nodes=b.nodes,
        originals=[dict(row,node=original_nodes[row['path']]) for row in rows],
        codec_versions=codec_versions())
    return document,b.blobs


def pack(index_raw,read):
    document,blobs=make_document(index_raw,read)
    document_raw=canonical(document);need(len(document_raw)<=MAX_STORED_BLOB)
    members=[('DAG.json',document_raw)]+[(f'blobs/{digest}.bin',raw) for digest,raw in sorted(blobs.items())]
    need(len(members)<=MAX_NODES+1)
    raw=ustar(sorted(members));need(len(raw)<=MAX_NODE_BYTES)
    encoded=lzma.compress(raw,format=lzma.FORMAT_XZ,check=lzma.CHECK_SHA256,
        filters=[dict(id=lzma.FILTER_LZMA2,preset=3,dict_size=64*1024*1024)])
    need(len(encoded)<=MAX_ENCODED_BYTES)
    files={f'history/stream-{i:04d}.xz.part':encoded[start:start+CHUNK_BYTES]
           for i,start in enumerate(range(0,len(encoded),CHUNK_BYTES))}
    record=dict(schema=SCHEMA,encoding='USTAR+XZ64+fixed-DAG-v1',
        history_index_sha256=sha(index_raw),tar_bytes=len(raw),tar_sha256=sha(raw),
        encoded_bytes=len(encoded),encoded_sha256=sha(encoded),dag_sha256=sha(document_raw),
        chunk_bytes=CHUNK_BYTES,chunks=[dict(name=name,bytes=len(value),sha256=sha(value)) for name,value in files.items()],
        original_files=len(document['originals']),codec_versions=document['codec_versions'],
        remote_decompression=False,global_historical_debt_complete=False,
        historical_unknown_actual_cost=None,refunds=0)
    verify(index_raw,record,files)
    return files,record


def _decode(index_raw,record,files):
    rows=checked_index(index_raw)
    need(type(record) is dict and set(record)=={'schema','encoding','history_index_sha256',
        'tar_bytes','tar_sha256','encoded_bytes','encoded_sha256','dag_sha256','chunk_bytes','chunks',
        'original_files','codec_versions','remote_decompression','global_historical_debt_complete',
        'historical_unknown_actual_cost','refunds'}
        and record['schema']==SCHEMA and record['encoding']=='USTAR+XZ64+fixed-DAG-v1'
        and record['history_index_sha256']==sha(index_raw)
        and type(record['original_files']) is int and record['original_files']==len(rows)
        and record['codec_versions']==codec_versions()
        and record['remote_decompression'] is False
        and record['global_historical_debt_complete'] is False
        and record['historical_unknown_actual_cost'] is None
        and type(record['refunds']) is int and record['refunds']==0
        and type(record['chunk_bytes']) is int and record['chunk_bytes']==CHUNK_BYTES)
    for key,cap in [('tar_bytes',MAX_NODE_BYTES),('encoded_bytes',MAX_ENCODED_BYTES)]:
        need(type(record[key]) is int and 0<record[key]<=cap)
    for key in ('tar_sha256','encoded_sha256','dag_sha256'):digest(record[key])
    count=(record['encoded_bytes']+CHUNK_BYTES-1)//CHUNK_BYTES
    names=[f'history/stream-{i:04d}.xz.part' for i in range(count)]
    need(type(files) is dict and set(files)==set(names) and type(record['chunks']) is list
        and len(record['chunks'])==count)
    for i,(name,row) in enumerate(zip(names,record['chunks'])):
        raw=files[name]
        need(type(row) is dict and set(row)=={'name','bytes','sha256'}
            and type(row['bytes']) is int and type(raw) is bytes
            and len(raw)==min(CHUNK_BYTES,record['encoded_bytes']-i*CHUNK_BYTES)
            and row==dict(name=name,bytes=len(raw),sha256=sha(raw)))
    encoded=b''.join(files[name] for name in names);need(sha(encoded)==record['encoded_sha256'])
    decoder=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=MEMLIMIT)
    raw=decoder.decompress(encoded,max_length=record['tar_bytes']+1)
    need(decoder.eof and not decoder.unused_data and len(raw)==record['tar_bytes'] and sha(raw)==record['tar_sha256'])
    # The fixed envelope recipe itself is part of the format, not merely a
    # choice of any XZ stream that decodes to equivalent bytes.
    need(encoded==lzma.compress(raw,format=lzma.FORMAT_XZ,check=lzma.CHECK_SHA256,
        filters=[dict(id=lzma.FILTER_LZMA2,preset=3,dict_size=64*1024*1024)]))
    blobs={};document_raw=None;names=set()
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for info in archive:
            checked_name(info.name)
            need(info.isfile() and info.name not in names and len(names)<MAX_NODES+1
                and 0<info.size<=MAX_STORED_BLOB and info.mode==0o600
                and info.uid==info.gid==info.mtime==0 and not info.uname and not info.gname
                and not info.pax_headers)
            names.add(info.name);payload=archive.extractfile(info).read(info.size+1);need(len(payload)==info.size)
            if info.name=='DAG.json':document_raw=payload
            else:
                key=info.name.removeprefix('blobs/').removesuffix('.bin');digest(key)
                need(info.name==f'blobs/{key}.bin' and sha(payload)==key and key not in blobs)
                blobs[key]=payload
    need(document_raw is not None and sha(document_raw)==record['dag_sha256'])
    document=parse(document_raw)
    need(type(document) is dict and set(document)=={'schema','nodes','originals','codec_versions'}
        and document['schema']==DAG_SCHEMA and document['codec_versions']==record['codec_versions']
        and document_raw==canonical(document))
    need(raw==ustar(sorted([('DAG.json',document_raw)]+
        [(f'blobs/{key}.bin',value) for key,value in blobs.items()])))
    originals=document['originals']
    need(type(originals) is list and len(originals)==len(rows))
    for expected,row in zip(rows,originals):
        need(type(row) is dict and set(row)=={'path','bytes','sha256','node'}
            and canonical({k:row[k] for k in expected})==canonical(expected));digest(row['node'])
    # Every declared node must contribute to an original. Reject padding with
    # unused literals or transforms before doing any reconstruction work.
    nodes=document['nodes'];need(type(nodes) is list and 1<=len(nodes)<=MAX_NODES)
    by_id={}
    for row in nodes:
        need(type(row) is dict and set(row)=={'id','node'} and type(row['id']) is str
             and type(row['node']) is dict and row['id'] not in by_id)
        by_id[row['id']]=row['node']
    reachable=set();pending=[row['node'] for row in originals]
    while pending:
        key=pending.pop();need(type(key) is str and key in by_id)
        if key in reachable:continue
        reachable.add(key);node=by_id[key];op=node.get('op')
        need(type(op) is str and op in ('literal','ustar','xz32','gzip9','slice'))
        if op=='ustar':
            members=node.get('members');need(type(members) is list and 1<=len(members)<=500)
            need(all(type(row) is dict and set(row)=={'name','node'} for row in members))
            pending.extend(row['node'] for row in members)
        elif op in ('xz32','gzip9','slice'):
            need(type(node.get('source')) is str);pending.append(node['source'])
    need(reachable==set(by_id))
    outputs,summary=reconstruct(document['nodes'],blobs)
    literals={row['node']['blob'] for row in document['nodes'] if row['node']['op']=='literal'}
    need(literals==set(blobs))
    for row in originals:
        need(row['node'] in outputs)
        value=outputs[row['node']];need(len(value)==row['bytes'] and sha(value)==row['sha256'])
    restored={row['path']:outputs[row['node']] for row in originals}
    summary=dict(files=len(rows),original_bytes=sum(row['bytes'] for row in rows),
        encoded_bytes=len(encoded),tar_bytes=len(raw),chunks=count,
        all_original_bytes_verified=True,**summary)
    return restored,summary


def verify(index_raw,record,files):
    """Validate the whole graph and all originals without accessing any path."""
    return _decode(index_raw,record,files)[1]


def decode(index_raw,record,files):
    """Return every exact original path -> bytes for independent local comparison.

    Names are labels only. This function never opens or creates those paths.
    The128MiB XZ memory/output limits are distinct from bounded DAG intermediate
    storage (512MiB aggregate outputs); neither is a whole-process RSS claim.
    """
    return _decode(index_raw,record,files)[0]


# The complete reviewed v1 implementation above is byte-for-byte preserved.
# Explicit v2 index closures use one combined DAG, never nested v1 decoders.
_pack_v1=pack
_verify_v1=verify
_decode_v1=decode
INDEX_V2='meshticulum.app-upgrade-local-history-index.v2'
DAG_V2='meshticulum.fixed-history-DAG.v2'
RECORD_V2='meshticulum.opaque-history-reconstruction.v2'
ENCODING_V2='USTAR+XZ64+fixed-DAG-v2'
MAX_INDEX_DOCUMENTS=8
MAX_INDEX_METADATA_BYTES=16_000_000
MAX_EXPANDED_FILES=2048
MAX_EXPANDED_BYTES=512*1024*1024


def _strict_row(row):
    need(type(row) is dict and set(row)=={'path','sha256','bytes'})
    path=row['path']
    need(type(path) is str and 0<len(path)<=4096 and path.startswith('/')
         and not path.startswith('//') and '\x00' not in path
         and str(PurePosixPath(path))==path and '..' not in PurePosixPath(path).parts
         and type(row['bytes']) is int and 0<row['bytes']<=MAX_STORED_BLOB)
    digest(row['sha256'])


def _index_parts(raw):
    need(type(raw) is bytes and 0<len(raw)<=MAX_INDEX_BYTES)
    value=parse(raw)
    need(type(value) is dict)
    if value.get('schema')=='meshticulum.app-upgrade-local-history-index.v1':
        rows=checked_index(raw);parents=[]
    else:
        need(value.get('schema')==INDEX_V2 and set(value)=={'schema','files','parents','pin_mismatches',
             'global_historical_debt_complete','historical_unknown_actual_cost','install_authority','rf_authority'})
        plain={key:item for key,item in value.items() if key!='parents'}
        plain['schema']='meshticulum.app-upgrade-local-history-index.v1'
        rows=checked_index(canonical(plain));parents=value['parents']
        need(type(parents) is list and 1<=len(parents)<=MAX_INDEX_DOCUMENTS)
    for row in rows:_strict_row(row)
    local={row['path']:row for row in rows}
    for row in parents:
        _strict_row(row)
        need(row['bytes']<=MAX_INDEX_BYTES and local.get(row['path'])==row)
    need([r['path'] for r in parents]==sorted({r['path'] for r in parents}))
    return rows,parents


def checked_closure(index_raw,read):
    """Resolve bounded declarative indices before reading any ordinary payload.

    The reader receives only exact declared parent path/hash pairs. During
    decoding it reads the verified in-memory original map, never the filesystem.
    """
    need(type(index_raw) is bytes and 0<len(index_raw)<=MAX_INDEX_BYTES)
    rows={};metadata={};documents={};active=set()
    metadata_bytes=len(index_raw);need(metadata_bytes<=MAX_INDEX_METADATA_BYTES)

    def visit(raw,depth):
        nonlocal metadata_bytes
        need(type(depth) is int and 1<=depth<=MAX_DEPTH)
        pin=sha(raw);need(pin not in active)
        if pin in documents:return
        need(len(documents)+len(active)<MAX_INDEX_DOCUMENTS)
        active.add(pin)
        local,parents=_index_parts(raw)
        for row in local:
            old=rows.get(row['path']);need(old is None or old==row)
            rows[row['path']]=row
            need(len(rows)<=MAX_EXPANDED_FILES)
        for parent in parents:
            need(parent['sha256'] not in active and depth<MAX_DEPTH)
            path=parent['path']
            if path not in metadata:
                # Reserve metadata/doc work before invoking the parent reader.
                need(metadata_bytes+parent['bytes']<=MAX_INDEX_METADATA_BYTES)
                if parent['sha256'] not in documents:
                    need(len(documents)+len(active)+1<=MAX_INDEX_DOCUMENTS)
                metadata_bytes+=parent['bytes']
                child=read(path,parent['sha256'])
                need(type(child) is bytes and len(child)==parent['bytes'] and sha(child)==parent['sha256'])
                metadata[path]=child
            child=metadata[path]
            need(len(child)==parent['bytes'] and sha(child)==parent['sha256'])
            visit(child,depth+1)
        active.remove(pin);documents[pin]=raw

    visit(index_raw,1)
    ordered=[rows[path] for path in sorted(rows)]
    expanded=sum(row['bytes'] for row in ordered)
    need(expanded<=MAX_EXPANDED_BYTES)
    return ordered,metadata,dict(index_documents=len(documents),index_metadata_bytes=metadata_bytes,
        direct_files=len(_index_parts(index_raw)[0]),expanded_files=len(ordered),expanded_bytes=expanded)


def _compression_bound(size):
    # Conservative allocation/work reservation, not a claimed compressed size.
    return size+size//3+1024*1024


class ClosureBuilder(Builder):
    """Reserve shared work before each operation; identical operations reuse it."""
    def __init__(self):
        super().__init__();self.generated=0;self.codec_input=0;self.codec_nodes=0
        self.depths={};self.operations={};self.regenerated={}

    def run(self,op,maximum,produce,**fields):
        signature=canonical(dict(op=op,**fields))
        if signature in self.operations:return self.operations[signature]
        deps=([r['node'] for r in fields['members']] if op=='ustar'
              else [fields['source']] if op in ('xz32','xz64','gzip9','slice') else [])
        need(all(key in self.depths for key in deps))
        depth=1+max((self.depths[key] for key in deps),default=0)
        need(type(maximum) is int and 0<maximum<=MAX_NODE_BYTES
             and len(self.nodes)+1<=MAX_NODES and depth<=MAX_DEPTH
             and self.generated+maximum<=MAX_GENERATED_BYTES)
        if op in ('xz32','xz64','gzip9'):
            size=len(self.values[fields['source']])
            need(self.codec_nodes+1<=MAX_CODEC_NODES and self.codec_input+size<=MAX_CODEC_INPUT_BYTES)
            self.codec_nodes+=1;self.codec_input+=size
        value=produce();need(type(value) is bytes and 0<len(value)<=maximum)
        key=super().add(op,value,**fields)
        need(key not in self.depths)
        self.generated+=len(value);self.depths[key]=depth;self.operations[signature]=key
        return key

    def literal(self,value):
        need(type(value) is bytes and 0<len(value)<=MAX_STORED_BLOB)
        pin=sha(value)
        key=self.run('literal',len(value),lambda:value,blob=pin)
        self.blobs[pin]=value;return key

    def payload(self,value):
        """Reuse only explicitly regenerated historical archive content."""
        need(type(value) is bytes and 0<len(value)<=MAX_STORED_BLOB)
        key=self.regenerated.get(sha(value))
        if key is not None:
            need(self.values[key]==value);return key
        return self.literal(value)

    def tar(self,members):
        rows=[dict(name=name,node=key) for name,key in sorted(members)]
        need(1<=len(rows)<=500 and len({r['name'] for r in rows})==len(rows))
        for row in rows:
            checked_name(row['name']);need(row['node'] in self.values)
            need(len(self.values[row['node']])<=MAX_STORED_BLOB)
        size=sum(512+((len(self.values[r['node']])+511)//512)*512 for r in rows)+1024
        size=((size+10239)//10240)*10240
        return self.run('ustar',size,lambda:ustar([(r['name'],self.values[r['node']])for r in rows]),members=rows)

    def slice(self,source,offset,size):
        need(type(offset) is int and type(size) is int and 0<=offset and 0<size<=MAX_STORED_BLOB
             and source in self.values and offset+size<=len(self.values[source]))
        return self.run('slice',size,lambda:self.values[source][offset:offset+size],source=source,offset=offset)

    def compress(self,op,source):
        need(op in ('xz32','xz64','gzip9') and source in self.values)
        raw=self.values[source];bound=_compression_bound(len(raw))
        produce=(lambda:lzma.compress(raw,format=lzma.FORMAT_XZ,check=lzma.CHECK_SHA256,
            filters=[dict(id=lzma.FILTER_LZMA2,preset=3,dict_size=(64 if op=='xz64' else 32)*1024*1024)])) if op in ('xz32','xz64') else lambda:frozen_gzip(raw)
        return self.run(op,bound,produce,source=source)



def make_closure_document(index_raw,read):
    rows,metadata,closure=checked_closure(index_raw,read)
    by_path={row['path']:row for row in rows};originals={}
    for row in rows:
        raw=metadata[row['path']] if row['path'] in metadata else read(row['path'],row['sha256'])
        need(type(raw) is bytes and len(raw)==row['bytes'] and sha(raw)==row['sha256'])
        originals[row['path']]=raw
    def unique_input(pin):
        values=[originals[row['path']] for row in rows if row['sha256']==pin]
        need(len(values)==1);return values[0]
    # Historical archive reconstruction is activated only by the exact reviewed
    # anchor pair. Generic closures use literal nodes, with the same bounds.
    anchors={row['sha256'] for row in rows}
    if NESTED_INDEX_SHA not in anchors and ARCHIVE_MAP_SHA not in anchors:
        b=ClosureBuilder()
        original_nodes={path:b.literal(raw) for path,raw in originals.items()}
        document=dict(schema=DAG_V2,nodes=b.nodes,
            originals=[dict(row,node=original_nodes[row['path']]) for row in rows],
            codec_versions=codec_versions())
        return document,b.blobs,closure
    need(NESTED_INDEX_SHA in anchors and ARCHIVE_MAP_SHA in anchors)
    nested=parse(unique_input(NESTED_INDEX_SHA))
    mapping=parse(unique_input(ARCHIVE_MAP_SHA))
    need(type(mapping) is dict and set(mapping)=={'schema','source_only','rf_authorized',
        'upgrade_deployment','original_archives_unchanged','units','global_historical_debt_complete',
        'historical_unknown_actual_cost','refunds'}
        and mapping['schema']=='meshticulum.local-lossless-archive-history.v1'
        and mapping['source_only'] is True and mapping['rf_authorized'] is False
        and mapping['original_archives_unchanged'] is True
        and mapping['upgrade_deployment']=='c68a9c0f78c0411ebe91ccbc053ce499'
        and mapping['global_historical_debt_complete'] is False
        and mapping['historical_unknown_actual_cost'] is None
        and type(mapping['refunds']) is int and mapping['refunds']==0
        and type(mapping['units']) is dict and set(mapping['units'])=={'unit-a','unit-b'})
    piece_paths=[]
    for row in mapping['units'].values():
        need(type(row) is dict and set(row)=={'path','bytes','sha256','chunks'}
            and type(row['bytes']) is int and 0<row['bytes']<=MAX_ENCODED_BYTES
            and type(row['chunks']) is list and len(row['chunks'])==4)
        digest(row['sha256'])
        for piece in row['chunks']:
            need(type(piece) is dict and set(piece)=={'path','bytes','sha256'}
                and piece['path'] in by_path and piece==by_path[piece['path']]
                and piece['bytes']<=CHUNK_BYTES)
            piece_paths.append(piece['path'])
    need(len(set(piece_paths))==8)
    header_mapping=checked_header_mapping(rows,originals)
    header_pieces={piece['path'] for unit in header_mapping.values() for piece in unit['chunks']}
    b=ClosureBuilder();original_nodes={}
    rebuild_mnr_archives(b,rows,originals)
    for path,raw in originals.items():
        if path not in piece_paths and path not in header_pieces:original_nodes[path]=b.payload(raw)
    nested_rows=nested['files']
    need(type(nested_rows) is list and len(nested_rows)==264)
    for row in nested_rows:need(row['path'] in by_path and row==by_path[row['path']])
    old_tar=b.tar([(f'{i:04d}.bin',original_nodes[row['path']]) for i,row in enumerate(nested_rows)])
    old_xz=b.compress('xz32',old_tar);old_encoded=b.values[old_xz]
    need(sha(old_encoded)==NESTED_XZ_SHA and len(old_encoded)<=MAX_ENCODED_BYTES)
    old_parts={f'history/stream-{i:04d}.xz.part':b.slice(old_xz,start,min(CHUNK_BYTES,len(old_encoded)-start))
        for i,start in enumerate(range(0,len(old_encoded),CHUNK_BYTES))}
    for _,row in sorted(mapping['units'].items()):
        archive_raw=b''.join(originals[piece['path']] for piece in row['chunks'])
        need(len(archive_raw)==row['bytes'] and sha(archive_raw)==row['sha256'])
        tar_raw=ungzip_bounded(archive_raw);members=[];names=set()
        with tarfile.open(fileobj=io.BytesIO(tar_raw),mode='r:') as archive:
            for info in archive:
                checked_name(info.name)
                need(info.isfile() and info.name not in names and len(names)<1024
                    and 0<info.size<=MAX_STORED_BLOB and info.mode==0o600
                    and info.uid==info.gid==info.mtime==0 and not info.uname and not info.gname
                    and not info.pax_headers)
                names.add(info.name);payload=archive.extractfile(info).read(info.size+1)
                need(len(payload)==info.size)
                key=old_parts.get(info.name)
                if key is not None:need(b.values[key]==payload)
                else:key=b.payload(payload)
                members.append((info.name,key))
        need(len(members)==94 and set(old_parts)<=names)
        tar_node=b.tar(members);need(b.values[tar_node]==tar_raw)
        archive_node=b.compress('gzip9',tar_node);rebuilt=b.values[archive_node]
        need(rebuilt==archive_raw);offset=0
        for piece in row['chunks']:
            key=b.slice(archive_node,offset,piece['bytes']);offset+=piece['bytes']
            need(b.values[key]==originals[piece['path']]);original_nodes[piece['path']]=key
        need(offset==len(rebuilt))
    rebuild_header_archives(b,header_mapping,originals,original_nodes)
    need(set(original_nodes)==set(originals) and len(b.nodes)<=MAX_NODES)
    document=dict(schema=DAG_V2,nodes=b.nodes,
        originals=[dict(row,node=original_nodes[row['path']]) for row in rows],
        codec_versions=codec_versions())
    return document,b.blobs,closure


def _decode_closure(index_raw,record,files):
    _index_parts(index_raw)
    need(type(record) is dict and set(record)=={'schema','encoding','history_index_sha256',
        'tar_bytes','tar_sha256','encoded_bytes','encoded_sha256','dag_sha256','chunk_bytes','chunks',
        'original_files','codec_versions','remote_decompression','global_historical_debt_complete',
        'historical_unknown_actual_cost','refunds'}
        and record['schema']==RECORD_V2 and record['encoding']==ENCODING_V2
        and record['history_index_sha256']==sha(index_raw)
        and type(record['original_files']) is int and 1<=record['original_files']<=MAX_EXPANDED_FILES
        and record['codec_versions']==codec_versions()
        and record['remote_decompression'] is False
        and record['global_historical_debt_complete'] is False
        and record['historical_unknown_actual_cost'] is None
        and type(record['refunds']) is int and record['refunds']==0
        and type(record['chunk_bytes']) is int and record['chunk_bytes']==CHUNK_BYTES)
    for key,cap in [('tar_bytes',MAX_NODE_BYTES),('encoded_bytes',MAX_ENCODED_BYTES)]:
        need(type(record[key]) is int and 0<record[key]<=cap)
    for key in ('tar_sha256','encoded_sha256','dag_sha256'):digest(record[key])
    count=(record['encoded_bytes']+CHUNK_BYTES-1)//CHUNK_BYTES
    names=[f'history/stream-{i:04d}.xz.part' for i in range(count)]
    need(type(files) is dict and set(files)==set(names) and type(record['chunks']) is list
        and len(record['chunks'])==count)
    for i,(name,row) in enumerate(zip(names,record['chunks'])):
        raw=files[name]
        need(type(row) is dict and set(row)=={'name','bytes','sha256'}
            and type(row['bytes']) is int and type(raw) is bytes
            and len(raw)==min(CHUNK_BYTES,record['encoded_bytes']-i*CHUNK_BYTES)
            and row==dict(name=name,bytes=len(raw),sha256=sha(raw)))
    encoded=b''.join(files[name] for name in names);need(sha(encoded)==record['encoded_sha256'])
    decoder=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=MEMLIMIT)
    raw=decoder.decompress(encoded,max_length=record['tar_bytes']+1)
    need(decoder.eof and not decoder.unused_data and len(raw)==record['tar_bytes'] and sha(raw)==record['tar_sha256'])
    # The fixed envelope recipe itself is part of the format, not merely a
    # choice of any XZ stream that decodes to equivalent bytes.
    need(encoded==lzma.compress(raw,format=lzma.FORMAT_XZ,check=lzma.CHECK_SHA256,
        filters=[dict(id=lzma.FILTER_LZMA2,preset=3,dict_size=64*1024*1024)]))
    blobs={};document_raw=None;names=set()
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for info in archive:
            checked_name(info.name)
            need(info.isfile() and info.name not in names and len(names)<MAX_NODES+1
                and 0<info.size<=MAX_STORED_BLOB and info.mode==0o600
                and info.uid==info.gid==info.mtime==0 and not info.uname and not info.gname
                and not info.pax_headers)
            names.add(info.name);payload=archive.extractfile(info).read(info.size+1);need(len(payload)==info.size)
            if info.name=='DAG.json':document_raw=payload
            else:
                key=info.name.removeprefix('blobs/').removesuffix('.bin');digest(key)
                need(info.name==f'blobs/{key}.bin' and sha(payload)==key and key not in blobs)
                blobs[key]=payload
    need(document_raw is not None and sha(document_raw)==record['dag_sha256'])
    document=parse(document_raw)
    need(type(document) is dict and set(document)=={'schema','nodes','originals','codec_versions'}
        and document['schema']==DAG_V2 and document['codec_versions']==record['codec_versions']
        and document_raw==canonical(document))
    need(raw==ustar(sorted([('DAG.json',document_raw)]+
        [(f'blobs/{key}.bin',value) for key,value in blobs.items()])))
    originals=document['originals']
    need(type(originals) is list and len(originals)==record['original_files'])
    original_table={}
    for row in originals:
        need(type(row) is dict and set(row)=={'path','bytes','sha256','node'})
        _strict_row({key:row[key] for key in ('path','bytes','sha256')});digest(row['node'])
        need(row['path'] not in original_table);original_table[row['path']]=row
    need(list(original_table)==sorted(original_table))
    # Every declared node must contribute to an original. Reject padding with
    # unused literals or transforms before doing any reconstruction work.
    nodes=document['nodes'];need(type(nodes) is list and 1<=len(nodes)<=MAX_NODES)
    by_id={}
    for row in nodes:
        need(type(row) is dict and set(row)=={'id','node'} and type(row['id']) is str
             and type(row['node']) is dict and row['id'] not in by_id)
        by_id[row['id']]=row['node']
    validate_closure_nodes(nodes,blobs)
    reachable=set();pending=[row['node'] for row in originals]
    while pending:
        key=pending.pop();need(type(key) is str and key in by_id)
        if key in reachable:continue
        reachable.add(key);node=by_id[key];op=node.get('op')
        need(type(op) is str and op in ('literal','ustar','xz32','xz64','gzip9','slice'))
        if op=='ustar':
            members=node.get('members');need(type(members) is list and 1<=len(members)<=500)
            need(all(type(row) is dict and set(row)=={'name','node'} for row in members))
            pending.extend(row['node'] for row in members)
        elif op in ('xz32','xz64','gzip9','slice'):
            need(type(node.get('source')) is str);pending.append(node['source'])
    need(reachable==set(by_id))
    # Parent documents must be literal originals. Resolve their small bounded
    # metadata before materializing any transforms; no filesystem is available.
    def parent_read(path,pin):
        need(path in original_table)
        row=original_table[path];need(row['sha256']==pin and row['node'] in by_id)
        node=by_id[row['node']]
        need(set(node)=={'op','bytes','sha256','blob'} and node['op']=='literal'
             and node['sha256']==pin and node['bytes']==row['bytes']
             and node['blob']==pin and pin in blobs)
        return blobs[pin]
    rows,metadata,closure=checked_closure(index_raw,parent_read)
    need(len(rows)==len(originals))
    for expected,row in zip(rows,originals):
        need({key:row[key] for key in expected}==expected)
    literals={row['node']['blob'] for row in document['nodes'] if row['node']['op']=='literal'}
    need(literals==set(blobs))
    outputs,summary=reconstruct_closure(document['nodes'],blobs)
    for row in originals:
        need(row['node'] in outputs)
        value=outputs[row['node']];need(len(value)==row['bytes'] and sha(value)==row['sha256'])
    restored={row['path']:outputs[row['node']] for row in originals}
    checked_rows,_,checked_stats=checked_closure(index_raw,lambda path,pin:restored[path])
    need(checked_rows==rows and checked_stats==closure)
    summary=dict(**closure,files=len(rows),original_bytes=sum(row['bytes'] for row in rows),
        encoded_bytes=len(encoded),tar_bytes=len(raw),chunks=count,
        all_original_bytes_verified=True,**summary)
    return restored,summary


def validate_closure_nodes(nodes,blobs):
    """Validate the entire topological graph and reserve work before transforms."""
    need(type(nodes) is list and 1<=len(nodes)<=MAX_NODES and type(blobs) is dict)
    sizes={};depths={};generated=0;codec_input=0;codec_nodes=0
    fields={'literal':{'blob'},'ustar':{'members'},'xz32':{'source'},'xz64':{'source'},
            'gzip9':{'source'},'slice':{'source','offset'}}
    for row in nodes:
        need(type(row) is dict and set(row)=={'id','node'} and type(row['node']) is dict)
        key=row['id'];digest(key);node=row['node'];op=node.get('op')
        need(key==sha(canonical(node)) and key not in sizes and type(op) is str and op in fields
             and set(node)=={'op','bytes','sha256'}|fields[op]
             and type(node['bytes']) is int and 0<node['bytes']<=MAX_NODE_BYTES)
        digest(node['sha256']);deps=[];maximum=node['bytes']
        if op=='literal':
            digest(node['blob'])
            need(node['blob'] in blobs and node['bytes']<=MAX_STORED_BLOB)
            value=blobs[node['blob']]
            need(type(value) is bytes and len(value)==node['bytes']
                 and sha(value)==node['blob']==node['sha256'])
        elif op=='ustar':
            members=node['members'];need(type(members) is list and 1<=len(members)<=500)
            names=[];maximum=1024
            for item in members:
                need(type(item) is dict and set(item)=={'name','node'})
                checked_name(item['name']);digest(item['node'])
                need(item['node'] in sizes and sizes[item['node']]<=MAX_STORED_BLOB)
                names.append(item['name']);deps.append(item['node'])
                maximum+=512+((sizes[item['node']]+511)//512)*512
            need(names==sorted(set(names)))
            maximum=((maximum+10239)//10240)*10240
            need(node['bytes']==maximum)
        else:
            digest(node['source']);need(node['source'] in sizes);deps=[node['source']]
            if op=='slice':
                need(type(node['offset']) is int and 0<=node['offset']
                     and node['bytes']<=MAX_STORED_BLOB
                     and node['offset']+node['bytes']<=sizes[node['source']])
            else:
                maximum=_compression_bound(sizes[node['source']])
                codec_nodes+=1;codec_input+=sizes[node['source']]
                need(codec_nodes<=MAX_CODEC_NODES and codec_input<=MAX_CODEC_INPUT_BYTES)
        depth=1+max((depths[dep] for dep in deps),default=0)
        need(depth<=MAX_DEPTH and node['bytes']<=maximum<=MAX_NODE_BYTES
             and generated+maximum<=MAX_GENERATED_BYTES)
        generated+=node['bytes'];sizes[key]=node['bytes'];depths[key]=depth
    return dict(nodes=len(nodes),maximum_depth=max(depths.values()),
        generated_intermediate_bytes=generated,stored_blob_bytes=sum(map(len,blobs.values())),
        codec_nodes=codec_nodes,codec_input_bytes=codec_input)


def reconstruct_closure(nodes,blobs):
    summary=validate_closure_nodes(nodes,blobs);outputs={}
    for row in nodes:outputs[row['id']]=closure_node_bytes(row['node'],outputs,blobs)
    return outputs,summary


def _pack_closure(index_raw,read):
    document,blobs,closure=make_closure_document(index_raw,read)
    document_raw=canonical(document);need(len(document_raw)<=MAX_STORED_BLOB)
    members=sorted([('DAG.json',document_raw)]+
        [(f'blobs/{pin}.bin',raw) for pin,raw in blobs.items()])
    need(len(members)<=MAX_NODES+1)
    size=sum(512+((len(raw)+511)//512)*512 for _,raw in members)+1024
    size=((size+10239)//10240)*10240
    need(size<=MAX_NODE_BYTES and _compression_bound(size)<=MAX_GENERATED_BYTES)
    raw=ustar(members);need(len(raw)==size)
    encoded=lzma.compress(raw,format=lzma.FORMAT_XZ,check=lzma.CHECK_SHA256,
        filters=[dict(id=lzma.FILTER_LZMA2,preset=3,dict_size=64*1024*1024)])
    need(0<len(encoded)<=MAX_ENCODED_BYTES)
    files={f'history/stream-{i:04d}.xz.part':encoded[start:start+CHUNK_BYTES]
        for i,start in enumerate(range(0,len(encoded),CHUNK_BYTES))}
    record=dict(schema=RECORD_V2,encoding=ENCODING_V2,
        history_index_sha256=sha(index_raw),tar_bytes=len(raw),tar_sha256=sha(raw),
        encoded_bytes=len(encoded),encoded_sha256=sha(encoded),dag_sha256=sha(document_raw),
        chunk_bytes=CHUNK_BYTES,chunks=[dict(name=name,bytes=len(value),sha256=sha(value)) for name,value in files.items()],
        original_files=closure['expanded_files'],codec_versions=document['codec_versions'],
        remote_decompression=False,global_historical_debt_complete=False,
        historical_unknown_actual_cost=None,refunds=0)
    _decode_closure(index_raw,record,files)
    return files,record


def pack(index_raw,read):
    """Build one bounded graph for an explicit v2 closure, or unchanged v1."""
    need(type(index_raw) is bytes and 0<len(index_raw)<=MAX_INDEX_BYTES)
    value=parse(index_raw);need(type(value) is dict)
    if value.get('schema')==INDEX_V2:return _pack_closure(index_raw,read)
    return _pack_v1(index_raw,read)


def verify(index_raw,record,files):
    """Verify every original without reading external paths."""
    need(type(index_raw) is bytes and 0<len(index_raw)<=MAX_INDEX_BYTES)
    value=parse(index_raw);need(type(value) is dict)
    if value.get('schema')==INDEX_V2:return _decode_closure(index_raw,record,files)[1]
    return _verify_v1(index_raw,record,files)


def decode(index_raw,record,files):
    """Return exact expanded originals; no external filesystem access."""
    need(type(index_raw) is bytes and 0<len(index_raw)<=MAX_INDEX_BYTES)
    value=parse(index_raw);need(type(value) is dict)
    if value.get('schema')==INDEX_V2:return _decode_closure(index_raw,record,files)[0]
    return _decode_v1(index_raw,record,files)


HEADER_MAP_SHA='ce5b3665aed682b339113d964ed808638e589c8880689d2a0019c0b4dae76f23'
HEADER_INNER_SHA='f09147c8452d50c232ed9aa9146ebc45f7c17fbc0aeef0b6a9656d3d09e45e94'
HEADER_INNER_TAR_SHA='edc8dfe9a27954125a4084cbd71d76d0afb9f239abbb9fbe27c45db56d23c3c3'
HEADER_ARCHIVES={
    'unit-a':(15663042,'32cd8b6705fda4f0b108c11b9bf16b6b65f283b73bf851af7ea82ff91546dc46'),
    'unit-b':(15663045,'c5c697f0e96ed067bc691e48320a5a9e1ee65826b0589975f1d8fcf5b447d94a')}


def checked_header_mapping(rows,originals):
    candidates=[row for row in rows if row['sha256']==HEADER_MAP_SHA]
    if not candidates:return {}
    need(len(candidates)==1)
    value=parse(originals[candidates[0]['path']]);by_path={row['path']:row for row in rows}
    need(type(value) is dict and set(value)=={'schema','source_only','rf_authorized',
        'upgrade_deployment','original_archives_unchanged','units','global_historical_debt_complete',
        'historical_unknown_actual_cost','refunds'}
        and value['schema']=='meshticulum.local-lossless-archive-history.v1'
        and value['source_only'] is True and value['rf_authorized'] is False
        and value['original_archives_unchanged'] is True
        and value['upgrade_deployment']=='b79ee563463042d28942df1c7d5809ab'
        and value['global_historical_debt_complete'] is False
        and value['historical_unknown_actual_cost'] is None
        and type(value['refunds']) is int and value['refunds']==0
        and type(value['units']) is dict and set(value['units'])==set(HEADER_ARCHIVES))
    pieces=[]
    for unit,row in value['units'].items():
        need(type(row) is dict and set(row)=={'path','bytes','sha256','chunks'}
             and type(row['bytes']) is int and (row['bytes'],row['sha256'])==HEADER_ARCHIVES[unit]
             and type(row['chunks']) is list and len(row['chunks'])==4)
        _strict_row({key:row[key] for key in ('path','sha256','bytes')} | {'bytes':1})
        for piece in row['chunks']:
            _strict_row(piece)
            need(by_path.get(piece['path'])==piece and piece['bytes']<=CHUNK_BYTES)
            pieces.append(piece['path'])
        need(sum(piece['bytes'] for piece in row['chunks'])==row['bytes'])
    need(len(set(pieces))==8)
    return value['units']


def _regular_tar_members(raw,count_limit):
    need(type(raw) is bytes and 0<len(raw)<=MAX_NODE_BYTES
         and type(count_limit) is int and 0<count_limit<=500)
    result={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for info in archive:
            checked_name(info.name)
            need(info.isfile() and info.name not in result and len(result)<count_limit
                and 0<info.size<=MAX_STORED_BLOB and info.mode==0o600
                and info.uid==info.gid==info.mtime==0 and not info.uname and not info.gname
                and not info.pax_headers)
            payload=archive.extractfile(info).read(info.size+1);need(len(payload)==info.size)
            result[info.name]=payload
    need(raw==ustar(sorted(result.items())))
    return result


def rebuild_header_archives(b,mapping,originals,original_nodes):
    shared_inner=None;shared_parts=None
    for unit,row in sorted(mapping.items()):
        archived=b''.join(originals[piece['path']] for piece in row['chunks'])
        need((len(archived),sha(archived))==HEADER_ARCHIVES[unit])
        raw=ungzip_bounded(archived);members=_regular_tar_members(raw,95);need(len(members)==95)
        need('history/ENCODING.json' in members)
        record=parse(members['history/ENCODING.json'])
        need(type(record) is dict and record.get('schema')==SCHEMA
             and record.get('encoding')=='USTAR+XZ64+fixed-DAG-v1'
             and type(record.get('encoded_bytes')) is int and record['encoded_bytes']==14056876
             and type(record.get('tar_bytes')) is int and record['tar_bytes']==65443840
             and record.get('encoded_sha256')==HEADER_INNER_SHA
             and record.get('tar_sha256')==HEADER_INNER_TAR_SHA
             and type(record.get('chunks')) is list and len(record['chunks'])==4)
        expected_names=[f'history/stream-{i:04d}.xz.part' for i in range(4)]
        for name,piece in zip(expected_names,record['chunks']):
            need(type(piece) is dict and set(piece)=={'name','bytes','sha256'}
                 and type(piece['bytes']) is int and name in members
                 and piece==dict(name=name,bytes=len(members[name]),sha256=sha(members[name])))
        encoded=b''.join(members[name] for name in expected_names)
        need(len(encoded)==14056876 and sha(encoded)==HEADER_INNER_SHA)
        if shared_inner is None:
            decoder=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=MEMLIMIT)
            inner=decoder.decompress(encoded,max_length=65443840+1)
            need(decoder.eof and not decoder.unused_data and len(inner)==65443840
                 and sha(inner)==HEADER_INNER_TAR_SHA)
            # Retained DAG.json is a literal byte string, not a nested program.
            inner_members=_regular_tar_members(inner,472);need(len(inner_members)==472)
            need('DAG.json' in inner_members)
            inner_tar=b.tar([(name,b.payload(payload)) for name,payload in inner_members.items()])
            need(b.values[inner_tar]==inner)
            inner_xz=b.compress('xz64',inner_tar);need(b.values[inner_xz]==encoded)
            shared_inner=encoded
            shared_parts={name:b.slice(inner_xz,i*CHUNK_BYTES,len(members[name]))
                for i,name in enumerate(expected_names)}
        else:need(encoded==shared_inner)
        tar_node=b.tar([(name,shared_parts[name] if name in shared_parts else b.payload(payload))
            for name,payload in members.items()])
        need(b.values[tar_node]==raw)
        gzip_node=b.compress('gzip9',tar_node);need(b.values[gzip_node]==archived)
        offset=0
        for piece in row['chunks']:
            key=b.slice(gzip_node,offset,piece['bytes']);offset+=piece['bytes']
            need(b.values[key]==originals[piece['path']]);original_nodes[piece['path']]=key
        need(offset==len(archived))


def closure_node_bytes(node,outputs,blobs):
    if node['op']!='xz64':return node_bytes(node,outputs,blobs)
    # Called only after validate_closure_nodes has checked exact shape, source,
    # conservative allocation and all global budgets. No configurable filters.
    raw=lzma.compress(outputs[node['source']],format=lzma.FORMAT_XZ,check=lzma.CHECK_SHA256,
        filters=[dict(id=lzma.FILTER_LZMA2,preset=3,dict_size=64*1024*1024)])
    need(len(raw)==node['bytes'] and sha(raw)==node['sha256'])
    return raw


MNR_ARCHIVES={
    'cacd41cced449c7173be00806e9804c2b1dd52432e1b5ea9e81136f8d35dcc59':
        '8f4e6001a27010d05418f4de856163cd79f6ca3be94eb8cb8575858bdcd03a16',
    '746dc1b0dc5899d68c66d68a42ab108f83528fdb04e97591349faf8ac8e0fb64':
        '63cb7fefae82d7fd6392ee8fa4356c97d06e326c99bf8102fcd3bf05c98cb562'}


def rebuild_mnr_archives(b,rows,originals):
    available={row['sha256'] for row in rows}
    if not (set(MNR_ARCHIVES)&available):return
    need(set(MNR_ARCHIVES)<=available)
    for pin,tar_pin in sorted(MNR_ARCHIVES.items()):
        matches=[row for row in rows if row['sha256']==pin];need(len(matches)==1)
        archived=originals[matches[0]['path']]
        need(len(archived)==6590946 and sha(archived)==pin)
        decoder=zlib.decompressobj(31)
        raw=decoder.decompress(archived,32215040+1)
        need(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
            and len(raw)==32215040 and len(raw)<=MAX_NODE_BYTES and sha(raw)==tar_pin)
        members=_regular_tar_members(raw,279)
        need(len(members)==279 and sum(map(len,members.values()))==31992196
            and sum(map(len,members.values()))<=32_000_000)
        tar_node=b.tar([(name,b.literal(payload)) for name,payload in members.items()])
        need(b.values[tar_node]==raw)
        key=b.compress('gzip9',tar_node);need(b.values[key]==archived)
        b.regenerated[pin]=key
