"""Actual owner/grants/signed rows/PTYs; SDK/native facts are source models."""
from collections import Counter
from contextlib import contextmanager
import copy
from dataclasses import replace
import os
import sys
import threading
import time

import pytest

from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.serial_protocol import Message
from lantern_meshticulum.sage.forward_radio import ForwardRadioError, _RadioAuthorizationMode
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner
from lantern_meshticulum.sage.mesh_inbox import _canonical
from lantern_meshticulum.sage.radio_native_adoption import SCOPE
from tests.integrations.sage import test_forward_native_provider as provider_fixture
from tests.integrations.sage import test_forward_radio as radio_fixture
from tests.integrations.sage.test_forward_radio_facts_action import (
    active, provider_model, retained, revoke, PURPOSE_DOMAIN, ENROLL_DOMAIN,
)
from tests.integrations.sage.test_forward_radio_native_fence import supervisor_model, metadata


@contextmanager
def authority_clock(monkeypatch):
    """Observe the original Core clock callback, never replace a live method."""
    original_provider = provider_fixture.ForwardNativeProvider
    box = dict(enabled=False, callback=None, calls=0)
    def provider(*args, **kwargs):
        original_clock = kwargs['clock_reader']
        def clock():
            value = original_clock()
            frame = sys._getframe(1)
            try:
                authority = frame.f_code is RunAuthority.live.__code__ or (
                    frame.f_back is not None and frame.f_back.f_code is RunAuthority.live.__code__)
            finally: del frame
            if box['enabled'] and authority:
                box['calls'] += 1
                if box['callback'] is not None: box['callback']()
            return value
        return original_provider(*args, **dict(kwargs, clock_reader=clock))
    with monkeypatch.context() as patch:
        patch.setattr(provider_fixture, 'ForwardNativeProvider', provider)
        try: yield box
        finally: box['enabled'] = False


def authorize(r): return r.stage._authorize()


def unchanged(r, before):
    assert (r.adoption._highest, r.adoption._record) == before
    assert r.source.close_calls == 0 and not r.source.closed
    retained(r)


def test_four_rows_two_probes_and_original_authority_are_one_fresh_action(tmp_path, monkeypatch, record_property):
    with supervisor_model(tmp_path, monkeypatch), authority_clock(monkeypatch) as clock, active(tmp_path, monkeypatch) as r:
        trace = []; requests = []; fences = []; statuses = Counter()
        paths = {'/v1/workflows/' + r.adoption.record_id: 'row',
                 '/v1/workflows/' + r.provider._purpose.record_id: 'purpose',
                 '/v1/workflows/' + r.provider._enrollment.record_id: 'enrollment'}
        def response(value):
            path = value.request.url.path; requests.append((value.request.method, path))
            if path in paths: trace.append(paths[path])
            if metadata(r) and path == '/v1/messages/storage': statuses[len(fences)] += 1
        r.client._client.event_hooks['response'].append(response)
        r.source.hook = lambda: fences.append(True) if metadata(r) else None
        r.action['mutate'] = lambda *_: trace.append('facts')
        clock['callback'] = lambda: trace.append('authority')
        clock['enabled'] = True
        assert authorize(r) is True
        expected = ['purpose', 'enrollment', 'row', 'facts', 'row', 'authority',
                    'row', 'facts', 'row', 'purpose', 'enrollment']
        assert trace == expected and clock['calls'] == 1
        assert len(fences) == 30 and statuses == Counter({i: 2 for i in range(1, 31)})
        first = Counter(requests)
        assert all(method == 'GET' for method, _ in first)
        assert authorize(r) is True
        assert trace == expected + expected and clock['calls'] == 2
        assert len(fences) == 60 and Counter(requests) == first + first
        assert r.action['calls'] == 4 and r.state.rows == r.saved_rows
        record_property('source_modeled_requests_per_authorize', sum(first.values()))
        record_property('full_key_bookends', 2)
        record_property('metadata_fences_per_authorize', 30)
        retained(r)


