#!/opt/sage-lantern/venv/bin/python
"""Ingest one knowledge-pack JSONL into a PUBLIC domain of the personal SAGE node.

    ingest_pack.py <pack.jsonl> [--domain public-knowledge] [--tag pack:malaysia-v1]

Each line becomes one fact memory with the node's own embedding (nomic-embed-text
on the units). The domain is registered once and is the public side of the
store: private/local knowledge stays in local-field-knowledge, curated packs
live in a domain that can be shared, mirrored and versioned. Re-running skips
chunks whose tag is already present in the domain.

SUPERSEDED - use scripts/lantern/build-knowledge-pack.py with
scripts/lantern/install-knowledge-pack.py for anything new. This script stays
only to describe the copies already on the test units. Two things it got wrong:
the skip check reads `record.tags`, which the SDK's MemoryRecord does not
expose, so it never matched (a re-run re-proposes content the node answers as a
replay rather than a duplicate); and `--domain public-knowledge` cannot be
registered by the vendored agent today (403), so packs land in the home domain.
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time

sys.path.insert(0, "/opt/sage-lantern/venv/lib/python3.11/site-packages")
from sage_sdk import AgentIdentity, SageClient  # noqa: E402

IDENTITY = "/var/lib/sage-lantern/identity/lantern-agent.key"
BASE_URL = "http://127.0.0.1:8080"


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("pack", type=pathlib.Path)
    parser.add_argument("--domain", default="public-knowledge")
    parser.add_argument("--tag", default="knowledge-pack")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.pack.read_text().splitlines() if line.strip()]
    client = SageClient(base_url=BASE_URL, identity=AgentIdentity.from_file(IDENTITY))
    try:
        client.register_domain(args.domain, description="Curated public knowledge packs, versioned and shareable")
    except Exception as error:
        if "exist" not in str(error).lower():
            print(f"domain registration: {type(error).__name__}: {error}", file=sys.stderr)
    written = skipped = 0
    started = time.monotonic()
    for row in rows:
        embedding = client.embed(row["text"])
        marker = f"id:{row['id']}"
        existing = client.query(embedding=embedding, domain_tag=args.domain, top_k=3)
        if any(marker in (getattr(record, "tags", None) or []) for record in existing.results):
            skipped += 1
            continue
        client.propose(content=row["text"], memory_type="fact", domain_tag=args.domain,
                       confidence=0.95, embedding=embedding,
                       tags=[args.tag, marker, "public-knowledge-pack"])
        written += 1
    seconds = time.monotonic() - started
    print(json.dumps({"pack": str(args.pack), "rows": len(rows), "written": written,
                      "skipped": skipped, "seconds": round(seconds, 1),
                      "domain": args.domain, "tag": args.tag}, sort_keys=True))


if __name__ == "__main__":
    main()
