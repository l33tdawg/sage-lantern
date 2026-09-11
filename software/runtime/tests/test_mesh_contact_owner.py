import copy
import json
from dataclasses import replace

import httpx
import pytest
from sage_sdk import AgentIdentity, SageClient

from sage_lantern import workflow_store
from sage_lantern.device_profile import DeviceProfile
from sage_lantern.mesh_contact_owner import ContactIssuanceLedger, OwnerContactHandlers, KIND
from sage_lantern.mesh_contact_issuance import ContactIssuanceUnavailable
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe


@pytest.fixture
def owner():
    if KIND not in workflow_store.KINDS:
        pytest.skip('requires reviewed additive contact_issuance runtime/SDK schema snapshot')
    identity = AgentIdentity.from_seed(bytes(range(32)))
    radio = AgentIdentity.from_seed(bytes(range(32, 64)))
    binding = dict(broker_id='b' * 32, epoch='e' * 32, config_sha256='c' * 64,
        chain_id='private-local', agent_id=identity.agent_id, radio_public_key=radio.agent_id)
    state = dict(rows={}, active=True, puts=0, unknown=False,
        profile=DeviceProfile(configured=True, display_name='Owner', revision=1), binding=binding)
    def http(request):
        assert request.headers['X-Agent-ID'] == identity.agent_id and request.headers['X-Signature']
        if request.url.path == '/v1/messages/storage':
            return httpx.Response(200, json=dict(schema='sage.message-storage.v1', instance_id='a' * 64,
                agent_id=identity.agent_id, encryption_expected=True, vault_active=True, vault_generation='1',
                stable=True, canonical_send_idempotency=True, encrypted_send_admission=True))
        if request.url.path == '/v1/workflows':
            after = request.url.params.get('after', '')
            limit = int(request.url.params.get('limit', '50'))
            rows = [row for identifier, row in sorted(state['rows'].items()) if identifier > after]
            page = rows[:limit]
            more = len(rows) > limit
            return httpx.Response(200, json=dict(schema='sage.workflow-journal.v1',
                items=page, has_more=more, next_after=page[-1]['record_id'] if more else None))
        identifier = request.url.path.rsplit('/', 1)[1]
        if request.method == 'GET':
            return httpx.Response(200 if identifier in state['rows'] else 404, json=state['rows'].get(identifier, {}))
        body = json.loads(request.content)
        assert body['kind'] == KIND
        previous = state['rows'].get(identifier, {}).get('revision', 0)
        if body['expected_revision'] != previous:
            return httpx.Response(409, json={})
        state['puts'] += 1
        state['rows'][identifier] = dict(schema='sage.workflow-journal.v1', agent_id=identity.agent_id,
            record_id=identifier, kind=KIND, revision=previous + 1, payload=body['payload'], trust='untrusted_auxiliary')
        if state['unknown']:
            raise httpx.ReadTimeout('synthetic persisted response loss')
        return httpx.Response(200, json=state['rows'][identifier])
    with SageClient('http://127.0.0.1:19100', identity, trust_env=False) as client:
        client._client.close()
        client._client = httpx.Client(base_url='http://127.0.0.1:19100', transport=httpx.MockTransport(http), trust_env=False)
        probe = MessageStorageProbe(client, actor=RecipientID('private-local', identity.agent_id), endpoint='http://127.0.0.1:19100')
        store = workflow_store.WorkflowStore(client, storage=probe)
        ledger = ContactIssuanceLedger(store, identity, access_check=lambda: state['active'], clock=lambda: 1000)
        handler = OwnerContactHandlers(ledger, read_profile=lambda: state['profile'],
            read_binding=lambda: state['binding'], access_check=lambda: state['active'], clock=lambda: 1000)
        yield handler, ledger, state


def preview(handler, **changes):
    return handler.handle(dict(op='owner-contact-preview', broker_id='b' * 32, epoch='e' * 32,
        ttl_seconds=600, findable=True, messaging_allowed=True, **changes), peer_uid=0)


