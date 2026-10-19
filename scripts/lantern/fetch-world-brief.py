#!/usr/bin/env python3
"""Fetch a dated world brief from Wikipedia's Current Events portal.

    python3 scripts/lantern/fetch-world-brief.py --from 2026-06-20 --to 2026-09-20 \
        --out work/world-brief-20260920/brief-2026-09-20.jsonl

The portal is one page per day (``Portal:Current events/2026_September_20``), human
curated, already dated and cited. Its text is CC BY-SA 4.0, which is open but
share-alike; the pack that this feeds records that licence and the exact page URL
per chunk so attribution travels with the content.

Only the page text is fetched - titles and summaries, no article bodies, no
images. One API call covers up to 50 days via ``titles=`` batching, and the
request carries a descriptive User-Agent because Wikimedia requires one.

Output is pack-source JSONL for ``build-knowledge-pack.py``: one row per event,
with the day as ``date``, the section as ``title``/tag, and the source page URL.
Every row also carries the portal's licence (``CC-BY-SA-4.0``) so the pack audit
does not have to trust a build-time flag for a share-alike source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = ("SageLanternWorldBrief/1.0 (SAGE Lantern offline knowledge packs; "
              "https://sage.ngo)")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
TOPIC = re.compile(r"^'''(.+?)'''\s*$")
BULLET = re.compile(r"^(\*+)\s*(\S.*?)\s*$")
REMOVE_LINK = re.compile(r"\[\[[^]]*\]\]")
TAGS = re.compile(r"\[\[(?:[Ww]ikt|[Ww]ikt\w+|[Ss]imple|[Ff]ile|[Ii]mage|[Cc]ategory):[^]]*\]\]")
COMMENT = re.compile(r"<!--.*?-->", re.S)
REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S)
TAG = re.compile(r"</?(?:small|span|div|p|br|nowiki|sub|sup|b|i|u|code|abbr)[^>]*>", re.I)
LINK = re.compile(r"\[\[([^]|]+)\|([^]]+)\]\]|\[\[([^]]+)\]\]")
QUOTES = re.compile(r"'{2,5}")
EXTERNAL = re.compile(r"\[(https?://\S+)\s+([^]]+)\]")
SPACE = re.compile(r"[ \t\u00a0]+")
# Inline templates that carry the sentence rather than decorate it: {{ill|Name|ja|...}}
# renders as the name, {{lang|ja|text}} and {{nowrap|text}} render as their payload.
# Dropping those would delete words from the middle of a fact, so they are unwrapped
# before the generic template stripper runs.
KEEP_FIRST = re.compile(r"\{\{\s*(?:ill|interlanguage link|illm)\s*\|\s*([^|}]+)[^{}]*\}\}", re.I)
KEEP_LAST = re.compile(r"\{\{\s*(?:nowrap|nobr|lang|lang-\w+|mention|abbr|small)\s*\|([^{}]*)\}\}", re.I)
MIN_TEXT = 40
MAX_TEXT = 1200
BATCH = 50
LICENCE = "CC-BY-SA-4.0"


class BriefError(RuntimeError):
    pass


def strip_templates(text: str) -> str:
    """Remove {{templates}} including nested ones, innermost first."""
    while True:
        start = text.find("{{")
        if start < 0:
            return text
        depth, index = 0, start
        while index < len(text) - 1:
            if text[index:index + 2] == "{{":
                depth += 1
                index += 2
                continue
            if text[index:index + 2] == "}}":
                depth -= 1
                index += 2
                if depth == 0:
                    break
                continue
            index += 1
        else:
            return text[:start]
        text = text[:start] + text[index:]


def clean(wikitext: str) -> str:
    text = COMMENT.sub("", wikitext)
    text = REF.sub("", text)
    text = KEEP_FIRST.sub(lambda match: match.group(1).strip(), text)
    text = KEEP_LAST.sub(lambda match: ([part.strip() for part in match.group(1).split("|")]
                                        [-1]), text)
    text = strip_templates(text)
    text = TAG.sub("", text)
    text = TAGS.sub("", text)
    text = EXTERNAL.sub(r"\2", text)
    text = LINK.sub(lambda match: match.group(2) or match.group(3), text)
    text = QUOTES.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&ndash;", "-")
    return SPACE.sub(" ", text).strip(" -" + "\u2013")


def is_heading_only(raw: str) -> bool:
    """A bullet that carries no sentence - ``*[[2026 Iran war]]`` - is a heading.

    The portal nests an article chain above each event (``*``, ``**``, ``***``) and
    only the leaf carries prose. Peeling the links, citations and templates off a
    bullet and finding nothing left is what separates the two.
    """
    text = REF.sub("", raw)
    text = EXTERNAL.sub("", text)
    text = REMOVE_LINK.sub("", text)
    text = strip_templates(text)
    return len(text.strip(" \t-\u2013\u2014")) < 15


def looks_like_an_event(text: str) -> bool:
    """Sentences here end with a period or a source attribution in parentheses.

    A heading that carries no link ("Aftermath of the South Sudanese Civil War") is
    otherwise indistinguishable from a short event. Measured on 93 days: this rule
    drops two rows, both that same heading, and no real event.
    """
    stripped = text.rstrip()
    return stripped.endswith(")") or stripped.endswith(".")


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", cleaned)[:40]


def page_title(day: dt.date) -> str:
    return f"Portal:Current events/{day.year}_{MONTHS[day.month - 1]}_{day.day}"


def page_url(day: dt.date) -> str:
    return "https://en.wikipedia.org/wiki/" + page_title(day).replace(" ", "_")


def canonical(title: str) -> str:
    """The API returns underscores as spaces (a 'normalized' title), so match on this."""
    return title.replace("_", " ").strip()


def parse_day(day: dt.date, wikitext: str):
    """One day of the portal into dated rows. Pure: same input, same output."""
    rows, topic, counter = [], "World", 0
    for line in wikitext.splitlines():
        heading = TOPIC.match(line)
        if heading:
            topic = clean(heading.group(1)) or topic
            continue
        match = BULLET.match(line)
        if not match:
            continue
        raw = match.group(2)
        if is_heading_only(raw):
            continue
        text = clean(raw)
        if len(text) < MIN_TEXT or not looks_like_an_event(text):
            continue
        counter += 1
        rows.append({
            "id": f"wb-{day:%Y%m%d}-{counter:02d}",
            "text": text[:MAX_TEXT],
            "title": topic,
            "tags": ["world-brief", slug(topic)] if slug(topic) else ["world-brief"],
            "source": page_url(day),
            "date": f"{day:%Y-%m-%d}",
            "licence": LICENCE,
        })
    return rows


def fetch(titles, api: str, delay: float) -> dict:
    pages = {}
    for start in range(0, len(titles), BATCH):
        batch = titles[start:start + BATCH]
        query = urllib.parse.urlencode({
            "action": "query", "prop": "revisions", "rvslots": "main", "rvprop": "content",
            "format": "json", "formatversion": "2", "redirects": "1", "titles": "|".join(batch)})
        request = urllib.request.Request(f"{api}?{query}", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as error:
            raise BriefError(f"fetch of {len(batch)} page(s) failed: {error}") from error
        for page in payload.get("query", {}).get("pages", []):
            title = canonical(page.get("title", ""))
            if page.get("missing"):
                pages[title] = None
                continue
            revisions = page.get("revisions") or []
            content = revisions[0].get("slots", {}).get("main", {}).get("content") if revisions else None
            pages[title] = content
        if delay:
            time.sleep(delay)
    return pages


def range_days(first: dt.date, last: dt.date):
    if last < first:
        raise BriefError("--to is before --from")
    if (last - first).days > 366:
        raise BriefError("a window longer than a year is not a brief; split it into packs")
    day = first
    days = []
    while day <= last:
        days.append(day)
        day += dt.timedelta(days=1)
    return days


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise BriefError(f"{value} is not an ISO date (YYYY-MM-DD)") from error


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--from", dest="first", required=True, type=parse_date)
    parser.add_argument("--to", dest="last", required=True, type=parse_date)
    parser.add_argument("--out", type=Path, required=True, help="pack-source JSONL to write")
    parser.add_argument("--api", default=API)
    parser.add_argument("--request-delay", type=float, default=1.0,
                        help="seconds between API calls; Wikimedia asks for a polite client")
    parser.add_argument("--keep-empty", action="store_true",
                        help="also emit days that carry no events")
    parser.add_argument("--dump-raw", type=Path, default=None,
                        help="write each fetched page's raw wikitext here (source debugging)")
    args = parser.parse_args()

    days = range_days(args.first, args.last)
    titles = [page_title(day) for day in days]
    pages = fetch(titles, args.api, args.request_delay)
    if args.dump_raw is not None:
        args.dump_raw.mkdir(parents=True, exist_ok=True)
        for title, content in pages.items():
            if content is None:
                continue
            name = title.replace("Portal:Current events/", "").replace("/", "_") + ".wikitext"
            (args.dump_raw / name).write_text(content, "utf-8")
    rows, empty = [], []
    for day in days:
        content = pages.get(canonical(page_title(day)))
        if content is None:
            empty.append(f"{day:%Y-%m-%d}")
            continue
        day_rows = parse_day(day, content)
        if not day_rows:
            empty.append(f"{day:%Y-%m-%d}")
        rows.extend(day_rows)
    if not rows:
        raise BriefError("no events parsed; check the window and the API response")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    print(json.dumps({"out": str(args.out), "from": f"{args.first:%Y-%m-%d}",
                      "to": f"{args.last:%Y-%m-%d}", "days": len(days),
                      "events": len(rows), "days_without_events": empty,
                      "licence": LICENCE,
                      "source": "wikipedia:portal-current-events"}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BriefError as error:
        print(f"fetch-world-brief: {error}", file=sys.stderr)
        sys.exit(2)
