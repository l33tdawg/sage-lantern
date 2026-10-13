# SPDX-License-Identifier: Apache-2.0
"""Real owner/key/accounting composition with explicitly modeled native/SDK IO.

No backend server, device or native/private transport qualification is implied.
"""
import base64
import copy
from dataclasses import asdict
from types import SimpleNamespace
import uuid

import pytest
pytest.importorskip('sage_sdk', reason='compatible optional SDK required')

from lantern_meshticulum.sage.forward_budget import ForwardBudgetError
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwnerError
from lantern_meshticulum.sage.forward_operation import ForwardOperationError
from lantern_meshticulum.sage.mesh_binary_capture import CaptureError
from lantern_meshticulum.sage.mesh_inbox import _canonical
from lantern_meshticulum.sage import noise_native_enrollment, noise_purpose
from tests.integrations.sage.test_forward_native_owner import setup
from tests.integrations.sage.test_forward_native_provider import fixture, DEBT
from tests.integrations.sage.test_forward_budget import plan


def revoke_signed_record(r, provider, kind):
    component = provider._purpose if kind == 'purpose' else provider._enrollment
    row = r.state.rows[component.record_id]
    row['revision'] += 1
    body = row['payload']['body']; body['revision'] = row['revision']
    if kind == 'purpose':
        body.update(state='retired', retirement=dict(epoch_us=body['last_epoch_us'],
                                                    monotonic_us=body['last_monotonic_us']))
        domain = noise_purpose.SIGNATURE_DOMAIN
    else:
        body['state'] = 'revoked'; domain = noise_native_enrollment.SIGNATURE_DOMAIN
    row['payload']['signature'] = base64.b64encode(
        r.hardware.identity._signing_key.sign(domain + _canonical(body)).signature).decode()


def test_empty_lease_keeps_native_observations_and_one_post_close_signed_key_pair():
    with fixture() as (r, provider, source, bind):
        bind(); operation = provider.start(r.packet); owner = provider.owner
        reads = []; events = []; scope_calls = []
        get = r.client.workflow_get; status = r.client.message_storage_status
        authorized = r.authority.authorized; capture = r.hardware.capture_lease
        def read(**arguments):
            reads.append(arguments['record_id']); events.append('key_record'); return get(**arguments)
        def storage():
            events.append('storage'); return status()
        def scopes(*arguments):
            scope_calls.append(arguments[1]); return authorized(*arguments)
        def acquire(consent):
            lease = capture(consent); check = lease.check; close = lease.close
            def current():
                events.append('lease_check'); return check()
            def finish():
                events.append('close'); close()
            lease.check, lease.close = current, finish
            return lease
        r.client.workflow_get = read; r.client.message_storage_status = storage
        r.authority.authorized = scopes; r.hardware.capture_lease = acquire
        # The previous lease schedule performed six full key observations.
        # Compare that signed-read component, not a whole transport rate.
        for _ in range(6): owner.access_check()
        assert reads.count(provider._purpose.record_id) == 6
        assert reads.count(provider._enrollment.record_id) == 6 and len(reads) == 12
        reads.clear(); events.clear(); scope_calls.clear()
        with owner.owner_lease():
            assert reads == [] and events.count('lease_check') == 1
            events.append('body')
        assert reads.count(provider._purpose.record_id) == 1
        assert reads.count(provider._enrollment.record_id) == 1 and len(reads) == 2
        assert len(scope_calls) >= 6 * 4 and events.count('lease_check') == 2
        assert events.count('close') == 1 and events.index('close') > events.index('body')
        assert events.index('key_record') > events.index('close')
        assert not owner._held and not owner._busy and not r.hardware._mutex.locked()
        assert source.actual is None and operation.budget.prior == DEBT


@pytest.mark.parametrize('kind', ['purpose', 'enrollment'])
def test_signed_revocation_inside_lease_is_refused_before_actual_crypto_action(kind):
    with fixture() as (r, provider, source, bind):
        bind(); operation = provider.start(r.packet); owner = provider.owner
        debt = copy.deepcopy(r.state.rows[operation.budget.record_id]); before = None
        with pytest.raises(ForwardOperationError):
            with owner.owner_lease():
                revoke_signed_record(r, provider, kind)
                before = copy.deepcopy(r.state.rows)
                operation.packet()
        assert before is not None and r.state.rows == before
        assert r.state.rows[operation.budget.record_id] == debt
        assert not operation.budget._permits and operation.session._closed
        assert not owner._held and not r.hardware._mutex.locked()
        assert operation.budget.prior == DEBT and source.actual is None