def confirm(handler, result, **changes):
    request = dict(op='owner-contact-confirm', broker_id='b' * 32, epoch='e' * 32,
        challenge=result['challenge'], plan_sha256=result['plan_sha256'], consent=True)
    return handler.handle({**request, **changes}, peer_uid=0)


def test_real_sdk_guarded_ledger_preview_confirm_and_one_shot(owner):
    handler, ledger, state = owner
    shown = preview(handler)
    assert state['puts'] == 0
    exported = confirm(handler, shown)
    assert exported['revision'] == 1 and state['puts'] == 2
    assert ledger.current()['card'] == exported['card']
    with pytest.raises(ContactIssuanceUnavailable): confirm(handler, shown)
    assert state['puts'] == 2


@pytest.mark.parametrize('fault', ['locked', 'broker', 'epoch', 'profile', 'consent', 'challenge', 'unknown'])
def test_revocation_or_unknown_never_returns_card_or_retries(owner, fault):
    handler, ledger, state = owner
    shown = preview(handler)
    changes = {}
    if fault == 'locked': state['active'] = False
    if fault in ('broker', 'epoch'):
        field = 'broker_id' if fault == 'broker' else 'epoch'
        state['binding'] = {**state['binding'], field: 'd' * 32}
    if fault == 'profile': state['profile'] = replace(state['profile'], revision=2, public_discoverable=True)
    if fault == 'consent': changes['consent'] = False
    if fault == 'challenge': changes['challenge'] = 'wrong'
    if fault == 'unknown': state['unknown'] = True
    with pytest.raises(Exception): confirm(handler, shown, **changes)
    assert state['puts'] == (1 if fault == 'unknown' else 0)
    assert handler.pending is None
    if fault == 'unknown':
        state['unknown'] = False
        assert ledger.current()['state'] == 'reserved'
        with pytest.raises(ContactIssuanceUnavailable): preview(handler)


def test_kernel_uid_denial_and_signed_ledger_tampering(owner):
    handler, ledger, state = owner
    with pytest.raises(ContactIssuanceUnavailable): handler.handle({}, peer_uid=994)
    confirm(handler, preview(handler))
    row = next(iter(state['rows'].values()))
    row['payload'] = copy.deepcopy(row['payload'])
    row['payload']['body']['card_revision'] += 1
    with pytest.raises(Exception): ledger.current()


def test_cancel_and_state_do_not_sign_export_or_write(owner):
    handler, ledger, state = owner
    shown = preview(handler)
    request = dict(broker_id='b' * 32, epoch='e' * 32)
    assert handler.handle(dict(request, op='owner-contact-state'), peer_uid=0) == {'state': 'absent', 'revision': 0}
    assert handler.handle(dict(request, op='owner-contact-cancel'), peer_uid=0) == {'cancelled': True}
    with pytest.raises(ContactIssuanceUnavailable): confirm(handler, shown)
    assert state['puts'] == 0


def test_two_previews_share_real_cas_counter_not_independent_revision_spaces(owner):
    handler, ledger, state = owner
    other = OwnerContactHandlers(ledger, read_profile=lambda: state['profile'],
        read_binding=lambda: state['binding'], access_check=lambda: state['active'], clock=lambda: 1000)
    first, second = preview(handler), preview(other)
    confirm(handler, first)
    with pytest.raises(ContactIssuanceUnavailable): confirm(other, second)
    assert state['puts'] == 2 and ledger.current()['card_revision'] == 1


def test_unknown_reservation_state_is_redacted(owner):
    handler, ledger, state = owner
    shown = preview(handler)
    state['unknown'] = True
    with pytest.raises(Exception): confirm(handler, shown)
    state['unknown'] = False
    observed = handler.handle(dict(op='owner-contact-state', broker_id='b' * 32, epoch='e' * 32), peer_uid=0)
    assert observed == dict(state='reserved', revision=1, record_revision=1,
        plan_sha256=shown['plan_sha256'], card_sha256=None)
