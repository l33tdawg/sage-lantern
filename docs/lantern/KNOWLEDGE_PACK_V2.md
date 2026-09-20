# Knowledge pack format v2 (prototype)

**Status:** prototype, **not** normative. `OPEN-DST-001` (canonical pack encoding,
chunking, compression and POUF) is undecided in
[OPEN_DECISIONS.md](../OPEN_DECISIONS.md), so this layout may change before the G2
interop freeze; the interim rule there is "prototype format is non-normative".

What this layout does fix, for our own pipeline, is two accepted requirements:

| Requirement | What this format does |
|---|---|
| `DST-001` - pack manifests are immutable, content-addressed and signed | One manifest per pack version, canonical bytes, a single digest definition, detached ed25519 signature |
| `SYS-008` - packs trust signatures/hashes, not carriers | Every chunk is verified against the manifest; a relay is never a trust input |

It also gives the swarm direction in
[SWARM_CONTENT_DISTRIBUTION.md](SWARM_CONTENT_DISTRIBUTION.md) its unit of transfer:
a chunk named by its hash, a manifest that names the chunk list, and a per-chunk
delta between versions.

## Why the flat JSONL had to go

Pack #1 was ingested as one `fact` memory per line of
`work/knowledge-pack-20260920/pack-malaysia-v1.jsonl`
(`scripts/lantern/ingest-knowledge-pack.py`). That worked, but the ingest had no
content address, no provenance beyond a tag, no version relation, and its dedupe
was a no-op: it looked for an `id:` marker in `record.tags`, and the SDK's
`MemoryRecord` has no `tags` field to read back, so the check never matched.

## Layout of one pack directory

```
<pack>/manifest.json          canonical manifest - the signed root
<pack>/manifest.sig.json      detached ed25519 signature over manifest.json
<pack>/chunks/<sha256>.json   one chunk record per content address
<pack>/delta-v1-to-v2.json    optional per-chunk delta against a parent version
```

### Chunk record

```json
{"date":"2026-09-20","id":"my-005","schema":"sage.knowledge-pack.chunk.v2",
 "sha256":"04970863...","source":"curated:owner-notes-2026-09-20",
 "tags":["malaysia"],"text":"Kuala Lumpur Tower (Menara Kuala Lumpur) is 421 metres tall...",
 "title":""}
```

* `sha256` is the **content address**: the SHA-256 of the canonical bytes of
  `{id, title, text, tags, source, date}`. The file name is that digest.
* The **embedding is not part of the address**. Re-embedding a pack with a better
  model must not change chunk identity, so provenance for embeddings lives in the
  manifest instead, and the installer verifies the two against each other.
* An optional `embedding` block (`{model, dimension, vector_f32_le}`) may ride
  along so a field unit can install without spending CPU on embedding. Vectors are
  base64 little-endian float32, not JSON floats - see the canonical-bytes rule.
* Prototype limits: chunk id is a lowercase slug of at most 64 characters, text is
  1-4000 characters. A chunk is what retrieval carries, so it has to stay inside a
  comfortable context slice.

### Manifest

```json
{"schema":"sage.knowledge-pack.manifest.v2",
 "pack":{"id":"malaysia","version":1,"title":"...","licence":"CC-BY-4.0",
         "provenance":{"source":"curated:owner-notes-2026-09-20"},
         "created_at":"2026-09-20T00:00:00Z"},
 "embedding":{"model":"nomic-embed-text-v1.5","dimension":768,"normalized":true,
              "embedded_chunks":0},
 "chunks":[{"id":"my-001","sha256":"...","bytes":392,"title":"","tags":["malaysia"]}, ...],
 "totals":{"chunks":40,"text_bytes":5051,"chunk_bytes":14451,"embedded_chunks":0},
 "parent":null}
```

* `chunks` is sorted by `id`; a chunk id and a content address each appear once.
* `licence` and `provenance.source` are mandatory. A pack without provenance is not
  distributable, and which licences may travel on the public channel is
  `OPEN-PUB-001`'s call (interim default: public domain or an explicitly approved
  permissive licence).
* `parent` names the earlier version by `{version, manifest_sha256}`.

### Canonical bytes and the one digest

