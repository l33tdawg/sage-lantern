"""Saved edit replay must never invent chronology from equal timestamps."""
from copy import deepcopy
from itertools import permutations

from sage_lantern.conversation_branch import effective_thread_items


THREAD = '00000000-0000-4000-8000-000000000090'


def identifier(number):
    return f'00000000-0000-4000-8000-{number:012d}'


def row(number, created='2026-10-09T10:00:02Z', *, supersedes=None):
    return {'session_id': identifier(number), 'thread_id': THREAD,
            'created_at': created, 'question': f'Question {number}',
            'answer': f'Answer {number}', 'has_photo': False,
            **({'supersedes': identifier(supersedes)} if supersedes is not None else {})}


def sessions(rows):
    return [item['session_id'] for item in effective_thread_items(rows, THREAD)]


def test_tied_saved_edit_never_revives_target_or_unordered_descendants():
    anchor = row(1, '2026-10-09T10:00:01Z')
    cohort = [row(2), row(3), row(4, supersedes=2)]
    for order in permutations(cohort):
        saved = [*order, anchor]
        before = deepcopy(saved)
        assert sessions(saved) == [identifier(1), identifier(4)]
        assert saved == before


def test_all_tied_plain_rows_around_edit_are_unprovable():
    assert sessions([row(1), row(2), row(4, supersedes=2)]) == [identifier(4)]


def test_tied_nested_corrections_have_one_causally_latest_edit():
    anchor = row(1, '2026-10-09T10:00:01Z')
    cohort = [row(2), row(3), row(4, supersedes=2), row(5, supersedes=4)]
    for order in permutations(cohort):
        assert sessions([*order, anchor]) == [identifier(1), identifier(5)]


def test_tied_edit_of_earlier_prefix_discards_its_known_descendants():
    saved = [row(1, '2026-10-09T10:00:00Z'), row(2, '2026-10-09T10:00:01Z'),
             row(3, '2026-10-09T10:00:01.500000Z'), row(4), row(5, supersedes=2)]
    assert sessions(saved) == [identifier(1), identifier(5)]


def test_independent_tied_edits_cannot_choose_a_latest_by_uuid_or_input_order():
    anchor = row(1, '2026-10-09T10:00:00Z')
    target = row(2, '2026-10-09T10:00:01Z')
    edits = [row(4, supersedes=2), row(5, supersedes=2)]
    for order in permutations(edits):
        assert sessions([*order, target, anchor]) == [identifier(1)]


def test_cyclic_tied_edits_do_not_supply_context():
    anchor = row(1, '2026-10-09T10:00:00Z')
    assert sessions([row(4, supersedes=5), row(5, supersedes=4), anchor]) == [identifier(1)]


def test_missing_edit_target_cannot_establish_a_retained_old_prefix():
    saved = [row(1, '2026-10-09T10:00:00Z'), row(4, supersedes=2),
             row(5, '2026-10-09T10:00:03Z')]
    assert sessions(saved) == [identifier(4), identifier(5)]


def test_offset_spellings_of_one_instant_share_the_same_ambiguity():
    anchor = row(1, '2026-10-09T09:59:59Z')
    target = row(2, '2026-10-09T12:00:00+02:00')
    descendant = row(3, '2026-10-09T10:00:00Z')
    edit = row(4, '2026-10-09T05:00:00-05:00', supersedes=2)
    assert sessions([edit, descendant, target, anchor]) == [identifier(1), identifier(4)]


def test_distinct_microseconds_establish_the_old_prefix():
    saved = [row(4, '2026-10-09T10:00:00.000004Z', supersedes=2),
             row(3, '2026-10-09T10:00:00.000003Z'),
             row(2, '2026-10-09T10:00:00.000002Z'),
             row(1, '2026-10-09T10:00:00.000001Z')]
    assert sessions(saved) == [identifier(1), identifier(4)]


def test_equal_plain_timestamps_preserve_supplied_order_without_inventing_edges():
    saved = [row(3), row(1), row(2)]
    assert effective_thread_items(saved, THREAD) == saved
