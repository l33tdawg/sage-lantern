"""Source-pinned isolated REST/vault canary harness; --check never opens servers.

--run is an explicit test execution step. It launches only Go's temporary
loopback fixtures; lifecycle/accounting/RF remain modeled and cannot qualify a
native private transport. Fixture keys travel on child stdin only.
"""
import argparse
import ast
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[2]
SDK_VERSION='11.19.10+lantern.20260906'
TEST_NAME='TestMeshticulumNativeProviderBackendCanary'
OUTCOME_PREFIX='NATIVE_PROVIDER_BACKEND_OUTCOME '


def need(value,reason):
    if not value:raise RuntimeError(reason)


def digest(path):
    value=path.lstat();need(stat.S_ISREG(value.st_mode),'source_regular_file_required')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sanitized_environment(core,runtime,child_seconds):
    # No inherited SDK/model endpoint, proxy, secret or Python import settings.
    allowed=('PATH','HOME','TMPDIR','TMP','TEMP','GOCACHE','GOMODCACHE','GOPATH','CGO_ENABLED','CC','CXX')
    env={key:os.environ[key] for key in allowed if key in os.environ}
    env.update(GOPROXY='off',GOSUMDB='off',GOENV='off',GOTOOLCHAIN='local',GOWORK='off',GOFLAGS='-mod=readonly',
        PYTHONDONTWRITEBYTECODE='1',PYTHONWARNINGS='ignore',PYTHONNOUSERSITE='1',
        PYTHONPATH=os.pathsep.join(map(str,(PROJECT,PROJECT/'src',core/'src',runtime))),
        MESHTICULUM_SOURCE=str(core),LANTERN_NATIVE_RUNTIME_SOURCE=str(runtime),
        MESHTICULUM_NATIVE_BACKEND_PYTHON=sys.executable,
        MESHTICULUM_NATIVE_BACKEND_SCRIPT=str(Path(__file__).with_name('native_provider_backend.py')),
        MESHTICULUM_NATIVE_BACKEND_CHILD_SECONDS=str(child_seconds))
    return env


def source_inputs(sage,core,runtime):
    files=[Path(__file__).resolve(),Path(__file__).with_name('native_provider_backend.py'),
        Path(__file__).with_name('native_provider_backend_test.go.txt'),
        PROJECT/'tests/integrations/sage/native_canary_helpers.py',
        PROJECT/'tests/integrations/sage/test_forward_operation.py',
        PROJECT/'tests/integrations/sage/test_mesh_inbox.py',PROJECT/'tests/__init__.py',
        PROJECT/'tests/integrations/__init__.py',PROJECT/'tests/integrations/sage/__init__.py',
        core/'tests/__init__.py',
        core/'tests/test_meshticulum_bench_runtime.py',core/'tests/test_meshticulum_private_carrier.py',
        core/'tests/radio_profiles.py',sage/'go.mod',sage/'go.sum']
    for tree,pattern in ((PROJECT/'src','*.py'),(core/'src','*.py'),(runtime/'sage_lantern','*.py'),(sage,'*.go')):
        files.extend(sorted(tree.rglob(pattern)))
    return {str(path.resolve(strict=True)):digest(path) for path in sorted(set(files))}


def prepare(sage,core,runtime,child_seconds):
    need(version('sage-agent-sdk')==SDK_VERSION,'compatible_sdk_required')
    need(type(child_seconds) is int and 15<=child_seconds<=1200,'bounded_child_timeout_required')
    for name in ('workflow_journal_handler_test.go','message_storage_status.go'):
        need((sage/'api/rest'/name).is_file(),'temporary_sage_fixture_source_required')
    need((core/'src/meshticulum/privacy/mesh_binary_envelope.py').is_file(),'explicit_core_source_required')
    need((runtime/'sage_lantern/mesh_daemon.py').is_file(),'explicit_runtime_source_required')
    for name in ('native_provider_backend.py','run_native_provider_backend_fixture.py'):
        ast.parse(Path(__file__).with_name(name).read_text())
    return source_inputs(sage,core,runtime),sanitized_environment(core,runtime,child_seconds)


