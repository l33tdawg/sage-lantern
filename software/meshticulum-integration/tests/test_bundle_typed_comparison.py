"""Compiled comparisons retain fresh mutable values and every source boundary."""
from copy import deepcopy

import pytest

from lantern_meshticulum.bench import pilot_bundle as bundle, pilot_native_caps as native
from lantern_meshticulum.bench.pilot_parent_caps import canonical
from tests.test_gfsk_bundle100k import data, gfdata
from tests.test_bundle_approval_merge import case, protected_source, tmp_path
from tests.test_native_state_comparison import leaves


def assign(value, path, replacement):
    for kind, key in path[:-1]:
        value = getattr(value, key) if kind == 'attr' else value[key]
    kind, key = path[-1]
    if kind == 'attr': object.__setattr__(value, key, replacement)
    else: value[key] = replacement


@pytest.mark.parametrize('field', ['policy', 'value', 'manifest', 'expected'])
def test_every_changed_live_leaf_is_refused_without_serializing(gfdata, field, monkeypatch):
    _, _, value, policy = gfdata
    context = bundle.BundleContext(value, policy)
    original = getattr(context, field)
    baseline = canonical(original.to_dict() if field == 'policy' else original)
    comparison = getattr(context, '_'+field+'_comparison')
    assert native._compare_state(deepcopy(original), comparison[0], comparison)
    def forbidden(_): raise AssertionError('hot_path_serialization_returned')
    monkeypatch.setattr(bundle, 'canonical', forbidden)
    context._check()
    for path, item in list(leaves(original)):
        replacement = not item if type(item) is bool else item+1 if type(item) is int else item+'changed' if type(item) is str else 'changed'
        assign(original, path, replacement)
        try:
            assert canonical(original.to_dict() if field == 'policy' else original) != baseline
            with pytest.raises(Exception): context._check()
        finally: assign(original, path, item)
    context._check()


@pytest.mark.parametrize('mutation', ['stages_tuple', 'trust_type', 'string_key_type', 'nan', 'bool', 'policy_units_list', 'dataclass_float'])
def test_container_scalar_and_dataclass_type_changes_are_refused(gfdata, mutation):
    _, _, value, policy = gfdata
    context = bundle.BundleContext(value, policy)
    if mutation == 'stages_tuple': context.value['stages'] = tuple(context.value['stages'])
    elif mutation == 'trust_type':
        class ForeignDict(dict): pass
        context.value['trust'] = ForeignDict(context.value['trust'])
    elif mutation == 'string_key_type':
        class ForeignString(str): pass
        first = context.value['stages'][0]
        first[ForeignString('index')] = first.pop('index')
    elif mutation == 'nan': context.value['whole_bytes'] = float('nan')
    elif mutation == 'bool': context.expected['parts'][0]['index'] = False
    elif mutation == 'policy_units_list': object.__setattr__(policy, 'units', list(policy.units))
    else:
        target = policy.unit('unit-a').limits
        object.__setattr__(target, 'radio_attempts', float(target.radio_attempts))
    with pytest.raises(Exception): context._check()


@pytest.mark.parametrize('field', ['policy', 'value', 'manifest', 'expected'])
@pytest.mark.parametrize('mutation', ['function', 'code', 'defaults', 'metadata'])
def test_compiled_context_function_and_metadata_cannot_be_replaced(gfdata, field, mutation):
    _, _, value, policy = gfdata
    context = bundle.BundleContext(value, policy)
    name = '_'+field+'_comparison'; comparison = getattr(context, name)
    if mutation == 'function':
        fake = lambda _: True
        setattr(context, name, (fake, fake.__code__, fake.__defaults__))
    elif mutation == 'code': comparison[0].__code__ = (lambda *_: True).__code__
    elif mutation == 'defaults': comparison[0].__defaults__ = tuple(list(comparison[0].__defaults__))
    else: setattr(context, name, tuple(list(comparison)))
    with pytest.raises(Exception): context._check()


@pytest.mark.parametrize('field', ['_refs', '_policy_bytes', '_manifest_bytes', '_expected_bytes'])
def test_original_comparison_baselines_remain_bound(gfdata, field):
    _, _, value, policy = gfdata
    context = bundle.BundleContext(value, policy)
    original = getattr(context, field)
    setattr(context, field, tuple(list(original)) if type(original) is tuple else b'{}')
    with pytest.raises(Exception): context._check()


@pytest.mark.parametrize('field', ['policy', 'facts'])
@pytest.mark.parametrize('mutation', ['function', 'code', 'defaults', 'metadata'])
def test_approval_clock_cannot_replace_compiled_live_comparisons(case, field, mutation):
    approval, clock, caps, raw = case
    name = '_'+field+'_comparison'; comparison = getattr(approval, name)
    def attack(index):
        if index != 2: return
        if mutation == 'function':
            fake = lambda _: True
            setattr(approval, name, (fake, fake.__code__, fake.__defaults__))
        elif mutation == 'code': comparison[0].__code__ = (lambda *_: True).__code__
        elif mutation == 'defaults': comparison[0].__defaults__ = tuple(list(comparison[0].__defaults__))
        else: setattr(approval, name, tuple(list(comparison)))
    clock.action = attack
    with pytest.raises(Exception): approval(raw)
    clock.action = None
    assert approval._closed and caps.current() is False


def test_all_context_bound_clock_reader_runtime_and_raw_hash_checks_remain(case, monkeypatch):
    approval, clock, caps, raw = case
    counts = dict(bound=0, context=0, reader=0, runtime=0, raw_hash=0)
    def tracked(owner, name, counter):
        original = getattr(owner, name)
        def check(*args, **kwargs):
            counts[counter] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(owner, name, check)
    tracked(approval, '_bound', 'bound')
    tracked(approval.bundle, '_check', 'context')
    tracked(approval.reader, 'check', 'reader')
    tracked(approval.runtime, 'check', 'runtime')
    original_sha = bundle.hashlib.sha256
    def hashed(data):
        if data is approval.bundle.raw: counts['raw_hash'] += 1
        return original_sha(data)
    monkeypatch.setattr(bundle.hashlib, 'sha256', hashed)
    assert approval(raw) is True
    assert clock.calls == 2
    assert counts == dict(bound=11, context=4, reader=1, runtime=1, raw_hash=4)
