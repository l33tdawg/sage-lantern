"""Tests for the Wikidata reference-facts adapter, driven by the real sample.

The fixture is a live ``wbgetentities`` response for six entities, committed
before the parser existed (``c3461f2``). The label fixture beside it is the
one-hop administrative-parent lookup those entities reference (Kinta District,
Greater London, Grand Paris and so on); the live adapter fetches it itself.
"""

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'fetch_wikidata_facts', ROOT / 'lantern/fetch-wikidata-facts.py')
wd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wd)

SAMPLE = json.loads((ROOT / 'tests/fixtures/wikidata-entities-sample.json').read_text('utf-8'))
LABEL_MAP = json.loads(
    (ROOT / 'tests/fixtures/wikidata-entities-sample-labels.json').read_text('utf-8'))
ENTITY_IDS = ['Q7969454', 'Q271619', 'Q1490', 'Q35765', 'Q84', 'Q90']
PAIRS = [('Q1490', 'Q35765'), ('Q84', 'Q90')]


def sample_rows(facts='heights,populations,distances,geography'):
    labels = wd.collect_response(SAMPLE)
    labels.update(LABEL_MAP)
    return wd.fact_rows(SAMPLE, labels, facts.split(','), PAIRS)[0]


def row_for(rows, identifier):
    matches = [item for item in rows if item['id'] == identifier]
    assert len(matches) == 1, f"expected exactly one {identifier}, found {len(matches)}"
    return matches[0]


def test_merdeka_height_is_the_metric_statement():
    # Wikidata carries both +678.9 m and +2227 ft for the same building. The
    # foot value is a parenthetical for the reader; the fact is the metric one.
    height = row_for(sample_rows(), 'wd-q7969454-height')
    assert height['text'] == 'Merdeka 118 is 678.9 metres (2,227 feet) tall.'
    assert height['source'] == 'https://www.wikidata.org/entity/Q7969454'


def test_feet_only_height_is_converted_and_marked_about(monkeypatch):
    # Petronas Towers carries +1483 ft and no metric statement; the fact is the
    # conversion, and the text says "about" because the metres were derived.
    response = {'entities': {'Q1': {
        'id': 'Q1', 'labels': {'en': {'language': 'en', 'value': 'Petronas Towers'}},
        'claims': {'P2048': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {
            'type': 'quantity', 'value': {'amount': '+1483',
                                          'unit': 'http://www.wikidata.org/entity/Q3710'}}}}]}}}}
    monkeypatch.setattr(wd, 'HEIGHT_ENTITIES', ('Q1',))
    rows, skipped = wd.fact_rows(response, wd.collect_response(response), ['heights'], [])
    assert skipped == []
    assert rows[0]['text'] == 'Petronas Towers is about 452 metres (1,483 feet) tall.'


def test_populations_prefer_rank_then_newest_point_in_time():
    rows = sample_rows()
    ipoh = row_for(rows, 'wd-q271619-population')
    assert ipoh['text'] == 'The population of Ipoh was 866,772 as of 2016.'
    assert ipoh['date'] == '2016'
    # Tokyo's preferred 2022 statement wins over the 2020 census and the 2010
    # estimate; the deprecated 38,140,000 never appears.
    tokyo = row_for(rows, 'wd-q1490-population')
    assert tokyo['text'] == 'The population of Tokyo was 14,264,798 as of 2022.'
    # Osaka's preferred 2021 value carries day precision, not a census method.
    osaka = row_for(rows, 'wd-q35765-population')
    assert osaka['text'] == 'The population of Osaka was 2,751,862 as of 1 March 2021.'
    assert osaka['date'] == '2021-03-01'
    # London's preferred value is the 2021 census, and the historic series stays out.
    london = row_for(rows, 'wd-q84-population')
    assert london['text'] == 'The population of London was 8,799,728 at the 2021 census.'
    paris = row_for(rows, 'wd-q90-population')
    assert paris['text'] == 'The population of Paris was 2,103,778 at the 2023 census.'
    for text in (item['text'] for item in rows):
        assert '38,140,000' not in text
        assert '13,159,388' not in text and '12,576,611' not in text


