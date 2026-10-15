"""Bounded synthetic format controls; no installation or native authority."""
import io
import struct
import zipfile
import zlib

import pytest

from tests.test_fast_bundle_archive import archive,canonical,runner,sha
from tests.test_lantern_meshticulum_bench_runtime import helper


def fixture_sources():
    return {**{f'lantern_meshticulum/bench/synthetic{i}.py':b'# public source control\n'*10 for i in range(72)},
        'bench_node.py':b'# entry\n','LICENSE':b'public synthetic notice\n',
        'lantern_meshticulum/bench/pilot_resume_rpc.py':b'# adapter\n'}


def image_fixture(sources=None):
    sources=fixture_sources() if sources is None else sources
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=False) as image:
        for name,data in sorted(sources.items()):
            row=zipfile.ZipInfo(name,(1980,1,1,0,0,0));row.create_system=3
            row.compress_type=zipfile.ZIP_DEFLATED;row.external_attr=0o100600<<16
            image.writestr(row,data,compresslevel=6)
    raw=stream.getvalue();loader=b'# source-only loader placeholder\n'
    physical={name:data for name,data in sources.items() if not name.startswith(('meshticulum/','lantern_meshticulum/'))}
    physical.update({'execution.zip':raw,'execution_runtime.py':loader})
    pins={name:sha(data) for name,data in sources.items()}
    manifest=dict(schema='meshticulum.bench-runtime-bundle.v2',private_services_enabled=False,
        public_parent_claims_required=True,files={name:sha(data) for name,data in physical.items()},source_files=pins,
        execution_layout=dict(schema='sage-lantern.sealed-source-image.v2',image='execution.zip',bytes=len(raw),
            sha256=sha(raw),loader='execution_runtime.py',loader_sha256=sha(loader),
            source_files_sha256=sha(canonical(pins)),expanded_bytes=sum(map(len,sources.values()))))
    return sources,physical,manifest


def decode(which,runner,physical,manifest):
    if which=='archive':
        raw,pin=archive(physical,manifest)
        return runner.archive_source_members(raw,pin)[1]
    return helper(which).image_members(physical['execution.zip'],manifest)


PARSERS=('execution_runtime','runtime_bootstrap','archive')


@pytest.mark.parametrize('which',PARSERS)
def test_explicit_deflated_layout_decodes_the_exact_source_map(runner,which):
    sources,physical,manifest=image_fixture()
    assert decode(which,runner,physical,manifest)==sources
    assert len(physical['execution.zip'])<sum(map(len,sources.values()))


@pytest.mark.parametrize('which',PARSERS)
@pytest.mark.parametrize('fault',['unversioned','unknown_version','expanded_mismatch','expanded_bool','expanded_limit',
    'local_crc','crc','local_flags','central_flags','encryption','zip64_version','local_extra','central_extra','member_comment',
    'reserved','method','offset','filename','permissions',
    'member_size','directory_size','directory_start','count','comment','trailer','prefix'])
