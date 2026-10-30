"""Pure installation phase, before any radio scope. Normal venv; pinned wheels only."""
import hashlib,json,os,subprocess,sys,urllib.request
from pathlib import Path

def run(argv,timeout=45):
    value=subprocess.run([str(x) for x in argv],capture_output=True,text=True,timeout=timeout)
    assert value.returncode==0,'clean_environment_command_failed:'+value.stderr[-500:]
    return value.stdout

def install(root,unit):
    root=Path(root).resolve();spec=json.loads((root/'RUNTIME.json').read_bytes());expected=spec['units'][unit]
    import platform
    assert list(sys.version_info[:2])==expected['python'] and platform.machine()==expected['machine'],'bootstrap_python_changed'
    env=root/'venv';ready=root/'ENVIRONMENT_READY.json'
    if env.exists():
        assert ready.is_file(),'partial_environment_requires_root_review'
        return json.loads(run([env/'bin/python','-I','-B',root/'verify_environment.py',root,unit]))
    wheelhouse=root/'wheelhouse';wheelhouse.mkdir(mode=0o700,exist_ok=True)
    for row in expected['dependencies']:
        target=wheelhouse/row['filename']
        if target.exists():raw=target.read_bytes()
        else:
            assert row['url'].startswith('https://files.pythonhosted.org/')
            with urllib.request.urlopen(row['url'],timeout=20) as response:raw=response.read()
            assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256'],'dependency_download_hash_changed'
            with target.open('xb') as f:f.write(raw)
        assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256'],'cached_dependency_hash_changed'
    sdk=root/'wheelhouse'/spec['sdk_wheel']['filename']
    assert sdk.stat().st_size==spec['sdk_wheel']['bytes'] and hashlib.sha256(sdk.read_bytes()).hexdigest()==spec['sdk_wheel']['sha256']
    # Standard ensurepip is available on both actual hosts; no parent environment is copied.
    run([sys.executable,'-I','-B','-m','venv',env],timeout=30)
    requirements=root/('requirements-'+unit+'.txt')
    lines=['meshticulum=='+spec['sdk_version']+' --hash=sha256:'+spec['sdk_wheel']['sha256']]
    lines += [r['name']+'=='+r['version']+' --hash=sha256:'+r['sha256'] for r in expected['dependencies']]
    with requirements.open('x') as f:f.write('\n'.join(lines)+'\n')
    run([env/'bin/python','-I','-B','-m','pip','--disable-pip-version-check','install','--no-index','--only-binary=:all:','--no-compile','--require-hashes','--find-links',wheelhouse,'-r',requirements])
    run([env/'bin/python','-I','-B','-m','pip','--disable-pip-version-check','check'])
    proof=json.loads(run([env/'bin/python','-I','-B',root/'verify_environment.py',root,unit]))
    # Help exercises the public installed entry point without a source path or radio authority.
    run([env/'bin/python','-I','-B','-m','meshticulum.resources.lamplighter','--help'])
    with ready.open('x') as f:json.dump(proof,f,sort_keys=True)
    return proof

if __name__=='__main__':print(json.dumps(install(sys.argv[1],sys.argv[2]),sort_keys=True))