def test_distance_is_great_circle_kilometres():
    rows = sample_rows()
    tokyo_osaka = row_for(rows, 'wd-distance-q1490-q35765')
    assert tokyo_osaka['text'] == ('Tokyo and Osaka are about 396 kilometres (246 miles) '
                                   'apart in a straight line.')
    london_paris = row_for(rows, 'wd-distance-q84-q90')
    assert london_paris['text'] == ('London and Paris are about 344 kilometres (214 miles) '
                                    'apart in a straight line.')


def test_administrative_geography_uses_admin_and_country():
    rows = sample_rows()
    ipoh = row_for(rows, 'wd-q271619-located-in')
    assert ipoh['text'] == 'Ipoh is in Kinta District, Malaysia.'
    # Tokyo's P131 is Japan itself; the country is not repeated.
    tokyo = row_for(rows, 'wd-q1490-located-in')
    assert tokyo['text'] == 'Tokyo is in Japan.'
    london = row_for(rows, 'wd-q84-located-in')
    assert london['text'] == 'London is in Greater London, the United Kingdom.'
    merdeka = row_for(rows, 'wd-q7969454-located-in')
    assert merdeka['text'] == 'Merdeka 118 is in Kuala Lumpur, Malaysia.'


def test_every_row_is_cc0_with_its_entity_url():
    rows = sample_rows()
    assert rows == sorted(rows, key=lambda item: item['id'])
    for item in rows:
        assert item['licence'] == 'CC0-1.0'
        assert item['source'].startswith('https://www.wikidata.org/entity/Q')
        assert item['source'].rsplit('/', 1)[-1].startswith('Q')
        assert 'world-facts' in item['tags']
        assert item['text'].strip() and item['id'].startswith('wd-')
        assert wd.LICENCE == 'CC0-1.0'


def test_scope_drives_the_rows_not_the_curated_globals():
    # A scaled pack passes its own lists; the committed globals are only one
    # possible scope, so a scope of one entity must produce that entity's rows.
    scope = {'heights': [], 'populations': ['Q271619'], 'countries': [], 'pairs': []}
    labels = wd.collect_response(SAMPLE)
    labels.update(LABEL_MAP)
    rows, skipped = wd.fact_rows(SAMPLE, labels, ['populations', 'geography'], [], scope)
    assert [item['id'] for item in rows] == ['wd-q271619-located-in', 'wd-q271619-population']
    assert skipped == []


def test_scope_file_round_trip_and_refusals(tmp_path):
    path = tmp_path / 'scope.json'
    path.write_text(json.dumps({
        'heights': ['Q12495'],
        'populations': ['Q1865', 'Q271619'],
        'countries': [],
        'pairs': [['Q1490', 'Q35765']],
        '_provenance': {'generator': 'enumerate-wikidata-entities.py'},
    }), 'utf-8')
    scope = wd.load_scope(path)
    assert scope['heights'] == ['Q12495']
    assert scope['populations'] == ['Q1865', 'Q271619']
    assert scope['pairs'] == [['Q1490', 'Q35765']]
    # A missing section is empty, not an error: a distances-only scope is legal.
    path.write_text(json.dumps({'pairs': [['Q1490', 'Q35765']]}), 'utf-8')
    assert wd.load_scope(path) == {'heights': [], 'populations': [], 'countries': [],
                                   'pairs': [['Q1490', 'Q35765']]}
    for bad in ('Q0', '1490', 'not-a-qid', ''):
        path.write_text(json.dumps({'populations': [bad]}), 'utf-8')
        with pytest.raises(wd.FactError):
            wd.load_scope(path)
    path.write_text(json.dumps({'pairs': [['Q1490']]}), 'utf-8')
    with pytest.raises(wd.FactError):
        wd.load_scope(path)
    path.write_text('[]', 'utf-8')
    with pytest.raises(wd.FactError):
        wd.load_scope(path)