def test_real_factory_authorization_during_partial_arm_keeps_prepaid_opening_child(tmp_path, monkeypatch):
    with authority_clock(monkeypatch) as clock:
        monkeypatch.setattr(radio_fixture, 'model', provider_model)
        with radio_fixture.factory_model(tmp_path, monkeypatch) as r:
            phases = []
            def observed():
                action = r.owner._radio_facts
                if action is None or type(action[1]) is not _RadioAuthorizationMode: return
                child = r.stage.adoption._child
                phases.append((child.opening, r.stage.transfer is None, r.owner._held,
                    r.owner._busy, child.observing, r.stage._native_observing))
                assert any(a.plan is r.profile.child and not a.bound for a in r.op.budget._permits.values())
            clock['callback'] = observed; clock['enabled'] = True
            runner = r.stage.prepare(payload=r.packet)
            assert phases and all(row == (True, True, True, True, True, True) for row in phases)
            assert runner is r.op.active and runner.admission.bound and not runner.retired
            assert r.commands == [Message.HELLO, Message.STATUS, Message.DISARM, Message.ARM, Message.STATUS]
            assert r.stage.journal.snapshot()['attempts'] == 0
            assert r.state.rows[r.op.budget.record_id]['payload']['body']['allocations'] == [r.profile.child.to_dict()]
            clock['enabled'] = False
            assert r.stage._cleanup(time.monotonic() + 3) is True


@pytest.mark.parametrize('row,position', [(row, position) for row in range(4) for position in range(1, 7)])
def test_each_sdk_fence_of_all_four_rows_keeps_fresh_accounting(tmp_path, monkeypatch, row, position):
    with active(tmp_path, monkeypatch) as r:
        seen = []; before = r.adoption._highest, r.adoption._record
        target = 8 * row + position
        def changed():
            if metadata(r):
                seen.append(True)
                if len(seen) == target: r.source.evidence = 'f' * 64
        r.source.hook = changed
        with pytest.raises(ForwardRadioError): authorize(r)
        assert len(seen) == target and r.adoption._uncertain and r.owner._uncertain
        unchanged(r, before)


@pytest.mark.parametrize('target,domain', [('purpose', PURPOSE_DOMAIN), ('enrollment', ENROLL_DOMAIN)])
def test_final_key_revocation_cannot_publish_or_authorize(tmp_path, monkeypatch, target, domain):
    with active(tmp_path, monkeypatch) as r:
        gets = []; before = r.adoption._highest, r.adoption._record
        def response(value):
            if value.request.url.path != '/v1/workflows/' + r.adoption.record_id: return
            gets.append(True)
            if len(gets) == 4: revoke(r, getattr(r.provider, '_' + target), domain)
        r.client._client.event_hooks['response'].append(response)
        with pytest.raises(ForwardRadioError): authorize(r)
        assert len(gets) == 4 and r.stage._unknown
        unchanged(r, before)


@pytest.mark.parametrize('after_row', [1, 2, 3])
def test_pending_revision_cannot_regress_between_any_adjacent_rows(tmp_path, monkeypatch, after_row):
    with active(tmp_path, monkeypatch) as r:
        old = copy.deepcopy(r.state.rows[r.adoption.record_id])
        row = r.state.rows[r.adoption.record_id]; body = row['payload']['body']
        row['revision'] = body['revision'] = 2
        import base64
        from lantern_meshticulum.sage.radio_native_adoption import DOMAIN
        row['payload']['signature'] = base64.b64encode(r.hardware.identity._signing_key.sign(DOMAIN + _canonical(body)).signature).decode()
        gets = []; before = r.adoption._highest, r.adoption._record
        def response(value):
            if value.request.url.path != '/v1/workflows/' + r.adoption.record_id: return
            gets.append(True)
            assert (r.adoption._highest, r.adoption._record) == before
            if len(gets) == after_row: r.state.rows[r.adoption.record_id] = old
        r.client._client.event_hooks['response'].append(response)
        with pytest.raises(ForwardRadioError): authorize(r)
        assert len(gets) == after_row + 1
        unchanged(r, before)


@pytest.mark.parametrize('probe,change', [(p, c) for p in (1, 2) for c in ('scope', 'epoch', 'clock', 'debt', 'malformed')])
def test_each_fact_probe_retains_fresh_live_fences(tmp_path, monkeypatch, probe, change):
    with active(tmp_path, monkeypatch) as r:
        seen = []; before = r.adoption._highest, r.adoption._record
        def changed(_, at):
            if at != probe: return
            if change == 'scope':
                with r.authority._mutex:
                    grant = r.authority._grants[id(r.owner.native_context)]
                    grant['operations'] = grant['operations'] - {SCOPE}
            elif change == 'epoch': r.state.instance_id = 'c' * 64
            elif change == 'clock': r.state.now = (r.state.now[0], r.child.application_deadline_monotonic_us)
            elif change == 'debt': r.source.actual = r.child.cost
            else: r.action['result'] = 1
            seen.append(True)
        r.action['mutate'] = changed
        with pytest.raises(ForwardRadioError): authorize(r)
        assert seen == [True] and r.stage._unknown
        unchanged(r, before)


