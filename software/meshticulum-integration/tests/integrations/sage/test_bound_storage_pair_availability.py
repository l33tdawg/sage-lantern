# SPDX-License-Identifier: Apache-2.0
"""Structural availability supplies no live storage or dispatch permission."""
import copy

import pytest

from tests.integrations.sage.test_bound_storage_pair import paired
from tests.integrations.sage.test_forward_native_provider import fixture
from lantern_meshticulum.sage.bound_storage_pair import BoundStoragePairError
from lantern_meshticulum.sage.forward_native_provider import ForwardNativeProviderError


def test_structural_availability_does_not_repeat_binding_callbacks(paired, monkeypatch):
    r, store, pair, epoch, requests = paired
    calls = []
    original = r.client._client.timeout.as_dict
    def observed():
        calls.append(True)
        return original()
    monkeypatch.setattr(r.client._client.timeout, 'as_dict', observed)
    for _ in range(20):
        assert pair.matches(r.hardware.probe, store) is True
    assert not calls and not requests
    assert pair.observe(epoch) == epoch
    assert len(calls) >= 4 and requests == ['/v1/messages/storage']


@pytest.mark.parametrize('setting', ['timeout', 'redirects', 'environment'])
def test_structural_match_never_admits_invalid_live_transport(paired, setting):
    r, store, pair, epoch, requests = paired
    transport = r.client._client
    if setting == 'timeout': transport.timeout = 6
    elif setting == 'redirects': transport.follow_redirects = True
    else: transport._trust_env = True
    assert pair.matches(r.hardware.probe, store) is True
    with pytest.raises(BoundStoragePairError):
        pair.observe(epoch)
    assert not requests and not r.state.rows
    with pytest.raises(BoundStoragePairError):
        pair.observe(epoch)
    assert not requests


def test_provider_cannot_dispatch_after_structural_match_with_invalid_transport():
    with fixture() as (r, provider, source, bind):
        before = copy.deepcopy(r.state.rows)
        requests = []
        r.client._client.event_hooks['response'].append(lambda response: requests.append(response.request.url.path))
        r.client._client.timeout = 6
        assert provider._storage_pair.matches(r.hardware.probe, provider._store) is True
        with pytest.raises(ForwardNativeProviderError):
            provider.generate()
        assert r.state.rows == before and not requests and source.actual is None