def test_slim_dump_rebuilds_the_same_rows():
    # The live payload is ~180 KB per entity; a scaled scope cannot keep that.
    # The slim copy must still rebuild the identical rows off-line.
    slim = wd.slim_response(SAMPLE)
    assert set(slim['entities']) == set(SAMPLE['entities'])
    for identifier, entity in slim['entities'].items():
        assert set(entity['claims']) <= set(wd.SCOPE_PROPERTIES)
        assert entity['labels'] == SAMPLE['entities'][identifier]['labels']
    full_labels = wd.collect_response(SAMPLE)
    full_labels.update(LABEL_MAP)
    slim_labels = wd.collect_response(slim)
    slim_labels.update(LABEL_MAP)
    for facts in ('heights,populations,distances,geography', 'geography'):
        assert (wd.fact_rows(slim, slim_labels, facts.split(','), PAIRS)[0]
                == wd.fact_rows(SAMPLE, full_labels, facts.split(','), PAIRS)[0])
    assert len(json.dumps(slim)) < len(json.dumps(SAMPLE))


def test_entity_without_an_english_label_is_skipped_not_fatal():
    # The scaled scope (3,900 entities) contains 13 items with no English
    # label; a pack cannot ship a fact without a name, and it must not crash
    # the run either - the skip is reported.
    response = {'entities': {'Q1': {
        'id': 'Q1', 'labels': {},
        'claims': {
            'P1082': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {
                'type': 'quantity', 'value': {'amount': '+1000', 'unit': '1'}}},
                'qualifiers': {'P585': [{'datavalue': {
                    'type': 'time', 'value': {'time': '+2020-00-00T00:00:00Z', 'precision': 9}}}]}}],
            'P131': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {
                'type': 'wikibase-entityid', 'value': {'entity-type': 'item', 'id': 'Q2'}}}}],
        }}}}
    scope = {'heights': [], 'populations': ['Q1'], 'countries': [], 'pairs': []}
    rows, skipped = wd.fact_rows(response, {'Q2': 'Somewhere'}, ['populations', 'geography'], [],
                                 scope)
    assert rows == []
    assert skipped == ['Q1: no English label in the response']


def test_distance_pair_without_a_label_is_skipped():
    response = {'entities': {
        'Q1': {'id': 'Q1', 'labels': {'en': {'value': 'One'}},
               'claims': {'P625': [{'rank': 'normal', 'mainsnak': {
                   'snaktype': 'value', 'datavalue': {'type': 'globecoordinate', 'value': {
                       'latitude': 1.0, 'longitude': 101.0, 'globe': ''}}}}]}},
        'Q2': {'id': 'Q2', 'labels': {}, 'claims': {'P625': [{'rank': 'normal', 'mainsnak': {
            'snaktype': 'value', 'datavalue': {'type': 'globecoordinate', 'value': {
                'latitude': 2.0, 'longitude': 102.0, 'globe': ''}}}}]}},
    }}
    scope = {'heights': [], 'populations': [], 'countries': [], 'pairs': [['Q1', 'Q2']]}
    rows, skipped = wd.fact_rows(response, wd.collect_response(response), ['distances'],
                                 [('Q1', 'Q2')], scope)
    assert rows == []
    assert skipped == ['Q1-Q2: an endpoint has no English label']


def test_repeated_pairs_collapse_to_one_chunk():
    # A chunk is addressed by its content, so the same pair twice is the same
    # chunk twice - the builder refuses that, correctly.
    assert wd.dedupe_pairs([('Q1', 'Q2'), ('Q2', 'Q1'), ('Q1', 'Q3')]) == [
        ('Q1', 'Q2'), ('Q1', 'Q3')]


