from dataclasses import replace
import http.client
import threading
import json
from collections import Counter

import pytest

from sage_lantern import mesh_daemon
from sage_lantern import device_access, mesh_contact_route
import test_unit_a_device_auth as auth_tests
from test_mesh_contact_route import route, owner


@pytest.fixture
def mounted(route):
    fixture = auth_tests.DeviceAuthTests(methodName='test_state_unlock_authorize_lock')
    fixture.setUp()
    try:
        token = fixture.unlock()
        fixture.profile.complete_setup('Actual owner', 0)
        root, forward, state, resource, supervisor = route
        def snapshot(capability):
            fixture.broker.require_token(capability)
            epoch = fixture.broker.access_epoch()
            assert epoch['locked'] is False
            return epoch['epoch'], fixture.profile.load()
        root.snapshot = snapshot
        fixture.broker.contacts.connector = forward.connector
        daemon = mesh_daemon.MeshDaemon.__new__(mesh_daemon.MeshDaemon)
        daemon.resource, daemon.supervisor, daemon._owner_contacts = resource, supervisor, root
        state['daemon'] = daemon
        yield fixture, token, route
    finally:
        fixture.doCleanups()


def preview(fixture, token):
    status, body, _ = fixture.request('POST', '/contacts/preview',
        dict(token=token, ttl_seconds=600, findable=True, messaging_allowed=False))
    assert status == 200
    return body


def confirm(fixture, token, shown):
    return fixture.request('POST', '/contacts/confirm', dict(token=token, consent=True,
        challenge=shown['challenge'], plan_sha256=shown['plan_sha256']))


def test_actual_auth_handler_device_session_daemon_and_ledger(mounted):
    fixture, token, route = mounted
    shown = preview(fixture, token)
    assert shown['plan']['display_name'] == 'Actual owner'
    status, card, headers = confirm(fixture, token, shown)
    assert status == 200 and card['revision'] == 1
    assert headers['Cache-Control'] == 'no-store' and token not in str(card)
    assert route[2]['puts'] == 2


@pytest.mark.parametrize('fault', ['origin', 'missing_origin', 'host', 'control', 'extra', 'false_consent', 'lock', 'new_session', 'cancel'])
def test_real_http_boundary_and_window_revocation(mounted, fault):
    fixture, token, route = mounted
    shown = preview(fixture, token)
    kwargs = {}
    payload = dict(token=token, consent=True, challenge=shown['challenge'], plan_sha256=shown['plan_sha256'])
    if fault == 'origin': kwargs['origin'] = 'https://other.invalid'
    if fault == 'missing_origin': kwargs['origin'] = False
    if fault == 'host': kwargs['host'] = 'other.invalid'
    if fault == 'control': kwargs['headers'] = {'X-Lantern-Auth': None}
    if fault == 'extra': payload['op'] = 'resume'
    if fault == 'false_consent': payload['consent'] = False
    if fault == 'lock': fixture.broker.lock(token)
    if fault == 'new_session':
        fixture.broker.lock(token)
        payload['token'] = fixture.unlock()
    if fault == 'cancel':
        assert fixture.request('POST', '/contacts/cancel', {'token': token})[0] == 200
    status, response, _ = fixture.request('POST', '/contacts/confirm', payload, **kwargs)
    assert status != 200 and 'card' not in response and token not in str(response)
    assert route[2]['puts'] == 0


@pytest.mark.parametrize('public', [False, True])
def test_actual_profile_boundary_accepts_both_preferences_without_change(mounted, public):
    fixture, token, _ = mounted
    original = replace(fixture.profile.load(), public_discoverable=public, revision=2)
    with fixture.profile._locked() as directory:
        fixture.profile._save(directory, original)
    assert fixture.request('POST', '/profile/get', {'token': token})[1]['public_discoverable'] is public
    assert preview(fixture, token)['plan']['public_discoverable'] is public
    assert fixture.profile.load() == original


def test_lock_after_real_reservation_withholds_card_preserves_unknown(mounted, monkeypatch):
    fixture, token, route = mounted
    shown = preview(fixture, token)
    root = route[0]
    put = root.issuance.ledger.store.put
    def revoke_after_write(*args, **kwargs):
        result = put(*args, **kwargs)
        fixture.broker.lock(token)
        return result
    monkeypatch.setattr(root.issuance.ledger.store, 'put', revoke_after_write)
    status, response, _ = confirm(fixture, token, shown)
    assert status != 200 and 'card' not in response and token not in str(response)
    assert route[2]['puts'] == 1
    assert next(iter(route[2]['rows'].values()))['payload']['body']['state'] == 'reserved'
    for thread in route[2]['threads']:
        thread.join(6)
        assert not thread.is_alive()
    assert root.issuance.pending is None and root.context is None


