import copy
import importlib.util
import json
import os
from pathlib import Path
import struct
import types
import zlib

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/lantern' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load('fresh_vision_activation', 'activate-fresh-vision.py')
BASE = load('fresh_vision_base', 'prepare-runtime-maintenance.py')
ARGV = ['/opt/sage-lantern/llama.cpp/llama-server', '--model', MODULE.MODEL_FILE,
        '--host', '127.0.0.1', '--port', '8081', '--threads', '6', '--parallel', '4']


def test_exact_installed_camera_default_is_bound_by_full_source_hash():
    raw = (ROOT / 'scripts/lantern/unit-a-camera-server.py').read_bytes()
    assert MODULE.sha(raw) == MODULE.CAMERA_DEFAULT_SHA
    MODULE.camera_binding([b'/opt/sage-lantern/venv/bin/python', MODULE.CAMERA_HELPER.encode()],
                          MODULE.CAMERA_DEFAULT_PATH, raw)


@pytest.mark.parametrize('change', ['source', 'path', 'argument', 'helper', 'missing'])
def test_camera_default_refuses_every_unreviewed_variant(change):
    raw = (ROOT / 'scripts/lantern/unit-a-camera-server.py').read_bytes()
    args = [b'/opt/sage-lantern/venv/bin/python', MODULE.CAMERA_HELPER.encode()]
    selected = MODULE.CAMERA_DEFAULT_PATH
    if change == 'source': raw += b'\n'
    if change == 'path': selected += '-other'
    if change == 'argument': args += [b'--unexpected']
    if change == 'helper': args[1] += b'-other'
    if change == 'missing': raw = None
    with pytest.raises(ValueError): MODULE.camera_binding(args, selected, raw)


def test_explicit_camera_binding_rejects_duplicates_and_missing_value():
    selected = MODULE.CAMERA_DEFAULT_PATH
    args = [b'python', MODULE.CAMERA_HELPER.encode()]
    MODULE.camera_binding(args + [b'--device', selected.encode()], selected)
    MODULE.camera_binding(args + [b'--device=' + selected.encode()], selected)
    for extra in ([b'--device'], [b'--device', selected.encode(), b'--device=' + selected.encode()]):
        with pytest.raises(ValueError): MODULE.camera_binding(args + extra, selected)


def test_vision_does_not_smuggle_performance_or_retrieval_change():
    result = MODULE.candidate_argv(ARGV)
    assert result[:len(ARGV)] == ARGV
    assert result[len(ARGV):] == ['--mmproj', MODULE.PROJECTOR, '--no-mmproj-offload', '--image-max-tokens', '256']
    assert MODULE.definition(result).decode().splitlines()[:2] == ['[Service]', 'ExecStart=']
    assert 'MemoryMax' not in MODULE.definition(result).decode()


@pytest.mark.parametrize('extra', [['--mmproj', MODULE.PROJECTOR], ['--image-max-tokens', '128'],
    ['--bad', '$(touch /tmp/forbidden)'], ['--bad', '%n']])
def test_candidate_rejects_ambiguous_existing_vision_or_systemd_expansion(extra):
    with pytest.raises(ValueError):
        MODULE.candidate_argv(ARGV + extra)


def test_photo_argv_retains_unit_audio_config_and_mesh_request():
    argv = MODULE.photo_argv('hw:CARD=Audio,DEV=0')
    assert argv[-1] == '--photo-ready'
    assert argv[argv.index('--drain-request')+1] == '/run/sage-lantern-mesh-control/voice-drain-request.json'
    assert argv[argv.index('--config')+1] == '/etc/sage-lantern/lantern.toml'
    with pytest.raises(ValueError):
        MODULE.photo_argv('hw:CARD=Audio,DEV=0 --bypass')


@pytest.mark.parametrize('color,pixel', [('red', b'\xff\x00\x00'), ('blue', b'\x00\x00\xff')])
def test_synthetic_png_has_only_known_color_pixels(color, pixel):
    raw = MODULE.color_png(color)
    assert raw[:8] == b'\x89PNG\r\n\x1a\n'
    offset = 8
    chunks = {}
    while offset < len(raw):
        size = struct.unpack('!I', raw[offset:offset+4])[0]
        kind = raw[offset+4:offset+8]
        data = raw[offset+8:offset+8+size]
        assert struct.unpack('!I', raw[offset+8+size:offset+12+size])[0] == zlib.crc32(kind + data)
        chunks[kind] = data
        offset += 12 + size
    assert zlib.decompress(chunks[b'IDAT']) == (b'\0' + pixel * 64) * 64
    assert len(raw) < 256


