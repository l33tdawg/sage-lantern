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

## Where this stands (2026-09-20)

| | State |
|---|---|
| Format, builder, installer | built, 107 tests, prototype under `OPEN-DST-001` |
| Trust | Foundation pack key 1 (`pk-1b7a5f4ee2c66492`, active) in the shipped trust root; private half outside the repo, **backups still outstanding** |
| Pack #1 | `reference` class, Malaysia v1: signed, per-chunk licence, archive `91c5ba0c` |
| Pack #2 | `brief` class, world brief 20 Jun – 20 Sep 2026: 2,052 events at confidence 0.80, per-chunk licence, signed, archive `11dad074` |
| Reference pack: world facts | `world-facts` v1 (Wikidata, CC0): 191 facts - 19 heights, 70 populations, 13 straight-line distances, 89 administrative rows, every chunk CC0-1.0 and sourced to its entity URL; signed, archive `7734508d` |
| Published | release assets `pack/malaysia-v1` and `pack/world-brief-v20260920` in `l33tdawg/sage-lantern`; the repo stays **private until the public launch on 2027-01-01** (owner direction 2026-09-20), so fetch is credentialed and distribution stays out of band until then |
| Window roll | `--roll-from <old>`: install the successor, then retire the chunks it dropped; rehearsed on two real overlapping windows (13 in, 275 out, 1,764 shared) |
| Device window | owner direction 2026-09-20: **one month max**. A brief installs its newest 31 days by default (647 of the shipped brief's 2,052 events), `--window-days` goes narrower, `--window-all` is the explicit archive/lab install |
| Device install | `world-facts` v1 is installed on unit A (191/191, `--check` complete, recall measured 2026-09-20 - `work/reference-facts-20260920/RESULTS.md`); unit B is untouched and a **brief** install still cannot land anywhere until the node grows a public cache domain (`bbd52246`) |

The class, per-chunk confidence and per-item licence fields changed the prototype
schema on 2026-09-20. Nothing had been published, so both packs were rebuilt and
re-signed rather than teaching readers several manifest shapes; the superseded
generations sit beside them under `superseded-pre-class/` and
`superseded-pre-licence/`.

Next: the arXiv and agency adapters with the per-item licence gate now in the
format. The Wikidata/CC0 adapter for the measured numeric gaps is done
(`scripts/lantern/fetch-wikidata-facts.py`, pack `world-facts` v1 signed,
installed on unit A and measured: the exact fact ranks first for all five probes
and the 2B answers from it - `work/reference-facts-20260920/RESULTS.md`). Then
the device install of a brief once the node grows a public cache domain
(`bbd52246`) so a brief does not land in the private home domain. The
snapshot-pack lane stays blocked until `SAGE-EXPORT` and `OPEN-COM-003` land — see
[DUAL_HOME_AND_PUBLICATION_CONTRACT.md](DUAL_HOME_AND_PUBLICATION_CONTRACT.md) §10.1.

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
 "confidence_bp":9500,"licence":"CC-BY-4.0","tags":["malaysia"],
 "text":"Kuala Lumpur Tower (Menara Kuala Lumpur) is 421 metres tall...","title":""}
```

* `sha256` is the **content address**: the SHA-256 of the canonical bytes of
  `{id, title, text, tags, source, date, confidence_bp}`. The file name is that
  digest. Re-rating a chunk is therefore a new chunk - the delta reports the old
  address removed and the new one added - which is what makes a confidence
  change actually reach a node instead of being ignored by an idempotent re-run.
* `confidence_bp` is the chunk's confidence in **basis points** (an integer out
  of 10000). The canonical-bytes rule forbids floating point in signed
  structures, so 0.8 is stored as `8000`; the builder's CLI still takes ordinary
  decimals. A pack's class bounds what a chunk may claim - see below.
* `licence` is what **that item** is. A uniform pack names its licence once and
  every chunk repeats it; a mixed-source pack declares `--licence mixed` and then
  every item must name its own, because one umbrella field cannot honestly
  describe CC BY-SA article text sitting beside CC0 structured data. A chunk with
  no licence is refused at build time.
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
 "pack":{"id":"malaysia","version":1,"class":"reference","title":"...","licence":"CC-BY-4.0",
         "provenance":{"source":"curated:owner-notes-2026-09-20"},
         "created_at":"2026-09-20T00:00:00Z"},
 "embedding":{"model":"nomic-embed-text-v1.5","dimension":768,"normalized":true,
              "embedded_chunks":0},
 "chunks":[{"id":"my-001","sha256":"...","bytes":392,"title":"","tags":["malaysia"]}, ...],
 "totals":{"chunks":40,"text_bytes":5051,"chunk_bytes":14451,"embedded_chunks":0},
 "parent":null}
```

