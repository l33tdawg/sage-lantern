#!/usr/bin/env python3
"""Verify a knowledge pack v2 and install it into a personal SAGE node.

    install-knowledge-pack.py <pack-dir> --check              # coverage, no writes
    install-knowledge-pack.py <pack-dir>                      # install missing chunks
    install-knowledge-pack.py <pack-dir> --retire --reason ...
    install-knowledge-pack.py <pack-dir> --migrate-to public-knowledge

Every run verifies the pack first: manifest canonical form, signature (unless
--allow-unsigned), each chunk's content address, and that the chunk store holds
exactly what the manifest lists. A carrier is never trusted (SYS-008).

Idempotency is by content address, not by similarity: each memory carries
``chunk:<sha256>`` and ``pack:<id>-v<version>`` tags, and the installer asks the
node for memories tagged with the chunk address before proposing anything. That
is one local request per chunk; re-running a pack of any size converges.

Batch memories cannot carry an idempotency_key (the SDK rejects it for non-task
types), so the derived-key replay the node performs on identical content is a
second line of defence, not the mechanism.

--retire and --migrate-to are the "remove or move a whole pack in one
operation" path the pack format promised. Both report what the node actually
did: post-app-v21 a forget may park as a challenge (k>0) instead of deprecating
immediately, and this tool reports that rather than claiming removal.

The domain defaults to the node's own home domain. That default is resolved, not
assumed: a write may say ``domain_tag=None`` ("home"), but on a read ``None``
means "no filter at all", so every read path first resolves the concrete domain
and refuses to guess when the agent owns more than one. Registering a *public*
pack domain is a node-side policy change (OPEN-PUB-001 / task bbd52246); pass
--register-domain only once that policy exists.

Pack identity is the two tags ``pack:<id>-v<version>`` and ``chunk:<sha256>``.
For an ordinary (non-scoped) domain the node keeps tags as node-local serving
metadata rather than consensus state, so the index is per node and is rebuilt by
installing the pack, not by syncing the chain.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile


SPEC = importlib.util.spec_from_file_location('knowledge_pack', Path(__file__).with_name('knowledge_pack.py'))
knowledge_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(knowledge_pack)

DEFAULT_IDENTITY = Path('/var/lib/sage-lantern/identity/lantern-agent.key')
DEFAULT_BASE_URL = 'http://127.0.0.1:8080'
DEFAULT_LEDGER_DIR = Path('/var/lib/sage-lantern/knowledge-packs')
APPLIANCE_SITE_PACKAGES = Path('/opt/sage-lantern/venv/lib/python3.11/site-packages')
PAGE = 50


class PackInstallError(RuntimeError):
    pass


def load_sdk():
    try:
        import sage_sdk
    except ModuleNotFoundError:
        if not APPLIANCE_SITE_PACKAGES.is_dir():
            raise PackInstallError(
                'sage_sdk is not importable and the appliance venv is not present; '
                'run this on a unit or point PYTHONPATH at the SDK') from None
        sys.path.insert(0, str(APPLIANCE_SITE_PACKAGES))
        import sage_sdk
    return sage_sdk


def build_client(args):
    sdk = load_sdk()
    identity = sdk.AgentIdentity.from_file(Path(args.identity))
    return sdk.SageClient(base_url=args.base_url, identity=identity)


def chunk_tags(pack, digest: str) -> list[str]:
    return [pack.tag, pack.chunk_tag(digest), f"chunk-id:{pack.chunks[digest]['id']}",
            'sage-knowledge-pack']


def list_tagged(client, tag: str, domain: str | None, limit: int | None = None) -> list:
    """Every memory carrying a tag, paged. Tags are the pack's index, not its data."""
    collected, offset = [], 0
    while True:
        page = client.list_memories(limit=PAGE, offset=offset, domain=domain, tag=tag)
        records = list(page.memories)
        collected.extend(records)
        offset += len(records)
        if not records or not page.has_more or (limit is not None and len(collected) >= limit):
            break
    return collected[:limit] if limit is not None else collected


def resolve_read_domain(client, requested: str | None, *, needed_by: str) -> str:
    """Name the domain a read may use.

    ``domain_tag=None`` means "the caller's home domain" on a write, but it means
    "no filter at all" on a read. Asking the node for a chunk tag with no filter
    would find copies in other domains and, on the retire path, would forget
    them too - including the copies a migration had just written. So every read
    path resolves the concrete domain first, and refuses to guess when the
    caller's home domain is not the only domain they own.
    """
    if requested:
        return requested
    domains, cursor = [], None
    while True:
        page = client.owned_domains(cursor=cursor)
        domains.extend(page.domains)
        if not page.has_more or not page.next_cursor:
            break
        cursor = page.next_cursor
    if len(domains) == 1:
        return domains[0]
    raise PackInstallError(
        f"{needed_by} must know which domain to read and this agent owns {len(domains)} "
        f"domains ({', '.join(sorted(domains)) or 'none'}); pass --domain explicitly")