def _population_claim(amount, year='2020'):
    return {'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {
        'type': 'quantity', 'value': {'amount': amount, 'unit': '1'}}},
        'qualifiers': {'P585': [{'datavalue': {
            'type': 'time', 'value': {'time': f'+{year}-00-00T00:00:00Z',
                                      'precision': 9}}}]}}


def _coordinate_claim(latitude, longitude):
    return {'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {
        'type': 'globecoordinate', 'value': {'latitude': latitude,
                                             'longitude': longitude, 'globe': ''}}}}


def test_entity_in_both_lists_is_parsed_once():
    # Singapore is a city and a sovereign state; one entity, one chunk - two
    # identical content objects are one content address.
    response = {'entities': {'Q334': {
        'id': 'Q334', 'labels': {'en': {'value': 'Singapore'}},
        'claims': {'P1082': [_population_claim('+5866139', '2021')]}}}}
    scope = {'heights': [], 'populations': ['Q334'], 'countries': ['Q334'],
             'pairs': []}
    rows, skipped = wd.fact_rows(response, wd.collect_response(response),
                                 ['populations'], [], scope)
    assert [item['id'] for item in rows] == ['wd-q334-population']
    assert skipped == []


def test_an_ambiguous_place_name_carries_its_country():
    # Two cities named Yulin in different countries: without the country the
    # two distance rows are the same sentence about different places.
    response = {'entities': {
        'Q1': {'id': 'Q1', 'labels': {'en': {'value': 'Yulin'}},
               'claims': {'P625': [_coordinate_claim(22.63, 110.15)],
                          'P17': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value',
                                  'datavalue': {'type': 'wikibase-entityid', 'value': {
                                      'entity-type': 'item', 'id': 'Q148'}}}}]}},
        'Q2': {'id': 'Q2', 'labels': {'en': {'value': 'Yulin'}},
               'claims': {'P625': [_coordinate_claim(38.28, 109.73)],
                          'P17': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value',
                                  'datavalue': {'type': 'wikibase-entityid', 'value': {
                                      'entity-type': 'item', 'id': 'Q148'}}}}]}},
        'Q3': {'id': 'Q3', 'labels': {'en': {'value': 'Huanggang'}},
               'claims': {'P625': [_coordinate_claim(30.45, 114.87)],
                          'P17': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value',
                                  'datavalue': {'type': 'wikibase-entityid', 'value': {
                                      'entity-type': 'item', 'id': 'Q148'}}}}]}},
    }}
    labels = wd.collect_response(response)
    labels['Q148'] = 'China'
    scope = {'heights': [], 'populations': ['Q1', 'Q2', 'Q3'], 'countries': [],
             'pairs': [['Q1', 'Q3']]}
    rows, _ = wd.fact_rows(response, labels, ['distances', 'geography'], [('Q1', 'Q3')], scope)
    distance = [item for item in rows if 'distance' in item['tags']][0]
    assert distance['text'].startswith('Yulin, China and Huanggang are about')
    # An unambiguous name stays plain.
    assert not any('Huanggang, China' in item['text'] for item in rows)


def test_duplicate_items_with_the_same_sentence_ship_once():
    # Wikidata holds duplicate items; two rows with identical text are one fact.
    response = {'entities': {
        'Q1': {'id': 'Q1', 'labels': {'en': {'value': 'Minna'}},
               'claims': {'P1082': [_population_claim('+304113')]}},
        'Q2': {'id': 'Q2', 'labels': {'en': {'value': 'Minna'}},
               'claims': {'P1082': [_population_claim('+304113')]}},
    }}
    scope = {'heights': [], 'populations': ['Q1', 'Q2'], 'countries': [], 'pairs': []}
    rows, skipped = wd.fact_rows(response, wd.collect_response(response),
                                 ['populations'], [], scope)
    assert [item['id'] for item in rows] == ['wd-q1-population']
    assert skipped == ['wd-q2-population: same text as wd-q1-population']