* `chunks` is sorted by `id`; a chunk id and a content address each appear once.
* `class` is `reference` or `brief`, and it is not decoration: it fixes the
  memory type and the confidence band a node installs the pack with, and whether
  the installed text carries a time-and-source frame.
* `licence` and `provenance.source` are mandatory. A pack without provenance is not
  distributable, and which licences may travel on the public channel is
  `OPEN-PUB-001`'s call (interim default: public domain or an explicitly approved
  permissive licence).
* `parent` names the earlier version by `{version, manifest_sha256}`.

### Pack class and confidence

Two classes exist because a curated fact and this morning's news are different
claims, and one confidence cannot describe both.

| Class | What it is | Installed as | Confidence |
|---|---|---|---|
| `reference` | curated material whose truth the Foundation asserts | `fact`, text verbatim | the chunk's `confidence_bp`, 0.95 by default |
| `brief` | dated third-party material that can be wrong | `observation`, text framed `As of <date>, per <source>:` | the chunk's `confidence_bp`, 0.80 by default, and never at or above 0.95 |

Three rules make that real, and all three are enforced:

* A `brief` chunk must carry a `YYYY-MM-DD` date and a source; news with no day
  or attribution cannot be answered "as of" or "per".
* A `brief` chunk may not carry the fact confidence. Everything at 0.95 or above
  is a claim of settled truth, and a rolling brief must never make one.
* The frame lives in the installed content, not in the record. Recall hands the
  answering model a plain string (a `MemoryRecord` carries no tags, source or
  date), so `As of 2026-09-20, per https://...: <text>` is the only place the
  day and the source reach an answer at all. The signed chunk text stays clean;
  the frame is a deterministic install-time projection.

A brief also installs as a **window**, because the archive and what a device can
afford are different sizes. Owner direction 2026-09-20: one month maximum. The
installer's default and cap is the newest 31 days of the pack, anchored on the
pack's own newest chunk date rather than on the device clock (which may be
wrong); `--window-days N` goes narrower; `--window-all` is the explicit
archive/lab escape hatch and is never the default. The shipped 93-day brief
installs as 647 memories instead of 2,052. A reference pack has no window -
curated facts do not go stale - and passing a window flag for one is an error.

That changes what a roll must retire. `--roll-from` retires
**previous window − new window**, so a chunk the successor still carries but
which has aged past the window leaves the device too; a rolling archive would
otherwise keep serving news the device is no longer allowed to hold. With
`--window-all` the same rule collapses to the delta's removed addresses.

### Per-item licence

The manifest's `licence` is the pack's umbrella declaration; each chunk's
`licence` is what that item actually is, and it rides in the content address for
the same reason confidence does - re-licensing an item is a new chunk the delta
reports, not a silent edit.

| Pack build | Rule |
|---|---|
| `--licence CC-BY-4.0` (uniform) | Every chunk is stamped `CC-BY-4.0`; a row or `--default-licence` that contradicts it is refused |
| `--licence mixed` | Every item must name its own concrete licence (row field or `--default-licence`); an item with none is refused rather than inheriting one it may not have |

