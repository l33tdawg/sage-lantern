#!/usr/bin/env python3
"""SAGE knowledge pack v2: content-addressed chunks behind a signed manifest.

Prototype format. OPEN-DST-001 (canonical pack encoding, chunking, compression
and POUF) is undecided, so this layout is non-normative and may change before
the G2 interop freeze. What it does fix, for our own pipeline:

* DST-001 - a pack manifest is immutable, content-addressed and signed.
* SYS-008 - a carrier is trusted for transport only. Every chunk is verified
  against the manifest, so a relay is never a trust input.

Canonical bytes are a JCS-shaped subset of RFC 8785 rather than full JCS:
object keys are ASCII only, so Python's ``sorted()`` matches JCS UTF-16 key
order, and no floating-point number ever reaches a hashed or signed structure
(embedding vectors travel as base64 float32). Those are the two places where a
Python canonical-JSON implementation diverges from RFC 8785 in practice, and
both are excluded rather than approximated.

Layout of one pack directory::

    <pack>/manifest.json            canonical manifest, the signed root
    <pack>/manifest.sig.json        detached ed25519 signature over manifest.json
    <pack>/chunks/<sha256>.json     one chunk record per content address
    <pack>/delta-v1-to-v2.json      optional per-chunk delta against a parent

A chunk record carries its own content address; the address covers the
curatorial content (id, title, text, tags, source, date) and deliberately not
the embedding, so re-embedding with a better model does not change chunk
identity. Embedding provenance lives in the manifest.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import struct


SCHEMA_CHUNK = "sage.knowledge-pack.chunk.v2"
SCHEMA_MANIFEST = "sage.knowledge-pack.manifest.v2"
SCHEMA_DELTA = "sage.knowledge-pack.delta.v2"
SCHEMA_SIGNATURE = "sage.knowledge-pack.signature.v1"

CHUNK_CONTENT_FIELDS = ("id", "title", "text", "tags", "source", "date")
CHUNK_RECORD_FIELDS = set(CHUNK_CONTENT_FIELDS) | {"schema", "sha256", "embedding"}
MANIFEST_FIELDS = {"schema", "pack", "embedding", "chunks", "totals", "parent"}
PACK_FIELDS = {"id", "version", "title", "licence", "provenance", "created_at"}
PROVENANCE_FIELDS = {"source", "curator", "url", "note"}
EMBEDDING_FIELDS = {"model", "dimension", "normalized", "embedded_chunks"}
MANIFEST_CHUNK_FIELDS = {"id", "sha256", "bytes", "title", "tags"}
TOTALS_FIELDS = {"chunks", "text_bytes", "chunk_bytes", "embedded_chunks"}
DELTA_FIELDS = {"schema", "pack_id", "from_version", "to_version", "from_manifest_sha256",
                "to_manifest_sha256", "added", "removed", "unchanged", "added_bytes"}
SIGNATURE_FIELDS = {"schema", "algorithm", "public_key_hex", "manifest_sha256", "signature_base64"}
VECTOR_FIELDS = {"model", "dimension", "vector_f32_le"}

PACK_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,63}\Z")
CHUNK_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX_KEY = re.compile(r"[0-9a-f]{64}\Z")

# Prototype chunk sizing: retrieval carries whole chunks, so a chunk has to stay
# inside a comfortable context slice. Raise it only with retrieval evidence.
MAX_TEXT_CHARS = 4000
MAX_CHUNKS = 200000


class PackFormatError(ValueError):
    """The pack does not match the v2 prototype format, or fails verification."""


def require(condition, message):
    if not condition:
        raise PackFormatError(message)


def _reject_floats(value, where="canonical value"):
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        raise PackFormatError(f"{where} may not contain a float; integers, strings, "
                              "booleans, null and containers of those only")
    if isinstance(value, list):
        for item in value:
            _reject_floats(item, where)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            require(isinstance(key, str) and key.isascii(),
                    f"{where} keys must be ASCII strings")
            _reject_floats(item, where)
        return
    raise PackFormatError(f"{where} contains an unsupported value of type {type(value).__name__}")


def canonical_json(value) -> bytes:
    """Canonical bytes for hashing and signing (JCS-shaped ASCII-key subset)."""
    _reject_floats(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                      allow_nan=False).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def content_object(chunk: dict) -> dict:
    return {field: chunk[field] for field in CHUNK_CONTENT_FIELDS}


def content_address(chunk: dict) -> str:
    return sha256_hex(canonical_json(content_object(chunk)))


def encode_vector(values) -> str:
    require(isinstance(values, (list, tuple)) and values, "embedding vector must be a non-empty list")
    packed = bytearray()
    for value in values:
        require(isinstance(value, (int, float)) and not isinstance(value, bool),
                "embedding vector entries must be numbers")
        number = float(value)
        require(number == number and number not in (float("inf"), float("-inf")),
                "embedding vector entries must be finite")
        packed += struct.pack("<f", number)
    return base64.b64encode(bytes(packed)).decode("ascii")


def decode_vector(text: str, dimension: int) -> list[float]:
    require(isinstance(text, str), "encoded vector must be a string")
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as error:
        raise PackFormatError("encoded vector is not canonical base64") from error
    require(base64.b64encode(raw).decode("ascii") == text,
            "encoded vector is not canonical base64")
    require(len(raw) == dimension * 4,
            f"encoded vector is {len(raw)} bytes, expected {dimension * 4}")
    return list(struct.unpack(f"<{dimension}f", raw))


def build_chunk(identifier: str, text: str, *, title: str = "", tags=(), source: str = "",
                date: str = "", embedding=None, embedding_model=None) -> dict:
    record = {
        "schema": SCHEMA_CHUNK,
        "id": identifier,
        "title": title,
        "text": text,
        "tags": sorted({tag for tag in tags if tag}),
        "source": source,
        "date": date,
    }
    record["sha256"] = content_address(record)
    if embedding is not None:
        require(embedding_model, "an embedding needs its model name")
        record["embedding"] = {"model": embedding_model, "dimension": len(embedding),
                               "vector_f32_le": encode_vector(embedding)}
    validate_chunk(record)
    return record


def chunk_bytes(record: dict) -> bytes:
    return canonical_json(record) + b"\n"


def validate_chunk(record) -> None:
    require(isinstance(record, dict), "chunk record must be an object")
    require(set(record) in (CHUNK_RECORD_FIELDS, CHUNK_RECORD_FIELDS - {"embedding"}),
            f"chunk record fields must be {sorted(CHUNK_RECORD_FIELDS)}, with embedding optional")
    require(record["schema"] == SCHEMA_CHUNK, f"chunk schema must be {SCHEMA_CHUNK}")
    require(CHUNK_ID.match(record["id"] or ""), "chunk id must be a lowercase slug")
    require(isinstance(record["text"], str) and record["text"].strip(),
            f"chunk {record['id']} has empty text")
    require(len(record["text"]) <= MAX_TEXT_CHARS,
            f"chunk {record['id']} is {len(record['text'])} characters, over the "
            f"{MAX_TEXT_CHARS}-character prototype limit")
    for field in ("title", "source", "date"):
        require(isinstance(record[field], str), f"chunk {record['id']} field {field} must be a string")
    require(isinstance(record["tags"], list) and all(isinstance(tag, str) and tag
            for tag in record["tags"]) and record["tags"] == sorted(set(record["tags"])),
            f"chunk {record['id']} tags must be a sorted list of unique non-empty strings")
    require(HEX64.match(record["sha256"] or ""), "chunk sha256 must be lowercase hex")
    require(record["sha256"] == content_address(record),
            f"chunk {record['id']} content address does not match its content")
    if "embedding" in record:
        embedding = record["embedding"]
        require(isinstance(embedding, dict) and set(embedding) == VECTOR_FIELDS,
                f"chunk {record['id']} embedding block fields must be {sorted(VECTOR_FIELDS)}")
        require(isinstance(embedding["model"], str) and embedding["model"],
                f"chunk {record['id']} embedding model must be a non-empty string")
        require(isinstance(embedding["dimension"], int) and not isinstance(embedding["dimension"], bool)
                and 1 <= embedding["dimension"] <= 8192,
                f"chunk {record['id']} embedding dimension is out of range")
        decode_vector(embedding["vector_f32_le"], embedding["dimension"])


def build_manifest(*, pack_id: str, version: int, title: str, licence: str, provenance: dict,
                   created_at: str, chunks, embedding_model: str, embedding_dimension: int,
                   parent=None) -> dict:
    ordered = sorted(chunks, key=lambda record: record["id"])
    require(len({record["sha256"] for record in ordered}) == len(ordered),
            "two chunks share a content address; de-duplicate the source")
    require(len({record["id"] for record in ordered}) == len(ordered),
            "two chunks share an id")
    embedded = sum(1 for record in ordered if "embedding" in record)
    for record in ordered:
        if "embedding" in record:
            require(record["embedding"]["model"] == embedding_model
                    and record["embedding"]["dimension"] == embedding_dimension,
                    f"chunk {record['id']} embedding provenance contradicts the manifest")
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "pack": {"id": pack_id, "version": version, "title": title, "licence": licence,
                 "provenance": provenance, "created_at": created_at},
        "embedding": {"model": embedding_model, "dimension": embedding_dimension,
                      "normalized": True, "embedded_chunks": embedded},
        "chunks": [{"id": record["id"], "sha256": record["sha256"],
                    "bytes": len(chunk_bytes(record)), "title": record["title"],
                    "tags": record["tags"]} for record in ordered],
        "totals": {"chunks": len(ordered),
                   "text_bytes": sum(len(record["text"].encode("utf-8")) for record in ordered),
                   "chunk_bytes": sum(len(chunk_bytes(record)) for record in ordered),
                   "embedded_chunks": embedded},
        "parent": parent,
    }
    validate_manifest(manifest)
    return manifest


def manifest_bytes(manifest: dict) -> bytes:
    return canonical_json(manifest) + b"\n"


def manifest_digest(manifest: dict) -> str:
    """Digest of the manifest exactly as stored: canonical bytes plus the newline.

    One definition only. The signature document, a parent link and a delta all
    name the manifest by this value, so it has to be the file's digest and not
    the digest of a re-serialisation that happens to differ by a trailing byte.
    """
    return sha256_hex(manifest_bytes(manifest))


def validate_manifest(manifest) -> None:
    require(isinstance(manifest, dict) and set(manifest) == MANIFEST_FIELDS,
            f"manifest fields must be {sorted(MANIFEST_FIELDS)}")
    require(manifest["schema"] == SCHEMA_MANIFEST, f"manifest schema must be {SCHEMA_MANIFEST}")

    pack = manifest["pack"]
    require(isinstance(pack, dict) and set(pack) == PACK_FIELDS,
            f"manifest pack fields must be {sorted(PACK_FIELDS)}")
    require(PACK_ID.match(pack["id"] or ""), "pack id must be a lowercase slug")
    require(isinstance(pack["version"], int) and not isinstance(pack["version"], bool)
            and pack["version"] >= 1, "pack version must be an integer >= 1")
    require(isinstance(pack["title"], str) and pack["title"], "pack title must be non-empty")
    require(isinstance(pack["licence"], str) and pack["licence"],
            "pack licence must be non-empty; a pack without provenance is not distributable")
    require(isinstance(pack["created_at"], str) and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z",
            pack["created_at"]), "pack created_at must be an RFC3339 UTC instant")
    provenance = pack["provenance"]
    require(isinstance(provenance, dict) and set(provenance) <= PROVENANCE_FIELDS
            and provenance.get("source"), "pack provenance must name its source")
    require(all(isinstance(value, str) and value for value in provenance.values()),
            "pack provenance values must be non-empty strings")

    embedding = manifest["embedding"]
    require(isinstance(embedding, dict) and set(embedding) == EMBEDDING_FIELDS,
            f"manifest embedding fields must be {sorted(EMBEDDING_FIELDS)}")
    require(isinstance(embedding["model"], str) and embedding["model"],
            "manifest embedding model must be non-empty")
    require(isinstance(embedding["dimension"], int) and not isinstance(embedding["dimension"], bool)
            and 1 <= embedding["dimension"] <= 8192, "manifest embedding dimension is out of range")
    require(isinstance(embedding["normalized"], bool), "manifest normalized must be a boolean")

    chunks = manifest["chunks"]
    require(isinstance(chunks, list) and 0 < len(chunks) <= MAX_CHUNKS,
            f"manifest must list 1..{MAX_CHUNKS} chunks")
    seen_ids, seen_hashes = set(), set()
    for entry in chunks:
        require(isinstance(entry, dict) and set(entry) == MANIFEST_CHUNK_FIELDS,
                f"manifest chunk fields must be {sorted(MANIFEST_CHUNK_FIELDS)}")
        require(CHUNK_ID.match(entry["id"] or ""), "manifest chunk id must be a lowercase slug")
        require(HEX64.match(entry["sha256"] or ""), "manifest chunk sha256 must be lowercase hex")
        require(isinstance(entry["bytes"], int) and entry["bytes"] > 0,
                f"manifest chunk {entry['id']} bytes must be positive")
        require(isinstance(entry["tags"], list) and all(isinstance(tag, str) for tag in entry["tags"]),
                f"manifest chunk {entry['id']} tags must be a list of strings")
        require(entry["id"] not in seen_ids and entry["sha256"] not in seen_hashes,
                f"manifest repeats chunk {entry['id']}")
        seen_ids.add(entry["id"])
        seen_hashes.add(entry["sha256"])
    require([entry["id"] for entry in chunks] == sorted(entry["id"] for entry in chunks),
            "manifest chunks must be sorted by id")

    totals = manifest["totals"]
    require(isinstance(totals, dict) and set(totals) == TOTALS_FIELDS,
            f"manifest totals fields must be {sorted(TOTALS_FIELDS)}")
    require(totals["chunks"] == len(chunks), "manifest totals.chunks contradicts the chunk list")
    require(totals["chunk_bytes"] == sum(entry["bytes"] for entry in chunks),
            "manifest totals.chunk_bytes contradicts the chunk list")
    require(isinstance(totals["text_bytes"], int) and totals["text_bytes"] > 0,
            "manifest totals.text_bytes must be positive")
    require(totals["embedded_chunks"] == embedding["embedded_chunks"]
            and 0 <= totals["embedded_chunks"] <= len(chunks),
            "manifest embedded_chunks contradicts the chunk list")

    parent = manifest["parent"]
    if parent is not None:
        require(isinstance(parent, dict) and set(parent) == {"version", "manifest_sha256"},
                "manifest parent must be null or {version, manifest_sha256}")
        require(isinstance(parent["version"], int) and parent["version"] >= 1
                and parent["version"] < pack["version"],
                "manifest parent version must be a lower integer")
        require(HEX64.match(parent["manifest_sha256"] or ""),
                "manifest parent digest must be lowercase hex")


def delta_document(old_manifest: dict, new_manifest: dict) -> dict:
    """Per-chunk delta between two manifests of the same pack."""
    validate_manifest(old_manifest)
    validate_manifest(new_manifest)
    require(old_manifest["pack"]["id"] == new_manifest["pack"]["id"],
            "a delta needs two versions of the same pack")
    old = {entry["sha256"]: entry for entry in old_manifest["chunks"]}
    new = {entry["sha256"]: entry for entry in new_manifest["chunks"]}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    return {
        "schema": SCHEMA_DELTA,
        "pack_id": old_manifest["pack"]["id"],
        "from_version": old_manifest["pack"]["version"],
        "to_version": new_manifest["pack"]["version"],
        "from_manifest_sha256": manifest_digest(old_manifest),
        "to_manifest_sha256": manifest_digest(new_manifest),
        "added": added,
        "removed": removed,
        "unchanged": len(set(old) & set(new)),
        "added_bytes": sum(new[digest]["bytes"] for digest in added),
    }


def validate_delta(document) -> None:
    require(isinstance(document, dict) and set(document) == DELTA_FIELDS,
            f"delta fields must be {sorted(DELTA_FIELDS)}")
    require(document["schema"] == SCHEMA_DELTA, f"delta schema must be {SCHEMA_DELTA}")
    require(PACK_ID.match(document["pack_id"] or ""), "delta pack id must be a lowercase slug")
    require(isinstance(document["from_version"], int) and document["from_version"] >= 1
            and isinstance(document["to_version"], int)
            and document["to_version"] > document["from_version"],
            "delta versions must increase")
    for field in ("from_manifest_sha256", "to_manifest_sha256"):
        require(HEX64.match(document[field] or ""), f"delta {field} must be lowercase hex")
    for field in ("added", "removed"):
        require(isinstance(document[field], list)
                and all(HEX64.match(digest or "") for digest in document[field])
                and document[field] == sorted(set(document[field])),
                f"delta {field} must be a sorted list of unique content addresses")
    require(set(document["added"]).isdisjoint(document["removed"]),
            "a chunk cannot be both added and removed")
    require(isinstance(document["unchanged"], int) and document["unchanged"] >= 0,
            "delta unchanged must be a non-negative integer")
    require(isinstance(document["added_bytes"], int) and document["added_bytes"] >= 0,
            "delta added_bytes must be a non-negative integer")


# --- signatures -------------------------------------------------------------

def load_signing_key(path: Path):
    """Load an ed25519 private key from a raw-hex file or a PEM file."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    raw = Path(path).read_bytes()
    text = raw.decode("ascii", "ignore").strip()
    if "BEGIN" in text:
        key = serialization.load_pem_private_key(raw, password=None)
        require(isinstance(key, ed25519.Ed25519PrivateKey), "signing key must be ed25519")
        return key
    require(HEX_KEY.match(text), "signing key file must be 32-byte lowercase hex or an ed25519 PEM")
    return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(text))


