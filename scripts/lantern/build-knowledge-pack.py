#!/usr/bin/env python3
"""Build a knowledge pack v2 directory from a curated JSONL or Markdown source.

The builder is deterministic: the same source and the same flags (including
--created-at) produce byte-identical chunk files and manifest. Chunk records
carry no timestamps for exactly that reason.

    build-knowledge-pack.py pack.jsonl --out pack-malaysia-v2 --pack-id malaysia \
        --version 2 --title "Malaysia - practical regional facts" \
        --licence CC-BY-4.0 --source-label curated:owner-notes-2026-09-20 \
        --created-at 2026-09-20T00:00:00Z --default-date 2026-09-20 \
        --parent ../pack-malaysia-v1 --signing-key ~/.sage/pack-signing.key

JSONL rows use {"id","text"} with optional {"title","tags","source","date","confidence"}.
Markdown sources use one "## Title" section per chunk, with optional leading
"Tags:", "Source:", "Date:", "Confidence:" lines and an "<!-- id: slug -->" override.

A pack is either a ``reference`` (curated facts, confidence 0.95 by default) or a
``brief`` (dated third-party material, confidence 0.80 by default and never at or
above the fact band), and each chunk records its own confidence in basis points.
The class is not decoration: it decides the memory type and confidence a node
installs a chunk with, and whether the installed text carries an "as of <date>,
per <source>" frame.

Embeddings are optional and recorded per chunk. Pass --embed-url to precompute
them (the field units then install without spending CPU on embedding); leave it
out and the installer embeds on the device. A chunk's content address never
covers its embedding, so re-embedding with a better model does not change
chunk identity.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('knowledge_pack', Path(__file__).with_name('knowledge_pack.py'))
knowledge_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(knowledge_pack)


def slug(value: str) -> str:
    cleaned = re.sub(r'[^a-z0-9]+', '-', value.strip().lower()).strip('-')
    return re.sub(r'-{2,}', '-', cleaned)


def confidence_bp(value, where: str) -> int:
    """A confidence as basis points (integer), the only canonical representation.

    The canonical-bytes rule forbids floating point in hashed structures, so the
    chunk record stores an integer out of 10000 and the CLI accepts ordinary
    decimals like 0.8.
    """
    require = knowledge_pack.require
    require(not isinstance(value, bool) and isinstance(value, (int, float)),
            f"{where}: confidence must be a number")
    number = float(value)
    require(0 < number <= 1, f"{where}: confidence must be in (0, 1]")
    points = round(number * 10000)
    require(abs(number * 10000 - points) < 1e-6,
            f"{where}: confidence is finer than one basis point (0.0001)")
    return int(points)


def parse_jsonl(path: Path, defaults: dict):
    rows = []
    allowed = {'id', 'text', 'title', 'tags', 'source', 'date', 'confidence'}
    for number, line in enumerate(path.read_text('utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise knowledge_pack.PackFormatError(f"{path}:{number}: invalid JSON: {error}") from error
        if not isinstance(row, dict):
            raise knowledge_pack.PackFormatError(f"{path}:{number}: row must be an object")
        unknown = set(row) - allowed
        if unknown:
            raise knowledge_pack.PackFormatError(f"{path}:{number}: unknown field(s) {sorted(unknown)}")
        if not isinstance(row.get('id'), str) or not isinstance(row.get('text'), str):
            raise knowledge_pack.PackFormatError(f"{path}:{number}: row needs string id and text")
        tags = row.get('tags') or []
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise knowledge_pack.PackFormatError(f"{path}:{number}: tags must be a list of strings")
        rows.append({
            'id': row['id'], 'text': row['text'], 'title': row.get('title', ''),
            'tags': list(tags) + list(defaults['tags']),
            'source': row.get('source') or defaults['source'],
            'date': row.get('date') or defaults['date'],
            'confidence': row.get('confidence'),
        })
    if not rows:
        raise knowledge_pack.PackFormatError(f"{path}: no rows found")
    return rows


METADATA = re.compile(r'^(Tags|Source|Date|Confidence|Id):\s*(.+?)\s*$')
ID_COMMENT = re.compile(r'<!--\s*id:\s*([A-Za-z0-9._-]+)\s*-->')


def parse_markdown(path: Path, defaults: dict):
    lines = path.read_text('utf-8').splitlines()
    title, sections, current = '', [], None
    for line in lines:
        if line.startswith('## '):
            current = {'title': line[3:].strip(), 'body': []}
            sections.append(current)
            continue
        if line.startswith('# ') and current is None:
            title = title or line[2:].strip()
            continue
        if current is not None:
            current['body'].append(line)
    if not sections:
        raise knowledge_pack.PackFormatError(f"{path}: no '## ' sections found")
    rows = []
    for index, section in enumerate(sections, start=1):
        body = list(section['body'])
        while body and not body[0].strip():
            body.pop(0)
        metadata = {}
        while body and (match := METADATA.match(body[0])):
            metadata[match.group(1).lower()] = match.group(2)
            body.pop(0)
        text = '\n'.join(body).strip()
        comment = ID_COMMENT.search(text)
        if comment:
            metadata['id'] = comment.group(1)
            text = ID_COMMENT.sub('', text).strip()
        if not text:
            raise knowledge_pack.PackFormatError(f"{path}: section {index} ({section['title']}) is empty")
        identifier = metadata.get('id') or slug(section['title'])
        if len(identifier) > 64:
            raise knowledge_pack.PackFormatError(
                f"{path}: section {index} derives a {len(identifier)}-character id; "
                "add an explicit '<!-- id: slug -->' of at most 64 characters")
        confidence = metadata.get('confidence')
        if confidence is not None:
            try:
                confidence = float(confidence)
            except ValueError as error:
                raise knowledge_pack.PackFormatError(
                    f"{path}: section {index} ({section['title']}) has a non-numeric "
                    f"Confidence: {metadata['confidence']!r}") from error
        rows.append({
            'id': identifier, 'text': text, 'title': section['title'],
            'tags': [tag.strip() for tag in metadata.get('tags', '').split(',') if tag.strip()]
                    + list(defaults['tags']),
            'source': metadata.get('source') or defaults['source'],
            'date': metadata.get('date') or defaults['date'],
            'confidence': confidence,
        })
    return title, rows


def embed_text(url: str, api: str, model: str, text: str, timeout: float):
    if api == 'ollama':
        endpoint, payload = f"{url.rstrip('/')}/api/embeddings", {'model': model, 'prompt': text}
    else:
        endpoint, payload = f"{url.rstrip('/')}/v1/embeddings", {'model': model, 'input': text}
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode('utf-8'),
                                    headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as error:
        raise knowledge_pack.PackFormatError(f"embedding request to {endpoint} failed: {error}") from error
    vector = body.get('embedding') if api == 'ollama' else (body.get('data') or [{}])[0].get('embedding')
    if not isinstance(vector, list) or not vector:
        raise knowledge_pack.PackFormatError(f"embedding response from {endpoint} carried no vector")
    return vector


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pack-id', required=True)
    parser.add_argument('--version', type=int, required=True)
    parser.add_argument('--title', default='')
    parser.add_argument('--class', dest='pack_class', choices=knowledge_pack.PACK_CLASSES,
                        default='reference',
                        help='reference (curated facts, 0.95) or brief (dated third-party '
                             'material, 0.80)')
    parser.add_argument('--default-confidence', type=float, default=None,
                        help='per-chunk confidence for source rows that do not carry one; '
                             'defaults to 0.95 for a reference and 0.80 for a brief')
    parser.add_argument('--licence', required=True,
                        help='licence identifier for the source material; a pack without '
                             'provenance is not distributable')
    parser.add_argument('--source-label', required=True,
                        help='where this content came from, e.g. curated:owner-notes-2026-09-20')
    parser.add_argument('--curator', default='')
    parser.add_argument('--url', default='')
    parser.add_argument('--note', default='')
    parser.add_argument('--created-at', required=True,
                        help='RFC3339 UTC instant; fixed for byte-identical rebuilds')
    parser.add_argument('--default-date', default='')
    parser.add_argument('--default-source', default='')
    parser.add_argument('--default-tag', action='append', default=[])
    parser.add_argument('--parent', type=Path, default=None,
                        help='earlier pack directory of the same pack; writes a chunk delta')
    parser.add_argument('--embed-url', default='')
    parser.add_argument('--embed-api', choices=('ollama', 'openai'), default='ollama')
    parser.add_argument('--embed-model', default='nomic-embed-text-v1.5')
    parser.add_argument('--embed-dimension', type=int, default=768)
    parser.add_argument('--embed-timeout', type=float, default=60.0)
    parser.add_argument('--signing-key', type=Path, default=None)
    parser.add_argument('--archive', nargs='?', const='', default=None, metavar='PATH',
                        help='also write a deterministic .tar.gz for delivery; default '
                             '<pack-id>-v<version>.tar.gz beside the pack directory')
    args = parser.parse_args()

    defaults = {'tags': args.default_tag, 'source': args.default_source or args.source_label,
                'date': args.default_date}
    pack_title = args.title
    if args.source.suffix.lower() == '.jsonl':
        rows = parse_jsonl(args.source, defaults)
    else:
        derived_title, rows = parse_markdown(args.source, defaults)
        pack_title = pack_title or derived_title
    if not pack_title:
        raise knowledge_pack.PackFormatError('a pack needs a title, from --title or the source heading')

    default_confidence = knowledge_pack.REFERENCE_CONFIDENCE_BP if args.pack_class == 'reference' \
        else knowledge_pack.BRIEF_CONFIDENCE_BP
    if args.default_confidence is not None:
        default_confidence = confidence_bp(args.default_confidence, '--default-confidence')

    # A parent is loaded without a signature requirement: it is a source for the
    # delta, and the new manifest pins its digest, so the relationship is
    # verifiable later even while no pack signing key exists.
    parent = knowledge_pack.load_pack(args.parent, allow_unsigned=True) if args.parent else None
    if parent is not None:
        if parent.pack_id != args.pack_id:
            raise knowledge_pack.PackFormatError(
                f"--parent is pack {parent.pack_id}, not {args.pack_id}")
        if parent.version >= args.version:
            raise knowledge_pack.PackFormatError(
                f"--parent version {parent.version} is not lower than {args.version}")
    existing = args.out / 'manifest.json'
    if existing.is_file():
        current = knowledge_pack.load_pack(args.out, allow_unsigned=True)
        if (current.pack_id, current.version) != (args.pack_id, args.version):
            raise knowledge_pack.PackFormatError(
                f"{args.out} already holds {current.pack_id} v{current.version}; "
                "build into a fresh directory")

    records = []
    for row in rows:
        vector = None
        if args.embed_url:
            vector = embed_text(args.embed_url, args.embed_api, args.embed_model, row['text'],
                                args.embed_timeout)
            if len(vector) != args.embed_dimension:
                raise knowledge_pack.PackFormatError(
                    f"embedding service returned {len(vector)} dimensions, expected "
                    f"{args.embed_dimension}")
        confidence = default_confidence if row['confidence'] is None \
            else confidence_bp(row['confidence'], f"chunk {row['id']}")
        if args.pack_class == 'brief' and confidence > knowledge_pack.BRIEF_MAX_CONFIDENCE_BP:
            raise knowledge_pack.PackFormatError(
                f"chunk {row['id']} carries confidence {confidence / 10000:g}; "
                "a brief is never a fact - keep it below 0.95")
        records.append(knowledge_pack.build_chunk(
            row['id'], row['text'], title=row['title'], tags=row['tags'], source=row['source'],
            date=row['date'], confidence_bp=confidence, embedding=vector,
            embedding_model=args.embed_model if vector else None))
    records.sort(key=lambda record: record['id'])

    provenance = {'source': args.source_label}
    for name in ('curator', 'url', 'note'):
        if getattr(args, name):
            provenance[name] = getattr(args, name)
    manifest = knowledge_pack.build_manifest(
        pack_id=args.pack_id, version=args.version, pack_class=args.pack_class, title=pack_title,
        licence=args.licence, provenance=provenance, created_at=args.created_at, chunks=records,
        embedding_model=args.embed_model, embedding_dimension=args.embed_dimension,
        parent=None if parent is None else {'version': parent.version,
                                            'manifest_sha256': knowledge_pack.manifest_digest(parent.manifest)})
    signature = None
    if args.signing_key is not None:
        signature = knowledge_pack.sign_manifest(
            knowledge_pack.manifest_bytes(manifest), knowledge_pack.load_signing_key(args.signing_key))
    delta = knowledge_pack.delta_document(parent.manifest, manifest) if parent is not None else None

    chunks = {record['sha256']: record for record in records}
    knowledge_pack.write_pack(args.out, manifest, chunks, signature=signature, delta=delta)
    pack = knowledge_pack.load_pack(args.out, allow_unsigned=signature is None)
    summary = pack.summary()
    summary.update({'out': str(args.out), 'source': str(args.source),
                    'default_confidence_bp': default_confidence,
                    'delta': None if delta is None else {'added': len(delta['added']),
                                                         'removed': len(delta['removed']),
                                                         'unchanged': delta['unchanged']}})
    if args.archive is not None:
        target = Path(args.archive) if args.archive else \
            args.out.parent / f"{args.pack_id}-v{args.version}{knowledge_pack.ARCHIVE_SUFFIX}"
        summary['archive'] = knowledge_pack.write_archive(args.out, target)
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except knowledge_pack.PackFormatError as error:
        print(f"build-knowledge-pack: {error}", file=sys.stderr)
        sys.exit(2)