@pytest.mark.parametrize('answer,finish,accepted', [('Red.', 'stop', True), ('blue', 'stop', False),
    ('red', 'length', False), (None, 'stop', False)])
def test_probe_requires_actual_bounded_answer_not_http200_only(answer, finish, accepted):
    response = dict(choices=[dict(finish_reason=finish, message=dict(content=answer))],
                    usage=dict(prompt_tokens=30, completion_tokens=1))
    if accepted:
        assert MODULE.verify_response(response, 'red') == dict(prompt_tokens=30, completion_tokens=1)
    else:
        with pytest.raises(ValueError):
            MODULE.verify_response(response, 'red')


@pytest.mark.parametrize('fault', [None, 'wrong_color', 'http_error', 'epoch_changed'])
def test_probe_retains_uncertainty_on_failure_and_uses_only_synthetic_payload(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(MODULE, 'STATE', tmp_path)
    calls = []
    backend = types.SimpleNamespace(model_pin={'pid': 2}, current_after_auth=lambda: calls.append('current'))
    backend.helper = types.SimpleNamespace(sync=lambda path: None,
        canonical=lambda value: json.dumps(value).encode(), decode=json.loads,
        Recovery=types.SimpleNamespace(model_ready=lambda supplied: supplied.model_pin))
    backend.record = lambda phase, details: calls.append((phase, details))
    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ('127.0.0.1', 8081, 90)
        def request(self, method, path, body, headers):
            assert (method, path) == ('POST', '/v1/chat/completions')
            value = json.loads(body)
            assert value['chat_template_kwargs'] == {'enable_thinking': False}
            assert value['messages'][0]['content'][0]['text'] == MODULE.PROBE_PROMPT
            assert value['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
            calls.append('synthetic-request')
        def getresponse(self):
            color = 'red' if calls.count('synthetic-request') == 1 else 'blue'
            if fault == 'wrong_color':
                color = 'green'
            if fault == 'epoch_changed':
                backend.current_after_auth = lambda: (_ for _ in ()).throw(ValueError('epoch_changed'))
            return types.SimpleNamespace(status=503 if fault == 'http_error' else 200,
                read=lambda limit: json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(content=color))])).encode())
        def close(self):
            calls.append('connection-closed')
    monkeypatch.setattr(MODULE.http.client, 'HTTPConnection', Connection)
    if fault:
        with pytest.raises(ValueError):
            MODULE.probe(backend)
        assert len(list((tmp_path/'synthetic-pending').iterdir())) == 1
        assert not any(isinstance(call, tuple) and call[0] == 'synthetic_probe_done' for call in calls)
    else:
        result = MODULE.probe(backend)
        assert len(result) == 2 and all(row['matched'] for row in result)
        assert list((tmp_path/'synthetic-pending').iterdir()) == []
    assert 'connection-closed' in calls


@pytest.mark.parametrize('approval', [[], ['--approve-model-restart'], ['--approve-microphone-capture'],
    ['--approve-model-restart', '--approve-microphone-capture', '--wait-owner-seconds', '0'],
    ['--approve-model-restart', '--approve-microphone-capture', '--wait-owner-seconds', '301']])
def test_vision_unapproved_or_invalid_apply_refuses_before_any_load(monkeypatch, approval):
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE, 'trusted', lambda *args: pytest.fail('must not load'))
    with pytest.raises(ValueError):
        MODULE.main(['--expected', '/unused', '--expected-sha256', 'a'*64,
            '--backend', '/unused', '--backend-sha256', 'b'*64, '--apply', *approval])