@pytest.mark.parametrize('change', ['callback', 'method', 'code', 'owner-code', 'authority-clock', 'owner-probe', 'grant', 'deadline', 'fd-map'])
def test_original_authorization_and_authority_bindings_cannot_be_substituted(tmp_path, monkeypatch, change):
    with active(tmp_path, monkeypatch) as r:
        seen = []; before = r.adoption._highest, r.adoption._record
        def changed(_, at):
            if at != 1: return
            authority = r.stage.authority
            if change == 'callback': r.stage._authorization_action = r.stage._authorization_action_ref = lambda *_: True
            elif change == 'method': monkeypatch.setattr(ForwardNativeOwner, 'observe_radio_authorization', lambda *_: True)
            elif change == 'code': monkeypatch.setattr(RunAuthority.live, '__code__', (lambda self: True).__code__)
            elif change == 'owner-code': monkeypatch.setattr(ForwardNativeOwner.observe_radio_authorization, '__code__', (lambda self, factory: True).__code__)
            elif change == 'authority-clock': authority.clock = lambda: r.state.now
            elif change == 'owner-probe': authority.owner_probe = lambda: True
            elif change == 'grant': authority.grant = replace(authority.grant)
            elif change == 'deadline': authority.application_deadline_us += 1
            else: authority.fds = dict(authority.fds)
            seen.append(True)
        r.action['mutate'] = changed
        with pytest.raises(ForwardRadioError): authorize(r)
        assert seen == [True] and r.stage._unknown
        unchanged(r, before)


@pytest.mark.parametrize('change', ['debt', 'epoch', 'clock', 'close'])
def test_original_authority_clock_callback_is_fenced_before_the_third_row(tmp_path, monkeypatch, change):
    with authority_clock(monkeypatch) as clock, active(tmp_path, monkeypatch) as r:
        before = r.adoption._highest, r.adoption._record; seen = []
        def changed():
            if change == 'debt': r.source.evidence = 'f' * 64
            elif change == 'epoch': r.state.instance_id = 'c' * 64
            elif change == 'clock': r.state.now = (r.state.now[0], r.child.application_deadline_monotonic_us)
            else: assert r.owner.close() is False
            seen.append(True)
        clock['callback'] = changed; clock['enabled'] = True
        with pytest.raises(ForwardRadioError): authorize(r)
        clock['enabled'] = False
        assert seen == [True] and r.action['calls'] == 1 and r.stage._unknown
        unchanged(r, before)


@pytest.mark.parametrize('action', ['authorize', 'native', 'read', 'crypto', 'close', 'cleanup', 'release', 'foreign-drain'])
def test_caught_reentry_or_cleanup_never_releases_owned_fds(tmp_path, monkeypatch, action):
    with active(tmp_path, monkeypatch) as r:
        seen = []; before = r.adoption._highest, r.adoption._record
        def changed(_, at):
            if at != 1: return
            assert r.owner._busy and r.adoption._busy and r.stage._native_observing and r.adoption._child.observing
            if action == 'read': assert r.adoption.current() is False
            elif action == 'close': assert r.owner.close() is False
            elif action == 'cleanup': assert r.stage._cleanup(time.monotonic() + 1) is False
            elif action == 'release': assert r.adoption.release() is False
            elif action == 'foreign-drain':
                results = []
                thread = threading.Thread(target=lambda: results.append(r.owner._registry.close(timeout=0)))
                thread.start(); thread.join(2)
                assert not thread.is_alive() and results == [False]
            else:
                call = {'authorize': lambda: authorize(r),
                    'native': lambda: r.stage._native(r.owner.context, r.child, r.stage.transfer),
                    'crypto': lambda: r.owner.crypto_action_check(r.owner.context, r.owner.intention, r.owner.remote,
                        r.owner.context.initiator_noise_public_key, r.owner.context.digest)}[action]
                with pytest.raises(Exception): call()
            assert all(os.fstat(fd).st_ino == inode for fd, inode in r.saved_fds)
            seen.append(True)
        r.action['mutate'] = changed
        with pytest.raises(ForwardRadioError): authorize(r)
        assert seen == [True] and r.stage._unknown
        unchanged(r, before)


@pytest.mark.parametrize('change', ['scope', 'clock'])
def test_initial_known_refusal_keeps_only_existing_owned_containment_parity(tmp_path, monkeypatch, change):
    with active(tmp_path, monkeypatch) as r:
        now = r.state.now; grant = r.authority._grants[id(r.owner.native_context)]; scopes = grant['operations']
        before = r.adoption._highest, r.adoption._record
        if change == 'scope': grant['operations'] = scopes - {SCOPE}
        else: r.state.now = (now[0], r.child.application_deadline_monotonic_us)
        with pytest.raises(ForwardRadioError): authorize(r)
        assert r.action['calls'] == 0 and not r.stage._unknown and r.adoption._uncertain
        unchanged(r, before)
        r.state.now = now; grant['operations'] = scopes
        assert r.stage._cleanup(time.monotonic() + 3) is True


