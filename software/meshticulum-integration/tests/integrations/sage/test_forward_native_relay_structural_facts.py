"""Admitted scalar snapshots are structural pins, never permission caches."""
from dataclasses import asdict
import weakref

import pytest

from lantern_meshticulum.sage import forward_native_relay as native
from tests.integrations.sage.test_forward_native_relay import relay_rig, Accounting, DEBT


FIELDS = (
    ('context.initiator', 'chain_id'), ('context.initiator', 'public_key'),
    ('context.responder', 'chain_id'), ('context.responder', 'public_key'),
    *(('context', name) for name in ('session_id', 'binding_digest', 'issued_epoch_us',
        'expires_epoch_us', 'max_records', 'initiator_noise_public_key', 'responder_noise_public_key')),
    ('description.relay_peer', 'chain_id'), ('description.relay_peer', 'agent_id'),
    *(('description.deadlines', name) for name in ('application_deadline_monotonic',
        'authority_deadline_monotonic', 'authority_expires_epoch')),
    ('description', 'max_packet_bytes'),
    *(('intention', name) for name in ('operation_id', 'packet_sha256', 'packet_bytes',
        'authority_digest', 'host_boot_id', 'application_expires_epoch_us', 'application_deadline_monotonic_us')),
    *(('limits', name) for name in ('radio_attempts', 'estimated_airtime_us', 'ip_frames',
        'tx_stream_bytes', 'rx_stream_bytes')),
    ('local', 'chain_id'), ('local', 'agent_id'),
    ('', 'config_revision'), ('', 'broker_id'), ('', 'storage_epoch'),
)


def target(owner, path):
    for name in path.split('.') if path else ():
        owner = getattr(owner, name)
    return owner


@pytest.mark.parametrize('path,name', FIELDS)
def test_each_original_fact_is_still_checked_at_every_available(tmp_path, path, name):
    with relay_rig(tmp_path) as r:
        obj = target(r.owner, path)
        original = getattr(obj, name)
        replacement = original + 1 if type(original) in (int, float) else \
            bytes([original[0] ^ 1]) + original[1:] if type(original) is bytes else original + 'x'
        object.__setattr__(obj, name, replacement)
        try:
            with pytest.raises(native.ForwardNativeRelayError): r.owner._available()
        finally:
            object.__setattr__(obj, name, original)
        r.owner._available()
        assert not r.state.rows and r.accounting.debt == DEBT


@pytest.mark.parametrize('kind', ['int_float', 'container', 'bytes_type', 'consistent_raw'])
def test_json_distinctions_and_original_raw_anchor_are_preserved(tmp_path, kind):
    with relay_rig(tmp_path) as r:
        owner = r.owner
        obj, name = (owner.limits, 'radio_attempts') if kind != 'bytes_type' else (owner.context, 'session_id')
        original, raw = getattr(obj, name), owner._facts
        changed = float(original) if kind == 'int_float' else \
            [original] if kind == 'container' else bytearray(original) if kind == 'bytes_type' else original + 1
        object.__setattr__(obj, name, changed)
        if kind == 'consistent_raw':
            owner._facts = native._canonical(dict(description=native.description_value(owner.description),
                intention=owner.intention.to_dict(), limits=asdict(owner.limits), local=asdict(owner.local),
                config=owner.config_revision, broker=owner.broker_id, storage=owner.storage_epoch))
        try:
            with pytest.raises(native.ForwardNativeRelayError): owner._available()
        finally:
            object.__setattr__(obj, name, original)
            owner._facts = raw


@pytest.mark.parametrize('value', [0, 1])
def test_same_value_boolean_is_not_an_admitted_integer(tmp_path, value):
    class EmptyAccounting(Accounting):
        def __init__(self, rig):
            super().__init__(rig)
            self.debt = native.Cost()
    with relay_rig(tmp_path, limit=native.Cost(value, 1, 1, 1, 1), accounting_factory=EmptyAccounting) as r:
        object.__setattr__(r.owner.limits, 'radio_attempts', bool(value))
        try:
            with pytest.raises(native.ForwardNativeRelayError): r.owner._available()
        finally:
            object.__setattr__(r.owner.limits, 'radio_attempts', value)


@pytest.mark.parametrize('member', ['reader', 'description', 'canonical', 'asdict', 'intention', 'peer', 'digest', 'prologue'])
@pytest.mark.parametrize('change', ['code', 'defaults'])
def test_serializer_source_changes_cannot_skip_the_original_proof(tmp_path, monkeypatch, member, change):
    with relay_rig(tmp_path) as r:
        functions = dict(reader=native._structural_values, description=native.description_value,
            canonical=native._canonical, asdict=native.asdict, intention=type(r.intention).to_dict,
            peer=type(r.context.initiator).to_dict, digest=type(r.context).digest.fget,
            prologue=type(r.context).prologue.fget)
        with monkeypatch.context() as patch:
            function = functions[member]
            patch.setattr(function, '__code__' if change == 'code' else '__defaults__',
                          (lambda *args, **kwargs: True).__code__ if change == 'code' else (None,))
            with pytest.raises(native.ForwardNativeRelayError): r.owner._available()