@pytest.mark.parametrize('apply', [False, True])
def test_vision_cli_default_readonly_and_explicit_approved_dispatch(monkeypatch,tmp_path,capsys,apply):
    base_path=str(Path(MODULE.__file__).with_name('prepare-runtime-maintenance.py'))
    default_path=str(Path(MODULE.__file__).with_name('camera-vision-defaults.py'))
    envelope={'backend':dict(path=base_path,sha256='b'*64),
              'defaults':dict(path=default_path,sha256='c'*64)}
    fixture_source=b'def prepare(backend, *, approved_capture, wait_seconds):\n assert approved_capture and wait_seconds == 300\n backend.calls.append("apply")\n return {"fixture_apply":True}\n'
    raw={base_path:fixture_source,default_path:b'', '/fixture-expected':json.dumps(envelope).encode()}
    monkeypatch.setattr(MODULE,'trusted',lambda path,digest:raw[str(path)])
    monkeypatch.setattr(MODULE.os,'geteuid',lambda:0)
    monkeypatch.setattr(MODULE,'STATE',tmp_path/'absent-attempt')
    monkeypatch.setattr(MODULE.signal,'signal',lambda *args:None)
    monkeypatch.setattr(MODULE.signal,'alarm',lambda *args:None)
    calls=[]
    backend=types.SimpleNamespace(calls=calls,vision_probe=['fixture'],model_candidate=['fixture-model'],
        close=lambda:calls.append('close'),reconcile=lambda:calls.append('reconcile'))
    monkeypatch.setattr(MODULE,'backend_class',lambda base,defaults:lambda supplied:backend)
    args=['--expected','/fixture-expected','--expected-sha256','a'*64,
          '--backend',base_path,'--backend-sha256','b'*64]
    if apply:args+=['--apply','--approve-model-restart','--approve-microphone-capture']
    MODULE.main(args)
    result=json.loads(capsys.readouterr().out)
    assert calls==(['apply','close'] if apply else ['reconcile','close'])
    assert result['runtime_changed'] is False
    if apply:assert result['fixture_apply'] is True
    else:assert result['preflight'] is True and result['activation_authorized'] is False


def test_original_envelope_is_retained_and_journals_bind_it(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'STATE', tmp_path)
    cls = MODULE.backend_class(BASE)
    backend = cls.__new__(cls)
    backend.sequence = 0
    backend.original_envelope = {'original': 'unchanged'}
    backend.expected = {'effective': 'before'}
    backend.helper = types.SimpleNamespace(canonical=lambda value: json.dumps(value,sort_keys=True).encode(),
        write=lambda path, raw: path.open('xb').write(raw))
    backend.record('intent')
    backend.expected['effective'] = 'after'
    backend.record('configured')
    first = json.loads((tmp_path/'01-intent.json').read_bytes())
    second = json.loads((tmp_path/'02-configured.json').read_bytes())
    assert first['expected_sha256'] == second['expected_sha256']
    assert first['effective_inputs_sha256'] != second['effective_inputs_sha256']
    assert json.loads((tmp_path/'vision-expected.json').read_bytes()) == backend.original_envelope


def test_default_runtime_lane_remains_retrieval_only():
    import inspect
    assert inspect.signature(BASE.validate).parameters['candidate_sha256'].default == BASE.RETRIEVAL_V1
    assert MODULE.backend_class(BASE).replacement_phase == 'vision_configure'


def test_no_runtime_install_in_vision_replacement_method():
    import inspect
    source = inspect.getsource(MODULE.backend_class(BASE).install_pinned_runtime)
    assert 'pip' not in source
    assert source.index('verify_all_importers_empty') < source.index("self.inhibit(MODEL)")
    assert source.index('self.start_model()') < source.index('probe(self)') < source.index('self.add_definition(VOICE')