def test_malaysian_entries_carry_the_malaysia_tag():
    rows = sample_rows()
    ipoh = row_for(rows, 'wd-q271619-population')
    assert 'malaysia' in ipoh['tags']
    tokyo = row_for(rows, 'wd-q1490-population')
    assert 'malaysia' not in tokyo['tags']


def test_country_articles_are_prose_not_data(monkeypatch):
    response = {'entities': {'Q55': {
        'id': 'Q55', 'labels': {'en': {'language': 'en', 'value': 'Netherlands'}},
        'claims': {
            'P1082': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {
                'type': 'quantity', 'value': {'amount': '+18191100', 'unit': '1'}}},
                'qualifiers': {'P585': [{'datavalue': {'value': {
                    'time': '+2025-00-00T00:00:00Z', 'precision': 9}}}]}}],
            'P36': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {
                'type': 'wikibase-entityid', 'value': {
                    'entity-type': 'item', 'id': 'Q9899'}}}}]}}}}
    monkeypatch.setattr(wd, 'COUNTRY_ENTITIES', ('Q55',))
    monkeypatch.setattr(wd, 'POPULATION_ENTITIES', ())
    labels = wd.collect_response(response)
    labels['Q9899'] = 'Amsterdam'
    rows, skipped = wd.fact_rows(response, labels, ['populations', 'geography'], [])
    texts = {item['text'] for item in rows}
    assert skipped == []
    assert 'The population of the Netherlands was 18,191,100 as of 2025.' in texts
    assert 'Amsterdam is the capital of the Netherlands.' in texts


def test_missing_entity_stops_the_run(tmp_path, monkeypatch):
    arguments = ['fetch-wikidata-facts.py', '--out', str(tmp_path / 'facts.jsonl'),
                 '--offline', str(ROOT / 'tests/fixtures/wikidata-entities-sample.json'),
                 '--entities', 'Q7969454,Q99999999999',
                 '--facts', 'heights']
    monkeypatch.setattr(sys, 'argv', arguments)
    with pytest.raises(wd.FactError) as error:
        wd.main()
    assert 'Q99999999999' in str(error.value)


def test_undated_population_is_skipped_not_invented(monkeypatch):
    response = {'entities': {'Q1': {
        'id': 'Q1', 'labels': {'en': {'language': 'en', 'value': 'Nowhere'}},
        'claims': {'P1082': [{'mainsnak': {'snaktype': 'value', 'datavalue': {
            'type': 'quantity', 'value': {'amount': '+10', 'unit': '1'}}}, 'rank': 'normal'}]}}}}
    monkeypatch.setattr(wd, 'POPULATION_ENTITIES', ('Q1',))
    monkeypatch.setattr(wd, 'COUNTRY_ENTITIES', ())
    labels = wd.collect_response(response)
    assert wd.population_fact(response['entities']['Q1']) is None
    rows, skipped = wd.fact_rows(response, labels, ['populations'], [])
    assert rows == []
    assert skipped == ['Q1: no dated P1082 population']


def test_deprecated_rank_is_never_a_candidate():
    statement = {'rank': 'deprecated', 'mainsnak': {'snaktype': 'value', 'datavalue': {
        'type': 'quantity', 'value': {'amount': '+1', 'unit': '1'}}}}
    assert wd.ranked([statement]) == []


def test_point_in_time_wording_tracks_precision():
    def qualifier(time, precision):
        return {'qualifiers': {'P585': [{'datavalue': {'value': {
            'time': time, 'precision': precision}}}]}}

    assert wd.point_in_time(qualifier('+2016-00-00T00:00:00Z', 9)) == \
        ((2016, 0, 0), '2016', '2016')
    assert wd.point_in_time(qualifier('+2021-03-00T00:00:00Z', 10)) == \
        ((2021, 3, 0), 'March 2021', '2021-03')
    assert wd.point_in_time(qualifier('+2021-03-01T00:00:00Z', 11)) == \
        ((2021, 3, 1), '1 March 2021', '2021-03-01')
    assert wd.point_in_time({'qualifiers': {}}) is None


