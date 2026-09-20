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