@pytest.mark.parametrize('failure', [None, 'model-stop', 'model-start', 'probe'])
def test_concrete_transition_never_enables_photo_before_verified_probe(monkeypatch, failure):
    backend = MODULE.backend_class(BASE).__new__(MODULE.backend_class(BASE))
    calls = []
    def action(name):
        calls.append(name)
        if failure == name:
            raise ValueError('injected-' + name)
    backend.verify_all_importers_empty = lambda: action('all-importers-empty')
    backend.runtime_tool = types.SimpleNamespace(verify_installed=lambda raw: action('verify-r7'))
    backend.new_wheel = backend.old_wheel = b'R7'
    backend.inference_empty = lambda: action('actual-inference-empty')
    backend.ensure_default = lambda: action('camera-default-required')
    backend.inhibit = lambda service: action('inhibit-model')
    backend.record = lambda *args: None
    backend.system = types.SimpleNamespace(clock=lambda: 0,
        _command=lambda args, deadline: action('model-stop'),
        _show=lambda *args: dict(LoadState='loaded', ActiveState='active', MainPID='10', InvocationID='new', Job=''),
        _no_job=lambda value: value == '')
    backend.empty = lambda *args: action('empty-model')
    backend.show = lambda service: {}
    backend.stopped = set()
    backend.current_after_auth = lambda: action('current')
    backend.add_definition = lambda service, argv: action('definition-' + service)
    backend.model_candidate = MODULE.candidate_argv(ARGV)
    backend.model_pin = {'pid': 10}
    backend.voice_candidate = MODULE.photo_argv('hw:CARD=Audio,DEV=0')
    backend.definition_paths = {service: Path('/etc/systemd/system/'+service+'.d/95-lantern-local-vision.conf')
                                for service in (MODULE.MODEL, MODULE.VOICE)}
    backend.expected = dict(model_files=[], audio_inventory={}, capture_snapshot=dict(inputs={}, services={}))
    backend.audio_tail = types.SimpleNamespace(release=lambda *args: action('release-model'))
    backend.start_model = lambda: action('model-start')
    backend.running = {MODULE.MODEL: dict(process=dict(pid=10), invocation='new')}
    backend.capture_before = {'services': {MODULE.MODEL: dict(LoadState='loaded', ActiveState='active', MainPID='old', InvocationID='old', Job='')}}
    backend.helper = types.SimpleNamespace(canonical=lambda value: json.dumps(value).encode())
    monkeypatch.setattr(MODULE, 'probe', lambda backend: action('probe') or ['verified-fixture'])
    if failure:
        with pytest.raises(ValueError, match='injected-' + failure):
            backend.install_pinned_runtime()
        assert 'definition-' + MODULE.VOICE not in calls
    else:
        backend.install_pinned_runtime()
        assert calls.index('actual-inference-empty') < calls.index('model-stop') < calls.index('empty-model')
        assert calls.index('model-start') < calls.index('probe') < calls.index('definition-' + MODULE.VOICE)
        assert backend.expected['capture_snapshot']['services'][MODULE.MODEL]['InvocationID'] == 'new'


@pytest.mark.parametrize('populated,confirmed', [('0', True), ('1', False)])
def test_failure_contains_model_and_never_claims_populated_group_empty(tmp_path, monkeypatch, populated, confirmed):
    class Parent:
        def retain_audio_failure(self, owner):
            return {'containment_verified': True}
    cls = MODULE.backend_class(types.SimpleNamespace(Maintenance=Parent))
    backend = cls.__new__(cls)
    boot = Path('/proc/sys/kernel/random/boot_id')
    original_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda path, *args, **kwargs:
        'boot' if path == boot else original_read(path, *args, **kwargs))
    backend.expected = {'boot_id': 'boot'}
    backend.model_touched = True
    backend.guarded = set()
    path = tmp_path/'guard.conf'
    backend.guard_path = lambda service: path
    backend.guard = lambda: b'exact-owned-guard'
    backend.helper = types.SimpleNamespace(write=lambda path, raw: path.write_bytes(raw), public=lambda path: path.read_bytes())
    group = tmp_path/'group'
    group.mkdir()
    (group/'cgroup.events').write_text('populated '+populated+'\n')
    calls = []
    backend.system = types.SimpleNamespace(clock=lambda: 0,
        _command=lambda args, deadline: calls.append(args),
        _guard=lambda *args: calls.append('effective-guard'), _cgroup=lambda name: group,
        _empty_tree=lambda *args: None, _no_job=lambda value: value == '')
    backend.show = lambda service: dict(MainPID='0', ActiveState='inactive', Job='')
    backend.record = lambda *args: (_ for _ in ()).throw(OSError('late-journal-failure'))
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: 0)
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    result = backend.retain_audio_failure(None)
    assert result['model_contained'] is confirmed
    assert ['stop', MODULE.MODEL] in calls
    assert path.read_bytes() == b'exact-owned-guard'
