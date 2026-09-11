import importlib.util
import os
from pathlib import Path
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts/lantern'/filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load('initial_exec_base', 'prepare-runtime-maintenance.py')
VISION = load('initial_exec_vision', 'activate-fresh-vision.py')


@pytest.fixture
def process_fixture(tmp_path, monkeypatch):
    proc = tmp_path/'proc'
    root = proc/'123'
    root.mkdir(parents=True)
    systemd, target, foreign = (tmp_path/name for name in ('systemd', 'required', 'foreign'))
    for path in (systemd, target, foreign):
        path.write_bytes(path.name.encode())
    monkeypatch.setattr(BASE, 'PROC', proc)
    monkeypatch.setattr(BASE, 'SYSTEMD_IMAGE', str(systemd))
    state = dict(uid=0, clock=0)
    real_stat = Path.stat
    def stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == root:
            return types.SimpleNamespace(st_uid=state['uid'])
        return result
    monkeypatch.setattr(Path, 'stat', stat)
    def setup(service):
        unit = dict(MainPID='123', InvocationID='b'*32, ActiveState='active', Job='',
                    ControlGroup='/system.slice/'+service)
        (root/'stat').write_text('123 (fixture) S ' + ' '.join(['0']*18 + ['456']))
        (root/'cgroup').write_text('0::/system.slice/'+service+'\n')
        (root/'cmdline').write_bytes(str(target).encode()+b'\0--fixture\0')
        (root/'exe').symlink_to(systemd)
        backend = VISION.backend_class(BASE).__new__(VISION.backend_class(BASE))
        backend.expected = dict(services={service: dict(unit={'InvocationID':'a'*32},
            process=dict(executable=str(target), uid=os.getuid(), argv_sha256=VISION.sha((root/'cmdline').read_bytes())))},
            files={str(path): VISION.sha(path.read_bytes()) for path in (systemd,target)})
        backend.helper = types.SimpleNamespace(sha=VISION.sha, metadata=BASE.file_identity)
        backend.file_metadata = {str(path): BASE.file_identity(real_stat(path)) for path in (systemd,target)}
        backend.show = lambda service: dict(unit)
        backend.system = types.SimpleNamespace(clock=lambda: state['clock'],
            _show=lambda *args: {'User':'sage-lantern-camera','DynamicUser':'yes'})
        return backend, unit
    return types.SimpleNamespace(setup=setup, root=root, systemd=systemd, target=target,
                                 foreign=foreign, state=state)


@pytest.mark.parametrize('service', [BASE.MODEL, BASE.AUTH, BASE.UI, *BASE.BASIC])
def test_real_process_predicate_waits_systemd_then_requires_final_image_uid(process_fixture, service):
    fixture = process_fixture
    backend, unit = fixture.setup(service)
    value, observed = backend.starting_process(service, unit, None, 10)
    assert value is None and observed == ('123',456,'b'*32,False)
    (fixture.root/'exe').unlink()
    (fixture.root/'exe').symlink_to(fixture.target)
    fixture.state['uid'] = os.getuid()
    value, observed = backend.starting_process(service, unit, observed, 10)
    assert value['executable'] == str(fixture.target) and value['uid'] == os.getuid()
    assert value['argv_sha256'] == VISION.sha((fixture.root/'cmdline').read_bytes())
    assert observed[-1] is True


@pytest.mark.parametrize('fault', ['pid', 'ticks', 'invocation', 'image', 'uid', 'deadline', 'cgroup', 'revert'])
def test_real_initial_exec_predicate_rejects_identity_changes(process_fixture, fault):
    fixture = process_fixture
    backend, unit = fixture.setup(BASE.AUTH)
    _, observed = backend.starting_process(BASE.AUTH, unit, None, 10)
    (fixture.root/'exe').unlink()
    (fixture.root/'exe').symlink_to(fixture.foreign if fault=='image' else fixture.target)
    fixture.state['uid'] = 999999 if fault=='uid' else os.getuid()
    if fault=='pid': unit['MainPID']='124'
    if fault=='invocation': unit['InvocationID']='c'*32
    if fault=='ticks': (fixture.root/'stat').write_text('123 (fixture) S '+' '.join(['0']*18+['457']))
    if fault=='deadline': fixture.state['clock']=11
    if fault=='cgroup': (fixture.root/'cgroup').write_text('0::/foreign\n')
    if fault=='revert':
        _, observed=backend.starting_process(BASE.AUTH,unit,observed,10)
        (fixture.root/'exe').unlink(); (fixture.root/'exe').symlink_to(fixture.systemd)
    with pytest.raises((ValueError,FileNotFoundError)):
        backend.starting_process(BASE.AUTH,unit,observed,10)


