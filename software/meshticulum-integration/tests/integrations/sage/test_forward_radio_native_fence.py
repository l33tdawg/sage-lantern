"""Real supervisor/authority/signed rows/PTYs; service, SDK and accounting models.

Only read-and-reject metadata boundaries are exercised. No native qualification,
private payload dispatch, grant renewal or permission caching is implied.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
import os
import uuid

import pytest

from sage_lantern.storage_supervisor import Producer, RootStorageSupervisor
from lantern_meshticulum.sage.forward_budget import Cost
from lantern_meshticulum.sage.forward_native_provider import ForwardNativeProvider
from tests.integrations.sage import test_forward_native_provider as provider_fixture
from tests.integrations.sage.test_forward_radio_adoption_action import prepared, retained
from tests.integrations.sage.test_forward_radio_facts_action import active, invoke, retained as facts_retained


@contextmanager
def supervisor_model(tmp_path, monkeypatch):
    """Install the original supervisor before provider/owner bindings exist."""
    original_setup = provider_fixture.setup
    original_provider = provider_fixture.ForwardNativeProvider
    current = []

    @contextmanager
    def setup(**kwargs):
        with original_setup(**kwargs) as r:
            hooks = dict(vault=None, clock=None, lifecycle=[], snapshots=0)
            class Backend:
                vault = (800, 'a' * 32)
                instance = Producer(901, 1001, {'instance_id': str(uuid.uuid4())})
                def vault_identity(self):
                    if hooks['vault'] is not None: hooks['vault']()
                    return self.vault
                def start_voice(self):
                    hooks['lifecycle'].append('start')
                    return self.instance
                def producer(self, *, stopping=False): return self.instance
            def snapshot():
                hooks['snapshots'] += 1
                return r.hardware.probe.snapshot()
            directory = tmp_path.resolve() / 'native-supervisor'
            directory.mkdir(mode=0o700)
            backend = Backend()
            supervisor = RootStorageSupervisor(directory, backend=backend, snapshot=snapshot,
                access_epoch=lambda: r.state.epoch, clock=lambda: 10.0, owner_uid=os.geteuid())
            r.hardware.supervisor = r.supervisor = supervisor
            r.fence_hooks, r.native_backend = hooks, backend
            current.append(r)
            try:
                supervisor.start(); supervisor.own_resource(r.hardware)
                yield r
            finally:
                hooks['vault'] = hooks['clock'] = None
                supervisor.close()
                assert supervisor._fd is None and supervisor._closed
                assert hooks['lifecycle'] == ['start']

    def provider(*args, **kwargs):
        original_clock = kwargs['clock_reader']; r = current[-1]
        def clock():
            value = original_clock()
            if r.fence_hooks['clock'] is not None: r.fence_hooks['clock']()
            return value
        return original_provider(*args, **dict(kwargs, clock_reader=clock))

    with monkeypatch.context() as patch:
        patch.setattr(provider_fixture, 'setup', setup)
        patch.setattr(provider_fixture, 'ForwardNativeProvider', provider)
        yield


def metadata(r):
    return r.owner._radio_read is not None and r.provider._observing and not r.provider._purpose._observing


def record_fences(r):
    seen = dict(index=0, status=Counter(), requests=[], change=False)
    def observed():
        if metadata(r): seen['index'] += 1
    def response(value):
        seen['requests'].append((value.request.method, value.request.url.path))
        if metadata(r) and value.request.url.path == '/v1/messages/storage':
            seen['status'][seen['index']] += 1
    r.source.hook = observed
    r.client._client.event_hooks['response'].append(response)
    return seen


def unchanged(r, original):
    assert (r.adoption._highest, r.adoption._record) == original
    assert r.source.close_calls == 0 and not r.source.closed
    assert not r.provider._observing
    retained(r)


def test_six_fences_each_observe_native_pre_vault_and_paired_post_vault_status(tmp_path, monkeypatch, record_property):
    with supervisor_model(tmp_path, monkeypatch), prepared(tmp_path) as r:
        seen = record_fences(r)
        assert type(r.supervisor) is RootStorageSupervisor and r.supervisor._fd is not None
        assert r.adoption.current() is True
        assert seen['index'] == 6 and seen['status'] == Counter({index: 2 for index in range(1, 7)})
        first = Counter(seen['requests']); snapshots = r.fence_hooks['snapshots']
        assert first['GET', '/v1/workflows/' + r.adoption.record_id] == 1
        assert first['GET', '/v1/workflows/' + r.provider._purpose.record_id] == 2
        assert first['GET', '/v1/workflows/' + r.provider._enrollment.record_id] == 2
        assert all(method == 'GET' for method, _ in first)
        assert r.adoption.current() is True
        assert seen['index'] == 12 and seen['status'] == Counter({index: 2 for index in range(1, 13)})
        assert Counter(seen['requests']) == first + first and r.fence_hooks['snapshots'] > snapshots
        record_property('source_modeled_requests_per_adoption', sum(first.values()))
        record_property('metadata_statuses_per_fence', 2)
        record_property('native_supervisor', type(r.supervisor).__name__)
        assert r.state.rows == r.rows
        retained(r)


@pytest.mark.parametrize('position', range(1, 7))
@pytest.mark.parametrize('change', ['storage', 'debt'])
def test_late_post_vault_change_at_each_sdk_fence_cannot_publish(tmp_path, monkeypatch, position, change):
    with supervisor_model(tmp_path, monkeypatch), prepared(tmp_path) as r:
        seen = record_fences(r); original = r.adoption._highest, r.adoption._record
        def after(value):
            if (seen['change'] or not metadata(r) or seen['index'] != position
                or value.request.url.path != '/v1/messages/storage' or seen['status'][position] != 2): return
            seen['change'] = True
            if change == 'storage': r.state.instance_id = 'c' * 64
            else: r.source.debt = Cost()  # An apparent refund is still changed native evidence.
        r.client._client.event_hooks['response'].append(after)
        assert r.adoption.current() is False and seen['change']
        # A change in the last pre-GET guard can be read before later SDK/full
        # bookends refuse. No returned row has become admitted or published.
        if position >= 3:
            assert ('GET', '/v1/workflows/' + r.adoption.record_id) in seen['requests']
        assert all(method == 'GET' for method, _ in seen['requests'])
        unchanged(r, original)


@pytest.mark.parametrize('boundary,change', [
    ('vault', 'vault'), ('vault', 'storage'), ('vault', 'debt'),
    ('access', 'storage'), ('access', 'expiry'), ('access', 'scope'),
    ('clock', 'storage'), ('clock', 'debt'), ('clock', 'expiry')])
def test_last_fence_callback_drift_is_rejected_by_original_full_return(tmp_path, monkeypatch, boundary, change):
    with supervisor_model(tmp_path, monkeypatch), prepared(tmp_path) as r:
        seen = record_fences(r); original = r.adoption._highest, r.adoption._record
        def mutate():
            if seen['change'] or not metadata(r) or seen['index'] != 6: return
            if boundary != 'vault' and seen['status'][6] != 2: return
            seen['change'] = True
            if change == 'vault': r.native_backend.vault = (801, 'c' * 32)
            elif change == 'storage': r.state.instance_id = 'c' * 64
            elif change == 'debt': r.source.actual = Cost()
            elif change == 'expiry': r.state.now = (r.state.now[0], r.child.application_deadline_monotonic_us)
            else:
                with r.authority._mutex:
                    grant = r.authority._grants[id(r.provider.setup_context)]
                    grant['operations'] = frozenset()
        if boundary == 'vault': r.fence_hooks['vault'] = mutate
        elif boundary == 'clock': r.fence_hooks['clock'] = mutate
        else:
            authorized = r.authority.authorized
            def access(*args, **kwargs):
                value = authorized(*args, **kwargs); mutate(); return value
            monkeypatch.setattr(r.authority, 'authorized', access)
        assert r.adoption.current() is False and seen['change']
        assert ('GET', '/v1/workflows/' + r.adoption.record_id) in seen['requests']
        unchanged(r, original)


@pytest.mark.parametrize('field', ['messaging_enabled', 'dispatch_enabled'])
def test_equal_integer_consent_substitution_is_refused_inside_its_fence(tmp_path, monkeypatch, field):
    with supervisor_model(tmp_path, monkeypatch), prepared(tmp_path) as r:
        seen = []; original = r.adoption._highest, r.adoption._record
        def observed():
            if metadata(r) and not seen:
                object.__setattr__(r.owner.consent, field, 1); seen.append(True)
        r.source.hook = observed
        requests = []; r.client._client.event_hooks['response'].append(lambda v: requests.append(v.request.url.path))
        assert r.adoption.current() is False and seen == [True]
        assert '/v1/workflows/' + r.adoption.record_id not in requests
        unchanged(r, original)


@pytest.mark.parametrize('change', ['helper', 'source', 'owner-clock', 'owner-context', 'actor', 'endpoint', 'epoch', 'token'])
def test_new_fence_refuses_borrowed_bindings_and_method_changes(tmp_path, monkeypatch, change):
    with supervisor_model(tmp_path, monkeypatch), prepared(tmp_path) as r:
        seen = []; original = r.adoption._highest, r.adoption._record
        def observed():
            if not metadata(r) or seen: return
            if change == 'helper': monkeypatch.setattr(ForwardNativeProvider, '_check_accounting', lambda _: None)
            elif change == 'source': r.provider.accounting = object()
            elif change == 'owner-clock': r.owner._clock = lambda: r.state.now
            elif change == 'owner-context': r.owner.native_context = replace(r.owner.native_context)
            elif change == 'actor': r.hardware.probe.actor = replace(r.hardware.probe.actor, chain_id='other')
            elif change == 'endpoint': r.provider._store.storage.endpoint += '/other'
            elif change == 'epoch': r.state.epoch = 'c' * 32
            else: r.owner._radio_read = object()
            seen.append(True)
        r.source.hook = observed
        assert r.adoption.current() is False and seen == [True]
        if change == 'token':
            assert r.owner._radio_read is not None and r.owner._uncertain
            # Refusal retains a substituted token instead of clearing foreign ownership.
            r.owner._radio_read = None
        unchanged(r, original)


@pytest.mark.parametrize('probe', [1, 2])
def test_facts_probe_vault_callback_cannot_admit_or_release_fds(tmp_path, monkeypatch, probe):
    with supervisor_model(tmp_path, monkeypatch), active(tmp_path, monkeypatch) as r:
        original = r.adoption._highest, r.adoption._record; reached = []
        def changed(_, index):
            if index == probe:
                r.native_backend.vault = (801, 'c' * 32); reached.append(True)
        r.action['mutate'] = changed
        from lantern_meshticulum.sage.forward_radio import ForwardRadioError
        with pytest.raises(ForwardRadioError): invoke(r)
        assert reached == [True] and (r.adoption._highest, r.adoption._record) == original
        assert r.source.close_calls == 0 and not r.source.closed
        facts_retained(r)
