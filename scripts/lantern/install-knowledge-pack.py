#!/usr/bin/env python3
"""Verify a knowledge pack v2 and install it into a personal SAGE node.

    install-knowledge-pack.py <pack-dir> --check              # coverage, no writes
    install-knowledge-pack.py <pack-dir>                      # install missing chunks
    install-knowledge-pack.py <pack-dir> --roll-from <old>   # new window in, dropped chunks out
    install-knowledge-pack.py <pack-dir> --retire --reason ...
    install-knowledge-pack.py <pack-dir> --migrate-to public-knowledge

Every run verifies the pack first: manifest canonical form, signature (unless
--allow-unsigned), each chunk's content address, and that the chunk store holds
exactly what the manifest lists. A carrier is never trusted (SYS-008).

The pack may be a directory or the ``.tar.gz`` a release ships, which is how a
pack travels from GitHub to a unit that has no route to the internet: fetch the
asset, move it to the unit however it moves, then ``--verify`` before ``--install``.

Signature trust comes from ``pack-trust-root.json`` next to this script - the
Foundation's pack keys, edited by hand under review. A pack signed by a key that
is not in the root is refused; so is a revoked key, even though its signature
verifies cryptographically. ``--verify`` runs that whole check with no node
contact at all.

Idempotency is by content address, not by similarity: each memory carries
``chunk:<sha256>`` and ``pack:<id>-v<version>`` tags, and the installer asks the
node for memories tagged with the chunk address before proposing anything. That
is one local request per chunk; re-running a pack of any size converges.

The pack's class decides how a chunk enters the node. A ``reference`` chunk is a
fact at its recorded confidence (0.95 by default) with the signed text verbatim.
A ``brief`` chunk is an observation at its recorded confidence (0.80 by default,
never 0.95 because news is third-party and can be wrong) and its installed text
carries ``As of <date>, per <source>:`` because recall returns plain content and
an answer about the news has to say when it was true and whose account it is.

``--roll-from`` is the rolling-window apply step: it installs the successor and
then retires the chunks the delta dropped, in that order, so a failed install
never empties the old window. The old manifest is authenticated by the parent
digest inside the signed successor, the removed set is recomputed from the two
manifests, and the shipped delta document may only agree with that - it is not
signed and never directs a forget on its own.

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
DEFAULT_TRUST_ROOT = Path(__file__).with_name('pack-trust-root.json')
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


def chunk_memory_type(pack) -> str:
    """A reference pack is curated fact; a brief is dated third-party material."""
    return 'observation' if pack.pack_class == 'brief' else 'fact'


def chunk_content(pack, digest: str) -> str:
    """The text a node stores for one chunk.

    A reference installs the signed text verbatim. A brief gets its day and
    source in front of the text, because recall hands the answering model a
    plain string - the record's date and source fields never reach it, so the
    framing has to be inside the content for an answer to say when the claim was
    made and whose account it is.
    """
    record = pack.chunks[digest]
    if pack.pack_class != 'brief':
        return record['text']
    return f"As of {record['date']}, per {record['source']}: {record['text']}"


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
    # Embeddings cover the signed text, not a brief's install-time frame: the
    # frame is boilerplate, and the vector should describe the event a question
    # is actually about.
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
    memory_type = chunk_memory_type(pack)
    entries = []
    installed = skipped = replays = failures = 0
    for entry in pack.manifest['chunks']:
        digest = entry['sha256']
        confidence_bp = pack.chunks[digest]['confidence_bp']
        existing = list_tagged(client, pack.chunk_tag(digest), domain, limit=1)
        if existing:
            skipped += 1
            entries.append({'id': entry['id'], 'sha256': digest, 'present': True,
                            'memory_id': existing[0].memory_id, 'written': False,
                            'confidence_bp': confidence_bp})
            continue
        if dry_run:
            entries.append({'id': entry['id'], 'sha256': digest, 'present': False,
                            'written': False, 'confidence_bp': confidence_bp})
            continue
        try:
            response = client.propose(content=chunk_content(pack, digest), memory_type=memory_type,
                                      domain_tag=domain, confidence=confidence_bp / 10000,
                                      embedding=_vector_for(client, pack, digest),
                                      tags=chunk_tags(pack, digest))
        except Exception as error:  # noqa: BLE001 - reported per chunk, a re-run resumes
            failures += 1
            entries.append({'id': entry['id'], 'sha256': digest, 'present': False,
                            'written': False, 'confidence_bp': confidence_bp,
                            'error': f'{type(error).__name__}: {error}'})
            continue
        _check_outcome(response, digest)
        replay = _replay_of(response)
        replays += int(replay)
        installed += int(not replay)
        entries.append({'id': entry['id'], 'sha256': digest, 'present': True,
                        'memory_id': getattr(response, 'memory_id', None),
                        'written': not replay, 'replay': replay,
                        'confidence_bp': confidence_bp})
    return {'pack': pack.pack_id, 'version': pack.version, 'domain': domain,
            'class': pack.pack_class,
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


def _forget_tagged(client, tag: str, *, domain: str, reason: str) -> dict:
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


def retire(client, tag: str, *, domain: str | None, reason: str) -> dict:
    domain = resolve_read_domain(client, domain, needed_by='--retire')
    return _forget_tagged(client, tag, domain=domain, reason=reason)


def roll(client, pack, previous, *, domain: str | None, dry_run: bool = False) -> dict:
    """Install a successor version, then retire the chunks the window dropped.

    Order matters: install first, retire second. A run that fails while adding
    chunks leaves the old window intact rather than emptying the index.

    The old manifest is the new manifest's pinned parent - its digest is inside
    the signed successor - so the removed set is recomputed from both manifests
    and the shipped delta document is only allowed to agree with that. A delta
    file is not signed, so it never directs a forget on its own.
    """
    domain = resolve_read_domain(client, domain, needed_by='--roll-from')
    parent = pack.manifest['parent']
    if parent is None:
        raise PackInstallError(
            f"pack {pack.tag} names no parent version; a window roll installs a successor")
    if previous.pack_id != pack.pack_id:
        raise PackInstallError(f"--roll-from is pack {previous.pack_id}, not {pack.pack_id}")
    previous_digest = knowledge_pack.manifest_digest(previous.manifest)
    if previous.version != parent['version'] or previous_digest != parent['manifest_sha256']:
        raise PackInstallError(
            f"--roll-from is {previous.pack_id} v{previous.version} ({previous_digest[:12]}...), "
            f"but {pack.tag} names v{parent['version']} "
            f"({parent['manifest_sha256'][:12]}...) as its parent")
    delta = knowledge_pack.delta_document(previous.manifest, pack.manifest)
    shipped = knowledge_pack.find_pack_delta(pack.directory, to_version=pack.version)
    if shipped is not None:
        knowledge_pack.check_delta_matches(shipped, delta)

    summary = {'pack': pack.pack_id, 'version': pack.version, 'domain': domain,
               'from_version': previous.version, 'added': len(delta['added']),
               'removed': len(delta['removed']), 'unchanged': delta['unchanged'],
               'delta': 'checked-against-manifests' if shipped is not None
               else 'recomputed-from-manifests',
               'phase': 'rolling', 'dry_run': dry_run, 'install': None, 'retire': None}
    installed = install(client, pack, domain=domain, dry_run=dry_run)
    summary['install'] = {key: installed[key] for key in
                          ('chunks', 'installed', 'skipped', 'replays', 'failures')}
    # The per-chunk detail is what the ledger records; the roll summary stays
    # short because a rolling brief is re-applied on every window.
    summary['install_detail'] = installed
    if installed['failures']:
        summary['phase'] = 'roll-incomplete'
        return summary

    if dry_run:
        detail = [{'sha256': digest, 'memories': len(list_tagged(client, pack.chunk_tag(digest),
                                                                 domain))}
                  for digest in delta['removed']]
        summary['phase'] = 'roll-dry-run'
        summary['retire'] = {'removed': len(delta['removed']),
                             'memories': sum(row['memories'] for row in detail),
                             'detail': detail, 'dry_run': True}
        return summary

    reason = (f"knowledge pack {pack.tag} rolled from v{previous.version}; "
              "the window no longer holds this chunk")
    summary['retire'] = {'removed': len(delta['removed']), 'memories': 0,
                         'deprecated': 0, 'parked': 0, 'failed': 0, 'detail': []}
    for digest in delta['removed']:
        outcome = _forget_tagged(client, pack.chunk_tag(digest), domain=domain, reason=reason)
        summary['retire']['memories'] += outcome['memories']
        for name in ('deprecated', 'parked', 'failed'):
            summary['retire'][name] += outcome['outcomes'].get(name, 0)
        summary['retire']['detail'].append({'sha256': digest, 'memories': outcome['memories'],
                                            'deprecated': outcome['outcomes'].get('deprecated', 0),
                                            'parked': outcome['outcomes'].get('parked', 0),
                                            'failed': outcome['outcomes'].get('failed', 0)})
    summary['phase'] = 'rolled'
    return summary


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
                                   'replay': row.get('replay', False),
                                   'confidence_bp': row.get('confidence_bp')}
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
    parser.add_argument('--trust-root', type=Path, default=DEFAULT_TRUST_ROOT,
                        help='reviewed key list that authorizes pack signatures')
    parser.add_argument('--no-trust-root', action='store_true',
                        help='ignore the shipped trust root (verification by pinned key only)')
    parser.add_argument('--public-key-hex', default=None,
                        help='require the manifest to be signed by exactly this ed25519 key')
    parser.add_argument('--trusted-key', type=Path, default=None,
                        help='file holding the trusted ed25519 public key as lowercase hex')
    parser.add_argument('--allow-unsigned', action='store_true',
                        help='accept a pack with no signature; the escape hatch, not the plan')
    parser.add_argument('--register-domain', action='store_true',
                        help='register the target domain first; fails loudly if policy refuses it')
    parser.add_argument('--ledger', type=Path, default=None)
    parser.add_argument('--no-ledger', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verify', action='store_true',
                        help='verify the pack and stop; no node, no identity, no writes')
    parser.add_argument('--check', action='store_true', help='report coverage and write nothing')
    parser.add_argument('--retire', action='store_true',
                        help='forget every memory carrying the pack tag in --domain')
    parser.add_argument('--roll-from', type=Path, default=None,
                        help='previous version of this pack (directory or archive); install this '
                             'one, then retire the chunks the pinned delta dropped')
    parser.add_argument('--migrate-to', default='',
                        help='install into this domain, verify, then retire the source copies')
    parser.add_argument('--reason', default='')
    args = parser.parse_args()

    selected = [name for name in ('check', 'retire', 'verify') if getattr(args, name)]
    if bool(args.migrate_to) and selected:
        raise PackInstallError('--migrate-to does not combine with --verify, --check or --retire')
    if args.roll_from is not None and (selected or bool(args.migrate_to)):
        raise PackInstallError('--roll-from does not combine with --verify, --check, --retire '
                               'or --migrate-to')
    if selected and args.dry_run:
        raise PackInstallError('--dry-run applies to install and --migrate-to only')
    if args.verify and (args.ledger or args.register_domain):
        raise PackInstallError('--verify writes nothing; drop --ledger and --register-domain')

    trusted = args.public_key_hex
    if args.trusted_key is not None:
        candidate = args.trusted_key.read_text('ascii').strip()
        if trusted is not None and trusted != candidate:
            raise PackInstallError('--trusted-key and --public-key-hex disagree')
        trusted = candidate
    trust_root = None if args.no_trust_root or trusted is not None \
        else knowledge_pack.load_trust_root(args.trust_root)
    with knowledge_pack.pack_directory(args.pack) as directory:
        pack = knowledge_pack.load_pack(directory, trust_root=trust_root,
                                        trusted_public_hex=trusted,
                                        allow_unsigned=args.allow_unsigned)
        previous = None
        if args.roll_from is not None:
            with knowledge_pack.pack_directory(args.roll_from) as previous_directory:
                # The parent digest inside the signed successor is the authority;
                # the old pack is loaded without a trust root for that reason.
                previous = knowledge_pack.load_pack(previous_directory, allow_unsigned=True)
        return dispatch(args, pack, trust_root, previous)


def dispatch(args, pack, trust_root, previous=None) -> int:
    domain = args.domain or None
    common = {'pack': pack.pack_id, 'version': pack.version, 'signed': pack.signature is not None,
              'class': pack.pack_class,
              'key_id': pack.key_id, 'key_status': pack.key_status,
              'trust_root': None if trust_root is None else str(args.trust_root),
              'active_keys': None if trust_root is None else knowledge_pack.active_key_ids(trust_root)}
    if args.verify:
        summary = dict(common, pack_dir=str(args.pack), verified=True,
                       unsigned_accepted=pack.signature is None)
        print(json.dumps(summary, sort_keys=True))
        return 0

    client = build_client(args)
    if args.register_domain:
        client.register_domain(domain or '', description='Curated knowledge packs')

    reason = args.reason or f"knowledge pack {pack.tag} retired by operator"
    recorded = None
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
    elif previous is not None:
        summary = roll(client, pack, previous, domain=domain, dry_run=args.dry_run)
        recorded = summary.pop('install_detail', None)
        summary['pack_dir'] = str(args.pack)
        summary['previous_pack_dir'] = str(args.roll_from)
    else:
        summary = install(client, pack, domain=domain, dry_run=args.dry_run)
        recorded = summary
        summary['pack_dir'] = str(args.pack)
    if recorded is not None and not args.dry_run:
        ledger = None if args.no_ledger else (args.ledger or DEFAULT_LEDGER_DIR / f'{pack.pack_id}.json')
        if ledger is not None:
            write_ledger(ledger, recorded)
            summary['ledger'] = str(ledger)
    summary.update(common)
    summary['unsigned_accepted'] = pack.signature is None
    print(json.dumps(summary, sort_keys=True))
    if summary.get('failures') or summary.get('install', {}).get('failures'):
        return 1
    if args.check and not summary.get('complete'):
        return 1
    if summary.get('outcomes', {}).get('failed'):
        return 1
    if (summary.get('retire') or {}).get('outcomes', {}).get('failed'):
        return 1
    if (summary.get('retire') or {}).get('failed'):
        return 1
    if summary.get('phase') in ('copy-incomplete', 'roll-incomplete'):
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (knowledge_pack.PackFormatError, PackInstallError) as error:
        print(f"install-knowledge-pack: {error}", file=sys.stderr)
        sys.exit(2)
