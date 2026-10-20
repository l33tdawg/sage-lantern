# SPDX-License-Identifier: Apache-2.0
"""Real native authority/CAS/core flows; hardware, accounting and RF are modeled.

No network, device or endpoint-adoption qualification. A is an independent
generated identity; only the C/B source fixture owns endpoint crypto keys.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
from collections import deque
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey
from sage_lantern.mesh_daemon import HardwareRuntime
from sage_lantern.mesh_community import MeshActionAuthority, MeshRequestAccess
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.message_contacts import RecipientID
from lantern_meshticulum.sage.forward_native_relay import (
    ForwardNativeRelay, ForwardNativeRelayError, NativeRelayFactory, RelayPrepared,
    RelayTerminal, RELAY_SCOPES, relay_approval_binding,
)
from lantern_meshticulum.sage.forward_native_provider import NativeAccountingSource, NativeAccountingObservation
from lantern_meshticulum.sage.forward_budget import Cost, ChildPlan
from lantern_meshticulum.sage.forward_children import ForwardChildrenError, ForwardClosePermit
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage.mesh_inbox import _canonical
from meshticulum.bench.authority import RunAuthority, canonical, revoke
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import BenchGrant, SessionCipher
from meshticulum.contracts.identity import PeerID, DeadlineSet, SessionKey
from meshticulum.privacy.forward_relay import RelayDescription, RelayHop, RelayStage
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.integrations.sage.test_forward_operation import backend
from tests.test_forward_session_flow import session_pair
from tests.test_meshticulum_bench_runtime import BOOT, Clock, ModelWorker
from tests.test_meshticulum_private_carrier import sealed_message, open_private, CONTENT
from tests.radio_profiles import SOURCE_ADMISSION, profile_for
from tests.test_pilot_parent_snapshot import root_observation


DEBT = Cost(7, 7000, 3, 11, 13)
LIMITS = Cost(2567, 600007000, 5000, 1000000, 1000000)


class Accounting(NativeAccountingSource):
    def __init__(self, r):
        self.r = r; self.debt = DEBT; self.actual = None; self.evidence = 'a' * 64
        self.closed = False; self.calls = 0; self.hook = None
        self._busy = 0
    def observe(self, resource, intention):
        assert not self.closed and resource is self.r.hardware and intention is self.r.intention
        self.calls += 1
        if self.hook: self.hook()
        return NativeAccountingObservation(self.debt, self.actual, self.evidence, BOOT, *self.r.clock())
    def close(self):
        assert all(f.closed for f in self.r.factories if f.opened)
        self.closed = True
        return True
    def close_from_registry(self, permit):
        assert type(permit) is ForwardClosePermit and permit.resource is self.r.hardware
        assert permit.original_owner == self.r.owner._owner and permit.consume(self) is True
        return self.close()


@contextmanager
def relay_rig(path, *, scopes=RELAY_SCOPES, limit=LIMITS, approval_change=None, before_create=None,
              accounting_factory=Accounting, description_deadline=None):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    clock = Clock(); inner = to_binary(sealed_message(1).wire)
    sessions, context, stores = session_pair(path, clock, inner)
    key = SigningKey.generate()
    state, client, endpoint = backend(key)
    state.owner_live = True; state.epoch = 'e' * 32; state.boot = BOOT
    local = PeerID('native-relay-source-control', bytes(key.verify_key).hex())
    intention = ReceiveIntention(str(uuid.uuid4()), hashlib.sha256(inner).hexdigest(), len(inner),
        context.binding_digest.hex(), BOOT, context.expires_epoch_us,
        clock()[1] + context.expires_epoch_us - clock()[0])
    # A fresh alternate descriptor keeps the exact original intention deadline.
    declared = intention.application_deadline_monotonic_us / 1e6 if description_deadline is None else description_deadline
    description = RelayDescription(context, local, DeadlineSet(declared, declared, context.expires_epoch_us / 1e6))
    h = HardwareRuntime.__new__(HardwareRuntime)
    h.identity = client._identity; h.actor = RecipientID(local.chain_id, local.agent_id)
    h.probe = MessageStorageProbe(client, actor=h.actor, endpoint=endpoint)
    h._mutex = threading.Lock(); h._closed = h._closing = h._uncertain = False
    h._provider = h._coordinator = h._gateway = h._read_receipts = None
    h._radio_events = deque(); h._background_stop = threading.Event()
    h._epoch = lambda: state.epoch; h._lock_marker = lambda: True
    h.config = dict(agent_id=local.agent_id, chain_id=local.chain_id, messaging_enabled=True,
        receive_while_locked=False, dispatch_enabled=True, dispatch_while_locked=False,
        peers=[dict(chain_id=p.chain_id, agent_id=p.public_key.hex(), radio_public_key='a' * 64)
               for p in (context.initiator, context.responder)])
    h._config_reader = lambda: copy.deepcopy(h.config)
    h.supervisor = SimpleNamespace(phase='active', broker_id='b' * 32, _resource=h,
        resource_lifecycle_check=lambda: state.live, verified_epoch=lambda: state.instance_id + ':1',
        admission_epoch=lambda: state.instance_id + ':1')
    r = SimpleNamespace(hardware=h, client=client, state=state, clock=clock, context=context,
        description=description, intention=intention, local=local, inner=inner, sessions=sessions,
        path=path, owner=None, factories=[])
    r.accounting = accounting_factory(r); r.authority = MeshActionAuthority()
    approved = relay_approval_binding(description, intention, limit, r.accounting.observe(h, intention),
        config_revision=hashlib.sha256(_canonical(h.config)).hexdigest(), broker_id=h.supervisor.broker_id,
        storage_epoch=h.probe.snapshot().epoch)
    try:
        with r.authority.grant(h.actor, MeshRequestAccess(state.epoch, lambda: state.owner_live), scopes,
                approval_binding=approved if approval_change is None else approval_change(approved)) as native:
            if before_create: before_create(r)
            r.owner = ForwardNativeRelay(h, r.authority, native, description=description, intention=intention,
                limits=limit, accounting=r.accounting, boot_reader=lambda: state.boot, clock_reader=clock)
            yield r
    finally:
        if r.owner is not None:
            r.owner.fence()
            h._forward_children.close(1)
            r.owner.close()
        for factory in r.factories: factory.emergency_close()
        for session in sessions: session.close()
        for store in stores: store.db.close()
        client._client.close()


class ModeledFactory(NativeRelayFactory):
    """Held file grants + real journals/flows; physical ownership is a model."""
    def __init__(self, r, packet, kind, sends):
        self.r = r; self.kind = kind; self.sends = sends
        self.packet = packet; self.profile = profile_for('sf7-500')
        self.opened = self.closed = self.disarmed = False
        self.before_open = self.after_open = self.during_current = self.during_retire = None
        self.bad_terminal = False; self.terminal_transform = None; self.current_calls = self.prepared_calls = 0
        self.handles = []; self.journal_final = None
        self.contained_on = []
        slot = (kind - 1) * 2 + int(sends); number = slot + 1
        endpoints = tuple(PeerID(p.chain_id, p.public_key.hex()) for p in (r.context.initiator, r.context.responder))
        source, target = endpoints if kind % 2 else endpoints[::-1]
        source, target = (r.local, target) if sends else (source, r.local)
        self.local_index = 0 if sends else 1
        self.directories = []
        self.plans = []
        self.path = r.path / ('stage-' + str(slot)); self.path.mkdir(mode=0o700)
        for index, peer in enumerate((source, target)):
            unit = 'unit-a' if index == 0 else 'unit-b'
            directory = self.path / unit; directory.mkdir(mode=0o700)
            plan = dict(schema='meshticulum.service-run.v1', unit=unit, host_boot_id=BOOT,
                local_peer=asdict(peer), initiator=asdict(source), responder=asdict(target),
                session_id=number.to_bytes(16, 'big').hex(), generation=(1000 + number).to_bytes(16, 'big').hex(),
                issued_epoch_us=r.context.issued_epoch_us, expires_epoch_us=r.context.expires_epoch_us,
                application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us,
                profile=asdict(self.profile), sender_unit='unit-a', country='MY', antenna_confirmed=True,
                max_frames=512, object=dict(sha256=hashlib.sha256(packet).hexdigest(), bytes=len(packet),
                    chunk_index=slot, object_id=r.context.digest.hex()),
                service=dict(kind='protected_message', format='MPF2.record',
                             application_expires_epoch_us=r.context.expires_epoch_us))
            for name, raw in (('grant.json', canonical(plan)), ('secret.bin', bytes([number]) * 32), ('active', b'active\n')):
                fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as output: output.write(raw)
            self.directories.append(directory); self.plans.append(plan)
        p = self.plans[self.local_index]
        route = {k: p[k] for k in ('initiator', 'responder', 'session_id', 'generation', 'profile', 'object', 'sender_unit', 'country', 'service')}
        grant = BenchGrant(SessionKey(source, target, bytes.fromhex(p['session_id']), bytes.fromhex(p['generation'])),
            'bench_transfer', hashlib.sha256(canonical(route)).digest(), p['issued_epoch_us'], p['expires_epoch_us'],
            208, p['max_frames'], bytes([number]) * 32)
        self.hop = RelayHop('radio', grant.session, grant.route_binding_digest, grant.digest)
        self.plan = ChildPlan(slot, kind, sends, grant.digest.hex(), p['object']['sha256'], len(packet), 'lora',
            hashlib.sha256(canonical(asdict(self.profile))).hexdigest(), BOOT, r.context.expires_epoch_us,
            r.intention.application_deadline_monotonic_us, Cost(256, 60000000), 512)
        self.original_plan = self.plan
        self.original_files = tuple((d / name, (d / name).read_bytes()) for d in self.directories for name in ('grant.json', 'secret.bin'))
        self.evidence = hashlib.sha256(b''.join(raw for _, raw in self.original_files)).hexdigest()
        r.factories.append(self)

    def observed(self):
        assert all(path.read_bytes() == raw for path, raw in self.original_files)
        assert self.plan == self.original_plan
        return RelayPrepared(self.plan, self.hop, self.evidence, BOOT, *self.r.clock())

    def prepared(self, owner, plan, hop):
        self.prepared_calls += 1
        assert owner is self.r.owner and plan is self.plan and hop is self.hop
        return self.observed()

    def open(self, owner, child):
        assert not self.opened and owner._held and child.plan is self.plan
        row = self.r.state.rows[owner._budget.record_id]['payload']['body']
        assert len(row['allocations']) == (self.kind - 1) * 2 + 2
        assert [row['allocations'][-2 + n]['cost'] for n in range(2)] == [asdict(Cost(256, 60000000))] * 2
        if self.before_open: self.before_open(owner, child)
        assert owner.access_check() is True
        self.opened = True
        for index, (directory, plan) in enumerate(zip(self.directories, self.plans)):
            journal = AttemptJournal(directory / 'attempts.sqlite', binding=bytes([self.plan.slot + 1]) * 32,
                max_attempts=256, max_airtime_us=60000000, create=True, request_clock=lambda: self.r.clock()[0])
            worker = ModelWorker(journal, self.r.clock)
            authority = RunAuthority(directory, expected_unit=plan['unit'], host_boot_id=BOOT,
                clock=self.r.clock, owner_probe=lambda w=worker: w.owned, radio_admission=SOURCE_ADMISSION)
            store = SessionStore(directory / 'wire.sqlite', authority.grant, authority.local_peer,
                boot_id=BOOT, clock=self.r.clock, create=True)
            flow = BenchFlow(authority=authority, worker=worker, cipher=SessionCipher(authority.grant, authority.local_peer, store),
                journal=journal, flow_key=authority.flow_key, contract=authority.contract, profile=authority.carrier_profile,
                ledger=Ledger(BudgetPolicy(180000000, 60000000, 120000000, 1000000, quantization_us=1),
                    authority.ledger_epoch, self.r.clock()[1]), scheduler_epoch=authority.scheduler_epoch,
                broker_epoch=authority.broker_epoch, sends=index == 0, clock=self.r.clock)
            if index == self.local_index:
                transmit = worker.transmit
                def held_transmit(packet, transmit=transmit):
                    assert owner._held and owner.resource._mutex.locked()
                    assert owner.access_check() is True
                    return transmit(packet)
                worker.transmit = held_transmit
            self.handles.append(SimpleNamespace(journal=journal, worker=worker, authority=authority,
                store=store, flow=flow, transfer=ServiceTransfer(flow)))
        self.handles[0].worker.peer, self.handles[1].worker.peer = self.handles[1].worker, self.handles[0].worker
        self.local = self.handles[self.local_index]; self.remote = self.handles[1 - self.local_index]
        if self.after_open: self.after_open(owner, child)
        return self.local.transfer

    def current(self, owner, child):
        self.current_calls += 1
        assert owner is self.r.owner and child.transfer is self.local.transfer and not self.closed
        if self.during_current: self.during_current(owner, child)
        return self.observed()

    def _close(self):
        if self.closed: return
        if self.handles:
            j = self.local.journal
            self.journal_final = j.snapshot()
            self.journal_final['outcomes'] = dict(j.db.execute('SELECT state,count(*) FROM attempts GROUP BY state'))
            for h, directory in zip(self.handles, self.directories):
                h.flow.close(); h.worker.owned = False
                h.store.retire(); h.store.close(); h.journal.close(); revoke(directory); h.authority.close()
        self.closed = self.disarmed = True

    def retire(self, owner, child, deadline):
        assert owner._held and time.monotonic() < deadline
        if self.during_retire: self.during_retire(owner, child)
        self._close()

    def contain(self, owner, child, deadline):
        assert owner._closed and time.monotonic() < deadline
        self.contained_on.append(threading.get_ident())
        self._close()

    def terminal(self, owner, child):
        snapshot = self.journal_final or {}
        value = RelayTerminal(self.observed(), snapshot, hashlib.sha256(_canonical(snapshot)).hexdigest(),
            hashlib.sha256(b'modeled terminal; no physical proof' + bytes.fromhex(self.evidence)).hexdigest(),
            self.closed and not self.bad_terminal, False, self.disarmed)
        return value if self.terminal_transform is None else self.terminal_transform(value)

    def emergency_close(self):
        self._close()


def prepare_stage(r, packet, kind=1):
    incoming = ModeledFactory(r, packet, kind, False)
    outgoing = ModeledFactory(r, packet, kind, True)
    stage = RelayStage(kind, incoming.hop, outgoing.hop, len(packet), hashlib.sha256(packet).digest())
    return stage, incoming, outgoing


def begin(r, stage, incoming, outgoing):
    return r.owner.prepare(stage, incoming.plan, outgoing.plan, incoming_factory=incoming, outgoing_factory=outgoing)


def drive(r, packet, incoming, outgoing):
    incoming.remote.transfer.send(packet)
    for _ in range(1200):
        incoming.remote.flow.step()
        done = r.owner.step()
        outgoing.remote.flow.step()
        if done and incoming.remote.flow.flow.complete and outgoing.remote.flow.flow.complete: break
        r.clock.advance(50000)
    else: pytest.fail('relay model failed within original clocks')
    return outgoing.remote.transfer.received()


def carry(r, packet, kind):
    stage, incoming, outgoing = prepare_stage(r, packet, kind)
    children = begin(r, stage, incoming, outgoing)
    assert len(r.hardware._forward_children._children) == kind * 2
    received = drive(r, packet, incoming, outgoing)
    receipt = r.owner.finish_stage()
    assert received == packet and not receipt['native_qualified'] and not receipt['private_admission']
    assert all(c.retired and c._terminal_value.journal_snapshot['attempts'] > 0 for c in children)
    return received


def test_independent_a_owner_relays_all_five_ciphertext_stages_and_prepays_ten_children(tmp_path):
    with relay_rig(tmp_path) as r:
        assert not r.state.rows and r.owner.local.agent_id not in (
            r.context.initiator.public_key.hex(), r.context.responder.public_key.hex())
        i, b = r.sessions
        second = b.respond(carry(r, i.start(), 1))
        i.finish(carry(r, second, 2))
        fourth = b.accept_confirmation(carry(r, i.confirm(), 3))
        i.verify_confirmation(carry(r, fourth, 4))
        received = carry(r, i.seal(r.inner), 5)
        assert CONTENT.encode() not in received
        assert open_private(b.open(received), r.clock).content == CONTENT
        body = r.state.rows[r.owner._budget.record_id]['payload']['body']
        assert body['binding']['actor']['agent_id'] == r.local.agent_id
        assert body['binding']['prior_debt'] == asdict(DEBT)
        assert len(body['allocations']) == 10
        assert sum(c['cost']['radio_attempts'] for c in body['allocations']) == 2560
        assert sum(c['cost']['estimated_airtime_us'] for c in body['allocations']) == 600000000
        assert not any(name in vars(r.owner) for name in ('session', '_purpose', '_key', 'plaintext', 'receiver'))
        assert r.owner.snapshot()['completed_stages'] == 5 and not r.owner.snapshot()['native_qualified']
        assert r.hardware.close(2) is True and r.accounting.closed


@pytest.mark.parametrize('scope', RELAY_SCOPES)
def test_each_real_a_scope_is_required_before_any_budget_record(tmp_path, scope):
    with pytest.raises(ForwardNativeRelayError):
        with relay_rig(tmp_path, scopes=tuple(s for s in RELAY_SCOPES if s != scope)): pass


def test_original_full_relay_digest_cannot_be_replaced_by_generic_approval(tmp_path):
    with pytest.raises(ForwardNativeRelayError):
        with relay_rig(tmp_path, approval_change=lambda _: 'f' * 64): pass


def test_initial_native_fact_error_releases_transferred_accounting_source(tmp_path):
    holder = []
    def refuse(r):
        holder.append(r)
        def interrupted(): raise RuntimeError('modeled unavailable configuration')
        r.hardware._config_reader = interrupted
    with pytest.raises(RuntimeError):
        with relay_rig(tmp_path, before_create=refuse): pass
    assert holder[0].accounting.closed and not holder[0].state.rows


def test_failed_accounting_close_remains_unknown_on_later_close(tmp_path, monkeypatch):
    with relay_rig(tmp_path) as r:
        calls = []
        def interrupted(self):
            calls.append(self)
            raise RuntimeError('modeled accounting close interruption')
        # Pin the original source method at construction, as a real implementation
        # that can fail during its eventual close would be pinned.
        with monkeypatch.context() as patch:
            patch.setattr(Accounting, 'close', interrupted)
            with relay_rig(tmp_path / 'second') as other:
                assert other.hardware.close(1) is False
                assert other.hardware.close(1) is False
                assert other.owner.close() is False
                assert other.owner._uncertain and calls == [other.accounting]


@pytest.mark.parametrize('change', ['owner', 'consent', 'peer', 'boot', 'storage', 'epoch', 'expiry', 'debt', 'actual'])
def test_current_native_inputs_and_unknown_debt_are_checked_before_prepayment(tmp_path, change):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        if change == 'owner': r.state.owner_live = False
        elif change == 'consent': r.hardware.config['dispatch_enabled'] = False
        elif change == 'peer': r.hardware.config['peers'].pop()
        elif change == 'boot': r.state.boot = str(uuid.uuid4())
        elif change == 'storage': r.state.instance_id = 'f' * 64
        elif change == 'epoch': r.state.epoch = 'f' * 32
        elif change == 'expiry': r.clock.advance(100000000)
        elif change == 'debt': r.accounting.debt = Cost()
        else: r.accounting.actual = Cost()
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert not r.state.rows and not incoming.opened and not outgoing.opened


def test_second_child_over_budget_keeps_first_full_reservation_and_opens_neither(tmp_path):
    with relay_rig(tmp_path, limit=DEBT.plus(Cost(256, 60000000))) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        body = r.state.rows[r.owner._budget.record_id]['payload']['body']
        assert len(body['allocations']) == 1 and body['allocations'][0]['cost'] == asdict(Cost(256, 60000000))
        assert not incoming.opened and not outgoing.opened


@pytest.mark.parametrize('change', ['cost', 'direction', 'hash', 'peer', 'deadline', 'grant', 'prepared_bytes'])
def test_prepared_exact_protected_child_binding_refuses_before_carrier_open(tmp_path, change):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        if change == 'cost': incoming.plan = replace(incoming.plan, cost=Cost(1, 1))
        elif change == 'direction': incoming.plan = replace(incoming.plan, sends=True)
        elif change == 'hash': incoming.plan = replace(incoming.plan, packet_sha256='f' * 64)
        elif change == 'peer': stage = replace(stage, incoming=replace(stage.incoming,
            session=replace(stage.incoming.session, initiator=PeerID(
                r.context.responder.chain_id, r.context.responder.public_key.hex()))))
        elif change == 'deadline': incoming.plan = replace(incoming.plan,
            application_deadline_monotonic_us=r.intention.application_deadline_monotonic_us + 1)
        elif change == 'grant': incoming.plan = replace(incoming.plan, grant_digest='f' * 64)
        else: (incoming.directories[0] / 'grant.json').write_bytes(b'changed')
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert not incoming.opened and not outgoing.opened


def test_both_opening_children_are_tracked_and_reentrant_shutdown_cannot_claim_cleanup(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        observations = []
        def reenter(owner, child):
            observations.append(r.hardware.close(.01))
            assert len(r.hardware._forward_children._children) == 2
        incoming.before_open = reenter
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert observations == [False] and not outgoing.opened
        assert r.hardware.close(.2) is False
        assert incoming.closed and outgoing.closed and r.owner._uncertain
        body = r.state.rows[r.owner._budget.record_id]['payload']['body']
        assert len(body['allocations']) == 2 and not r.owner.snapshot()['native_qualified']


@pytest.mark.parametrize('change', ['owner', 'factory', 'reentry'])
def test_active_two_leg_observation_drift_fences_before_dispatch(tmp_path, change):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        begin(r, stage, incoming, outgoing)
        def drift(owner, child):
            if change == 'owner': r.state.owner_live = False
            elif change == 'factory': outgoing.current = lambda *args: outgoing.observed()
            else:
                with pytest.raises(ForwardNativeRelayError): owner.step()
        incoming.during_current = drift
        with pytest.raises(ForwardNativeRelayError): r.owner.step()
        assert not any(h.worker.sent for f in (incoming, outgoing) for h in f.handles)
        assert len(r.state.rows[r.owner._budget.record_id]['payload']['body']['allocations']) == 2


def test_expired_owner_contains_both_actual_transfers_without_renewing_or_refunding(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        children = begin(r, stage, incoming, outgoing)
        before = copy.deepcopy(r.state.rows)
        original = tuple(c.plan.application_deadline_monotonic_us for c in children)
        r.clock.advance(100000000)
        assert r.hardware.close(2) is True
        assert all(f.closed and f.disarmed for f in (incoming, outgoing))
        assert all(c.retired and c.transfer.cancelled for c in children)
        assert r.accounting.closed and r.owner.snapshot()['completed_stages'] == 0
        assert r.state.rows == before and original == tuple(c.plan.application_deadline_monotonic_us for c in children)
        assert not any(h.worker.sent for f in (incoming, outgoing) for h in f.handles)
        with pytest.raises(ForwardNativeRelayError): r.owner.step()


def test_flow_final_ack_does_not_substitute_for_independent_native_terminal_proof(tmp_path):
    with relay_rig(tmp_path) as r:
        packet = r.sessions[0].start()
        stage, incoming, outgoing = prepare_stage(r, packet)
        begin(r, stage, incoming, outgoing)
        assert drive(r, packet, incoming, outgoing) == packet
        assert incoming.local.flow.flow.complete and outgoing.local.flow.flow.complete
        incoming.bad_terminal = True
        with pytest.raises(ForwardNativeRelayError): r.owner.finish_stage()
        assert r.owner.snapshot()['completed_stages'] == 0
        assert r.hardware.close(2) is False
        assert incoming.closed and outgoing.closed and not r.accounting.closed
        assert len(r.state.rows[r.owner._budget.record_id]['payload']['body']['allocations']) == 2


def test_native_unknown_attempt_survives_containment_and_cannot_claim_clean_close(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        begin(r, stage, incoming, outgoing)
        journal = incoming.local.journal
        request = journal.reserve(b'opaque-unknown', 1000)
        journal.settle(request, b'opaque-unknown', 'UNKNOWN', 'modeled unknown observation')
        assert r.hardware.close(2) is False
        assert incoming.closed and outgoing.closed and not r.accounting.closed
        assert incoming.journal_final['fenced'] is True
        assert incoming.journal_final['outcomes'] == {'UNKNOWN': 1}
        body = r.state.rows[r.owner._budget.record_id]['payload']['body']
        assert [row['cost'] for row in body['allocations']] == [asdict(Cost(256, 60000000))] * 2


def test_exception_after_open_contains_actual_handles_and_preserves_ambiguous_debt(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        def interrupt(owner, child): raise RuntimeError('modeled return interruption')
        incoming.after_open = interrupt
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert incoming.opened and not incoming.closed and not outgoing.opened
        assert len(r.hardware._forward_children._children) == 2
        assert r.hardware.close(2) is False
        assert incoming.closed and outgoing.closed
        assert all(h.flow.closed and h.authority.closed and h.journal.lock_fd is None for h in incoming.handles)
        assert all(c.unknown for c in r.hardware._forward_children._children)
        assert len(r.state.rows[r.owner._budget.record_id]['payload']['body']['allocations']) == 2


def test_factory_prepared_evidence_is_rechecked_before_second_open(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        def change_other(owner, child):
            (outgoing.directories[0] / 'grant.json').write_bytes(b'different protected grant')
        incoming.after_open = change_other
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert incoming.opened and not outgoing.opened
        assert len(r.state.rows[r.owner._budget.record_id]['payload']['body']['allocations']) == 2
        assert r.hardware.close(2) is False and incoming.closed and outgoing.closed


def test_callback_accounting_source_swap_cannot_create_a_new_approval(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        def change_source(): r.owner.accounting = Accounting(r)
        r.accounting.hook = change_source
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        assert not incoming.opened and not outgoing.opened and not r.state.rows


def test_recovered_full_reservations_do_not_recreate_in_memory_admission(tmp_path):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        original = r.owner
        # Failure after both CAS writes retains the original namespace, even
        # though neither factory got an opportunity to open a carrier.
        def interrupt(owner, child): raise RuntimeError('before open')
        incoming.before_open = interrupt
        with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(Exception):
            ForwardNativeRelay(r.hardware, r.authority, original.native_context,
                description=r.description, intention=r.intention, limits=LIMITS,
                accounting=Accounting(r), boot_reader=lambda: r.state.boot, clock_reader=r.clock)
        assert r.state.rows == before and not incoming.opened and not outgoing.opened


@pytest.mark.parametrize('change', ['attempt_type', 'outcome_total', 'unknown', 'airtime', 'disarm', 'pin'])
def test_inconsistent_terminal_records_never_prove_native_retirement(tmp_path, change):
    with relay_rig(tmp_path) as r:
        stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
        begin(r, stage, incoming, outgoing)
        def altered(value):
            final = copy.deepcopy(value.journal_snapshot)
            if change == 'attempt_type': final['attempts'] = 0.0
            elif change == 'outcome_total': final['outcomes'] = {'RF_DONE': 1}
            elif change == 'unknown': final.update(attempts=1, outcomes={'UNKNOWN': 1})
            elif change == 'airtime': final['reserved_estimated_airtime_us'] = 60000001
            elif change == 'disarm': return replace(value, radio_disarmed=False)
            else: return replace(value, journal_sha256='f' * 64)
            return replace(value, journal_snapshot=final, journal_sha256=hashlib.sha256(_canonical(final)).hexdigest())
        incoming.terminal_transform = altered
        assert r.hardware.close(2) is False
        assert incoming.closed and outgoing.closed and not r.accounting.closed
        assert not r.owner.snapshot()['native_qualified']


def test_quiescent_shutdown_thread_closes_core_both_legs_then_accounting_with_one_use_permits(tmp_path):
    ready = threading.Event(); release = threading.Event(); holder = {}; failures = []
    def producer():
        try:
            with relay_rig(tmp_path) as r:
                stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
                children = begin(r, stage, incoming, outgoing)
                holder.update(r=r, children=children, incoming=incoming, outgoing=outgoing)
                ready.set(); assert release.wait(15)
        except BaseException as error: failures.append(error); ready.set()
    thread = threading.Thread(target=producer); thread.start()
    try:
        assert ready.wait(15) and not failures
        r = holder['r']
        with pytest.raises(ForwardNativeRelayError): r.owner.close()
        assert r.hardware.close(2) is True
        assert all(c.retired and c.transfer.flow.closed and c.transfer.authority.closed for c in holder['children'])
        assert all(holder[k].contained_on == [threading.get_ident()] for k in ('incoming', 'outgoing'))
        assert r.accounting.closed and r.owner._core_closed
        before = copy.deepcopy(r.state.rows)
        with pytest.raises(ForwardChildrenError): r.owner._registry_close(r.hardware._forward_children)
        with pytest.raises(ForwardNativeRelayError): r.owner.step()
        assert r.state.rows == before
    finally:
        release.set(); thread.join(15)
    assert not thread.is_alive() and not failures
    assert holder['r'].accounting.closed and holder['r'].owner._core_closed


def test_shutdown_never_contains_a_still_opening_factory_then_drains_both_tracked_legs(tmp_path):
    entered = threading.Event(); unwind = threading.Event(); done = threading.Event(); release = threading.Event()
    holder = {}; failures = []
    def producer():
        try:
            with relay_rig(tmp_path) as r:
                stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
                holder.update(r=r, incoming=incoming, outgoing=outgoing)
                def opening(owner, child): entered.set(); assert unwind.wait(15)
                incoming.before_open = opening
                with pytest.raises(ForwardNativeRelayError): begin(r, stage, incoming, outgoing)
                done.set(); assert release.wait(15)
        except BaseException as error: failures.append(error); entered.set(); done.set()
    thread = threading.Thread(target=producer); thread.start()
    try:
        assert entered.wait(15) and not failures
        r = holder['r']
        assert r.hardware.close(.02) is False
        assert all(holder[k].contained_on == [] for k in ('incoming', 'outgoing'))
        unwind.set(); assert done.wait(15) and not failures
        assert r.hardware.close(2) is False
        assert all(holder[k].contained_on == [threading.get_ident()] for k in ('incoming', 'outgoing'))
        assert all(holder[k].closed and not holder[k].opened for k in ('incoming', 'outgoing'))
        assert len(r.state.rows[r.owner._budget.record_id]['payload']['body']['allocations']) == 2
    finally:
        unwind.set(); release.set(); thread.join(15)
    assert not thread.is_alive() and not failures


def test_registered_component_permit_is_exact_target_one_use_and_source_idle(tmp_path):
    observations = []
    class CheckedAccounting(Accounting):
        def close_from_registry(self, permit):
            assert permit.original_owner == self.r.owner._owner and permit.resource is self.r.hardware
            with pytest.raises(ForwardChildrenError): permit.consume(self.r.owner._relay)
            assert not permit._used
            self._busy = 1
            try:
                with pytest.raises(ForwardChildrenError): permit.consume(self)
                assert not permit._used
            finally: self._busy = 0
            assert super().close_from_registry(permit) is True
            with pytest.raises(ForwardChildrenError): permit.consume(self)
            observations.append(permit)
            return True
    ready = threading.Event(); release = threading.Event(); holder = {}; failures = []
    def producer():
        try:
            with relay_rig(tmp_path, accounting_factory=CheckedAccounting) as r:
                holder['r'] = r; ready.set(); assert release.wait(15)
        except BaseException as error: failures.append(error); ready.set()
    thread = threading.Thread(target=producer); thread.start()
    try:
        assert ready.wait(15) and not failures
        r = holder['r']; registry = r.hardware._forward_children
        with pytest.raises(ForwardChildrenError): registry.cleanup_permit(r.owner, r.accounting)
        forged = ForwardClosePermit(registry, r.owner, r.accounting, object(), time.monotonic() + 1)
        with pytest.raises(AttributeError): forged.consume = lambda *args: True
        with pytest.raises(ForwardChildrenError): forged.consume(r.accounting)
        redirected = SimpleNamespace(consume_cleanup_permit=lambda *args: True)
        forged._registry = redirected
        with pytest.raises(ForwardChildrenError): forged.consume(r.accounting)
        forged._registry = registry
        registry.consume_cleanup_permit = lambda *args: True
        try:
            with pytest.raises(ForwardChildrenError): forged.consume(r.accounting)
        finally: del registry.consume_cleanup_permit
        assert r.hardware.close(2) is True
        assert len(observations) == 1 and observations[0]._used
        with pytest.raises(ForwardChildrenError): observations[0].consume(r.accounting)
    finally:
        release.set(); thread.join(15)
    assert not thread.is_alive() and not failures


def test_busy_independent_accounting_observer_blocks_registry_permits(tmp_path):
    with relay_rig(tmp_path) as r:
        r.accounting._busy = 1
        assert r.hardware.close(.02) is False
        assert not r.owner._core_closed and not r.accounting.closed
        assert not r.hardware._forward_children._consumed_owner_permits
        assert not r.hardware._forward_children._component_permits
        r.accounting._busy = 0
        assert r.hardware.close(2) is True and r.accounting.closed


def test_optional_accounting_drain_absence_is_pinned_and_has_no_foreign_fallback(tmp_path, monkeypatch):
    class OriginalThreadAccounting(Accounting): close_from_registry = None
    ready = threading.Event(); release = threading.Event(); holder = {}; failures = []
    def producer():
        try:
            with relay_rig(tmp_path, accounting_factory=OriginalThreadAccounting) as r:
                holder['r'] = r; ready.set(); assert release.wait(15)
        except BaseException as error: failures.append(error); ready.set()
    thread = threading.Thread(target=producer); thread.start()
    try:
        assert ready.wait(15) and not failures
        r = holder['r']
        assert r.hardware.close(2) is False
        assert r.owner._core_closed and not r.accounting.closed
    finally:
        release.set(); thread.join(15)
    assert not thread.is_alive() and not failures and holder['r'].accounting.closed
    with relay_rig(tmp_path / 'pinned', accounting_factory=OriginalThreadAccounting) as r:
        monkeypatch.setattr(OriginalThreadAccounting, 'close_from_registry', Accounting.close_from_registry)
        with pytest.raises(ForwardNativeRelayError): r.owner.access_check()
        assert not r.state.rows


def test_actual_held_sqlite_reader_closes_last_on_registry_shutdown_thread(root_observation):
    from lantern_meshticulum.sage.forward_native_accounting import HeldParentAccounting
    from tests.test_pilot_parent_snapshot import fixture as ledger_fixture, scope
    directory = Path(tempfile.mkdtemp(prefix='.relay-held-control-', dir=Path(__file__).parents[2]))
    ready = threading.Event(); release = threading.Event(); holder = {}; failures = []
    try:
        ledger = directory / 'ledger'; ledger.mkdir(mode=0o700)
        data = ledger_fixture(ledger); native = data['natives']['unit-a']
        original = {p: p.read_bytes() for p in native['path'].parent.iterdir() if p.is_file()}
        def accounting(r):
            selected = replace(scope(data), current_host_boot_id=r.intention.host_boot_id,
                observation_expires_epoch_us=r.clock()[0] + 50000000)
            return HeldParentAccounting(r.hardware, r.intention, native['state'], selected,
                clock_reader=r.clock, boot_reader=lambda: r.state.boot)
        def producer():
            try:
                with relay_rig(directory / 'relay', accounting_factory=accounting,
                               limit=Cost(10000, 1000000000, 100000, 10000000, 10000000)) as r:
                    stage, incoming, outgoing = prepare_stage(r, r.sessions[0].start())
                    children = begin(r, stage, incoming, outgoing)
                    holder.update(r=r, children=children, incoming=incoming, outgoing=outgoing,
                        fds=tuple(r.accounting._reader._tokens), proof=r.accounting.evidence(),
                        rows=copy.deepcopy(r.state.rows), owner=r.owner._owner,
                        source_owner=r.accounting._owner, reader_owner=r.accounting._reader._owner)
                    ready.set(); assert release.wait(30)
            except BaseException as error: failures.append(error); ready.set()
        thread = threading.Thread(target=producer); thread.start()
        try:
            assert ready.wait(30) and not failures
            r = holder['r']
            assert r.accounting.close() is False
            assert r.hardware.close(5) is True
            assert all(c.retired and c.transfer.flow.closed and c.transfer.authority.closed for c in holder['children'])
            assert all(holder[k].contained_on == [threading.get_ident()] for k in ('incoming', 'outgoing'))
            assert r.accounting._closed and r.owner._source_closed and r.owner._core_closed
            assert r.owner._owner == holder['owner'] and r.accounting._owner == holder['source_owner']
            assert r.accounting._reader._owner == holder['reader_owner']
            assert r.state.rows == holder['rows'] and holder['proof']['actual_usage'] is None
            assert not holder['proof']['complete_global_debt'] and not holder['proof']['private_dispatch_authority']
            assert {p: p.read_bytes() for p in original} == original
            for fd in holder['fds']:
                with pytest.raises(OSError): os.fstat(fd)
        finally:
            release.set(); thread.join(30)
        assert not thread.is_alive() and not failures
    finally: shutil.rmtree(directory)

