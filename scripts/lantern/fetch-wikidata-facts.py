#!/usr/bin/env python3
"""Fetch CC0 numeric facts from Wikidata into pack-source JSONL.

    python3 scripts/lantern/fetch-wikidata-facts.py \
        --out work/reference-facts-20260920/world-facts.jsonl \
        --dump-raw work/reference-facts-20260920/raw/wikidata-entities.json

The measured gap on the 2B is numeric specifics: Merdeka 118 as 118 m (real
678.9 m), Ipoh's population as 200,000 (real 866,772), Tokyo-Osaka as 130 km
(real 396 km), London-Paris as 160 km (real 344 km). That class of fact is
permanent, so it travels as a reference pack built from Wikidata's structured
statements rather than prose: Wikidata is CC0, which is why the pack can carry
numbers without a share-alike obligation.

One chunk per fact, every chunk ``licence: CC0-1.0`` and ``source`` set to the
entity URL (``https://www.wikidata.org/entity/Q...``). The per-item licence is
written on every row even though the pack is uniformly CC0 - the builder's
mixed-source gate then has something real to verify if this source is ever
combined with CC BY-SA material.

Parsing rules, all of them learned from the six-entity sample committed as
``scripts/tests/fixtures/wikidata-entities-sample.json`` (fixture first, parser
second):

* Rank preference. ``preferred`` statements are the only candidates when any
  exist; otherwise ``normal`` ones are used. ``deprecated`` is never used - the
  Tokyo item carries a deprecated 38,140,000 that must not come back.
* Unit mapping. Heights (P2048) arrive as both metres (Q11573) and feet (Q3710)
  for the same building; the metric statement is the fact and the foot
  statement is only a parenthetical. If only an imperial statement exists, it is
  converted and the text says "about". Populations (P1082) must be
  dimensionless (unit ``1``).
* Newest point in time. A population statement carries its date in the P585
  qualifier, and one city can carry several (Tokyo has 2020 and 2010; London
  has every census since 1801). Within the winning rank band the newest P585
  wins, and its precision decides the wording: ``as of 2016`` for year
  precision, ``as of 1 March 2021`` for day precision. The date is carried into
  the text because a reference pack installs as plain text with no "as of"
  frame.
* Straight-line distance. Distances come from P625 coordinates with the
  haversine formula, never from a statement - a curated pair of entities gets a
  fact only when both carry Earth coordinates.

The curated entity list below is the pack's scope: landmarks for heights, major
cities and countries for populations, a short pair list for straight-line
distances, and capitals plus located-in statements for administrative
geography. Entities are fetched by QID in batches of 50; ``--dump-raw`` and
``--dump-labels`` save the response and the administrative-parent labels, and
``--offline`` plus ``--labels`` read them back so a build is reproducible
without the network.
"""

from __future__ import annotations

import argparse
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


API = "https://www.wikidata.org/w/api.php"
USER_AGENT = ("SageLanternWikidataFacts/1.0 (SAGE Lantern offline knowledge packs; "
              "https://sage.ngo)")
LICENCE = "CC0-1.0"
ENTITY_URL = "https://www.wikidata.org/entity/"
BATCH = 50

P_HEIGHT = "P2048"
P_POPULATION = "P1082"
P_POINT_IN_TIME = "P585"
P_COORDINATES = "P625"
P_LOCATED_IN = "P131"
P_COUNTRY = "P17"
P_CAPITAL = "P36"
P_METHOD = "P459"

Q_METRE = "Q11573"
Q_FOOT = "Q3710"
Q_KILOMETRE = "Q828224"
Q_CENTIMETRE = "Q174728"
Q_CENSUS = "Q39825"
Q_EARTH = "Q2"
MALAYSIA = "Q833"

# Units convertible to metres for the height fallback. The metric statement is
# preferred when it exists; conversion is the honest path for an item that only
# carries an imperial one.
HEIGHT_UNITS = {Q_METRE: Decimal("1"), Q_CENTIMETRE: Decimal("0.01"),
                Q_FOOT: Decimal("0.3048"), Q_KILOMETRE: Decimal("1000")}
KM_PER_MILE = Decimal("1.609344")