@pytest.mark.parametrize('fault', [None, 'wrong_argv', 'wrong_uid', 'never_exec'])
def test_real_start_model_uses_real_process_predicate(process_fixture, monkeypatch, fault):
    fixture=process_fixture
    backend,unit=fixture.setup(BASE.MODEL)
    calls=[]
    backend.model_candidate=[str(fixture.target),'--fixture']
    backend.collector=types.SimpleNamespace(model_argv=lambda *args: backend.model_candidate)
    backend.current_after_auth=lambda: calls.append('full-current-check')
    backend.record=lambda phase,details=None: calls.append(phase)
    backend.running={}
    backend.system._command=lambda args,deadline: calls.append(args)
    backend.system._no_job=lambda value: value==''
    backend.helper.Recovery=types.SimpleNamespace(model_ready=lambda supplied: {'pid':123})
    def sleep(seconds):
        fixture.state['clock'] += 1
        if fault!='never_exec' and (fixture.root/'exe').resolve()==fixture.systemd:
            (fixture.root/'exe').unlink(); (fixture.root/'exe').symlink_to(fixture.target)
            fixture.state['uid']=999999 if fault=='wrong_uid' else os.getuid()
            if fault=='wrong_argv': (fixture.root/'cmdline').write_bytes(str(fixture.target).encode()+b'\0--foreign\0')
    monkeypatch.setattr(VISION.time,'sleep',sleep)
    if fault:
        with pytest.raises(ValueError): backend.start_model()
        assert 'model_start_done' not in calls
    else:
        backend.start_model()
        assert backend.running[BASE.MODEL]['process']['start_ticks']==456
        assert calls[-1]=='model_start_done'


@pytest.mark.parametrize('service', [BASE.AUTH,BASE.UI,*BASE.BASIC])
@pytest.mark.parametrize('wrong_argv', [False,True])
def test_real_inherited_restore_waits_then_rejects_wrong_final_argv(process_fixture,monkeypatch,service,wrong_argv):
    fixture=process_fixture
    backend,unit=fixture.setup(service)
    guard=fixture.root.parent/'maintenance.conf'
    backend.guard_path=lambda service: guard
    guard.write_bytes(backend.guard())
    backend.installed=True
    backend.guarded={service}
    backend.stopped={service}
    backend.running={}
    backend.restore_attempted=set()
    calls=[]
    backend.current_after_auth=lambda: calls.append('full-current-check')
    backend.record=lambda phase,details=None: calls.append(phase)
    backend.helper.public=lambda path: path.read_bytes()
    backend.helper.sync=lambda path: None
    backend.system._command=lambda args,deadline: calls.append(args)
    backend.system._no_job=lambda value: value==''
    backend.locked=lambda: calls.append('locked-check')
    def sleep(seconds):
        fixture.state['clock']+=1
        if (fixture.root/'exe').resolve()==fixture.systemd:
            (fixture.root/'exe').unlink(); (fixture.root/'exe').symlink_to(fixture.target)
            fixture.state['uid']=os.getuid()
            if wrong_argv:(fixture.root/'cmdline').write_bytes(str(fixture.target).encode()+b'\0--foreign\0')
    monkeypatch.setattr(BASE.time,'sleep',sleep)
    if wrong_argv:
        with pytest.raises(ValueError):backend._restore(service)
        assert service not in backend.running
    else:
        backend._restore(service)
        assert backend.running[service]['process']['start_ticks']==456
        assert 'start_'+service+'_done' in calls
        if service==BASE.AUTH:assert 'locked-check' in calls
