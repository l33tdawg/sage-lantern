# SPDX-License-Identifier: Apache-2.0
"""Relay observer construction and method binding; native facts/RF are models."""

import pytest

from lantern_meshticulum.sage.forward_radio import ForwardRadioError
from tests.integrations.sage.test_forward_native_relay_radio import prepared_rig


def test_relay_observer_is_bound_before_adoption_without_endpoint_factory(tmp_path, monkeypatch):
    with prepared_rig(tmp_path, monkeypatch, enroll=False) as r:
        r.observer._bound()
        assert r.observer._factory is r.observer._factory_ref is None
        assert r.observer.owner is r.owner and r.observer.profile is r.profile
        assert r.observer.transport is r.wire.transports[0]
        assert r.factory.observer is r.observer
        assert r.observer.adoption is r.observer.worker is r.observer.identity is None
        assert not r.observer._used and not r.factory._used
        assert r.wire.commands == [[], []]


@pytest.mark.parametrize('member', ['_device_facts', '_worker_current'])
@pytest.mark.parametrize('location', ['instance', 'class'])
def test_relay_observer_refuses_sealed_method_drift_before_adoption(
        tmp_path, monkeypatch, member, location):
    with prepared_rig(tmp_path, monkeypatch, enroll=False) as r:
        r.observer._bound()
        with monkeypatch.context() as change:
            if location == 'instance':
                change.setitem(r.observer.__dict__, member, lambda *args: True)
            else:
                change.setattr(type(r.observer), member, lambda *args: True)
            with pytest.raises(ForwardRadioError):
                r.observer._bound()
        r.observer._bound()
        assert r.observer.adoption is None and not r.factory._used
        assert r.wire.commands == [[], []]


@pytest.mark.parametrize('damage', ['reference', 'relay-factory'])
def test_relay_observer_refuses_factory_binding_substitution(tmp_path, monkeypatch, damage):
    with prepared_rig(tmp_path, monkeypatch, enroll=False) as r:
        r.observer._bound()
        with monkeypatch.context() as change:
            change.setattr(r.observer, '_factory', r.factory)
            if damage == 'relay-factory':
                change.setattr(r.observer, '_factory_ref', r.factory)
            with pytest.raises(ForwardRadioError):
                r.observer._bound()
        r.observer._bound()
        assert r.observer._factory is r.observer._factory_ref is None
        assert r.observer.adoption is None and not r.factory._used
        assert r.wire.commands == [[], []]
