from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS enrollments (
 id TEXT PRIMARY KEY, device_id TEXT NOT NULL, secret_hash TEXT NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL,
 setup_secret_used_at TEXT, management_public_key TEXT UNIQUE,
 management_fingerprint TEXT UNIQUE, tls_csr_pem TEXT, tls_csr_fingerprint TEXT UNIQUE,
 tls_key_fingerprint TEXT UNIQUE,
 proof_challenge TEXT, comparison_code TEXT, claimed_at TEXT, approved_at TEXT,
 issued_at TEXT, certificate_fingerprint TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS enrollments_device ON enrollments(device_id);
CREATE TABLE IF NOT EXISTS devices (
 device_id TEXT PRIMARY KEY, management_public_key TEXT NOT NULL UNIQUE,
 management_fingerprint TEXT NOT NULL UNIQUE, tls_key_fingerprint TEXT NOT NULL UNIQUE,
 certificate_fingerprint TEXT NOT NULL UNIQUE, certificate_serial TEXT NOT NULL UNIQUE,
 enrolled_at TEXT NOT NULL, last_sequence INTEGER
);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL REFERENCES devices(device_id),
 sequence INTEGER NOT NULL, observed_at TEXT NOT NULL, freshness_expires_at TEXT NOT NULL,
 clock_confidence TEXT NOT NULL, state_json TEXT NOT NULL, state_hash TEXT NOT NULL,
 signature TEXT NOT NULL, envelope_hash TEXT NOT NULL, ingested_at TEXT NOT NULL,
 UNIQUE(device_id, sequence)
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL,
 actor TEXT NOT NULL, device_id TEXT, enrollment_id TEXT, detail_json TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
        os.chmod(path, 0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def audit(db: sqlite3.Connection, now: str, event: str, actor: str, *, device_id: str | None = None, enrollment_id: str | None = None, detail: dict | None = None) -> None:
        # Detail is deliberately allow-listed by each caller. Never put secrets,
        # keys, CSRs, signatures, raw state, or authorization headers here.
        db.execute("INSERT INTO audit_events(occurred_at,event_type,actor,device_id,enrollment_id,detail_json) VALUES(?,?,?,?,?,?)", (now, event, actor, device_id, enrollment_id, json.dumps(detail or {}, sort_keys=True, separators=(",", ":"))))