def public_key_hex(private_key) -> str:
    from cryptography.hazmat.primitives import serialization

    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()


def sign_manifest(raw: bytes, private_key) -> dict:
    from cryptography.hazmat.primitives import serialization

    signature = private_key.sign(raw)
    return {
        "schema": SCHEMA_SIGNATURE,
        "algorithm": "ed25519",
        "public_key_hex": public_key_hex(private_key),
        "manifest_sha256": sha256_hex(raw),
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def verify_manifest_signature(raw: bytes, document, trusted_public_hex: str | None = None) -> str:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    require(isinstance(document, dict) and set(document) == SIGNATURE_FIELDS,
            f"signature fields must be {sorted(SIGNATURE_FIELDS)}")
    require(document["schema"] == SCHEMA_SIGNATURE, f"signature schema must be {SCHEMA_SIGNATURE}")
    require(document["algorithm"] == "ed25519", "signature algorithm must be ed25519")
    require(HEX_KEY.match(document["public_key_hex"] or ""), "signature public key must be 32-byte hex")
    require(document["manifest_sha256"] == sha256_hex(raw),
            "signature covers a different manifest than the one present")
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(document["public_key_hex"]))
    except ValueError as error:
        raise PackFormatError("signature public key is not a valid ed25519 key") from error
    try:
        key.verify(base64.b64decode(document["signature_base64"], validate=True), raw)
    except (InvalidSignature, ValueError, TypeError) as error:
        raise PackFormatError("manifest signature does not verify") from error
    if trusted_public_hex is not None:
        require(HEX_KEY.match(trusted_public_hex or ""), "trusted public key must be 32-byte hex")
        require(document["public_key_hex"] == trusted_public_hex,
                f"manifest is signed by {document['public_key_hex']}, not the trusted key")
    return document["public_key_hex"]


