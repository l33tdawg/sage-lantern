#!/usr/bin/env python3
"""Enumerate the Wikidata entities a scaled reference pack is scoped to.

    python3 scripts/lantern/enumerate-wikidata-entities.py \
        --out work/reference-facts-20260920/world-facts-v2-scope.json \
        --cities-min-population 20000 --countries \
        --pairs-top 120 --pairs-anchor Q1865 --pairs-anchor-top 400

The committed pack (`world-facts` v1, 191 facts) came from hand-written lists;
the practical pack class is tens of thousands of chunks, which no hand-written
list reaches. This writes the scope file the fetcher takes with ``--scope``:
three QID lists (cities with a population statement at or above the threshold,
sovereign states) and a distance-pair list generated from the city ranking.

Every query is recorded in the file's ``_provenance`` block, so a scope is
reproducible and reviewable rather than a list of numbers someone pasted. Two
WDQS realities shaped the queries, both learned by running them: the
``wdt:P31/wdt:P279*`` subclass walk is fine as a COUNT but returns 502 on a row
return at these sizes, so cities are matched against a bounded list of classes
(city, big city, town, million city, capital); and the ranking is done locally,
because an ORDER BY over a multi-thousand-row result is the difference between
a query that answers and one that times out.

Same terms as the rest of this adapter: Wikidata structured statements are CC0,
the endpoint is the public WDQS instance, and requests are spaced politely.
Enumeration is read-only; nothing here writes to Wikidata.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = ("SageLanternWikidata/1.0 (SAGE Lantern offline knowledge packs; "
              "https://sage.ngo)")
LICENCE = "CC0-1.0"
ENTITY_URL = "http://www.wikidata.org/entity/"

CITY_CLASS = "Q515"           # city
CITY_CLASSES = ("Q515",        # city
                "Q1549591",    # big city
                "Q3957",       # town
                "Q1637706",    # city with millions of inhabitants
                "Q5119")       # capital city
SOVEREIGN_STATE = "Q3624078"  # sovereign state


class EnumerateError(RuntimeError):
    pass


def city_query(minimum: int) -> str:
    classes = " ".join(f"wd:{identifier}" for identifier in CITY_CLASSES)
    return (
        "SELECT ?item ?pop WHERE { "
        "?item wdt:P31 ?class ; wdt:P1082 ?pop . "
        f"VALUES ?class {{ {classes} }} "
        f"FILTER(?pop >= {minimum}) }}"
    )


COUNTRY_QUERY = (
    "SELECT ?item ?pop WHERE { "
    f"?item wdt:P31 wd:{SOVEREIGN_STATE} ; wdt:P1082 ?pop . }}"
)


def binding_qid(binding: dict) -> str:
    """The QID inside one SPARQL result binding, or '' for anything else."""
    value = ((binding or {}).get("value") or "")
    if isinstance(value, str) and value.startswith(ENTITY_URL):
        identifier = value[len(ENTITY_URL):]
        if identifier.startswith("Q") and identifier[1:].isdigit():
            return identifier
    return ""


def binding_number(binding: dict) -> float | None:
    value = ((binding or {}).get("value") or "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def city_pairs(cities: list, top: int) -> list:
    """All pairs among the ``top`` most populous cities (``cities`` is ordered)."""
    chosen = cities[:max(0, top)]
    return [[first, second]
            for index, first in enumerate(chosen)
            for second in chosen[index + 1:]]


def anchor_pairs(cities: list, anchor: str, top: int) -> list:
    """Pairs from one anchor to the ``top`` most populous cities."""
    return [[anchor, city] for city in cities[:max(0, top)] if city != anchor]


def merge_pairs(*groups) -> list:
    """Concatenate pair lists, keeping one entry per unordered pair.

    The anchor pairs overlap the pairwise set whenever the anchor is itself in
    the city list (Kuala Lumpur is), and two identical pairs are two identical
    chunks - which the pack builder refuses, correctly, because a chunk is
    addressed by its content.
    """
    merged, seen = [], set()
    for group in groups:
        for pair in group:
            key = tuple(sorted(pair))
            if key in seen:
                continue
            seen.add(key)
            merged.append(list(pair))
    return merged


def sparql(query: str, *, endpoint: str, delay: float, timeout: int = 240) -> list:
    """Rows of one SELECT, as {variable: binding} dicts."""
    request = urllib.request.Request(
        f"{endpoint}?{urllib.parse.urlencode({'query': query})}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as error:
        raise EnumerateError(f"query failed: {error}\n{query}") from error
    if delay:
        time.sleep(delay)
    return ((payload.get("results") or {}).get("bindings") or [])


def ranked_cities(rows: list) -> list:
    """QIDs ordered by population, most populous first, duplicates removed."""
    scored = []
    for row in rows:
        identifier = binding_qid(row.get("item") or {})
        population = binding_number(row.get("pop") or {})
        if identifier and population is not None:
            scored.append((population, identifier))
    scored.sort(key=lambda item: (-item[0], item[1]))
    ordered, seen = [], set()
    for _, identifier in scored:
        if identifier not in seen:
            seen.add(identifier)
            ordered.append(identifier)
    return ordered


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True, help="scope JSON to write")
    parser.add_argument("--cities-min-population", type=int, default=0,
                        help="include cities at or above this population (0 disables)")
    parser.add_argument("--countries", action="store_true",
                        help="include sovereign states that carry a population")
    parser.add_argument("--heights", default="",
                        help="comma-separated QIDs for the heights list "
                             "(default: none - the curated ones stay a separate scope)")
    parser.add_argument("--pairs-top", type=int, default=0,
                        help="pairwise distances among the N most populous cities")
    parser.add_argument("--pairs-anchor", default="",
                        help="QID to pair against the most populous cities")
    parser.add_argument("--pairs-anchor-top", type=int, default=0,
                        help="how many cities the anchor pairs with")
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=240,
                        help="seconds to wait for one query; a row-returning class "
                             "query over a low population threshold is heavy")
    args = parser.parse_args()

    if not (args.cities_min_population or args.countries):
        raise EnumerateError("nothing to enumerate; pass --cities-min-population "
                             "and/or --countries")

    cities, queries = [], []
    if args.cities_min_population:
        query = city_query(args.cities_min_population)
        rows = sparql(query, endpoint=args.endpoint, delay=args.request_delay,
                      timeout=args.timeout)
        cities = ranked_cities(rows)
        queries.append({"what": "cities", "query": query, "rows": len(rows)})
        print(json.dumps({"cities": len(cities), "rows": len(rows)}), file=sys.stderr)

    countries = []
    if args.countries:
        rows = sparql(COUNTRY_QUERY, endpoint=args.endpoint, delay=args.request_delay,
                      timeout=args.timeout)
        countries = ranked_cities(rows)
        queries.append({"what": "countries", "query": COUNTRY_QUERY, "rows": len(rows)})
        print(json.dumps({"countries": len(countries), "rows": len(rows)}), file=sys.stderr)

    pairs = []
    if args.pairs_top:
        pairs = merge_pairs(pairs, city_pairs(cities, args.pairs_top))
    if args.pairs_anchor:
        pairs = merge_pairs(pairs, anchor_pairs(cities, args.pairs_anchor,
                                                args.pairs_anchor_top))

    height_qids = [part.strip() for part in args.heights.split(",") if part.strip()]
    scope = {
        "_provenance": {
            "generator": "scripts/lantern/enumerate-wikidata-entities.py",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endpoint": args.endpoint,
            "licence": LICENCE,
            "queries": queries,
            "pairs": {"top": args.pairs_top, "anchor": args.pairs_anchor,
                      "anchor_top": args.pairs_anchor_top},
            "counts": {"heights": len(height_qids), "populations": len(cities),
                       "countries": len(countries), "pairs": len(pairs)},
        },
        "heights": height_qids,
        "populations": cities,
        "countries": countries,
        "pairs": pairs,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scope, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                        "utf-8")
    print(json.dumps({"out": str(args.out), "populations": len(cities),
                      "countries": len(countries), "pairs": len(pairs),
                      "heights": len(height_qids), "licence": LICENCE},
                     sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except EnumerateError as error:
        print(f"enumerate-wikidata-entities: {error}", file=sys.stderr)
        sys.exit(2)