A chunk may never carry `mixed` itself: that is a pack-level marker, not a
licence. `fetch-world-brief.py` stamps `CC-BY-SA-4.0` on every row it emits, so
the brief's share-alike obligation travels with the source data rather than
resting on a build flag. `--verify`, install summaries and the ledger all report
the licence set, which is what makes a mixed pack auditable after the fact.

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
| `--window-days N` | Brief installs only: the newest N days of the pack (default and maximum 31). Applies to install, `--check` and `--roll-from` |
| `--window-all` | Install the whole pack instead of a brief window; the explicit archive/lab choice |
| `--verify` | Verifies the pack and stops: no node, no identity, no writes. This is the gate a delivered archive must pass first |
| `--check` | Coverage report (`present`/`missing`) and nothing written; exit 1 when a chunk is missing |
| `--roll-from <old>` | The rolling-window apply step. Installs the successor, then retires the chunk memories the delta dropped — in that order, so a failed install leaves the old window intact |
| `--retire` | Forgets every memory carrying the pack tag, and reports what the node actually did |
| `--migrate-to <domain>` | Copies the pack into another domain, verifies completeness there, and only then retires the source copies |

`--roll-from` takes the previous pack directory or archive. That previous
manifest is authenticated by the `parent.manifest_sha256` inside the signed
successor (the old pack is not re-verified against the trust root for that
reason), the added/removed/unchanged sets are **recomputed from the two
manifests**, and the delta document shipped in the pack may only agree with that
recomputation. A delta file is not covered by the signature, so it is never the
authority for a forget; a contradiction refuses the whole run before anything is
written. The summary reports `deprecated` versus `parked` per removed chunk,
because a post-app-v21 forget can park as a challenge instead of removing.

One property to hold on to: retirement is by content address, and the index
de-duplicates by content address across versions. A chunk that two installed
artifacts genuinely share is one memory, so rolling one of them out retires the
shared memory too. Packs are curated as one lineage per id; do not install two
independent lineages that reuse the same chunk content if you intend to roll one
of them.

The other side of the same property: a chunk that was already present is not
re-proposed, so it keeps the `pack:<id>-v<version>` tag of the version that first
installed it (there is no tag backfill in this SDK). A version tag therefore marks
what that version introduced. Dropping a whole rolled lineage is one `--retire`
per version tag; dropping exactly the chunks a window lost is what `--roll-from`
does by content address.

## Delivery: GitHub release to offline unit

A pack travels as one deterministic archive, so the release asset has a stable
identity: same pack in, same bytes out.

```sh
python3 scripts/lantern/build-knowledge-pack.py work/knowledge-pack-20260920/pack-malaysia-v1.jsonl \
    --out work/knowledge-pack-20260920/pack-malaysia-v1 --pack-id malaysia \
    --version 1 --class reference --title "Malaysia - practical regional facts" --licence CC-BY-4.0 \
    --source-label curated:owner-notes-2026-09-20 --created-at 2026-09-20T00:00:00Z \
    --default-date 2026-09-20 --default-tag malaysia \
    --signing-key ~/sage-foundation/pack-signing-1.key --archive
# -> work/.../malaysia-v1.tar.gz   (sha256 91c5ba0c... for the signed pack #1)
```

Attach the archive to a GitHub release in `l33tdawg/sage-lantern`, with the
manifest digest and the archive digest in the release notes. The tag scheme is
`pack/<id>-v<version>`, one release per pack version, and a published asset is
never overwritten or deleted: a rolling brief must keep its old windows
reachable, so a device that missed one can still fetch it, and supersession is a
new release rather than an edit.

Both packs are published: the releases `pack/malaysia-v1` and
`pack/world-brief-v20260920`. Each was verified as published, not as built —
downloaded back from the release and re-checked offline with `--verify` against
the trust root, digests matching the built archive. The repository is
**private until the public launch on 2027-01-01** (owner direction 2026-09-20),
so fetching an asset needs repository access until then; verification of an asset
you already hold needs no account, no server and no network, and device
distribution stays out of band (USB, LAN, later the swarm) meanwhile. The
release notes are written for the day the repository opens: the tag, the digests
and the verify command do not change when the visibility does.

