#!/usr/bin/env python3
"""Bounded read-only observation collector, not approval or mutation authority.

Run in the sealed R7 maintenance interpreter with a reviewed root-owned spec.
Signed workflow reads stay on device; only their stable digest leaves this
process. Never export keys, tokens, question/answer payloads or media. The
separate vision driver must independently validate the resulting envelope.
"""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import types


def require(value):
    if not value:
        raise ValueError('vision_collection_refused')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def read_pin(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'}
            and re.fullmatch('[0-9a-f]{64}', pin['sha256']))
    path = Path(pin['path'])
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        fields = ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        require(len(raw) == before.st_size and digest(raw) == pin['sha256'] and all(
            getattr(before, field) == getattr(after, field)
            for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


def load(pin):
    raw = read_pin(pin)
    module = types.ModuleType('read_only_vision_dependency')
    module.__file__ = pin['path']
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


def get(port, route):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request('GET', route)
        response = connection.getresponse()
        raw = response.read(262145)
        require(response.status == 200 and len(raw) <= 262144)
        return json.loads(raw, object_pairs_hook=unique)
    finally:
        connection.close()


def observe_process(service, value):
    require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
            and re.fullmatch('[1-9][0-9]*', value['MainPID'])
            and value['ControlGroup'] == '/system.slice/' + service)
    root = Path('/proc') / value['MainPID']
    before = (root/'stat').read_bytes().rsplit(b')',1)[1].split()
    require(before[0] not in (b'Z',b'T',b't',b'X')
            and (root/'cgroup').read_text() == '0::/system.slice/' + service + '\n')
    image = (root/'exe').stat()
    arguments = (root/'cmdline').read_bytes()
    result = dict(pid=int(value['MainPID']),start_ticks=int(before[19]),uid=root.stat().st_uid,
        exe_dev=image.st_dev,exe_ino=image.st_ino,executable=os.readlink(root/'exe'),argv_sha256=digest(arguments))
    require((root/'stat').read_bytes().rsplit(b')',1)[1].split()[19] == before[19]
            and ((root/'exe').stat().st_dev,(root/'exe').stat().st_ino) == (image.st_dev,image.st_ino))
    return result, arguments.split(b'\0')[:-1]


def listener(pid, port):
    require(type(port) is int and 1 <= port <= 65535)
    rows = [line.split() for line in Path('/proc/net/tcp').read_text().splitlines()[1:]]
    matches = [row for row in rows if row[3] == '0A' and row[1].endswith(f':{port:04X}')]
    require(len(matches) == 1 and matches[0][1] == f'0100007F:{port:04X}')
    inode = matches[0][9]
    found = False
    for path in Path('/proc',str(pid),'fd').iterdir():
        try:
            found |= os.readlink(path) == 'socket:[' + inode + ']'
        except FileNotFoundError:
            pass
    require(found)
    return inode


def observe_workflows(collector, helper, current, expected):
    return collector.signed_observation(helper, types.SimpleNamespace(current=current), expected)


def collect(spec):
    names = {'backend','driver','defaults','helper','collector','runtime_tool','native_observer',
        'environment_builder','environment_complete','coordinator','adapter','installed_wheel','camera_path'}
    require(type(spec) is dict and set(spec) == names)
    base = load(spec['backend'])
    helper = load(spec['helper'])
    collector = load(spec['collector'])
    driver = load(spec['driver'])
    for name in names - {'camera_path','installed_wheel'}:
        read_pin(spec[name])
    require(spec['installed_wheel']['sha256'] == driver.R7)
    helper.pinned(spec['installed_wheel'])
    complete = json.loads(read_pin(spec['environment_complete']),object_pairs_hook=unique)
    require(sys.flags.isolated and sys.dont_write_bytecode and sys.prefix == complete['venv'])
    from sage_lantern.device_access import access_epoch
    from sage_lantern.deployment_bringup import REQUIRED, OPTIONAL
    from sage_lantern.mesh_daemon import load_config
    from sage_lantern.suspend_system import SuspendSystem
    system = SuspendSystem()
    services, extra, files = {}, {}, {}
    paths = {base.SYSTEMD_IMAGE, driver.PROJECTOR, driver.MODEL_FILE, str(base.MESH_CONFIG)}
    def show(service):
        return system._show(service,system.clock()+5,base.FIELDS)
    for service in base.SERVICES:
        value = show(service)
        if service == base.DISPLAY and value['ActiveState'] == 'inactive':
            policy = None
            if os.path.lexists(base.DISPLAY_ENABLE):
                policy = helper.file_hash(str(base.DISPLAY_ENABLE))
                paths.add(str(base.DISPLAY_ENABLE))
            base.inactive_display(system,service,value,policy_sha256=policy)
            process,args = None,[]
        else:
            process,args = observe_process(service,value)
        services[service] = dict(unit=value,process=process)
        paths.update((value['FragmentPath'],*value['DropInPaths'].split()))
        if process is not None: paths.add(process['executable'])
        for argument in args:
            if argument.startswith((b'/usr/local/libexec/',b'/opt/sage-lantern/libexec/',b'/opt/sage-lantern/fleet-admin/')):
                name=argument.decode()
                if Path(name).is_file(): paths.add(name)
    for service in base.EXTRA:
        value=system._show(service,system.clock()+5,('LoadState','ActiveState','Job'))
        require(value['ActiveState'] in ('inactive','failed') and system._no_job(value['Job']))
        if value['LoadState']=='loaded':
            value=show(service)
            paths.update((value['FragmentPath'],*value['DropInPaths'].split()))
        else: require(value['LoadState']=='not-found')
        extra[service]=value
    model_argv=collector.model_argv(system,helper)
    model_files={services[base.MODEL]['unit']['FragmentPath'],
        *services[base.MODEL]['unit']['DropInPaths'].split(),model_argv[0],driver.MODEL_FILE,driver.PROJECTOR}
    paths.update(model_files)
    import subprocess
    inspection=subprocess.run(['/opt/sage-lantern/venv/bin/python','-I','-B','-m',
        'sage_lantern.audio_readiness','--inspect'],check=True,capture_output=True,timeout=35,
        env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C','PYTHONDONTWRITEBYTECODE':'1'})
    snapshot=json.loads(inspection.stdout,object_pairs_hook=unique)['snapshot']
    paths.update(snapshot['inputs'])
    inventory={path:helper.file_hash(path) for path in REQUIRED}
    for service in (base.VOICE,base.BROKER):
        for path in services[service]['unit']['DropInPaths'].split():
            require(path in REQUIRED|OPTIONAL)
            inventory[path]=helper.file_hash(path)
    paths.update(inventory)
    for path in paths: files[path]=helper.file_hash(path)
    defaults=load(spec['defaults'])
    if os.path.lexists(defaults.PATH): files[str(defaults.PATH)]=helper.file_hash(str(defaults.PATH))
    parent_pins={str(path):base.directory_identity(path.lstat()) for path in base.SERVICE_PARENTS}
    receipts={}
    entries=list(base.RECEIPTS.iterdir())
    require(len(entries)<=128)
    uid=services[base.AUTH]['process']['uid']
    for path in entries: receipts[str(path)]=digest(base.read_receipt(path,uid,parent_pins))
    instance_path=base.VOICE_RUN/'instance.json'
    descriptor=os.open(instance_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid==services[base.VOICE]['process']['uid']
                and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1 and 0<info.st_size<16384)
        raw=os.read(descriptor,16384)
        require(len(raw)==info.st_size and base.file_identity(os.fstat(descriptor))==base.file_identity(info))
        instance=json.loads(raw,object_pairs_hook=unique)
    finally: os.close(descriptor)
    config=load_config()
    native_port=base.configured_native_port(config)
    native=get(native_port,'/v1/dashboard/health')
    capture_site='/opt/sage-lantern/venv/lib/python3.11/site-packages'
    startup_before={name:dict(path=str(path),sha256=helper.file_hash(str(path))) for name,path in (
        ('config',Path('/etc/sage-lantern/startup.json')),('latest',Path('/var/lib/sage-lantern-startup/latest.json')),
        ('coordinator',Path('/opt/sage-lantern/libexec/startup-coordinator.py')))}
    expected=dict(schema='sage.lantern.runtime-maintenance.v1',
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),access_epoch=access_epoch(),
        storage_epoch=None,workflow_sha256=None,actor=dict(chain_id=config['chain_id'],agent_id=config['agent_id']),
        helper=spec['helper'],collector=spec['collector'],installed_wheel=spec['installed_wheel'],
        candidate_wheel=spec['installed_wheel'],site=capture_site,files=files,services=services,
        receipts=receipts,receipt_parents=parent_pins,maintenance_site=sys.prefix+'/lib/python3.11/site-packages',
        runtime_tool=spec['runtime_tool'],native_health={key:native[key] for key in ('boot_id','version','encrypted')},
        native_listener_inode=listener(services[base.NATIVE]['process']['pid'],native_port),native_observer=spec['native_observer'],
        extra_services=extra,environment_builder=spec['environment_builder'],environment_complete=spec['environment_complete'],
        voice_instance=instance,audio_tail=dict(path=str(Path(spec['driver']['path']).with_name('complete-runtime-maintenance-audio.py')),
            sha256=helper.file_hash(str(Path(spec['driver']['path']).with_name('complete-runtime-maintenance-audio.py')))),
        audio_tools={name:spec[name] for name in ('helper','collector','adapter','coordinator')}|{'runtime_wheel':spec['installed_wheel']},
        capture_snapshot=snapshot,audio_inventory=inventory,model_files=sorted(model_files),model_argv=model_argv,
        startup_before=startup_before)
    require(expected['access_epoch'] is not None)
    metadata={path:helper.metadata(Path(path).lstat()) for path in files}
    def current():
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==expected['boot_id']
                and access_epoch()==expected['access_epoch'])
        for service,pin in services.items():
            value=show(service)
            require(value==pin['unit'])
            if pin['process'] is None:
                base.inactive_display(system,service,value,
                    policy_sha256=files.get(str(base.DISPLAY_ENABLE)))
            else: require(observe_process(service,value)[0]==pin['process'])
        require(all(helper.metadata(Path(path).lstat())==pin for path,pin in metadata.items()))
        require(all(digest(base.read_receipt(path,uid,parent_pins))==pin for path,pin in receipts.items()))
    expected['storage_epoch'],expected['workflow_sha256']=observe_workflows(collector,helper,current,expected)
    current()
    base.validate(expected,candidate_sha256=driver.R7)
    return dict(schema='sage.lantern.fresh-vision-activation.v1',backend=spec['backend'],defaults=spec['defaults'],
                maintenance=expected,camera_path=spec['camera_path'])


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--spec',required=True)
    parser.add_argument('--spec-sha256',required=True)
    args=parser.parse_args()
    require(os.geteuid()==0)
    def expired(signum,frame): raise TimeoutError('vision_collection_deadline')
    signal.signal(signal.SIGALRM,expired)
    signal.alarm(300)
    try:
        spec=json.loads(read_pin(dict(path=args.spec,sha256=args.spec_sha256)),object_pairs_hook=unique)
        print(json.dumps(collect(spec),sort_keys=True))
    finally: signal.alarm(0)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__,pins_produced=False)),file=sys.stderr)
        raise SystemExit(2) from None