def parse_outcome(raw):
    outcomes=[line.split(OUTCOME_PREFIX,1)[1] for line in raw.splitlines() if OUTCOME_PREFIX in line]
    need(len(outcomes)<=1,'duplicate_fixture_outcome')
    if not outcomes:return None
    value=json.loads(outcomes[0]);need(type(value) is dict and value.get('schema')==
        'lantern.meshticulum.native-provider-backend-canary.v1','wrong_fixture_outcome')
    need(value.get('complete') is True and value.get('independent_go_main_wal_scan') is True,'incomplete_fixture_outcome')
    for field in ('private_transport_qualified','native_accounting_qualified','native_owner_consent_qualified',
        'private_production_enabled','radio_opened','memory_erasure_qualified',
        'noise_key_encodings_scanned','noise_key_disk_persistence_qualified'):
        need(value.get(field) is False,'unsupported_qualification_claim')
    return value


def execute(sage,out,inputs,env,child_seconds,diagnose_replay=False):
    os.umask(0o077);out.mkdir(mode=0o700,exist_ok=False)
    started_epoch_us=time.time_ns()//1000;started_monotonic_ns=time.monotonic_ns()
    overlay=out/'overlay.json'
    overlay.write_text(json.dumps({'Replace':{str(sage/'api/rest/meshticulum_native_provider_backend_test.go'):
        str(Path(__file__).with_name('native_provider_backend_test.go.txt'))}},sort_keys=True,indent=2)+'\n')
    selected='TestMeshticulumNativeProviderReplayWindowBoundary' if diagnose_replay else TEST_NAME
    command=['go','test','-overlay',str(overlay),'./api/rest','-run','^'+selected+'$','-count=1','-v',
        '-timeout',str(child_seconds+60)+'s']
    timed_out=False;interrupted=False
    with (out/'output.log').open('xb') as log:
        child=subprocess.Popen(command,cwd=sage,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=child.wait(timeout=child_seconds+120)
        except subprocess.TimeoutExpired:
            timed_out=True;os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
            code=124
        except BaseException:
            interrupted=True;os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
            code=130
    try:
        runtime=Path(env['LANTERN_NATIVE_RUNTIME_SOURCE']);core=Path(env['MESHTICULUM_SOURCE'])
        unchanged=source_inputs(sage,core,runtime)==inputs
    except Exception:unchanged=False
    log=out/'output.log';outcome=None;outcome_error=None
    try:outcome=parse_outcome(log.read_text())
    except Exception as error:outcome_error=type(error).__name__
    record=dict(schema='lantern.meshticulum.native-provider-backend-fixture-run.v1',
        complete=code==0 and not timed_out and unchanged and outcome is not None and not diagnose_replay,
        diagnostic_only=diagnose_replay,diagnostic_completed=diagnose_replay and code==0 and unchanged,
        returncode=code,test_execution_timed_out=timed_out,test_execution_interrupted=interrupted,
        owned_process_group_reaped=True,
        started_epoch_us=started_epoch_us,finished_epoch_us=time.time_ns()//1000,
        elapsed_execution_wall_us=(time.monotonic_ns()-started_monotonic_ns)//1000,
        child_timeout_seconds=child_seconds,outer_timeout_seconds=child_seconds+120,
        inputs_unchanged_during_run=unchanged,input_sha256=inputs,output_sha256=digest(log),
        fixture_outcome=outcome,outcome_error=outcome_error,sdk_version=SDK_VERSION,
        production_owner_consent_qualified=False,native_accounting_qualified=False,
        private_transport_qualified=False,private_production_enabled=False,radio_opened=False)
    target=out/'RESULT.json';target.write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
    print(json.dumps(dict(saved=str(target),complete=record['complete'],returncode=code)))
    return 0 if record['complete'] or record['diagnostic_completed'] else code or 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sage-source',type=Path,required=True)
    parser.add_argument('--meshticulum-source',type=Path,required=True)
    parser.add_argument('--runtime-source',type=Path,required=True)
    parser.add_argument('--child-timeout-seconds',type=int,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--diagnose-replay-window',action='store_true')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check',action='store_true');mode.add_argument('--run',action='store_true')
    args=parser.parse_args()
    sage=args.sage_source.resolve(strict=True);core=args.meshticulum_source.resolve(strict=True)
    runtime=args.runtime_source.resolve(strict=True)
    inputs,env=prepare(sage,core,runtime,args.child_timeout_seconds)
    if args.check:
        print(json.dumps(dict(source_ready=True,execution_started=False,sdk_version=SDK_VERSION,
            child_timeout_seconds=args.child_timeout_seconds,source_file_count=len(inputs),
            input_manifest_sha256=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()),sort_keys=True))
        return 0
    need(args.output is not None,'new_output_directory_required')
    return execute(sage,args.output.resolve(),inputs,env,args.child_timeout_seconds,args.diagnose_replay_window)


if __name__=='__main__':raise SystemExit(main())