def test_real_owner_snapshot_http_authorize_profile_epoch(mounted, monkeypatch):
    fixture, token, _ = mounted
    server = auth_tests.MODULE.DeviceAuthServer(fixture.device, profile=fixture.profile, bind_and_activate=False)
    server.server_address = ('127.0.0.1', 0)
    server.server_bind()
    server.server_activate()
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    original_connection = http.client.HTTPConnection
    def connection(host, port, timeout):
        assert (host, port) == ('127.0.0.1', 3196)
        client = original_connection(*server.server_address, timeout=timeout)
        request = client.request
        def fixed_host(method, path, body=None, headers=None):
            return request(method, path, body, {**(headers or {}), 'Host': '127.0.0.1:3196'})
        client.request = fixed_host
        return client
    try:
        monkeypatch.setattr(http.client, 'HTTPConnection', connection)
        monkeypatch.setattr(device_access, '_marker_state', lambda marker: True)
        monkeypatch.setattr(mesh_contact_route, '_marker_state', lambda marker: True)
        epoch, profile = mesh_contact_route.owner_snapshot(token)
        assert profile == fixture.profile.load() and epoch == server.broker.access_epoch()['epoch']
        with pytest.raises(Exception): mesh_contact_route.owner_snapshot('x' * 43)
        fixture.device.lock()
        with pytest.raises(Exception): mesh_contact_route.owner_snapshot(token)
    finally:
        server.shutdown()
        worker.join(5)
        server.server_close()


@pytest.fixture
def http_route(mounted, monkeypatch):
    fixture, token, route = mounted
    server = auth_tests.MODULE.DeviceAuthServer(fixture.device, profile=fixture.profile, bind_and_activate=False)
    server.broker = fixture.broker
    server.server_address = ('127.0.0.1', 0)
    server.server_bind()
    server.server_activate()
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    original_connection = http.client.HTTPConnection
    calls = Counter()
    def connection(host, port, timeout):
        assert (host, port) == ('127.0.0.1', 3196)
        client = original_connection(*server.server_address, timeout=timeout)
        request = client.request
        def fixed_host(method, path, body=None, headers=None):
            calls[path] += 1
            return request(method, path, body, {**(headers or {}), 'Host': '127.0.0.1:3196'})
        client.request = fixed_host
        return client
    def browser(path, payload):
        client = original_connection(*server.server_address, timeout=15)
        try:
            client.request('POST', path, json.dumps(dict(token=token, **payload)),
                {'Host': '127.0.0.1:3196', 'Origin': 'http://127.0.0.1:3100',
                 'Content-Type': 'application/json', 'X-Lantern-Auth': 'control'})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()
    try:
        monkeypatch.setattr(http.client, 'HTTPConnection', connection)
        monkeypatch.setattr(device_access, '_marker_state', lambda marker: True)
        monkeypatch.setattr(mesh_contact_route, '_marker_state', lambda marker: True)
        route[0].snapshot = mesh_contact_route.owner_snapshot
        yield browser, calls, fixture, route
    finally:
        server.shutdown()
        worker.join(5)
        server.server_close()


def test_actual_route_http_fanout(http_route):
    browser, calls, _, _ = http_route
    status, plan = browser('/contacts/preview', dict(ttl_seconds=600, findable=True, messaging_allowed=False))
    assert status == 200
    preview_calls = sum(calls.values())
    status, result = browser('/contacts/confirm', dict(consent=True, challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))
    assert status == 200 and result['revision'] == 1
    print('owner_auth_http_calls', dict(preview=preview_calls, confirm=sum(calls.values())-preview_calls, paths=dict(calls)))
    assert preview_calls <= 16 and sum(calls.values()) - preview_calls <= 40
    assert set(calls) == {'/owner-contact-snapshot'}


def test_snapshot_helper_only_and_token_required(mounted):
    fixture, token, _ = mounted
    assert fixture.request('POST', '/owner-contact-snapshot', {'token': token})[0] == 403
    assert fixture.request('GET', '/owner-contact-snapshot', origin=False)[0] == 403
    assert fixture.request('POST', '/owner-contact-snapshot', {'token': 'invalid'}, origin=False)[0] == 401
    status, value, headers = fixture.request('POST', '/owner-contact-snapshot', {'token': token}, origin=False)
    assert status == 200 and value['profile']['display_name'] == 'Actual owner'
    assert value['epoch'] == fixture.broker.access_epoch()['epoch'] and headers['Cache-Control'] == 'no-store'
    assert token not in str(value)