def test_keyword_mutation_instance_shadow_and_readonly_slot(tmp_path, monkeypatch):
    with relay_rig(tmp_path) as r:
        with monkeypatch.context() as patch:
            patch.setitem(native.asdict.__kwdefaults__, 'dict_factory', list)
            with pytest.raises(native.ForwardNativeRelayError): r.owner._available()
        object.__setattr__(r.intention, 'to_dict', lambda: {})
        try:
            with pytest.raises(native.ForwardNativeRelayError): r.owner._available()
        finally:
            object.__delattr__(r.intention, 'to_dict')
        anchor = r.owner._ForwardNativeRelay__structural_guard
        for action in (lambda: setattr(r.owner, '_ForwardNativeRelay__structural_guard', None),
                       lambda: delattr(r.owner, '_ForwardNativeRelay__structural_guard')):
            with pytest.raises(native.ForwardNativeRelayError): action()
        r.owner.__dict__['_ForwardNativeRelay__structural_guard'] = (lambda: True,)
        assert r.owner._ForwardNativeRelay__structural_guard is anchor
        assert weakref.ref(r.owner)() is r.owner
        r.owner._available()


@pytest.mark.parametrize('part', ['local', 'deadlines', 'limits'])
def test_dataclass_field_shape_cannot_silently_change(tmp_path, monkeypatch, part):
    with relay_rig(tmp_path) as r:
        cls = type(r.owner.local if part == 'local' else r.description.deadlines if part == 'deadlines' else r.owner.limits)
        with monkeypatch.context() as patch:
            changed = dict(cls.__dataclass_fields__)
            changed.pop(next(iter(changed)))
            patch.setattr(cls, '__dataclass_fields__', changed)
            with pytest.raises(native.ForwardNativeRelayError): r.owner._available()


@pytest.mark.parametrize('delegate', ['json', 'hash', 'asdict_inner', 'fields', 'deepcopy', 'encoder'])
def test_semantic_serializer_delegate_substitution_refuses(tmp_path, monkeypatch, delegate):
    with relay_rig(tmp_path) as r:
        context = type(r.context).prologue.fget.__globals__
        dataclass = native.asdict.__globals__
        targets = dict(json=(context['json'], 'dumps'), hash=(context['hashlib'], 'sha256'),
            asdict_inner=(dataclass, '_asdict_inner'), fields=(dataclass, 'fields'),
            deepcopy=(dataclass['copy'], 'deepcopy'), encoder=(context['json'].JSONEncoder, 'encode'))
        obj, name = targets[delegate]
        with monkeypatch.context() as patch:
            if type(obj) is dict: patch.setitem(obj, name, lambda *args, **kwargs: {})
            else: patch.setattr(obj, name, lambda *args, **kwargs: {})
            with pytest.raises(native.ForwardNativeRelayError): r.owner._available()


@pytest.mark.parametrize('name', ['original_values', 'scalar_reader'])
def test_captured_field_or_reader_cell_cannot_be_replaced(tmp_path, name):
    with relay_rig(tmp_path) as r:
        check = r.owner._ForwardNativeRelay__structural_guard[0]
        cells = dict(zip(check.__code__.co_freevars, check.__closure__))
        cell = cells[name]
        original = cell.cell_contents
        cell.cell_contents = tuple([*original]) if name == 'original_values' else lambda owner: ()
        try:
            with pytest.raises(native.ForwardNativeRelayError): r.owner._available()
        finally:
            cell.cell_contents = original


def test_encoder_constructor_substitution_refuses(tmp_path, monkeypatch):
    with relay_rig(tmp_path) as r:
        json = type(r.context).prologue.fget.__globals__['json']
        class IsolatedEncoder(json.JSONEncoder):
            pass
        with monkeypatch.context() as patch:
            # CPython cannot safely restore the original type's __new__ slot by
            # deleting it. This test-local subclass leaves that type untouched.
            patch.setattr(json, 'JSONEncoder', IsolatedEncoder)
            check = native._structural_guard(r.owner)[0]
            check()
            patch.setattr(IsolatedEncoder, '__new__', staticmethod(lambda *args, **kwargs: None), raising=False)
            with pytest.raises(native.ForwardNativeRelayError): check()


@pytest.mark.parametrize('failure', ['new_optional_global', 'primitive_handler'])
def test_python311_missing_atomic_global_is_sealed_without_requiring_it(tmp_path, monkeypatch, failure):
    with relay_rig(tmp_path) as r:
        namespace = native.asdict.__globals__
        with monkeypatch.context() as patch:
            patch.delitem(namespace, '_ATOMIC_TYPES', raising=False)
            check = native._structural_guard(r.owner)[0]
            check()  # Pure structural proof; never native permission or IO.
            if failure == 'new_optional_global':
                patch.setitem(namespace, '_ATOMIC_TYPES', frozenset((str, int, float)))
            else:
                patch.setitem(namespace['copy']._deepcopy_dispatch, str, lambda value, memo: 'changed')
            with pytest.raises(native.ForwardNativeRelayError): check()


def test_original_accounting_callback_and_post_callback_mutation_still_run(tmp_path):
    with relay_rig(tmp_path) as r:
        original = r.intention.packet_bytes
        calls = []
        def mutate():
            calls.append(True)
            object.__setattr__(r.intention, 'packet_bytes', original + 1)
        r.accounting.hook = mutate
        try:
            with pytest.raises(native.ForwardNativeRelayError): r.owner.access_check()
            assert calls == [True] and not r.state.rows and r.accounting.debt == DEBT
        finally:
            r.accounting.hook = None
            object.__setattr__(r.intention, 'packet_bytes', original)
