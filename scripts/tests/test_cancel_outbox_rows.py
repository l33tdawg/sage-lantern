"""The on-unit outbox cancel tool decides eligibility, and reports what it wrote.

The transition itself (CAS, proofs, terminal semantics) is pinned by
software/runtime/tests/test_mesh_outbox.py; this pins the operator tool's own
rules, which are the part a unit window depends on: only lapsed pre-terminal
rows (or an admitted attempt that already reached for the wire), never a live
window the runtime can still complete, never finished peer evidence, and a lost
acknowledgement is read back rather than retried.

The one exception is the post-wire unknown (owner decision 2026-09-22): a
`dispatching` row is an admitted attempt whose fragment may already be on the
wire, so it is endable inside its window as well as after it, and the record
says which rule the owner acted under.
"""

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('cancel_outbox_rows', ROOT / 'scripts/lantern/cancel-outbox-rows.py')
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MESSAGE = '12345678-1234-4234-8234-123456789abc'
OTHER = '22345678-1234-4234-8234-123456789abc'
BINDING = 'owner-cancel-20260921'


def body(state, *, issued_at=100, expires_at=200, attempts=0, next_fragment=0, binding=''):
    return {'schema': 'sage.lantern.mesh-outbound.v1', 'state': state, 'attempts': attempts,
            'next_fragment': next_fragment, 'retry_binding': binding,
            'approved': {'issued_at': issued_at, 'expires_at': expires_at}}


@pytest.mark.parametrize('state,basis', [('queued', 'window_lapsed'), ('awaiting_receipt', 'window_lapsed'),
                                         ('dispatching', 'post_wire_unknown')])
def test_a_lapsed_row_that_never_became_evidence_is_the_owner_to_end(state, basis):
    # A lapsed attempt is endable on the stronger basis: it is an admitted
    # attempt, and that is the reason the record has to carry.
    assert MODULE.eligible(body(state), 200) == (True, basis)


@pytest.mark.parametrize('state', ['queued', 'awaiting_receipt'])
def test_a_live_window_with_nothing_on_the_wire_is_left_to_the_runtime(state):
    allowed, refused = MODULE.eligible(body(state, issued_at=100, expires_at=1000), 200)
    assert (allowed, refused) == (False, 'window_still_open')


def test_a_post_wire_unknown_is_endable_inside_its_window():
    """Unit A, 2026-09-22: six of these with live windows and every route 503.

    The swap that wrote `dispatching` precedes the radio write, so the fragment
    may already be on the wire and the runtime can only continue the row by
    sending it again. The owner's decision is the way out, in-window included,
    and the record names the basis.
    """
    allowed, verdict = MODULE.eligible(body('dispatching', attempts=2, next_fragment=3), 200)
    assert (allowed, verdict) == (True, 'post_wire_unknown')


def test_a_post_wire_unknown_still_yields_to_finished_evidence():
    delivered = body('dispatching', issued_at=100, expires_at=1000)
    delivered['state'] = 'cancelled'
    assert MODULE.eligible(delivered, 200) == (False, 'row_already_cancelled')
    assert MODULE.eligible({**delivered, 'state': 'delivered'}, 200) == (False, 'row_carries_peer_evidence')


@pytest.mark.parametrize('state,refused', [('delivered', 'row_carries_peer_evidence'),
                                           ('read', 'row_carries_peer_evidence'),
                                           ('cancelled', 'row_already_cancelled')])
def test_finished_evidence_and_an_existing_cancel_are_refused(state, refused):
    assert MODULE.eligible(body(state), 500) == (False, refused)


class Store:
    def __init__(self, rows):
        self.rows = rows

    def scan(self, *, limit, max_records, access_check):
        return list(self.rows)

    def get(self, record_id, *, kind, access_check):
        return next(row for row in self.rows if row['record_id'] == record_id)


class Outbox:
    """Records the decisions it is asked to make and can fail like a lost ack."""

    def __init__(self, bodies, *, fault=False, reread=None):
        self.bodies, self.fault, self.reread = bodies, fault, reread or {}
        self.calls = []
        self.guards = []

    def _verified(self, record):
        identifier = record['record_id']
        # The reread bodies only exist once the write has been attempted; until
        # then this is the row the scan sees.
        if self.calls and identifier in self.reread:
            return self.reread[identifier]
        return self.bodies[identifier]

    def cancel(self, message_id, *, expected_revision, decision_binding, access_check):
        self.calls.append((message_id, expected_revision, decision_binding))
        self.guards.append(access_check)
        if self.fault:
            raise RuntimeError('mesh_outbox_unavailable')
        self.bodies[message_id] = body('cancelled', binding=decision_binding)
        return type('Progress', (), {'state': 'cancelled', 'revision': expected_revision + 1})()