def _vector_for(client, pack, digest: str):
    recorded = pack.vector(digest)
    if recorded is not None:
        return recorded
    return client.embed(pack.chunks[digest]['text'])


def _replay_of(response) -> bool:
    return bool(getattr(response, 'idempotent_replay', False))


def _check_outcome(record: dict, digest: str) -> None:
    status = getattr(record, 'status', None)
    text = status.value if hasattr(status, 'value') else str(status or '')
    if text == 'indeterminate':
        raise PackInstallError(
            f"chunk {digest[:12]} reached the network but the node could not observe its fate; "
            'reconcile that memory before re-running (re-running would sign a new transaction)')


def install(client, pack, *, domain: str | None, dry_run: bool = False) -> dict:
    domain = resolve_read_domain(client, domain, needed_by='install')
    entries = []
    installed = skipped = replays = failures = 0
    for entry in pack.manifest['chunks']:
        digest = entry['sha256']
        existing = list_tagged(client, pack.chunk_tag(digest), domain, limit=1)
        if existing:
            skipped += 1
            entries.append({'id': entry['id'], 'sha256': digest, 'present': True,
                            'memory_id': existing[0].memory_id, 'written': False})
            continue
        if dry_run:
            entries.append({'id': entry['id'], 'sha256': digest, 'present': False, 'written': False})
            continue
        try:
            response = client.propose(content=pack.chunks[digest]['text'], memory_type='fact',
                                      domain_tag=domain, confidence=0.95,
                                      embedding=_vector_for(client, pack, digest),
                                      tags=chunk_tags(pack, digest))
        except Exception as error:  # noqa: BLE001 - reported per chunk, a re-run resumes
            failures += 1
            entries.append({'id': entry['id'], 'sha256': digest, 'present': False,
                            'written': False, 'error': f'{type(error).__name__}: {error}'})
            continue
        _check_outcome(response, digest)
        replay = _replay_of(response)
        replays += int(replay)
        installed += int(not replay)
        entries.append({'id': entry['id'], 'sha256': digest, 'present': True,
                        'memory_id': getattr(response, 'memory_id', None),
                        'written': not replay, 'replay': replay})
    return {'pack': pack.pack_id, 'version': pack.version, 'domain': domain,
            'chunks': len(pack.manifest['chunks']), 'installed': installed, 'skipped': skipped,
            'replays': replays, 'failures': failures, 'dry_run': dry_run, 'chunks_detail': entries}


def coverage(client, pack, *, domain: str | None) -> dict:
    domain = resolve_read_domain(client, domain, needed_by='--check')
    present = []
    for entry in pack.manifest['chunks']:
        found = list_tagged(client, pack.chunk_tag(entry['sha256']), domain, limit=1)
        present.append({'id': entry['id'], 'sha256': entry['sha256'],
                        'present': bool(found),
                        'memory_id': found[0].memory_id if found else None})
    missing = [row for row in present if not row['present']]
    return {'pack': pack.pack_id, 'version': pack.version, 'domain': domain,
            'chunks': len(present), 'present': len(present) - len(missing), 'missing': len(missing),
            'complete': not missing, 'chunks_detail': present}


def retire(client, tag: str, *, domain: str | None, reason: str) -> dict:
    domain = resolve_read_domain(client, domain, needed_by='--retire')
    records = list_tagged(client, tag, domain)
    outcomes = {'deprecated': 0, 'parked': 0, 'failed': 0}
    detail = []
    for record in records:
        try:
            response = client.forget(record.memory_id, reason=reason)
        except Exception as error:  # noqa: BLE001 - reported per memory, never swallowed
            outcomes['failed'] += 1
            detail.append({'memory_id': record.memory_id, 'outcome': 'failed',
                           'detail': f'{type(error).__name__}: {error}'})
            continue
        outcome = str(response.get('status') or response.get('result') or 'unknown')
        if outcome in outcomes:
            outcomes[outcome] += 1
        else:
            outcomes.setdefault(outcome, 0)
            outcomes[outcome] += 1
        detail.append({'memory_id': record.memory_id, 'outcome': outcome})
    return {'tag': tag, 'domain': domain, 'memories': len(records),
            'outcomes': outcomes, 'retired': outcomes.get('deprecated', 0),
            'parked': outcomes.get('parked', 0), 'detail': detail}