def test_large_positive_amounts_keep_commas_and_decimals_keep_their_shape():
    from decimal import Decimal

    assert wd.format_amount(Decimal('866772')) == '866,772'
    assert wd.format_amount(Decimal('678.90')) == '678.9'
    assert wd.format_amount(Decimal('2227')) == '2,227'


def test_rows_build_a_reference_pack(tmp_path, monkeypatch):
    source = tmp_path / 'world-facts.jsonl'
    with source.open('w', encoding='utf-8') as handle:
        for item in sample_rows():
            handle.write(json.dumps(item, sort_keys=True) + '\n')
    arguments = ['build-knowledge-pack.py', str(source), '--out', str(tmp_path / 'pack'),
                 '--pack-id', 'world-facts', '--version', '1',
                 '--title', 'World reference facts', '--class', 'reference',
                 '--licence', 'CC0-1.0', '--source-label', 'wikidata:cc0-20260920',
                 '--created-at', '2026-09-20T00:00:00Z', '--default-tag', 'world-facts']
    monkeypatch.setattr(sys, 'argv', arguments)
    builder = importlib.util.spec_from_file_location(
        'build_knowledge_pack', ROOT / 'lantern/build-knowledge-pack.py')
    module = importlib.util.module_from_spec(builder)
    builder.loader.exec_module(module)
    module.main()
    pack = module.knowledge_pack.load_pack(tmp_path / 'pack', allow_unsigned=True)
    assert pack.pack_class == 'reference'
    assert pack.manifest['pack']['licence'] == 'CC0-1.0'
    assert pack.manifest['totals']['chunks'] == len(sample_rows())
    assert {record['licence'] for record in pack.chunks.values()} == {'CC0-1.0'}
    assert {record['confidence_bp'] for record in pack.chunks.values()} == {9500}


def test_cli_writes_jsonl_from_the_fixture(tmp_path, monkeypatch):
    out = tmp_path / 'facts.jsonl'
    arguments = ['fetch-wikidata-facts.py', '--out', str(out),
                 '--offline', str(ROOT / 'tests/fixtures/wikidata-entities-sample.json'),
                 '--labels', str(ROOT / 'tests/fixtures/wikidata-entities-sample-labels.json'),
                 '--entities', ','.join(ENTITY_IDS),
                 '--pairs', 'Q1490-Q35765,Q84-Q90',
                 '--facts', 'heights,populations,distances,geography']
    monkeypatch.setattr(sys, 'argv', arguments)
    wd.main()
    written = [json.loads(line) for line in out.read_text('utf-8').splitlines() if line.strip()]
    assert len(written) == len(sample_rows())
    assert {item['id'] for item in written} == {item['id'] for item in sample_rows()}
    assert all(item['licence'] == 'CC0-1.0' for item in written)


def snak(prop, datatype, value):
    return {'mainsnak': {'snaktype': 'value', 'property': prop,
                         'datavalue': {'value': value, 'type': datatype}},
            'rank': 'normal'}


def place(identifier, label, *, country='Q833', coordinate=None, population=None):
    """One entity in the shape the parser reads, for the twin rules."""
    claims = {}
    if country:
        claims['P17'] = [snak('P17', 'wikibase-entityid',
                              {'id': country, 'entity-type': 'item'})]
    if coordinate:
        claims['P625'] = [snak('P625', 'globecoordinate',
                               {'latitude': coordinate[0], 'longitude': coordinate[1],
                                'globe': 'http://www.wikidata.org/entity/Q2'})]
    if population is not None:
        statement = snak('P1082', 'quantity', {'amount': f'+{population}', 'unit': '1'})
        statement['qualifiers'] = {'P585': [
            {'datavalue': {'value': {'time': '+2020-00-00T00:00:00Z', 'precision': 9},
                           'type': 'time'}}]}
        statement['qualifiers-order'] = ['P585']
        claims['P1082'] = [statement]
    return {'id': identifier, 'labels': {'en': {'language': 'en', 'value': label}},
            'claims': claims}


