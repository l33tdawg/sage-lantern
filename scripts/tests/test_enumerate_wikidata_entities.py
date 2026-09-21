"""Tests for the Wikidata scope enumerator (offline: pure helpers only).

The queries themselves are recorded in the scope's provenance block and run
against the live endpoint by hand; these tests pin the parsing, ranking and
pair generation so a scope cannot quietly change shape between runs.
"""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'enumerate_wikidata_entities', ROOT / 'lantern/enumerate-wikidata-entities.py')
enum = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enum)


def binding(identifier, value=None):
    entry = {}
    if identifier is not None:
        entry['item'] = {'value': f'{enum.ENTITY_URL}{identifier}'}
    if value is not None:
        entry['pop'] = {'value': str(value)}
    return entry


def test_binding_qid_accepts_entity_urls_only():
    assert enum.binding_qid({'value': f'{enum.ENTITY_URL}Q1490'}) == 'Q1490'
    assert enum.binding_qid({'value': 'Q1490'}) == ''
    assert enum.binding_qid({'value': f'{enum.ENTITY_URL}Q1490x'}) == ''
    assert enum.binding_qid({}) == ''


def test_ranked_cities_orders_by_population_and_dedupes():
    rows = [binding('Q1', 300), binding('Q2', 900), binding('Q1', 300),
            binding('Q3', 600), binding(None, 5), binding('Q4', None)]
    assert enum.ranked_cities(rows) == ['Q2', 'Q3', 'Q1']


def test_straight_line_pairs_are_pairwise_among_the_top():
    cities = ['Q1', 'Q2', 'Q3', 'Q4']
    assert enum.city_pairs(cities, 3) == [['Q1', 'Q2'], ['Q1', 'Q3'], ['Q2', 'Q3']]
    assert enum.city_pairs(cities, 0) == []
    assert enum.anchor_pairs(cities, 'Q9', 2) == [['Q9', 'Q1'], ['Q9', 'Q2']]
    # An anchor that is itself in the list does not pair with itself.
    assert enum.anchor_pairs(cities, 'Q1', 2) == [['Q1', 'Q2']]


def test_merge_pairs_keeps_one_entry_per_unordered_pair():
    pairwise = [['Q1', 'Q2'], ['Q1', 'Q3']]
    anchors = [['Q1', 'Q2'], ['Q4', 'Q1']]
    assert enum.merge_pairs(pairwise, anchors) == [['Q1', 'Q2'], ['Q1', 'Q3'], ['Q4', 'Q1']]


def test_seed_entities_are_forced_into_the_pair_selection():
    cities = ['Q1', 'Q2', 'Q3', 'Q4']
    # A seed outside the top cut is still paired: the facts a pack exists to fix
    # are not a popularity contest (Tokyo-Osaka and London-Paris went missing
    # from a 201-city list for exactly this reason).
    assert enum.city_pairs(cities, 2, ('Q9',)) == [
        ['Q1', 'Q2'], ['Q1', 'Q9'], ['Q2', 'Q9']]
    assert enum.anchor_pairs(cities, 'Q8', 2, ('Q9',)) == [
        ['Q8', 'Q1'], ['Q8', 'Q2'], ['Q8', 'Q9']]
    # A seed already inside the top cut is not paired with itself twice.
    assert enum.city_pairs(cities, 2, ('Q2',)) == [['Q1', 'Q2']]
    assert enum.pair_candidates(cities, 0, ('Q9',)) == ['Q9']


def test_seed_query_names_the_entities_and_asks_for_a_population():
    query = enum.seed_query(('Q90', 'Q35765'))
    assert 'wd:Q90' in query and 'wd:Q35765' in query
    assert 'P1082' in query


def test_class_list_covers_the_cities_the_first_scaled_run_missed():
    # Verified against P31 of the live items: Paris (Q90) carries
    # metropolis/national capital/largest city, Osaka (Q35765) the Japanese
    # designated-city classes. None of them was matched by the original five,
    # which is why the 27.8k-fact pack could not answer its own two examples.
    assert {'Q174844', 'Q200250', 'Q108178728', 'Q51929311'} <= set(enum.CITY_CLASSES)
    assert {'Q1749269', 'Q494721', 'Q65589340'} <= set(enum.CITY_CLASSES)


def test_queries_name_the_class_and_threshold():
    query = enum.city_query(20000)
    assert 'P1082' in query and '20000' in query
    assert all(identifier in query for identifier in enum.CITY_CLASSES)
    assert 'P279' not in query  # the subclass walk is not part of the row query
    assert enum.SOVEREIGN_STATE in enum.COUNTRY_QUERY and 'P1082' in enum.COUNTRY_QUERY