def migrate(client, pack, *, source_domain: str | None, dest_domain: str | None,
            dry_run: bool = False) -> dict:
    source = resolve_read_domain(client, source_domain, needed_by='--migrate-to (source)')
    destination = resolve_read_domain(client, dest_domain, needed_by='--migrate-to (destination)')
    installed = install(client, pack, domain=destination, dry_run=dry_run)
    verified = coverage(client, pack, domain=destination)
    summary = {'pack': pack.pack_id, 'version': pack.version,
               'from': source, 'to': destination,
               'phase': 'copy', 'install': {key: installed[key] for key in
                                            ('installed', 'skipped', 'replays', 'failures', 'chunks')},
               'dest_complete': verified['complete'], 'dest_present': verified['present'],
               'dry_run': dry_run, 'retire': None}
    if not verified['complete']:
        summary['phase'] = 'copy-incomplete'
        return summary
    if dry_run:
        summary['phase'] = 'copy-verified-no-retire'
        return summary
    source_records = list_tagged(client, pack.tag, source)
    if source == destination:
        summary['phase'] = 'copy-complete'
        summary['retire'] = {'memories': len(source_records), 'skipped': 'source and destination '
                            'are the same domain; nothing to move'}
        return summary
    summary['phase'] = 'copy-complete-retire-pending'
    summary['retire'] = retire(client, pack.tag, domain=source,
                               reason=f"knowledge pack {pack.tag} moved to {destination}")
    return summary


def write_ledger(path: Path, summary: dict) -> None:
    document = {'schema': 'sage.knowledge-pack.ledger.v1', 'pack': summary['pack'],
                'version': summary['version'], 'domain': summary['domain'],
                'updated_at': dt.datetime.now(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'chunks': {row['sha256']: {'id': row['id'], 'memory_id': row.get('memory_id'),
                                           'written': row.get('written', False),
                                           'replay': row.get('replay', False)}
                           for row in summary['chunks_detail']}}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(document, sort_keys=True, indent=2) + '\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('pack', type=Path)
    parser.add_argument('--identity', type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL)
    parser.add_argument('--domain', default='',
                        help='target domain; empty means the node\'s own home domain')
    parser.add_argument('--public-key-hex', default=None,
                        help='require the manifest to be signed by exactly this ed25519 key')
    parser.add_argument('--trusted-key', type=Path, default=None,
                        help='file holding the trusted ed25519 public key as lowercase hex')
    parser.add_argument('--allow-unsigned', action='store_true',
                        help='accept a pack with no signature (interim: no pack signing key exists)')
    parser.add_argument('--register-domain', action='store_true',
                        help='register the target domain first; fails loudly if policy refuses it')
    parser.add_argument('--ledger', type=Path, default=None)
    parser.add_argument('--no-ledger', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--check', action='store_true', help='report coverage and write nothing')
    parser.add_argument('--retire', action='store_true',
                        help='forget every memory carrying the pack tag in --domain')
    parser.add_argument('--migrate-to', default='',
                        help='install into this domain, verify, then retire the source copies')
    parser.add_argument('--reason', default='')
    args = parser.parse_args()

    selected = [name for name in ('check', 'retire') if getattr(args, name)]
    if bool(args.migrate_to) and selected:
        raise PackInstallError('--migrate-to does not combine with --check or --retire')
    if selected and args.dry_run:
        raise PackInstallError('--dry-run applies to install and --migrate-to only')

    trusted = args.public_key_hex
    if args.trusted_key is not None:
        candidate = args.trusted_key.read_text('ascii').strip()
        if trusted is not None and trusted != candidate:
            raise PackInstallError('--trusted-key and --public-key-hex disagree')
        trusted = candidate
    pack = knowledge_pack.load_pack(args.pack, trusted_public_hex=trusted,
                                    allow_unsigned=args.allow_unsigned)
    domain = args.domain or None
    client = build_client(args)
    if args.register_domain:
        client.register_domain(domain or '', description='Curated knowledge packs')

    reason = args.reason or f"knowledge pack {pack.tag} retired by operator"
    if args.retire:
        summary = retire(client, pack.tag, domain=domain, reason=reason)
        summary['pack'] = pack.pack_id
        summary['version'] = pack.version
    elif args.check:
        summary = coverage(client, pack, domain=domain)
        summary['pack_dir'] = str(args.pack)
    elif args.migrate_to:
        summary = migrate(client, pack, source_domain=domain, dest_domain=args.migrate_to or None,
                          dry_run=args.dry_run)
    else:
        summary = install(client, pack, domain=domain, dry_run=args.dry_run)
        summary['pack_dir'] = str(args.pack)
        ledger = None if args.no_ledger else (args.ledger or DEFAULT_LEDGER_DIR / f'{pack.pack_id}.json')
        if ledger is not None and not args.dry_run:
            write_ledger(ledger, summary)
            summary['ledger'] = str(ledger)
    print(json.dumps(summary, sort_keys=True))
    if summary.get('failures') or summary.get('install', {}).get('failures'):
        return 1
    if args.check and not summary.get('complete'):
        return 1
    if summary.get('outcomes', {}).get('failed'):
        return 1
    if summary.get('phase') == 'copy-incomplete':
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (knowledge_pack.PackFormatError, PackInstallError) as error:
        print(f"install-knowledge-pack: {error}", file=sys.stderr)
        sys.exit(2)