@pytest.mark.parametrize('action', ['factory', 'owner', 'adoption', 'provider'])
def test_first_row_caught_reentry_remains_unknown(tmp_path, monkeypatch, action):
    with active(tmp_path, monkeypatch) as r:
        seen = []; before = r.adoption._highest, r.adoption._record
        def changed():
            if not metadata(r) or seen: return
            if action == 'adoption': assert r.adoption.current() is False
            else:
                call = {'factory': lambda: authorize(r), 'owner': r.owner.access_check,
                        'provider': r.provider.generate}[action]
                with pytest.raises(Exception): call()
            assert all(os.fstat(fd).st_ino == inode for fd, inode in r.saved_fds)
            seen.append(True)
        r.source.hook = changed
        with pytest.raises(ForwardRadioError): authorize(r)
        assert seen == [True] and r.action['calls'] == 0 and r.stage._unknown
        unchanged(r, before)


@pytest.mark.parametrize('change', ['callback', 'code', 'owner-code', 'authority-clock', 'authority-type'])
def test_preentry_structural_refusal_is_unknown_not_known_permission_refusal(tmp_path, monkeypatch, change):
    with active(tmp_path, monkeypatch) as r:
        before = r.adoption._highest, r.adoption._record
        if change == 'callback': r.stage._authorization_action = r.stage._authorization_action_ref = lambda *_: True
        elif change == 'code': monkeypatch.setattr(RunAuthority.live, '__code__', (lambda self: True).__code__)
        elif change == 'owner-code': monkeypatch.setattr(ForwardNativeOwner.observe_radio_authorization, '__code__', (lambda self, factory: True).__code__)
        elif change == 'authority-type': r.stage.authority = object()
        else: r.stage.authority.clock = lambda: r.state.now
        with pytest.raises(ForwardRadioError): authorize(r)
        assert r.action['calls'] == 0 and r.stage._unknown
        unchanged(r, before)


@pytest.mark.parametrize('provider', [False, True])
@pytest.mark.parametrize('damage', ['malformed', 'signature'])
def test_first_row_invalid_payload_refuses_before_facts_and_authority(tmp_path, monkeypatch, provider, damage):
    from tests.integrations.sage import test_forward_radio_native_action as legacy
    fixture = active if provider else legacy.active
    with fixture(tmp_path, monkeypatch) as r:
        adoption = r.stage.adoption
        before = adoption._highest, adoption._record
        row = r.state.rows[adoption.record_id]
        if damage == 'malformed': row['payload']['body'] = []
        else: row['payload']['signature'] = 'AA=='
        responses = []
        r.client._client.event_hooks['response'].append(lambda value: responses.append(True)
            if value.request.url.path == '/v1/workflows/' + adoption.record_id else None)
        with pytest.raises(ForwardRadioError): authorize(r)
        assert responses == [True] and r.action['calls'] == 0
        assert (adoption._highest, adoption._record) == before and adoption._uncertain
        # Legacy current() collapses every read failure to False. The explicit
        # authorization action must not label malformed metadata a permission refusal.
        assert r.stage._unknown is provider
        if provider: unchanged(r, before)
        else: legacy.retained(r)


@pytest.mark.parametrize('change', ['native-callback', 'owner-busy', 'adoption-busy', 'owner-uncertain'])
def test_refusal_before_observation_markers_keeps_unknown_and_borrowed_busy_state(tmp_path, monkeypatch, change):
    with active(tmp_path, monkeypatch) as r:
        before = r.adoption._highest, r.adoption._record
        if change == 'native-callback': r.owner._radio_native_check = lambda *_: True
        elif change == 'owner-busy': r.owner._busy = True
        elif change == 'adoption-busy': r.adoption._busy = True
        else: r.owner._uncertain = True
        try:
            with pytest.raises(ForwardRadioError): authorize(r)
            assert r.action['calls'] == 0 and r.stage._unknown
            if change == 'owner-busy': assert r.owner._busy
            elif change == 'adoption-busy': assert r.adoption._busy
            assert (r.adoption._highest, r.adoption._record) == before
            assert all(os.fstat(fd).st_ino == inode for fd, inode in r.saved_fds)
            assert r.source.close_calls == 0 and r.state.rows == r.saved_rows
        finally:
            # Release only the simulated caller's busy marker for fixture teardown.
            if change == 'owner-busy': r.owner._busy = False
            elif change == 'adoption-busy': r.adoption._busy = False