def test_all_three_readers_refuse_noncanonical_or_unbounded_images(runner,which,fault):
    _,physical,manifest=image_fixture();raw=bytearray(physical['execution.zip']);end=len(raw)-22
    central=struct.unpack_from('<I',raw,end+16)[0]
    if fault=='unversioned':
        manifest['execution_layout']['schema']='sage-lantern.sealed-source-image.v1'
        manifest['execution_layout'].pop('expanded_bytes')
    elif fault=='unknown_version':manifest['execution_layout']['schema']='sage-lantern.sealed-source-image.v3'
    elif fault=='expanded_mismatch':manifest['execution_layout']['expanded_bytes']+=1
    elif fault=='expanded_bool':manifest['execution_layout']['expanded_bytes']=True
    elif fault=='expanded_limit':manifest['execution_layout']['expanded_bytes']=2250001
    elif fault=='local_crc':struct.pack_into('<I',raw,14,0)
    elif fault=='crc':
        struct.pack_into('<I',raw,14,0);struct.pack_into('<I',raw,central+16,0)
    elif fault=='local_flags':struct.pack_into('<H',raw,6,8)
    elif fault=='central_flags':struct.pack_into('<H',raw,central+8,8)
    elif fault=='encryption':
        struct.pack_into('<H',raw,6,1);struct.pack_into('<H',raw,central+8,1)
    elif fault=='zip64_version':
        struct.pack_into('<H',raw,4,45);struct.pack_into('<H',raw,central+6,45)
    elif fault=='local_extra':struct.pack_into('<H',raw,28,1)
    elif fault=='central_extra':struct.pack_into('<H',raw,central+30,1)
    elif fault=='member_comment':struct.pack_into('<H',raw,central+32,1)
    elif fault=='reserved':raw[central+7]=1
    elif fault=='method':
        struct.pack_into('<H',raw,8,0);struct.pack_into('<H',raw,central+10,0)
    elif fault=='offset':struct.pack_into('<I',raw,central+42,1)
    elif fault=='filename':raw[30]^=1
    elif fault=='permissions':struct.pack_into('<I',raw,central+38,0o100666<<16)
    elif fault=='member_size':
        struct.pack_into('<I',raw,22,500001);struct.pack_into('<I',raw,central+24,500001)
    elif fault=='directory_size':struct.pack_into('<I',raw,end+12,1)
    elif fault=='directory_start':struct.pack_into('<I',raw,end+16,central-1)
    elif fault=='count':struct.pack_into('<H',raw,end+10,76)
    elif fault=='comment':struct.pack_into('<H',raw,end+20,1)
    elif fault=='trailer':raw.extend(b'x')
    else:raw[:0]=b'x'
    physical['execution.zip']=bytes(raw);manifest['files']['execution.zip']=sha(raw)
    manifest['execution_layout'].update(bytes=len(raw),sha256=sha(raw))
    with pytest.raises((RuntimeError,zipfile.BadZipFile,NotImplementedError,UnicodeError,struct.error,zlib.error)):
        decode(which,runner,physical,manifest)


@pytest.mark.parametrize('which',PARSERS)
def test_sum_of_expanded_members_is_bounded_before_decode(runner,which):
    sources=fixture_sources()
    for name in list(sources)[:5]:sources[name]=b'#'+b'x'*449998+b'\n'
    _,physical,manifest=image_fixture(sources)
    assert len(physical['execution.zip'])<100000 and sum(map(len,sources.values()))>2250000
    manifest['execution_layout']['expanded_bytes']=2250000
    with pytest.raises(RuntimeError):decode(which,runner,physical,manifest)


def single_member(data,stream,*,declared=None,crc=None):
    row=zipfile.ZipInfo('control.py',(1980,1,1,0,0,0))
    row.create_system=3;row.create_version=row.extract_version=20;row.reserved=0
    row.external_attr=0o100600<<16;row.compress_type=zipfile.ZIP_DEFLATED;row.header_offset=0
    row.file_size=len(data) if declared is None else declared;row.compress_size=len(stream)
    row.CRC=zlib.crc32(data) if crc is None else crc
    name=row.filename.encode('ascii')
    raw=struct.pack('<4s5H3I2H',b'PK\x03\x04',20,0,8,0,33,row.CRC,row.compress_size,row.file_size,len(name),0)+name+stream
    return raw,row


@pytest.mark.parametrize('which',('execution_runtime','runtime_bootstrap','run_public_gfsk250_mb1_fast_bundle100k'))
@pytest.mark.parametrize('fault',['truncated','tail','concatenated','short_size','long_size','crc','overlap'])
def test_raw_inflater_requires_exact_bounded_output_and_stream_end(monkeypatch,which,fault):
    # Only a small local decoder record is exercised; no source is executed.
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).parents[3]/'firmware/meshticulum-bench'))
    data=b'# bounded synthetic source\n'*100
    encoder=zlib.compressobj(6,wbits=-15);stream=encoder.compress(data)+encoder.flush()
    declared=None;crc=None
    if fault=='truncated':stream=stream[:-1]
    elif fault=='tail':stream+=b'x'
    elif fault=='concatenated':stream+=stream
    elif fault=='short_size':declared=len(data)-1
    elif fault=='long_size':declared=len(data)+1
    elif fault=='crc':crc=0
    raw,row=single_member(data,stream,declared=declared,crc=crc)
    with pytest.raises((RuntimeError,zlib.error)):
        helper(which).deflated_member(raw,row,0,len(raw)-(fault=='overlap'))


@pytest.mark.parametrize('data',[b'',b'# ordinary source\n'])
def test_empty_and_nonempty_raw_deflate_members_have_exact_boundaries(data):
    encoder=zlib.compressobj(6,wbits=-15);stream=encoder.compress(data)+encoder.flush()
    raw,row=single_member(data,stream)
    assert helper('execution_runtime').deflated_member(raw,row,0,len(raw))==(data,len(raw))
