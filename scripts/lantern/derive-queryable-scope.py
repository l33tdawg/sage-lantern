#!/usr/bin/env python3
"""Cut a queryable-size scope out of a full reference scope.

    python3 scripts/lantern/derive-queryable-scope.py \
        --scope work/reference-facts-20260920/world-facts-v2-scope.json \
        --out work/reference-facts-20260920/world-facts-lite-scope.json \
        --populations 1600 --pairs-top 60 --pairs-anchor Q1865 \
        --pairs-anchor-top 400

Why a cut exists at all: the node under app-v23 refuses a hybrid query whose
candidate set exceeds 8,192, so the 27,822-fact world pack cannot be recalled as
one pack on a unit. The recall path is therefore measured with a cut that stays
under that ceiling - and the cut has to be reproducible, because "5,693 chunks,
roughly the popular ones" is not something a later run can rebuild or review.

The cut is deterministic and keeps rank order: ``populations`` are the first N
of the source ranking, ``countries`` and ``heights`` are kept whole (both are
small), and ``pairs`` are the merge of the pairwise list among the top-N cities
and the anchor pairs, deduplicated exactly as the enumerator deduplicates them,
so a cut can never introduce a pair the source scope does not sanction.

Seeds are inherited from the source scope's provenance when ``--seeds`` is not
given. A measured-gap entity is meant to survive every cut, and re-stating one
by hand is how a cut drops it by accident.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LICENCE = "CC0-1.0"
WHY = ("the node under app-v23 refuses a hybrid query whose candidate set "
       "exceeds 8,192, so a scaled pack is measured through a cut that stays "
       "queryable rather than through the full pack")
SEED_WHY = ("named by the measured gaps rather than the ranking: a reference "
            "pack is built to fix specific facts, and a top-N cut must not "
            "drop them")
QID = re.compile(r"Q[1-9][0-9]*\Z")


class CutError(RuntimeError):
    pass


def load_enumerator():
    """The enumerator's pair helpers, so a cut merges pairs the same way."""
    path = Path(__file__).with_name("enumerate-wikidata-entities.py")
    spec = importlib.util.spec_from_file_location("enumerate_wikidata_entities", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENUM = load_enumerator()


def inherit_seeds(scope: dict) -> tuple:
    """The source scope's seeds, so a cut cannot silently drop one."""
    provenance = scope.get("_provenance") or {}
    seeds = (provenance.get("seeds") or {}).get("entities") or []
    return tuple(str(seed) for seed in seeds)


def qid_list(values, where: str) -> list:
    checked = []
    for value in values or []:
        if not isinstance(value, str) or not QID.match(value.strip()):
            raise CutError(f"{where} holds {value!r}, expected a QID like Q1490")
        checked.append(value.strip())
    return checked


def cut(scope: dict, *, populations: int, pairs_top: int, pairs_anchor: str = "",
        pairs_anchor_top: int = 0, seeds: tuple = ()) -> dict:
    """The source scope, cut to the requested size, in rank order."""
    ranked = qid_list(scope.get("populations"), "populations")
    countries = qid_list(scope.get("countries"), "countries")
    heights = qid_list(scope.get("heights"), "heights")
    for entry in scope.get("pairs") or []:
        if (not isinstance(entry, list) or len(entry) != 2
                or not all(isinstance(item, str) and QID.match(item.strip())
                           for item in entry)):
            raise CutError(f'pairs entries look like ["Q1490", "Q35765"], got {entry!r}')
    pairs = []
    kept_populations = ranked[:populations] if populations else list(ranked)
    for seed in seeds:
        if seed not in kept_populations:
            kept_populations.append(seed)
    if pairs_top:
        pairs = ENUM.merge_pairs(pairs, ENUM.city_pairs(ranked, pairs_top, seeds))
    if pairs_anchor:
        pairs = ENUM.merge_pairs(
            pairs, ENUM.anchor_pairs(ranked, pairs_anchor, pairs_anchor_top, seeds))
    return {"populations": kept_populations, "countries": countries,
            "heights": heights, "pairs": pairs}


def scope_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--scope", type=Path, required=True,
                        help="the full scope file this cut is derived from")
    parser.add_argument("--out", type=Path, required=True, help="cut scope JSON to write")
    parser.add_argument("--populations", type=int, default=0,
                        help="keep the first N of the ranked populations list "
                             "(0 keeps every one)")
    parser.add_argument("--pairs-top", type=int, default=0,
                        help="pairwise distances among the N most populous cities")
    parser.add_argument("--pairs-anchor", default="",
                        help="QID to pair against the most populous cities")
    parser.add_argument("--pairs-anchor-top", type=int, default=0,
                        help="how many cities the anchor pairs with")
    parser.add_argument("--seeds", default="",
                        help="comma-separated QIDs forced into every pair selection "
                             "(default: the seeds recorded in the source scope)")
    args = parser.parse_args()

    try:
        source = json.loads(args.scope.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CutError(f"could not read {args.scope}: {error}") from error
    if not isinstance(source, dict):
        raise CutError(f"{args.scope} must hold a JSON object")

    seeds = tuple(part.strip() for part in args.seeds.split(",") if part.strip()) \
        or inherit_seeds(source)
    result = cut(source, populations=args.populations,
                 pairs_top=args.pairs_top, pairs_anchor=args.pairs_anchor,
                 pairs_anchor_top=args.pairs_anchor_top, seeds=seeds)
    payload = {
        "_provenance": {
            "generator": "scripts/lantern/derive-queryable-scope.py",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "licence": LICENCE,
            "derived_from": str(args.scope),
            "derived_from_sha256": scope_digest(args.scope),
            "cut": {"populations": args.populations, "pairs_top": args.pairs_top,
                    "pairs_anchor": args.pairs_anchor,
                    "pairs_anchor_top": args.pairs_anchor_top},
            "seeds": {"entities": list(seeds), "why": SEED_WHY},
            "why": WHY,
            "counts": {key: len(value) for key, value in result.items()},
        },
        **result,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        "utf-8")
    print(json.dumps({"out": str(args.out), "counts": payload["_provenance"]["counts"]}),
          file=sys.stderr)


if __name__ == "__main__":
    main()
