"""Actual signed SDK and owned PTY; native facts/backend/firmware are models."""
from collections import Counter
from contextlib import contextmanager
import copy
import sys

import pytest
from sage_sdk.models import WorkflowJournalRecord, WorkflowJournalPage

from lantern_meshticulum.sage import forward_native_relay_radio as relay_radio_module
from lantern_meshticulum.sage import radio_native_adoption as endpoint_adoption_module
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner
from lantern_meshticulum.sage.forward_native_relay_radio import RelayRadioAdoption, RelayRadioError
from lantern_meshticulum.sage.message_contacts import RecipientID
from lantern_meshticulum.sage.message_storage import MessageStorageProbe
from lantern_meshticulum.sage.radio_native_adoption import NativeRadioAdoption, RadioAdoptionError
from lantern_meshticulum.sage.workflow_store import WorkflowStore, WorkflowUnavailable, WorkflowNotFound
from tests.integrations.sage.test_forward_radio import factory_model
from tests.integrations.sage.test_forward_native_relay_radio import prepared_rig
from tests.integrations.sage.test_radio_native_adoption import model
from tests.integrations.sage.test_workflow_store import setup, FIRST


@contextmanager
def active(tmp_path, monkeypatch):
    with factory_model(tmp_path, monkeypatch) as r:
        r.runner = r.stage.prepare(payload=r.packet)
        r.adoption = r.stage.adoption
        r.saved_payment = copy.deepcopy(r.state.rows[r.op.budget.record_id])
        yield r
        assert r.state.rows[r.op.budget.record_id] == r.saved_payment
        assert r.stage.journal.snapshot()['attempts'] == 0
        assert not r.stage.report()['native_qualified']


def test_eight_fresh_guards_six_store_callbacks_and_both_epochs_each_observation(tmp_path, monkeypatch):
    methods = {
        NativeRadioAdoption._guard: 'guard', NativeRadioAdoption._read_guard: 'read_guard',
        NativeRadioAdoption._assets: 'assets', ForwardNativeOwner.phase_check: 'phase',
        WorkflowStore.get: 'get', WorkflowStore._invoke: 'invoke',
        WorkflowStore._snapshot: 'snapshot', WorkflowStore._access: 'access',
    }
    codes = {method.__code__: name for method, name in methods.items()}
    with active(tmp_path, monkeypatch) as r:
        count = Counter(); requests = []
        r.client._client.event_hooks['response'].append(
            lambda response: requests.append((response.request.method, response.request.url.path)))
        old = sys.getprofile()
        def entered(frame, event, arg):
            if event == 'call' and frame.f_code in codes: count[codes[frame.f_code]] += 1
        try:
            sys.setprofile(entered)
            assert r.adoption.current() is True
            assert r.adoption.current() is True
        finally: sys.setprofile(old)
        assert count == dict(guard=16, read_guard=12, assets=4, phase=16,
                             get=2, invoke=2, snapshot=4, access=12)
        assert requests.count(('GET', '/v1/workflows/' + r.adoption.record_id)) == 2
        assert requests.count(('GET', '/v1/messages/storage')) == 20
        assert len(requests) == 22


def test_relay_retains_shared_fd_checks_and_nine_guards_without_endpoint_authority(tmp_path, monkeypatch):
    methods = {RelayRadioAdoption._guard: 'guard', WorkflowStore._access: 'access',
               WorkflowStore._snapshot: 'snapshot'}
    codes = {method.__code__: name for method, name in methods.items()}
    with prepared_rig(tmp_path, monkeypatch) as r:
        adoption = RelayRadioAdoption(r.owner, r.profile, r.directory, r.wire.transports[0])
        try:
            assert '_read_return' not in vars(adoption) and '_read_return_ref' not in vars(adoption)
            assert adoption._bound() is None
            count = Counter(); old = sys.getprofile()
            def entered(frame, event, arg):
                if event == 'call' and frame.f_code in codes: count[codes[frame.f_code]] += 1
            try:
                sys.setprofile(entered)
                assert adoption.observe() is True
            finally: sys.setprofile(old)
            assert count == dict(guard=9, access=6, snapshot=2)
            with pytest.raises(RadioAdoptionError): NativeRadioAdoption._bound(adoption)
            # A rejected endpoint entry neither changes relay policy nor gives
            # it an endpoint seal; the original independent observation works.
            assert adoption.observe() is True
            assert '_read_return' not in vars(adoption) and '_read_return_ref' not in vars(adoption)
            assert not r.wire.frames[0] and not r.wire.armed[0]
        finally: adoption.release()


@pytest.mark.parametrize('field', ['_read_return', '_read_return_ref'])
def test_endpoint_without_return_seal_still_refuses_and_stays_uncertain(tmp_path, monkeypatch, field):
    with active(tmp_path, monkeypatch) as r:
        saved = vars(r.adoption).pop(field)
        try: assert r.adoption.current() is False and r.adoption._uncertain
        finally: vars(r.adoption)[field] = saved
        assert r.adoption.current() is False


