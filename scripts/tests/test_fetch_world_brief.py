import datetime as dt
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'fetch_world_brief', ROOT / 'lantern/fetch-world-brief.py')
brief = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brief)

DAY = dt.date(2026, 9, 20)
# Shape copied from Portal:Current events/2026_September_19: bare '''Topic''' headings,
# an article chain of * / ** / *** bullets, and prose only on the leaf bullets.
WIKITEXT = """{{Current events|year=2026|month=09|day=20|top=yes}}

<!-- All news items below this line -->
'''Armed conflicts and attacks'''
*[[Middle Eastern crisis (2023-present)|Middle Eastern crisis]]
**[[2026 Iran war]]
***[[2026 Iran war ceasefire]]
****[[Supreme National Security Council]] [[Secretary of the Supreme National Security Council|Secretary]] [[Mohsen Rezaee]] says that [[Iran]] has set seven conditions for resuming [[ceasefire]] talks. [https://www.example.invalid/x (''Iran International'')]
'''Science and technology'''
*[[2026 Asian Games]]
**[[Japan|Japan]]ese [[Triathlon|triathlete]] {{ill|Takumi Hojo|ja|北條巧}} wins the first [[gold medal]] of the Games, finishing the 750-meter swim and the 20-kilometer bike ride. [https://www.example.invalid/y (Japan Times)]
*** Short.
<!-- All news items above this line -->

{{Current events|year=2026|month=09|day=20|bottom=yes}}"""


def test_parse_day_extracts_dated_topic_rows():
    rows = brief.parse_day(DAY, WIKITEXT)
    assert [row['id'] for row in rows] == ['wb-20260920-01', 'wb-20260920-02']
    assert all(row['date'] == '2026-09-20' for row in rows)
    assert all(row['source'] == 'https://en.wikipedia.org/wiki/Portal:Current_events/2026_September_20'
               for row in rows)
    assert all(row['licence'] == 'CC-BY-SA-4.0' for row in rows)
    first, second = rows
    assert first['title'] == 'Armed conflicts and attacks'
    assert first['tags'] == ['world-brief', 'armed-conflicts-and-attacks']
    assert first['text'].startswith('Supreme National Security Council Secretary Mohsen Rezaee says')
    assert first['text'].endswith('(Iran International)')
    assert 'example.invalid' not in first['text'] and 'https' not in first['text']
    assert second['title'] == 'Science and technology'
    assert 'Takumi Hojo wins the first gold medal' in second['text']
    # The article chain (*, **, ***) is context, not an event, and "*** Short." is too short.
    assert len(rows) == 2


def test_parse_day_drops_short_lines_and_reports_nothing_for_empty_pages():
    assert len(brief.parse_day(DAY, WIKITEXT)) == 2
    assert brief.parse_day(DAY, "{{Current events|year=2026|month=09|day=20}}\n'''See also'''\n") == []
    assert brief.parse_day(DAY, "") == []


def test_heading_only_bullets_are_not_events():
    assert brief.is_heading_only('[[2026 Iran war]]') is True
    assert brief.is_heading_only('[[Japan at the 2026 Asian Games]], [[Triathlon at the 2026 Asian Games]]') is True
    assert brief.is_heading_only('{{ill|Takumi Hojo|ja|北條巧}}') is True
    assert brief.is_heading_only('[[U.S. military]] strikes a vessel in the [[Caribbean Sea]].') is False


def test_a_bare_heading_without_links_is_not_an_event():
    # Seen twice in 93 days of real data: a 43-character heading that is long enough
    # to pass the length rule and carries no link for the heading check to notice.
    assert brief.looks_like_an_event('Aftermath of the South Sudanese Civil War') is False
    assert brief.looks_like_an_event('Qatar is attacked by projectiles from Iran. (Al Jazeera)') is True
    assert brief.looks_like_an_event('The 20th Asian Games are opened in Nagoya, Japan.') is True


def test_inline_templates_keep_the_words_they_carry():
    assert brief.clean('{{ill|Takumi Hojo|ja|北條巧}} wins') == 'Takumi Hojo wins'
    assert brief.clean('{{lang|ja|北條巧}} wins') == '北條巧 wins'
    assert brief.clean('{{nowrap|20 km}} ride') == '20 km ride'


def test_strip_templates_handles_nesting_and_unbalanced_input():
    assert brief.strip_templates('a{{b}}c') == 'ac'
    assert brief.strip_templates('a{{b{{c}}d}}e') == 'ae'
    assert brief.strip_templates('a{{unclosed') == 'a'
    assert brief.strip_templates('{{only}}') == ''


def test_clean_unwraps_links_and_entities():
    text = brief.clean("[[Kuala Lumpur]]'s [[Menara KL|tower]] is 421 m &ndash; [[File:x.jpg|thumb|cap]]")
    # The dangling dash from the removed file link is trimmed with the rest of the padding.
    assert text == "Kuala Lumpur's tower is 421 m"


def test_page_title_and_url_use_the_portal_naming():
    day = dt.date(2026, 9, 5)
    assert brief.page_title(day) == 'Portal:Current events/2026_September_5'
    assert brief.page_url(day) == 'https://en.wikipedia.org/wiki/Portal:Current_events/2026_September_5'
    # The API answers with spaces where we asked with underscores; lookups must match anyway.
    assert brief.canonical('Portal:Current events/2026_September_5') == \
        brief.canonical('Portal:Current events/2026 September 5')


def test_range_days_is_inclusive_and_bounded():
    days = brief.range_days(dt.date(2026, 6, 20), dt.date(2026, 9, 20))
    assert len(days) == 93 and days[0] == dt.date(2026, 6, 20) and days[-1] == dt.date(2026, 9, 20)
    assert brief.range_days(DAY, DAY) == [DAY]
    with pytest.raises(brief.BriefError):
        brief.range_days(DAY, dt.date(2026, 9, 19))
    with pytest.raises(brief.BriefError):
        brief.range_days(DAY, dt.date(2028, 9, 20))


def test_parse_date_refuses_junk():
    assert brief.parse_date('2026-09-20') == DAY
    with pytest.raises(brief.BriefError):
        brief.parse_date('20/09/2026')
