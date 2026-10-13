"""Source-only archive imports and mutation controls; no native or device calls."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

import pytest

from tests.test_lantern_meshticulum_bench_runtime import helper


@pytest.fixture(scope='module',params=['sealed-image','sealed-image-deflated'])
def packaged_image(request):
    root = Path(__file__).resolve().parents[3]
    anchor = Path(tempfile.mkdtemp(prefix='sealed-image-', dir=Path(__file__).parent)); anchor.chmod(0o700)
    output = anchor / 'output'
    result = subprocess.run([sys.executable, str(root / 'firmware/meshticulum-bench/build_runtime.py'),
        '--execution-layout', request.param, '--radio-profile', 'gfsk250', '--output-dir', str(output)],
        cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    record = json.loads((output / 'RUNTIME_BUNDLE.json').read_bytes())
    raw = Path(record['archive']).read_bytes()
    files = helper('runtime_bootstrap').members(raw, record['sha256'])
    assert files['execution.zip'] and record['manifest']['schema'] == 'meshticulum.bench-runtime-bundle.v2'
    try: yield anchor, files, record
    finally: shutil.rmtree(anchor)


def extract(packaged_image, label):
    anchor, files, record = packaged_image
    root = anchor / label / ('runtime-' + record['sha256'][:16]); root.mkdir(parents=True, mode=0o700)
    root.parent.chmod(0o700)
    for name, raw in files.items():
        path = root / name; path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(raw); path.chmod(0o600)
    return root


ENTRY = '''
import hashlib,importlib,json,os,sys,types
from pathlib import Path
root=Path(sys.argv[1]);case=sys.argv[2]
raw=(root/'manifest.json').read_bytes();manifest=json.loads(raw)
if case=='preexisting':sys.modules['meshticulum']=types.ModuleType('meshticulum')
helper=types.ModuleType('_lantern_execution_runtime');helper.__file__=str(root/'execution_runtime.py')
sys.modules[helper.__name__]=helper;exec(compile((root/'execution_runtime.py').read_bytes(),helper.__file__,'exec'),helper.__dict__)
try:
 session=helper.install(root,raw,expected_manifest_sha256=hashlib.sha256(raw).hexdigest())
 if case=='preexisting':raise AssertionError('preexisting module accepted')
 from lantern_meshticulum.bench import pilot_resume_rpc as rpc,node,bundle_checkpoint_root
 from meshticulum.bench import flow_runtime
 from meshticulum.flow import flow
 assert node.BenchFlow is flow_runtime.BenchFlow
 assert flow.__name__=='meshticulum.flow.flow'
 assert helper.module_root(rpc.__name__)==root
 assert bundle_checkpoint_root.source_pin()==hashlib.sha256(helper.member_source(bundle_checkpoint_root.__name__)).hexdigest()
 if case=='roundtrip':
  original=rpc.Path;rpc.Path=lambda value:root.parent if value=='/opt/meshticulum-bench' else original(value)
  with rpc.Runtime(root,hashlib.sha256(raw).hexdigest()) as runtime:
   assert runtime.check() is True
  assert not any(name=='sage_lantern' or name.startswith('sage_lantern.') for name in sys.modules)
  print(json.dumps({'accepted':True,'loaded':len(session.loaded),'origin':rpc.__file__}));raise SystemExit(0)
 if case in ('runtime_binding','runtime_guard'):
  original=rpc.Path;rpc.Path=lambda value:root.parent if value=='/opt/meshticulum-bench' else original(value)
  with rpc.Runtime(root,hashlib.sha256(raw).hexdigest()) as runtime:
   if case=='runtime_binding':runtime._image=None
   else:runtime._Runtime__image_current=lambda:True
   runtime.check()
  raise AssertionError('runtime binding accepted')
 if case=='image_bytes':
  p=root/'execution.zip';data=p.read_bytes();p.write_bytes(bytes([data[0]^1])+data[1:])
 elif case=='image_inode':
  p=root/'execution.zip';replacement=root/'replacement';replacement.write_bytes(p.read_bytes());replacement.chmod(0o600);replacement.replace(p)
 elif case=='image_symlink':
  p=root/'execution.zip';p.rename(root/'old-image');p.symlink_to(root/'old-image')
 elif case=='root_rebound':
  root.rename(root.with_name(root.name+'-held'));root.mkdir(mode=0o700)
 elif case=='origin':rpc.__file__='foreign source'
 elif case=='loader':rpc.__spec__.loader=object()
 elif case=='module':sys.modules[rpc.__name__]=types.ModuleType(rpc.__name__)
 elif case=='loose_missing':
  p=root/'meshticulum';p.mkdir();(p/'unplanned.py').write_text('executed=True')
  sys.path.insert(0,str(root));importlib.import_module('meshticulum.unplanned')
 elif case=='reload':importlib.reload(rpc)
 elif case=='state':session.members={'untrusted':b'code'}
 elif case=='registry':session.loaded['meshticulum.unplanned']=types.ModuleType('meshticulum.unplanned')
 elif case=='registration':
  loader=rpc.__spec__.loader;fake=types.ModuleType(rpc.__name__)
  fake.__file__=rpc.__file__;fake.__package__=rpc.__package__;fake.__spec__=rpc.__spec__
  session._remove(rpc.__name__);sys.modules[rpc.__name__]=fake
  session._record(rpc.__name__,fake,loader,rpc.__file__,rpc.__package__,())
 elif case=='fake_module_exec':
  fake=types.ModuleType(rpc.__name__);fake.__spec__=rpc.__spec__
  rpc.__spec__.loader.exec_module(fake)
 elif case=='private_executor':
  spec=session.find_spec('meshticulum.bench.hat_probe');fake=types.ModuleType(spec.name)
  fake.__spec__=spec
  session._Session__execute(spec.loader,fake)
 elif case=='api':helper.member_source=lambda name:b'untrusted'
 elif case=='dictionary_shadow':session.__dict__['check_modules']=lambda:True
 elif case=='singleton':
  helper._ACTIVE=object()
  assert helper.active(root,hashlib.sha256(raw).hexdigest()) is session
  print(json.dumps({'refused':True,'ignored_fake_global':True}));raise SystemExit(0)
 elif case=='injected_namespace':
  spec=session.find_spec('meshticulum.bench.hat_probe');module=spec.loader.create_module(spec)
  module.__spec__=spec;module.__loader__=spec.loader;module.__dict__['__builtins__']={}
  spec.loader.exec_module(module)
 else:raise AssertionError('unknown case')
 session.guard.check();session.check_modules()
 helper.active(root,hashlib.sha256(raw).hexdigest())
except (RuntimeError,OSError,ImportError,TypeError,AttributeError) as error:
 print(json.dumps({'refused':True,'error':type(error).__name__}))
else:raise AssertionError('attack accepted')
'''


def control(packaged_image, case):
    root = extract(packaged_image, case)
    result = subprocess.run([sys.executable, '-I', '-B', '-c', ENTRY, str(root), case],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_canonical_imports_and_actual_runtime_reader_composition(packaged_image):
    result = control(packaged_image, 'roundtrip')
    assert result['accepted'] and result['loaded'] >= 75 and '/execution.zip/' in result['origin']


@pytest.mark.parametrize('case', ['preexisting','image_bytes','image_inode','image_symlink','root_rebound',
    'origin','loader','module','loose_missing','reload','state','registry','registration',
    'fake_module_exec','singleton','injected_namespace','private_executor','api',
    'runtime_binding','runtime_guard','dictionary_shadow'])
def test_sealed_import_or_current_source_mutation_is_refused(packaged_image, case):
    assert control(packaged_image, case)['refused']


def test_v2_archive_and_source_correspondence_are_exact(packaged_image):
    _, files, record = packaged_image; manifest = record['manifest']
    assert set(files) == set(manifest['files']) | {'manifest.json'}
    assert 'lantern_meshticulum/bench/pilot_resume_rpc.py' in manifest['source_files']
    assert not any(name.startswith(('meshticulum/','lantern_meshticulum/')) for name in files)
    assert manifest['execution_layout']['bytes'] <= 2000000 and sum(map(len, files.values())) <= 2000000
    assert 'meshticulum/flow/flow.py' in manifest['source_files']
    assert manifest['source_files']['meshticulum/flow/flow.py'] == '9a8d4bb3bea2f69581d80ecd31fda7692c2435fe1363be53564d5281485cb857'


def test_embedded_rpc_entries_still_compile():
    compile(helper('management_file_rpc').RPC_FILE_ENTRY, 'file-entry', 'exec')
    compile(helper('run_public_sf5_c_bulk_8k').RPC_ENTRY, 'argv-entry', 'exec')


@pytest.mark.parametrize('case', ['bytecode','native','traversal','symlink','compressed','duplicate','member_pin'])
def test_image_parser_rejects_execution_or_roster_substitution(packaged_image, case):
    _, files, record = packaged_image
    manifest = json.loads(json.dumps(record['manifest']))
    original = helper('runtime_bootstrap').image_members(files['execution.zip'], manifest)
    target = next(name for name in original if name.endswith('.py'))
    output = io.BytesIO(); sources = {}
    with zipfile.ZipFile(output, 'w') as archive:
        for name, raw in original.items():
            if name == target:
                if case == 'bytecode': name = name[:-3] + '.pyc'
                elif case == 'native': name = name[:-3] + '.so'
                elif case == 'traversal': name = '../' + name
            row = zipfile.ZipInfo(name); row.create_system = 3
            row.external_attr = (0o120600 if case == 'symlink' and name == target else 0o100600) << 16
            row.compress_type = (zipfile.ZIP_DEFLATED if manifest['execution_layout']['schema'].endswith('.v2')
                                 else zipfile.ZIP_STORED)
            if case == 'compressed' and name == target:
                row.compress_type = zipfile.ZIP_STORED if row.compress_type == zipfile.ZIP_DEFLATED else zipfile.ZIP_DEFLATED
            archive.writestr(row, raw); sources[name] = hashlib.sha256(raw).hexdigest()
        if case == 'duplicate': archive.writestr(target, original[target])
    raw = output.getvalue(); digest = hashlib.sha256(raw).hexdigest()
    manifest['execution_layout'].update(bytes=len(raw), sha256=digest,
        source_files_sha256=hashlib.sha256(json.dumps(sources,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    manifest['files']['execution.zip'] = digest; manifest['source_files'] = sources
    if case == 'member_pin':
        sources[target] = '0' * 64
        manifest['execution_layout']['source_files_sha256'] = hashlib.sha256(
            json.dumps(sources,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    for parser in (helper('runtime_bootstrap').image_members, helper('execution_runtime').image_members):
        with pytest.raises(RuntimeError): parser(raw, manifest)


def test_archive_validation_does_not_execute_received_loader(packaged_image):
    _, source_files, record = packaged_image
    files = dict(source_files); manifest = json.loads(json.dumps(record['manifest']))
    # A data parser must not run even a correctly hashed received loader.
    files['execution_runtime.py'] = b"raise AssertionError('received loader executed')\n"
    pin = hashlib.sha256(files['execution_runtime.py']).hexdigest()
    manifest['files']['execution_runtime.py'] = pin
    manifest['execution_layout']['loader_sha256'] = pin
    files['manifest.json'] = json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as archive:
        for name, raw in files.items():
            row = tarfile.TarInfo(name); row.size = len(raw); archive.addfile(row,io.BytesIO(raw))
    raw = output.getvalue()
    assert helper('runtime_bootstrap').members(raw,hashlib.sha256(raw).hexdigest()) == files