def test_endpoint_refuses_replaced_shared_reader_before_it_can_restore_itself(tmp_path, monkeypatch):
    with active(tmp_path, monkeypatch) as r:
        original = endpoint_adoption_module._held_radio_binding; executed = []
        def replacement(adoption):
            executed.append(True)
            endpoint_adoption_module._held_radio_binding = original
            return original(adoption)
        with monkeypatch.context() as patch:
            patch.setattr(endpoint_adoption_module, '_held_radio_binding', replacement)
            assert r.adoption.current() is False and r.adoption._uncertain
            assert not executed
        assert r.adoption.current() is False


def test_relay_refuses_replaced_shared_reader_before_it_can_restore_itself(tmp_path, monkeypatch):
    with prepared_rig(tmp_path, monkeypatch) as r:
        adoption = RelayRadioAdoption(r.owner, r.profile, r.directory, r.wire.transports[0])
        try:
            original = relay_radio_module._held_radio_binding; executed = []
            def replacement(value):
                executed.append(True)
                relay_radio_module._held_radio_binding = original
                return original(value)
            with monkeypatch.context() as patch:
                patch.setattr(relay_radio_module, '_held_radio_binding', replacement)
                with pytest.raises(RelayRadioError): adoption.observe()
                assert not executed
            assert adoption.observe() is True
        finally: adoption.release()


@pytest.mark.parametrize('where,field', [
    ('store', 'get'), ('store', '_invoke'), ('store', '_snapshot'), ('store', '_access'),
    ('store', '_record'), ('probe', 'snapshot'), ('probe', '_binding'),
])
@pytest.mark.parametrize('placement', ['instance', 'class'])
def test_replaced_read_methods_refuse_before_substitute_can_restore_itself(tmp_path, monkeypatch, where, field, placement):
    with active(tmp_path, monkeypatch) as r:
        target = r.adoption.store if where == 'store' else r.adoption.store.storage
        original = getattr(type(target), field); executed = []
        def replacement(*args, **kwargs):
            executed.append(True)
            if placement == 'instance': target.__dict__.pop(field, None)
            else: setattr(type(target), field, original)
            return True
        with monkeypatch.context() as patch:
            patch.setattr(target if placement == 'instance' else type(target), field, replacement)
            assert r.adoption.current() is False
            assert executed == [] and r.adoption._uncertain
        assert r.adoption.current() is False


@pytest.mark.parametrize('where,field', [
    ('store', 'client'), ('store', 'storage'), ('store', '_actor'), ('store', '_endpoint'),
    ('probe', 'client'), ('probe', 'actor'), ('probe', 'endpoint'),
    ('actor', 'chain_id'), ('actor', 'agent_id'),
])
def test_class_properties_refuse_without_invoking_getter(tmp_path, monkeypatch, where, field):
    with active(tmp_path, monkeypatch) as r:
        store = r.adoption.store
        target = {'store': store, 'probe': store.storage, 'actor': store.storage.actor}[where]
        executed = []
        def getter(value): executed.append(True); return object()
        with monkeypatch.context() as patch:
            patch.setattr(type(target), field, property(getter), raising=False)
            assert r.adoption.current() is False
            assert not executed and r.adoption._uncertain
        assert r.adoption.current() is False


@pytest.mark.parametrize('field', ['__eq__', '__ne__', '__getattribute__', '__getattr__'])
def test_actor_comparison_or_lookup_substitution_never_runs(tmp_path, monkeypatch, field):
    with active(tmp_path, monkeypatch) as r:
        executed = []
        def changed(*args): executed.append(True); return True
        with monkeypatch.context() as patch:
            patch.setattr(RecipientID, field, changed, raising=False)
            assert r.adoption.current() is False
            assert not executed and r.adoption._uncertain
        assert r.adoption.current() is False


@pytest.mark.parametrize('change', ['actor', 'actor-scalar', 'endpoint', 'store-client', 'probe-client', 'probe'])
def test_original_raw_refs_and_scalar_values_are_bound(tmp_path, monkeypatch, change):
    with active(tmp_path, monkeypatch) as r:
        store = r.adoption.store; probe = store.storage; actor = probe.actor
        with monkeypatch.context() as patch:
            if change == 'actor':
                patch.setattr(probe, 'actor', RecipientID(actor.chain_id, actor.agent_id))
                patch.setattr(store, '_actor', probe.actor)
            elif change == 'actor-scalar':
                before = actor.chain_id
                object.__setattr__(actor, 'chain_id', 'changed-chain')
            elif change == 'endpoint':
                patch.setattr(probe, 'endpoint', 'http://127.0.0.1:19999')
                patch.setattr(store, '_endpoint', probe.endpoint)
            elif change == 'store-client': patch.setattr(store, 'client', object())
            elif change == 'probe-client': patch.setattr(probe, 'client', object())
            else: patch.setattr(store, 'storage', object())
            try: assert r.adoption.current() is False and r.adoption._uncertain
            finally:
                if change == 'actor-scalar': object.__setattr__(actor, 'chain_id', before)
        assert r.adoption.current() is False


