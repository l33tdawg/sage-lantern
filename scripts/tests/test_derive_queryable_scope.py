"""Tests for the queryable-size scope cut (offline: pure helpers only).

The cut is what makes a scaled pack measurable on a unit, so these tests pin the
two properties that matter: it never invents a pair the source scope does not
sanction, and it never drops a seed the source scope names.
"""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'derive_queryable_scope', ROOT / 'lantern/derive-queryable-scope.py')
cut_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cut_module)


SOURCE = {
    '_provenance': {'seeds': {'entities': ['Q90', 'Q35765']}},
    'populations': ['Q1', 'Q2', 'Q3', 'Q4', 'Q5'],
    'countries': ['Q6', 'Q7'],
    'heights': ['Q8'],
    'pairs': [['Q1', 'Q2'], ['Q1', 'Q3']],
}


def test_cut_keeps_rank_order_and_whole_small_sections():
    result = cut_module.cut(SOURCE, populations=3, pairs_top=0)
    assert result['populations'] == ['Q1', 'Q2', 'Q3']
    assert result['countries'] == ['Q6', 'Q7']
    assert result['heights'] == ['Q8']


def test_cut_pairs_are_pairwise_and_anchor_pairs_deduplicated():
    result = cut_module.cut(SOURCE, populations=0, pairs_top=3,
                            pairs_anchor='Q1', pairs_anchor_top=5)
    assert result['pairs'] == [['Q1', 'Q2'], ['Q1', 'Q3'], ['Q2', 'Q3'],
                               ['Q1', 'Q4'], ['Q1', 'Q5']]


def test_cut_forces_seeds_into_the_pair_selection():
    result = cut_module.cut(SOURCE, populations=1, pairs_top=1, seeds=('Q5',))
    assert ['Q1', 'Q5'] in result['pairs']
    assert result['populations'] == ['Q1', 'Q5']


def test_cut_inherits_seeds_from_the_source_provenance():
    assert cut_module.inherit_seeds(SOURCE) == ('Q90', 'Q35765')
    assert cut_module.inherit_seeds({}) == ()


def test_cut_refuses_a_scope_that_is_not_qids():
    for broken in ({'populations': ['Q1', 'tokyo']},
                   {'pairs': [['Q1']]},
                   {'pairs': [['Q1', 'tokyo']]}):
        try:
            cut_module.cut(broken, populations=0, pairs_top=0)
        except cut_module.CutError:
            continue
        raise AssertionError(f'{broken!r} should have been refused')