# --- pack directories -------------------------------------------------------

class Pack:
    """A loaded, verified pack directory."""

    def __init__(self, directory: Path, manifest: dict, raw: bytes, chunks: dict, signer: str | None):
        self.directory = Path(directory)
        self.manifest = manifest
        self.raw = raw
        self.chunks = chunks
        self.signer = signer

    @property
    def pack_id(self) -> str:
        return self.manifest["pack"]["id"]

    @property
    def version(self) -> int:
        return self.manifest["pack"]["version"]

    @property
    def tag(self) -> str:
        return f"pack:{self.pack_id}-v{self.version}"

    def chunk_tag(self, digest: str) -> str:
        return f"chunk:{digest}"

    def vector(self, digest: str):
        record = self.chunks[digest]
        if "embedding" not in record:
            return None
        embedding = record["embedding"]
        if embedding["model"] != self.manifest["embedding"]["model"] \
                or embedding["dimension"] != self.manifest["embedding"]["dimension"]:
            return None
        return decode_vector(embedding["vector_f32_le"], embedding["dimension"])

    def summary(self) -> dict:
        return {"pack": self.pack_id, "version": self.version, "chunks": len(self.chunks),
                "text_bytes": self.manifest["totals"]["text_bytes"],
                "chunk_bytes": self.manifest["totals"]["chunk_bytes"],
                "embedded_chunks": self.manifest["totals"]["embedded_chunks"],
                "manifest_sha256": sha256_hex(self.raw), "signer": self.signer,
                "licence": self.manifest["pack"]["licence"]}