def test_snapshot_profile_io_does_not_hold_auth_gate(mounted, monkeypatch):
    fixture, token, _ = mounted
    entered, release, locked = threading.Event(), threading.Event(), threading.Event()
    original_load = fixture.profile.load
    def blocked_load():
        entered.set()
        assert release.wait(3)
        return original_load()
    monkeypatch.setattr(fixture.profile, 'load', blocked_load)
    outcomes = []
    def snapshot():
        try: outcomes.append(fixture.broker.owner_contact_snapshot(token))
        except Exception: outcomes.append('revoked')
    thread = threading.Thread(target=snapshot)
    thread.start()
    assert entered.wait(3)
    def lock():
        fixture.broker.lock(token)
        locked.set()
    locker = threading.Thread(target=lock)
    locker.start()
    try:
        assert locked.wait(1), 'profile IO must not block auth revocation'
    finally:
        release.set()
        thread.join(3)
        locker.join(3)
    assert outcomes == ['revoked']


@pytest.mark.parametrize('fault', ['none', 'lock', 'profile'])
def test_actual_http_checks_at_each_write_and_after_write(http_route, monkeypatch, fault):
    browser, calls, fixture, route = http_route
    status, plan = browser('/contacts/preview', dict(ttl_seconds=600, findable=True, messaging_allowed=False))
    assert status == 200
    boundary = sum(calls.values())
    ledger = route[0].issuance.ledger
    original = ledger.store.client.workflow_put
    observed = []
    def put(*args, **kwargs):
        observed.append(sum(calls.values()))
        result = original(*args, **kwargs)
        if len(observed) == 1:
            if fault == 'lock': fixture.device.lock()
            if fault == 'profile': fixture.profile.rename('Changed owner', expected_revision=1)
        return result
    monkeypatch.setattr(ledger.store.client, 'workflow_put', put)
    status, result = browser('/contacts/confirm', dict(consent=True, challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))
    assert observed[0] > boundary
    assert sum(calls.values()) >= observed[-1]
    if fault == 'none':
        assert status == 200 and result['revision'] == 1
        assert len(observed) == 2 and observed[1] > observed[0]
        assert sum(calls.values()) > observed[-1]
    else:
        assert status != 200 and 'card' not in result and len(observed) == 1
        assert route[2]['puts'] == 1


def test_actual_http_export_fresh_check_and_budget(http_route):
    browser, calls, _, route = http_route
    status, plan = browser('/contacts/preview', dict(ttl_seconds=600, findable=True, messaging_allowed=False))
    assert status == 200
    status, issued = browser('/contacts/confirm', dict(consent=True, challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))
    assert status == 200
    calls.clear()
    status, plan = browser('/contacts/reconcile/preview', {'action': 'export'})
    assert status == 200
    preview_calls = sum(calls.values())
    status, result = browser('/contacts/reconcile/confirm', dict(consent=True, challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))
    assert status == 200 and result == issued and route[2]['puts'] == 2
    print('export_auth_http_calls', dict(preview=preview_calls, confirm=sum(calls.values())-preview_calls))
    assert 0 < preview_calls <= 13 and 0 < sum(calls.values())-preview_calls <= 21


def test_actual_http_reconciliation_budget(http_route):
    browser, calls, _, route = http_route
    status, plan = browser('/contacts/preview', dict(ttl_seconds=600, findable=True, messaging_allowed=False))
    assert status == 200
    route[2]['unknown'] = True
    status, _ = browser('/contacts/confirm', dict(consent=True, challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))
    assert status != 200
    route[2]['unknown'] = False
    calls.clear()
    status, plan = browser('/contacts/reconcile/preview', {'action': 'abandon'})
    assert status == 200
    preview_calls = sum(calls.values())
    status, result = browser('/contacts/reconcile/confirm', dict(consent=True, challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))
    assert status == 200 and result['state'] == 'abandoned'
    print('reconcile_auth_http_calls', dict(preview=preview_calls, confirm=sum(calls.values())-preview_calls))
    assert preview_calls <= 24 and sum(calls.values()) - preview_calls <= 177


@pytest.mark.parametrize('fault', ['lock', 'profile'])
def test_actual_http_export_rejects_revoked_approval(http_route, fault):
    browser, _, fixture, route = http_route
    status, plan = browser('/contacts/preview', dict(ttl_seconds=600, findable=True, messaging_allowed=False))
    assert status == 200
    status, _ = browser('/contacts/confirm', dict(consent=True, challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))
    assert status == 200
    status, plan = browser('/contacts/reconcile/preview', {'action': 'export'})
    assert status == 200
    if fault == 'lock': fixture.device.lock()
    else: fixture.profile.rename('Changed owner', expected_revision=1)
    status, result = browser('/contacts/reconcile/confirm', dict(consent=True, challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))
    assert status != 200 and 'card' not in result and route[2]['puts'] == 2
