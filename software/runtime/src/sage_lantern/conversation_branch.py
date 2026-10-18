"""Replay one retained conversation branch; never erase or rewrite its records.

Callers validate saved row schemas before using this helper. Content field names
are irrelevant: question/answer and transcript/response rows use the same IDs,
timestamps, and correction metadata. An active Snapshot is already authoritative
and must not be replayed as though it were a complete saved inventory.
"""
from datetime import datetime
from itertools import groupby


def _created(row):
    return datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))


def effective_thread_items(items, thread_id):
    """Return original rows in the current branch of the requested saved thread.

    Legacy unthreaded records are independent conversations identified by session_id.
    A correction discards its target and later descendants from the visible branch.
    If suppression/retention hid the target, the old prefix cannot be proved and is
    discarded from this view. Retained storage and suppression policy are unchanged.

    Identical timestamps cannot order a follow-up around an edit. Omit those tied
    plain rows and retain only a latest edit established by one complete causal
    chain. Ambiguous or cyclic tied edits retain only the provable earlier prefix.
    """
    rows = [row for row in items if row.get('thread_id', row['session_id']) == thread_id]
    rows.sort(key=_created)
    result = []
    for _, cohort in groupby(rows, key=_created):
        cohort = list(cohort)
        edits = {row['session_id']: row for row in cohort if row.get('supersedes')}
        if not edits:
            result.extend(cohort)
            continue

        cohort_ids = {row['session_id'] for row in cohort}
        cut = len(result)
        for row in edits.values():
            target = row['supersedes']
            index = next((index for index, previous in enumerate(result)
                          if previous['session_id'] == target), None)
            if index is not None:
                cut = min(cut, index)
            elif target not in cohort_ids:
                cut = 0
        result = result[:cut]

        replaced = {row['supersedes'] for row in edits.values()}
        terminals = set(edits) - replaced
        if len(terminals) != 1:
            continue
        terminal = next(iter(terminals))
        current = terminal
        visited = set()
        while current in edits and current not in visited:
            visited.add(current)
            current = edits[current]['supersedes']
        if len(visited) == len(edits) and current not in edits:
            result.append(edits[terminal])
    return result