def load_pack(directory, trusted_public_hex: str | None = None, allow_unsigned: bool = False) -> Pack:
    """Verify a pack directory and return it. Every check is fail-closed."""
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    require(manifest_path.is_file(), f"{manifest_path} is missing")
    raw = manifest_path.read_bytes()
    require(raw.endswith(b"\n"), "manifest.json must end with a newline")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackFormatError(f"manifest.json is not valid UTF-8 JSON: {error}") from error
    validate_manifest(manifest)
    require(manifest_bytes(manifest) == raw,
            "manifest.json is not in canonical form; re-run the builder instead of editing it")

    signature_path = directory / "manifest.sig.json"
    signer = None
    if signature_path.is_file():
        payload = signature_path.read_bytes()
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PackFormatError(f"manifest.sig.json is not valid UTF-8 JSON: {error}") from error
        require(canonical_json(document) + b"\n" == payload,
                "manifest.sig.json is not in canonical form")
        signer = verify_manifest_signature(raw, document, trusted_public_hex)
    else:
        require(allow_unsigned, f"{signature_path} is missing and unsigned packs are not accepted here")
        require(trusted_public_hex is None, "a trusted public key was supplied but the pack is unsigned")

    store = directory / "chunks"
    require(store.is_dir(), f"chunk store {store} is missing")
    chunks = {}
    for entry in manifest["chunks"]:
        path = store / f"{entry['sha256']}.json"
        require(path.is_file(), f"chunk file {path.name} is missing")
        payload = path.read_bytes()
        require(payload.endswith(b"\n"), f"chunk file {path.name} must end with a newline")
        try:
            record = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PackFormatError(f"chunk file {path.name} is not valid UTF-8 JSON: {error}") from error
        validate_chunk(record)
        require(chunk_bytes(record) == payload,
                f"chunk file {path.name} is not in canonical form")
        require(record["sha256"] == entry["sha256"], f"chunk file {path.name} is misnamed")
        require(content_address(record) == entry["sha256"],
                f"chunk file {path.name} does not hash to its name")
        require(record["id"] == entry["id"] and record["title"] == entry["title"]
                and record["tags"] == entry["tags"],
                f"chunk file {path.name} contradicts the manifest")
        require(len(payload) == entry["bytes"],
                f"chunk file {path.name} is {len(payload)} bytes, manifest says {entry['bytes']}")
        chunks[entry["sha256"]] = record
    present = {path.name[:-len(".json")] for path in store.glob("*.json")}
    stray = sorted(present - set(chunks))
    if stray:
        raise PackFormatError(
            f"chunk store holds {len(stray)} file(s) the manifest does not list "
            f"(first: {stray[0]}); build into a fresh directory rather than reusing one")
    return Pack(directory, manifest, raw, chunks, signer)


def load_delta(path) -> dict:
    document = json.loads(Path(path).read_text("utf-8"))
    validate_delta(document)
    return document


def write_pack(directory, manifest: dict, chunks, signature: dict | None = None,
               delta: dict | None = None, delta_name: str | None = None) -> None:
    """Write a verified pack directory. Callers pass already-validated records."""
    directory = Path(directory)
    store = directory / "chunks"
    store.mkdir(parents=True, exist_ok=True)
    written = 0
    for digest, record in sorted(chunks.items()):
        path = store / f"{digest}.json"
        payload = chunk_bytes(record)
        if path.exists():
            require(path.read_bytes() == payload, f"{path} exists with different bytes")
            continue
        path.write_bytes(payload)
        written += 1
    raw = manifest_bytes(manifest)
    manifest_path = directory / "manifest.json"
    manifest_path.write_bytes(raw)
    if signature is not None:
        (directory / "manifest.sig.json").write_text(
            canonical_json(signature).decode("utf-8") + "\n", "utf-8")
    if delta is not None:
        name = delta_name or f"delta-v{delta['from_version']}-to-v{delta['to_version']}.json"
        (directory / name).write_text(canonical_json(delta).decode("utf-8") + "\n", "utf-8")
    return written
