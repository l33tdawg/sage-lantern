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
carrying `public_key_hex` and `manifest_sha256`. Verification can pin a trusted
key (`--public-key-hex`, `--trusted-key`), and a manifest signed by a different
key is refused rather than accepted on trust.

**There is no pack signing key or authority yet.** Until one exists, `--allow-unsigned`
is available for local packs, and it is a deliberate interim hole: who signs packs,
and under which authority, is not decided here.

## Installing, checking, moving and removing

`scripts/lantern/install-knowledge-pack.py` verifies the pack, then works through
the node's own tag index. Every memory carries `pack:<id>-v<version>` and
`chunk:<sha256>`, so a pack is addressable and removable by tag.

| Operation | Behaviour |
|---|---|
| install | Asks for the chunk tag first; only proposes what is missing. One local request per chunk; re-running converges |
| `--check` | Coverage report (`present`/`missing`) and nothing written; exit 1 when a chunk is missing |
| `--retire` | Forgets every memory carrying the pack tag, and reports what the node actually did |
| `--migrate-to <domain>` | Copies the pack into another domain, verifies completeness there, and only then retires the source copies |

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
  `scripts/lantern/build-knowledge-pack.py`, `scripts/lantern/install-knowledge-pack.py`.
* Conformance suite (43 tests): `scripts/tests/test_knowledge_pack.py`,
  `scripts/tests/test_build_knowledge_pack.py`,
  `scripts/tests/test_install_knowledge_pack.py`. It covers tampered chunks, stray
  and missing chunk files, an edited manifest, a manifest swapped under a valid
  signature, a wrong trusted key, byte-identical rebuilds, delta arithmetic, tag
  scoping between versions, parked challenges, an indeterminate commit and a
  copy-incomplete migration that must not retire the source.
* Pack #1 rebuilt in this format: `work/knowledge-pack-20260920/pack-malaysia-v1/`
  (40 chunks, manifest `6ab58f40...`), verified byte-identical on rebuild.

## Not settled here

* `OPEN-DST-001` - canonical encoding freeze, compression, POUF.
* Who signs packs, and whether a pack signature is device, curator or Foundation
  authority.
* `OPEN-PUB-001` - which licences may travel on the public channel.
* The sharded chunk store and chunk-index tree the 1-10 GB class needs.