def records(*identifiers):
    return [dict(kind='mesh_outbound', record_id=identifier, revision=2) for identifier in identifiers]


def test_a_dry_run_lists_what_would_be_cancelled_and_writes_nothing():
    store = Store(records(MESSAGE))
    outbox = Outbox({MESSAGE: body('queued')})
    rows = MODULE.cancel_rows(store, outbox, lambda: True, now=300, binding=BINDING,
                              only=None, apply=False)
    assert outbox.calls == []
    assert rows == [{'message_id': MESSAGE, 'revision_before': 2, 'state_before': 'queued',
                     'attempts': 0, 'next_fragment': 0, 'seconds_since_window_closed': 100,
                     'window': 'lapsed', 'eligible_by': 'window_lapsed', 'outcome': 'would_cancel'}]


def test_the_live_owner_session_is_the_fence_the_tool_passes_through():
    store = Store(records(MESSAGE))
    outbox = Outbox({MESSAGE: body('queued')})
    guard = lambda: True
    MODULE.cancel_rows(store, outbox, guard, now=300, binding=BINDING, only=None, apply=True)
    # The same callable the tool got is handed to the outbox, so a session that
    # lapses between the scan and the CAS refuses the write (test_mesh_outbox
    # pins that refusal).
    assert outbox.guards == [guard]


def test_the_post_wire_unknown_is_applied_and_the_record_names_the_basis():
    store = Store(records(MESSAGE))
    outbox = Outbox({MESSAGE: body('dispatching', issued_at=100, expires_at=1000, attempts=7, next_fragment=4)})
    rows = MODULE.cancel_rows(store, outbox, lambda: True, now=200, binding=BINDING,
                              only=None, apply=True)
    assert outbox.calls == [(MESSAGE, 2, BINDING)]
    assert rows[0]['eligible_by'] == 'post_wire_unknown'
    assert rows[0]['window'] == 'open'
    assert rows[0]['state_before'] == 'dispatching'
    # The evidence of how far it got travels with the decision.
    assert (rows[0]['attempts'], rows[0]['next_fragment']) == (7, 4)
    assert rows[0]['outcome'] == 'confirmed' and rows[0]['state_after'] == 'cancelled'


def test_applying_cancels_the_row_and_records_the_decision():
    store = Store(records(MESSAGE))
    outbox = Outbox({MESSAGE: body('dispatching', attempts=2, next_fragment=0)})
    rows = MODULE.cancel_rows(store, outbox, lambda: True, now=300, binding=BINDING,
                              only=None, apply=True)
    assert outbox.calls == [(MESSAGE, 2, BINDING)]
    assert rows[0]['outcome'] == 'confirmed' and rows[0]['state_after'] == 'cancelled'
    assert rows[0]['state_before'] == 'dispatching' and rows[0]['attempts'] == 2


def test_a_lost_acknowledgement_is_read_back_not_retried():
    store = Store(records(MESSAGE))
    outbox = Outbox({MESSAGE: body('queued')}, fault=True,
                    reread={MESSAGE: body('cancelled', binding=BINDING)})
    rows = MODULE.cancel_rows(store, outbox, lambda: True, now=300, binding=BINDING,
                              only=None, apply=True)
    assert outbox.calls == [(MESSAGE, 2, BINDING)]
    assert rows[0]['outcome'] == 'confirmed_after_reread' and rows[0]['state_after'] == 'cancelled'


def test_an_outcome_that_stays_unknown_is_reported_and_not_repeated():
    store = Store(records(MESSAGE))
    outbox = Outbox({MESSAGE: body('queued')}, fault=True)
    rows = MODULE.cancel_rows(store, outbox, lambda: True, now=300, binding=BINDING,
                              only=None, apply=True)
    assert outbox.calls == [(MESSAGE, 2, BINDING)]
    assert rows[0]['outcome'] == 'unknown' and rows[0]['state_after'] == 'queued'


def test_naming_one_row_leaves_the_others_alone():
    store = Store(records(MESSAGE, OTHER))
    outbox = Outbox({MESSAGE: body('queued'), OTHER: body('queued')})
    rows = MODULE.cancel_rows(store, outbox, lambda: True, now=300, binding=BINDING,
                              only=OTHER, apply=True)
    assert outbox.calls == [(OTHER, 2, BINDING)]
    assert [row['message_id'] for row in rows] == [OTHER]


def test_a_row_that_is_not_an_outbox_body_is_skipped():
    store = Store(records(MESSAGE))
    outbox = Outbox({MESSAGE: {'schema': 'sage.lantern.mesh-receipt-replay.v1'}})
    assert MODULE.cancel_rows(store, outbox, lambda: True, now=300, binding=BINDING,
                              only=None, apply=True) == []