Canonical bytes are a **JCS-shaped subset** of RFC 8785 rather than full JCS:
object keys are ASCII only (so a plain sorted-keys dump matches JCS UTF-16 key
order) and **no floating-point number ever reaches a hashed or signed structure**
(embedding vectors are base64 float32). Those are the two places a Python
canonical-JSON implementation diverges from RFC 8785 in practice; here they are
excluded rather than approximated. Full RFC 8785 conformance stays part of
`OPEN-DST-001`.

Files end with a newline, and `manifest_sha256` means **the digest of the stored
manifest file** everywhere - signature document, parent link and delta alike. A
manifest is never re-serialised and re-hashed; the file is the artifact.

### Deltas

`delta-v<from>-to-<to>.json` lists the added, removed and unchanged content
addresses plus `added_bytes`. Because chunks are content-addressed, a peer that
already holds the previous version fetches only `added`.

### Signatures

`manifest.sig.json` is a detached ed25519 signature over the manifest file bytes,
carrying `key_id`, `public_key_hex` and `manifest_sha256`. `key_id` is derived
from the key itself (`pk-` plus the first 16 hex of its digest), so the trust root
and the signature cannot disagree about which key signed.

**The Foundation signs packs as the authority of truth.** Custody follows the
model we use for an Apple Developer ID: one organisation key, generated once,
held by the Foundation, backed up out of band in more than one place, and never
in this repository. One thing does not transfer: a field unit is offline and
cannot call a notarisation service, so there is no online check to make. The
trust anchor ships with the verifier instead, as
`scripts/lantern/pack-trust-root.json` — a reviewed list of the Foundation's
public keys. A pack is trusted because a key in that file signed it, not because
an authority answered a request.

Key status is a three-state decision, not a boolean:

| Status | Signs new packs | Verifies existing packs |
|---|---|---|
| `active` | yes | yes |
| `retired` | no (rotation is over) | yes - old archives stay valid without re-signing |
| `revoked` | no | **no** - the signature verifies cryptographically and is still refused |

Rotation and revocation are ordinary reviewed commits to the trust root, which
is why nothing else has to change: a new key is added `active`, the old one moves
to `retired`, and a compromised key moves to `revoked` without touching a single
pack. `scripts/lantern/pack-signing-key.py` generates a key outside the
repository, prints the trust-root entry to commit, and can read a *backup* to
prove it still derives the key the root records:

Like every tool in `scripts/lantern/`, these run through the interpreter rather
than from `PATH`: there is no exec bit and no shell alias, so the path you type
has to match where you are standing. From the repository root:

```sh
python3 scripts/lantern/pack-signing-key.py generate --out /Users/<you>/sage-foundation/pack-signing-1.key --note "Foundation pack key 1"
```

From `scripts/`, that same tool is `python3 lantern/pack-signing-key.py`, and from
anywhere else it is the absolute path. Do not escape the `~` in a path
(`\~/...` is a directory literally named `~`, not your home directory); an
absolute path is the least surprising option.

```sh
python3 scripts/lantern/pack-signing-key.py generate --out /Users/<you>/sage-foundation/pack-signing-1.key --note "Foundation pack key 1"
python3 scripts/lantern/pack-signing-key.py entry \
    --key ~/sage-foundation/pack-signing-1.key      # paste into the trust root
python3 scripts/lantern/pack-signing-key.py check \
    --key ~/backups/pack-signing-1.key              # backup drill against the root
```

The shipped trust root holds **Foundation pack key 1**
(`pk-1b7a5f4ee2c66492`, active), so an unsigned pack no longer passes on its own.
The private half lives outside the repository at
`~/sage-foundation/pack-signing-1.key` (0600), and it is the Foundation's to back
up: the tool can prove a backup against the root, but it cannot create the
off-disk copies. `--allow-unsigned` remains the explicit escape hatch for local
packs, and every run reports `unsigned_accepted` in its JSON so a provisioning log
never quietly records an unverified pack as verified.

## Installing, checking, moving and removing

`scripts/lantern/install-knowledge-pack.py` verifies the pack, then works through
the node's own tag index. Every memory carries `pack:<id>-v<version>` and
`chunk:<sha256>`, so a pack is addressable and removable by tag.