def entities(*places):
    return {item['id']: item for item in places}


def labels_of(response):
    return {identifier: entity['labels']['en']['value']
            for identifier, entity in response['entities'].items()}


def test_twins_merge_identical_labels_in_the_same_country():
    response = {'entities': entities(
        place('Q1490', 'Tokyo', coordinate=(35.68, 139.76), population=14000000),
        place('Q7473516', 'Tokyo', coordinate=(35.68, 139.70), population=9640742))}
    twins = wd.twin_representatives(response['entities'], labels_of(response),
                                    ['Q1490', 'Q7473516'])
    # The more populous entity speaks for the place, and the duplicate is
    # flagged as carrying the identical label so its own facts are dropped.
    assert twins == {'Q7473516': ('Q1490', True)}


def test_twins_keep_the_same_label_in_another_country_or_far_away():
    response = {'entities': entities(
        place('Q84', 'London', country='Q145', coordinate=(51.507, -0.128), population=8799728),
        place('Q92561', 'London', country='Q16', coordinate=(42.98, -81.25), population=383822),
        place('Q1', 'Springfield', country='Q30', coordinate=(37.21, -93.29), population=169176),
        place('Q2', 'Springfield', country='Q30', coordinate=(42.10, -72.59), population=155929))}
    assert wd.twin_representatives(response['entities'], labels_of(response),
                                   ['Q84', 'Q92561', 'Q1', 'Q2']) == {}


def test_twins_merge_a_metropolitan_qualifier_but_not_a_lookalike_city():
    response = {'entities': entities(
        place('Q13580', 'Metro Manila', coordinate=(14.609, 121.022), population=14001751),
        place('Q1461', 'Manila', coordinate=(14.599, 120.984), population=1902590),
        place('Q1867', 'Taipei', coordinate=(25.033, 121.565), population=2706030),
        place('Q244898', 'New Taipei City', coordinate=(25.011, 121.462), population=4364124))}
    twins = wd.twin_representatives(response['entities'], labels_of(response),
                                    ['Q13580', 'Q1461', 'Q1867', 'Q244898'])
    # "New Taipei City" is not "Taipei City": only the qualifier-around-the-same
    # -name case collapses, and it keeps the more populous entity.
    assert twins == {'Q1461': ('Q13580', False)}


def test_fact_rows_emits_one_distance_and_drops_the_duplicate_facts():
    response = {'entities': entities(
        place('Q1490', 'Tokyo', coordinate=(35.68, 139.76), population=14000000),
        place('Q7473516', 'Tokyo', coordinate=(35.68, 139.70), population=9640742),
        place('Q35765', 'Osaka', coordinate=(34.69, 135.50), population=2751862))}
    scope = {'heights': [], 'populations': ['Q1490', 'Q7473516', 'Q35765'],
             'countries': [], 'pairs': [['Q1490', 'Q35765'], ['Q7473516', 'Q35765'],
                                        ['Q1490', 'Q7473516']]}
    rows, skipped = wd.fact_rows(response, labels_of(response),
                                 ['populations', 'distances'], scope['pairs'], scope)
    assert [row['id'] for row in rows if row['id'].startswith('wd-distance-')] == \
        ['wd-distance-q1490-q35765']
    population_ids = {row['id'] for row in rows if row['id'].endswith('-population')}
    assert 'wd-q1490-population' in population_ids
    assert 'wd-q7473516-population' not in population_ids
    assert any('duplicate item of Q1490' in note for note in skipped)
    assert any('same place as wd-distance-q1490-q35765' in note for note in skipped)