On the receiving side — which may be a laptop, a
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
* **Share-alike and attribution sources** — Wikipedia dumps (CC BY-SA) and most
  open-access papers. Usable, but the licence travels with the pack: `licence`
  and `provenance` are mandatory fields for exactly this reason, and
  redistribution terms have to be checked per source, not per pack. Wikidata
  structured statements are separate: they are CC0, and
  `scripts/lantern/fetch-wikidata-facts.py` writes that licence on every row it
  emits, so a pack that mixes them with article text has to declare `mixed`.
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
* **Class, not just licence:** the wiki text is third-party and can be wrong, so the
  brief ships as class `brief`: every chunk at confidence 0.80, installed as an
  `observation` whose content is framed `As of <day>, per <page URL>: <text>`. It
  cannot ship at 0.95 and the builder refuses it if someone tries. The answers a
  node gives from it therefore carry the day and the source rather than asserting
  the present.
* **Window policy:** a brief ages out, and rolling a window retires the chunks that
  *left* it, not just the ones that arrived — otherwise the index accumulates
  stale news and the top-4 recall budget starts crowding out durable facts.
  `install-knowledge-pack.py --roll-from <old>` is that apply step. It installs
  the successor first and retires second; the removed set is recomputed from the
  pinned parent manifest, so the unsigned delta document can only confirm the
  removal, never cause one. Rehearsed on two real overlapping windows: 13 events
  in, 275 out, 1,764 untouched, and a second application converges to zero
  writes.
* **Install size is a recall decision, not a copying decision.** A three-month
  brief is ~2,000 memories in a node whose recall budget is four chunks; on a unit
  that also holds private facts that is a lot of competition. The archive is the
  right thing to publish; the device window is capped at one month (owner
  direction 2026-09-20), which for the shipped brief is 647 memories instead of
  2,052 and can be narrowed further with `--window-days`. A brief still belongs in
  its own domain rather than mixed into the private home domain (which is what
  `bbd52246` unblocks).

Science and other open collections fit the same class with the same caveat: at
arXiv, the *paper's own* licence decides (many are CC BY, plenty are arXiv's
non-exclusive default, which is not redistribution rights), so a source adapter
has to read the licence per item and the builder has to refuse an item with none.
That is what the per-chunk `licence` beside `source` is for, and what `--licence
mixed` enforces; the remaining per-source work is writing the adapters and
reading each source's licence at fetch time.

## Curated packs are not the public chain's cache

Owner direction (2026-09-20): news and outside material belong to the **public
plane**, outside the private chain; a pack that carries public-chain content is a
*cache for faster local lookup*, supplemented periodically.

The programme already specifies that split, and the two artifacts must not be
confused because their trust stories differ:

| | Curated pack (this document) | Commons Snapshot Pack |
|---|---|---|
| Trust | Foundation signing key, in the shipped trust root | Consensus checkpoint plus an exporter attestation, bound to a frozen height |
| Content | Whatever the curator chose, with per-pack provenance and licence | Only committed, non-deprecated `PUBLIC=0` records |
| Authority | The Foundation is the authority of truth | The chain is; the signature proves *who exported and what bytes*, not truth |
| Lifecycle | Versioned and superseded | Derived, replaceable, drops and rebuilds freely |

Where each one lands on a device, per `docs/MASTER_TECHNICAL_PRD.md` §13: a
snapshot pack "output imports only into **Lantern Cache**" — defined as a
*replaceable derived public read cache*, explicitly not canonical personal or
public chain state, and explicitly not a snapshot, backup, state sync or
Federation Copy (§13.4, principles 8). A Lantern also runs exactly one consensus
runtime, its personal node; the public side is "a bounded client, queue and
cache — not a second CometBFT replica"
(`docs/admin/DUAL_NETWORK_ARCHITECTURE_OPTIONS.md` §4).

Three consequences worth holding on to:

* **The brief today is a curated pack, not chain state.** News becomes canonical
  only by crossing publication: proposer envelope, intake and quarantine,
  moderation, a committed `PUBLIC` record. Until that lane exists, publishing the
  brief as a signed Foundation artifact is the honest description.
* **A cache must never be presented as consensus.** §13.4 is explicit that without
  native per-record inclusion proofs, the export root and exporter signature are an
  off-chain attestation, never consensus proof. A cached item therefore carries
  its anchor (height and time), and an answer drawn from it should say when it was
  true — the same "as of" rule as the brief.
