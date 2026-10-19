"""Synthetic exact-peer/cancellation tests; no owner PIN or processes."""
import importlib.util
import json
from pathlib import Path
import socket
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sage_lantern.desktop_native import (NativeError, NativeSession, PeerAnchor,
    decode, kernel_peer, process_snapshot, pidfd_alive, trusted_file)
from test_desktop_auth import rig, PIN

ROOT = Path(__file__).resolve().parents[2]
BOOT = '10b61161-4469-43de-9fc3-d947ee7147fb'
OVERLAY = '/org/gnome/shell=/usr/local/lib/sage-lantern/gnome-pin/overlay'

@pytest.fixture
def peer():
    process = dict(pid=123, uid=1000, start=456, executable='/usr/bin/gnome-shell',
        cgroup='0::/user.slice/user-1000.slice/user@1000.service/session.slice/org.gnome.Shell@ubuntu.service',
        environment={'G_RESOURCE_OVERLAYS': OVERLAY},
        argv=[b'/usr/bin/gnome-shell', b'--mode=ubuntu'])
    logind = dict(Id='3', User='1000', Type='wayland', Class='user',
                  Active='yes', Remote='no', Seat='seat0', Leader='100')
    deps = dict(snapshot=lambda _: process, session_read=lambda _: logind,
                boot_read=lambda: BOOT)
    anchor = PeerAnchor.enroll(123, 1000, '3', OVERLAY, 'a' * 64, **deps)
    return SimpleNamespace(anchor=anchor, process=process, logind=logind, deps=deps)

def test_enrolled_peer_binding_contains_kernel_start_and_session(peer):
    assert peer.anchor.matches(123, 1000, **peer.deps)
    assert 'pid123:start456:session3' in peer.anchor.binding
    assert len(peer.anchor.binding) <= 160
    assert not peer.anchor.matches(124, 1000, **peer.deps)
    assert not peer.anchor.matches(123, 0, **peer.deps)

@pytest.mark.parametrize('field,value', [
    ('start', 457), ('uid', 1001), ('executable', '/usr/bin/python3'),
    ('cgroup', '0::/user.slice/user-1000.slice/user@1000.service/app.slice/fake.service'),
    ('environment', {}), ('environment', {'G_RESOURCE_OVERLAYS': OVERLAY, 'LD_PRELOAD': '/tmp/x'}),
    ('argv', [b'/usr/bin/gnome-shell', b'--mode=ubuntu', b'--unsafe-mode']),
])
def test_process_replacement_unmeasured_code_and_wrong_unit_refuse(peer, field, value):
    peer.process[field] = value
    assert not peer.anchor.matches(123, 1000, **peer.deps)

@pytest.mark.parametrize('field,value', [
    ('Id', '4'), ('User', '1001'), ('Type', 'x11'), ('Class', 'greeter'),
    ('Active', 'no'), ('Remote', 'yes'), ('Seat', 'seat1'), ('Leader', '101'),
])
def test_session_switch_or_loss_refuses(peer, field, value):
    peer.logind[field] = value
    assert not peer.anchor.matches(123, 1000, **peer.deps)