@pytest.mark.parametrize('kind', ['purpose', 'enrollment'])
def test_binary_capture_cannot_publish_when_signed_key_is_revoked_during_lease_close(kind):
    with fixture() as (r, provider, source, bind):
        bind(); operation = provider.start(r.packet); owner = provider.owner
        capture = owner._composition.capture; acquire = r.hardware.capture_lease
        debt = copy.deepcopy(r.state.rows[operation.budget.record_id]); snapshots = []
        def during_close(consent):
            lease = acquire(consent); close = lease.close
            def finish():
                close(); revoke_signed_record(r, provider, kind)
                snapshots.append(copy.deepcopy(r.state.rows))
            lease.close = finish; return lease
        r.hardware.capture_lease = during_close
        with pytest.raises(CaptureError): capture.state()
        assert len(snapshots) == 1 and r.state.rows == snapshots[0]
        assert r.state.rows[operation.budget.record_id] == debt
        assert owner._uncertain and capture._uncertain and capture._busy is None
        assert not owner._held and not r.hardware._mutex.locked()
        assert not r.client._client.is_closed and source.actual is None


def test_lost_actual_allocation_return_keeps_full_prior_and_child_debt_after_lease_cleanup():
    with fixture() as (r, provider, source, bind):
        bind(); operation = provider.start(r.packet); owner = provider.owner
        child = plan(SimpleNamespace(budget=operation.budget)); put = r.client.workflow_put
        def lost(**arguments):
            value = put(**arguments)
            if arguments['record_id'] == operation.budget.record_id:
                raise RuntimeError('modeled return lost after signed allocation CAS')
            return value
        r.client.workflow_put = lost
        with pytest.raises(ForwardBudgetError): operation.budget.pre_admit(child)
        body = r.state.rows[operation.budget.record_id]['payload']['body']
        assert body['binding']['prior_debt'] == asdict(DEBT)
        assert body['allocations'] == [child.to_dict()] and not operation.budget._permits
        assert operation.budget._uncertain and not owner._held and not r.hardware._mutex.locked()
        assert source.actual is None


@pytest.mark.parametrize('drift', ['authority', 'storage', 'boot', 'access_epoch',
                                  'epoch_expiry', 'mono_expiry', 'rollback', 'reentry'])
def test_fresh_native_observation_after_acquire_refuses_drift_and_joins_original_lease(drift):
    with setup() as r:
        owner = r.create(); capture = r.hardware.capture_lease; configured = r.hardware.configured
        leases = []; ready = [False]; entered = []
        def checked():
            value = configured()
            if ready[0] and drift == 'reentry':
                with pytest.raises(ForwardNativeOwnerError): owner.storage_check()
            return value
        def acquire(consent):
            lease = capture(consent); leases.append(lease); ready[0] = True
            if drift == 'authority': r.state.owner_live = False
            elif drift == 'storage': r.state.instance_id = 'f' * 64
            elif drift == 'boot': r.state.boot = str(uuid.uuid4())
            elif drift == 'access_epoch': r.state.epoch = 'f' * 32
            elif drift == 'epoch_expiry': r.state.now = (r.intention.application_expires_epoch_us, r.state.now[1])
            elif drift == 'mono_expiry': r.state.now = (r.state.now[0], r.intention.application_deadline_monotonic_us)
            elif drift == 'rollback': r.state.now = (r.state.now[0], r.state.now[1] - 1)
            return lease
        r.hardware.configured = checked; r.hardware.capture_lease = acquire
        with pytest.raises(ForwardNativeOwnerError):
            with owner.owner_lease(): entered.append(True)
        assert not entered and len(leases) == 1 and leases[0].check() is False
        assert owner._uncertain and not owner._held and not owner._busy
        assert not r.hardware._mutex.locked() and not r.state.rows
        assert not r.client._client.is_closed