* **That lane is gated, and the gate is a decision, not code.** `DST-005` allows
  only committed non-deprecated `PUBLIC` records; `SAGE-EXPORT` (a `required-sage-v12`
  contract) does not exist in v11.18.4; and `OPEN-COM-003` still owns the normative
  deterministic export schema, which is why the interim rule is "do not publish
  Commons Snapshot Packs". The curated lane is not blocked by any of that, which is
  why it went first.

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
* Conformance suite (107 tests): `scripts/tests/test_knowledge_pack.py`,
  `scripts/tests/test_build_knowledge_pack.py`,
  `scripts/tests/test_install_knowledge_pack.py`,
  `scripts/tests/test_pack_signing_key.py`. It covers tampered chunks, stray and
  missing chunk files, an edited manifest, a manifest swapped under a valid
  signature, a wrong trusted key, a revoked key, an unknown signer, byte-identical
  rebuilds, byte-identical archives, archive traversal and link members, a key
  file inside the repository, a mismatched trust-root key id, delta arithmetic,
  class and confidence rules (a brief can never claim the fact confidence; a
  confidence re-rating is a new content address), tag scoping between versions,
  parked challenges, an indeterminate commit, a copy-incomplete migration that
  must not retire the source, a roll that installs before it retires, a roll that
  refuses a delta contradicting the manifests, and a failed roll that leaves the
  old window alone. The window rules are pinned too: a brief installs its newest
  month by default, refuses more than 31 days, leaves reference packs alone, and
  a roll retires a chunk that aged out of the window even when the successor
  still carries it. Licence rules are pinned as well: a chunk without a licence
  is refused, a uniform pack refuses a contradicting item, and a `mixed` pack
  requires a concrete licence on every item.
* Pack #1 rebuilt in this format: `work/knowledge-pack-20260920/pack-malaysia-v1/`
  (40 chunks at 0.95, class `reference`, licence `CC-BY-4.0`, manifest
  `8ac19c6e...`) plus the signed delivery archive `malaysia-v1.tar.gz`
  (sha256 `91c5ba0c...`, member count 43
  including `manifest.sig.json`). `--verify` on the archive passes with no node
  contact and reports `key_status: active, unsigned_accepted: false`.
* The world brief in this format: `work/world-brief-20260920/world-brief-v20260920/`
  (2,052 events at 0.80, class `brief`, per-item `CC-BY-SA-4.0`, manifest
  `0bc86f9d...`), archive `world-brief-v20260920.tar.gz` sha256 `11dad074...`,
  member count 2,055.
* Two-window roll rehearsal on real portal data:
  `work/lantern-brief-roll-20260920/` (`rehearsal_roll.py` plus its two packs and
  the RESULTS record) - 2,039 memories installed, then 13 installed / 275 retired
  / 1,764 untouched, index exactly the new window, second application a no-op.
* One-month window rehearsal on the shipped signed brief:
  `work/lantern-brief-roll-20260920/rehearsal_window.py` - 647 of 2,052 events
  installed at `--window-days 31`, and a real windowed roll that retired 44
  aged-out chunks (0 from the delta) leaving exactly the new month.

## Not settled here

* `OPEN-DST-001` - canonical encoding freeze, compression, POUF.
* `OPEN-PUB-001` - which licences may travel on the public channel.
* The sharded chunk store and chunk-index tree the 1-10 GB class needs.
* Whether a pack signature should ever be held by something other than the
  Foundation (a curator key, a device key), and what that would let a device do.
* Overlapping reference packs. Pack #1 carries Ipoh's 2020 census population
  (759,000) and `world-facts` carries the 2016 Wikidata estimate (866,772); on
  the undated probe the 2B answered from the newer pack-1 line while the
  world-facts chunk ranked first. Whether a successor pack should retire the
  facts it supersedes, or every overlapping chunk should carry its as-of in the
  text, is not decided (`work/reference-facts-20260920/RESULTS.md`).
