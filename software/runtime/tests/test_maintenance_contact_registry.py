import copy
from dataclasses import asdict

import pytest
from nacl.exceptions import BadSignatureError

from sage_lantern.maintenance_owner_ledgers import OwnerLedgerInspection
from sage_lantern.mesh_contact_registry import contact_record_id
from sage_lantern.workflow_store import WorkflowUnavailable
from test_mesh_contact_registry import setup, put


def records(setup):
    put(setup)
    return [setup.read(setup.registry.index_id), setup.read(contact_record_id(setup.owner, setup.actor))]


def checker(setup, rows):
    return OwnerLedgerInspection(rows, asdict(setup.owner), setup.registry.store,
        setup.identity, containment=setup.access)


def test_signed_contacts_retained_without_refreshing_consent_or_writing(setup):
    rows = records(setup)
    before = copy.deepcopy(rows)
    writes = len(setup.writes)
    setup.now = 1000
    inspection = checker(setup, rows)
    assert [inspection.inspect(row) for row in rows] == ['retained_issued', 'retained_issued']
    assert inspection.finish()
    assert rows == before and len(setup.writes) == writes


@pytest.mark.parametrize('missing', [0, 1])
def test_missing_index_or_member_refused(setup, missing):
    rows = records(setup)
    del rows[missing]
    with pytest.raises(WorkflowUnavailable):
        checker(setup, rows).inspect(rows[0])


def test_tampered_contact_signature_refused(setup):
    rows = records(setup)
    rows[1]['payload']['body']['card'] += ' '
    with pytest.raises(BadSignatureError):
        checker(setup, rows).inspect(rows[1])
