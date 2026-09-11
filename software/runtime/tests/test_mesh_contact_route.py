import socket
import threading
from types import SimpleNamespace

import pytest

from sage_lantern.mesh_contact_forwarding import OwnerContactForwarder, validate_payload
from sage_lantern.mesh_contact_route import OwnerContactRoute
from sage_lantern.mesh_voice_ipc import receive, send
from test_mesh_contact_owner import owner


@pytest.fixture
def route(owner):
    _, ledger, state = owner
    state['tokens'] = {'a' * 43, 'z' * 43}
    state['threads'] = []
    def snapshot(token):
        assert state['active'] and token in state['tokens']
        return state['binding']['epoch'], state['profile']
    root = OwnerContactRoute(snapshot=snapshot, clock=lambda: 1000)
    resource = SimpleNamespace(config=dict(voice_uid=941, chain_id='private-local',
        radio={'public_key': state['binding']['radio_public_key']}), identity=ledger.identity,
        probe=ledger.store.storage, active=lambda: state['active'], _mutex=threading.Lock(), _provider=object())
    supervisor = SimpleNamespace(broker_id='b' * 32)
    def connector():
        client, server = socket.socketpair()
        server.settimeout(5)
        def worker():
            try:
                if state.get('daemon') is not None:
                    state['daemon'].handle(server, pid=1234, uid=state.get('uid', 941))
                else:
                    root.handle(server, receive(server), resource=resource, supervisor=supervisor, uid=state.get('uid', 941))
            except Exception:
                try: send(server, {'error': 'unavailable'})
                except Exception: pass
            finally:
                server.close()
        thread = threading.Thread(target=worker)
        state['threads'].append(thread)
        thread.start()
        return client
    def require_token(token):
        snapshot(token)
    broker = SimpleNamespace(require_token=require_token,
        access_epoch=lambda: dict(schema='sage.lantern.access-epoch.v1', locked=not state['active'], epoch=state['binding']['epoch']))
    forward = OwnerContactForwarder(broker, connector=connector)
    yield root, forward, state, resource, supervisor
    for thread in state['threads']:
        thread.join(6)
        assert not thread.is_alive()


def shown(forward, token='a' * 43):
    return forward.handle('/contacts/preview', dict(token=token, ttl_seconds=600, findable=True, messaging_allowed=False))


def confirmed(forward, plan, token='a' * 43):
    return forward.handle('/contacts/confirm', dict(token=token, consent=True,
        challenge=plan['challenge'], plan_sha256=plan['plan_sha256']))


def test_actual_framed_route_to_real_sdk_handlers(route):
    root, forward, state, resource, _ = route
    plan = shown(forward)
    assert state['puts'] == 0
    result = confirmed(forward, plan)
    assert result['revision'] == 1 and state['puts'] == 2
    assert root.context is None and 'a' * 43 not in repr(root.__dict__)
    assert type(resource._provider) is object


@pytest.mark.parametrize('fault', ['token', 'other_session', 'locked', 'epoch', 'broker', 'config', 'cancel', 'root_uid', 'wrong_uid', 'uninitialized'])
def test_confirm_revocation_window_and_peer_denial(route, fault):
    root, forward, state, resource, supervisor = route
    plan = shown(forward)
    token = 'a' * 43
    if fault == 'token': state['tokens'].clear()
    if fault == 'other_session': token = 'z' * 43
    if fault == 'locked': state['active'] = False
    if fault == 'epoch': state['binding']['epoch'] = 'f' * 32
    if fault == 'broker': supervisor.broker_id = 'c' * 32
    if fault == 'config': resource.config['changed'] = True
    if fault == 'cancel': forward.handle('/contacts/cancel', {'token': token})
    if fault == 'root_uid': state['uid'] = 0
    if fault == 'wrong_uid': state['uid'] = 999
    if fault == 'uninitialized': resource._provider = None
    with pytest.raises(Exception): confirmed(forward, plan, token)
    assert state['puts'] == 0


def test_reconciliation_route_abandon_then_explicit_new_issue(route, owner):
    _, forward, state, _, _ = route
    plan = shown(forward)
    state['unknown'] = True
    with pytest.raises(Exception): confirmed(forward, plan)
    state['unknown'] = False
    recovery = forward.handle('/contacts/reconcile/preview', {'token': 'a' * 43, 'action': 'abandon'})
    result = forward.handle('/contacts/reconcile/confirm', dict(token='a' * 43, consent=True,
        challenge=recovery['challenge'], plan_sha256=recovery['plan_sha256']))
    assert result['state'] == 'abandoned'
    assert confirmed(forward, shown(forward))['revision'] == 2


@pytest.mark.parametrize('path,payload', [('/drain', {'token': 'a' * 43}),
    ('/contacts/preview', dict(token='a' * 43, ttl_seconds=True, findable=True, messaging_allowed=True)),
    ('/contacts/state', dict(token='a' * 43, op='resume')),
    ('/contacts/confirm', dict(token='a' * 43, consent=False, challenge='b' * 64, plan_sha256='c' * 64))])
def test_exact_http_operation_contract(path, payload):
    with pytest.raises(Exception): validate_payload(path, payload)
