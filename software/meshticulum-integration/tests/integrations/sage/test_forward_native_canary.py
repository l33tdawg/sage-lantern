"""Generated canary through genuine provider/owner/enrollment composition.

SDK backend, hardware lifecycle/accounting and RF remain explicit models.
These controls do not qualify native private transport or encryption at rest.
"""
import json
import pytest
from meshticulum.privacy.mesh_envelope import open_envelope
from lantern_meshticulum.sage import forward_native_provider as provider
from lantern_meshticulum.sage.forward_budget import Cost
from tests.integrations.sage.native_canary_helpers import (CANARY_PRIOR_DEBT,SharedClock,
    canary_limits,generated_canary_pair,modeled_stage)


def test_fresh_canary_limits_preserve_prior_debt_and_all_five_enforced_child_caps(tmp_path):
    from meshticulum.bench.services import ServiceTransfer
    from lantern_meshticulum.sage.forward_operation import describe_child
    from tests.test_meshticulum_private_carrier import carrier_pair
    clock=SharedClock()
    service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=180000000)
    with carrier_pair(tmp_path/'caps',b'source-only-budget-control',clock,'unit-a','unit-b',1,
            service=service,original_expires_epoch_us=180000000) as carrier:
        for flow in (carrier.sender,carrier.receiver):
            cost=describe_child(ServiceTransfer(flow),slot=0,kind=1).cost
            assert cost==Cost(256,60000000)
            retained=CANARY_PRIOR_DEBT
            for _ in range(5):retained=retained.plus(cost)
            assert retained.within(canary_limits())
            assert not retained.plus(Cost(estimated_airtime_us=1)).within(canary_limits())
            assert not retained.within(Cost(2000,300000000,12000,10000000,10000000))


def test_generated_canary_crosses_five_scoped_stages_and_signed_endpoint_storage(tmp_path):
    with generated_canary_pair(provider) as pair:
        original=(pair.context.expires_epoch_us,pair.intention.application_deadline_monotonic_us)
        for index in range(5):
            result=modeled_stage(tmp_path,pair,index,replay=index==4)
            assert result['modeled_transport'] and result['physical_qualified'] is False
            assert all(v>0 for v in result['attempt_counts'])
            if index==4:assert result['replay_refusals']>0
            print('source_canary_stage_retired index='+str(index)+' stages=5',flush=True)
        accepted=pair.operations[1].settle();assert accepted.status=='admitted'
        row=pair.endpoints[1].state.rows[accepted.record_id]
        recovered=open_envelope(row['payload']['wire'],pair.keys[1],expected_sender=pair.peers[0],
            expected_recipient=pair.peers[1],now=pair.clock()[0]//1000000)
        assert recovered.content==pair.content
        assert accepted.record_id not in pair.endpoints[0].state.rows
        assert pair.content not in json.dumps([e.state.rows for e in pair.endpoints])
        assert (pair.context.expires_epoch_us,pair.intention.application_deadline_monotonic_us)==original
        assert pair.native_accounting_qualified is pair.private_transport_qualified is False
        assert all(p.owner._composition.journal.store is p.operation.budget.store for p in pair.providers)
        assert all(p.owner._composition.journal._lease is p.operation.budget._lease for p in pair.providers)
        assert all(p.retire() is True for p in pair.providers)
        assert all(s.actual_usage is None for s in pair.sources)
        assert all(not e.resource._mutex.locked() for e in pair.endpoints)
        assert all(s.closed for s in pair.sources)
        for p,op in zip(pair.providers,pair.operations):
            assert op.session._closed
            with pytest.raises(Exception):p.generate()
            with pytest.raises(Exception):p.start(sealed_record=pair.packet if p.local_initiator else None)


@pytest.mark.parametrize('fault',['owner','consent','deadline','accounting_debt','accounting_evidence',
    'accounting_replaced','unknown_changed_to_zero','debt_over_limit'])
def test_generated_provider_refuses_live_binding_drift_before_wire(fault):
    with generated_canary_pair(provider,start=False) as pair:
        left=pair.providers[0];rows=json.dumps(pair.endpoints[0].state.rows,sort_keys=True)
        if fault=='owner':pair.endpoints[0].state.owner_live=False
        elif fault=='consent':pair.endpoints[0].resource.config['dispatch_enabled']=False
        elif fault=='deadline':pair.clock.now=(pair.context.expires_epoch_us,pair.intention.application_deadline_monotonic_us)
        elif fault=='accounting_debt':pair.sources[0].retained_debt=Cost(8,8000,3,512,512)
        elif fault=='accounting_evidence':pair.sources[0].evidence_sha256='f'*64
        elif fault=='accounting_replaced':left.accounting=pair.sources[1]
        elif fault=='unknown_changed_to_zero':pair.sources[0].actual_usage=Cost()
        else:pair.sources[0].retained_debt=left.limits.plus(Cost(1))
        with pytest.raises(Exception):left.start(sealed_record=pair.packet)
        assert left._retained_debt==Cost(7,7000,3,512,512)
        assert left._operation is None
        assert json.dumps(pair.endpoints[0].state.rows,sort_keys=True)==rows
        assert left._accounting_binding and b'"actual_usage":null' in left._accounting_binding
