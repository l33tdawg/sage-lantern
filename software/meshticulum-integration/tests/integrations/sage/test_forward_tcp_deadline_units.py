# SPDX-License-Identifier: Apache-2.0
"""Protected TCP deadlines keep their integer identity and native tightening."""
from dataclasses import replace
import hashlib
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from meshticulum.bench.services import ServiceTransfer
from meshticulum.privacy.forward_session import ForwardContext, public
from lantern_meshticulum.sage.forward_operation import describe_child
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.forward_tcp import ForwardTcpError, plan_stage
from lantern_meshticulum.sage.mesh_binary_capture import MAX_TIME_US
from tests.integrations.sage.test_forward_native_owner import setup
from tests.integrations.sage.test_forward_tcp import native_plan
from tests.integrations.sage.test_mesh_inbox import SENDER, RECIPIENT, sealed
from meshticulum.privacy.mesh_binary_envelope import to_binary
from tests.test_meshticulum_bench_runtime import BOOT
from tests.test_ip_transport import Clock as IpClock, pair as ip_pair


DEADLINES = (
    pytest.param(1049280873877, id='roundtrip-above'),
    pytest.param(1049280873884, id='roundtrip-below'),
    pytest.param(1049280873873, id='roundtrip-exact'),
    pytest.param(MAX_TIME_US - 2000000, id='maximum-parent-time'),
)
EPOCH = 100000000


def fresh_native_values(child_deadline):
    # Original 80s source fixture lifetime, declared before native approval.
    noise = X25519PrivateKey.generate(), X25519PrivateKey.generate()
    context = ForwardContext(SENDER, RECIPIENT, uuid.uuid4().bytes, b'a' * 32,
        EPOCH, EPOCH + 80000000, 64, public(noise[0]), public(noise[1]))
    packet = to_binary(sealed(issued_at=100, expires_at=180).decode())
    intention = ReceiveIntention(str(uuid.uuid4()), hashlib.sha256(packet).hexdigest(), len(packet),
        context.binding_digest.hex(), BOOT, context.expires_epoch_us, child_deadline + 2000000)
    return context, intention, noise, packet


@pytest.mark.parametrize('deadline', DEADLINES)
def test_tcp_profile_retains_protected_plan_integer_before_io(monkeypatch, deadline):
    values = fresh_native_values(deadline)
    initial = EPOCH, values[1].application_deadline_monotonic_us - 80000000
    with setup(original=values, initial_clock=initial) as rig:
        owner = rig.create()
        try:
            plan = native_plan(owner, b'x' * 77, 0,
                (('127.0.0.1', 10001), ('127.0.0.1', 10002)))
            assert plan['application_deadline_monotonic_us'] == deadline
            import socket
            monkeypatch.setattr(socket, 'socket', lambda *args, **kwargs: pytest.fail('pure planning opened a socket'))
            profile = plan_stage(owner, plan, b's' * 32, slot=0, kind=1)
            assert profile.child.application_deadline_monotonic_us == deadline
            assert not rig.state.rows and not rig.hardware._mutex.locked()
        finally:
            owner.close()


def test_tcp_profile_still_refuses_lost_original_parent_margin_before_io(monkeypatch):
    values = fresh_native_values(1049280873884)
    initial = EPOCH, values[1].application_deadline_monotonic_us - 80000000
    with setup(original=values, initial_clock=initial) as rig:
        owner = rig.create()
        try:
            plan = native_plan(owner, b'x' * 77, 0,
                (('127.0.0.1', 10001), ('127.0.0.1', 10002)))
            plan['application_deadline_monotonic_us'] = owner.intention.application_deadline_monotonic_us - 999999
            import socket
            monkeypatch.setattr(socket, 'socket', lambda *args, **kwargs: pytest.fail('refused planning opened a socket'))
            with pytest.raises(ForwardTcpError):
                plan_stage(owner, plan, b's' * 32, slot=0, kind=1)
            assert not rig.state.rows and not rig.hardware._mutex.locked()
        finally:
            owner.close()


@pytest.mark.parametrize('deadline', DEADLINES)
@pytest.mark.parametrize('tightened', (False, True), ids=('original', 'epoch-tightened'))
def test_actual_ip_transfer_description_preserves_effective_integer(tmp_path, monkeypatch, deadline, tightened):
    # Clock is fixed before grant files, authorities and stores are constructed.
    monkeypatch.setattr(IpClock, '__call__', lambda self: (EPOCH + self.offset, deadline - 50000000 + self.offset))
    declared = deadline + 10000000 if tightened else deadline
    expires = EPOCH + (50000000 if tightened else 60000000)
    with ip_pair(tmp_path / 'pair', b'x' * 77, 'MPF2.record',
                 expires=expires, deadline_us=declared) as (flows, workers, stores, clock):
        for flow, worker, store in zip(flows, workers, stores):
            transfer = ServiceTransfer(flow)
            assert flow.authority.plan['application_deadline_monotonic_us'] == declared
            assert flow.authority.application_deadline_us == deadline
            assert store.original_deadline_us == deadline
            child = describe_child(transfer, slot=0, kind=1)
            assert child.application_deadline_monotonic_us == deadline
            snapshot = worker.journal.snapshot()
            assert snapshot['attempts'] == 0 and snapshot['received_stream_bytes'] == 0

