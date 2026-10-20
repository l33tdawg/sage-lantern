# SPDX-License-Identifier: Apache-2.0
"""Real localhost TCP/crypto; native root, consent, purpose and SDK are modeled."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
import os
import queue
import socket
import threading
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from meshticulum.privacy.forward_session import ForwardContext, public
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.ip.wire import READY_MODE
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.forward_tcp import ForwardTcpStage, ForwardTcpError, plan_stage
from tests.integrations.sage.test_forward_native_owner import setup
from tests.integrations.sage.test_mesh_inbox import SENDER, RECIPIENT, sealed
from tests.test_meshticulum_bench_runtime import BOOT


def clock():
    return time.time_ns() // 1000, time.monotonic_ns() // 1000


def original():
    epoch, mono = clock()
    noise = X25519PrivateKey.generate(), X25519PrivateKey.generate()
    ctx = ForwardContext(SENDER, RECIPIENT, uuid.uuid4().bytes, b'a' * 32,
        epoch - 1000000, epoch + 60000000, 64, public(noise[0]), public(noise[1]))
    packet = to_binary(sealed(issued_at=epoch // 1000000 - 1,
                             expires_at=epoch // 1000000 + 60).decode())
    intention = ReceiveIntention(str(uuid.uuid4()), hashlib.sha256(packet).hexdigest(), len(packet),
        ctx.binding_digest.hex(), BOOT, ctx.expires_epoch_us, mono + 60000000)
    return ctx, intention, noise, packet


def make_owner(r, initiator):
    ctx = r.context
    return ForwardNativeOwner(r.hardware, r.authority, r.native_context, context=ctx,
        intention=r.intention, local_initiator=initiator,
        key_check=lambda key, digest: key == public(r.noise[0 if initiator else 1]) and digest == ctx.digest,
        peer_key_check=lambda peer, key, digest: peer == (ctx.responder if initiator else ctx.initiator)
            and key == public(r.noise[1 if initiator else 0]) and digest == ctx.digest,
        boot_reader=lambda: r.state.boot, clock_reader=clock)


def native_plan(owner, packet, slot, addresses, *, startup_mode=None):
    kind = slot + 1
    first = owner.context.initiator if kind % 2 else owner.context.responder
    second = owner.context.responder if kind % 2 else owner.context.initiator
    peer = lambda p: dict(chain_id=p.chain_id, agent_id=p.public_key.hex())
    plan=dict(schema='meshticulum.ip-service-run.v1', host_boot_id=BOOT,
        local_peer=peer(owner.local), session=dict(initiator=peer(first), responder=peer(second),
            session_id=bytes([slot + 11]).hex() * 16, generation=bytes([slot + 21]).hex() * 16),
        format='MPF2.record', object=dict(bytes=len(packet), sha256=hashlib.sha256(packet).hexdigest()),
        route_binding_digest=owner.context.digest.hex(), issued_epoch_us=owner.context.issued_epoch_us,
        expires_epoch_us=owner.context.expires_epoch_us,
        application_expires_epoch_us=owner.context.expires_epoch_us,
        application_deadline_monotonic_us=owner.intention.application_deadline_monotonic_us - 2000000,
        initiator_address=list(addresses[0]), responder_address=list(addresses[1]),
        limits=dict(max_plaintext=256, max_frames=64, max_tx_stream_bytes=65536,
                    max_rx_stream_bytes=65536, max_object_bytes=4096, window_frames=4, max_retries=3))
    if startup_mode is not None:
        plan.update(schema='meshticulum.ip-service-run.v2',startup_mode=startup_mode)
    return plan


def grant_files(directory, profile):
    directory.mkdir(mode=0o700)
    for name, raw in (('grant.json', profile.plan_bytes), ('secret.bin', profile.grant.secret), ('active', b'active\n')):
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)


def unused_address():
    with socket.socket() as value:
        value.bind(('127.0.0.1', 0))
        return value.getsockname()


@pytest.mark.parametrize('damage', ['dns', 'peer', 'role', 'format', 'route', 'expiry', 'deadline', 'caps'])
def test_pure_profile_refuses_unbound_route_or_limits_before_io(damage, monkeypatch):
    with setup() as r:
        owner = r.create()
        p = native_plan(owner, b'x' * 77, 0, (('127.0.0.1', 10001), ('127.0.0.1', 10002)))
        if damage == 'dns': p['responder_address'][0] = 'localhost'
        elif damage == 'peer': p['session']['responder']['agent_id'] = 'c' * 64
        elif damage == 'role': p['local_peer'] = p['session']['responder']
        elif damage == 'format': p['format'] = 'MR1.request'
        elif damage == 'route': p['route_binding_digest'] = 'c' * 64
        elif damage == 'expiry': p['application_expires_epoch_us'] += 1
        elif damage == 'deadline': p['application_deadline_monotonic_us'] = r.intention.application_deadline_monotonic_us
        else: p['limits']['max_frames'] = 0
        monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('pure planning cannot open sockets'))
        with pytest.raises(ForwardTcpError): plan_stage(owner, p, b's' * 32, slot=0, kind=1)
        assert not r.state.rows


def test_no_default_private_admission_or_forged_profile(tmp_path):
    with setup() as r:
        owner = r.create()
        p = native_plan(owner, b'x' * 77, 0, (('127.0.0.1', 10001), ('127.0.0.1', 10002)))
        profile = plan_stage(owner, p, b's' * 32, slot=0, kind=1)
        with pytest.raises(ForwardTcpError): ForwardTcpStage(owner, profile, tmp_path / 'absent')
        forged = replace(profile, child=replace(profile.child, cost=Cost(ip_frames=1, tx_stream_bytes=128, rx_stream_bytes=128)))
        with pytest.raises(ForwardTcpError): ForwardTcpStage(owner, forged, tmp_path / 'absent', native_admission=lambda p: True)
        assert not r.state.rows and not (tmp_path / 'absent').exists()


def test_explicit_sender_ready_plan_changes_native_profile_without_changing_caps():
    with setup() as r:
        owner=r.create()
        p=native_plan(owner,b'x'*77,0,(('127.0.0.1',10001),('127.0.0.1',10002)))
        legacy=plan_stage(owner,p,b's'*32,slot=0,kind=1)
        p.update(schema='meshticulum.ip-service-run.v2',startup_mode=READY_MODE)
        ready=plan_stage(owner,p,b's'*32,slot=0,kind=1)
        assert legacy.grant.startup_mode is None and ready.grant.startup_mode==READY_MODE
        assert legacy.grant.digest!=ready.grant.digest and legacy.digest!=ready.digest
        assert legacy.child.cost==ready.child.cost and legacy.child.cipher_frame_limit==ready.child.cipher_frame_limit
        assert legacy.child.application_deadline_monotonic_us==ready.child.application_deadline_monotonic_us
        assert json.loads(ready.plan_bytes)['startup_mode']==READY_MODE and not r.state.rows


@pytest.mark.parametrize('damage',['v1_extra','v2_missing','unknown'])
def test_private_profile_refuses_unversioned_or_unrecognized_readiness(damage):
    with setup() as r:
        owner=r.create()
        p=native_plan(owner,b'x'*77,0,(('127.0.0.1',10001),('127.0.0.1',10002)),startup_mode=READY_MODE)
        if damage=='v1_extra':p['schema']='meshticulum.ip-service-run.v1'
        elif damage=='v2_missing':p.pop('startup_mode')
        else:p['startup_mode']='another_mode'
        with pytest.raises(ForwardTcpError):plan_stage(owner,p,b's'*32,slot=0,kind=1)
        assert not r.state.rows


def test_parent_cap_refusal_precedes_socket_and_journal_creation(tmp_path, monkeypatch):
    with setup() as r:
        owner = r.create(); op = owner.operation(r.noise[0], limits=Cost(ip_frames=1, tx_stream_bytes=128, rx_stream_bytes=128),
                                                prior_debt=Cost(), sealed_record=r.packet)
        packet = op.packet()
        profile = plan_stage(owner, native_plan(owner, packet, 0, (('127.0.0.1', 10001), ('127.0.0.1', 10002))), b's' * 32, slot=0, kind=1)
        stage = ForwardTcpStage(owner, profile, tmp_path / 'absent', native_admission=lambda p: True)
        monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('parent refused before socket'))
        with pytest.raises(Exception): stage.prepare(payload=packet)
        assert not (tmp_path / 'absent').exists() and owner._registry._children == []
        assert r.state.rows[op.budget.record_id]['payload']['body']['allocations'] == []


@pytest.mark.parametrize('failure', ['callback-close', 'caught-reentry', 'profile-replaced', 'listener-failure'])
def test_partial_listener_cleanup_retains_full_parent_debt(tmp_path, failure, monkeypatch):
    values = original()
    with setup(initiator=False, original=values) as r:
        owner = make_owner(r, False); op = owner.operation(r.noise[1], limits=Cost(ip_frames=500, tx_stream_bytes=1000000, rx_stream_bytes=1000000), prior_debt=Cost())
        # Descriptor is shaped like H1; no application content is staged.
        packet = b'MPF2' + b'\x01' + r.context.session_id + bytes(8) + bytes(48)
        address = unused_address()
        profile = plan_stage(owner, native_plan(owner, packet, 0, (unused_address(), address)), b's' * 32, slot=0, kind=1)
        directory = tmp_path / 'grant'; grant_files(directory, profile)
        box = {}
        def ready(p):
            if failure == 'callback-close': owner.fence()
            elif failure == 'caught-reentry':
                with pytest.raises(ForwardTcpError): box['stage'].prepare()
            elif failure == 'profile-replaced': box['stage'].profile = replace(p, approval_binding='f' * 64)
            else: raise RuntimeError('bounded fixture listener failure')
            return True
        stage = ForwardTcpStage(owner, profile, directory, native_admission=lambda p: True, ready=ready); box['stage'] = stage
        with pytest.raises(Exception): stage.prepare()
        body = r.state.rows[op.budget.record_id]['payload']['body']
        assert body['allocations'] == [profile.child.to_dict()]
        assert all(value.fileno() == -1 for value, identity in stage._sockets)
        assert stage.journal.closed and stage._directory_fd is None
        assert owner._registry._children[0].unknown and stage._unknown
        assert not (directory / 'sender.bin').exists()


def test_five_stages_real_tcp_authenticated_ack_and_joint_receive_commit(tmp_path):
    values = original(); commands = [queue.Queue(), queue.Queue()]; replies = queue.Queue(); reports = [[], []]
    def actor(index):
        try:
            with setup(initiator=index == 0, original=values) as r:
                owner = make_owner(r, index == 0)
                op = owner.operation(r.noise[index], limits=Cost(ip_frames=500, tx_stream_bytes=1000000, rx_stream_bytes=1000000),
                    prior_debt=Cost(ip_frames=2, tx_stream_bytes=128, rx_stream_bytes=128), sealed_record=r.packet if index == 0 else None)
                replies.put(('created', index, owner))
                while True:
                    command = commands[index].get(timeout=12)
                    if command[0] == 'packet': replies.put(('packet', index, op.packet())); continue
                    if command[0] == 'settle': replies.put(('settled', index, op.settle().status)); continue
                    if command[0] == 'close':
                        replies.put(('closed', index, owner.close())); break
                    _, slot, packet, addresses, ready = command
                    profile = plan_stage(owner, native_plan(owner, packet, slot, addresses),
                        hashlib.sha256(bytes([slot]) + r.context.digest).digest(), slot=slot, kind=slot + 1)
                    directory = tmp_path / f'{index}-{slot}'; grant_files(directory, profile)
                    def listening(p):
                        assert r.state.rows[op.budget.record_id]['payload']['body']['allocations'][-1] == p.child.to_dict()
                        ready.set(); return True
                    stage = ForwardTcpStage(owner, profile, directory, native_admission=lambda p: True, ready=listening)
                    runner = stage.prepare(payload=packet if profile.child.sends else None)
                    runner.start()
                    deadline = time.monotonic() + 8
                    while not runner.step():
                        assert time.monotonic() < deadline
                        time.sleep(.001)
                    assert runner.wire_complete
                    assert owner.retire_stage(runner) is True
                    report = stage.report(); reports[index].append(report)
                    assert report['retired'] and not report['uncertain']
                    assert report['byte_journal']['attempts'] > 0
                    assert report['byte_journal']['peer_receipt_established'] is False
                    assert not report['byte_journal']['pending_receive_stream_bytes']
                    replies.put(('stage', index, slot))
        except BaseException as error:
            replies.put(('error', index, repr(error)))
    workers = [threading.Thread(target=actor, args=(i,), daemon=True) for i in (0, 1)]
    for worker in workers: worker.start()
    def receive(expected, count=1):
        values = [replies.get(timeout=12) for _ in range(count)]
        assert all(row[0] == expected for row in values), values
        return values
    try:
        receive('created', 2)
        for slot in range(5):
            sender = slot % 2; receiver = 1 - sender
            commands[sender].put(('packet',)); packet = receive('packet')[0][2]
            addresses = unused_address(), unused_address(); ready = threading.Event()
            commands[receiver].put(('stage', slot, packet, addresses, ready))
            assert ready.wait(5)
            commands[sender].put(('stage', slot, packet, addresses, ready))
            receive('stage', 2)
        commands[1].put(('settle',)); assert receive('settled')[0][2] == 'admitted'
        for index in (0, 1): commands[index].put(('close',))
        assert all(row[2] is True for row in receive('closed', 2))
    finally:
        for index in (0, 1): commands[index].put(('close',))
        for worker in workers: worker.join(12)
    assert all(not worker.is_alive() for worker in workers)
    assert all(len(side) == 5 for side in reports)
    assert len(list(tmp_path.glob('*/bytes.sqlite'))) == 10
    assert not list(tmp_path.glob('*/sender.bin'))


@pytest.mark.parametrize('damage', ['child-expiry', 'active-revoked'])
def test_current_child_admission_refuses_before_any_socket(tmp_path, monkeypatch, damage):
    with setup(original=original()) as r:
        owner = make_owner(r, True)
        op = owner.operation(r.noise[0], limits=Cost(ip_frames=500, tx_stream_bytes=1000000, rx_stream_bytes=1000000),
                             prior_debt=Cost(), sealed_record=r.packet)
        packet = op.packet()
        profile = plan_stage(owner, native_plan(owner, packet, 0,
            (('127.0.0.1', 10001), ('127.0.0.1', 10002))), b's' * 32, slot=0, kind=1)
        directory = tmp_path / 'grant'; grant_files(directory, profile); box = {}; changed = []
        def admission(p):
            if box['stage'].authority is not None and not changed:
                changed.append(True)
                if damage == 'child-expiry':
                    epoch, mono = clock()
                    owner._clock = lambda: (epoch, p.child.application_deadline_monotonic_us + 1)
                else:
                    (directory / 'active').write_bytes(b'retired\n')
            return True
        stage = ForwardTcpStage(owner, profile, directory, native_admission=admission); box['stage'] = stage
        monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('child refusal precedes socket creation'))
        with pytest.raises(Exception): stage.prepare(payload=packet)
        assert changed and stage._sockets == []
        assert r.state.rows[op.budget.record_id]['payload']['body']['allocations'] == [profile.child.to_dict()]


@pytest.mark.parametrize('expired_cleanup', [False, True])
def test_stale_socket_detached_before_worker_close_even_after_cleanup_deadline(tmp_path, expired_cleanup):
    from meshticulum.ip.journal import IpJournal
    from meshticulum.ip.worker import TcpWorker
    with setup(original=original()) as r:
        owner = make_owner(r, True)
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1)
        client = socket.socket(); client.connect(listener.getsockname()); server, _ = listener.accept(); listener.close()
        profile = plan_stage(owner, native_plan(owner, b'x' * 77, 0,
            (client.getsockname(), server.getsockname())), b's' * 32, slot=0, kind=1)
        directory = tmp_path / 'grant'; grant_files(directory, profile)
        stage = ForwardTcpStage(owner, profile, directory, native_admission=lambda p: True)
        journal = IpJournal(directory / 'bytes.sqlite', profile.grant, create=True)
        worker = TcpWorker(client, profile.grant, profile.local_peer, journal)
        stage.journal = journal; stage.worker = worker; stage._worker_socket = worker.sock
        stage._handles.update(journal=journal, worker=worker); stage._track_socket(worker.sock)
        stale = worker.sock.fileno(); os.close(stale)
        foreign = os.open(tmp_path / 'foreign', os.O_CREAT | os.O_RDWR, 0o600)
        if foreign != stale: os.dup2(foreign, stale)
        expected = os.fstat(stale)
        try:
            assert stage._cleanup(time.monotonic() - 1 if expired_cleanup else time.monotonic() + 1) is False
            actual = os.fstat(stale)
            assert (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
            assert worker.sock.fileno() == -1 and stage._unknown
        finally:
            server.close(); worker.close()
            if not journal.closed: journal.close()
            os.close(stale)
            if foreign != stale: os.close(foreign)


@pytest.mark.parametrize('damage', ['reused', 'missing'])
def test_authority_cleanup_uses_complete_original_descriptor_mapping(tmp_path, damage):
    from meshticulum.ip.authority import IpAuthority
    with setup(original=original()) as r:
        owner = make_owner(r, True)
        profile = plan_stage(owner, native_plan(owner, b'x' * 77, 0,
            (('127.0.0.1', 10001), ('127.0.0.1', 10002))), b's' * 32, slot=0, kind=1)
        directory = tmp_path / 'grant'; grant_files(directory, profile)
        stage = ForwardTcpStage(owner, profile, directory, native_admission=lambda p: True)
        authority = IpAuthority(directory, local_peer=profile.local_peer, host_boot_id=BOOT, clock=clock, owner_probe=lambda: True)
        stage.authority = authority; stage._handles['authority'] = authority
        stage._authority_fd_refs = tuple((name, fd, authority.file_ids[name]) for name, fd in authority.fds.items())
        stage._directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        info = os.fstat(stage._directory_fd); stage._directory_identity = info.st_dev, info.st_ino
        stale = authority.fds['secret.bin']; foreign = None
        if damage == 'missing': authority.fds.pop('secret.bin')
        else:
            os.close(stale); foreign = os.open(tmp_path / 'foreign', os.O_CREAT | os.O_RDWR, 0o600)
            if foreign != stale: os.dup2(foreign, stale)
        try:
            assert stage._cleanup(time.monotonic() + 1) is False and stage._unknown
            if damage == 'reused':
                assert os.fstat(stale).st_ino == os.fstat(foreign).st_ino
            else:
                with pytest.raises(OSError): os.fstat(stale)
        finally:
            if foreign is not None:
                os.close(stale)
                if foreign != stale: os.close(foreign)