# The measured probes live in these groups. Heights are structures; populations
# are the cities and countries a household asks about; the distance pairs are
# the straight-line class the 2B got wrong (Tokyo-Osaka 130 km, London-Paris
# 160 km) plus nearby pairs a Malaysian household is likely to ask about.
#
# Three candidates were measured and left out because the data does not carry
# the fact: Marina Bay Sands has no P2048 statement at all, while Shah Alam and
# Kota Kinabalu carry P1082 with no P585 qualifier - an undated population is
# not a fact this pack will ship (the run reports such skips when they appear).

# English needs the article on a few country names; this is prose, not data, so
# it lives here rather than being derived from the entity.
ARTICLE_COUNTRIES = {
    "Q145": "the United Kingdom",
    "Q30": "the United States",
    "Q55": "the Netherlands",
}
HEIGHT_ENTITIES = (
    "Q12495",     # Burj Khalifa
    "Q7969454",   # Merdeka 118
    "Q83063",     # Petronas Towers
    "Q83101",     # Taipei 101
    "Q18547",     # Shanghai Tower
    "Q243",       # Eiffel Tower
    "Q9188",      # Empire State Building
    "Q9202",      # Statue of Liberty
    "Q45178",     # Sydney Opera House
    "Q134883",    # CN Tower
    "Q57965",     # Tokyo Skytree
    "Q745016",    # Kuala Lumpur Tower
    "Q39054",     # Leaning Tower of Pisa
    "Q48435",     # Sagrada Familia
    "Q44440",     # Golden Gate Bridge
    "Q41225",     # Big Ben (Elizabeth Tower)
    "Q62939",     # Burj Al Arab
    "Q11245",     # One World Trade Center
    "Q18536",     # The Shard
)

POPULATION_ENTITIES = (
    "Q1865",      # Kuala Lumpur
    "Q271619",    # Ipoh
    "Q231318",    # Johor Bahru
    "Q864965",    # Petaling Jaya
    "Q61089",     # Malacca City
    "Q220445",    # Kuching
    "Q474868",    # Alor Setar
    "Q846502",    # Kuala Terengganu
    "Q334",       # Singapore
    "Q1490",      # Tokyo
    "Q35765",     # Osaka
    "Q84",        # London
    "Q90",        # Paris
    "Q60",        # New York City
    "Q956",       # Beijing
    "Q8686",      # Shanghai
    "Q1353",      # Delhi
    "Q1156",      # Mumbai
    "Q3630",      # Jakarta
    "Q1861",      # Bangkok
    "Q1461",      # Manila
    "Q1858",      # Hanoi
    "Q8684",      # Seoul
    "Q1867",      # Taipei
    "Q8646",      # Hong Kong
    "Q3130",      # Sydney
    "Q3141",      # Melbourne
    "Q612",       # Dubai
    "Q1519",      # Abu Dhabi
    "Q406",       # Istanbul
    "Q85",        # Cairo
    "Q3870",      # Nairobi
    "Q8673",      # Lagos
    "Q174",       # Sao Paulo
    "Q1489",      # Mexico City
    "Q649",       # Moscow
    "Q64",        # Berlin
    "Q2807",      # Madrid
    "Q220",       # Rome
    "Q172",       # Toronto
    "Q24639",     # Vancouver
    "Q65",        # Los Angeles
    "Q1297",      # Chicago
    "Q727",       # Amsterdam
    "Q1741",      # Vienna
    "Q656",       # Saint Petersburg
    "Q87",        # Alexandria
)

# Countries carry a population and their capital (P36) - administrative
# geography in its most useful form.
COUNTRY_ENTITIES = (
    "Q833",       # Malaysia
    "Q17",        # Japan
    "Q142",       # France
    "Q145",       # United Kingdom
    "Q252",       # Indonesia
    "Q869",       # Thailand
    "Q408",       # Australia
    "Q668",       # India
    "Q148",       # China
    "Q30",        # United States
    "Q79",        # Egypt
    "Q114",       # Kenya
    "Q1033",      # Nigeria
    "Q155",       # Brazil
    "Q96",        # Mexico
    "Q159",       # Russia
    "Q183",       # Germany
    "Q29",        # Spain
    "Q38",        # Italy
    "Q16",        # Canada
    "Q55",        # Netherlands
    "Q40",        # Austria
    "Q884",       # South Korea
)