| Operation | Behaviour |
|---|---|
| install | Asks for the chunk tag first; only proposes what is missing. One local request per chunk; re-running converges |
| `--verify` | Verifies the pack and stops: no node, no identity, no writes. This is the gate a delivered archive must pass first |
| `--check` | Coverage report (`present`/`missing`) and nothing written; exit 1 when a chunk is missing |
| `--retire` | Forgets every memory carrying the pack tag, and reports what the node actually did |
| `--migrate-to <domain>` | Copies the pack into another domain, verifies completeness there, and only then retires the source copies |

## Delivery: GitHub release to offline unit

A pack travels as one deterministic archive, so the release asset has a stable
identity: same pack in, same bytes out.

```sh
python3 scripts/lantern/build-knowledge-pack.py work/knowledge-pack-20260920/pack-malaysia-v1.jsonl \
    --out work/knowledge-pack-20260920/pack-malaysia-v1 --pack-id malaysia \
    --version 1 --title "Malaysia - practical regional facts" --licence CC-BY-4.0 \
    --source-label curated:owner-notes-2026-09-20 --created-at 2026-09-20T00:00:00Z \
    --default-date 2026-09-20 --default-tag malaysia \
    --signing-key ~/sage-foundation/pack-signing-1.key --archive
# -> work/.../malaysia-v1.tar.gz   (sha256 0cea5c80... for the signed pack #1)
```

Attach the archive to a GitHub release, with the manifest digest and the archive
digest in the release notes. On the receiving side — which may be a laptop, a
USB stick, a LAN transfer or, later, a swarm chunk exchange — the sequence is

```sh
python3 scripts/lantern/install-knowledge-pack.py malaysia-v1.tar.gz --verify   # refuses anything unverified
python3 scripts/lantern/install-knowledge-pack.py malaysia-v1.tar.gz            # then install
```

`--verify` resolves the signature against `pack-trust-root.json`, checks every
chunk hash, refuses a stray or missing chunk file, refuses an archive member that
escapes the pack root, and needs no node. Extraction is bounded (400,000 members,
16 GiB expanded) so a corrupt or hostile archive cannot fill a unit's disk.
GitHub is the distribution channel; the signature, not the channel, is what makes
a pack true.

## Where pack content comes from

Still a curation question rather than a format one, and `OPEN-PUB-001` (accepted
licences per channel) is not decided. The candidates, cheapest first:

* **Owner-curated notes** — what pack #1 is today; no licence question because we
  wrote it.
* **Public domain and permissive sources** — Project Gutenberg, national archives,
  government open-data portals, CC0 datasets. Attribution is still recorded in
  `provenance`.
* **Share-alike and attribution sources** — Wikipedia/Wikidata dumps (CC BY-SA)
  and most open-access papers. Usable, but the licence travels with the pack:
  `licence` and `provenance` are mandatory fields for exactly this reason, and
  redistribution terms have to be checked per source, not per pack.
* **Anything requiring a licence we do not hold** — out of scope until
  `OPEN-PUB-001` says otherwise.

### Class 1: the dated world brief

The owner's call (2026-09-20): a **rolling dated brief**, not a live feed, starting
with world news because recency is the one gap a model can never close on its own.
CC BY-SA is accepted for the Foundation channel.

* **Source:** `scripts/lantern/fetch-world-brief.py` pulls Wikipedia's Current
  Events portal (one page per day, human-curated, dated and cited, CC BY-SA 4.0)
  over the public API. Titles and summaries only — no article bodies, no images —
  with the page URL as each chunk's `source` and the day as its `date`.
* **Shape:** one event per chunk, topic as `title` and tag. A 93-day window came to
  2,052 events, 417 KB of text, one 475 KB signed archive.
* **Class, not just licence:** the wiki text is third-party and can be wrong, so a
  brief is not a reference fact. News wants a distinct class and a lower confidence
  than `fact` at 0.95, and its answers should carry the day and the source
  ("as of 20 September, per ...") rather than asserting the present. That addition
  is not built yet.
* **Window policy:** a brief ages out. Rolling a window needs the chunks that
  *left* it retired, not just the new one installed — otherwise the index
  accumulates stale news and the top-4 recall budget starts crowding out durable
  facts. The delta document already names `removed`; the apply step that exploits
  it is the next piece of tooling.
* **Install size is a recall decision, not a copying decision.** A three-month
  brief is ~2,000 memories in a node whose recall budget is four chunks; on a unit
  that also holds private facts that is a lot of competition. The archive is the
  right thing to publish; the installed window should be tuned to what recall can
  afford, and a brief belongs in its own domain rather than mixed into the private
  home domain (which is what `bbd52246` unblocks).