def test_boot_change_and_failed_metadata_are_not_authorization(peer):
    assert not peer.anchor.matches(123, 1000, **{**peer.deps,
        'boot_read': lambda: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'})
    assert not peer.anchor.matches(123, 1000, **{**peer.deps,
        'snapshot': Mock(side_effect=OSError('fixture'))})

def session(rig, valid=lambda: True):
    def broker(payload):
        action = payload['action']
        if action == 'open':
            return rig.broker.desktop_open(payload['binding'], payload['mode'])
        if action == 'unlock':
            return rig.broker.desktop_unlock(payload['guard'], payload['pin'])
        if action == 'lock':
            return rig.broker.desktop_lock(payload['guard'])
        return rig.broker.desktop_access(payload['guard'], payload['token'], action)
    native = NativeSession('fixture-pid-start-session', broker, valid)
    native.request(dict(action='open', mode='desktop'))
    return native

def test_native_flow_never_exports_guard_or_tokens_or_unlocks_browser(rig):
    native = session(rig)
    assert native.request({'action': 'presence'}) == {'authorized': False}
    assert native.request({'action': 'unlock', 'pin': PIN}) == {'authorized': True}
    assert native.request({'action': 'activity'}) == {'authorized': True}
    assert native.request({'action': 'presence'}) == {'authorized': True}
    assert rig.device.status() == {'locked': True}
    assert native.request({'action': 'lock'}) == {'authorized': False}
    assert native.token is None
    assert rig.broker.desktop.active is None

def test_socket_disconnect_revokes_without_waiting_for_lease(rig):
    native = session(rig)
    native.request({'action': 'unlock', 'pin': PIN})
    native.close()
    assert rig.broker.desktop.active is None
    assert native.guard is native.token is None
    with pytest.raises(NativeError):
        native.request({'action': 'presence'})

def test_live_peer_loss_revokes_before_any_renewal(rig):
    valid = [True]
    native = session(rig, lambda: valid[0])
    native.request({'action': 'unlock', 'pin': PIN})
    valid[0] = False
    with pytest.raises(NativeError, match='peer_refused'):
        native.request({'action': 'activity'})
    assert rig.broker.desktop.active is None

@pytest.mark.parametrize('cancel', ['disconnect', 'mode', 'lock', 'peer'])
def test_pin_response_cannot_escape_disconnect_relock_or_mode_race(rig, cancel):
    valid = [True]
    native = session(rig, lambda: valid[0])
    entered, release = threading.Event(), threading.Event()
    def verify(pin):
        entered.set()
        assert release.wait(2)
        return True
    rig.verifier.side_effect = verify
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(native.request, {'action': 'unlock', 'pin': PIN})
        try:
            assert entered.wait(2)
            if cancel == 'disconnect':
                native.close()
            elif cancel == 'mode':
                native.request({'action': 'open', 'mode': 'kiosk'})
            elif cancel == 'lock':
                native.request({'action': 'lock'})
            else:
                valid[0] = False
        finally:
            release.set()
        with pytest.raises(Exception):
            future.result(timeout=2)
    assert native.token is None
    assert rig.broker.desktop.active is None

def test_presence_cannot_revive_expired_grant(rig):
    native = session(rig)
    native.request({'action': 'unlock', 'pin': PIN})
    rig.now[0] += 6
    assert native.request({'action': 'presence'}) == {'authorized': False}
    assert native.request({'action': 'activity'}) == {'authorized': False}

@pytest.mark.parametrize('payload', [
    {'action': 'unlock', 'pin': '123'}, {'action': 'unlock', 'pin': 12345678},
    {'action': 'unlock', 'pin': PIN, 'uid': 0}, {'action': 'activate', 'token': 'x' * 43},
    {'action': 'presence', 'binding': 'self-asserted'}, {'action': 'open', 'mode': True},
])
def test_native_protocol_rejects_claimed_identity_and_bearer_tokens(rig, payload):
    native = session(rig)
    with pytest.raises(NativeError, match='invalid_request'):
        native.request(payload)
    rig.verifier.assert_not_called()

@pytest.mark.parametrize('raw', [b'{}', b'{"action":"open","action":"unlock"}\n',
    b'{"n":NaN}\n', b'[]\n', b'x' * 513 + b'\n', b'\xff\n'])
def test_bounded_frames_and_duplicate_fields_fail_closed(raw):
    with pytest.raises(NativeError):
        decode(raw)

def test_kernel_peer_ignores_json_identity(monkeypatch):
    monkeypatch.setattr(socket, 'SO_PEERCRED', 17, raising=False)
    connection = Mock()
    connection.getsockopt.return_value = struct.pack('3i', 123, 1000, 1000)
    assert kernel_peer(connection) == (123, 1000)

def test_pidfd_death_and_no_metadata_are_closed(monkeypatch):
    monkeypatch.setattr('sage_lantern.desktop_native.select.select', lambda *args: ([], [], []))
    assert pidfd_alive(7)
    monkeypatch.setattr('sage_lantern.desktop_native.select.select', lambda *args: ([7], [], []))
    assert not pidfd_alive(7)

def test_trusted_payload_rejects_user_owned_tree_and_symlink(tmp_path):
    path = tmp_path / 'payload'
    path.write_text('fixture')
    with pytest.raises(NativeError):
        trusted_file(path)
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(NativeError):
        trusted_file(link)

def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/lantern' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_source_bound_build_is_mandatory_before_layout_and_refuses_drift(tmp_path):
    builder = load_script('native_guard_builder', 'build-gnome-pin-candidate.py')
    preflight = ROOT / 'work/c-desktop-pin-preflight-20261001/installed-source.json'
    startup = ROOT / 'work/c-desktop-pin-candidate-20261001/startup-source.json'
    output = tmp_path / 'candidate'
    result = builder.build(preflight, startup, output)
    main = (output / 'overlay/ui/main.js').read_text()
    assert main.index('LanternPinGuard.install();') < main.index('layoutManager.init();')
    assert 'extensionManager.init();' not in main
    assert result['desktop_pin_accepted'] is False
    assert 'mandatory_guard_missing' in (output / 'overlay/ui/init.js').read_text()
    with pytest.raises(FileExistsError):
        builder.build(preflight, startup, output)
    changed = json.loads(startup.read_text())
    changed['resources']['/org/gnome/shell/ui/main.js']['source'] += '\n// drift\n'
    wrong = tmp_path / 'startup.json'
    wrong.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        builder.build(preflight, wrong, tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()

def test_launcher_refuses_missing_resource_and_injected_code(tmp_path, monkeypatch):
    launcher = load_script('native_guard_launcher', 'launch-guarded-gnome.py')
    files = {name: 'a' * 64 for name in launcher.REQUIRED}
    manifest = dict(schema='sage.lantern.gnome-pin-candidate.v1', files=files,
                    gnome_library_sha256='b' * 64)
    def trusted(path, digest=None):
        if Path(path).name == 'manifest.json':
            return json.dumps(manifest).encode()
        if str(path).endswith('lanternPinGuard.js'):
            raise NativeError()
        return b'fixture'
    monkeypatch.setattr(launcher, 'trusted_file', trusted)
    with pytest.raises(NativeError):
        launcher.launch_environment(tmp_path, 'c' * 64, {'XDG_SESSION_TYPE': 'wayland'})
    with pytest.raises(NativeError):
        launcher.launch_environment(tmp_path, 'c' * 64,
            {'XDG_SESSION_TYPE': 'wayland', 'LD_PRELOAD': '/tmp/forged.so'})

def test_proc_snapshot_handles_complex_comm_without_retaining_unrelated_environment(tmp_path):
    pid = tmp_path / '123'
    pid.mkdir()
    executable = tmp_path / 'gnome-shell'
    executable.touch()
    (pid / 'exe').symlink_to(executable)
    (pid / 'stat').write_text('123 (name with ) parentheses) S ' + ' '.join(['0'] * 18 + ['456'] + ['0'] * 3))
    (pid / 'status').write_text('Uid:\t1000\t1000\t1000\t1000\n')
    (pid / 'cgroup').write_text('0::/fixture\n')
    (pid / 'cmdline').write_bytes(b'/usr/bin/gnome-shell\0--mode=ubuntu\0')
    (pid / 'environ').write_bytes(b'PRIVATE_VALUE=must-not-retain\0G_RESOURCE_OVERLAYS=fixture\0')
    result = process_snapshot(123, tmp_path)
    assert result['start'] == 456
    assert result['environment'] == {'G_RESOURCE_OVERLAYS': 'fixture'}
    assert 'must-not-retain' not in repr(result)


def test_native_policy_requires_full_guard_and_revokes_payload_mutations(monkeypatch):
    import hashlib
    import stat
    adapter = load_script('native_policy_test', 'desktop-native-adapter.py')
    directory = Path('/usr/local/lib/sage-lantern/test-candidate/overlay')
    files = {str(directory / 'ui' / name): 'a' * 64 for name in
             ('main.js', 'init.js', 'lanternPinGuard.js', 'lanternGuardState.js')}
    files[str(directory.parent / 'manifest.json')] = 'b' * 64
    files['/usr/lib/gnome-shell/libshell-18.so'] = 'c' * 64
    policy = dict(schema='sage.lantern.desktop-native-policy.v1', uid=1000,
        overlay='/org/gnome/shell=' + str(directory), executable_sha256='d' * 64,
        files=files, native_socket='/run/sage-lantern-desktop/native.sock',
        broker_socket='/run/sage-lantern-desktop-auth/control.sock', broker_uid=997)
    path = Path('/etc/sage-lantern/desktop-policy.json')
    mutation = [0]
    payloads = {str(path), '/usr/bin/gnome-shell', *files}
    def metadata(filename):
        name = str(filename)
        regular = name in payloads
        return SimpleNamespace(st_uid=0, st_mode=(stat.S_IFREG | 0o644) if regular else (stat.S_IFDIR | 0o755),
            st_dev=1, st_ino=hash(name), st_size=1,
            st_mtime_ns=mutation[0] if name.endswith('main.js') else 0, st_ctime_ns=0)
    monkeypatch.setattr(Path, 'lstat', metadata)
    monkeypatch.setattr(adapter, 'trusted_file', lambda filename, digest=None:
        json.dumps(policy).encode() if Path(filename) == path else b'fixture')
    _, check = adapter.load_policy(path)
    assert check()
    mutation[0] = 1
    assert not check()
    policy['files'].pop(str(directory / 'ui/lanternPinGuard.js'))
    with pytest.raises(NativeError):
        adapter.load_policy(path)


def test_prepare_policy_refuses_missing_session_and_preserves_existing_output(tmp_path):
    builder = load_script('guard_policy_builder', 'build-gnome-pin-candidate.py')
    prepare = load_script('guard_policy_prepare', 'prepare-gnome-pin-policy.py')
    candidate = tmp_path / 'candidate'
    builder.build(ROOT / 'work/c-desktop-pin-preflight-20261001/installed-source.json',
                  ROOT / 'work/c-desktop-pin-candidate-20261001/startup-source.json', candidate)
    manifest = json.loads((candidate / 'manifest.json').read_text())
    observed = dict(broker_uid=997, shell_sha256='a' * 64,
        library_sha256=manifest['gnome_library_sha256'], active_session='3',
        session='Id=3\nUser=1000\nSeat=seat0\nClass=user\nType=wayland\nActive=yes\nRemote=no\n')
    live = tmp_path / 'live.json'
    live.write_text(json.dumps(observed))
    output = tmp_path / 'policy.json'
    policy = prepare.prepare(candidate, live, Path('/usr/local/lib/sage-lantern/candidate'), output)
    assert policy['broker_uid'] == 997
    assert len(policy['files']) == 6
    with pytest.raises(FileExistsError):
        prepare.prepare(candidate, live, Path('/usr/local/lib/sage-lantern/candidate'), output)
    observed['session'] = ''
    live.write_text(json.dumps(observed))
    with pytest.raises(ValueError):
        prepare.prepare(candidate, live, Path('/usr/local/lib/sage-lantern/candidate'), tmp_path / 'refused.json')
    assert not (tmp_path / 'refused.json').exists()