DISTANCE_PAIRS = (
    ("Q1490", "Q35765"),   # Tokyo - Osaka (the 130 km probe)
    ("Q84", "Q90"),        # London - Paris (the 160 km probe)
    ("Q1865", "Q271619"),  # Kuala Lumpur - Ipoh
    ("Q1865", "Q334"),     # Kuala Lumpur - Singapore
    ("Q334", "Q3630"),     # Singapore - Jakarta
    ("Q956", "Q8686"),     # Beijing - Shanghai
    ("Q3130", "Q3141"),    # Sydney - Melbourne
    ("Q90", "Q64"),        # Paris - Berlin
    ("Q612", "Q1519"),     # Dubai - Abu Dhabi
    ("Q60", "Q84"),        # New York - London
    ("Q1490", "Q8684"),    # Tokyo - Seoul
    ("Q85", "Q87"),        # Cairo - Alexandria
    ("Q649", "Q656"),      # Moscow - Saint Petersburg
)

FACT_TYPES = ("heights", "populations", "distances", "geography")
FACT_TAG = {"height": "heights", "population": "populations", "distance": "distances",
            "geography": "geography"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


class FactError(RuntimeError):
    pass


def entity_qid(value) -> str:
    """The QID inside a wikibase-entityid value, or '' for anything else."""
    if isinstance(value, dict) and value.get("entity-type") == "item":
        identifier = value.get("id", "")
        return identifier if isinstance(identifier, str) and identifier.startswith("Q") else ""
    return ""


def entity_label(entity: dict, language: str = "en") -> str:
    return ((entity.get("labels") or {}).get(language) or {}).get("value", "")


def display_label(identifier: str, labels: dict) -> str:
    return ARTICLE_COUNTRIES.get(identifier) or labels.get(identifier, "")


def statements(entity: dict, prop: str) -> list:
    return [statement for statement in (entity.get("claims") or {}).get(prop, [])
            if isinstance(statement, dict)]


def datavalue(statement: dict):
    """The typed datavalue (``{"type": ..., "value": ...}``), or None."""
    snak = statement.get("mainsnak") or {}
    if snak.get("snaktype") != "value":
        return None
    value = snak.get("datavalue")
    return value if isinstance(value, dict) else None


def snak_value(statement: dict):
    """The payload inside the typed datavalue, or None."""
    return (datavalue(statement) or {}).get("value")


def qualifier_values(statement: dict, prop: str) -> list:
    return [(qualifier.get("datavalue") or {}).get("value")
            for qualifier in (statement.get("qualifiers") or {}).get(prop, [])]


def ranked(statements_in: list) -> list:
    """Preferred statements when any exist, else normal ones; deprecated never."""
    usable = [statement for statement in statements_in if statement.get("rank") != "deprecated"]
    preferred = [statement for statement in usable if statement.get("rank") == "preferred"]
    return preferred or [statement for statement in usable if statement.get("rank") == "normal"]


def quantity(statement: dict):
    typed = datavalue(statement)
    if not isinstance(typed, dict) or typed.get("type") != "quantity":
        return None, ""
    value = typed.get("value")
    if not isinstance(value, dict):
        return None, ""
    amount, unit = value.get("amount", ""), value.get("unit", "")
    unit_qid = unit.rsplit("/", 1)[-1] if isinstance(unit, str) else ""
    try:
        return Decimal(amount), unit_qid
    except (InvalidOperation, TypeError):
        return None, unit_qid


def format_amount(value: Decimal) -> str:
    """678.90 -> '678.9', 866772 -> '866,772'."""
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return format(value.normalize(), "f")


def point_in_time(statement: dict):
    """(sort key, display date, ISO-ish date) from the P585 qualifier, or None.

    Wikidata times are '+2016-00-00T00:00:00Z' with the precision in its own
    field: 9 is a year, 10 a month, 11 a day. The sort key pads missing
    components with zero so precisions compare sensibly.
    """
    values = qualifier_values(statement, P_POINT_IN_TIME)
    if not values:
        return None
    value = values[0]
    if not isinstance(value, dict):
        return None
    raw = value.get("time", "")
    precision = value.get("precision") or 0
    try:
        year, month, day = int(raw[1:5]), int(raw[6:8]), int(raw[9:11])
    except (TypeError, ValueError):
        return None
    if not 1 <= month <= 12:
        month = 0
    if not 1 <= day <= 31:
        day = 0
    if precision >= 11 and month and day:
        return ((year, month, day), f"{day} {MONTHS[month - 1]} {year}",
                f"{year:04d}-{month:02d}-{day:02d}")
    if precision >= 10 and month:
        return ((year, month, 0), f"{MONTHS[month - 1]} {year}", f"{year:04d}-{month:02d}")
    return ((year, 0, 0), str(year), f"{year:04d}")


def population_fact(entity: dict):
    """The newest preferred population statement, or None; never a deprecated one."""
    candidates = []
    for statement in ranked(statements(entity, P_POPULATION)):
        amount, unit = quantity(statement)
        if amount is None or unit != "1":
            continue
        when = point_in_time(statement)
        if when is None:
            continue
        candidates.append((when[0], amount, when[1], when[2], statement))
    if not candidates:
        return None
    sort_key, amount, display, iso_date, statement = max(candidates, key=lambda item: item[0])
    census = bool(qualifier_values(statement, P_METHOD)
                  and Q_CENSUS in [entity_qid(value)
                                   for value in qualifier_values(statement, P_METHOD)])
    return {"number": format_amount(amount), "display": display, "date": iso_date,
            "census": census}


def height_fact(entity: dict):
    """Metric height, with the foot statement (when present) as a parenthetical."""
    metric, feet, converted = [], [], False
    for statement in ranked(statements(entity, P_HEIGHT)):
        amount, unit = quantity(statement)
        if amount is None or unit not in HEIGHT_UNITS:
            continue
        if unit == Q_METRE:
            metric.append(amount)
        elif unit == Q_FOOT:
            feet.append(amount)
            converted = converted or not metric
        else:
            metric.append(amount * HEIGHT_UNITS[unit])
    if not metric and feet:
        # Feet-only items exist (Petronas Towers carries 1483 ft and no metric
        # statement); convert, and the text will say "about".
        metric = [feet[0] * HEIGHT_UNITS[Q_FOOT]]
        converted = True
    if not metric:
        return None
    metres = metric[0].quantize(Decimal("0.1")) if converted else metric[0]
    return {"metres": format_amount(metres), "feet": format_amount(feet[0]) if feet else "",
            "converted": converted}


def coordinate(entity: dict):
    """(latitude, longitude) from an Earth P625 statement, or None."""
    for statement in ranked(statements(entity, P_COORDINATES)):
        typed = datavalue(statement)
        if not isinstance(typed, dict) or typed.get("type") != "globecoordinate":
            continue
        value = typed.get("value")
        if not isinstance(value, dict):
            continue
        if value.get("globe") not in ("", None, f"http://www.wikidata.org/entity/{Q_EARTH}"):
            continue
        latitude, longitude = value.get("latitude"), value.get("longitude")
        if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
            return float(latitude), float(longitude)
    return None


def haversine_km(first, second) -> float:
    """Great-circle distance in kilometres (mean Earth radius 6371.0088 km)."""
    lat1, lon1 = math.radians(first[0]), math.radians(first[1])
    lat2, lon2 = math.radians(second[0]), math.radians(second[1])
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    value = (math.sin(delta_lat / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2)
    return 2 * 6371.0088 * math.asin(math.sqrt(value))


def located_in_fact(entity: dict, labels: dict) -> str:
    """'{label} is in {admin}, {country}.' from P131 and P17, or ''."""
    own = entity.get("id", "")
    admin = ""
    for statement in ranked(statements(entity, P_LOCATED_IN)):
        target = entity_qid(snak_value(statement))
        if target and target != own and labels.get(target):
            admin = labels[target]
            break
    country, country_id = "", ""
    for statement in ranked(statements(entity, P_COUNTRY)):
        target = entity_qid(snak_value(statement))
        if target and labels.get(target):
            country = labels[target]
            country_id = target
            break
    if not labels.get(own, ""):
        return ""
    if admin and country and admin != country:
        return f"{labels[own]} is in {admin}, {display_label(country_id, labels)}."
    if country:
        return f"{labels[own]} is in {display_label(country_id, labels)}."
    if admin:
        return f"{labels[own]} is in {admin}."
    return ""


def capital_fact(entity: dict, labels: dict) -> str:
    """'{capital} is the capital of {country}.' (or the plural form), or ''."""
    capitals = []
    for statement in ranked(statements(entity, P_CAPITAL)):
        target = entity_qid(snak_value(statement))
        name = labels.get(target, "")
        if name and name not in capitals:
            capitals.append(name)
    country_id = entity.get("id", "")
    country = display_label(country_id, labels)
    if not capitals or not country:
        return ""
    if len(capitals) == 1:
        return f"{capitals[0]} is the capital of {country}."
    joined = ", ".join(capitals[:-1]) + f" and {capitals[-1]}"
    return f"{country} has several capitals: {joined}."


def licence_gate(item: dict) -> dict:
    """Refuse any item that would travel without its own stated licence.

    Every Wikidata statement is CC0, so a row that names another licence - or
    none - is a parser bug, not a source variance, and must stop the build.
    """
    if item.get("licence") != LICENCE:
        raise FactError(f"chunk {item.get('id', '?')} carries licence "
                        f"{item.get('licence')!r}, expected {LICENCE!r} for Wikidata")
    if not item.get("source", "").startswith(ENTITY_URL):
        raise FactError(f"chunk {item.get('id', '?')} must name its entity URL as source")
    return item


def row(identifier: str, text: str, *, title: str, tags: list, source: str,
        date: str = "") -> dict:
    return {
        "id": identifier,
        "text": text,
        "title": title,
        "tags": tags,
        "source": source,
        "date": date,
        "licence": LICENCE,
    }


def fact_rows(response: dict, labels: dict, facts, pairs) -> tuple:
    """Parse a wbgetentities response into (rows, skipped).

    skipped names each entity/fact that produced nothing, so a run reports what
    it could not pin down instead of quietly shipping a thinner pack.
    """
    entities = response.get("entities") or {}
    rows, skipped = [], []

    def known(identifier: str):
        entity = entities.get(identifier)
        if not isinstance(entity, dict) or entity.get("missing") is not None:
            return None
        return entity

    def malaysian(entity: dict) -> bool:
        return any(entity_qid(snak_value(statement)) == MALAYSIA
                   for statement in statements(entity, P_COUNTRY))

    def tags_for(entity: dict, fact: str) -> list:
        tags = ["world-facts", fact]
        if malaysian(entity):
            tags.append("malaysia")
        return tags

    def add_location(identifier: str, fact: str):
        entity = known(identifier)
        if entity is None:
            return
        text = located_in_fact({**entity, "id": identifier}, labels)
        if text:
            rows.append(licence_gate(row(
                f"wd-{identifier.lower()}-located-in", text,
                title=f"{labels[identifier]}: location",
                tags=tags_for(entity, fact), source=ENTITY_URL + identifier)))

    if "heights" in facts:
        for identifier in HEIGHT_ENTITIES:
            entity = known(identifier)
            if entity is None:
                skipped.append(f"{identifier}: missing from the response")
                continue
            fact = height_fact(entity)
            if fact is None:
                skipped.append(f"{identifier}: no usable P2048 height")
                continue
            metres = f"about {fact['metres']}" if fact["converted"] else fact["metres"]
            text = f"{labels[identifier]} is {metres} metres"
            if fact["feet"]:
                text += f" ({fact['feet']} feet)"
            rows.append(licence_gate(row(
                f"wd-{identifier.lower()}-height", text + " tall.",
                title=f"{labels[identifier]}: height",
                tags=tags_for(entity, "height"), source=ENTITY_URL + identifier)))

    if "populations" in facts:
        for identifier in POPULATION_ENTITIES + COUNTRY_ENTITIES:
            entity = known(identifier)
            if entity is None:
                skipped.append(f"{identifier}: missing from the response")
                continue
            fact = population_fact(entity)
            if fact is None:
                skipped.append(f"{identifier}: no dated P1082 population")
                continue
            if fact["census"]:
                text = (f"The population of {display_label(identifier, labels)} was {fact['number']} "
                        f"at the {fact['date'][:4]} census.")
            else:
                text = (f"The population of {display_label(identifier, labels)} was {fact['number']} "
                        f"as of {fact['display']}.")
            rows.append(licence_gate(row(
                f"wd-{identifier.lower()}-population", text,
                title=f"{labels[identifier]}: population (as of {fact['display']})",
                tags=tags_for(entity, "population"),
                source=ENTITY_URL + identifier, date=fact["date"])))

    if "geography" in facts:
        for identifier in list(HEIGHT_ENTITIES) + list(POPULATION_ENTITIES):
            add_location(identifier, "geography")
        for identifier in COUNTRY_ENTITIES:
            entity = known(identifier)
            if entity is None:
                continue
            text = capital_fact({**entity, "id": identifier}, labels)
            if text:
                rows.append(licence_gate(row(
                    f"wd-{identifier.lower()}-capital", text,
                    title=f"{labels[identifier]}: capital",
                    tags=tags_for(entity, "geography"),
                    source=ENTITY_URL + identifier)))

    if "distances" in facts:
        for first, second in pairs:
            one, two = known(first), known(second)
            if one is None or two is None:
                skipped.append(f"{first}-{second}: an endpoint is missing from the response")
                continue
            first_coordinate, second_coordinate = coordinate(one), coordinate(two)
            if first_coordinate is None or second_coordinate is None:
                skipped.append(f"{first}-{second}: an endpoint has no Earth coordinates")
                continue
            kilometres = round(haversine_km(first_coordinate, second_coordinate))
            tags = ["world-facts", "distance"]
            if malaysian(one) or malaysian(two):
                tags.append("malaysia")
            rows.append(licence_gate(row(
                f"wd-distance-{first.lower()}-{second.lower()}",
                text=(
                    f"{labels[first]} and {labels[second]} are about "
                    f"{kilometres:,} kilometres "
                    f"({round(kilometres / float(KM_PER_MILE)):,} miles) apart "
                    "in a straight line."),
                title=f"{labels[first]}-{labels[second]}: straight-line distance",
                tags=tags, source=ENTITY_URL + first)))

    rows.sort(key=lambda item: item["id"])
    return rows, skipped


def collect_response(response: dict) -> dict:
    labels = {}
    for identifier, entity in (response.get("entities") or {}).items():
        if isinstance(entity, dict) and not entity.get("missing"):
            label = entity_label(entity)
            if label:
                labels[identifier] = label
    return labels


def referenced_qids(response: dict) -> list:
    """QIDs referenced by a ranked fact but not necessarily fetched themselves.

    Only the statements the parser would actually use are named, so a label
    fetch does not drag in every historical administrative parent a city lists.
    """
    wanted = set()
    for entity in (response.get("entities") or {}).values():
        if not isinstance(entity, dict):
            continue
        for prop in (P_LOCATED_IN, P_COUNTRY, P_CAPITAL):
            for statement in ranked(statements(entity, prop)):
                target = entity_qid(snak_value(statement))
                if target:
                    wanted.add(target)
    return sorted(wanted)


def fetch(ids: list, api: str, delay: float) -> dict:
    """One wbgetentities response for ids, batched and polite."""
    entities = {}
    for start in range(0, len(ids), BATCH):
        batch = ids[start:start + BATCH]
        query = urllib.parse.urlencode({
            "action": "wbgetentities", "ids": "|".join(batch),
            "props": "claims|labels", "languages": "en",
            "format": "json", "formatversion": "2"})
        request = urllib.request.Request(f"{api}?{query}", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as error:
            raise FactError(f"fetch of {len(batch)} entities failed: {error}") from error
        if payload.get("error"):
            raise FactError(f"Wikidata API error: {payload['error'].get('info', payload['error'])}")
        entities.update(payload.get("entities") or {})
        if delay:
            time.sleep(delay)
    return {"entities": entities, "success": 1}


def label_fetch(ids: list, api: str, delay: float) -> dict:
    """English labels for referenced entities (administrative parents, capitals)."""
    labels = {}
    for start in range(0, len(ids), BATCH):
        batch = ids[start:start + BATCH]
        query = urllib.parse.urlencode({
            "action": "wbgetentities", "ids": "|".join(batch),
            "props": "labels", "languages": "en",
            "format": "json", "formatversion": "2"})
        request = urllib.request.Request(f"{api}?{query}", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as error:
            raise FactError(f"label fetch failed: {error}") from error
        for identifier, entity in (payload.get("entities") or {}).items():
            if isinstance(entity, dict) and not entity.get("missing"):
                label = entity_label(entity)
                if label:
                    labels[identifier] = label
        if delay:
            time.sleep(delay)
    return labels


def parse_comma_list(value: str) -> list:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_pairs(value: str) -> list:
    pairs = []
    for part in parse_comma_list(value):
        halves = part.split("-")
        if len(halves) != 2 or not all(half.startswith("Q") for half in halves):
            raise FactError(f"{part!r} is not a pair like Q1490-Q35765")
        pairs.append((halves[0].strip(), halves[1].strip()))
    return pairs


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True, help="pack-source JSONL to write")
    parser.add_argument("--entities", default="",
                        help="comma-separated QIDs to fetch (default: the curated list)")
    parser.add_argument("--pairs", default="",
                        help="comma-separated QID-QID pairs for straight-line distances "
                             "(default: the curated list)")
    parser.add_argument("--facts", default=",".join(FACT_TYPES),
                        help=f"comma-separated fact types, any of {', '.join(FACT_TYPES)}")
    parser.add_argument("--offline", type=Path, default=None,
                        help="parse a saved wbgetentities response instead of fetching")
    parser.add_argument("--dump-raw", type=Path, default=None,
                        help="write the fetched response here for reproducible rebuilds")
    parser.add_argument("--dump-labels", type=Path, default=None,
                        help="write the fetched administrative-parent labels here; "
                             "pass the file back with --labels for an offline rebuild")
    parser.add_argument("--labels", type=Path, default=None,
                        help="JSON map of QID to label for referenced entities "
                             "(needed with --offline)")
    parser.add_argument("--api", default=API)
    parser.add_argument("--request-delay", type=float, default=1.0,
                        help="seconds between API calls; Wikimedia asks for a polite client")
    args = parser.parse_args()

    facts = parse_comma_list(args.facts)
    unknown = [fact for fact in facts if fact not in FACT_TYPES]
    if unknown:
        raise FactError(f"unknown fact type(s) {unknown}; choose from {list(FACT_TYPES)}")
    if not facts:
        raise FactError("--facts selected nothing")

    pair_list = parse_pairs(args.pairs) if args.pairs else list(DISTANCE_PAIRS)
    if args.entities:
        wanted = list(dict.fromkeys(parse_comma_list(args.entities)))
    else:
        wanted = list(dict.fromkeys(HEIGHT_ENTITIES + POPULATION_ENTITIES + COUNTRY_ENTITIES))
    if "distances" in facts:
        for first, second in pair_list:
            for identifier in (first, second):
                if identifier not in wanted:
                    wanted.append(identifier)

    if args.offline is not None:
        if not args.offline.is_file():
            raise FactError(f"{args.offline} does not exist")
        try:
            response = json.loads(args.offline.read_text("utf-8"))
        except json.JSONDecodeError as error:
            raise FactError(f"{args.offline} is not JSON: {error}") from error
    else:
        response = fetch(wanted, args.api, args.request_delay)
        if args.dump_raw is not None:
            args.dump_raw.parent.mkdir(parents=True, exist_ok=True)
            args.dump_raw.write_text(
                json.dumps(response, ensure_ascii=False, sort_keys=True), "utf-8")

    entities = response.get("entities") or {}
    missing = [identifier for identifier in wanted
               if not isinstance(entities.get(identifier), dict)
               or entities[identifier].get("missing") is not None]
    if missing:
        raise FactError(
            f"no entity for {', '.join(missing)}; the curated list must stay complete")

    labels = collect_response(response)
    if args.labels is not None:
        try:
            extra = json.loads(args.labels.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FactError(f"could not read {args.labels}: {error}") from error
        if not isinstance(extra, dict):
            raise FactError(f"{args.labels} must hold a JSON object of QID to label")
        labels.update({key: value for key, value in extra.items()
                       if isinstance(value, str) and value})
    if args.offline is None:
        parent_labels = label_fetch(referenced_qids(response), args.api, args.request_delay)
        for identifier, label in parent_labels.items():
            labels.setdefault(identifier, label)
        if args.dump_labels is not None:
            args.dump_labels.parent.mkdir(parents=True, exist_ok=True)
            args.dump_labels.write_text(
                json.dumps(parent_labels, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                "utf-8")

    rows, skipped = fact_rows(response, labels, facts, pair_list)
    if not rows:
        raise FactError("no facts parsed; check --facts and the response")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n")

    by_fact = {}
    for item in rows:
        fact = next((FACT_TAG[tag] for tag in item["tags"] if tag in FACT_TAG), "?")
        by_fact[fact] = by_fact.get(fact, 0) + 1
    print(json.dumps({"out": str(args.out), "entities": len(wanted), "facts": len(rows),
                      "by_fact": by_fact, "skipped": skipped, "licence": LICENCE,
                      "source": "wikidata:cc0"}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except FactError as error:
        print(f"fetch-wikidata-facts: {error}", file=sys.stderr)
        sys.exit(2)
