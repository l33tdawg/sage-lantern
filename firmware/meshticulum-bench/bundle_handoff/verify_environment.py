"""Verify a normal venv and exact installed wheel; imports open no device."""
import hashlib,importlib,importlib.metadata as metadata,json,sys,sysconfig
from pathlib import Path

def verify(root,unit,*,full=True):
    root=Path(root).resolve();spec=json.loads((root/'RUNTIME.json').read_bytes());expected=spec['units'][unit]
    prefix=Path(sys.prefix).resolve()
    assert prefix==root/'venv' and sys.prefix!=sys.base_prefix,'not_fresh_normal_venv'
    cfg=(prefix/'pyvenv.cfg').read_text().lower()
    assert 'include-system-site-packages = false' in cfg,'system_site_packages_enabled'
    assert sys.flags.isolated and not any('sdk.zip' in s or s.endswith('/src') for s in sys.path),'source_import_path_present'
    import platform
    assert list(sys.version_info[:2])==expected['python'] and platform.machine()==expected['machine'],'runtime_python_changed'
    purelib=Path(sysconfig.get_path('purelib')).resolve();assert purelib.is_relative_to(prefix)
    import meshticulum
    assert Path(meshticulum.__file__).resolve()==purelib/'meshticulum/__init__.py','sdk_not_installed_wheel'
    assert metadata.version('meshticulum')==spec['sdk_version']
    actual={str(p.relative_to(purelib)) for p in (purelib/'meshticulum').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    assert actual==set(spec['runtime_files']),'installed_runtime_inventory_changed'
    for name,pin in spec['runtime_files'].items():
        path=purelib/name;assert not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==pin,'installed_runtime_bytes_changed'
    dependencies=[]
    for row in expected['dependencies']:
        dist=metadata.distribution(row['name']);assert dist.version==row['version'],'installed_dependency_version_changed'
        assert Path(dist.locate_file('')).resolve()==purelib,'dependency_outside_fresh_venv'
        direct=dist.read_text('direct_url.json')
        assert not direct or not json.loads(direct).get('dir_info',{}).get('editable'),'editable_dependency'
        dependencies.append(dict(name=row['name'],version=dist.version,wheel_sha256=row['sha256'],wheel_filename=row['filename']))
    # Import actual crypto backends and the same public entry points; no interfaces open.
    for name in ['cryptography.hazmat.primitives.asymmetric.ed25519','nacl.bindings','meshticulum.bench.authority','meshticulum.bench.serial_worker','meshticulum.bench.burst_worker','meshticulum.bench.burst_protocol','meshticulum.bench.hat_worker','meshticulum.resources.byte_bundle']:
        module=importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(purelib),'runtime_module_outside_fresh_venv'
    assert not any(n=='sage_sdk' or n.startswith('sage_sdk.') or n=='sage_lantern' or n.startswith('sage_lantern.') for n in sys.modules)
    return dict(schema='meshticulum.installed-wheel-environment-proof.v1',unit=unit,python=sys.version,
        executable=sys.executable,prefix=str(prefix),base_prefix=sys.base_prefix,isolated=True,system_site_packages=False,
        editable=False,sdk_wheel_sha256=spec['sdk_wheel']['sha256'],runtime_files_verified=len(actual),
        runtime_inventory_sha256=hashlib.sha256(json.dumps(spec['runtime_files'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        sdk_module=str(Path(meshticulum.__file__).resolve()),dependencies=dependencies,grants_created=0,radio_opened=False)

if __name__=='__main__':print(json.dumps(verify(sys.argv[1],sys.argv[2]),sort_keys=True))