Science and other open collections fit the same class with the same caveat: at
arXiv, the *paper's own* licence decides (many are CC BY, plenty are arXiv's
non-exclusive default, which is not redistribution rights), so a source adapter
has to read the licence per item and the builder has to refuse an item with none.
That is why mixed-source packs want a per-chunk `licence` beside `source`, which
this format does not carry yet either.

Two semantics matter here and both are enforced in code:

* **`domain_tag=None` means "home" on a write and "no filter" on a read.** So every
  read path resolves the concrete domain first, and refuses to guess when the agent
  owns more than one domain instead of silently reading or forgetting across all of
  them. Without that rule, a migration would forget the copies it had just made.
* **A forget is not always a deprecation.** Post-app-v21, a challenge may park until
  `k+1` challengers accrue. `--retire` reports `deprecated` vs `parked` per memory
  rather than claiming removal.

A local ledger (`/var/lib/sage-lantern/knowledge-packs/<pack>.json`) records which
content address became which memory id, written atomically after the run.

Tags for an ordinary domain are node-local serving metadata, not consensus state:
the index is per node and is rebuilt by installing the pack, not by syncing a chain.

### The pre-v2 copies on the test units

Pack #1 is already ingested on units A and B as 40 memories tagged
`pack:malaysia-v1` and `id:<chunk id>`. Two consequences:

* `--retire`/`--migrate-to` work on them (the pack tag is there).
* `--check` reports `chunk:` tags missing, because the old ingest never wrote them,
  and there is **no way to backfill a tag** on an existing memory through this SDK.
  A plain re-install re-proposes identical content, which the node answers as a
  replay rather than a duplicate, but it does not attach the new tags either. The
  migration path is therefore: migrate into the new (public) domain, verify
  coverage there, then retire the home-domain copies by pack tag.

## Size classes and seeding (prototype rules)

| Class | Size | Shape | Status |
|---|---|---|---|
| Practical / regional | 10-100 MB | one pack directory, flat `chunks/`, tens of thousands of chunks | supported today |
| Dense subset | 1-10 GB | needs sharded chunk stores (`chunks/ab/...`) and a chunk-index tree instead of a flat manifest list | **not** supported yet - the flat manifest would be hundreds of MB and a million-file directory is the wrong container |

Seeding rule: a node seeds exactly the chunks it holds, addressed by hash; a
version bump ships a new manifest plus the delta, and the previous version's
chunks stay valid (they are content-addressed) until someone retires them.

## Evidence

* Format, builder and installer: `scripts/lantern/knowledge_pack.py`,
  `scripts/lantern/build-knowledge-pack.py`, `scripts/lantern/install-knowledge-pack.py`,
  `scripts/lantern/pack-signing-key.py`, `scripts/lantern/pack-trust-root.json`.
* Conformance suite (70 tests): `scripts/tests/test_knowledge_pack.py`,
  `scripts/tests/test_build_knowledge_pack.py`,
  `scripts/tests/test_install_knowledge_pack.py`,
  `scripts/tests/test_pack_signing_key.py`. It covers tampered chunks, stray and
  missing chunk files, an edited manifest, a manifest swapped under a valid
  signature, a wrong trusted key, a revoked key, an unknown signer, byte-identical
  rebuilds, byte-identical archives, archive traversal and link members, a key
  file inside the repository, a mismatched trust-root key id, delta arithmetic,
  tag scoping between versions, parked challenges, an indeterminate commit, and a
  copy-incomplete migration that must not retire the source.
* Pack #1 rebuilt in this format: `work/knowledge-pack-20260920/pack-malaysia-v1/`
  (40 chunks, manifest `6ab58f40...`) plus the signed delivery archive
  `malaysia-v1.tar.gz` (sha256 `0cea5c80...`, member count 43 including
  `manifest.sig.json`). `--verify` on the archive passes with no node contact and
  reports `key_status: active, unsigned_accepted: false`.

## Not settled here

* `OPEN-DST-001` - canonical encoding freeze, compression, POUF.
* `OPEN-PUB-001` - which licences may travel on the public channel.
* The sharded chunk store and chunk-index tree the 1-10 GB class needs.
* Whether a pack signature should ever be held by something other than the
  Foundation (a curator key, a device key), and what that would let a device do.
