"""Run temporary encrypted SAGE API fixtures; never production or RF IO.

Requires explicit SAGE/core source and the compatible installed SDK. A Go
overlay adds the fixture without changing SAGE source. Capture/lease policy is
modelled; successful backend compatibility cannot enable private dispatch.
"""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sage-source',type=Path,required=True)
    p.add_argument('--meshticulum-source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    sage=args.sage_source.resolve(strict=True);core=args.meshticulum_source.resolve(strict=True)
    assert version('sage-agent-sdk')=='11.19.10+lantern.20260906','compatible SDK required'
    for name in ('workflow_journal_handler_test.go','message_storage_status.go'):
        assert (sage/'api/rest'/name).is_file()
    assert (core/'src/meshticulum/privacy/mesh_binary_envelope.py').is_file()
    os.umask(0o077);out=args.output.resolve();out.mkdir(mode=0o700,exist_ok=False)
    fixture=Path(__file__).with_name('backend_interop_test.go.txt')
    script=Path(__file__).with_name('backend_interop.py')
    inputs = [fixture, script, Path(__file__).resolve(),
              sage/'api/rest/workflow_journal_handler_test.go', sage/'api/rest/message_storage_status.go']
    inputs += sorted((PROJECT/'src').rglob('*.py'))
    inputs += sorted((core/'src').rglob('*.py'))
    input_hashes = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    overlay=out/'overlay.json'
    overlay.write_text(json.dumps({'Replace':{str(sage/'api/rest/meshticulum_backend_interop_test.go'):str(fixture)}},sort_keys=True,indent=2)+'\n')
    env={k:v for k,v in os.environ.items() if not k.startswith(('SAGE_','LANTERN_','MESHTICULUM_'))}
    paths=[str(PROJECT/'src'),str(core/'src')]
    if env.get('PYTHONPATH'):paths.append(env['PYTHONPATH'])
    env.update(GOPROXY='off',GOSUMDB='off',GOENV='off',GOTOOLCHAIN='local',GOWORK='off',GOFLAGS='-mod=readonly',
        PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join(paths),
        MESHTICULUM_INTEROP_PYTHON=sys.executable,MESHTICULUM_INTEROP_SCRIPT=str(script))
    command=['go','test','-overlay',str(overlay),'./api/rest','-run','^TestMeshticulumEncryptedBackendInterop$','-count=1','-v']
    with (out/'output.log').open('wb') as log:
        run=subprocess.run(command,cwd=sage,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
    inputs_unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
                           for path,digest in input_hashes.items())
    record={'schema':'lantern.meshticulum.encrypted-backend-fixture-run.v1','returncode':run.returncode,
        'fixture_only':True,'production_owner_consent_qualified':False,'private_transport_qualified':False,
        'private_production_enabled':False,'radio_opened':False,
        'output_sha256':hashlib.sha256((out/'output.log').read_bytes()).hexdigest(),
        'input_sha256':input_hashes, 'inputs_unchanged_during_run':inputs_unchanged,
        'sdk_version':version('sage-agent-sdk')}
    (out/'RESULT.json').write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'saved':str(out/'RESULT.json'),'returncode':run.returncode,'fixture_only':True}))
    if run.returncode:raise SystemExit(run.returncode)
    if not inputs_unchanged:raise SystemExit('fixture inputs changed during run; not qualified')

if __name__=='__main__':main()