@pytest.mark.parametrize('change', ['store-method', 'probe-property', 'actor-equality'])
def test_constructor_cannot_seal_a_replaced_implementation(tmp_path, monkeypatch, change):
    with model(tmp_path) as r:
        executed = []
        def changed(*args): executed.append(True); return True
        with monkeypatch.context() as patch:
            if change == 'store-method': patch.setattr(WorkflowStore, 'get', changed)
            elif change == 'probe-property': patch.setattr(MessageStorageProbe, 'client', property(changed), raising=False)
            else: patch.setattr(RecipientID, '__eq__', changed)
            with pytest.raises(RadioAdoptionError):
                NativeRadioAdoption(r.owner, r.child, r.directory, r.transport)
            assert executed == []


@pytest.mark.parametrize('change', ['scope', 'expiry', 'deadline', 'storage-epoch', 'reentry'])
def test_actual_sdk_return_callback_keeps_fresh_authority_and_sticky_refusal(tmp_path, monkeypatch, change):
    with active(tmp_path, monkeypatch) as r:
        mutated = []; now = r.state.now; live = r.state.owner_live; instance = r.state.instance_id
        def response(value):
            if value.request.url.path != '/v1/workflows/' + r.adoption.record_id: return
            mutated.append(True)
            if change == 'scope': r.state.owner_live = False
            elif change == 'expiry': r.state.now = (r.context.expires_epoch_us, r.state.now[1])
            elif change == 'deadline': r.state.now = (r.state.now[0], r.profile.child.application_deadline_monotonic_us)
            elif change == 'storage-epoch': r.state.instance_id = 'c' * 64
            else: assert r.adoption.current() is False
        hooks = r.client._client.event_hooks['response']; hooks.append(response)
        try:
            assert r.adoption.current() is False
            assert mutated == [True] and r.adoption._uncertain
        finally:
            hooks.remove(response); r.state.now = now; r.state.owner_live = live; r.state.instance_id = instance
        assert r.adoption.current() is False


@pytest.mark.parametrize('placement', ['instance', 'class'])
def test_hostile_sdk_model_cannot_redirect_final_access_and_restore_method(tmp_path, monkeypatch, placement):
    with active(tmp_path, monkeypatch) as r:
        store = r.adoption.store; original_dump = WorkflowJournalRecord.model_dump
        original_access = WorkflowStore._access; decoded = []; redirected = []
        with monkeypatch.context() as patch:
            def redirect(*args):
                redirected.append(True)
                if placement == 'instance': store.__dict__.pop('_access', None)
                else: WorkflowStore._access = original_access
                return True
            def dump(value, *args, **kwargs):
                result = original_dump(value, *args, **kwargs)
                if value.record_id == r.adoption.record_id:
                    decoded.append(True)
                    patch.setattr(store if placement == 'instance' else WorkflowStore, '_access', redirect)
                return result
            patch.setattr(WorkflowJournalRecord, 'model_dump', dump)
            assert r.adoption.current() is False
            assert decoded == [True] and redirected == [] and r.adoption._uncertain
        assert r.adoption.current() is False


@pytest.mark.parametrize('field', ['actor', 'endpoint', 'client'])
def test_model_callback_cannot_install_a_post_guard_probe_property(tmp_path, monkeypatch, field):
    with active(tmp_path, monkeypatch) as r:
        original_dump = WorkflowJournalRecord.model_dump; decoded = []; executed = []
        with monkeypatch.context() as patch:
            def getter(value): executed.append(True); return object()
            def dump(value, *args, **kwargs):
                result = original_dump(value, *args, **kwargs)
                if value.record_id == r.adoption.record_id:
                    decoded.append(True)
                    patch.setattr(MessageStorageProbe, field, property(getter), raising=False)
                return result
            patch.setattr(WorkflowJournalRecord, 'model_dump', dump)
            assert r.adoption.current() is False
            assert decoded == [True] and executed == [] and r.adoption._uncertain


@pytest.mark.parametrize('missing', [False, True])
@pytest.mark.parametrize('placement', ['instance', 'class'])
def test_generic_get_retains_original_final_access_after_record_or_page_decode(setup, monkeypatch, missing, placement):
    s = setup; s.records[FIRST] = s.record(); allowed = [True]; redirected = []; final_checks = []
    cls = WorkflowJournalPage if missing else WorkflowJournalRecord
    original_dump = cls.model_dump; original_access = WorkflowStore._access
    identifier = '00000000-0000-4000-8000-000000000003' if missing else FIRST
    def access(): final_checks.append(allowed[0]); return allowed[0]
    def redirect(*args):
        redirected.append(True)
        if placement == 'instance': s.store.__dict__.pop('_access', None)
        else: WorkflowStore._access = original_access
    def dump(value, *args, **kwargs):
        result = original_dump(value, *args, **kwargs)
        allowed[0] = False
        monkeypatch.setattr(s.store if placement == 'instance' else WorkflowStore, '_access', redirect)
        return result
    monkeypatch.setattr(cls, 'model_dump', dump)
    with pytest.raises(WorkflowUnavailable) as error:
        s.store.get(identifier, kind='mesh_outbound', access_check=access)
    assert type(error.value) is WorkflowUnavailable and not isinstance(error.value, WorkflowNotFound)
    assert not redirected and final_checks[-1] is False
